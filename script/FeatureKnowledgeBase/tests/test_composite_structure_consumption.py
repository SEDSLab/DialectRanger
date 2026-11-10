"""Structure composition must survive the real Type -> Evidence -> Final path."""
import json
import pytest
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.owner.structure_execution import execute_structures
from tests.test_profile_definition_execution import document, run
from tests.test_five_structure_executors import profile_for


def table(name, value='1'):
    return ('<table><tr><td><strong>Description</strong></td><td>Returns a rotation.</td></tr>'
        '<tr><td>Example</td><td><code>'+name+'('+value+')</code></td></tr>'
        '<tr><td>Result</td><td><code>'+value+'</code></td></tr></table>')


def test_heading_stack_uses_levels_not_array_positions(tmp_path):
    p=tmp_path/'raw.html'
    p.write_bytes(b'<main><h2>Operators</h2><p>Operator prose.</p><h2>Functions</h2>'
                 b'<h4>TURN(x)</h4><p>First.</p><h4>SHIFT(x)</h4><p>Second.</p></main>')
    d=from_v2_page(build_page_topology({'url':'https://official.test/a','html_path':str(p)},{}),dbms='sample',html_path=p)
    bytext={b['text']:b for b in d['blocks']}
    assert bytext['Functions']['heading_ancestry']==[]
    assert bytext['TURN(x)']['heading_ancestry']==['Functions']
    assert bytext['Second.']['heading_ancestry']==['Functions','SHIFT(x)']


@pytest.mark.parametrize('heading_level',[2,4])
def test_definition_section_nested_fields_reaches_final(tmp_path,heading_level):
    h=str(heading_level)
    d=document(tmp_path,'Functions','<h'+h+'>TURN(x)</h'+h+'>'+table('TURN')+
        '<h'+h+'>SHIFT(x)</h'+h+'>'+table('SHIFT','2'))
    profile=profile_for(d,'B')
    proof=next(p for p in execute_structures(d,profile)['proofs'] if p['identity_values']==['TURN'])
    assert proof['structure_execution']['compositions'][0]['child_structure']=='FIELD_TABLE'
    own=set(proof['block_ids'])
    assert not any(b['block_id'] in own and 'SHIFT' in b['text'] for b in d['blocks'])
    records=run(tmp_path,d,profile)
    r=next(r for r in records if r['name']=='TURN' and r['feature_type']=='function')
    sem=r['semantics']
    assert 'Returns a rotation.' in [c['value'] for c in sem['description']]
    assert 'TURN(x)' in [c['value'] for c in sem['syntax']]
    assert any('TURN(1)' in c['value'] and 'Result 1' in c['value'] for c in sem['examples'])
    assert 'SHIFT' not in json.dumps(sem)
    for claim in sem['examples']:
        assert claim['value'].replace('\n',' ') in claim['evidence']['evidence_span']['text']


