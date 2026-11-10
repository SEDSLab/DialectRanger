"""Additive source witnesses; no SQL identity or semantic classification."""
from collections import defaultdict
from bs4 import Comment, NavigableString, Tag

from .models import stable_id, clean_text
from .source_visibility import exclusion


def preserve_source(root, blocks, page_id, dom_path):
    """Keep old text/IDs, add literal text and bounded uncovered text runs."""
    tags = [root, *root.find_all(True)]
    paths = {dom_path(t): t for t in tags}
    covered = set()
    positions = {id(t): i for i, t in enumerate(root.descendants)}
    for b in blocks:
        node = paths.get(b['dom_path'])
        if node is None:
            continue
        b['source_text'] = node.get_text('', strip=False)
        b['normalized_text'] = b['text']
        b['source_text_origin'] = 'FROZEN_DOM_TEXT_CONTENT'
        b['source_dom_order'] = positions.get(id(node), -1)
        b['source_parent_locator'] = dom_path(node.parent) if isinstance(node.parent, Tag) else None
        covered.update(id(s) for s in node.descendants if isinstance(s, NavigableString))
    runs = []
    current = None
    for s in root.descendants:
        if not isinstance(s, NavigableString) or isinstance(s, Comment):
            continue
        if id(s) in covered or exclusion(s):
            current = None
            continue
        if current is None or current['parent'] is not s.parent:
            current = {'parent': s.parent, 'strings': [], 'position': positions[id(s)]}
            runs.append(current)
        current['strings'].append(s)
    for run in runs:
        literal = ''.join(str(s) for s in run['strings'])
        if not literal.strip():
            continue
        parent = run['parent']
        strings = [s for s in parent.descendants if isinstance(s, NavigableString) and not isinstance(s, Comment)]
        wanted = {id(s) for s in run['strings']}
        indexes = [i for i, s in enumerate(strings) if id(s) in wanted]
        path = dom_path(parent)
        blocks.append({'block_id': stable_id('block', [page_id, 'uncovered_text', path, indexes]),
            'block_type': 'other', 'text': clean_text(literal), 'normalized_text': clean_text(literal),
            'source_text': literal, 'source_text_origin': 'FROZEN_DOM_TEXT_RUN',
            'source_text_node_indexes': indexes, 'dom_path': path,
            'source_parent_locator': dom_path(parent.parent) if isinstance(parent.parent, Tag) else None,
            'element_name': parent.name, 'source_dom_order': run['position'],
            'section_id': page_id+'#unclassified', 'inline_nodes': [],
            'boundary_kind': 'unclassified_source',
            'boundary_id': stable_id('boundary', [page_id, 'unclassified_source', path]),
            'preservation_only': True, 'content_interpretation': 'UNCLASSIFIED_SOURCE',
            'order': len(blocks), 'definition_context': {}})
    # Keep legacy relative order and IDs; place new text by actual DOM position.
    blocks.sort(key=lambda b: b.get('source_dom_order', -1))
    for i, b in enumerate(blocks):
        b['order'] = i
    return {'source_root_locator': dom_path(root),
            'preservation_only_blocks': sum(bool(b.get('preservation_only')) for b in blocks),
            'policy': 'Unclassified source retained, no independent identity authority'}


def grid_observations(table, dom_path):
    """Physical cell positions and explicit header associations, not roles.

    Complex/invalid grids remain observable and ambiguous; never invent a
    Function/Description column from cell contents. Existing headers unchanged.
    """
    rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
    occupied = set(); cells = []; invalid = False
    for ri, row in enumerate(rows):
        column = 0
        for ci, node in enumerate(row.find_all(['td', 'th'], recursive=False)):
            while (ri, column) in occupied:
                column += 1
            spans = []
            for attr in ['rowspan', 'colspan']:
                try:
                    n = int(node.get(attr, 1))
                    if attr == 'rowspan' and n == 0:
                        n = len(rows)-ri
                    if not 1 <= n <= 1000:
                        raise ValueError()
                except (ValueError, TypeError):
                    n = 1; invalid = True
                spans.append(n)
            rs, cs = spans
            for y in range(ri, min(len(rows), ri+rs)):
                for x in range(column, column+cs):
                    if (y, x) in occupied:
                        invalid = True
                    occupied.add((y, x))
            cells.append({'row_index': ri, 'cell_index': ci, 'column_start': column,
                'column_end': column+cs-1, 'rowspan': rs, 'colspan': cs,
                'cell_kind': node.name.upper(), 'scope': node.get('scope'),
                'html_id': node.get('id'), 'header_ids': list(node.get('headers') or []),
                'dom_locator': dom_path(node), 'source_text': node.get_text('', strip=False)})
            column += cs
    ids = defaultdict(list)
    for c in cells:
        if c['html_id']:
            ids[c['html_id']].append(c)
    result = {}
    for ri, row in enumerate(rows):
        rowcells = [dict(c) for c in cells if c['row_index']==ri]
        for c in rowcells:
            if c['header_ids']:
                hs = [ids[h][0] for h in c['header_ids'] if len(ids[h])==1 and ids[h][0]['cell_kind']=='TH']
                ambiguous = len(hs)!=len(c['header_ids']) or any(not h['source_text'].strip() for h in hs)
            else:
                hs = [h for h in cells if h['cell_kind']=='TH' and h['source_text'].strip() and h['scope'] not in {'row','rowgroup'}
                      and h['row_index'] < ri and h['column_start']<=c['column_end']
                      and h['column_end']>=c['column_start']]
                ambiguous = not hs
            c['header_locators'] = [h['dom_locator'] for h in hs]
            c['header_status'] = 'UNKNOWN' if invalid or ambiguous else 'EXPLICIT'
        result[dom_path(row)] = {'cells': rowcells, 'structure_status': 'UNKNOWN' if invalid else 'OBSERVED'}
    return result
