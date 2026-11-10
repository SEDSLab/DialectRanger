import copy
import json
import pytest
from tests.test_profile_definition_execution import document,run
from agents.owner.source_relations import prepare_relation_documents,verified_relation
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.identity.production import run_identity_owner_path
from agents.pipeline.generic_unified import run_generic_downstream
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology


def at_url(d,url):
    result=from_v2_page(build_page_topology({'url':url,'html_path':d['html_path']},{}),
                        dbms='sample',html_path=d['html_path'])
    result['document_id']=result['page_id']
    return result


def test_bare_catalog_with_external_prose_is_structural_not_fake_description(tmp_path):
    d=document(tmp_path,'Spatial Data Types','<p>These data types store geometric values.</p>'
        '<ul><li><code>SHAPE</code></li><li><code>DOT</code></li></ul>'
        '<p>The types share coordinate reference properties.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='relations')
    old=copy.deepcopy(d);prepared=prepare_relation_documents([d],p)
    assert d==old
    scopes=prepared[0]['definition_source_relation_scopes']
    assert {s['primary_identity'] for s in scopes}=={'SHAPE','DOT'}
    for s in scopes:
        anchor=next(b for b in d['blocks'] if b['block_id']==s['heading_block_id'])
        assert verified_relation(s,anchor,s['primary_identity'])
        assert s['shared_context_block_ids']
    fs=run(tmp_path,d,p)
    assert {r['name'] for r in fs}=={'SHAPE','DOT'}
    assert all(r['feature_type']=='datatype' and not r['semantics']['syntax']
               and not r['semantics']['description'] for r in fs)
    bundles=[json.loads(l) for l in (tmp_path/'run/evidence/bundles.jsonl').open(encoding='utf-8')]
    assert all(any(r['relation']=='SHARED_CONTEXT_FOR' for r in b['definition_source_relations']) for b in bundles)


@pytest.mark.parametrize('heading',['API Data Types','Datatype Examples','Type Mapping'])
def test_non_native_member_lists_do_not_create_relation_proofs(tmp_path,heading):
    d=document(tmp_path,heading,'<p>These data types represent values.</p>'
        '<ul><li><code>SHAPE</code></li><li><code>DOT</code></li></ul>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='negative')
    assert not prepare_relation_documents([d],p)[0]['definition_source_relation_scopes']


def test_class_definition_requires_catalog_membership_and_keeps_separate_subject(tmp_path):
    folder=tmp_path/'catalog';folder.mkdir()
    catalog=document(folder,'Spatial Data Types','<p>These data types store geometry values.</p>'
        '<ul><li><code>SHAPE</code></li><li><code>DOT</code></li></ul>')
    catalog=at_url(catalog,'https://official.test/catalog')
    d=document(tmp_path,'Dot Class','<p>A Dot is a geometry representing a location.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[catalog,d],run_id='class')
    ds=prepare_relation_documents([catalog,d],p)
    assert ds[1]['definition_source_relation_scopes'][0]['identity_values']==['DOT']
    assert not prepare_relation_documents([d],p)[0]['definition_source_relation_scopes']
    result=run_identity_owner_path([catalog,d],p)
    assert any(u['canonical_identity']=='DOT' and u['feature_type']=='DATATYPE' for u in result['units'])


def test_explicit_datatype_page_definition_and_scope_control(tmp_path):
    d=document(tmp_path,'The DOCUMENT Data Type','<p>The engine supports a native DOCUMENT data type.</p>'
        '<p>It validates stored documents.</p><h2>Other operations</h2><p>FOREIGN(x) returns another value.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='page')
    prepared=prepare_relation_documents([d],p)
    scope=prepared[0]['definition_source_relation_scopes'][0]
    assert scope['primary_identity']=='DOCUMENT'
    assert not any('FOREIGN' in b['text'] for b in d['blocks'] if b['block_id'] in scope['block_ids'])
    fs=run(tmp_path,d,p)
    r=next(r for r in fs if r['name']=='DOCUMENT' and r['feature_type']=='datatype')
    assert any('validates' in c['value'] for c in r['semantics']['description'])
    disabled=copy.deepcopy(p);disabled['hint_contract']['definition_scope_patterns']=[]
    assert prepare_relation_documents([d],disabled)==[d]
    from agents.owner.definition_scope import definition_scopes
    assert not any(s.get('definition_pattern')=='SOURCE_RELATION_DEFINITION'
                   for s in definition_scopes(prepared[0],disabled))


def test_invalid_cited_anchor_cannot_be_verified(tmp_path):
    d=document(tmp_path,'The DOCUMENT Data Type','<p>The engine supports a native DOCUMENT data type.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='anchor')
    s=prepare_relation_documents([d],p)[0]['definition_source_relation_scopes'][0]
    b=next(b for b in d['blocks'] if b['block_id']==s['heading_block_id'])
    assert not verified_relation(s,dict(b,text='The FOREIGN Data Type'),'DOCUMENT')


def test_relation_body_reference_does_not_redeclare_datatype_as_operator(tmp_path):
    d=document(tmp_path,'The DOCUMENT Data Type',
        '<p>The engine supports a native DOCUMENT data type.</p>'
        '<p>Normally, you search DOCUMENT values using the LIKE operator.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='body_reference')
    fs=run(tmp_path,d,p)
    f=next(r for r in fs if r['name']=='DOCUMENT' and r['feature_type']=='datatype')
    assert any('LIKE operator' in c['value'] for c in f['semantics']['description'])
    decisions=[json.loads(l) for l in (tmp_path/'run/type/type_decisions.jsonl').open(encoding='utf-8')]
    decision=next(r for r in decisions if r['identity_unit_id']==f['provenance']['identity_unit_id'])
    assert decision['status']=='CONFIRMED'


def test_catalog_definition_and_same_name_function_do_not_cross_subjects(tmp_path):
    docs=[]
    bodies=[('Spatial Data Types','<p>These data types store geometry values.</p><ul>'
             '<li><code>SHAPE</code></li><li><code>DOT</code></li></ul>'),
            ('Dot Class','<p>A Dot is a geometry representing a location.</p>'),
            ('Functions','<h2>DOT(x)</h2><p>Returns a calculated value.</p>')]
    for i,(title,body) in enumerate(bodies):
        folder=tmp_path/str(i);folder.mkdir();d=document(folder,title,body)
        d=at_url(d,'https://official.test/'+str(i))
        docs.append(d)
    p=build_cold_start_profile(dbms='sample',documents=docs,run_id='same_name')
    run_generic_downstream(dbms='sample',documents=docs,profile=p,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    dot=[f for f in fs if f['name']=='DOT']
    assert {f['feature_type'] for f in dot}=={'function','datatype'}
    datatype=next(f for f in dot if f['feature_type']=='datatype')
    function=next(f for f in dot if f['feature_type']=='function')
    assert all('calculated' not in c['value'] for c in datatype['semantics']['description'])
    assert all('geometry' not in c['value'] for c in function['semantics']['description'])
    assert any('location' in c['value'] for c in datatype['semantics']['description'])

