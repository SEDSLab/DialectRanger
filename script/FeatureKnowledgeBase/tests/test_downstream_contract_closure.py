import copy
import hashlib
import json
import pytest

from agents.contracts.field_validation import make_field_contract
from agents.contracts.identity_key import canonical_identity_key
from agents.contracts.evidence_cache import reuse_contracts,seal_bundle
from agents.semantic_stage.pipeline import _load_reusable_bundles,_materialize_semantic_response
from agents.semantic_stage.extractive_fragments import validate_extractive_fragments
from agents.semantic_stage.source_field_ledger import audit_source_fields
from agents.contracts.source_binding import isolate_optional_sources
from agents.pipeline.publication import select_publication


def admitted_record():
    record={'feature_id':'f','name':'TURN','provenance':{'identity_unit_id':'u'},
        'canonical_identity':{'key':canonical_identity_key('sample','TURN','FUNCTION')},
        'semantics':{'description':[{'value':'Returns a value.','evidence_refs':['b'],'evidence':{'block_id':'b'}}]},
        'structural':{'attributes':{}}}
    admission={'identity_unit_id':'u','decision':'ADMITTED_PARTIAL','identity_credible':True,
               'official_source':True,'owner_evidence_correct':True,'lineage_complete':True}
    fields={'description':[{'text':'Returns a value.','evidence_refs':['b']}]}
    validation={'identity_group_id':'u','semantic_status':'PARTIAL','invalid_evidence_refs':1,
                'ungrounded_claims':1,'field_isolation':make_field_contract('u',fields)}
    return record,admission,validation


def test_rejected_example_does_not_block_accepted_description():
    b={'source_blocks':[{'evidence_ref':'b','block_id':'b','text':'Returns a value.'}]}
    parsed={'description':[{'text':'Returns a value.','evidence_refs':['b']}],
            'official_examples':[{'sql':'SELECT invented();','evidence_refs':['missing']}]}
    result=validate_extractive_fragments(parsed,b)
    assert result['accepted_claims']==1 and result['rejected_claims']==1
    assert not result['fields']['official_examples']
    assert any(r['disposition']=='QUARANTINED' and r['raw_claim']['sql']=='SELECT invented();' for r in result['claim_results'])
    record,admission,v=admitted_record();v['field_isolation']=make_field_contract('u',result['fields'])
    accepted=result['fields']['description'][0]
    record['semantics']['description'][0].update({k:accepted[k] for k in ('fragment_refs','source_locations','field_evidence')})
    final,review,_=select_publication([record],[admission],[v],{'status':'PASS'})
    assert final==[record] and not review


@pytest.mark.parametrize('tamper',['text','reference','contract','uid','bad_source','conflict','lineage'])
def test_field_isolation_cannot_grant_unverified_content_or_identity(tamper):
    r,a,v=admitted_record();lineage={'status':'PASS'}
    if tamper=='text':r['semantics']['description'][0]['value']='Invented'
    elif tamper=='reference':r['semantics']['description'][0]['evidence_refs']=['foreign']
    elif tamper=='contract':v['field_isolation']['accepted_claims'][0][1]='forged'
    elif tamper=='uid':v['field_isolation']=make_field_contract('another',{})
    elif tamper=='bad_source':a['owner_evidence_correct']=False
    elif tamper=='conflict':a['decision']='NEEDS_REVIEW'
    else:lineage['status']='FAIL'
    final,review,_=select_publication([r],[a],[v],lineage)
    assert not final and review


@pytest.mark.parametrize('state',['NOT_RUN','UNAVAILABLE','CALL_FAILED','PARSE_FAILED'])
def test_structural_record_can_publish_without_semantic_execution(state):
    r,a,v=admitted_record();r['semantics']={};r['semantic_execution_state']=state
    v.update(semantic_status=state,field_isolation=make_field_contract('u',{}))
    final,_,_=select_publication([r],[a],[v],{'status':'PASS'})
    assert final and final[0]['semantic_execution_state']==state


