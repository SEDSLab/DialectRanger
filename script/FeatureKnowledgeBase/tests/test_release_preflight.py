import copy
import json
from pathlib import Path
import pytest

import crawler_cli as cli
from agents.pipeline.preflight import evaluate_documents, sample_documents, sample_urls
from agents.pipeline.release_quality import describe_quality
from tests.test_profile_definition_execution import document
from tests.test_subject_contract_repair import section_profile


def fixture(tmp_path, body=None):
    d = document(tmp_path, 'Functions', body or '<h2>TURN()</h2><p>Returns a rotation.</p>')
    return d, section_profile(d)


def test_preflight_runs_real_owner_and_source_readers_without_provider(tmp_path, monkeypatch):
    from agents.semantic_extractor.llm_client import LLMClientFactory
    monkeypatch.setenv('FEATURE_CRAWLER_LLM_API_KEY', 'fixture-not-a-real-key')
    monkeypatch.setattr(LLMClientFactory, 'create', lambda **kw: pytest.fail('No external provider in preflight'))
    d, p = fixture(tmp_path, '<section><header><h2>1 . 2 . TURN()</h2></header>'
        '<p>Returns a rotation.</p><pre>TURN(x)</pre></section>')
    old = copy.deepcopy([d, p])
    summary = evaluate_documents([d], p, tmp_path/'check')
    assert summary['status'] == 'SUPPORTED'
    assert summary['official_coverage'] == 'NOT_EVALUATED'
    assert summary['actual_external_llm_api_calls'] == 0 and not summary['semantic_extraction_executed']
    rows = [json.loads(l) for l in (tmp_path/'check/pages.jsonl').read_text(encoding='utf8').splitlines()]
    assert rows[0]['units'][0]['identity_slots'][0]['status'] == 'VERIFIED_IDENTITY_SLOT'
    assert rows[0]['units'][0]['boundary']['container']
    assert rows[0]['units'][0]['fields'][0]['locations']
    assert [d, p] == old


def test_missing_syntax_and_example_are_not_preflight_failure(tmp_path):
    d, p = fixture(tmp_path)
    summary = evaluate_documents([d], p, tmp_path/'check')
    assert summary['status'] == 'SUPPORTED'
    row = json.loads((tmp_path/'check/pages.jsonl').read_text(encoding='utf8'))
    diagnostics = row['units'][0]['fields'][0]['diagnostics']
    assert any(x['status'] == 'NO_MATCH' for x in diagnostics)


@pytest.mark.parametrize('damage', ['missing', 'hash'])
def test_preflight_source_failure_is_execution_error(tmp_path, damage):
    d, p = fixture(tmp_path)
    if damage == 'missing':
        Path(d['html_path']).unlink()
    else:
        Path(d['html_path']).write_text('changed', encoding='utf8')
    s = evaluate_documents([d], p, tmp_path/'check')
    assert s['status'] == 'EXECUTION_ERROR'
    assert s['page_status_counts']['EXECUTION_ERROR'] == 1


def test_mismatched_structural_recipe_is_not_unseen_repair(tmp_path):
    d, p = fixture(tmp_path, '<dl><dt>TURN(x)</dt><dd>Returns a rotation.</dd></dl>')
    s = evaluate_documents([d], p, tmp_path/'check')
    assert s['status'] == 'PROFILE_ADJUSTMENT_REQUIRED'
    assert s['observed_executor_counts'] == {}


def test_unproven_concept_is_not_counted_as_missing_feature(tmp_path):
    d, p = fixture(tmp_path, '<h2>General Concepts</h2><p>Read this introduction first.</p>')
    s = evaluate_documents([d], p, tmp_path/'check')
    assert s['status'] == 'UNSUPPORTED_STRUCTURE'
    row = json.loads((tmp_path/'check/pages.jsonl').read_text(encoding='utf8'))
    assert 'not proof' in row['note']


def test_executor_exception_is_visible(tmp_path, monkeypatch):
    import agents.owner.generic as owners
    d, p = fixture(tmp_path)
    def broken(*a, **kw):
        raise RuntimeError('owner fixture failure')
    monkeypatch.setattr(owners, 'generate_owner_hypotheses', broken)
    s = evaluate_documents([d], p, tmp_path/'check')
    assert s['status'] == 'EXECUTION_ERROR'
    row = json.loads((tmp_path/'check/pages.jsonl').read_text(encoding='utf8'))
    assert row['error_type'] == 'RuntimeError' and row['error'] == 'owner fixture failure'


