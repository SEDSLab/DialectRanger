"""Bounded, source-verified structural preflight. No Identity/Type/Semantic run.

SUPPORTED means the sampled structural contract executed, never official recall,
feature truth or completeness. The same production Owner/field readers are used.
"""
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def bounded_count(value):
    value = int(value)
    if not 1 <= value <= 64:
        raise ValueError('sample size must be between 1 and 64')
    return value


def _hash(value):
    return hashlib.sha256(str(value).encode('utf8')).hexdigest()


def _balanced(groups, limit, key):
    pending = {k: sorted(v, key=key) for k, v in groups.items()}
    result = []
    while pending and len(result) < limit:
        for bucket in sorted(pending):
            result.append(pending[bucket].pop(0))
            if not pending[bucket]:
                del pending[bucket]
            if len(result) == limit:
                break
    return result


def sample_urls(rows, limit):
    """Before fetch: diverse URL paths only, not guessed feature identities."""
    groups = defaultdict(list)
    for row in rows:
        parts = urlsplit(row['url']).path.strip('/').split('/')
        groups['/'.join(parts[:-1])].append(row)
    return _balanced(groups, bounded_count(limit), lambda r: (_hash(r['url']), r['url']))


def observed_layout(document):
    blocks = document.get('blocks') or []
    paths = [str(b.get('dom_path') or '') for b in blocks]
    kinds = Counter(b.get('block_kind') for b in blocks)
    families = []
    if kinds['HEADING'] == 1:
        families.append('A')
    if kinds['HEADING'] > 1:
        families.append('B')
    if any(b.get('source_node_kind') == 'DT' for b in blocks) or any(re.search(r'(?:^| > )li(?:[.#:]| > |$)', p) for p in paths):
        families.append('C')
    if any(b.get('block_kind') == 'TABLE_ROW' and (b.get('table_metadata') or {}).get('headers') for b in blocks):
        families.append('D')
    if any('::=' in str(b.get('text') or '') for b in blocks):
        families.append('E')
    context = ' '.join(str(b.get('text') or '') for b in blocks if b.get('block_kind') == 'HEADING')
    words = [name for name, pattern in [('FUNCTION', r'\bfunctions?\b'), ('OPERATOR', r'\boperators?\b'),
             ('DATATYPE', r'\b(?:data[ -]?types?|storage classes|type affinity)\b')]
             if re.search(pattern, context, re.I)]
    return {'possible_families': families, 'heading_context_words': words,
            'wrapped_heading': any(re.search(r' > header(?:[.#:]| > )', p) for p in paths),
            'native_term_body': any(b.get('source_node_kind') == 'DT' for b in blocks)}


def sample_documents(documents, limit):
    groups = defaultdict(list)
    for document in documents:
        groups[json.dumps(observed_layout(document), sort_keys=True)].append(document)
    return _balanced(groups, bounded_count(limit), lambda d: (_hash(d.get('url')), str(d.get('url'))))


