import copy
import json
import pytest
from agents.semantic_stage.grounding import validate_semantic_output
from agents.contracts.field_evidence import build_field_evidence,bind_claim
from agents.type.adaptation import _datatype_surfaces_from_block,_normal_surface
from agents.type.evidence import _surface_equals


def cell_bundle():
    cells=[]
    for i,(label,text) in enumerate([('Function','TURN(x)'),('Description','Returns the value.'),('Example','SELECT TURN(1);'),('Return type','integer')]):
        cells.append({'cell_index':i,'text':text,'dom_locator':'table > tr > td:'+str(i),
            'header_status':'EXPLICIT','headers':[{'text':label}]})
    return {'identity_group_id':'u','normalized_identity':'TURN','feature_type':'FUNCTION',
        'source_blocks':[{'block_id':'b','evidence_ref':'b','source_hash':'hash','source_url':'https://official.test/',
            'owner_ids':['o'],'dom_path':'table > tr','text':' '.join(c['text'] for c in cells),
            'block_kind':'TABLE_ROW','table_context':{'cells':cells}}]}


@pytest.mark.parametrize('raw,claim,field',[
    ('x < y','x > y','formal_syntax'),("SELECT 'A';","SELECT 'a';",'official_examples'),
    ('TURN("MiXeD")','TURN("MIXED")','formal_syntax'),("SELECT 'a  b';","SELECT 'a b';",'official_examples')])
def test_significant_sql_mutation_is_rejected(raw,claim,field):
    b={'source_blocks':[{'evidence_ref':'b','text':raw}]}
    c={'text':claim,'evidence_refs':['b']}
    assert validate_semantic_output({field:[c]},b)['accepted_claims']==0


def test_formatting_whitespace_is_preserved_without_false_rejection():
    b={'source_blocks':[{'evidence_ref':'b','text':'SELECT\n TURN(1);'}]}
    r=validate_semantic_output({'official_examples':[{'sql':'SELECT TURN(1);','evidence_refs':['b']}]},b)
    assert r['accepted_claims']==1


@pytest.mark.parametrize('mutation',['role','cell','hash','subject','fragment'])
def test_field_proof_mutations_fail_closed(mutation):
    b=cell_bundle();b['field_evidence']=build_field_evidence(b)
    f=next(f for f in b['field_evidence'] if f['field']=='description')
    c={'text':'Returns the value.','evidence_refs':['b'],'source_locations':[{
        'block_id':'b','dom_locator':f['dom_locator'],'cell_index':f['cell_index'],'source_hash':'hash'}]}
    field='description';value=c
    if mutation=='role':field='formal_syntax';value=[c]
    if mutation=='cell':c['source_locations'][0]['cell_index']=0
    if mutation=='hash':c['source_locations'][0]['source_hash']='different'
    if mutation=='subject':b['normalized_identity']='OTHER'
    if mutation=='fragment':c['fragment_refs']=['invented']
    assert validate_semantic_output({field:value},b)['accepted_claims']==0


def test_known_description_cannot_be_syntax_without_any_locator():
    b=cell_bundle()
    r=validate_semantic_output({'formal_syntax':[{'text':'Returns the value.','evidence_refs':['b']}]},b)
    assert not r['fields']['formal_syntax']


def test_return_column_does_not_recover_first_cell_as_datatype():
    assert not _datatype_surfaces_from_block({'block_type':'table_row','text':'log2(x) double',
        'cells':['log2(x)','double'],'table_headers':['Function','Return type']})
    assert _datatype_surfaces_from_block({'block_type':'table_row','text':'TOKEN Exact values',
        'cells':['TOKEN','Exact values'],'table_headers':['Data type','Description']})


def test_quoted_identity_equality_is_exact():
    assert not _surface_equals('space."MiXeD"','space."MiXeD"Other(x)')
    assert not _surface_equals('space."MiXeD"','space."MIXED"(x)')
    assert _surface_equals('space."MiXeD"','SPACE."MiXeD"(x)')
    assert _normal_surface('space."MiXeD"')=='SPACE."MiXeD"'


def test_multiple_return_claims_are_not_overwritten():
    b=cell_bundle();r=validate_semantic_output({'return_type':[{'text':'integer','evidence_refs':['b']},
        {'text':'NOT PRESENT','evidence_refs':['b']}]},b)
    assert [c['text'] for c in r['fields']['return_type']]==['integer']


def test_parameter_columns_reach_final_with_name_description_and_proof(tmp_path):
    from tests.test_profile_definition_execution import document,run
    from tests.test_five_structure_executors import profile_for
    d=document(tmp_path,'TURN function','<h2>Syntax</h2><pre>TURN(x)</pre><p>Returns a rotation.</p>'
        '<h2>Parameters</h2><table><tr><th>Parameter</th><th>Description</th></tr>'
        '<tr><td>x</td><td>The input value.</td></tr></table>')
    rows=run(tmp_path,d,profile_for(d,'A'))
    r=next(r for r in rows if r['name']=='TURN' and r['feature_type']=='function')
    assert r['semantics']['parameters'][0]['name']=='x'
    assert r['semantics']['parameters'][0]['description']=='The input value.'
    assert r['semantics']['parameters'][0]['field_evidence']
    front=json.loads((tmp_path/'run/frontend/data/sample.json').read_text(encoding='utf8'))['features']
    assert any(f['parameters'] for f in front)


