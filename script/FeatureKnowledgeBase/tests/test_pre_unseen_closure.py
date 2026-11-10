import copy,hashlib,json
from pathlib import Path
import pytest
from agents.profile_bootstrap.cold_start import build_cold_start_profile,validate_cold_start_profile,load_cold_start_profile,profile_digest
from agents.semantic_stage.applicability import operator_explanation_spans,bounded_proof,foreign_type_reason
from agents.semantic_stage.structured_semantics import extract_structured_semantics
from agents.semantic_stage.source_structure import example_introduction,SourceStructureIndex

def manual():
    p=build_cold_start_profile(dbms='sample',documents=[],run_id='test')
    p.update(generated=False,generation_mode='MANUAL_PREPARED',manual_edit=False,
        profile_provenance={'author':'tester','authored_at':'2026-10-23','revision':'1'})
    return p

def test_manual_profile_load_is_honest_and_unchanged(tmp_path):
    p=manual();old=copy.deepcopy(p);path=tmp_path/'profile.json';path.write_text(json.dumps(p))
    assert load_cold_start_profile(path)==old and profile_digest(p)==profile_digest(old)
    p.update(generation_mode='MANUAL_REVISED',manual_edit=True)
    with pytest.raises(ValueError):validate_cold_start_profile(p)
    p['profile_provenance']['derived_from_digest']=profile_digest(old)
    assert validate_cold_start_profile(p)==p

@pytest.mark.parametrize('change',['false_auto','unknown_top','unknown_hint','unsupported_plan','inventory','no_origin'])
def test_manual_profile_rejects_unsupported_or_disguised_configuration(change):
    p=manual()
    if change=='false_auto':p['generation_mode']='AUTO_GENERATED'
    elif change=='unknown_top':p['selector']='.db-only'
    elif change=='unknown_hint':p['hint_contract']['force_type']='FUNCTION'
    elif change=='unsupported_plan':p['hint_contract']['content_binding_patterns']=['ALL_PAGE_TEXT']
    elif change=='inventory':p['known_features']=['ALPHA']
    else:p['profile_provenance']={}
    with pytest.raises(ValueError):validate_cold_start_profile(p)

@pytest.mark.parametrize('text,names',[
    ('The || operator is concatenate. The -> and ->> operators are extract.',[['||'],['->','->>']]),
    ('The unary operator + is a no-op.',[['+']]),
    ('The IS and IS NOT operators work with nulls.',[['IS','IS NOT']]),
    ('Integer divide yields an integer result, truncated toward zero.',[['/']]),
    ('If the optional ESCAPE clause is present, its expression has one character.',[['ESCAPE']]),
    ('ALPHA mentions || in an ordinary description.',[]),
    ('SELECT a || b; -- an operator example',[]),
    ('The database operator is maintained by staff.',[]),
    ('The left operand of the MATCH operator is a table name.',[]),
    ('If the left operand of the MATCH operator is a column, then use that column.',[]),
    ('Text values from the ->> operator are inserted as strings.',[]),
    ('The not-equal operator can be either != or <>. The || operator is concatenate.',[['||']]),
])
def test_operator_explanations_are_explicit_member_spans(text,names):
    spans=operator_explanation_spans(text)
    assert [s['members'] for s in spans]==names
    for span in spans:assert span['text'] in text
    if len(spans)>1:assert 'extract' not in spans[0]['text']

def test_foreign_typed_heading_is_not_permission_to_delete_the_real_type():
    b={'block_id':'b','block_kind':'CODE','text':'SELECT TOKEN(3)',
       'observed_context':{'heading_path':['TOKEN function','Example'],'labels':[{'text':'Example'}]}}
    r=extract_structured_semantics({'normalized_identity':'TOKEN','feature_type':'DATATYPE','evidence':[b]})
    assert not r['official_examples']
    assert r['semantic_extensions']['uncertain_fields'][0]['raw_text']==b['text']
    r=extract_structured_semantics({'normalized_identity':'TOKEN','feature_type':'FUNCTION','evidence':[b]})
    assert r['official_examples']

