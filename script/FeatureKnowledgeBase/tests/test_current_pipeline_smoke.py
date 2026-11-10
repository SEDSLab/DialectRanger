"""Run the real unified CLI over a tiny frozen corpus, with no external calls."""
import hashlib
import json
import sys
from pathlib import Path
import pytest

import crawler_cli as cli


@pytest.mark.parametrize('external_profile', [False, True])
def test_unified_cli_cached_corpus_to_frontend(tmp_path, monkeypatch, external_profile):
    cache = tmp_path / 'cache'
    cache.mkdir()
    pages = {
        'index.html': '<h1>Fixture Documentation</h1><a href="functions.html">Functions</a><a href="types.html">Data types</a><a href="operators.html">Operators</a>',
        'functions.html': '<h1>Functions</h1><h2>DEMO</h2><h3>Syntax</h3><pre>DEMO(x)</pre><h3>Description</h3><p>Returns the input value.</p><h3>Example</h3><pre>SELECT DEMO(1);</pre>',
        'types.html': '<h1>Data types</h1><table><tr><th>Data type</th><th>Description</th></tr><tr><td>INTEGER</td><td>Signed integer.</td></tr></table>',
        'operators.html': '<h1>Operators</h1><table><tr><th>Operator</th><th>Description</th></tr><tr><td>+</td><td>Adds values.</td></tr></table>',
    }
    records = []
    for name, body in pages.items():
        path = cache / name
        path.write_text('<html><head><title>Fixture reference</title></head><body><main>' + body + '</main></body></html>', encoding='utf-8')
        records.append({'url': 'https://fixture.test/docs/' + name, 'local_artifact_path': name,
                        'content_type': 'text/html', 'content_hash': hashlib.sha256(path.read_bytes()).hexdigest()})
    (cache / 'page_manifest.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    output = tmp_path / 'run'
    args = ['run_feature_crawler.py', 'build', '--dbms', 'fixture',
        '--docs-url', 'https://fixture.test/docs/index.html', '--cache-only', '--cache-dir', str(cache),
        '--output-root', str(output), '--semantic-provider', 'deterministic']
    if external_profile:
        from agents.profile_bootstrap.cold_start import build_cold_start_profile, profile_digest
        profile=build_cold_start_profile(dbms='fixture',documents=[],run_id='fixed-smoke-profile')
        profile_path=tmp_path/'profile.json'
        profile_path.write_text(json.dumps(profile),encoding='utf-8')
        args.extend(['--profile',str(profile_path)])
    monkeypatch.setattr(cli,'ROOT',str(tmp_path))
    monkeypatch.setattr(sys, 'argv', args)
    assert cli.main() == 0
    summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    runtime=Path(summary['source_run'])
    manifest = json.loads((runtime / 'run_manifest.json').read_text(encoding='utf-8'))
    assert manifest['execution_status'] == 'EXECUTION_COMPLETED'
    assert manifest['actual_model_calls'] == 0
    assert {x.name for x in output.iterdir()}=={'final.json','review.json','summary.json'}
    assert (tmp_path/'frontend/data/catalog.json').is_file()
    assert manifest['FRONTEND_PROJECTION_LOSS'] == 0
    if external_profile:
        metadata=json.loads((runtime/'unified_pipeline/presemantic/profile/profile_metadata.json').read_text(encoding='utf-8'))
        assert metadata['load_mode']=='PROVIDED_PROFILE'
        assert metadata['profile_digest']==profile_digest(profile)