def test_keys_separate_quoted_names_and_legitimate_types():
    assert canonical_identity_key('d','space."MiXeD"','FUNCTION')!=canonical_identity_key('d','space."MIXED"','FUNCTION')
    assert canonical_identity_key('d','turn','FUNCTION')==canonical_identity_key('d','TURN','FUNCTION')
    assert canonical_identity_key('d','DATE','FUNCTION')!=canonical_identity_key('d','DATE','DATATYPE')


@pytest.mark.parametrize('change',['profile','html','owner','bundle','version','legacy'])
def test_stale_bundle_is_rebuilt_from_inputs_not_silently_reused(tmp_path,change):
    html=tmp_path/'raw.html';html.write_text('source',encoding='utf8')
    d={'page_id':'p','url':'https://official.test/reference','page_hash':hashlib.sha256(html.read_bytes()).hexdigest(),
       'html_path':str(html),'blocks':[{'block_id':'b','text':'TURN(x)'}]}
    g={'identity_unit_id':'u','owner_ids':['o']};o={'owner_id':'o','page_id':'p','member_block_ids':['b']};p={'hint_contract':{}}
    expected=reuse_contracts([g],[o],[d],p)
    b=seal_bundle({'identity_group_id':'u','source_blocks':[]},expected['u'])
    if change=='profile':p['hint_contract']['field']='changed'
    elif change=='html':html.write_text('changed source',encoding='utf8')
    elif change=='owner':o['member_block_ids']=[]
    elif change=='bundle':b['source_blocks']=[{'block_id':'forged'}]
    elif change=='version':b['reuse_contract']['context']['version']='old'
    else:b.pop('reuse_contract')
    path=tmp_path/'bundles.jsonl';path.write_text(json.dumps(b)+'\n',encoding='utf8');audit=[]
    reused=_load_reusable_bundles(path,[g],expected=reuse_contracts([g],[o],[d],p),audit=audit)
    assert not reused and audit[0]['state']=='CACHE_STALE'


def test_unchanged_bundle_reuses_with_matching_fingerprint(tmp_path):
    f=tmp_path/'x.html';f.write_text('x',encoding='utf8')
    d={'page_id':'p','html_path':str(f),'page_hash':'h','blocks':[]}
    g={'identity_unit_id':'u','owner_ids':['o']};o={'owner_id':'o','page_id':'p'}
    expected=reuse_contracts([g],[o],[d],{})
    b=seal_bundle({'identity_group_id':'u','source_blocks':[]},expected['u'])
    p=tmp_path/'bundles.jsonl';p.write_text(json.dumps(b)+'\n',encoding='utf8')
    assert _load_reusable_bundles(p,[g],expected=expected)['u']==b


def test_optional_source_quarantine_does_not_hide_bad_core():
    from agents.semantic_stage.source_structure import SourceStructureIndex
    core={'block_id':'b','text':'TURN(x)','owner_ids':['o'],'source_hash':'hash'}
    optional={'block_id':'x','text':'Foreign example','owner_ids':['foreign']}
    owner={'owner_id':'o','page_id':'p','member_block_ids':['b','x']}
    doc={'page_id':'p','url':'https://official.test/reference','page_hash':'hash','blocks':[core,optional]}
    unit={'identity_unit_id':'u','canonical_identity':'TURN','feature_type':'FUNCTION','owner_ids':['o'],
          'occurrence_records':[{'defining':True,'source_block_id':'b'}]}
    bundle={'source_blocks':[core,optional],'owners':[owner]}
    clean=isolate_optional_sources(unit,bundle,[owner],[doc],SourceStructureIndex())
    assert clean['source_blocks']==[core] and clean['field_source_quarantine'][0]['block_id']=='x'
    core['source_hash']='forged'
    clean=isolate_optional_sources(unit,bundle,[owner],[doc],SourceStructureIndex())
    assert core in clean['source_blocks']  # Core is not removed to fake eligibility.


