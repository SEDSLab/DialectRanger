import copy
import pytest

from agents.owner.structure_execution import compile_structure_plan, execute_structures
from agents.owner.profile_execution import declarations, applies
from agents.contracts.definition_entry import callable_signature
from agents.identity.production import normalize_identity
from tests.test_profile_definition_execution import document
from tests.test_five_structure_executors import profile_for


def row_plan(identifier, header, when=None):
    return {'structure':'D','plan_id':identifier,'identity_slot':'VERIFIED_IDENTITY_CELL','boundary':'CURRENT_ROW',
        'execution':{'version':1,'identity':{'alternatives':[{'kind':'COLUMN_HEADER','headers':[header]}],
                                          'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Description']}}],
        'when':when or {'table_headers_all':[header,'Description']}}}


def test_same_executor_has_distinct_scoped_plans_without_native_nomination(tmp_path):
    d=document(tmp_path,'Reference','<table><tr><th>Routine</th><th>Description</th></tr>'
        '<tr><td>namespace.spin(x)</td><td>A rotation.</td></tr></table>'
        '<table><tr><th>SQL data type</th><th>Description</th></tr>'
        '<tr><td>clock(n) with time zone</td><td>A temporal declaration.</td></tr></table>')
    p=profile_for(d,'D');p['hint_contract']['definition_structure_patterns']=[row_plan('routines','Routine'),row_plan('types','SQL data type')]
    original=copy.deepcopy(d);saved=copy.deepcopy(p)
    result=execute_structures(d,p,native_executor=lambda *a:[])
    assert {n for r in result['proofs'] for n in r['identity_values']}=={'NAMESPACE.SPIN','CLOCK WITH TIME ZONE'}
    assert {r['structure_execution']['plan']['plan_id'] for r in result['proofs']}=={'routines','types'}
    assert all(r['structure_execution']['status']=='VERIFIED_STRUCTURE' for r in result['proofs'])
    assert d==original and p==saved


def test_overlapping_plans_do_not_silently_choose_one(tmp_path):
    d=document(tmp_path,'Reference','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>spin(x)</td><td>A rotation.</td></tr></table>')
    p=profile_for(d,'D');p['hint_contract']['definition_structure_patterns']=[row_plan('one','Function'),row_plan('two','Function')]
    result=execute_structures(d,p,native_executor=lambda *a:[])
    assert not result['proofs']
    assert any(r.get('reason')=='OVERLAPPING_EXPLICIT_PLANS' for r in result['diagnostics'])
    assert result['controlled_block_ids']


@pytest.mark.parametrize('name', ['unrecognised(x)', 'SELECT spin(x)', '123'])
def test_missing_location_and_parse_failure_are_different(tmp_path,name):
    d=document(tmp_path,'Reference','<table><tr><th>Function</th><th>Description</th></tr>'
        '<tr><td>'+name+'</td><td>Body.</td></tr></table>')
    p=profile_for(d,'D');p['hint_contract']['definition_structure_patterns']=[row_plan('missing','Absent',{'table_headers_any':['Function']})]
    result=execute_structures(d,p,native_executor=lambda *a:[])
    assert any(r.get('reason')=='IDENTITY_LOCATION_NO_MATCH' for r in result['diagnostics'])
    if name!='unrecognised(x)':
        p['hint_contract']['definition_structure_patterns']=[row_plan('present','Function')]
        result=execute_structures(d,p,native_executor=lambda *a:[])
        assert any(r.get('reason')=='DECLARATION_PARSE_FAILED' for r in result['diagnostics'])


def test_heading_plan_directly_builds_bounded_proof(tmp_path):
    d=document(tmp_path,'Reference','<h2>namespace.spin(x)</h2><p>A rotation.</p>'
        '<h3>Examples</h3><pre>SELECT namespace.spin(1);</pre>'
        '<h2>other(x)</h2><p>Foreign subject.</p>')
    p=profile_for(d,'B');p['hint_contract']['definition_structure_patterns'][0].update(plan_id='headings',execution={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'}],'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]})
    result=execute_structures(d,p,native_executor=lambda *a:[])
    proof=next(p for p in result['proofs'] if p['identity_values']==['NAMESPACE.SPIN'])
    text=' '.join(b['text'] for b in d['blocks'] if b['block_id'] in proof['block_ids'])
    assert 'A rotation.' in text and 'Foreign subject.' not in text


