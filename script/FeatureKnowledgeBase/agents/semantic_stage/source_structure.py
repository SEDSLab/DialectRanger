"""Read observable structure, never semantic fields, from existing frozen sources.

This optional projection does not expand Owner scope or create DocumentIR IDs.
Unresolvable sources/headers remain explicit UNKNOWN, rather than guessed cells.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag
from agents.document_structure_v2.topology import _dom_path
from agents.document_structure_v2.definition_observations import feature_domain
from agents.document_structure_v2.local_definition_context import observe_local_definition_context, observe_page_function_definition

_INLINE = re.compile(r'^\s*(Syntax|Signature|Grammar|Function type|Parameters?|Arguments?|Return type|Returns?|Examples?|Notes?)\s*:\s*(.*)$', re.I | re.S)


def example_introduction(text):
    """An observable introduction, not any prose containing 'example'."""
    return bool(re.search(r'\b(?:for example|following (?:examples?|expressions|statements|queries))\b',text,re.I)
        or re.search(r'\b(?:an?|the)\s+example(?:,\s*including\s+(?:the\s+)?result,?)?\s+is\s+(?:given|shown|provided)\s+below\s*[:.]?\s*$',text,re.I)
        or re.fullmatch(r'\s*(?:an?|the)\s+examples?\s+(?:is|are)\s+(?:given|shown|provided)\s+(?:below|here)\s*[:.]?\s*',text,re.I))


def _text(node):
    return node.get_text(' ', strip=True)


def _ref(node):
    return {'dom_locator': _dom_path(node), 'text': _text(node)}


def _definition_targets(node, url):
    """Actual local HTML anchors, never an identity/URL-name inference."""
    anchors=[]
    if node.get('id'):anchors.append(node['id'])
    if node.name=='a' and node.get('name'):anchors.append(node['name'])
    # Empty anchor siblings preceding a declaration label are commonly used
    # instead of heading IDs. Do not attach that label to subsequent prose.
    previous=node.find_previous_sibling()
    while previous is not None and previous.name=='a' and not _text(previous):
        if previous.get('name') or previous.get('id'):
            anchors.append(previous.get('name') or previous.get('id'))
        previous=previous.find_previous_sibling()
    parent=node.parent
    if isinstance(parent,Tag) and parent.name in {'li','dd'} and parent.find('p',recursive=False) is node:
        if parent.get('id'):anchors.append(parent['id'])
    return sorted({urljoin(str(url or ''),'#'+str(a)) for a in anchors if a and url})


def _locator_index(soup):
    """Equivalent to topology._dom_path, built once rather than CSS rescans."""
    index={}
    def visit(parent, prefix):
        children=[c for c in parent.children if isinstance(c,Tag)]
        counts={}
        for c in children: counts[c.name]=counts.get(c.name,0)+1
        seen={}
        for c in children:
            seen[c.name]=seen.get(c.name,0)+1
            part=c.name
            if c.get('id'):part+='#'+c['id']
            elif counts[c.name]>1:part+=':nth-of-type('+str(seen[c.name])+')'
            path=prefix+' > '+part if prefix else part
            index.setdefault(path,[]).append(c)
            visit(c,path)
    visit(soup,'')
    return index


class SourceStructureIndex:
    """Per-build cache; local reads only. No URL/path guessing or fallback fetch."""
    def __init__(self):
        self.pages = {}
        self.tables = {}
        self.observations = {}

    def fragment_block_refs(self, document, fragment, block_ids, *, container=None):
        """Prove a link fragment occurs inside a selected source block."""
        page=self._page(document)
        if page.get('status')!='RESOLVED':return []
        soup=page['soup']
        anchors=soup.find_all(id=fragment)+soup.find_all('a',attrs={'name':fragment})
        anchors={id(a):a for a in anchors}
        if len(anchors)!=1:return []
        anchor=next(iter(anchors.values()))
        wanted=set(block_ids);refs=[]
        for block in document.get('blocks',[]):
            if block['block_id'] not in wanted:continue
            nodes=page['nodes'].get(str(block.get('dom_path') or ''),[])
            if len(nodes)==1 and (anchor is nodes[0] or any(p is nodes[0] for p in anchor.parents)):
                refs.append(block['block_id'])
        # Empty HTML anchors may be siblings immediately before a defining
        # paragraph. Accept adjacency only inside its proven container, never
        # "nearest following text" across a page or another definition.
        if not refs and container and anchor.name=='a' and not anchor.get_text(strip=True):
            containers=page['nodes'].get(container,[])
            if len(containers)==1 and anchor.parent is containers[0]:
                following=None
                for sibling in anchor.next_siblings:
                    if isinstance(sibling,Tag):
                        if sibling.name=='a' and not sibling.get_text(strip=True):continue
                        following=sibling;break
                    if str(sibling).strip():break
                if following is not None:
                    for block in document.get('blocks',[]):
                        if block['block_id'] not in wanted:continue
                        nodes=page['nodes'].get(str(block.get('dom_path') or ''),[])
                        if len(nodes)==1 and (nodes[0] is following or any(p is following for p in nodes[0].parents)):
                            refs.append(block['block_id'])
        return refs

    def _page(self, document):
        key = str(document.get('page_id') or document.get('html_path') or '')
        if key in self.pages:
            return self.pages[key]
        provenance = document.get('provenance') or {}
        path = document.get('html_path') or provenance.get('html_path')
        result = {'status': 'FROZEN_HTML_MISSING', 'soup': None}
        if path:
            try:
                content = Path(path).read_bytes()
                actual = hashlib.sha256(content).hexdigest()
                expected = document.get('page_hash') or provenance.get('page_hash')
                if expected and actual != expected:
                    result = {'status': 'SOURCE_HASH_MISMATCH', 'soup': None}
                else:
                    soup=BeautifulSoup(content, 'lxml')
                    result = {'status': 'RESOLVED', 'soup': soup, 'source_hash': actual,
                              'nodes':_locator_index(soup)}
            except (OSError, ValueError):
                pass
        self.pages[key] = result
        return result

    def observe(self, document: Mapping[str, Any], block: Mapping[str, Any]):
        key = (str(document.get('page_id')), str(block.get('block_id')))
        if key in self.observations:
            return self.observations[key]
        page = self._page(document)
        locator = str(block.get('dom_path') or '')
        nodes = []
        if page['soup'] is not None and locator:
            nodes = page['nodes'].get(locator, [])
        node = nodes[0] if len(nodes) == 1 else None
        observed = {'container_kind': 'UNKNOWN', 'visible_label': None,
                    'label_status': 'UNKNOWN', 'heading_path': list(block.get('heading_ancestry') or []),
                    'labels': [], 'raw_node_kind': node.name.upper() if node is not None else 'UNKNOWN'}
        result = {'source_structure_status': page['status'] if page['status'] != 'RESOLVED' else 'RESOLVED' if node is not None else 'DOM_LOCATOR_UNRESOLVED',
                  'source_hash': page.get('source_hash'), 'observed_context': observed, 'table_context': None,
                  'definition_targets': _definition_targets(node,document.get('url')) if node is not None else []}
        if node is not None:
            # A marked label and its value can share one paragraph. Preserve
            # their physical relationship without assigning a semantic role.
            if node.name in {'p', 'dd'}:
                full_text=_text(node)
                for label in node.find_all(['span','strong','b']):
                    label_text=_text(label)
                    if (not label_text or not full_text.startswith(label_text)
                            or label.name=='span' and not set(label.get('class') or []) & {'title','label'}):
                        continue
                    tail=full_text[len(label_text):].lstrip(' :')
                    if tail:
                        observed['inline_field_label']={'label':label_text,'value':tail,
                            'source_text':full_text,'label_locator':_dom_path(label),
                            'source_locator':locator}
                    break
            title_node=page['soup'].find('title') or page['soup'].find('h1')
            observed['document_title']=_text(title_node) if title_node is not None else None
            observed['outgoing_links']=[{'target_url':urljoin(str(document.get('url') or ''),str(a['href'])),
                'label':_text(a),'dom_locator':_dom_path(a)} for a in node.find_all('a',href=True)]
            observed['feature_domain']=feature_domain(node)
            # A host SQL example may demonstrate an embedded XPath/JSONPath
            # expression. Preserve that language boundary for identity/type,
            # but do not erase the actual SQL example from its bound owner.
            if (observed['feature_domain']=='EMBEDDED_QUERY_LANGUAGE'
                    and node.name in {'pre','code'} and re.match(
                        r'^\s*(?:[A-Za-z_][\w.-]*>\s*)?(?:SELECT|WITH|INSERT|UPDATE|DELETE|VALUES|CREATE|ALTER|EXPLAIN)\b',
                        node.get_text(' ',strip=True),re.I)):
                observed['embedded_expression_domain']=observed['feature_domain']
                observed['host_language']='SQL'
                observed['feature_domain']='DOCUMENT_FEATURE'
            local_definition = observe_local_definition_context(node) or observe_page_function_definition(node, str(document.get('title') or ''))
            if local_definition:
                observed['local_definition'] = local_definition
            for parent in [node, *node.parents]:
                if isinstance(parent, Tag) and parent.name == 'details':
                    summary = parent.find('summary', recursive=False)
                    observed['container_kind'] = 'DETAILS'
                    observed['container_locator'] = _dom_path(parent)
                    if summary is not None:
                        label = {**_ref(summary), 'origin': 'DETAILS_SUMMARY'}
                        observed['labels'].append(label)
                        observed.update(visible_label=label['text'], label_status='OBSERVED')
                    break
            text = str(block.get('text') or block.get('raw_text') or '')
            match = _INLINE.match(text)
            if match:
                label = {'text': match.group(1), 'value_text': match.group(2),
                         'origin': 'INLINE_LABEL', 'dom_locator': locator,
                         'block_id': block.get('block_id')}
                observed['labels'].append(label)
                if observed['visible_label'] is None:
                    observed.update(visible_label=label['text'], label_status='OBSERVED')
            # An immediately adjacent prose introduction is observable source
            # structure, not permission to treat every code block as an example.
            code_container = node
            if node.name == 'code' and isinstance(node.parent, Tag) and node.parent.name == 'pre':
                code_container = node.parent
            if code_container.name == 'pre' and isinstance(code_container.parent, Tag) and code_container.parent.name == 'blockquote':
                code_container = code_container.parent
            if node.name in {'pre', 'code'}:
                # The source preserves line boundaries needed to distinguish
                # SQL comments from executable statements. The IR text/IDs are
                # unchanged; this is a hash-verified observation of that node.
                observed['literal_code_text']=node.get_text()
                # Some reference generators put a visible introduction as
                # direct text of a generic container, not a separate P/IR
                # block. Observe only the short inline prefix immediately
                # before this code; never scan an ancestor's whole prose.
                parent=code_container.parent
                if isinstance(parent,Tag) and parent.name in {'div','p','figure','blockquote','section'}:
                    before=[]
                    for sibling in parent.children:
                        if sibling is code_container:break
                        before.append(sibling)
                    if before and all(not isinstance(s,Tag) or s.name in {'span','strong','em','b','i','a'} for s in before):
                        prefix=' '.join(_text(s) if isinstance(s,Tag) else str(s) for s in before).strip()
                        if len(prefix)<=200 and example_introduction(prefix):
                            observed['labels'].append({'dom_locator':_dom_path(parent),
                                'text':'Example','introduction_text':prefix,
                                'origin':'CONTAINER_PREFIX_EXAMPLE_INTRODUCTION'})
                for _ in range(3):
                    parent=code_container.parent
                    if (code_container.find_previous_sibling() is not None or not isinstance(parent,Tag)
                            or parent.name not in {'div','section','figure','blockquote'}
                            or _text(parent)!=_text(code_container)):
                        break
                    code_container=parent
                intro = code_container.find_previous_sibling()
                if intro is not None and intro.name == 'p' and example_introduction(_text(intro)):
                    observed['labels'].append({**_ref(intro), 'text': 'Example',
                                               'introduction_text': _text(intro),
                                               'origin': 'ADJACENT_EXAMPLE_INTRODUCTION'})
                # Side-by-side code examples are often a layout table, not a
                # feature catalog. Require every body cell to be pure PRE/CODE
                # and a real adjacent introduction; do not infer from SQL words.
                table=node.find_parent('table')
                if table is not None and not observed['labels']:
                    cells=[c for c in table.find_all('td') if c.find_parent('table') is table]
                    pure=bool(cells) and all(c.find('pre') is not None
                        and _text(c)==' '.join(_text(pre) for pre in c.find_all('pre')) for c in cells)
                    container=table
                    for _ in range(2):
                        if container.find_previous_sibling() is not None:break
                        parent=container.parent
                        if not isinstance(parent,Tag) or parent.name not in {'center','div','figure'} or _text(parent)!=_text(container):break
                        container=parent
                    intro=container.find_previous_sibling()
                    if pure and intro is not None and intro.name=='p' and example_introduction(_text(intro)):
                        observed['labels'].append({**_ref(intro),'text':'Example',
                            'introduction_text':_text(intro),'origin':'ADJACENT_EXAMPLE_INTRODUCTION',
                            'context_scope':'CODE_ONLY_COMPARISON_TABLE','container_locator':_dom_path(table)})
            if node.name == 'tr' or node.get('role') == 'row':
                observed['literal_row_text']=_text(node)
                from agents.contracts.dom_table import nearest_table
                table = nearest_table(node)
                if table is not None:
                    tablekey = (key[0], _dom_path(table))
                    if tablekey not in self.tables:
                        self.tables[tablekey] = table_structure(table, page_id=key[0])
                    result['table_context'] = self.tables[tablekey].get(locator)
            # Read actual links and actual surrounding section anchors. These
            # facts grant neither identity nor admission; downstream may use
            # them only with independently verified subject witnesses.
            if ((observed.get('local_definition') or {}).get('role')=='FUNCTION_DECLARATION_LIST'
                    or node.name=='tr'):
                references=[]
                for link in node.find_all('a',href=True):
                    href=str(link['href']);target=urljoin(str(document.get('url') or ''),href)
                    if not href.startswith('#'):continue
                    fragment=href[1:]
                    targets=page['soup'].find_all(id=fragment)+page['soup'].find_all('a',attrs={'name':fragment})
                    if len({id(t) for t in targets})==1:
                        references.append({'target_url':target,'label':_text(link),'dom_locator':_dom_path(link)})
                observed['definition_references']=references
                heading=node.find_previous(re.compile(r'^h[1-6]$'))
                normalize=lambda v:' '.join(str(v or '').split())
                if heading is not None and normalize(_text(heading)) in {normalize(v) for v in block.get('heading_ancestry') or []}:
                    observed['definition_section']={'heading_text':_text(heading),'heading_locator':_dom_path(heading),
                        'anchor_urls':_definition_targets(heading,document.get('url'))}
        self.observations[key] = result
        return result


def table_structure(table: Tag, *, page_id: str = ''):
    """Logical HTML cell grid; explicit headers only, no value-based guessing.

    rowspan cells retain their source row; colspan headers cover each spanned
    column. Invalid/overlapping grids and non-unique explicit IDs stay UNKNOWN.
    Row headers are separate from column headers. Blank header cells are not
    invented into semantic column names.
    """
    tid = 'table:' + hashlib.sha256((page_id+'|'+_dom_path(table)).encode()).hexdigest()[:20]
    from agents.contracts.dom_table import table_rows, row_cells
    rows = table_rows(table)
    occupied = {}; allcells = []; byrow = {}; invalid = False
    for ri, row in enumerate(rows):
        col = 0; cells = []
        rowhead = bool(row.find_parent('thead')) or row.get('role') == 'rowheader' or bool(re.search(r'(^|[\s_-])(header|head)([\s_-]|$)', ' '.join(row.get('class') or []), re.I))
        for ci, cell in enumerate(row_cells(row)):
            while (ri,col) in occupied: col += 1
            def span(attr):
                nonlocal invalid
                try:
                    n=int(cell.get(attr,1))
                    if n == 0 and attr == 'rowspan': return len(rows)-ri
                    if 1 <= n <= 1000: return n
                except (TypeError,ValueError): pass
                invalid=True; return 1
            rs, cs = span('rowspan'), span('colspan')
            scope = str(cell.get('scope') or '').lower()
            header = cell.name == 'th' or cell.get('role') in {'columnheader','rowheader'} or rowhead
            role = 'ROW_HEADER' if scope in {'row','rowgroup'} or cell.get('role') == 'rowheader' else 'COLUMN_HEADER' if header else 'DATA_CELL'
            item = {**_ref(cell), 'cell_index':ci,'column_start':col,'column_end':col+cs-1,
                    'row_index':ri,'rowspan':rs,'colspan':cs,
                    'raw_rowspan':cell.get('rowspan'),'raw_colspan':cell.get('colspan'),
                    'cell_kind':cell.name.upper(),'header_role':role,'scope':scope or None,
                    'html_id':cell.get('id'),'explicit_header_ids':list(cell.get('headers') or []),
                    'markers':[_ref(n) for n in cell.find_all('sup')],
                    'footnote_links':[{**_ref(a),'href':a.get('href')} for a in cell.find_all('a',href=True)
                                      if a.find_parent('sup') or a.get('role')=='doc-noteref'],
                    'links':[{**_ref(a),'href':a.get('href')} for a in cell.find_all('a',href=True)
                             if not a.find_parent('sup') and a.get('role')!='doc-noteref']}
            # Source-local paragraph boundaries survive a composite header.
            # No class/DB selector: observe paragraphs/code inside this cell,
            # excluding nested tables and nested definition sections.
            item['nested_table'] = cell.find('table') is not None
            item['fragments'] = []
            for fragment in cell.find_all(['p', 'pre']):
                if fragment.find_parent(['td', 'th']) is not cell:
                    continue
                parent_fragment = fragment.find_parent(['p', 'pre'])
                if parent_fragment is not None and parent_fragment is not cell:
                    continue
                children = [c for c in fragment.children if (_text(c) if isinstance(c, Tag) else str(c).strip())]
                first = children[0] if children else None
                leading_code = isinstance(first, Tag) and first.name in {'code', 'var'}
                if isinstance(first, Tag) and first.name in {'a', 'span', 'em', 'i', 'strong'}:
                    code = first.find(['code', 'var'])
                    leading_code = code is not None and _text(first) == _text(code)
                item['fragments'].append({**_ref(fragment), 'text': ' '.join(_text(fragment).split()),
                    'node_kind': fragment.name.upper(),
                    'source_classes': list(fragment.get('class') or []),
                    'inline_code': [{'text':_text(code),'classes':list(code.get('class') or [])}
                                    for code in fragment.find_all(['code','var'])],
                    'leading_code': leading_code,
                    'nested_section': any(isinstance(p, Tag) and (p.name in {'section','aside','blockquote'}
                        or p.find(re.compile(r'^h[1-6]$'), recursive=False)) for p in fragment.parents if p is not cell and cell in p.parents)})
            cells.append(item); allcells.append(item)
            for rr in range(ri,min(len(rows),ri+rs)):
                for cc in range(col,col+cs):
                    if (rr,cc) in occupied: invalid=True
                    occupied[(rr,cc)]=item
            col += cs
        byrow[ri]=cells
    headers=[c for c in allcells if c['header_role']=='COLUMN_HEADER']
    result={}
    notes=shared_context(table)
    for ri,row in enumerate(rows):
        cells=byrow[ri]
        for cell in cells:
            if cell['header_role']!='DATA_CELL':
                matches=[]; status='HEADER_CELL'
            elif cell['explicit_header_ids']:
                matches=[h for hid in cell['explicit_header_ids'] for h in allcells if h['html_id']==hid and h['header_role']!='DATA_CELL']
                status='EXPLICIT' if len(matches)==len(cell['explicit_header_ids']) and len({h['html_id'] for h in matches})==len(matches) else 'UNKNOWN'
            else:
                matches=[h for h in headers if h['row_index'] < ri and h['row_index']+h['rowspan'] <= ri and h['column_start'] <= cell['column_end'] and h['column_end'] >= cell['column_start']]
                # More than one same-level header overlapping a spanning data
                # cell is not one unambiguous column association.
                status='OBSERVED_GRID' if matches and all(h['text'] for h in matches) else 'UNKNOWN'
                covered={c for h in matches for c in range(h['column_start'],h['column_end']+1)}
                if not set(range(cell['column_start'],cell['column_end']+1)) <= covered: status='UNKNOWN'
                if any(sum(h['row_index']==other['row_index'] for other in matches)>1 for h in matches): status='UNKNOWN'
            if invalid: status='UNKNOWN'
            cell['header_status']=status
            cell['headers']=[{k:h[k] for k in ('text','dom_locator','row_index','column_start','column_end','rowspan','colspan','markers')} for h in matches]
        inherited=[]
        seen=set()
        for (rr,cc),c in sorted(occupied.items()):
            if rr==ri and c['row_index']<ri and c['dom_locator'] not in seen:
                inherited.append({'cell_locator':c['dom_locator'],'source_row_index':c['row_index'],'column_start':c['column_start'],'column_end':c['column_end']});seen.add(c['dom_locator'])
        result[_dom_path(row)]={'table_id':tid,'table_locator':_dom_path(table),
            'row_id':_dom_path(row),'row_index':ri,'cells':cells,'inherited_cells':inherited,
            'structure_status':'UNKNOWN' if invalid else 'OBSERVED',
            'shared_context':notes}
    return result


def shared_context(table):
    """Keep table-level observable notes without asserting per-feature scope."""
    notes=[]
    for sibling in table.next_siblings:
        if not isinstance(sibling,Tag): continue
        if sibling.name in {'table','h1','h2','h3','h4','h5','h6'}: break
        first=sibling.find('sup')
        if sibling.get('role')=='doc-footnote' or (first is not None and _text(sibling).startswith(_text(first))):
            notes.append({**_ref(sibling),'marker':_text(first) if first else None,
                          'relationship':'ADJACENT_MARKED_TABLE_CONTEXT','scope_status':'UNCERTAIN',
                          'feature_assignment':False})
    # Explicit link-target relationships, unlike adjacency, are observable.
    for anchor in table.find_all('a',href=True):
        if not (anchor.find_parent('sup') or anchor.get('role')=='doc-noteref'): continue
        href=str(anchor['href'])
        if href.startswith('#'):
            targets=table.find_parent().find_all(id=href[1:])
            if len(targets)==1:
                notes.append({**_ref(targets[0]),'reference_locator':_dom_path(anchor),'href':href,
                              'relationship':'EXPLICIT_FRAGMENT_REFERENCE','scope_status':'OBSERVED_REFERENCE_ONLY',
                              'feature_assignment':False})
    return notes
