"""Definition positions survive grammar limits without bypassing provenance."""
import copy
import json
import pytest
from tests.test_profile_definition_execution import document,run
from tests.test_five_structure_executors import profile_for
from agents.owner.structure_execution import execute_structures
from agents.owner.definition_patterns import verified_pattern


def section_profile(d):
    p=profile_for(d,'B')
    p['hint_contract']['definition_patterns']=[]
    p['hint_contract']['definition_scope_patterns']=[{'relation':'HEADING_SECTION',
        'identity_slot':'HEADING','boundary':'NEXT_PEER_OR_PARENT_HEADING'}]
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'},{'kind':'VERIFIED_ANCHOR'}],
                              'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}},
                  {'role':'EXAMPLE','locator':{'kind':'SECTION_LABEL','labels':['Example','Examples']}},
                  {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    return p


@pytest.mark.parametrize('syntax',[
    'TURN(x) [FILTER (WHERE condition)] [OVER windowSpecification]',
    'TURN() OVER windowSpecification',
    'TURN(x) [UNRECOGNIZED_OPTION argument]',
])
def test_heading_identity_and_literal_syntax_survive_optional_tail(tmp_path,syntax):
    d=document(tmp_path,'Functions','<h2 id="turn">TURN</h2><pre>'+syntax+'</pre>'
        '<p>Returns the same data type as its argument.</p><p>Example:</p><pre>SELECT TURN(2);</pre>'
        '<h2 id="shift">SHIFT</h2><pre>SHIFT(x)</pre><p>Returns a displacement.</p>')
    rows=run(tmp_path,d,section_profile(d));r=next(r for r in rows if r['name']=='TURN')
    assert r['feature_type']=='function'
    assert syntax in [x['value'] for x in r['semantics']['syntax']]
    assert 'Returns the same data type as its argument.' in [x['value'] for x in r['semantics']['description']]
    assert 'displacement' not in json.dumps(r)


def test_multi_member_binding_does_not_require_every_syntax_to_parse(tmp_path):
    d=document(tmp_path,'Data Types','<h2 id="whole">WHOLE</h2>'
        '<pre>WHOLE | EXACT(precisionInt)</pre><p>A fixed precision number.</p>')
    p=section_profile(d);proof=next(v for v in execute_structures(d,p)['proofs'] if 'WHOLE' in v['identity_values'])
    assert 'WHOLE' in proof['identity_values']
    assert any(x['identity']=='EXACT' for x in proof.get('unresolved_identity_bindings',[]))
    rows=run(tmp_path,d,p)
    assert any(r['name']=='WHOLE' and r['feature_type']=='datatype' for r in rows)


def test_multiword_type_proof_is_consumed_by_identity(tmp_path):
    d=document(tmp_path,'Data Types','<h2 id="varying">VARYING TEXT</h2>'
        '<pre>VARYING TEXT [ ( lengthInt ) ]</pre><p>A string with variable length.</p>')
    rows=run(tmp_path,d,section_profile(d))
    assert any(r['name']=='VARYING TEXT' and r['feature_type']=='datatype' for r in rows)


def test_multi_callable_alternatives_are_separate_members(tmp_path):
    d=document(tmp_path,'Functions','<h2 id="turn">TURN</h2>'
        '<pre>{ TURN(x) } | { SPIN(x,y) }</pre><p>Returns a rotation.</p>')
    rows=run(tmp_path,d,section_profile(d))
    assert {'TURN','SPIN'} <= {r['name'] for r in rows}


@pytest.mark.parametrize('mutation',['name','hash','locator','ancestry','plan'])
def test_execution_proof_rechecks_frozen_identity_slot(tmp_path,mutation):
    d=document(tmp_path,'Functions','<h2 id="turn">TURN</h2><pre>TURN(x)</pre><p>Returns a rotation.</p>')
    p=section_profile(d);proof=copy.deepcopy(execute_structures(d,p)['proofs'][0])
    b=copy.deepcopy(next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id']))
    assert verified_pattern(proof,b,'TURN')
    if mutation=='name':b['text']='SHIFT'
    elif mutation=='hash':proof['structure_execution']['source_hash']='0'*64
    elif mutation=='locator':b['dom_path']=b['dom_path']+' > p'
    elif mutation=='ancestry':b['heading_ancestry']=['Data Types','TURN']
    else:proof['structure_execution']['plan']['execution']['identity']['alternatives']=[{'kind':'LEADING_CODE'}]
    assert verified_pattern(proof,b,'TURN') is None


def test_example_only_call_and_navigation_do_not_become_definition(tmp_path):
    d=document(tmp_path,'Guide','<h2>Examples</h2><pre>TURN(7)</pre><p>Try a rotation.</p>'
        '<nav><h2>SHIFT</h2><a href="#shift">SHIFT(x)</a></nav>')
    assert not execute_structures(d,section_profile(d))['proofs']