def test_fragment_metadata_tampering_is_rejected_at_publication(tmp_path):
    from tests.test_downstream_contract_closure import simple_run
    from agents.pipeline.publication import select_publication
    root=simple_run(tmp_path)
    r=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf8'))[0]
    admissions=[json.loads(l) for l in (root/'canonical/admission_decisions.jsonl').read_text(encoding='utf8').splitlines()]
    validations=[json.loads(l) for l in (root/'semantic_stage/semantic/semantic_validation.jsonl').read_text(encoding='utf8').splitlines()]
    r['semantics']['description'][0]['source_locations'][0]['source_hash']='tampered'
    final,review,_=select_publication([r],admissions,validations,{'status':'PASS'})
    assert not final and 'ACCEPTED_FIELD_CONTRACT_INVALID' in review[0]['publication']['reason_codes']


@pytest.mark.parametrize('reverse',[False,True])
def test_multi_block_composition_uses_same_verified_subject_and_order(tmp_path,reverse):
    from tests.test_profile_definition_execution import document
    from tests.test_five_structure_executors import profile_for
    from agents.pipeline.generic_unified import run_generic_downstream
    d=document(tmp_path,'Reference','<h2>turn(x)</h2><p>First detail.</p><p>Second detail.</p>'
        '<h2>other(x)</h2><p>Foreign detail.</p>')
    p=profile_for(d,'B');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'}],'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    b=next(json.loads(l) for l in (tmp_path/'run/evidence/bundles.jsonl').read_text(encoding='utf8').splitlines()
        if json.loads(l)['normalized_identity']=='TURN')
    fragments=[f for f in build_field_evidence(b) if f['field']=='description' and f['raw_text'] in {'First detail.','Second detail.'}]
    fragments.sort(key=lambda f:f['source_order'])
    assert len(fragments)==2
    if reverse:fragments.reverse()
    claim={'text':'\n'.join(f['raw_text'] for f in fragments),'evidence_refs':[f['block_id'] for f in fragments],
           'fragment_refs':[f['fragment_id'] for f in fragments]}
    result=validate_semantic_output({'description':claim},b)
    assert bool(result['accepted_claims']) is (not reverse)
    foreign=copy.deepcopy(claim);foreign['text']+=' Foreign detail.'
    assert not validate_semantic_output({'description':foreign},b)['accepted_claims']


def test_source_ledger_does_not_hide_changed_operator():
    from agents.semantic_stage.source_field_ledger import audit_source_fields
    b={'identity_group_id':'u','source_blocks':[{'block_id':'b','text':'x < y','block_kind':'CODE',
        'source_structure_status':'RESOLVED','observed_context':{'labels':[{'text':'Syntax'}]}}]}
    s={'formal_syntax':[{'text':'x > y'}]}
    r=audit_source_fields(b,s,[{'provenance':{'identity_unit_id':'u'},'semantics':{'syntax':[{'value':'x > y'}]}}])
    assert r[0]['disposition']=='MISSING_FROM_SEMANTIC'


def test_first_loss_tracks_publication_not_audit_canonical(tmp_path):
    from agents.pipeline.generic_unified import _write_conservation_audit
    stage=tmp_path/'semantic';(stage/'semantic').mkdir(parents=True)
    (stage/'semantic/semantic_features.jsonl').write_text(json.dumps({'identity_group_id':'u'})+'\n',encoding='utf8')
    audit=_write_conservation_audit(tmp_path,candidates=[],accepted=[],units=[{'identity_unit_id':'u',
        'group_status':'CONFIRMED_FEATURE','adaptation_provenance':[{'block_id':'b'}]}],decisions=[],
        bundles=[{'identity_group_id':'u'}],semantic_root=stage,admissions=[{'identity_unit_id':'u','decision':'ADMITTED'}],
        contract_canonical=[{'identity_unit_id':'u'}],frontend_manifest={},final_records=[],
        publication_decisions=[{'identity_unit_id':'u','published':False,'reason_codes':['CANONICAL_IDENTITY_COLLISION']}])
    assert audit['rows'][0]['first_loss']=='PUBLICATION'


def test_cache_only_loader_accepts_failed_fetch_audit_rows_without_network(tmp_path):
    from agents.pipeline.scoped_build import load_frozen_fetcher
    (tmp_path/'ok.html').write_text('Frozen source',encoding='utf8')
    rows=[{'url':'https://official.test/ok','fetch_status':'FETCHED','local_artifact_path':'ok.html'},
          {'url':'https://official.test/failed','fetch_status':'FETCH_FAILED','local_artifact_path':None}]
    (tmp_path/'page_manifest.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf8')
    fetch,cache=load_frozen_fetcher(tmp_path)
    assert fetch.network_free and len(cache)==1
    assert fetch(None,'https://official.test/failed')['error_type']=='FrozenCacheMiss'


def test_multi_signature_defining_slot_preserves_all_validated_variants():
    b={'identity_group_id':'u','normalized_identity':'TURN','feature_type':'FUNCTION',
       'defining_variants':['turn( X )','turn( X , Y )'],
       'source_blocks':[{'block_id':'b','evidence_ref':'b','text':'turn( X ) turn( X , Y )',
            'block_kind':'TEXT','occurrence_roles':['DEFINING_IDENTITY'],'dom_path':'dl > dt'}]}
    values=[{'text':v,'evidence_refs':['b'],'source_locations':[{'block_id':'b','dom_locator':'dl > dt'}]} for v in b['defining_variants']]
    result=validate_semantic_output({'formal_syntax':values},b)
    assert [v['text'] for v in result['fields']['formal_syntax']]==b['defining_variants']
    assert not validate_semantic_output({'formal_syntax':[{'text':'other(X)','evidence_refs':['b']}]},b)['accepted_claims']