def test_field_ledger_reports_missing_semantics_without_using_extractor():
    b={'block_id':'b','text':'SELECT TURN(1);','block_kind':'CODE','source_structure_status':'RESOLVED',
       'observed_context':{'labels':[{'text':'Examples'}]}}
    bundle={'identity_group_id':'u','source_blocks':[b],'source_urls':['https://official.test/reference']}
    final=[{'provenance':{'identity_unit_id':'u'},'semantics':{}}]
    rows=audit_source_fields(bundle,{},final)
    assert rows[0]['basis']=='OBSERVED_FIELD_LABEL' and rows[0]['disposition']=='MISSING_FROM_SEMANTIC'


def test_field_label_is_not_missing_example_content():
    b={'block_id':'b','text':'Example:','block_kind':'TEXT','source_structure_status':'RESOLVED',
       'observed_context':{'labels':[{'text':'Example'}]}}
    bundle={'identity_group_id':'u','source_blocks':[b]}
    rows=audit_source_fields(bundle,{},[{'provenance':{'identity_unit_id':'u'},'semantics':{}}])
    assert rows[0]['basis']=='FIELD_LABEL_ONLY' and rows[0]['disposition']=='UNKNOWN'


def simple_run(tmp_path,**kwargs):
    from tests.test_profile_definition_execution import document
    from tests.test_five_structure_executors import profile_for
    from tests.test_profile_plan_routing import row_plan
    from agents.pipeline.generic_unified import run_generic_downstream
    d=document(tmp_path,'Functions','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>TURN(x)</td><td>Returns a value.</td></tr></table>')
    p=profile_for(d,'D');p['hint_contract']['definition_structure_patterns']=[row_plan('functions','Function')]
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic',**kwargs)
    return tmp_path/'run'


def test_budget_zero_still_releases_independently_proven_identity(tmp_path):
    root=simple_run(tmp_path,semantic_budget=0)
    rows=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    assert len(rows)==1 and rows[0]['name']=='TURN'
    assert rows[0]['semantic_execution_state']=='NOT_REQUESTED'
    assert not rows[0]['semantics']['description']


def test_pipeline_rejects_bad_example_but_releases_good_fields(tmp_path,monkeypatch):
    from agents.semantic_stage.local_provider import DeterministicEvidenceClient
    original=DeterministicEvidenceClient.call
    def response(self,*args,**kwargs):
        row=original(self,*args,**kwargs)
        parsed=json.loads(row['_raw_response'])
        if 'description' in parsed:
            parsed['official_examples']=[{'sql':'SELECT invented();','evidence_refs':['foreign']}]
            row['_raw_response']=json.dumps(parsed)
        return row
    monkeypatch.setattr(DeterministicEvidenceClient,'call',response)
    root=simple_run(tmp_path)
    rows=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    assert len(rows)==1 and rows[0]['semantics']['description']
    assert not rows[0]['semantics']['examples']
    assert rows[0]['field_validation_status']=='QUARANTINED'
    assert 'invented' not in json.dumps(rows[0]['semantics'])


def test_parameter_and_return_tokens_in_description_are_not_field_proof(tmp_path,monkeypatch):
    from agents.semantic_stage.local_provider import DeterministicEvidenceClient
    original=DeterministicEvidenceClient.call
    def response(self,*args,**kwargs):
        row=original(self,*args,**kwargs);parsed=json.loads(row['_raw_response'])
        if 'description' in parsed:
            # Literal presence in another field is not parameter/return proof.
            ref=parsed['description'][0]['evidence_refs'][0]
            parsed['parameters']=[{'name':'x','description':None,'evidence_refs':[ref]}]
            parsed['return_type']={'text':'value','evidence_refs':[ref]}
            row['_raw_response']=json.dumps(parsed)
        return row
    monkeypatch.setattr(DeterministicEvidenceClient,'call',response)
    root=simple_run(tmp_path)
    rows=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    front=json.loads((root/'frontend/data/sample.json').read_text(encoding='utf8'))['features'][0]
    assert not rows[0]['semantics']['parameters'] and not rows[0]['semantics']['return_type']
    assert not front['parameters'] and not front['return_type']