def test_grammar_proof_stops_at_new_real_emphasis_label_without_upstream_mutation():
    a={'block_id':'a','text':'<op> ::= ||'}
    label={'block_id':'b','text':'OTHER FAMILY','inline_nodes':[{'element_name':'span','semantic_classes':['emphasis'],'text':'OTHER FAMILY'}]}
    foreign={'block_id':'c','text':'Foreign function description.'}
    p={'reference_mode':'PARAGRAPH_SIGNATURE','block_ids':['a','b','c']};before=copy.deepcopy(p)
    out=bounded_proof(p,{'blocks':[a,label,foreign]})
    assert out['block_ids']==['a'] and out['excluded_content_block_ids']==['b','c'] and p==before

def test_negated_member_cannot_borrow_unqualified_positive_truth_conditions():
    b={'block_id':'b','text':'If either member is null the distinct result is true.', 'block_kind':'TEXT',
       'definition_section_proofs':[{'primary_identity':'IS DISTINCT FROM','identity_values':['IS DISTINCT FROM','IS NOT DISTINCT FROM']}]}
    r=extract_structured_semantics({'normalized_identity':'IS NOT DISTINCT FROM','feature_type':'OPERATOR','evidence':[b]})
    assert not r['description'] and r['semantic_extensions']['uncertain_fields']

@pytest.mark.parametrize('identity,quarantine',[('*',True),('/',True),('+',False),('-',False)])
def test_shared_member_prose_does_not_assign_another_members_property(identity,quarantine):
    b={'block_id':'b','block_kind':'TEXT','text':'Unary + is a noop. Unary - is negation.',
       'definition_section_proofs':[{'primary_identity':identity,'identity_values':['+','-','*','/']}]}
    r=extract_structured_semantics({'normalized_identity':identity,'feature_type':'OPERATOR','evidence':[b]})
    assert bool(r['semantic_extensions']['uncertain_fields'])==quarantine
    if quarantine:assert not r['description']

def test_conditional_operator_paragraph_is_not_truncated_at_incidental_subject():
    text='The IS and IS NOT operators work with NULL. If both operands are NULL, then the IS operator is true and the IS NOT operator is false.'
    spans=operator_explanation_spans(text)
    assert spans==[{'members':['IS','IS NOT'],'text':text}]

@pytest.mark.parametrize('text,expected',[
    ('When two arguments are used, an example, including the result, is given below:',True),
    ('An example, including the result, is given below:',True),
    ('No example including the result is given below.',False),
])
def test_result_intro_is_example_not_formal_declaration(text,expected):
    assert example_introduction(text)==expected

def test_manual_profile_runs_real_generic_downstream(tmp_path):
    from tests.test_paragraph_label_definition import doc,entry
    from agents.pipeline.generic_unified import run_generic_downstream
    from agents.semantic_extractor.llm_client import LLMClientFactory
    d=doc(tmp_path,entry('ALPHA','Returns the current value.'))
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='auto')
    p.update(generated=False,generation_mode='MANUAL_PREPARED',manual_edit=False,profile_provenance=manual()['profile_provenance'])
    original=copy.deepcopy(p)
    result=run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    assert any(f['name']=='ALPHA' and f['semantics']['description'] for f in fs)
    assert p==original and result['semantic']['summary']['actual_model_calls']==0

@pytest.mark.parametrize('tamper',[None,'hash','span','identity','owner','source_file'])
def test_supplement_is_a_revalidated_relation_not_arbitrary_owner_widening(tmp_path,tamper):
    from tests.test_paragraph_label_definition import doc
    from agents.semantic_stage.applicability import supplemental_operator_blocks,verified_supplement
    d=doc(tmp_path,'<table><tr><th>Operator</th><th>Description</th></tr><tr><td>||</td><td>Concatenate</td></tr></table>'
        '<p>The || operator is concatenate. The -> operator is extract.</p>','Expressions')
    d['page_hash']=hashlib.sha256(open(d['html_path'],'rb').read()).hexdigest()
    o={'owner_id':'o','page_id':d['page_id'],'member_block_ids':[]}
    u={'canonical_identity':'||','feature_type':'OPERATOR'}
    proofs=supplemental_operator_blocks(u,[o],{d['page_id']:d},SourceStructureIndex(),{})
    bid=next(iter(proofs));raw=next(b for b in d['blocks'] if b['block_id']==bid)
    b=dict(raw,content_applicability=copy.deepcopy(proofs[bid]))
    if tamper=='hash':b['content_applicability']['source_hash']='bad'
    elif tamper=='span':b['content_applicability']['spans'][0]['text']='other'
    elif tamper=='identity':u['canonical_identity']='->'
    elif tamper=='owner':o['owner_id']='foreign'
    elif tamper=='source_file':open(d['html_path'],'a').write('changed')
    assert verified_supplement(u,b,o,d)==(tamper is None)

