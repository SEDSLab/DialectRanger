"""Generic source observations must reach release, without weakening review."""
import json
import pytest
from agents.contracts.publication_integrity import evaluate_publication_integrity
from agents.owner.definition_scope import normalized_name
from agents.document_structure_v2.topology import build_page_topology
from agents.contracts.document_ir import from_v2_page
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.pipeline.generic_unified import run_generic_downstream


@pytest.mark.parametrize('title',['The COMPUTE() function','4.1. The COMPUTE() function','COMPUTE(X)'])
def test_explicit_callable_definition_heading_normalized(title):
    assert normalized_name(title)=='COMPUTE'


def test_incidental_function_heading_not_normalized_to_feature():
    assert normalized_name('Examples using COMPUTE()')!='COMPUTE'


def check_block(block,name='COMPUTE',kind='FUNCTION',status='CONFIRMED'):
    block.update(block_id='b',owner_ids=['o'])
    unit={'identity_unit_id':'u','canonical_identity':name,'feature_type':kind,'owner_ids':['o']}
    return evaluate_publication_integrity(unit,{'source_blocks':[block]},
        {'primary_type':kind,'status':status},{'admission_status':'ADMITTED'})


def test_resolved_local_declaration_is_witness_not_type_review_override():
    block={'text':'COMPUTE(X)','source_structure_status':'RESOLVED',
           'observed_context':{'raw_node_kind':'LI','local_definition':{
               'role':'FUNCTION_DECLARATION_LIST','item_text':'COMPUTE(X)','intro_text':'Supports the following functions:'}}}
    assert check_block(block)['state']=='CONFIRMED'
    assert check_block(block,status='REVIEW_REQUIRED')['state']=='REVIEW'
    block['source_structure_status']='SOURCE_HASH_MISMATCH'
    assert check_block(block)['state']=='REVIEW'


def test_foreign_declaration_not_subject_evidence():
    block={'text':'OTHER(X)','source_structure_status':'RESOLVED',
           'observed_context':{'raw_node_kind':'LI','local_definition':{
               'role':'FUNCTION_DECLARATION_LIST','item_text':'OTHER(X)'}}}
    assert check_block(block)['state']=='REVIEW'


def test_multiple_native_signatures_are_not_alias_or_foreign_subject():
    block={'text':'COMPUTE(X) OTHER(X)','heading_ancestry':['Functions'],
           'observed_context':{'raw_node_kind':'DT'}}
    assert check_block(block)['state']=='CONFIRMED'
    assert check_block(block,name='OTHER')['state']=='CONFIRMED'
    assert check_block(block,name='UNRELATED')['state']=='REVIEW'


def test_shared_term_syntax_is_subject_local():
    from agents.semantic_stage.structured_semantics import extract_structured_semantics
    block={'evidence_ref':'term','block_kind':'DEFINITION','text':'COMPUTE(X) OTHER(X,Y)',
        'observed_context':{'raw_node_kind':'DT'},'definition_section_proofs':[{
            'primary_identity':'COMPUTE','identity_values':['COMPUTE','OTHER'],
            'identity_anchor_block_ids':{'COMPUTE':['term'],'OTHER':['term']}}]}
    for name,expected in [('COMPUTE','COMPUTE(X)'),('OTHER','OTHER(X,Y)')]:
        result=extract_structured_semantics({'normalized_identity':name,'feature_type':'FUNCTION','evidence':[block]})
        assert [c['text'] for c in result['formal_syntax']]==[expected]


@pytest.mark.parametrize('body,title,name',[
    ('<h1>Functions</h1><h2>The COMPUTE() function</h2><p>The COMPUTE(X) function returns its input.</p>',
     'Reference','COMPUTE'),
    ('<h1>Overview</h1><p>The CODEC() function is an SQL function implemented as an extension.</p>',
     'The CODEC() SQL Function','CODEC'),
    ('<h1>Overview</h1><p>This engine supports the following functions:</p><ol><li>STAMP(value)</li></ol>',
     'Time reference','STAMP'),
])
def test_actual_frozen_structure_reaches_final(tmp_path,body,title,name):
    path=tmp_path/'source.html'
    path.write_text('<html><head><title>'+title+'</title></head><body>'+body+'</body></html>',encoding='utf-8')
    doc=from_v2_page(build_page_topology({'url':'https://official.test/ref','html_path':str(path)},{}),
        dbms='fixture',html_path=str(path))
    profile=build_cold_start_profile(dbms='fixture',documents=[doc],run_id='test')
    run_generic_downstream(dbms='fixture',documents=[doc],profile=profile,output_root=tmp_path/'run',
        semantic=True,semantic_provider='deterministic')
    final=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    assert any(r['name']==name and r['feature_type']=='function' for r in final)
