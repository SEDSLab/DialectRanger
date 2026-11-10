"""Shared structural range proofs for existing Owners (not a new identity layer).

Discovery members remain stable identity anchors.  A proven definition scope is
an additive, ordered evidence range over original DocumentIR block IDs.
"""
from __future__ import annotations

import html
import re
from agents.contracts.identity_key import normalize_identity
from typing import Any, Mapping
from agents.owner.surface_patterns import formal_signatures, operator_label, datatype_definition_name


_LABELS = {'syntax', 'signature', 'grammar', 'description', 'parameters', 'arguments',
           'returns', 'return value', 'returned value', 'return type', 'examples',
           'example', 'usage', 'notes', 'note', 'constraints', 'results', 'result'}
_DEFINITION = re.compile(
    r'^(?:returns?|calculates?|computes?|converts?|represents?|defines?|specifies?|'
    r'rounds?|combines?|concatenates?|extracts?|evaluates?|produces?|creates?|'
    r'removes?|checks?|tests?|determines?|provides?|matches?|indicates?)\b', re.I)
_NAV = re.compile(r'(?:^| > )(?:nav|aside|footer)(?:[.#:\s>]|$)|'
                  r'(?:#|\.)(?:toc|sidebar|breadcrumb|table-of-contents)(?:[.#:\s>_-]|$)', re.I)
_EMBEDDED = re.compile(r'(?:^| > )(?:details|pre|code)(?:[.#:\s>]|$)', re.I)


def heading_text(value: Any) -> str:
    text = html.unescape(str(value or ''))
    text = re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'^\d+(?:\s*\.\s*\d+)*(?:\s*[.)])?\s+', '', text)


def heading_field_label(value, subject, labels):
    """A named field subsection must repeat this subject, not its neighbour."""
    text=heading_text(value).rstrip(':').strip().casefold()
    known={str(label).strip().casefold() for label in labels}
    if text in known:return text
    if subject:
        match=re.fullmatch(re.escape(str(subject).casefold())+r'(?:\(\))?\s+(.+)',text)
        if match and match[1] in known:return match[1]
        match=re.fullmatch(r'(.+?)\s+(?:of|for)\s+'+re.escape(str(subject).casefold())+r'(?:\(\))?',text)
        if match and match[1] in known:return match[1]
    return None


def normalized_name(value: Any) -> str:
    text = heading_text(value)
    from agents.contracts.definition_entry import CALLABLE_HEAD, QUALIFIED_IDENTIFIER
    if CALLABLE_HEAD.match(text) or re.fullmatch(QUALIFIED_IDENTIFIER,text):return normalize_identity(text)
    callable_heading = re.fullmatch(r'(?:The\s+)?([A-Za-z_][A-Za-z0-9_$]*)\s*\([^()]*\)\s*(?:(?:SQL\s+)?(?:function|routine))?', text, re.I)
    if callable_heading:
        return callable_heading.group(1).upper()
    typed_heading=re.fullmatch(r'(?:The\s+)?([A-Za-z_]\w*)\s+(?:datatype|data type|operator|predicate)',text,re.I)
    if typed_heading:return typed_heading.group(1).upper()
    if re.search(r'[A-Za-z_]', text):
        text = re.sub(r'\s*[¶🔗#]\s*$', '', text).strip()
    text = re.sub(r'^([A-Za-z_][A-Za-z0-9_$]*)\s+(?:function|aggregate|window function|operator|data\s+type)$', r'\1', text, flags=re.I)
    return normalize_identity(text.split('(', 1)[0].strip())


def navigation_block(block: Mapping[str, Any], *, article_present: bool = False) -> bool:
    path = str(block.get('dom_path') or '')
    attrs = block.get('semantic_attributes') or {}
    if str(block.get('block_kind') or '').upper() in {'NAV', 'NAVIGATION', 'FOOTER', 'ASIDE'}:
        return True
    if str(attrs.get('role') or '').casefold() in {'navigation', 'banner', 'contentinfo', 'complementary'} or _NAV.search(path):
        return True
    parts = path.split(' > ')
    for index, part in enumerate(parts):
        if re.match(r'^header(?:[.#:]|$)', part) and not _section_header(parts, index):
            return True
    # A reference link/list outside an observed article is not its definition,
    # even when a renderer leaves it with stale heading ancestry.
    return bool(article_present and path and ' > article' not in path and not path.startswith('article')
                and block.get('block_kind') in {'LIST_ITEM', 'LINK'})


def _section_header(parts, index):
    """A section's own header is not the site's header or navigation."""
    return (index > 0 and bool(re.match(r'^(?:section|article|div)(?:[.#:]|$)', parts[index-1]))
            and any(re.match(r'^(?:main|article)(?:[.#:]|$)', p) for p in parts[:index])
            and not any(re.match(r'^(?:nav|aside|footer)(?:[.#:]|$)', p) for p in parts[:index]))


def heading_container(heading):
    """Unwrap only an observed section-header wrapper, never an arbitrary DIV."""
    parts = str(heading.get('dom_path') or '').split(' > ')[:-1]
    for index,part in enumerate(parts):
        if re.match(r'^header(?:[.#:]|$)',part) and _section_header(parts,index):
            return ' > '.join(parts[:index])
    return ' > '.join(parts)