def test_sampling_is_deterministic_and_bounded():
    rows = [{'url': 'https://sample.test/'+folder+'/'+str(i)} for folder in ['one', 'two'] for i in range(5)]
    assert sample_urls(rows, 3) == sample_urls(list(reversed(rows)), 3)
    assert len(sample_urls(rows, 3)) == 3
    documents = [dict(url=r['url'], blocks=[{'block_kind':'HEADING','text':'Functions'}]) for r in rows]
    documents[-1]['blocks'].append({'source_node_kind':'DT'})
    assert sample_documents(documents, 2) == sample_documents(list(reversed(documents)), 2)
    assert any(any(b.get('source_node_kind') == 'DT' for b in d['blocks']) for d in sample_documents(documents, 2))


@pytest.mark.parametrize('budget', ['0', '65', '-1'])
def test_cli_rejects_unbounded_preflight(budget):
    with pytest.raises(SystemExit):
        cli._product_parser().parse_args(['preflight', '--dbms','sample','--profile','p.json',
                                        '--docs-manifest','urls.json','--sample-pages',budget])


def quality_fixture(tmp_path, overlap=False):
    stage = tmp_path/'unified_pipeline/canonical'
    stage.mkdir(parents=True)
    final = [{'feature_id':'one','feature_type':'function','semantic_status':'STRUCTURAL_ONLY','semantics':{}}]
    (tmp_path/'kb').mkdir()
    (tmp_path/'kb/final_feature_kb.json').write_text(json.dumps(final), encoding='utf8')
    (stage/'publication_decisions.jsonl').write_text(json.dumps({'feature_id':'one','published':True,'reason_codes':[]})+'\n')
    (stage/'review_features.jsonl').write_text(json.dumps({'record':{'feature_id':'one' if overlap else 'two'}})+'\n')
    legacy = {'quality_status':'QUALITY_ACCEPTED','RELEASE_CONTRACT_VALID':True,
              'FRONTEND_PROJECTION_LOSS':0,'provider_matches_requested':True}
    result = {'publication':{'final_count':1,'review_count':1},'lineage':{'status':'PASS'},
              'semantic':{'summary':{'provider_mode':'LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER'}}}
    return legacy, result


def test_execution_and_sparse_content_do_not_claim_official_coverage(tmp_path):
    legacy, result = quality_fixture(tmp_path)
    q = describe_quality(tmp_path, legacy, result, semantic_enabled=True)
    assert q['quality_status'] == 'QUALITY_PARTIAL'
    d = q['quality_dimensions']
    assert d['execution']['status'] == 'EXECUTION_COMPLETED'
    assert d['publication']['status'] == 'PASS'
    assert d['coverage']['status'] == 'NOT_EVALUATED'
    assert d['content']['status'] == 'PARTIAL'
    assert d['content']['final_records_with_field'] == {'description':0,'syntax':0,'examples':0}
    assert q['legacy_contract_quality_status'] == 'QUALITY_ACCEPTED'


def test_review_isolation_is_independent_of_nullable_fields(tmp_path):
    legacy, result = quality_fixture(tmp_path, overlap=True)
    q = describe_quality(tmp_path, legacy, result, semantic_enabled=True)
    assert q['quality_status'] == 'QUALITY_REVIEW_REQUIRED'
    assert q['quality_dimensions']['publication']['status'] == 'FAIL'
    assert q['quality_dimensions']['publication']['final_review_overlap'] == ['one']


def test_preflight_cli_dispatch_never_initializes_semantic(tmp_path, monkeypatch):
    from agents.pipeline import preflight
    from agents.semantic_extractor.llm_client import LLMClientFactory
    monkeypatch.setattr(LLMClientFactory,'create',lambda **kw:pytest.fail('No provider'))
    monkeypatch.setattr(preflight,'run_preflight',lambda *a,**kw:{'status':'PARTIAL','sampled_pages':1})
    monkeypatch.setattr('sys.argv',['run_feature_crawler.py','preflight','--dbms','sample',
        '--docs-manifest','urls.json','--profile','profile.json','--output-root',str(tmp_path/'run')])
    assert cli.main() == 2


def test_strict_preflight_stops_before_downstream(tmp_path, monkeypatch):
    from agents.pipeline import orchestrator, scoped_build
    d, p = fixture(tmp_path, '<h2>Concepts</h2><p>Introduction only.</p>')
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(p), encoding='utf8')
    monkeypatch.setattr(scoped_build,'scoped_documents',lambda *a,**kw:([d],[],{}))
    monkeypatch.setattr(orchestrator,'_materialize_generic_contract',lambda **kw:pytest.fail('Must stop before downstream'))
    with pytest.raises(RuntimeError,match='PREFLIGHT_REVIEW_REQUIRED'):
        orchestrator.run_build('sample','https://official.test/reference',tmp_path/'run',
            profile_path=str(path),semantic=False,require_preflight=True)
    assert not (tmp_path/'run/kb').exists()
    failure = json.loads((tmp_path/'run/quality_report.json').read_text())
    assert failure['quality_dimensions']['execution']['status'] == 'EXECUTION_FAILED'
