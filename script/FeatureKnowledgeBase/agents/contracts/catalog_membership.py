"""Source-verified catalog membership, independent of semantic completeness.

Profile roles can guide discovery, but cannot substitute for the observed row,
its headers, local section context, or exact identity cell.
"""
import re
from urllib.parse import urljoin, urlparse

from agents.owner.surface_patterns import formal_signatures, operator_label
from agents.contracts.definition_entry import declaration_names, identifier_key


def catalog_citation_valid(unit, block):
    """Adapted identities must actually cite this source, not borrow an Owner ID."""
    if not unit.get('adapted_identity_kind'):
        return True
    return any(item.get('block_id') == block.get('block_id')
               and item.get('source_url') == block.get('source_url')
               and item.get('page_id') == block.get('page_id')
               and (item.get('corpus_hash') or item.get('page_hash')) == block.get('source_hash')
               and item.get('owner_id') in (block.get('owner_ids') or [])
               and item.get('owner_id') in (unit.get('owner_ids') or [])
               for item in unit.get('adaptation_provenance') or [])


def catalog_membership(block, name):
    """Return typed local cell proofs; never search description/example cells."""
    if (block.get('block_kind') != 'TABLE_ROW'
            or block.get('source_structure_status') != 'RESOLVED'
            or not block.get('source_url') or not block.get('source_hash')):
        return []
    context = block.get('table_context') or {}
    cells = context.get('cells') or []
    headings = block.get('heading_ancestry') or []
    observed = block.get('observed_context') or {}
    labels = ' / '.join(str(v) for v in headings)
    if (observed.get('feature_domain') == 'EMBEDDED_QUERY_LANGUAGE'
            or re.search(r'\b(?:examples?|parameters?|arguments?|results?|output|navigation|'
                         r'mapping|mappings|connectors?|jdbc|odbc|api|sdk|configuration|'
                         r'programming|tutorial|migration)\b', labels, re.I)):
        return []
    # Use the nearest typed section, keeping mixed sections genuinely mixed.
    kinds = set()
    for label in headings:
        local = set()
        for kind, pattern in [('FUNCTION', r'\b(?:functions?|routines?)\b'),
                              ('OPERATOR', r'\boperators?\b'),
                              ('DATATYPE', r'\b(?:data[ -]?types?|types|storage classes)\b')]:
            if re.search(pattern, str(label), re.I):
                local.add(kind)
        if local:
            kinds = local
    # Description is a structural column label, NOT a required nonempty field.
    if not kinds or not any(re.fullmatch(r'\s*description\s*', str(h.get('text') or ''), re.I)
                            for cell in cells for h in cell.get('headers') or []):
        return []
    result = []
    for cell in cells:
        if cell.get('header_role') == 'COLUMN_HEADER' or not cell.get('dom_locator'):
            continue
        named_column = any(re.fullmatch(r'\s*(?:name|feature(?: name)?)\s*', str(h.get('text') or ''), re.I)
                           for h in cell.get('headers') or [])
        alias_column = (kinds == {'DATATYPE'} and any(
            re.fullmatch(r'\s*alias(?:es)?\s*', str(h.get('text') or ''), re.I)
            for h in cell.get('headers') or []) and any(
                any(re.fullmatch(r'\s*name\s*', str(h.get('text') or ''), re.I)
                    for h in primary.get('headers') or []) and declaration_names(primary.get('text') or '')
                for primary in cells))
        # An explicit HTML row header is already the row's identity slot. The
        # structure reader correctly does not treat it as an ordinary data cell
        # or attach column headers to it. Do not lose accessible catalog markup.
        row_identity = (cell.get('header_role') == 'ROW_HEADER'
                        and cell.get('column_start') == cell.get('column_end') == 0
                        and cell.get('rowspan') == 1)
        if not (named_column or row_identity or alias_column):
            continue
        text = str(cell.get('text') or '').strip()
        norm = ' '.join(text.split()).upper()
        signatures = formal_signatures(text)
        kind = None
        if signatures and {head for head, _ in signatures} == {name} and 'FUNCTION' in kinds:
            kind = 'FUNCTION'
        elif 'OPERATOR' in kinds and operator_label(text) and operator_label(text)[0] == name:
            # Bare words in a mixed catalog do not prove an operator type.
            if kinds == {'OPERATOR'} or re.fullmatch(r'[^\w\s]+', text):
                kind = 'OPERATOR'
        elif kinds == {'DATATYPE'} and identifier_key(name) in declaration_names(text):
            kind = 'DATATYPE'
        if kind:
            targets=[]
            for link in cell.get('links') or []:
                target=urljoin(block['source_url'],str(link.get('href') or ''))
                parsed=urlparse(target)
                if parsed.fragment and parsed.hostname==urlparse(block['source_url']).hostname:
                    targets.append(target)
            result.append({'feature_type': kind, 'surface': text,
                           'dom_locator': cell['dom_locator'],
                           'section_context': list(headings),
                           'table_id': context.get('table_id'),
                           'identity_column': 'ROW_HEADER' if row_identity else 'Aliases' if alias_column else 'Name',
                           'definition_targets':sorted(set(targets)),
                           'proof_kind': 'CATALOG_MEMBERSHIP'})
    return result