def execution_heading_container(heading):
    """Explicit A/B headings may be wrapped separately from their body.

    Stop at the nearest structural content boundary; heading order and subject
    checks still bound the range within it. Never promote to the whole DOM.
    """
    parts = heading_container(heading).split(' > ')
    for index in range(len(parts)-1, -1, -1):
        if re.match(r'^(?:main|article|section)(?:[.#:]|$)', parts[index]):
            return ' > '.join(parts[:index+1])
    return heading_container(heading)


def _parent(block):
    return str(block.get('dom_path') or '').rsplit(' > ', 1)[0]


def _contained(block, parent):
    path = str(block.get('dom_path') or '')
    return not parent or not path or path == parent or path.startswith(parent + ' > ')


def _nested_table(block, parent):
    """Ignore page-layout tables above the actual definition container."""
    path=str(block.get('dom_path') or '')
    relative=path[len(parent)+3:] if parent and path.startswith(parent+' > ') else path
    return bool(re.search(r'(?:^| > )(?:table|td|th)(?:[.#:]| > |$)',relative))


def _formal_names(block):
    text = html.unescape(str(block.get('text') or ''))
    text = re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    # SQL/example use never establishes a definition or alias relationship.
    if re.match(r'^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|VALUES)\b', text, re.I):
        return []
    if re.search(r'(?:^| > )details(?:[.#:\s>]|$)', str(block.get('dom_path') or '')):
        return []
    if block.get('block_kind') not in {'TEXT', 'CODE', 'SIGNATURE', 'LIST_ITEM', 'DEFINITION'}:
        return []
    text = re.sub(r'^\s*(?:syntax|signature)\s*:\s*', '', text, flags=re.I)
    return list(dict.fromkeys(m.group(1).upper() for m in re.finditer(
        r'(?:^|[|{])\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\(', text)))


def list_definition_scopes(document):
    """Prove signature-led repeated definitions from actual LI containment.

    Child paragraphs can share a generic repeated_unit enum. The DOM locator,
    not that lossy enum or a Profile declaration, proves the list boundary.
    Ordinary reference lists and example/API lists are not definitions.
    """
    groups = {}
    for block in document.get('blocks') or []:
        parts = str(block.get('dom_path') or '').split(' > ')
        positions = [i for i, part in enumerate(parts) if re.match(r'^li(?:[.#:]|$)', part)]
        for position in positions:
            groups.setdefault(' > '.join(parts[:position+1]), []).append(block)
    proofs = []
    for path, members in groups.items():
        members = sorted(members, key=lambda b: int(b.get('order') or 0))
        # Keep original leaf evidence, not the flattened aggregate of the LI.
        leaves = [b for b in members if b.get('block_kind') != 'LIST_ITEM']
        if not leaves:
            continue
        first = leaves[0]
        text = str(first.get('text') or '').strip()
        if first.get('block_kind') not in {'TEXT', 'CODE', 'SIGNATURE'}:
            continue
        signatures=formal_signatures(text)
        names=list(dict.fromkeys(n for n,_ in signatures))
        operator=operator_label(text) if not signatures else None
        context = ' / '.join(first.get('heading_ancestry') or [])
        operator_context=bool(re.search(r'\boperators?\b',context,re.I))
        # Keyword labels need an operator-only section, not a mixed chapter
        # where bare function names could otherwise be mistaken for operators.
        if operator and re.search(r'[A-Za-z]',operator[0]) and re.search(r'\b(?:functions?|routines?|data\s*types?)\b',context,re.I):
            operator_context=False
        if operator and re.match(r'^(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH)\b',operator[0]):
            operator_context=False
        if operator and operator_context and operator[0].casefold() not in _LABELS:
            names=[operator[0]]
        else:operator=None
        if len(names) != 1 or navigation_block(first) or re.search(
            r'\b(?:examples?|tutorial|API|SDK|JDBC|ODBC|programming|Python|Java)\b|C/C\+\+', context, re.I):
            continue
        if any((b.get('definition_context') or {}).get('role') == 'DEVELOPER_API_DECLARATION' for b in members):
            continue
        anchors = []
        for b in leaves:
            value = str(b.get('text') or '').strip()
            if ((not operator and formal_signatures(value)
                 and set(n for n,_ in formal_signatures(value))==set(names))
                    or (operator and b is first)):
                anchors.append(b['block_id'])
            else:
                break
        prose = [b for b in leaves[len(anchors):] if b.get('block_kind') == 'TEXT']
        operator_body=operator and prose and (
            any(b.get('block_kind')=='CODE' and re.match(r'^\s*(?:[\w.-]+>\s*)?(?:SELECT|VALUES)\b',str(b.get('text') or ''),re.I) for b in leaves)
            and bool(re.match(r'^[A-Za-z][^;\n]{0,300}',str(prose[0].get('text') or ''))))
        if not prose or not (operator_body or any(_DEFINITION.match(str(b.get('text') or '')) or re.search(
            r'\b'+re.escape(names[0])+r'\s*\([^)]*\)\s+(?:(?:function|routine)\s+)?'
            r'(?:is|returns?|adds?|subtracts?|converts?|computes?)\b', str(b.get('text') or ''), re.I)
            or re.match(r'^The\s+(?:first|second|third|former|latter)\s+(?:syntax|form)\s+(?:returns?|is)\b',
                        str(b.get('text') or ''), re.I) for b in prose[:2])):
            continue
        # An unlabeled second function declaration inside this container is
        # ambiguous, not an alias inferred merely from co-occurrence.
        if any(b['block_id'] not in anchors and b.get('block_kind') == 'TEXT'
               and formal_signatures(b.get('text'))
               and set(n for n,_ in formal_signatures(b.get('text')))!=set(names) for b in leaves):
            continue
        proofs.append({'section_id': 'list_definition:'+first['block_id'],
            'definition_pattern': 'OPERATOR_LED_LIST_ITEM' if operator else 'SIGNATURE_LED_LIST_ITEM',
            'feature_type': 'OPERATOR' if operator else None,
            'formal_signature_values': {b['block_id']:[v for _,v in formal_signatures(b['text'])] for b in leaves if b['block_id'] in anchors} if not operator else {},
            'operator_formal_expression': text if operator and operator[1] else None,
            'heading_block_id': first['block_id'],
            'start_order': first.get('order'), 'end_order_exclusive': int(members[-1].get('order') or 0)+1,
            'end_heading_block_id': None, 'source_anchor': document.get('url'),
            'dom_path': path, 'dom_parent': path, 'identity_values': names,
            'identity_anchor_block_ids': {names[0]: anchors}, 'primary_identity': names[0],
            'association': 'NATIVE_LIST_SIGNATURE_BODY', 'association_block_id': first['block_id'],
            'definition_anchor_block_ids': anchors, 'boundary_evidence': ['NATIVE_LI_CONTAINMENT', 'LEADING_FORMAL_SIGNATURE', 'LOCAL_DEFINITION_PROSE'],
            'block_ids': [b['block_id'] for b in leaves], 'scope_status': 'COMPLETE',
            'example_start_order': None})
    return proofs


