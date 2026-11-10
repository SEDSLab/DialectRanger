import json
import pytest

from agents.owner.surface_patterns import formal_signatures
from agents.owner.definition_scope import definition_scopes
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.pipeline.generic_unified import run_generic_downstream
from agents.owner.surface_patterns import native_definition_coverage
from agents.contracts.publication_integrity import subject_witnesses
from agents.semantic_stage.pipeline import build_semantic_prompt


def document(tmp_path,heading,body):
    path=tmp_path/'surface.html'
    path.write_text('<main><h1>'+heading+'</h1>'+body+'</main>',encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':'https://official.test/reference','html_path':str(path)},{}),
                   dbms='sample',html_path=str(path))
    d['document_id']=d['page_id']
    return d


def run(tmp_path,d):
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='definition_surfaces')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    return json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('value',[
    'ALPHA(x) trailing prose','SELECT ALPHA(x)','ALPHA(x) [broken','ALPHA(x))',
    'ALPHA(x); SELECT OTHER(x)','ALPHA(x) [optional))','ALPHA(x),'])
def test_partial_or_executable_signature_rejected(value):
    assert formal_signatures(value)==[]


def test_balanced_optional_suffix_and_nested_arguments():
    value="ALPHA([DISTINCT] expr) [optional [nested]]"
    assert formal_signatures(value)==[('ALPHA',value)]
    assert formal_signatures("ALPHA(x, nested(y)), ALPHA(z) [suffix]")==[
        ('ALPHA','ALPHA(x, nested(y))'),('ALPHA','ALPHA(z) [suffix]')]


def test_suffix_definition_full_e2e(tmp_path):
    signature='ALPHA([DISTINCT] expr) [optional_clause]'
    d=document(tmp_path,'Aggregate Functions','<ul><li><p><code>'+signature+'</code></p>'
        '<p>Returns an accumulated value.</p><p>Returns NULL for an empty input.</p>'
        '<pre>SELECT ALPHA(value) FROM items;</pre></li>'
        '<li><p>BETA(x)</p><p>Returns a different value.</p></li></ul>')
    a=next(r for r in run(tmp_path,d) if r['name']=='ALPHA')
    assert [c['value'] for c in a['semantics']['syntax']]==[signature]
    assert len(a['semantics']['description'])==2 and a['semantics']['examples']
    assert not any('BETA' in c['value'] for field in a['semantics'].values() if isinstance(field,list) for c in field if 'value' in c)


def test_symbol_and_keyword_operator_definition_e2e(tmp_path):
    d=document(tmp_path,'Arithmetic Operators','<ul>'
        '<li><p><code>+</code></p><p>Addition:</p><pre>SELECT 2+3;</pre></li>'
        '<li><p><code>QUOT</code></p><p>Integer quotient. Discards a fractional part.</p><pre>SELECT 5 QUOT 2;</pre></li>'
        '<li><p><code>a %% b</code></p><p>Returns a remainder.</p><pre>SELECT 5 %% 2;</pre></li></ul>')
    fs=run(tmp_path,d)
    for name in ['+','QUOT','%%']:
        r=next(r for r in fs if r['name']==name)
        assert r['feature_type']=='operator'
        assert r['semantics']['description'] and r['semantics']['examples']
        if name in ['+','QUOT']:assert not r['semantics']['syntax']
        else:assert [c['value'] for c in r['semantics']['syntax']]==['a %% b']


def test_datatype_paragraph_is_local_not_shared_chapter(tmp_path):
    d=document(tmp_path,'The MOMENT and DAYVALUE Types',
        '<p>The MOMENT type is used for a date and time value.</p>'
        '<p>The DAYVALUE type is used for a calendar date.</p>'
        '<p>These types share conversion behavior.</p>')
    proofs=[p for p in definition_scopes(d) if p.get('definition_pattern')=='DATATYPE_SELF_DEFINITION']
    assert {p['primary_identity'] for p in proofs}=={'MOMENT','DAYVALUE'}
    assert all(len(p['block_ids'])==1 for p in proofs)
    fs=run(tmp_path,d)
    for name in ['MOMENT','DAYVALUE']:
        r=next(r for r in fs if r['name']==name)
        assert r['feature_type']=='datatype'
        assert len(r['semantics']['description'])==1
        assert not r['semantics']['syntax'] and not r['semantics']['examples']
        assert 'share conversion' not in r['semantics']['description'][0]['value']