def evaluate_documents(documents, profile, output, *, sample_pages=12, sample_units=8):
    """Inspect actual ranges and field slots, without changing supplied data."""
    from agents.owner.structure_execution import compile_structure_plan, execute_structures
    from agents.owner.source_relations import prepare_relation_documents
    from agents.owner.generic import generate_owner_hypotheses
    from agents.contracts.definition_unit import build_definition_units
    from agents.semantic_stage.source_structure import SourceStructureIndex
    from agents.owner.profile_execution import field_candidates
    from agents.owner.definition_patterns import verified_pattern
    from agents.profile_bootstrap.cold_start import validate_cold_start_profile, profile_digest

    validate_cold_start_profile(profile)
    plans = compile_structure_plan(profile)
    selected = sample_documents(documents, sample_pages)
    unit_limit = bounded_count(sample_units)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    reader = SourceStructureIndex()
    # Cross-page contracts need the supplied corpus even when the inspected
    # unit is one page. Sampling must not turn a present endpoint into a miss.
    from agents.owner.linked_syntax import patterns as linked_patterns
    related = {d['page_id']:d for d in prepare_relation_documents(documents,profile)} if linked_patterns(profile) else {}
    pages = []
    for original in selected:
        row = {'url': original.get('url'), 'page_id': original.get('page_id'),
               'source_hash': original.get('page_hash'), 'html_path': original.get('html_path'),
               'observed_layout': observed_layout(original), 'configured_families': [p['structure'] for p in plans],
               'units': [], 'reason_codes': [], 'structural_only': True}
        try:
            path = Path(original.get('html_path') or '')
            if not path.is_file():
                raise FileNotFoundError('FROZEN_HTML_MISSING')
            if hashlib.sha256(path.read_bytes()).hexdigest() != original.get('page_hash'):
                raise ValueError('FROZEN_CONTENT_HASH_MISMATCH')
            document = copy.deepcopy(related[original['page_id']]) if related else prepare_relation_documents([copy.deepcopy(original)], profile)[0]
            execution = execute_structures(document, profile)
            owners = generate_owner_hypotheses([document], profile)
            units = build_definition_units([document], owners, profile, source_structure=reader,
                                           reference_documents=documents)
            row['executor_counts'] = execution['counts']
            row['executor_diagnostics'] = execution['diagnostics']
            row['definition_units_total'] = len(units)
            groups = defaultdict(list)
            for unit in units:
                groups[(unit.get('structure_execution') or {}).get('structure', 'LEGACY')].append(unit)
            inspected = _balanced(groups, unit_limit, lambda u: u['definition_unit_id'])
            row['definition_units_inspected'] = len(inspected)
            blocks = {b['block_id']: b for b in document.get('blocks', [])}
            for unit in inspected:
                evidence = {}
                for bid in unit['member_block_ids']:
                    block = blocks.get(bid)
                    if block is None:
                        continue
                    evidence[bid] = dict(block, **reader.observe(document, block))
                    evidence[bid].update(source_url=document['url'], source_hash=document['page_hash'],
                                         source_order=block.get('order', 0), owner_local_binding=True,
                                         evidence_usage='STRUCTURAL_PREFLIGHT_ONLY')
                missing = [bid for bid in unit['member_block_ids'] if bid not in evidence
                           or evidence[bid].get('source_structure_status') != 'RESOLVED']
                record = {'definition_unit_id': unit['definition_unit_id'], 'identities': unit['identity_values'],
                          'structure': (unit.get('structure_execution') or {}).get('structure'),
                          'boundary': unit['boundary'], 'anchor_refs': unit['identity_anchor_block_ids'],
                          'member_count': len(unit['member_block_ids']), 'source_unresolved_refs': missing,
                          'reason_codes': list(unit.get('reason_codes') or []), 'identity_slots': [], 'fields': []}
                proof = unit.get('source_proof') or {}
                v2 = (unit.get('structure_execution') or {}).get('schema_version') == 'StructureExecution/v2'
                for name in unit['identity_values']:
                    refs = unit['identity_anchor_block_ids'].get(name) or [proof.get('heading_block_id')]
                    verified = False
                    for bid in refs:
                        if bid not in evidence:
                            continue
                        block = dict(evidence[bid])
                        block['provenance'] = dict(block.get('provenance') or {}, html_path=document['html_path'],
                                                   source_url=document['url'], page_hash=document['page_hash'])
                        if verified_pattern(proof, block, name):
                            verified = True
                    record['identity_slots'].append({'identity': name, 'anchor_refs': refs,
                        'status': 'VERIFIED_IDENTITY_SLOT' if verified else 'SOURCE_RANGE_ONLY'})
                if v2 and any(s['status'] != 'VERIFIED_IDENTITY_SLOT' for s in record['identity_slots']):
                    record['reason_codes'].append('IDENTITY_SLOT_RECHECK_UNRESOLVED')
                bound = dict(unit, member_evidence_refs=unit['member_block_ids'],
                             binding_status='INCOMPLETE' if missing else 'COMPLETE')
                for name in unit['identity_values']:
                    candidates, diagnostics = field_candidates(bound, evidence, name)
                    record['fields'].append({'identity': name, 'diagnostics': diagnostics,
                        'locations': [dict({k: c.get(k) for k in ('role', 'block_id', 'source_locator', 'label_locator',
                                      'cell_index', 'sharing')}, sample_text=c.get('value', '')[:240])
                                      for c in candidates]})
                record['status'] = ('EXECUTION_ERROR' if missing or unit['status'] == 'INVALID' else
                                    'PROFILE_ADJUSTMENT_REQUIRED' if record['reason_codes'] else 'SUPPORTED')
                row['units'].append(record)
            if any(u['status'] == 'EXECUTION_ERROR' for u in row['units']):
                row['status'] = 'EXECUTION_ERROR'
                row['reason_codes'].append('DEFINITION_SOURCE_VERIFICATION_FAILED')
            elif any(u['status'] == 'SUPPORTED' for u in row['units']):
                row['status'] = 'SUPPORTED'
                if any(u['status'] != 'SUPPORTED' for u in row['units']):
                    row['reason_codes'].append('SOME_IDENTITY_SLOTS_REQUIRE_REVIEW')
            elif units or set(row['observed_layout']['possible_families']) - set(row['configured_families']):
                row['status'] = 'PROFILE_ADJUSTMENT_REQUIRED'
                row['reason_codes'].append('CONFIGURED_STRUCTURE_OR_IDENTITY_SLOT_NOT_PROVEN')
            else:
                row['status'] = 'UNSUPPORTED_STRUCTURE'
                row['reason_codes'].append('NO_VERIFIED_DEFINITION_RANGE')
                row['note'] = 'May be supporting/concept content; this is not proof that the page contains missing features.'
        except Exception as error:
            row.update(status='EXECUTION_ERROR', error_type=type(error).__name__, error=str(error))
        pages.append(row)
    counts = Counter(p['status'] for p in pages)
    status = ('SUPPORTED' if pages and counts['SUPPORTED'] == len(pages) and not any(p['reason_codes'] for p in pages)
              else 'PARTIAL' if counts['SUPPORTED'] else 'EXECUTION_ERROR' if counts['EXECUTION_ERROR']
              else 'PROFILE_ADJUSTMENT_REQUIRED' if counts['PROFILE_ADJUSTMENT_REQUIRED'] else 'UNSUPPORTED_STRUCTURE')
    summary = {'schema_version': 'structural-preflight/v1', 'status': status,
        'profile_id': profile['profile_id'], 'profile_digest': profile_digest(profile),
        'input_pages': len(documents), 'sampled_pages': len(pages), 'sample_page_budget': sample_pages,
        'sample_unit_budget_per_page': sample_units, 'sampling': 'deterministic observed-layout/context strata, URL SHA256 order',
        'page_status_counts': dict(counts), 'configured_families': [p['structure'] for p in plans],
        'observed_executor_counts': dict(sum((Counter(p.get('executor_counts', {})) for p in pages), Counter())),
        'official_coverage': 'NOT_EVALUATED', 'semantic_extraction_executed': False,
        'actual_external_llm_api_calls': 0, 'scope': 'Sampled structure/source/field-location checks only; not feature truth or field completeness',
        'missing_fields_policy': 'NO_MATCH is observational; fields may be absent in official documentation',
        'unsampled_pages': len(documents) - len(pages)}
    save(output/'summary.json', summary)
    (output/'pages.jsonl').write_text(''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in pages), encoding='utf8')
    lines = ['# Structural preflight', '', 'Status: ' + status, '', summary['scope'], '',
             'No Semantic or external model provider was invoked. Empty field slots do not reject features.', '']
    for page in pages:
        lines += ['## ' + str(page['url']), '', page['status'] + ': ' + ', '.join(page['reason_codes']), '']
        for unit in page['units']:
            lines += ['- Identities: ' + ', '.join(unit['identities']) + ' — ' + unit['status'],
                      '- Boundary: `' + json.dumps(unit['boundary'], ensure_ascii=False) + '`']
            for field in unit['fields']:
                for location in field['locations'][:12]:
                    lines += ['  - ' + str(location['role']) + ': ' + json.dumps(location['sample_text'], ensure_ascii=False)]
        lines += ['']
    (output/'report.md').write_text('\n'.join(lines), encoding='utf8')
    return summary