def datatype_prose_scopes(document):
    """Bound explicit datatype self-definitions; never absorb shared prose."""
    proofs=[]
    for b in document.get('blocks') or []:
        if b.get('block_kind')!='TEXT' or navigation_block(b):continue
        context=' / '.join(b.get('heading_ancestry') or [])
        name=datatype_definition_name(b.get('text'))
        if not name:continue
        page_subject=bool(re.match(r'^(?:The\s+)?'+re.escape(name)+r'(?:\s+(?:data\s*)?types?)?(?:\s*[–—|\-]|\s*$)',
                                  str(document.get('title') or ''),re.I))
        if not re.search(r'\b(?:data\s*types?|datatypes?|types)\b',context,re.I) and not page_subject:continue
        if re.search(r'\b(?:examples?|API|SDK|JDBC|ODBC|programming|Java|Python|mapping|connector|external|tutorial)\b|C/C\+\+',context,re.I):continue
        if (b.get('definition_context') or {}).get('role'):continue
        bid=b['block_id']
        proofs.append({'section_id':'datatype_definition:'+bid,'definition_pattern':'DATATYPE_SELF_DEFINITION',
            'feature_type':'DATATYPE','heading_block_id':bid,'start_order':b.get('order'),
            'end_order_exclusive':int(b.get('order') or 0)+1,'end_heading_block_id':None,
            'source_anchor':document.get('url'),'dom_path':b.get('dom_path'),'dom_parent':_parent(b),
            'identity_values':[name],'identity_anchor_block_ids':{name:[bid]},'primary_identity':name,
            'association':'EXPLICIT_LOCAL_DATATYPE_SENTENCE','association_block_id':bid,
            'definition_anchor_block_ids':[bid],'boundary_evidence':['EXPLICIT_DATATYPE_SELF_DEFINITION','LOCAL_PARAGRAPH_BOUNDARY']+(['PAGE_SUBJECT_CORROBORATION'] if page_subject else []),
            'block_ids':[bid],'scope_status':'COMPLETE','example_start_order':None})
    return proofs


