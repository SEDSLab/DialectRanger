import copy
import json
import pytest
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.profile_bootstrap.cold_start import build_cold_start_profile,validate_cold_start_profile
from agents.owner.definition_patterns import execute_patterns,parse_surface,validate_patterns
from agents.owner.generic import generate_owner_hypotheses
from agents.pipeline.generic_unified import run_generic_downstream


def document(tmp_path,title,body):
    p=tmp_path/'reference.html'
    p.write_text('<main><h1>'+title+'</h1>'+body+'</main>',encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':'https://official.test/reference','html_path':str(p)},{}),dbms='sample',html_path=str(p))
    d['document_id']=d['page_id']
    return d


def run(tmp_path,d,profile=None):
    profile=profile or build_cold_start_profile(dbms='sample',documents=[d],run_id='pattern_test')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    return json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))


def test_datatype_optional_declaration_complete_e2e(tmp_path):
    d=document(tmp_path,'Numeric Data Types','<ul><li><p><code>WHOLE[(M)] [UNSIGNED]</code></p>'
        '<p>A whole number declaration.</p><p>Null values are permitted.</p><pre>CREATE TABLE t(x WHOLE);</pre></li>'
        '<li><p><code>FRACTION(M,D)</code></p><p>A fractional declaration.</p></li></ul>')
    result=run(tmp_path,d)
    r=next(r for r in result if r['name']=='WHOLE' and r['feature_type']=='datatype')
    assert [c['value'] for c in r['semantics']['syntax']]==['WHOLE[(M)] [UNSIGNED]']
    assert [c['value'] for c in r['semantics']['description']]==['A whole number declaration.','Null values are permitted.']
    assert 'fractional' not in json.dumps(r['semantics'])


def test_multi_name_operator_and_composite_e2e(tmp_path):
    d=document(tmp_path,'Logical Operators','<ul><li><p><code>JOINALL</code>, <code>&amp;&amp;</code></p>'
        '<p>Returns a conjunction.</p><pre>SELECT 1 &amp;&amp; 1;</pre></li>'
        '<li><p><code>expr WITHIN low THROUGH high</code></p><p>Tests a bounded range.</p></li></ul>')
    result=run(tmp_path,d)
    for name in ['JOINALL','&&','WITHIN ... THROUGH ...']:
        r=next(r for r in result if r['name']==name)
        assert r['feature_type']=='operator' and r['semantics']['description']
        if name=='WITHIN ... THROUGH ...':
            assert [c['value'] for c in r['semantics']['syntax']]==['expr WITHIN low THROUGH high']
        else:assert not r['semantics']['syntax']


def test_profile_plan_actually_controls_new_owners(tmp_path):
    d=document(tmp_path,'Data Types','<ul><li><p><code>WHOLE[(M)]</code></p><p>A numerical declaration.</p></li></ul>')
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='plan')
    assert profile['hint_contract']['definition_patterns']
    assert execute_patterns(d,profile)
    owners=generate_owner_hypotheses([d],profile)
    assert any(p.get('definition_pattern')=='PROFILE_LOCAL_DEFINITION' for o in owners for p in o.get('definition_section_proofs',[]))
    disabled=copy.deepcopy(profile);disabled['hint_contract']['definition_patterns']=[]
    assert not execute_patterns(d,disabled)
    assert not any(p.get('definition_pattern')=='PROFILE_LOCAL_DEFINITION' for o in generate_owner_hypotheses([d],disabled) for p in o.get('definition_section_proofs',[]))


@pytest.mark.parametrize('title,body',[
    ('Datatype Examples','<p><code>WHOLE[(M)]</code></p><p>A numerical declaration.</p>'),
    ('API Data Types','<p><code>WHOLE[(M)]</code></p><p>A numerical declaration.</p>'),
    ('Data Types','<p>Ordinary prose mentions WHOLE.</p><p>More discussion follows.</p>'),
    ('Type Mapping','<p><code>WHOLE[(M)]</code></p><p>An external mapping.</p>'),
    ('Logical Operators','<p>a BLOB</p><p>An ordinary term.</p>'),
    ('Operators','<p><code>SELECT TOKEN(x)</code></p><p>Query output.</p>'),
])
def test_profile_cannot_make_negative_context_a_definition(tmp_path,title,body):
    d=document(tmp_path,title,'<ul><li>'+body+'</li></ul>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='negative')
    assert not execute_patterns(d,p)


def test_invalid_profile_grammar_not_silently_ignored(tmp_path):
    d=document(tmp_path,'Data Types','<p>Reference text.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='invalid')
    p['hint_contract']['definition_patterns']=[{'grammar':'CUSTOM_PYTHON','container':'LIST_ITEM','identity_slot':'LEADING_LITERAL','boundary':'CURRENT_CONTAINER'}]
    with pytest.raises(ValueError):validate_cold_start_profile(p)


def test_co_defined_datatype_names_without_alias_invention(tmp_path):
    d=document(tmp_path,'Data Types','<ul><li><p><code>LOGIC</code>, <code>TRUTH</code></p>'
        '<p>These declarations represent logical values.</p></li></ul>')
    result=run(tmp_path,d)
    for name in ['LOGIC','TRUTH']:
        r=next(r for r in result if r['name']==name and r['feature_type']=='datatype')
        assert r['semantics']['description'] and not r['semantics']['syntax']
        assert not r['semantics']['aliases']


@pytest.mark.parametrize('title,label',[
    ('Cast Functions and Operators','<code>WORD[(p)]</code>'),
    ('Cast Functions and Operators','<code>CHARACTER[(N)] [charset_info]</code>'),
    ('Regular Expression Syntax','<code>[a-dX]</code>'),
    ('Comparison and Ordering of Structured Values','<code>OBJECT</code>'),
])
def test_nested_non_definition_grammar_does_not_inherit_type(tmp_path,title,label):
    d=document(tmp_path,'Data Types and Operators','<h2>'+title+'</h2><ul><li><p>'+label+'</p><p>Values are compared with one another.</p></li></ul>')
    assert not execute_patterns(d)


def test_alternative_operator_forms_keep_own_syntax(tmp_path):
    d=document(tmp_path,'Comparison Functions and Operators','<ul><li><p><code>expr SAME pat</code>, <code>expr EQUALS pat</code></p>'
        '<p>Returns a comparison result.</p></li></ul>')
    rs=run(tmp_path,d)
    for name in ['SAME','EQUALS']:
        r=next(r for r in rs if r['name']==name)
        assert [c['value'] for c in r['semantics']['syntax']]==['expr '+name+' pat']