@pytest.mark.parametrize('heading,body',[
    ('Operator Examples','<ul><li><p>+</p><p>Addition:</p><pre>SELECT 2+3;</pre></li></ul>'),
    ('API Operators','<ul><li><p>METHOD</p><p>Returns a value.</p><pre>SELECT METHOD;</pre></li></ul>'),
    ('Arithmetic Operators','<ul><li><p>SELECT</p><p>Returns a value.</p><pre>SELECT x;</pre></li></ul>'),
    ('Function and Operator Reference','<ul><li><p>BARENAME</p><p>Returns a value.</p><pre>SELECT BARENAME;</pre></li></ul>'),
    ('External Type Mapping','<p>The FOREIGN type is used for mapped values.</p>'),
    ('Data Types','<p>The ABSENT type is not supported.</p>'),
    ('Data Types','<p>For example, the SOMETHING type is used here.</p>')])
def test_non_definition_context_does_not_create_new_proofs(tmp_path,heading,body):
    d=document(tmp_path,heading,body)
    assert not any(p.get('definition_pattern') in {'OPERATOR_LED_LIST_ITEM','DATATYPE_SELF_DEFINITION'}
                   for p in definition_scopes(d))


def test_recovery_coverage_is_exact_scope_not_name_only():
    proof={'section_id':'s','feature_type':'OPERATOR','identity_values':['%%'],
           'block_ids':['anchor','body']}
    owner={'owner_id':'o','page_id':'p','definition_section_proofs':[proof]}
    unit={'identity_unit_id':'u','canonical_identity':'%%','owner_ids':['o'],
          'occurrence_records':[{'definition_section_id':'s','owner_local_binding':True}]}
    coverage=native_definition_coverage([unit],[owner])
    assert coverage[('p','body','%%','OPERATOR')]=='u'
    assert ('another_page','body','%%','OPERATOR') not in coverage
    assert ('p','unrelated_example','%%','OPERATOR') not in coverage
    assert ('p','body','%','OPERATOR') not in coverage
    unit['occurrence_records']=[]
    assert native_definition_coverage([unit],[owner])=={}


def test_publication_rechecks_actual_proof_subject():
    unit={'identity_unit_id':'u','canonical_identity':'MOMENT','feature_type':'DATATYPE','owner_ids':['o']}
    block={'block_id':'b','text':'The OTHER type is used for a value.',
           'owner_ids':['o'],'block_kind':'TEXT','heading_ancestry':['Data Types'],
           'definition_section_proofs':[{'definition_pattern':'DATATYPE_SELF_DEFINITION',
              'heading_block_id':'b','identity_values':['MOMENT'],'primary_identity':'MOMENT',
              'identity_anchor_block_ids':{'MOMENT':['b']},'definition_anchor_block_ids':['b']}]}
    witnesses,conflicts=subject_witnesses(unit,{'source_blocks':[block]})
    assert not witnesses
    assert any(c['reason']=='FOREIGN_DEFINITION_SUBJECT' for c in conflicts)


def test_symbol_in_mixed_reference_keeps_local_type(tmp_path):
    d=document(tmp_path,'Functions and Operators',
        '<ul><li><p>%%</p><p>Returns a remainder.</p><pre>SELECT 5 %% 2;</pre></li></ul>')
    fs=run(tmp_path,d)
    r=next(r for r in fs if r['name']=='%%')
    assert r['feature_type']=='operator' and r['semantics']['description'] and r['semantics']['examples']


def test_proven_overload_body_is_not_cut_by_main_evidence_budget():
    filler=[{'block_id':'catalog:'+str(i),'text':'Catalog reference '+str(i),'block_kind':'TEXT'} for i in range(81)]
    def block(bid,name):
        return {'block_id':bid,'text':'Returns another overload result.','block_kind':'TEXT',
            'definition_section_proofs':[{'scope_status':'COMPLETE','identity_unit_id':'u',
                'identity_values':[name],'block_ids':[bid]}]}
    bundle={'identity_unit_id':'u','normalized_identity':'ALPHA','main_evidence':filler,
            'source_blocks':filler+[block('required','ALPHA'),block('foreign','BETA')]}
    prompt=build_semantic_prompt(bundle,{'identity_group_id':'u','normalized_identity':'ALPHA'})
    payload=json.loads(prompt.split('INPUT:\n')[1].split('\n\nOUTPUT SCHEMA:')[0])
    assert 'required' in payload['allowed_evidence_refs']
    assert 'foreign' not in payload['allowed_evidence_refs']
    assert 'catalog:80' not in payload['allowed_evidence_refs']
    assert payload['input_selection']['required_definition_evidence_refs']==['required']