def paragraph_definition_scopes(document):
    """Repeated bold-label + complete signature + prose in sibling paragraphs.

    A bold name alone is never a Feature. The literal signature must corroborate
    the label in a function reference context; scope stops before the next label
    (even an unsupported one), not at the surrounding chapter's end.
    """
    blocks=sorted(document.get('blocks') or [],key=lambda b:int(b.get('order') or 0))
    def boundary_label(b):
        text=str(b.get('text') or '').strip()
        if b.get('block_kind')!='TEXT' or b.get('source_node_kind')!='P':return None
        if len(text)>100 or not re.fullmatch(r'[A-Z][A-Z0-9_$ ,/()-]*',text):return None
        path=str(b.get('dom_path') or '')
        if navigation_block(b) or re.search(r'(?:^| > )(?:li|dt|dd|td|th|pre|code|blockquote|details)(?:[.#:]| > |$)',path):return None
        return text if any(n.get('element_name') in {'strong','b'} and str(n.get('text') or '').strip()==text
                           for n in b.get('inline_nodes') or []) else None
    boundaries={i:boundary_label(b) for i,b in enumerate(blocks)}
    labels={i:v if v and re.fullmatch(r'[A-Z][A-Z0-9_$]*',v) else None for i,v in boundaries.items()}
    proofs=[]
    for i,name in labels.items():
        if not name or i+2>=len(blocks):continue
        first=blocks[i];context=' / '.join(first.get('heading_ancestry') or [])
        if not re.search(r'\b(?:functions?|routines?)\b',context,re.I):continue
        if re.search(r'\b(?:examples?|tutorial|usage|API|SDK|programming|parameters?|results?|mapping)\b',context,re.I):continue
        parent=_parent(first);signature=blocks[i+1]
        sigs=formal_signatures(signature.get('text'))
        if (_parent(signature)!=parent or not sigs or {n for n,_ in sigs}!={name}):continue
        if not any(n.get('element_name')=='code' and str(n.get('text') or '').strip()==str(signature.get('text') or '').strip()
                   for n in signature.get('inline_nodes') or []):continue
        end=i+2
        while end<len(blocks):
            b=blocks[end]
            if boundaries.get(end) or not _contained(b,parent):break
            if b.get('block_kind')=='HEADING' and normalized_name(b.get('text')).lower() not in _LABELS:break
            end+=1
        members=blocks[i:end];anchors=[];formal={}
        for b in members[1:]:
            spans=formal_signatures(b.get('text'))
            if spans and {n for n,_ in spans}=={name}:
                anchors.append(b['block_id']);formal[b['block_id']]=[v for _,v in spans]
            else:break
        prose=[b for b in members[1+len(anchors):] if b.get('block_kind')=='TEXT'
               and re.search(r'[a-z]{3}',str(b.get('text') or '')) and not formal_signatures(b.get('text'))]
        if not prose:continue
        proofs.append({'section_id':'paragraph_definition:'+first['block_id'],
            'definition_pattern':'PARAGRAPH_LABEL_SIGNATURE_SECTION','feature_type':'FUNCTION',
            'heading_block_id':signature['block_id'],'label_block_id':first['block_id'],
            'start_order':first.get('order'),'end_order_exclusive':int(members[-1].get('order') or 0)+1,
            'end_heading_block_id':None,'source_anchor':document.get('url'),
            'dom_path':parent,'dom_parent':parent,'identity_values':[name],'primary_identity':name,
            'identity_anchor_block_ids':{name:anchors},'definition_anchor_block_ids':anchors,
            'formal_signature_values':formal,'association':'SIBLING_LABEL_SIGNATURE_PROSE',
            'association_block_id':first['block_id'],'boundary_evidence':['EXACT_BOLD_LABEL','MATCHING_LITERAL_SIGNATURE','NEXT_LABEL_OR_SECTION_BOUNDARY'],
            'block_ids':[b['block_id'] for b in members],'scope_status':'COMPLETE','example_start_order':None})
    return proofs


