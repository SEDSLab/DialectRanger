"""Release observability only. Never grants admission or changes KB records."""
import json
from collections import Counter
from pathlib import Path


def _read(path, default):
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else default


def _rows(path):
    if path.is_file():
        with path.open(encoding='utf8') as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def describe_quality(output, legacy, result, *, semantic_enabled):
    """Keep completeness unknown without independent official evaluation."""
    output = Path(output)
    stage = output/'unified_pipeline'
    final_path = output/'kb/final_feature_kb.json'
    final = _read(final_path, [])
    final_ids = [r['feature_id'] for r in final]
    review_path = stage/'canonical/review_features.jsonl'
    review_ids = {r.get('record', {}).get('feature_id') for r in _rows(review_path)} - {None}
    overlap = sorted(set(final_ids) & review_ids)
    duplicate = len(final_ids) - len(set(final_ids))
    decisions_path = stage/'canonical/publication_decisions.jsonl'
    decisions = {r['feature_id']: r for r in _rows(decisions_path)}
    unproven = [fid for fid in final_ids if not decisions.get(fid, {}).get('published')
                or decisions.get(fid, {}).get('reason_codes')]
    preflight = _read(output/'preflight/summary.json', None)
    publication = result.get('publication') or {}
    semantic = (result.get('semantic') or {}).get('summary') or {}
    discovery = (result.get('unified_contract') or {}).get('discovery') or {}
    health = legacy.get('definition_publication_health') or {}
    available = final_path.is_file() and decisions_path.is_file() and review_path.is_file()
    failed = duplicate or overlap or unproven or legacy.get('FRONTEND_PROJECTION_LOSS', 0) != 0 or result.get('lineage', {}).get('status') != 'PASS'
    publication_status = ('NOT_EVALUATED' if not available else 'FAIL' if failed else 'PASS' if final else 'PARTIAL')
    counts = Counter(str(r.get('semantic_status') or 'UNKNOWN') for r in final)
    fields = {name: sum(bool(r.get('semantics', {}).get(name)) for r in final)
              for name in ('description', 'syntax', 'examples')}
    dimensions = {
        'execution': {'status': 'EXECUTION_COMPLETED', 'scope': 'Stage execution, not quality certification'},
        'structure': {'status': preflight['status'] if preflight else 'NOT_EVALUATED',
            'sampled_pages': (preflight or {}).get('sampled_pages', 0),
            'page_status_counts': (preflight or {}).get('page_status_counts', {}),
            'report': 'preflight/report.md' if preflight else None,
            'definition_publication_health_issues': health.get('issues', []),
            'scope': 'Sampled structural execution only; does not prove official feature coverage'},
        'publication': {'status': publication_status, 'final_count': len(final) if available else publication.get('final_count', 0),
            'review_count': publication.get('review_count', 0), 'review_rows_are_not_release_features': True,
            'duplicate_final_ids': duplicate, 'final_review_overlap': overlap,
            'final_without_release_decision': unproven, 'lineage_status': result.get('lineage', {}).get('status', 'NOT_EVALUATED'),
            'frontend_projection_loss': legacy.get('FRONTEND_PROJECTION_LOSS'),
            'scope': 'Existing publication decisions, isolation, lineage and projection checks; not independent precision'},
        'coverage': {'status': 'NOT_EVALUATED', 'official_recall': 'NOT_EVALUATED', 'official_precision': 'NOT_EVALUATED',
            'by_type': {k: 'NOT_EVALUATED' for k in ('FUNCTION', 'OPERATOR', 'DATATYPE')},
            'discovery_truncated': bool(discovery.get('DISCOVERY_TRUNCATED')),
            'fetch_failures': discovery.get('fetch_failures', discovery.get('failed_pages', 'UNKNOWN')),
            'scope': 'Supplied URL processing and feature counts are not official recall; posterior evaluation is separate'},
        'content': {'status': 'PARTIAL' if semantic_enabled and semantic.get('provider_mode') else 'NOT_EVALUATED',
            'final_semantic_status_counts': dict(counts), 'final_records_with_field': fields,
            'provider_mode': semantic.get('provider_mode', 'NOT_EVALUATED'),
            'provider_call_failures': semantic.get('semantic_call_failed', 0),
            'provider_parse_failures': semantic.get('semantic_parse_failed', 0),
            'semantic_not_run': semantic.get('semantic_not_run', 'NOT_EVALUATED'),
            'official_availability_evaluated': False,
            'scope': 'Observed extracted fields only; empty fields may be legitimate; COMPLETE is not official completeness certification'},
    }
    hard_issue = (publication_status == 'FAIL' or (preflight or {}).get('status') == 'EXECUTION_ERROR'
                  or health.get('issues') or discovery.get('DISCOVERY_TRUNCATED')
                  or legacy.get('issues', {}).get('Scope Gap') or not legacy.get('provider_matches_requested', False)
                  or semantic.get('semantic_call_failed', 0) or semantic.get('semantic_parse_failed', 0)
                  or semantic.get('semantic_not_configured', 0) or semantic.get('semantic_not_run', 0))
    status = ('QUALITY_NOT_EVALUATED' if not semantic_enabled else 'QUALITY_REVIEW_REQUIRED' if hard_issue else 'QUALITY_PARTIAL')
    return {'quality_status': status, 'quality_dimensions': dimensions,
        'legacy_contract_quality_status': legacy.get('quality_status'),
        'release_contract_version': 'feature-kb-release/v1',
        'quality_scope': 'Execution, structure, publication, coverage and content are separate; no automatic official completeness certification'}


def failed_dimensions(preflight=None):
    return {name: {'status': 'EXECUTION_FAILED' if name == 'execution' else
                   (preflight or {}).get('status', 'NOT_EVALUATED') if name == 'structure' else 'NOT_EVALUATED'}
            for name in ('execution', 'structure', 'publication', 'coverage', 'content')}