def test_same_name_function_and_datatype_publish_separately(tmp_path):
    from tests.test_profile_definition_execution import document
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    from agents.pipeline.generic_unified import run_generic_downstream
    d=document(tmp_path,'Reference','<h2>Functions</h2><ul><li><p><code>TOKEN(x)</code></p>'
        '<p>Returns a value.</p></li></ul><h2>Data Types</h2><h3>TOKEN</h3><p>The TOKEN type stores exact values.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='dual-type')
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    assert {r['feature_type'] for r in rows if r['name']=='TOKEN'}=={'function','datatype'}


def test_projection_preserves_all_claim_refs_and_paragraph_order(tmp_path,monkeypatch):
    from tests.test_profile_definition_execution import document
    from tests.test_five_structure_executors import profile_for
    from agents.pipeline.generic_unified import run_generic_downstream
    from agents.semantic_stage.local_provider import DeterministicEvidenceClient
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p>'
        '<p>Returns the first value.</p><p>Preserves the second detail.</p></li></ul>')
    p=profile_for(d,'C');original=DeterministicEvidenceClient.call
    def response(self,*args,**kwargs):
        row=original(self,*args,**kwargs);parsed=json.loads(row['_raw_response'])
        values=parsed.get('description')
        if isinstance(values,list) and len(values)>1:
            refs=list(dict.fromkeys(ref for claim in values for ref in claim['evidence_refs']))
            for claim in values:claim['evidence_refs']=refs
            row['_raw_response']=json.dumps(parsed)
        return row
    monkeypatch.setattr(DeterministicEvidenceClient,'call',response)
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    claims=rows[0]['semantics']['description']
    assert [c['value'] for c in claims]==['Returns the first value.','Preserves the second detail.']
    for c in claims:
        assert len(c['evidence_refs'])==2 and len(c['additional_provenance'])==1
        assert c['value'] in c['evidence']['evidence_span']['text']


@pytest.mark.parametrize('title,header,value,kind',[
    ('Operators','Operator','+','operator'),('Data Types','SQL data type','TOKEN','datatype')])
def test_nonfunction_structural_publication_needs_no_provider_budget(tmp_path,title,header,value,kind):
    from tests.test_profile_definition_execution import document
    from tests.test_five_structure_executors import profile_for
    from tests.test_profile_plan_routing import row_plan
    from agents.pipeline.generic_unified import run_generic_downstream
    d=document(tmp_path,title,'<table><tr><th>'+header+'</th><th>Description</th></tr>'
        '<tr><td>'+value+'</td><td>Official definition.</td></tr></table>')
    p=profile_for(d,'D');p['hint_contract']['definition_structure_patterns']=[row_plan('typed',header)]
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic',semantic_budget=0)
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    assert len(rows)==1 and rows[0]['feature_type']==kind
    summary=json.loads((tmp_path/'run/semantic_stage/evaluation/semantic_summary.json').read_text(encoding='utf8'))
    assert summary['actual_model_calls']==0 and summary['budget_accounting']['provider_calls_total']==0


def test_quoted_identities_remain_distinct_through_final_and_frontend(tmp_path):
    from tests.test_profile_definition_execution import document
    from tests.test_five_structure_executors import profile_for
    from tests.test_profile_plan_routing import row_plan
    from agents.pipeline.generic_unified import run_generic_downstream
    d=document(tmp_path,'Functions','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>space.&quot;MiXeD&quot;(x)</td><td>Returns the first value.</td></tr>'
        '<tr><td>space.&quot;MIXED&quot;(x)</td><td>Returns the second value.</td></tr></table>')
    p=profile_for(d,'D');p['hint_contract']['definition_structure_patterns']=[row_plan('functions','Function')]
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    front=json.loads((tmp_path/'run/frontend/data/sample.json').read_text(encoding='utf8'))['features']
    assert len(rows)==len(front)==2
    assert len({r['canonical_identity']['key'] for r in rows})==2
    assert all(f['overload_count']==1 for f in front)
