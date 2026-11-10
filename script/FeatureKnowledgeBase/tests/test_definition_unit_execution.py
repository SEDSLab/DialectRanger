"""Layout-independent Profile execution, without any DBMS/name rules."""
import copy
import json
import pytest

from agents.owner.definition_patterns import execute_patterns
from agents.contracts.definition_unit import build_definition_units
from agents.owner.generic import generate_owner_hypotheses
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from tests.test_profile_definition_execution import document, run


def plan(grammar):
    return {'grammar':grammar,'container':'REPEATED_CONTAINER',
            'identity_slot':'LEADING_LITERAL','boundary':'CURRENT_CONTAINER'}


@pytest.mark.parametrize('tag', ['li','div','section'])
def test_identical_profile_plan_across_container_tags(tmp_path,tag):
    body=''.join('<{0}><p><code>{1}[(p)]</code></p><p>A {2} declaration.</p></{0}>'.format(tag,n,n.lower())
                 for n in ['WHOLE','FRACTION'])
    d=document(tmp_path,'Data Types',body)
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='same-plan')
    p['hint_contract']['definition_patterns']=[plan('DATATYPE_DECLARATION')]
    proofs=execute_patterns(d,p)
    assert [v['identity_values'] for v in proofs]==[['WHOLE'],['FRACTION']]
    units=build_definition_units([d],generate_owner_hypotheses([d],p),p)
    for proof in proofs:
        assert len(proof['block_ids'])==2
        assert all(next(b for b in d['blocks'] if b['block_id']==bid)['dom_path'].startswith(proof['dom_parent']+' > ')
                   for bid in proof['block_ids'])
    assert not any(u['status']=='INVALID' for u in units)
    rs=run(tmp_path,d,p)
    for n in ['WHOLE','FRACTION']:
        r=next(r for r in rs if r['name']==n and r['feature_type']=='datatype')
        assert [c['value'] for c in r['semantics']['description']]==['A '+n.lower()+' declaration.']


def test_bootstrap_observes_div_function_definitions_and_end_to_end(tmp_path):
    d=document(tmp_path,'Functions','<div><p><code>TURN(x)</code></p><p>Returns a rotation.</p>'
        '<pre>SELECT TURN(1);</pre></div><div><p><code>SHIFT(x)</code></p><p>Returns a displacement.</p></div>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='auto-div')
    assert plan('CALLABLE_DEFINITION') in p['hint_contract']['definition_patterns']
    rs=run(tmp_path,d,p)
    r=next(r for r in rs if r['name']=='TURN' and r['feature_type']=='function')
    assert [c['value'] for c in r['semantics']['syntax']]==['TURN(x)']
    assert [c['value'] for c in r['semantics']['description']]==['Returns a rotation.']
    assert r['semantics']['examples'] and 'displacement' not in json.dumps(r)
    units=[json.loads(l) for l in (tmp_path/'run/owners/definition_units.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(u['identity_values']==['TURN'] and u['execution_plan']==plan('CALLABLE_DEFINITION') for u in units)


def test_parameterized_parallel_declarations_keep_separate_syntax(tmp_path):
    d=document(tmp_path,'Data Types','<ul><li><p><code>WHOLE[(p,s)]</code>, <code>EXACT[(p,s)]</code></p>'
        '<p>These declarations store exact values.</p></li></ul>')
    rs=run(tmp_path,d)
    for n in ['WHOLE','EXACT']:
        r=next(r for r in rs if r['name']==n and r['feature_type']=='datatype')
        assert [c['value'] for c in r['semantics']['syntax']]==[n+'[(p,s)]']
        assert not r['semantics']['aliases']


@pytest.mark.parametrize('heading,body',[
    ('API Functions','<p><code>TURN(x)</code></p><p>Returns an API object.</p>'),
    ('Function Examples','<p><code>TURN(x)</code></p><p>Returns an example.</p>'),
    ('Functions','<p>Text mentions TURN(x).</p><p>More ordinary text.</p>'),
    ('Functions','<pre>SELECT TURN(1);</pre><p>A sample query.</p>'),
])
def test_repeated_containers_do_not_bypass_definition_validation(tmp_path,heading,body):
    d=document(tmp_path,heading,'<div>'+body+'</div><div>'+body+'</div>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='negative')
    p['hint_contract']['definition_patterns']=[plan('CALLABLE_DEFINITION')]
    assert not execute_patterns(d,p)


def test_no_plan_means_no_new_container_definition_and_profile_is_immutable(tmp_path):
    d=document(tmp_path,'Data Types','<div><p><code>WHOLE[(p)]</code></p><p>A number.</p></div>'
        '<div><p><code>EXACT[(p)]</code></p><p>An exact number.</p></div>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='disabled')
    p['hint_contract']['definition_patterns']=[]
    before=copy.deepcopy(p)
    assert execute_patterns(d,p)==[] and p==before


def test_definition_unit_reports_invalid_source_instead_of_publication_permission(tmp_path):
    d=document(tmp_path,'Functions','<p>Ordinary text.</p>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='invalid-proof')
    owner={'owner_id':'o','page_id':d['page_id'],'definition_section_proofs':[
        {'section_id':'s','identity_values':['TURN'],'definition_anchor_block_ids':['absent'],'block_ids':['absent']}]}
    unit=build_definition_units([d],[owner],p)[0]
    assert unit['status']=='INVALID' and 'SOURCE_BLOCK_MISSING' in unit['reason_codes']
    assert 'published' not in unit