def test_page_subject_plan_without_native_nomination(tmp_path):
    d=document(tmp_path,'namespace.spin(x)','<p>A rotation.</p><h2>Examples</h2><pre>SELECT namespace.spin(1);</pre>')
    p=profile_for(d,'A');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'}],'cardinality':'ONE'},'fields':[]}
    result=execute_structures(d,p,native_executor=lambda *a:[])
    assert len(result['proofs'])==1
    assert result['proofs'][0]['identity_values']==['NAMESPACE.SPIN']


def test_nested_foreign_subject_does_not_get_absorbed(tmp_path):
    d=document(tmp_path,'Reference','<h2>spin(x)</h2><p>A rotation.</p><h3>other(x)</h3><p>Other.</p>')
    p=profile_for(d,'B');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'}],'cardinality':'ONE_OR_MORE'},'fields':[]}
    result=execute_structures(d,p,native_executor=lambda *a:[])
    assert not any(r['identity_values']==['SPIN'] for r in result['proofs'])
    assert any(r.get('reason')=='AMBIGUOUS_NESTED_SUBJECT_BOUNDARY' for r in result['diagnostics'])


def test_named_foreign_example_heading_rejected_without_native_nomination(tmp_path):
    d=document(tmp_path,'Reference','<h2>spin(x)</h2><p>A rotation.</p>'
        '<h3>other Examples</h3><pre>SELECT other(1);</pre>')
    p=profile_for(d,'B');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'}],'cardinality':'ONE_OR_MORE'},'fields':[]}
    result=execute_structures(d,p,native_executor=lambda *a:[])
    assert not any(r['identity_values']==['SPIN'] for r in result['proofs'])
    assert any(r.get('reason')=='AMBIGUOUS_NESTED_SUBJECT_BOUNDARY' for r in result['diagnostics'])


@pytest.mark.parametrize('value,expected',[('space.fn(x)','SPACE.FN'),('"MiXeD"(x)','"MiXeD"'),
    ('space."MiXeD"(x)','SPACE."MiXeD"'),('{fn IFEMPTY(x,y)}','IFEMPTY')])
def test_shared_callable_parser_retains_qualification_and_quoted_case(value,expected):
    assert callable_signature(value)[0]==expected
    assert normalize_identity(value)==expected


@pytest.mark.parametrize('value',["space.fn('literal')",'space.fn(123)','SELECT space.fn(x)','space.fn(x);','space.fn(x) returns a value'])
def test_literals_statements_and_prose_are_not_declarations(value):
    assert callable_signature(value) is None


@pytest.mark.parametrize('value',['TIME(n) WITH TIME ZONE','interval day to second','inet4'])
def test_explicit_type_slot_does_not_require_type_word_in_heading(value):
    block={'block_kind':'TABLE_ROW','heading_ancestry':['Temporal'],
           'table_metadata':{'headers':['SQL data type','Description']}}
    parsed=declarations(value,block,'SQL data type')
    assert len(parsed)==1 and parsed[0]['feature_type']=='DATATYPE'


@pytest.mark.parametrize('heading,headers',[('Parameters',['Parameter','Type','Description']),
    ('Driver API',['SQL Name','Description']),('Connector Mapping',['SQL Name','Description']),
    ('Functions',['Type','Description']),('Query results',['SQL Name','Description'])])
def test_negative_definition_contexts_are_not_released_by_a_column(heading,headers):
    assert declarations('WHOLE',{'block_kind':'TABLE_ROW','heading_ancestry':[heading],
        'table_metadata':{'headers':headers}},headers[-2])==[]


def test_routing_uses_explicit_conditions_not_observed_pages():
    p=row_plan('scope','Function',{'page_path_prefixes':['/reference'],'heading_context_any':['Routines'],
                                 'table_headers_all':['Function','Description']})
    b={'heading_ancestry':['Reference','Routines'],'table_metadata':{'headers':['Function','Description']}}
    assert applies(p,b,{'url':'https://example.test/reference/functions'})
    assert not applies(p,b,{'url':'https://example.test/reference-other/functions'})
    assert not applies(p,dict(b,heading_ancestry=['Examples']),{'url':'https://example.test/reference'})


def test_duplicate_legacy_plans_remain_invalid_and_new_ids_are_required():
    p=row_plan('one','Function');q=copy.deepcopy(p);q.pop('plan_id')
    with pytest.raises(ValueError):compile_structure_plan({'hint_contract':{'definition_structure_patterns':[p,q]}})
    q=copy.deepcopy(p)
    with pytest.raises(ValueError):compile_structure_plan({'hint_contract':{'definition_structure_patterns':[p,q]}})
