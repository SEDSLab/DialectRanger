"""Nested definition fields over literal IR rows; no DB/feature vocabulary.

A heading/entry owns the subject. Its field table is not a second catalog.
Every field carries an immutable row/column witness and a physical table parent.
"""
import re
from collections import defaultdict
from agents.owner.surface_patterns import formal_signatures

PATTERN = 'COMPOSITE_FIELD_DEFINITION'


def norm(value):
    return ' '.join(str(value or '').split()).strip().rstrip(':').casefold()


def table_path(block):
    path = block.get('dom_path') or ''
    return path.rsplit(' > tr', 1)[0].rsplit(' > tbody', 1)[0].rsplit(' > thead', 1)[0]


def field_members(blocks, plan):
    from agents.owner.structure_execution import _labels
    labels = {norm(label): role for role, values in _labels(plan).items() for label in values}
    labels.update({'result': 'EXAMPLE_RESULT', 'results': 'EXAMPLE_RESULT',
                   'output': 'EXAMPLE_RESULT', 'alias': 'ALIAS', 'aliases': 'ALIAS'})
    tables = defaultdict(list)
    for block in blocks:
        metadata = block.get('table_metadata') or {}
        cells = metadata.get('literal_cells') or metadata.get('cells') or []
        if block.get('block_kind') != 'TABLE_ROW' or len(cells) != 2:
            continue
        label = norm(cells[0])
        role = labels.get(label) or labels.get(re.sub(r'\s+\d+$', '', label))
        if role and str(cells[1]).strip():
            tables[table_path(block)].append({'block_id': block['block_id'], 'field_role': role,
                'field_label': str(cells[0]), 'value': str(cells[1]), 'cell_index': 1,
                'table_locator': table_path(block), 'order': block.get('order'),
                'role_origin': 'OBSERVED_DEFINITION_FIELD_CELL'})
    # One occurrence of "Example" in a data table does not establish a field table.
    return [m for members in tables.values()
            if 'DESCRIPTION' in {m['field_role'] for m in members}
            and len({m['field_role'] for m in members}) >= 2 for m in members]


def heading_subject(heading):
    from agents.owner.reference_definitions import names_from_heading
    text = str(heading.get('text') or '')
    typed = names_from_heading(text)
    if typed and len(typed[1]) == 1:
        return typed[0], typed[1][0], []
    explicit = re.fullmatch(r'([A-Za-z_][\w$]*)\s+(?:SQL\s+)?(?:function|routine)', text, re.I)
    if explicit:
        return 'FUNCTION', explicit.group(1).upper(), []
    signatures = formal_signatures(text)
    context = ' / '.join(heading.get('heading_ancestry') or [])
    # This parser is consumed only with a verified Description+field table.
    # A complete declaration there need not have an artificial Function h1.
    if (len({name for name, _ in signatures}) == 1
            and not re.search(r'\b(?:data[ -]?types?|API|JDBC|ODBC|examples?|tutorial)\b', context, re.I)):
        return 'FUNCTION', signatures[0][0], signatures
    return None


def catalog_subject(block):
    from agents.contracts.definition_entry import observed_callable_declaration
    parsed = observed_callable_declaration(block.get('text'), block.get('inline_nodes') or [])
    nominal = any(n.get('element_name') == 'code' and 'function' in n.get('semantic_classes', [])
                  for n in block.get('inline_nodes') or [])
    return ('FUNCTION', parsed[0], [(parsed[0], parsed[1])]) if parsed and (parsed[2] or nominal) else None


