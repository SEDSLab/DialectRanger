"""Finite, source-local Profile observations; context is never a type vote.

No URLs, DBMS vocabulary, feature inventories or executable selectors are used.
Profiles select structural recipes. Runtime blocks must independently exhibit
the recorded heading/table context before the recipe can be attached to Owner.
"""
import re
from collections import defaultdict

TYPES = ('FUNCTION', 'OPERATOR', 'DATATYPE')
LABELS = {
    'FUNCTION': re.compile(r'\b(?:functions?|routines?)\b', re.I),
    'OPERATOR': re.compile(r'\b(?:operators?|predicates?)\b', re.I),
    'DATATYPE': re.compile(r'\b(?:data[ -]?types?|type (?:syntax|declarations?|definitions?)|storage classes?)\b', re.I),
}
NEGATIVE = re.compile(r'\b(?:examples?|tutorials?|mapping|connector|API|SDK|JDBC|ODBC|navigation)\b', re.I)
RECIPES = {
    'list_item': ('LEADING_LITERAL', 'CURRENT_CONTAINER'),
    'definition_list': ('DEFINITION_TERM', 'TERM_BODY_PAIR'),
    'heading_section': ('HEADING', 'HEADING_BOUNDED_SECTION'),
    'signature': ('SIGNATURE_TEXT_OR_HEADING', 'CURRENT_BLOCK'),
    'table_row': ('VERIFIED_IDENTITY_CELL', 'CURRENT_ROW'),
}
GRAMMARS = {'FUNCTION': 'CALLABLE_DEFINITION', 'OPERATOR': 'OPERATOR_EXPRESSION', 'DATATYPE': 'DATATYPE_DECLARATION'}


def local_context(block):
    """Nearest explicit taxonomy, with negative child sections as barriers."""
    headings = list(block.get('heading_ancestry') or [])
    for heading in reversed(headings):
        if NEGATIVE.search(str(heading)):
            return {'types': [], 'basis': 'NEGATIVE_SECTION', 'label': str(heading)}
        kinds = [kind for kind, regex in LABELS.items() if regex.search(str(heading))]
        if kinds:
            return {'types': kinds, 'basis': 'LOCAL_HEADING', 'label': str(heading)}
    headers = (block.get('table_metadata') or {}).get('headers') or []
    kinds = sorted({kind for header in headers for kind, regex in LABELS.items()
                    if regex.fullmatch(str(header).strip())})
    return {'types': kinds, 'basis': 'TABLE_HEADER' if kinds else 'UNKNOWN', 'label': ' | '.join(map(str, headers))}


def strategies(block):
    kind = block.get('block_kind') or block.get('block_type')
    output = set()
    mapped = {'TABLE_ROW': 'table_row', 'DEFINITION': 'definition_list', 'LIST_ITEM': 'list_item',
              'HEADING': 'heading_section', 'CODE': 'signature', 'SIGNATURE': 'signature'}.get(kind)
    if mapped:
        output.add(mapped)
    if re.search(r'(?:^| > )li(?:[.#:]| > |$)', str(block.get('dom_path') or '')):
        output.add('list_item')
    return output


def page_strata(page):
    """Multi-label content x structure, used for sampling, never admission."""
    result = set()
    for block in page.get('blocks') or []:
        context = local_context(block)
        for kind in context['types'] or ['UNKNOWN']:
            result.update((kind, strategy) for strategy in strategies(block))
        # A type-themed heading is not equivalent to observing its actual
        # declaration layout. Preserve both strata so a mapping table cannot
        # crowd out the native declaration page during greedy sampling.
        from agents.owner.definition_patterns import parse_surface
        for kind, grammar in GRAMMARS.items():
            if parse_surface(block, grammar):
                shapes = strategies(block) or {'paragraph'}
                result.update((kind, strategy + ':DECLARATION') for strategy in shapes)
    return result


def observe_regions(documents):
    regions = {}
    for page in documents:
        for block in page.get('blocks') or []:
            context = local_context(block)
            if not context['types']:
                continue
            for strategy in strategies(block):
                key = (page.get('page_id'), block.get('section_id'), tuple(context['types']), strategy, context['label'])
                row = regions.setdefault(key, {'page_id': page.get('page_id'), 'source_url': page.get('url'),
                    'page_hash': page.get('page_hash'), 'section_id': block.get('section_id'),
                    'owner_strategy': strategy, 'context_types': context['types'], 'context_basis': context['basis'],
                    'context_label': context['label'], 'block_ids': []})
                row['block_ids'].append(block['block_id'])
    return list(regions.values())


def build_local_patterns(regions):
    observed = defaultdict(list)
    for row in regions:
        for kind in row['context_types']:
            observed[(kind, row['owner_strategy'])].append(row)
    return [{'context_type': kind, 'owner_strategy': strategy, 'grammar': GRAMMARS[kind],
             'identity_slot': RECIPES[strategy][0], 'boundary': RECIPES[strategy][1],
             'observed_region_refs': [{'page_id': r['page_id'], 'section_id': r['section_id'],
                  'block_ids': r['block_ids'], 'page_hash': r['page_hash']} for r in refs]}
            for (kind, strategy), refs in sorted(observed.items()) if strategy in RECIPES]


def validate_local_patterns(patterns):
    if not isinstance(patterns, list):
        raise ValueError('local_type_patterns must be a list')
    seen = set()
    for pattern in patterns:
        if not isinstance(pattern, dict) or set(pattern) != {
            'context_type', 'owner_strategy', 'grammar', 'identity_slot', 'boundary', 'observed_region_refs'}:
            raise ValueError('local type pattern must use the finite structural contract')
        kind, strategy = pattern['context_type'], pattern['owner_strategy']
        if (kind not in TYPES or strategy not in RECIPES or pattern['grammar'] != GRAMMARS[kind]
            or (pattern['identity_slot'], pattern['boundary']) != RECIPES[strategy]):
            raise ValueError('unsupported local type recipe')
        if (kind, strategy) in seen or not isinstance(pattern['observed_region_refs'], list):
            raise ValueError('duplicate or invalid local type recipe')
        seen.add((kind, strategy))
    return patterns


def owner_local_hint(page, members, profile, fallback):
    """Attach only independently re-observed context; never promote a vote."""
    patterns = (profile or {}).get('hint_contract', {}).get('local_type_patterns')
    if patterns is None:
        return dict(fallback or {})
    validate_local_patterns(patterns)
    matches = []
    # Definition heads lead the Owner. Do not union unrelated body sections.
    if members:
        block = members[0]
        context = local_context(block)
        for pattern in patterns:
            if pattern['context_type'] in context['types'] and pattern['owner_strategy'] in strategies(block):
                matches.append({'context_type': pattern['context_type'], 'grammar': pattern['grammar'],
                    'owner_strategy': pattern['owner_strategy'], 'identity_slot': pattern['identity_slot'],
                    'boundary': pattern['boundary'], 'context_basis': context['basis'], 'context_label': context['label'],
                    'block_id': block['block_id'], 'page_id': page.get('page_id'), 'page_hash': page.get('page_hash')})
    result = dict(fallback or {})
    if matches:
        result['local_type_patterns'] = matches
        kinds = sorted({m['context_type'] for m in matches})
        # The legacy type_hint also participates in Identity grouping. A new
        # local context must not silently split existing IdentityUnits before
        # the actual Type stage has inspected a defining subject occurrence.
        result['local_context_types'] = kinds
        result['type_hint_authority'] = 'CONTEXT_ONLY_NOT_TYPE_DECISION'
    return result
