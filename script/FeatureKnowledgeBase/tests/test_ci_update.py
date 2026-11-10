import json
import sys
import pytest
import ci_update


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')


@pytest.fixture
def artifacts(tmp_path):
    candidate, baseline, run = (tmp_path / n for n in ('candidate', 'baseline', 'run'))
    manifest = tmp_path / 'urls.json'
    row = {'feature_id': 'sample:function:alpha', 'dbms': 'sample'}
    dump(candidate / 'final.json', [row])
    dump(candidate / 'review.json', [])
    dump(candidate / 'summary.json', {'dbms': 'sample', 'source_run': str(run), 'final_count': 1,
         'review_count': 0, 'actual_model_calls': 0, 'projection_loss': 0})
    dump(manifest, {'documents': [{'url': 'https://official.test/functions#alpha'}]})
    dump(run / 'scoped_discovery/discovery/discovery_summary.json',
         {'fetch_failures': 0, 'budget_exhausted': False, 'network_fetch': True})
    (run / 'scoped_discovery/discovery/discovered_urls.jsonl').write_text(json.dumps(
        {'url': 'https://official.test/functions', 'fetch_status': 'FETCHED'}) + '\n', encoding='utf-8')
    dump(run / 'unified_pipeline/execution_manifest.json',
         {'lineage': {'status': 'PASS'}, 'semantic': {'actual_model_calls': 0,
          'provider_mode': 'LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER'}})
    return candidate, baseline, manifest, run


def validate(artifacts):
    return ci_update.validate_candidate('sample', *artifacts[:3])


def test_complete_candidate_is_publishable(artifacts):
    assert validate(artifacts)['status'] == 'PASS'


def test_nested_review_schema_is_checked_without_treating_review_as_final(artifacts):
    candidate = artifacts[0]
    dump(candidate / 'review.json', [{'record': {'dbms': 'sample'}, 'admission': {'decision': 'REVIEW_REQUIRED'}}])
    summary = json.loads((candidate / 'summary.json').read_text())
    summary['review_count'] = 1; dump(candidate / 'summary.json', summary)
    assert validate(artifacts)['status'] == 'PASS'
    dump(candidate / 'review.json', [{'record': {'dbms': 'other'}}])
    assert 'WRONG_DBMS' in validate(artifacts)['errors']


def test_missing_input_page_blocks_update_without_modifying_baseline(artifacts):
    candidate, baseline, manifest, run = artifacts
    dump(baseline / 'final.json', [{'feature_id': 'old'}])
    before = (baseline / 'final.json').read_bytes()
    dump(manifest, {'documents': [{'url': 'https://official.test/missing'}]})
    assert 'INCOMPLETE_FETCH' in validate(artifacts)['errors']
    assert (baseline / 'final.json').read_bytes() == before


@pytest.mark.parametrize('patch,error', [
    ({'network_fetch': False}, 'LIVE_FETCH_NOT_VERIFIED'),
    ({'budget_exhausted': True}, 'CRAWL_TRUNCATED_OR_UNKNOWN'),
    ({'fetch_failures': 1}, 'INCOMPLETE_FETCH'),
])
def test_incomplete_or_cached_run_cannot_publish_as_online(artifacts, patch, error):
    path = artifacts[3] / 'scoped_discovery/discovery/discovery_summary.json'
    value = json.loads(path.read_text()); value.update(patch); dump(path, value)
    assert error in validate(artifacts)['errors']


@pytest.mark.parametrize('calls', [None, 1])
def test_unknown_or_nonzero_model_calls_fail_closed(artifacts, calls):
    path = artifacts[3] / 'unified_pipeline/execution_manifest.json'
    value = json.loads(path.read_text()); value['semantic']['actual_model_calls'] = calls
    dump(path, value)
    assert 'EXTERNAL_MODEL_CALL_COUNT_NOT_ZERO_OR_UNKNOWN' in validate(artifacts)['errors']


def test_baseline_count_drop_blocks_update(artifacts):
    dump(artifacts[1] / 'final.json', [{'feature_id': 'old1'}, {'feature_id': 'old2'}])
    assert 'FINAL_COUNT_DROP_EXCEEDS_TEN_PERCENT' in validate(artifacts)['errors']


def test_worker_selects_local_semantic_and_disables_hosted_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', [])
    from agents.semantic_extractor import llm_client
    # Undo the worker lock when the test ends, without letting it leak to other tests.
    monkeypatch.setattr(llm_client.LLMClientFactory, 'create', llm_client.LLMClientFactory.create)
    monkeypatch.setattr(llm_client.OpenAICompatibleClient, '__init__', llm_client.OpenAICompatibleClient.__init__)

    def fake_cli(path, run_name):
        from agents.semantic_stage.pipeline import _make_llm_client
        client, state, mode = _make_llm_client(run_llm=True, provider='deterministic',
            model=None, timeout=1, max_retries=0)
        assert client is not None and state['ready']
        assert mode == 'LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER'
        with pytest.raises(RuntimeError, match='disabled'):
            llm_client.LLMClientFactory.create({'provider': 'openai_compatible'})
        with pytest.raises(RuntimeError, match='disabled'):
            llm_client.OpenAICompatibleClient({})
        assert sys.argv[sys.argv.index('--semantic-provider') + 1] == 'deterministic'
        assert '--semantic-budget' not in sys.argv
        assert '--cache-only' not in sys.argv

    monkeypatch.setattr(ci_update.runpy, 'run_path', fake_cli)
    ci_update.run_worker('sample', tmp_path / 'candidate')