def run_preflight(dbms, docs_manifest, profile_path, output, *, sample_pages=12, sample_units=8,
                  cache_only=False, cache_dir=None):
    """Bounded official URL fetch (or cache), then the same structural readers."""
    from agents.profile_bootstrap.cold_start import load_cold_start_profile
    from agents.pipeline.scoped_build import load_explicit_documents, explicit_documents
    profile = load_cold_start_profile(profile_path)
    if profile['dbms'].casefold() != str(dbms).casefold():
        raise ValueError('External Profile DBMS mismatch')
    rows = load_explicit_documents(docs_manifest, dbms)
    chosen = sample_urls(rows, sample_pages)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Preflight output must be new and empty')
    output.mkdir(parents=True, exist_ok=True)
    sample = output/'sample_urls.json'
    save(sample, {'dbms': dbms, 'documents': chosen})
    documents, _, discovery = explicit_documents(dbms, output, {'docs_manifest': str(sample),
        'cache_only': cache_only, 'cache_dir': cache_dir, 'max_pages': None})
    result = evaluate_documents(documents, profile, output/'preflight', sample_pages=sample_pages, sample_units=sample_units)
    result.update(input_manifest=str(Path(docs_manifest).resolve()), supplied_urls=len(rows),
        url_sampled_before_fetch=len(chosen), fetch_summary=discovery)
    if len(documents) < len(chosen):
        result.update(status='PARTIAL' if documents else 'EXECUTION_ERROR', fetch_incomplete=True)
    save(output/'preflight/summary.json', result)
    save(output/'preflight_summary.json', result)
    return result
