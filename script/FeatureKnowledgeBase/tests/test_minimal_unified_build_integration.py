import json
from pathlib import Path
import pytest
import crawler_cli as cli
from agents.pipeline import orchestrator
from agents.pipeline.publication import select_publication
from agents.semantic_stage.pipeline import _make_llm_client


def test_two_argument_defaults_and_unique_output(monkeypatch):
    from agents.pipeline import delivery
    monkeypatch.setattr(delivery,"publish",lambda *a,**kw: {})
    seen=[]
    monkeypatch.setattr(orchestrator,'run_build',lambda db,url,out,**kw: seen.append((out,kw)) or {'status':'accepted'})
    parser=cli._product_parser()
    for _ in range(2):
        args=parser.parse_args(['build','--dbms','fixture','--docs-url','https://fixture.invalid/docs/'])
        assert args.pipeline=='unified' and args.semantic and args.semantic_provider=='deterministic'
        assert cli._run_product_command(args)
    assert seen[0][0]!=seen[1][0]


def test_local_provider_no_api_and_explicit_disable(monkeypatch):
    monkeypatch.delenv('FEATURE_CRAWLER_LLM_API_KEY',raising=False)
    c,s,m=_make_llm_client(run_llm=True,provider='deterministic',model=None,timeout=1,max_retries=0)
    assert s['ready'] and m=='LOCAL_DETERMINISTIC_EXTRACTIVE_ADAPTER'
    assert c.call('INPUT: '+json.dumps({'identity_group_id':'x','evidence':[]}),'x')['_status']=='ok'
    args=cli._product_parser().parse_args(['build','--dbms','fixture','--docs-url','https://fixture.invalid','--no-semantic'])
    assert args.semantic is False


def test_review_and_grounding_are_feature_local():
    records=[{'feature_id':str(i),'canonical_identity':{'key':str(i)},'provenance':{'identity_unit_id':str(i)}} for i in range(3)]
    admissions=[dict(identity_unit_id=str(i),decision='ADMITTED' if i!=1 else 'NEEDS_REVIEW',identity_credible=True,official_source=True,owner_evidence_correct=True,lineage_complete=True) for i in range(3)]
    validations=[{'identity_group_id':str(i),'semantic_status':'COMPLETE','ungrounded_claims':1 if i==2 else 0} for i in range(3)]
    final,review,decisions=select_publication(records,admissions,validations,{'status':'PASS'})
    assert [r['feature_id'] for r in final]==['0'] and len(review)==2
    assert len(records)==3 and all(r['admission'] and r['validation'] for r in review)


def test_scoped_crawl_bridge_and_truncation(tmp_path,monkeypatch):
    from agents.pipeline import scoped_build
    from agents.pipeline import delivery
    monkeypatch.setattr(delivery,"publish",lambda *a,**kw: {})
    calls=[]
    manifest={'scopes':{'FUNCTION':[{'url':'https://fixture.invalid/functions'}],'DATATYPE':[],'OPERATOR':[]}}
    monkeypatch.setattr(scoped_build,'resolve_feature_scope',lambda **kw:calls.append(kw) or {'manifest':manifest})
    def crawl(**kw):
        p=kw['output_root']/'corpus/page_manifest.jsonl';p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'url':'https://fixture.invalid/functions','fetch_status':'FETCHED'})+'\n')
        return {'summary':{'budget_exhausted':True,'frontier_stop_reason':'page_budget'}}
    monkeypatch.setattr(scoped_build,'run_scoped_discovery',crawl)
    monkeypatch.setattr(scoped_build,'build_document_ir_from_record',lambda r,**kw:{'page_id':'fixture','source_url':r['url']})
    docs,_,summary=scoped_build.scoped_documents('fixture','https://fixture.invalid/docs',tmp_path,{})
    assert calls[0]['docs_url']=='https://fixture.invalid/docs' and docs[0]['page_id']=='fixture'
    assert summary['DISCOVERY_TRUNCATED'] and summary['scope_manifest']['scopes']['OPERATOR']==[]