def scopes(document, plan, field_plan=None, *, catalog=False):
    from agents.owner.definition_scope import navigation_block
    blocks = sorted(document.get('blocks') or [], key=lambda b: b.get('order') or 0)
    result = []
    for i, heading in enumerate(blocks):
        if heading.get('block_kind') != ('TEXT' if catalog else 'HEADING') or navigation_block(heading):
            continue
        context = ' / '.join(heading.get('heading_ancestry') or [])
        if re.search(r'\b(?:examples?|API|SDK|JDBC|ODBC|tutorial|connector|mapping)\b', context, re.I):
            continue
        parsed = catalog_subject(heading) if catalog else heading_subject(heading)
        if not parsed:
            continue
        kind, name, signatures = parsed
        parent = str(heading.get('dom_path') or '').rsplit(' > ', 1)[0]
        if catalog:
            # A real declaration in the first catalog cell may contain its own
            # field table even when topology deliberately omits the outer TR.
            match = re.fullmatch(r'(.* > tr(?:#[^ >]+|:nth-of-type\(\d+\))?) > t[dh](?:#[^ >]+|:nth-of-type\(1\))?', parent)
            if not match:
                continue
            outer_table = table_path({'dom_path':match.group(1)})
            from agents.contracts.definition_entry import compound_header_role
            if not any(table_path(b) == outer_table and any(compound_header_role(h, 'FUNCTION')
                    for h in (b.get('table_metadata') or {}).get('headers', []))
                    for b in blocks if b.get('block_kind') == 'TABLE_ROW'):
                continue
        members = [heading]
        for block in blocks[i + 1:]:
            if not str(block.get('dom_path') or '').startswith(parent + ' > '):
                break
            if block.get('block_kind') == 'HEADING' and (block.get('heading_level') or 1) <= (heading.get('heading_level') or 1):
                break
            if block.get('block_kind') == 'HEADING' and (
                    any(n != name for n, _ in formal_signatures(block.get('text')))
                    or (heading_subject(block) and heading_subject(block)[1] != name)):
                break
            if navigation_block(block):
                break
            members.append(block)
        fields = field_members(members, field_plan or {})
        if not fields:
            continue
        bid = heading['block_id']
        result.append({'section_id': 'composite_definition:' + bid, 'definition_pattern': PATTERN,
            'feature_type': kind, 'primary_identity': name, 'identity_values': [name],
            'heading_block_id': bid, 'label_block_id': bid,
            'identity_anchor_block_ids': {name: [bid]}, 'definition_anchor_block_ids': [bid],
            'formal_signature_values': {bid: [s for _, s in signatures]},
            'subject_formal_values': {name: {bid: [s for _, s in signatures]}},
            'block_ids': [b['block_id'] for b in members], 'dom_parent': parent,
            'dom_path': heading.get('dom_path'), 'start_order': heading.get('order'),
            'end_order_exclusive': members[-1]['order'] + 1, 'scope_status': 'COMPLETE',
            'source_anchor': document.get('url'), 'association': 'SUBJECT_HEADING_WITH_FIELD_TABLE',
            'association_block_id': bid, 'example_start_order': None,
            'boundary_evidence': ['PEER_HEADING_BOUNDARY', 'WHOLE_CALLABLE_HEADING', 'LOCAL_LABELED_FIELD_TABLE'],
            'scope_execution_plan': dict(plan),
            'reference_mode': 'COMPOUND_CATALOG_ROW' if catalog else 'HEADING_SECTION',
            'field_plan': dict(field_plan or {}),
            'source_citations': {b['block_id']: {k: b.get(k) for k in
                ['block_id', 'text', 'dom_path', 'table_metadata', 'block_kind', 'order', 'heading_ancestry']} for b in members}})
    return result


def verified(proof, block, name):
    if proof.get('definition_pattern') != PATTERN or block.get('block_id') != proof.get('heading_block_id'):
        return None
    citation = (proof.get('source_citations') or {}).get(block['block_id'], {})
    if any(citation.get(k) != block.get(k) for k in ['text', 'dom_path']):
        return None
    parsed = catalog_subject(block) if proof.get('reference_mode') == 'COMPOUND_CATALOG_ROW' else heading_subject(block)
    if not parsed or parsed[1] != name or proof.get('identity_values') != [name] or parsed[0] != proof.get('feature_type'):
        return None
    citations = list((proof.get('source_citations') or {}).values())
    if not field_members(citations, proof.get('field_plan') or {}):
        return None
    return {'feature_type': parsed[0], 'identity_values': [name], 'formal_value': parsed[2][0][1] if parsed[2] else None}


def verified_fields(unit, evidence, identity):
    """Revalidate declared roles against bound, source-observed literal cells."""
    execution = unit.get('structure_execution') or {}
    if (unit.get('binding_status') != 'COMPLETE' or execution.get('status') != 'VERIFIED_STRUCTURE'
            or identity.upper() not in unit.get('identity_values', [])):
        return []
    result = []
    refs = set(unit.get('member_evidence_refs') or [])
    observed_rows = []
    for bid in refs:
        block = evidence.get(bid, {})
        cells = (block.get('table_context') or {}).get('cells') or []
        if (block.get('block_kind') == 'TABLE_ROW' and block.get('source_structure_status') == 'RESOLVED'
                and block.get('source_hash') == unit.get('source_hash') and block.get('source_url') == unit.get('source_url')
                and len(cells) == 2 and all(c.get('colspan', 1) == 1 and not c.get('nested_table') for c in cells)):
            observed_rows.append(dict(block, table_metadata={'cells':[c.get('text') for c in cells]}))
    observed = {m['block_id']:m for m in field_members(observed_rows, execution.get('plan') or {})}
    for field in execution.get('field_members', []):
        block = evidence.get(field['block_id'], {})
        if (field['block_id'] not in refs or block.get('source_hash') != unit.get('source_hash')
                or block.get('source_url') != unit.get('source_url')
                or block.get('source_structure_status') != 'RESOLVED'):
            continue
        cells = (block.get('table_context') or {}).get('cells') or []
        actual = observed.get(field['block_id'], {})
        if (len(cells) != 2 or any(actual.get(k) != field.get(k) for k in
                ['field_role','field_label','value','cell_index','table_locator'])):
            continue
        if not str(block.get('dom_path') or '').startswith(field['table_locator'] + ' > '):
            continue
        result.append(dict(field, definition_unit_id=unit['definition_unit_id']))
    return result
