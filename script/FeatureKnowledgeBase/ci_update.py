"""Build an isolated candidate, validate it, then export a data-only update.

No extraction rules live here. CI publication checks do not change Admission.
Hosted model clients are disabled in the child, even when a local .env exists.
"""
import argparse
import json
import re
import runpy
import subprocess
import sys
import shutil
from pathlib import Path
from urllib.parse import urldefrag
from uuid import uuid4

from integration import TOOL_ROOT, DATA_ROOT, read, write, publish_dataset


def validate_candidate(dbms, candidate, baseline, manifest, require_online=True):
    candidate, baseline, manifest = map(Path, (candidate, baseline, manifest))
    final = read(candidate / 'final.json')
    review = read(candidate / 'review.json')
    summary = read(candidate / 'summary.json')
    errors = []
    run = Path(summary['source_run'])
    discovery = read(run / 'scoped_discovery/discovery/discovery_summary.json')
    execution = read(run / 'unified_pipeline/execution_manifest.json')
    semantic = execution.get('semantic', {})
    expected = {urldefrag(item['url'])[0] for item in read(manifest)['documents']}
    observed = set()
    with (run / 'scoped_discovery/discovery/discovered_urls.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('fetch_status') == 'FETCHED':
                observed.add(urldefrag(row['url'])[0])
    missing = sorted(expected - observed)
    if missing or discovery.get('fetch_failures') != 0:
        errors.append('INCOMPLETE_FETCH')
    if discovery.get('budget_exhausted') is not False:
        errors.append('CRAWL_TRUNCATED_OR_UNKNOWN')
    if require_online and discovery.get('network_fetch') is not True:
        errors.append('LIVE_FETCH_NOT_VERIFIED')
    if summary.get('actual_model_calls') != 0 or semantic.get('actual_model_calls') != 0:
        errors.append('EXTERNAL_MODEL_CALL_COUNT_NOT_ZERO_OR_UNKNOWN')
    if semantic.get('provider_mode') != 'LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER':
        errors.append('UNEXPECTED_PROVIDER')
    ids = [row.get('feature_id') for row in final]
    if not ids or not all(ids) or len(ids) != len(set(ids)):
        errors.append('EMPTY_OR_DUPLICATE_FINAL')
    # Review uses {record, admission, publication, validation}; Final is flat.
    records = final + [row.get('record', row) for row in review]
    if any(row.get('dbms') != dbms for row in records):
        errors.append('WRONG_DBMS')
    if summary.get('dbms') != dbms or summary.get('final_count') != len(final) or summary.get('review_count') != len(review):
        errors.append('SUMMARY_COUNT_MISMATCH')
    if summary.get('projection_loss') != 0 or execution.get('lineage', {}).get('status') != 'PASS':
        errors.append('PROJECTION_OR_LINEAGE_FAILURE')
    previous = read(baseline / 'final.json') if (baseline / 'final.json').exists() else []
    # A large change needs inspection instead of automatically replacing good data.
    # This is an update guard, not a rule for rejecting individual Features.
    if previous and len(final) < len(previous) * 0.9:
        errors.append('FINAL_COUNT_DROP_EXCEEDS_TEN_PERCENT')
    return {'status': 'PASS' if not errors else 'BLOCKED', 'errors': errors,
            'dbms': dbms, 'previous_final': len(previous), 'candidate_final': len(final),
            'candidate_review': len(review), 'missing_input_urls': missing,
            'fetch_failures': discovery.get('fetch_failures'),
            'network_fetch': discovery.get('network_fetch'),
            'semantic_provider': 'deterministic',
            'actual_external_llm_api_calls': semantic.get('actual_model_calls'),
            'source_run': str(run)}


def run_worker(dbms, candidate):
    # Install the lock BEFORE importing/running the CLI or loading project .env.
    from agents.semantic_extractor import llm_client

    def forbidden(*args, **kwargs):
        raise RuntimeError('Hosted semantic providers are disabled in automatic updates')

    llm_client.LLMClientFactory.create = staticmethod(forbidden)
    llm_client.OpenAICompatibleClient.__init__ = forbidden
    sys.argv = [str(TOOL_ROOT / 'crawler_cli.py'), 'build', '--dbms', dbms,
                '--docs-manifest', str(TOOL_ROOT / 'inputs' / dbms / 'urls.json'),
                '--profile', str(TOOL_ROOT / 'inputs' / dbms / 'profile.json'),
                '--output-root', str(candidate), '--semantic',
                '--semantic-provider', 'deterministic']
    runpy.run_path(str(TOOL_ROOT / 'crawler_cli.py'), run_name='__main__')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dbms', default='sqlite')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--candidate-only', action='store_true', help='Validate and stage artifacts without publishing')
    parser.add_argument('--artifact-dir', type=Path)
    args = parser.parse_args()
    dbms = args.dbms.lower()
    if not re.fullmatch(r'[a-z][a-z0-9_-]*', dbms):
        parser.error('Invalid DBMS identifier')
    if args.worker:
        run_worker(dbms, args.worker.resolve())
        return 0
    candidate = TOOL_ROOT / '.runtime/ci' / (dbms + '_' + uuid4().hex) / 'candidate'
    command = [sys.executable, str(Path(__file__).resolve()), '--dbms', dbms,
               '--worker', str(candidate)]
    subprocess.run(command, cwd=str(TOOL_ROOT), check=True)
    audit = validate_candidate(dbms, candidate, DATA_ROOT / dbms,
                               TOOL_ROOT / 'inputs' / dbms / 'urls.json')
    write(TOOL_ROOT / '.runtime/ci/update_validation.json', audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if audit['status'] != 'PASS':
        return 1
    if args.candidate_only:
        if args.artifact_dir is None:
            parser.error('--candidate-only requires --artifact-dir')
        args.artifact_dir.mkdir(parents=True, exist_ok=True)
        for name in ('final.json','review.json','summary.json'):
            shutil.copyfile(str(candidate/name),str(args.artifact_dir/name))
        write(args.artifact_dir/'validation.json',audit)
        return 0
    projection = publish_dataset(dbms, candidate)
    write(TOOL_ROOT / '.runtime/ci/projection.json', projection)
    return 0


if __name__ == '__main__':
    sys.exit(main())