def test_explicit_operator_prose_reaches_final_with_unchanged_identity_scope(tmp_path,monkeypatch):
    from tests.test_paragraph_label_definition import doc
    from agents.pipeline.generic_unified import run_generic_downstream
    from agents.semantic_extractor.llm_client import LLMClientFactory
    monkeypatch.setattr(LLMClientFactory,'create',lambda *a,**k:pytest.fail('External provider forbidden'))
    d=doc(tmp_path,'<table><tr><th>Operator</th><th>Description</th></tr><tr><td>||</td><td>Concatenate</td></tr></table>'
        '<p>The || operator is concatenate. The -> operator is extract.</p>','Expressions')
    d['page_hash']=hashlib.sha256(open(d['html_path'],'rb').read()).hexdigest()
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='test')
    run_generic_downstream(dbms='sample',documents=[d],profile=p,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    f=next(f for f in fs if f['name']=='||' and f['feature_type']=='operator')
    assert any('The || operator' in c['value'] for c in f['semantics']['description'])
    assert not any('extract' in c['value'] for c in f['semantics']['description'])

@pytest.mark.parametrize('mode',['absolute','relative','missing','hash'])
def test_frozen_fetcher_is_strict_and_never_falls_back(tmp_path,mode,monkeypatch):
    from agents.pipeline.scoped_build import load_frozen_fetcher
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('Network forbidden'))
    p=tmp_path/'raw_html'/'x.html';p.parent.mkdir();p.write_bytes(b'<p>cached</p>')
    row={'url':'https://example.org/x','local_artifact_path':str(p) if mode=='absolute' else 'raw_html/x.html','content_hash':hashlib.sha256(p.read_bytes()).hexdigest()}
    if mode=='missing':p.unlink()
    if mode=='hash':row['content_hash']='wrong'
    (tmp_path/'page_manifest.jsonl').write_text(json.dumps(row)+'\n',encoding='utf8')
    if mode in {'missing','hash'}:
        with pytest.raises((FileNotFoundError,ValueError)):load_frozen_fetcher(tmp_path)
    else:
        fetch,_=load_frozen_fetcher(tmp_path)
        assert fetch.network_free and fetch(None,row['url'])['raw']==p.read_bytes()
        assert fetch(None,'https://example.org/missing')['error_type']=='FrozenCacheMiss'

def test_manual_profile_explicit_cached_unified_cli(tmp_path,monkeypatch):
    import socket,run_feature_crawler as cli
    from agents.semantic_extractor.llm_client import LLMClientFactory
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('Network forbidden'))
    monkeypatch.setattr(LLMClientFactory,'create',lambda *a,**k:pytest.fail('Provider forbidden'))
    p=manual();p['dbms']='fixture';profile=tmp_path/'profile.json';profile.write_text(json.dumps(p),encoding='utf8')
    url='https://fixture.example/functions'
    urls=tmp_path/'urls.json';urls.write_text(json.dumps({'dbms':'fixture','documents':[{'url':url}]}))
    html=tmp_path/'x.html';html.write_text('<html><head><title>Fixture SQL Functions</title></head><body><h1>Functions</h1><h2>ALPHA(X)</h2><p>Returns the argument.</p></body></html>')
    (tmp_path/'page_manifest.jsonl').write_text(json.dumps({'url':url,'local_artifact_path':'x.html','content_hash':hashlib.sha256(html.read_bytes()).hexdigest()})+'\n')
    monkeypatch.setattr(cli,'ROOT',str(tmp_path))
    out=tmp_path/'output'
    args=cli._product_parser().parse_args(['build','--dbms','fixture','--docs-manifest',str(urls),'--profile',str(profile),
        '--cache-only','--cache-dir',str(tmp_path),'--output-root',str(out),'--semantic-provider','deterministic'])
    assert cli._run_product_command(args)
    runtime=Path(json.loads((out/'summary.json').read_text())['source_run'])
    assert {x.name for x in out.iterdir()}=={'final.json','review.json','summary.json'}
    assert json.loads((runtime/'run_manifest.json').read_text())['actual_model_calls']==0
    meta=json.loads((runtime/'profile/profile_metadata.json').read_text())
    assert meta['generation_mode']=='MANUAL_PREPARED'