def test_failure_record_and_frozen_directory_safety(tmp_path,monkeypatch):
    monkeypatch.setattr(orchestrator,'_run_build',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('fixture failure')))
    output=tmp_path/'new'
    with pytest.raises(RuntimeError):orchestrator.run_build('fixture','https://fixture.invalid',output,semantic=False)
    assert json.loads((output/'run_manifest.json').read_text())['execution_status']=='EXECUTION_FAILED'
    old=tmp_path/'frozen';old.mkdir();(old/'marker').write_text('frozen')
    with pytest.raises(RuntimeError):orchestrator.run_build('fixture','https://fixture.invalid',old,semantic=False)
    assert not (old/'run_manifest.json').exists()


def test_resume_refuses_unsafe_full_stage(tmp_path):
    with pytest.raises(ValueError,match='resume'):
        orchestrator.run_build('fixture','https://fixture.invalid',tmp_path/'run',resume=True,semantic=False)


def test_external_profile_mismatch_fails_before_crawl(tmp_path, monkeypatch):
    from agents.pipeline import scoped_build
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    profile = build_cold_start_profile(dbms='other', documents=[], run_id='fixture')
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(profile), encoding='utf-8')
    def forbidden(*args, **kwargs):
        raise AssertionError('Must not fetch before validating external profile')
    monkeypatch.setattr(scoped_build, 'scoped_documents', forbidden)
    with pytest.raises(ValueError, match='DBMS mismatch'):
        orchestrator.run_build('fixture','https://fixture.invalid',tmp_path/'run',profile_path=str(path),semantic=False)


def test_external_profile_cli_argument_is_forwarded(tmp_path, monkeypatch):
    from agents.pipeline import delivery
    monkeypatch.setattr(delivery,"publish",lambda *a,**kw: {})
    calls=[]
    monkeypatch.setattr(orchestrator,'run_build',lambda *a,**kw:calls.append(kw) or {'status':'accepted'})
    args=cli._product_parser().parse_args(['build','--dbms','fixture','--docs-manifest','urls.json',
                                         '--profile','profile.json','--output-root',str(tmp_path/'run')])
    assert cli._run_product_command(args)
    assert calls[0]['profile_path']=='profile.json'


def test_retired_legacy_route_is_rejected():
    with pytest.raises(SystemExit):
        cli._product_parser().parse_args(['build','--dbms','fixture','--docs-url','https://fixture.invalid',
                                         '--pipeline','legacy'])


def test_quality_is_not_execution_success_when_semantic_disabled(tmp_path,monkeypatch):
    def result(*a,**k):
        return {'status':'accepted','publication':{'final_count':0,'review_count':1},'lineage':{'status':'PASS'},
                'semantic':{'summary':{}},'frontend':{'canonical_records':0,'frontend_records':0},
                'unified_contract':{'discovery':{'scope_manifest':{'scopes':{'FUNCTION':[1],'OPERATOR':[],'DATATYPE':[1]}}}}}
    monkeypatch.setattr(orchestrator,'_run_build',result)
    r=orchestrator.run_build('fixture','https://fixture.invalid',tmp_path/'quality',semantic=False)
    assert r['execution_status']=='EXECUTION_COMPLETED' and r['quality_status']=='QUALITY_NOT_EVALUATED'
    assert r['coverage_by_type']['OPERATOR']=='NOT_EVALUATED' and r['RELEASE_CONTRACT_VALID'] is False


def test_production_adapter_reexports_and_api_optin(monkeypatch):
    from agents.semantic_stage import local_provider
    from agents.semantic_extractor.llm_client import LLMClientFactory
    assert callable(local_provider._semantic_from_payload)
    from agents.pipeline import delivery
    monkeypatch.setattr(delivery,"publish",lambda *a,**kw: {})
    calls=[]
    class Stub:
        def _check_ready(self):return 'fixture missing key'
    monkeypatch.setattr(LLMClientFactory,'create',lambda config:calls.append(config) or Stub())
    _make_llm_client(run_llm=True,provider='deterministic',model=None,timeout=1,max_retries=0)
    assert calls==[]
    _,s,_=_make_llm_client(run_llm=True,provider='openai_compatible',model='fixture',timeout=1,max_retries=0)
    assert len(calls)==1 and not s['ready']