def _native_definition_scopes(document: Mapping[str, Any], profile=None) -> list[dict[str, Any]]:
    """Find bounded heading definitions using structure PLUS local definition evidence."""
    blocks = sorted(document.get('blocks') or [], key=lambda b: int(b.get('order') or 0))
    from agents.owner.scope_plan import compile_scope_plan
    from agents.owner.structure_execution import field_labels, compile_structure_plan
    field_names = _LABELS | field_labels(profile)
    syntax_labels={'syntax','signature','grammar'}
    for plan in compile_structure_plan(profile):
        for field in (plan.get('execution') or {}).get('fields',[]):
            if field.get('role')=='FORMAL_SYNTAX' and field.get('locator',{}).get('kind')=='SECTION_LABEL':
                syntax_labels.update(str(v).casefold() for v in field['locator']['labels'])
    plans={p['relation']:p for p in compile_scope_plan(profile)}
    from agents.owner.member_collections import scopes as member_collection_scopes
    article = any('article' in str(b.get('dom_path') or '').split(' > ') for b in blocks)
    scopes = (list_definition_scopes(document) if 'SIGNATURE_CONTAINER' in plans else [])
    scopes += member_collection_scopes(document,profile)
    scopes += datatype_prose_scopes(document) if 'SELF_DEFINITION' in plans else []
    scopes += paragraph_definition_scopes(document) if 'PARAGRAPH_SIGNATURE' in plans else []
    from agents.owner.function_definitions import function_scopes
    declared_functions=[p for p in function_scopes(document,plans)
        if not any(old.get('label_block_id')==p['heading_block_id']
                   and old.get('identity_values')==p['identity_values'] for old in scopes)]
    scopes += declared_functions
    from agents.owner.reference_definitions import reference_scopes
    scopes += reference_scopes(document,plans)
    if 'HEADING_SECTION' in plans:
        from agents.owner.composite_definition import scopes as composite_scopes
        from agents.owner.structure_execution import compile_structure_plan
        selected=compile_structure_plan(profile)
        field_plan=next((p for p in selected if p['structure']=='B'),
                        next((p for p in selected if p['structure']=='A'),{}))
        for proof in composite_scopes(document, plans['HEADING_SECTION'], field_plan):
            scopes = [p for p in scopes if not (p.get('heading_block_id') == proof['heading_block_id']
                and p.get('identity_values') == proof['identity_values']
                and set(p['block_ids']) <= set(proof['block_ids']))]
            scopes.append(proof)
    if 'CATALOG_ROW' in plans:
        from agents.owner.composite_definition import scopes as composite_scopes
        from agents.owner.structure_execution import compile_structure_plan
        row_plan=next((p for p in compile_structure_plan(profile) if p['structure']=='D'),{})
        scopes += composite_scopes(document, plans['CATALOG_ROW'], row_plan, catalog=True)
    from agents.owner.definition_patterns import execute_patterns
    from agents.owner.structure_execution import enabled_structures
    for proof in (execute_patterns(document,profile) if 'C' in enabled_structures(profile) else []):
        if not any((p.get('feature_type')==proof['feature_type'] or
                    (not p.get('feature_type') and proof.get('pattern_grammar')=='CALLABLE_DEFINITION'))
                   and p['identity_values']==proof['identity_values']
                   and set(proof['definition_anchor_block_ids'])<=set(p['definition_anchor_block_ids']) for p in scopes):
            scopes.append(proof)
    scopes.extend(p for p in document.get('definition_source_relation_scopes') or []
                  if ('CATALOG_MEMBER_CONTEXT' if p.get('source_role')=='CATALOG_MEMBER' or p.get('configured_structure')=='E'
                      else 'PAGE_SUBJECT_RELATION') in plans)
    for block in blocks:
        observed=block.get('definition_context') or {}
        if observed.get('role')=='STORAGE_CLASS_DEFINITION' and 'STORAGE_CLASS' in plans:
            name=observed['identity'];bid=block['block_id']
            scopes.append({'section_id':'local_definition:'+bid,'heading_block_id':bid,
                'start_order':block.get('order'),'end_order_exclusive':int(block.get('order') or 0)+1,
                'end_heading_block_id':None,'source_anchor':document.get('url'),
                'dom_path':block.get('dom_path'),'dom_parent':_parent(block),
                'identity_values':[name],'identity_anchor_block_ids':{name:[bid]},'primary_identity':name,
                'association':'OBSERVED_STORAGE_CLASS_DEFINITION','association_block_id':bid,
                'definition_anchor_block_ids':[bid],'boundary_evidence':['EXPLICIT_LOCAL_DATATYPE_LABEL'],
                'block_ids':[bid],'scope_status':'COMPLETE','example_start_order':None})
    # Native DT/DD is already a definition relationship. Do not require its
    # subject to be repeated as a heading, and never cross the next DT.
    for start, term in enumerate(blocks):
        if 'TERM_BODY_PAIR' not in plans:continue
        if term.get('source_node_kind') != 'DT':
            continue
        if (term.get('definition_context') or {}).get('role')=='DEVELOPER_API_DECLARATION':continue
        parent = _parent(term)
        names = list(dict.fromkeys(m.group(1).upper() for m in re.finditer(
            r'\b([A-Za-z_][A-Za-z0-9_$]*)\s*\(', str(term.get('text') or ''))))
        # Signature parameter types and callback grammars are not additional
        # subjects. Native nominal signature spans identify only the head.
        nominal=[str(n.get('text','')).strip().upper() for n in term.get('inline_nodes',[])
                 if 'sig-name' in n.get('semantic_classes',[])
                 and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*',str(n.get('text','')).strip())]
        if nominal:names=list(dict.fromkeys(nominal))
        if not names:
            continue
        end = start + 1
        while end < len(blocks) and _contained(blocks[end], parent):
            if blocks[end].get('source_node_kind') == 'DT' and _parent(blocks[end]) == parent:
                break
            end += 1
        section = blocks[start:end]
        if not any(b.get('source_node_kind') == 'DD' for b in section):
            continue
        scopes.append({'section_id': 'definition_entry:'+term['block_id'],
            'heading_block_id': term['block_id'], 'start_order': term.get('order'),
            'end_order_exclusive': blocks[end].get('order') if end < len(blocks) else None,
            'end_heading_block_id': None, 'source_anchor': str(document.get('url') or ''),
            'dom_path': term.get('dom_path'), 'dom_parent': parent,
            'identity_values': names, 'identity_anchor_block_ids': {n:[term['block_id']] for n in names},
            'primary_identity': names[0], 'association': 'NATIVE_DEFINITION_TERM_BODY',
            'association_block_id': term['block_id'], 'definition_anchor_block_ids':[term['block_id']],
            'boundary_evidence':['NATIVE_DT_DD_PAIR','DOM_PARENT_CONTAINMENT','LOCAL_FORMAL_SIGNATURE'],
            'block_ids':[b['block_id'] for b in section], 'scope_status':'COMPLETE', 'example_start_order':None})
    for start, heading in enumerate(blocks):
        if 'HEADING_SECTION' not in plans:continue
        if any(p['heading_block_id']==heading.get('block_id') for p in declared_functions):continue
        if heading.get('block_kind') != 'HEADING' or navigation_block(heading, article_present=article):
            continue
        if _EMBEDDED.search(str(heading.get('dom_path') or '')):
            continue
        subject = normalized_name(heading.get('text'))
        if subject.casefold() in field_names or not re.fullmatch(r'[A-Z_][A-Z0-9_$]*(?: [A-Z_][A-Z0-9_$]*){0,3}', subject):
            continue
        level = int(heading.get('heading_level') or 6)
        parent = heading_container(heading)
        end = len(blocks)
        ambiguous_child = False
        for pos in range(start + 1, len(blocks)):
            other = blocks[pos]
            if other.get('block_kind') != 'HEADING' or navigation_block(other, article_present=article):
                continue
            if not _contained(other, parent) or _EMBEDDED.search(str(other.get('dom_path') or '')):
                continue
            if int(other.get('heading_level') or 6) <= level:
                end = pos
                break
            if heading_field_label(other.get('text'), subject, field_names) is None:
                end = pos
                ambiguous_child = True
                break
        section = [b for b in blocks[start:end] if _contained(b, parent) and not navigation_block(b, article_present=article)]
        section_ids={b['block_id'] for b in section}
        if any(p.get('definition_pattern') in {'SIGNATURE_LED_LIST_ITEM','OPERATOR_LED_LIST_ITEM','DATATYPE_SELF_DEFINITION','PARAGRAPH_LABEL_SIGNATURE_SECTION'}
               and p['heading_block_id'] in section_ids and p['primary_identity']!=subject for p in scopes):
            # A chapter containing independent proven definitions is not a
            # second single-feature definition of its chapter title.
            continue
        # Explicit local Syntax/Signature labels distinguish a declaration
        # from explanatory formulas that invoke other functions later on.
        syntax_regions=[]
        for candidate in section:
            marked=any(str(n.get('text') or '').strip().rstrip(':').casefold() in syntax_labels
                       and (n.get('element_name') in {'b','strong'} or set(n.get('semantic_classes') or []) & {'title','label'})
                       and str(candidate.get('text') or '').startswith(str(n.get('text') or ''))
                       for n in candidate.get('inline_nodes',[]))
            path=str(candidate.get('dom_path') or '')
            if marked and any(subject in _formal_names(b) for b in section
                              if b.get('dom_path')==path or str(b.get('dom_path') or '').startswith(path+' > ')):
                syntax_regions.append(path)
        formal = []
        grammar_blocks = []
        definition_prefix = []
        for block in section[1:]:
            label = heading_field_label(block.get('text'), subject, field_names)
            if label in {'example', 'examples', 'usage'}:
                break
            definition_prefix.append(block)
            table_cell=_nested_table(block,parent)
            names=_formal_names(block)
            if syntax_regions and not any(block.get('dom_path')==p or str(block.get('dom_path') or '').startswith(p+' > ') for p in syntax_regions):
                names=[name for name in names if name==subject]
            # A matching syntax table can define this heading (common in
            # reference manuals). Other field-table names do not declare a
            # second subject or disprove the section's matching signature.
            if table_cell and subject not in names:names=[]
            for name in names:
                formal.append((name, block['block_id']))
            text = str(block.get('text') or '').strip()
            if block.get('block_kind') in {'CODE', 'SIGNATURE'} and not _EMBEDDED.search(
                str(block.get('dom_path') or '').rsplit(' > ', 1)[0]) and not re.match(
                    r'^(?:SELECT|WITH|INSERT|UPDATE|DELETE|VALUES)\b', text, re.I) and re.match(
                        r'^[\s{|]*[A-Za-z_][A-Za-z0-9_$]*(?:\s+[A-Za-z_][A-Za-z0-9_$]*)*\s*(?:\(|\[|\||}|$)', text) and (
                            not table_cell or re.search(r'(?:^|[|{])\s*'+re.escape(subject)+r'(?=\s|[([{]|$)',text,re.I)):
                grammar_blocks.append(block)
        prose = [b for b in definition_prefix if b.get('block_kind') == 'TEXT'
                 and not _EMBEDDED.search(str(b.get('dom_path') or ''))
                 and not _nested_table(b,parent)]
        defining_prose = next((b for b in prose if _DEFINITION.search(str(b.get('text') or ''))
            or re.match(r'^\s*(?:The\s+)?' + re.escape(subject) + r'\s*\([^)]*\)\s+(?:SQL\s+)?(?:function|routine)\b', str(b.get('text') or ''), re.I)
            or re.match(r'^\s*' + re.escape(subject) + r'\s+(?:is|returns?|represents?)\b', str(b.get('text') or ''), re.I)), None)
        if (heading.get('definition_context') or {}).get('role')=='DATATYPE_DEFINITION_HEADING' and prose:
            defining_prose=prose[0]
        reference_context = bool(re.search(r'\b(?:functions?|routines?|data[ -]?types?|type declarations?)\b',
                                          ' '.join(heading.get('heading_ancestry') or []), re.I))
        declaration = next((b for b in grammar_blocks if re.search(
            r'(?:^|[|{])\s*' + re.escape(subject) + r'(?=\s|[([{]|$)', b.get('text') or '', re.I)), None)
        local_grammar = declaration or (grammar_blocks[0] if reference_context and grammar_blocks else None)
        if subject not in {name for name, _ in formal} and defining_prose is None and local_grammar is None:
            continue
        formal_blocks = {bid for value, bid in formal if value == subject}
        if declaration is not None:
            formal_blocks.add(declaration['block_id'])
        related_names = {value for value, bid in formal if bid in formal_blocks} | {subject}
        if any(value not in related_names for value, _ in formal):
            # Separate callable heads are not aliases merely because they
            # occur before the next heading. Only alternatives in the same
            # formal surface (or repeated signatures of the same head) share
            # this definition proof.
            continue
        # A containing chapter with another unlabelled feature heading is not
        # silently certified as a complete single-feature section.
        if ambiguous_child:
            continue
        fragment = re.search(r'#[^ >]+', str(heading.get('dom_path') or ''))
        scopes.append({
            'section_id': heading.get('section_id') or 'definition_section:' + str(heading['block_id']),
            'heading_block_id': heading['block_id'], 'start_order': heading.get('order'),
            'end_order_exclusive': blocks[end].get('order') if end < len(blocks) else None,
            'end_heading_block_id': blocks[end]['block_id'] if end < len(blocks) else None,
            'source_anchor': str(document.get('url') or '') + (fragment.group(0) if fragment else ''),
            'dom_path': heading.get('dom_path'), 'dom_parent': parent,
            'identity_values': list(dict.fromkeys([subject] + [name for name, _ in formal if name in related_names])),
            'identity_anchor_block_ids': {name: list(dict.fromkeys([bid for n, bid in formal if n == name]))
                                          for name, _ in formal},
            'primary_identity': subject, 'association': 'EXACT_DEFINITION_HEADING',
            'association_block_id': heading['block_id'],
            'definition_anchor_block_ids': list(dict.fromkeys([heading['block_id']] +
                [bid for name, bid in formal if name == subject] + ([defining_prose['block_id']] if defining_prose else []) +
                ([local_grammar['block_id']] if local_grammar else []))),
            'boundary_evidence': ['HEADING_LEVEL', 'DOM_PARENT_CONTAINMENT' if parent else 'IR_HEADING_ORDER',
                'LOCAL_FORMAL_SIGNATURE' if subject in {name for name, _ in formal} else
                'LOCAL_DECLARATION_GRAMMAR' if local_grammar else 'LOCAL_DEFINITION_PROSE'],
            'block_ids': [b['block_id'] for b in section], 'scope_status': 'COMPLETE',
            'example_start_order': next((b.get('order') for b in section[1:]
                if heading_field_label(b.get('text'), subject, {'example', 'examples', 'usage'})), None),
        })
    # DocumentIR may attach every wrapped heading to one coarse page section.
    # Definition identity is the physical defining anchor, not that grouping.
    section_anchors = {}
    for proof in scopes:
        section_anchors.setdefault(proof.get('section_id'), set()).add(proof.get('heading_block_id'))
    for proof in scopes:
        if len(section_anchors[proof.get('section_id')]) > 1:
            proof['source_section_id'] = proof['section_id']
            proof['section_id'] = 'definition_section:' + str(proof['heading_block_id'])
        relation={'NATIVE_DEFINITION_TERM_BODY':'TERM_BODY_PAIR',
                  'EXACT_DEFINITION_HEADING':'HEADING_SECTION',
                  'OBSERVED_STORAGE_CLASS_DEFINITION':'STORAGE_CLASS'}.get(proof.get('association'))
        if relation is None:
            relation={'SIGNATURE_LED_LIST_ITEM':'SIGNATURE_CONTAINER','OPERATOR_LED_LIST_ITEM':'SIGNATURE_CONTAINER',
                      'DATATYPE_SELF_DEFINITION':'SELF_DEFINITION',
                      'PARAGRAPH_LABEL_SIGNATURE_SECTION':'PARAGRAPH_SIGNATURE'}.get(proof.get('definition_pattern'))
        if relation:
            proof['scope_execution_plan']=dict(plans[relation])
    return scopes


def definition_scopes(document: Mapping[str, Any], profile=None) -> list[dict[str, Any]]:
    """The single production dispatcher for Profile-selected A--E structures."""
    from agents.owner.structure_execution import execute_structures
    return execute_structures(document, profile, native_executor=_native_definition_scopes)['proofs']


def scope_for_owner(owner, document, scopes):
    """Associate an existing small anchor Owner with one proven definition."""
    from agents.owner.structure_execution import composed_definition
    selector=owner.get('profile_proof_selector')
    if selector:
        scopes=[p for p in scopes if p.get('section_id')==selector
                and (p.get('member_collection') or
                    (p.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2'
                    and p['structure_execution']['structure']=='D')]
    members = set(owner.get('member_block_ids') or [])
    blocks = [b for b in document.get('blocks') or [] if b.get('block_id') in members]
    raw = ' '.join(str(b.get('text') or '') for b in blocks)
    if re.search(r'\bAuthors?\s*:', raw, re.I) and re.search(r'\bPublisher\s*:', raw, re.I):
        return {'evidence_scope_status': 'UNKNOWN', 'owner_scope_reason_codes': ['BOOK_METADATA_NOT_DEFINITION']}
    article = any('article' in str(b.get('dom_path') or '').split(' > ') for b in document.get('blocks') or [])
    if blocks and all(navigation_block(b, article_present=article) for b in blocks):
        return {'evidence_scope_status': 'EXCLUDED_NAVIGATION', 'owner_scope_block_ids': [],
                'owner_scope_reason_codes': ['NAVIGATION_NOT_DEFINITION']}
    bounded_page=any(composed_definition(p)
        and (p.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2'
        and p['structure_execution'].get('structure')=='A'
        and members==set(p.get('block_ids',[])) for p in scopes)
    if (owner.get('owner_kind') in {'PageOwner', 'Page'} or owner.get('owner_scope_locality') == 'BROAD') and not bounded_page:
        return {}
    headings = {normalized_name(b.get('text')) for b in blocks if b.get('block_kind') == 'HEADING'
                and normalized_name(b.get('text')).casefold() not in _LABELS
                and not _EMBEDDED.search(str(b.get('dom_path') or ''))}
    certified_reference=any((p.get('definition_pattern') in {'REFERENCE_DECLARATION_SECTION','FUNCTION_DECLARATION_SECTION'} or composed_definition(p))
                            and members==set(p['block_ids']) for p in scopes)
    if len(headings) > 1 and not certified_reference and any(p['heading_block_id'] in members for p in scopes):
        return {'evidence_scope_status': 'EXCLUDED_AMBIGUOUS_DEFINITION_SCOPE', 'owner_scope_block_ids': [],
                'owner_scope_reason_codes': ['MULTIPLE_DEFINITION_HEADINGS_IN_ANCHOR_OWNER']}
    matched = [p for p in scopes if members and members <= set(p['block_ids'])]
    native=[p for p in matched if (composed_definition(p) or p.get('definition_pattern') in {
        'SIGNATURE_LED_LIST_ITEM','OPERATOR_LED_LIST_ITEM','DATATYPE_SELF_DEFINITION','PARAGRAPH_LABEL_SIGNATURE_SECTION','PROFILE_LOCAL_DEFINITION','SOURCE_RELATION_DEFINITION','FUNCTION_DECLARATION_SECTION','REFERENCE_DECLARATION_SECTION'})
        and members.intersection(p['definition_anchor_block_ids'])]
    if len(native)==1:matched=native
    if owner.get('owner_kind') == 'TableRowOwner':
        # Preserve reference grammar rows that are exact heading anchors (not
        # arbitrary catalog/result rows inside a feature section).
        matched = [p for p in matched if composed_definition(p) or all(normalized_name(b.get('text')) == p['primary_identity']
            and not _EMBEDDED.search(str(b.get('dom_path') or ''))
            and (p['example_start_order'] is None or int(b.get('order') or 0) < int(p['example_start_order'])) for b in blocks)]
    if len(matched) != 1:
        return {}
    proof = matched[0]
    anchored = any(normalized_name(b.get('text')) in proof['identity_values'] or
        (composed_definition(proof) and b.get('block_id') in proof['definition_anchor_block_ids']) or
        (proof.get('definition_pattern') in {'PROFILE_LOCAL_DEFINITION','SOURCE_RELATION_DEFINITION','FUNCTION_DECLARATION_SECTION','REFERENCE_DECLARATION_SECTION'} and b.get('block_id') in proof['definition_anchor_block_ids']) or
        (proof.get('definition_pattern')=='OPERATOR_LED_LIST_ITEM' and operator_label(b.get('text'))
         and operator_label(b.get('text'))[0] in proof['identity_values']) or
        (proof.get('definition_pattern')=='DATATYPE_SELF_DEFINITION' and datatype_definition_name(b.get('text')) in proof['identity_values']) or
        ((b.get('definition_context') or {}).get('role') in {'STORAGE_CLASS_DEFINITION','DATATYPE_DEFINITION_HEADING'}
         and (b.get('definition_context') or {}).get('identity') in proof['identity_values']) or
        re.search(r'(?<![A-Za-z0-9_$])' + re.escape(proof['primary_identity']) + r'\s*\(', str(b.get('text') or ''), re.I)
        for b in blocks)
    return {'owner_scope_version': 'owner-definition-scope/v1','profile_proof_selector':selector,
            # A profile-verified definition owns its declared heads, not every
            # table label or incidental callable nested in its body. Reuse the
            # existing identity binding contract; retain body blocks as evidence.
            **({'owner_scope_identity_values': list(proof['identity_values'])}
               if composed_definition(proof) or proof.get('definition_pattern') in {'PROFILE_LOCAL_DEFINITION','SOURCE_RELATION_DEFINITION','FUNCTION_DECLARATION_SECTION','REFERENCE_DECLARATION_SECTION'} else {}),
            'owner_scope_mode': 'PROVEN_DEFINITION_SECTION', 'owner_scope_locality': 'BOUNDED',
            'owner_scope_block_ids': list(proof['block_ids']),
            'owner_definition_identities': list(proof['identity_values']),
            'owner_definition_anchor_compatible': bool(anchored),
            'definition_section_proofs': [dict(proof)], 'evidence_scope_status': 'COMPLETE',
            'owner_scope_reason_codes': list(proof['boundary_evidence'])}