def test_field_labels_do_not_declare_function_subjects(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'+table('TURN'))
    records=run(tmp_path,d,profile_for(d,'BD'))
    assert not {'DESCRIPTION','EXAMPLE','RESULT'} & {r['name'] for r in records}


def test_field_table_cannot_cross_parent_to_find_result(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'
        '<table><tr><td>Description</td><td>Returns a rotation.</td></tr>'
        '<tr><td>Example</td><td>TURN(1)</td></tr></table>'
        '<h2>SHIFT(x)</h2>'+table('SHIFT','999'))
    records=run(tmp_path,d,profile_for(d,'B'))
    r=next(r for r in records if r['name']=='TURN')
    assert any(c['value']=='TURN(1)' for c in r['semantics']['examples'])
    # Provenance hashes may contain these digits by chance; assert field
    # values, not serialized identifiers, to detect cross-subject leakage.
    assert all('999' not in str(c.get('value','')) for values in r['semantics'].values()
               if isinstance(values,list) for c in values if isinstance(c,dict))


def test_profile_disables_composite_section_execution(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'+table('TURN'))
    assert not execute_structures(d,profile_for(d,[]))['proofs']


def test_unbound_copied_field_role_is_not_trusted():
    from agents.owner.composite_definition import verified_fields
    assert verified_fields({'binding_status':'INCOMPLETE','identity_values':['TURN'],
        'structure_execution':{'status':'VERIFIED_STRUCTURE','field_members':[
            {'block_id':'foreign','field_role':'EXAMPLE','value':'TURN(1)'}]}},{},'TURN')==[]


def test_custom_profile_field_table_roles_reach_final(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'+table('TURN').replace('Description','Behavior').replace('Example','Worked queries'))
    profile=profile_for(d,'B')
    profile['hint_contract']['definition_structure_patterns'][0]['field_labels']={
        'DESCRIPTION':['Behavior'],'EXAMPLE':['Worked queries']}
    records=run(tmp_path,d,profile)
    r=next(r for r in records if r['name']=='TURN')
    assert r['semantics']['description'] and r['semantics']['examples']


def test_copied_field_role_cannot_override_observed_cell_label(tmp_path):
    import copy
    from agents.owner.composite_definition import verified_fields
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'+table('TURN'))
    run(tmp_path,d,profile_for(d,'B'))
    b=json.loads(next((tmp_path/'run/semantic_stage/evidence/bundles.jsonl').open(encoding='utf8')))
    u=copy.deepcopy(b['definition_units'][0]);evidence={b['block_id']:b for b in b['source_blocks']}
    assert verified_fields(u,evidence,'TURN')
    u['structure_execution']['field_members'][0]['field_role']='FORMAL_SYNTAX'
    assert not any(m['field_role']=='FORMAL_SYNTAX' for m in verified_fields(u,evidence,'TURN'))


def test_api_heading_is_not_promoted_by_field_table(tmp_path):
    d=document(tmp_path,'SDK API Functions','<h2>TURN(x)</h2>'+table('TURN'))
    assert not any(p.get('definition_pattern')=='COMPOSITE_FIELD_DEFINITION'
        for p in execute_structures(d,profile_for(d,'B'))['proofs'])


@pytest.mark.parametrize('kind,title',[('function','TURN function'),('datatype','TURN data type')])
def test_subject_page_field_table_uses_same_contract(tmp_path,kind,title):
    body=table('TURN').replace('<table>','<table><tr><td>Syntax</td><td>TURN(x)</td></tr>',1)
    d=document(tmp_path,title,body)
    rs=run(tmp_path,d,profile_for(d,'A'))
    r=next(r for r in rs if r['name']=='TURN' and r['feature_type']==kind)
    assert r['semantics']['description'] and r['semantics']['syntax'] and r['semantics']['examples']
    assert all(c['value']!=title for c in r['semantics']['syntax'])


@pytest.mark.parametrize('tag',['li','div','section'])
def test_profile_entry_can_contain_field_table_as_body(tmp_path,tag):
    from tests.test_definition_unit_execution import plan
    body=''.join('<{0}><p><code>{1}(x)</code></p>{2}</{0}>'.format(tag,n,table(n)) for n in ['TURN','SHIFT'])
    d=document(tmp_path,'Functions',body)
    profile=profile_for(d,'C');profile['hint_contract']['definition_patterns']=[plan('CALLABLE_DEFINITION')]
    rs=run(tmp_path,d,profile)
    r=next(r for r in rs if r['name']=='TURN')
    assert r['semantics']['description'] and r['semantics']['examples']
    assert 'SHIFT' not in json.dumps(r['semantics'])


def test_catalog_definition_cell_can_contain_field_table(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Function Description</th></tr><tr><td>'
        '<p class="func_signature"><code class="function">TURN</code>(x) → integer</p>'+table('TURN')+'</td></tr></table>')
    rs=run(tmp_path,d,profile_for(d,'D'))
    r=next(r for r in rs if r['name']=='TURN')
    assert r['semantics']['description'] and r['semantics']['examples']


def test_shared_member_fields_are_not_individual_definitions(tmp_path):
    d=document(tmp_path,'Data Types','<p>The following data types are supported.</p><ul>'
        '<li><code>WHOLE</code></li><li><code>EXACT</code></li></ul>'+table('WHOLE'))
    rs=run(tmp_path,d,profile_for(d,'E'))
    assert rs
    assert all(not r['semantics']['description'] for r in rs)


def test_canonical_consumes_bound_owner_without_default_recipe_rediscovery(tmp_path,monkeypatch):
    import agents.pipeline.generic_unified as g
    def forbidden(*a,**kw):
        raise AssertionError('Canonical must not recompile default Owner recipes')
    monkeypatch.setattr(g,'project_definition_owner',forbidden)
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'+table('TURN'))
    assert any(r['name']=='TURN' for r in run(tmp_path,d,profile_for(d,'B')))


def test_bound_owner_projection_does_not_expand_unbound_owners():
    from agents.pipeline.generic_unified import _bound_owner_projection
    owners={'own':{'owner_id':'own','page_id':'p','member_block_ids':['anchor']},
            'other':{'owner_id':'other','page_id':'q','member_block_ids':['foreign']}}
    bound={'owner_id':'own','member_block_ids':['anchor','body'],
           'owner_scope_block_ids':['anchor','body'],'definition_section_proofs':[{'proof':'verified upstream'}]}
    result=_bound_owner_projection({'owner_ids':['own']},{'owners':[bound]},owners)
    assert result['own']['member_block_ids']==['anchor','body']
    assert result['other']==owners['other']
    assert owners['own']['member_block_ids']==['anchor']


def test_function_metadata_columns_do_not_make_identity_a_configuration(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN(x)</h2>'+table('TURN')+
        '<h2>Function Catalog</h2><table><tr><th>function_name</th><th>return_type</th><th>parameters</th></tr>'
        '<tr><td>TURN</td><td>INTEGER</td><td>[x]</td></tr></table>')
    rs=run(tmp_path,d,profile_for(d,'BD'))
    assert any(r['name']=='TURN' and r['feature_type']=='function' for r in rs)


def test_configuration_identity_column_retains_negative_evidence():
    from agents.type.evidence import build_typed_feature_evidence
    votes=build_typed_feature_evidence({'canonical_subject':'TURN','owner_id':'owner','owner_kind':'TableRowOwner',
        'heading_ancestry':['Configuration'],'blocks':[{'block_id':'row','block_type':'table_row','text':'TURN value',
        'cells':['TURN','value'],'table_headers':['Parameter','Description'],'heading_ancestry':['Configuration']}]})
    assert any(v['candidate_type']=='NON_TARGET' and v['non_target_category']=='CONFIGURATION' for v in votes)


def test_explicit_datatype_self_definition_has_page_subject_corroboration(tmp_path):
    p=tmp_path/'typed.html'
    p.write_bytes(b'<html><head><title>WHOLE - Official Reference</title></head><body><main>'
        b'<h2>Extra Dimensions</h2><p>The WHOLE type is used for exact values.</p></main></body></html>')
    d=from_v2_page(build_page_topology({'url':'https://official.test/reference','html_path':str(p)},{}),dbms='sample',html_path=p)
    d['document_id']=d['page_id']
    rs=run(tmp_path,d,profile_for(d,'AC'))
    r=next(r for r in rs if r['name']=='WHOLE' and r['feature_type']=='datatype')
    assert any('exact values' in c['value'] for c in r['semantics']['description'])


def test_incidental_type_sentence_without_page_or_typed_region_is_not_definition(tmp_path):
    d=document(tmp_path,'Other Concepts','<h2>Dimensions</h2><p>The WHOLE type is used for exact values.</p>')
    from agents.owner.definition_scope import datatype_prose_scopes
    assert not datatype_prose_scopes(d)
