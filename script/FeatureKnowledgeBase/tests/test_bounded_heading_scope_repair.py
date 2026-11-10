"""Offline A/B regression fixtures and focused deterministic Final validation."""
import pytest
from tests.test_owner_definition_coverage_phase1 import document
from tests.test_five_structure_executors import profile_for
from agents.owner.structure_execution import execute_structures
from agents.owner.profile_execution import declarations
from agents.owner.declaration_binding import verified_executed


def execute(doc, family):
    profile=profile_for(doc,family)
    profile['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'HEADING'}],'cardinality':'ONE'},
        'fields':[]}
    return execute_structures(doc,profile)['proofs']


@pytest.mark.parametrize('family,level',[('A',1),('B',2)])
def test_wrapped_heading_retains_body_without_navigation(tmp_path,family,level):
    d=document(tmp_path,f'<article><div><h{level}>TURN (Query-Dialect)</h{level}></div>'
        '<nav><h3>In this article</h3><a href="#syntax">Syntax</a></nav>'
        '<p>Returns a rotation.</p><h3>Syntax</h3><pre>TURN(x)</pre>'
        '<h3>Examples</h3><pre>SELECT TURN(1), OTHER(2)</pre>'
        '<aside><p>Sign in</p></aside></article>')
    proofs=execute(d,family)
    p=next(p for p in proofs if p['primary_identity']=='TURN')
    wanted={b['block_id'] for b in d['blocks'] if ' > nav' not in b['dom_path'] and ' > aside' not in b['dom_path']}
    assert set(p['block_ids'])==wanted
    assert p['identity_values']==['TURN']
    anchor=next(b for b in d['blocks'] if b['block_id']==p['heading_block_id'])
    assert verified_executed(p,anchor,'TURN')['feature_type']=='FUNCTION'


def test_display_qualifier_is_not_formal_declaration():
    assert declarations('TURN (Query-Dialect)',{'block_kind':'HEADING'})==[]
    assert declarations('TURN(x)',{'block_kind':'CODE'})[0]['formal_value']=='TURN(x)'


@pytest.mark.parametrize('signature',['TURN (value)','TURN (left, right)'])
def test_plain_spaced_function_heading_preserves_arguments(tmp_path,signature):
    d=document(tmp_path,'<article><h1>Functions</h1><h2>'+signature+
        '</h2><p>Returns a rotation.</p></article>')
    anchor=next(b for b in d['blocks'] if b['text']==signature)
    assert declarations(signature,anchor)==[
        {'identity':'TURN','feature_type':'FUNCTION','formal_value':signature}]
    p=next(p for p in execute(d,'B') if p['primary_identity']=='TURN')
    assert verified_executed(p,anchor,'TURN')['formal_value']==signature


@pytest.mark.parametrize('family',['A','B'])
def test_wrapped_qualifier_title_never_enters_final_syntax(tmp_path,family):
    from tests.test_profile_definition_execution import run
    from tests.test_subject_contract_repair import section_profile
    d=document(tmp_path,'<article><div><h1>TURN (Query-Dialect)</h1></div>'
        '<nav><h2>In this article</h2><a href="#syntax">Syntax</a></nav>'
        '<p>Returns a rotation.</p><h2>Syntax</h2><pre>TURN(value)</pre>'
        '<h2>Examples</h2><pre>SELECT TURN(1);</pre></article>')
    profile=section_profile(d)
    if family=='A':
        page_profile=profile_for(d,'A')
        page_profile['hint_contract']['definition_structure_patterns'][0]['execution']=profile['hint_contract']['definition_structure_patterns'][0]['execution']
        profile=page_profile
    rows=run(tmp_path,d,profile)
    row=next(r for r in rows if r['name']=='TURN')
    assert [v['value'] for v in row['semantics']['syntax']]==['TURN(value)']
    assert 'Returns a rotation.' in [v['value'] for v in row['semantics']['description']]
    assert 'SELECT TURN(1);' in [v['value'] for v in row['semantics']['examples']]


@pytest.mark.parametrize('signature',['TURN (value)','TURN (left, right)'])
def test_plain_spaced_signature_reaches_final(tmp_path,signature):
    from tests.test_profile_definition_execution import run
    from tests.test_subject_contract_repair import section_profile
    d=document(tmp_path,'<article><h1>Functions</h1><div><h2>'+signature+
        '</h2></div><p>Returns a rotation.</p><h3>Examples</h3>'
        '<pre>SELECT TURN(1);</pre></article>')
    row=next(r for r in run(tmp_path,d,section_profile(d)) if r['name']=='TURN')
    assert signature in [v['value'] for v in row['semantics']['syntax']]
    assert 'Returns a rotation.' in [v['value'] for v in row['semantics']['description']]
    assert 'SELECT TURN(1);' in [v['value'] for v in row['semantics']['examples']]


def test_qualifier_and_reference_alone_do_not_define(tmp_path):
    d=document(tmp_path,'<article><div><h1>TURN (Query-Dialect)</h1></div>'
        '<p>See TURN for details.</p><pre>SELECT TURN(1)</pre></article>')
    assert execute(d,'A')==[]


def test_trino_peer_sections_remain_separate(tmp_path):
    d=document(tmp_path,'<article><h1>Mathematical functions</h1>'
        '<div><h2><code>turn(x)</code></h2></div><p>Returns a rotation.</p>'
        '<h3>Examples</h3><pre>SELECT turn(1), shift(2)</pre>'
        '<div><h2><code>shift(x)</code></h2></div><p>Returns a shift.</p></article>')
    ps=execute(d,'B')
    a=next(p for p in ps if p['primary_identity']=='TURN')
    texts=[b['text'] for b in d['blocks'] if b['block_id'] in a['block_ids']]
    assert 'Returns a rotation.' in texts
    assert 'SELECT turn(1), shift(2)' in texts
    assert 'Returns a shift.' not in texts


def test_nested_peer_subject_cannot_lend_or_receive_definition(tmp_path):
    d=document(tmp_path,'<article><div><h1>TURN (Query-Dialect)</h1></div>'
        '<h2>Syntax</h2><pre>TURN(x)</pre><h2>SHIFT</h2>'
        '<h3>Syntax</h3><pre>SHIFT(x)</pre><p>Returns a shift.</p></article>')
    assert not any(p['primary_identity']=='TURN' for p in execute(d,'A'))


def test_content_boundary_does_not_escape_nearest_section(tmp_path):
    d=document(tmp_path,'<article><section><div><h2>TURN (Query-Dialect)</h2></div>'
        '<h3>Syntax</h3><pre>TURN(x)</pre></section>'
        '<p>Unrelated article content.</p></article>')
    p=next(p for p in execute(d,'B') if p['primary_identity']=='TURN')
    assert not any(b['text']=='Unrelated article content.' and b['block_id'] in p['block_ids'] for b in d['blocks'])


@pytest.mark.parametrize('label,identity',[
    ('TIME(P)','TIME'),('TIMESTAMP(P)','TIMESTAMP'),
    ('TIMESTAMP(P) WITH TIME ZONE','TIMESTAMP WITH TIME ZONE'),
    ('MEASURE(P) WITH UNIT','MEASURE WITH UNIT'),
])
def test_code_marked_parameterized_type_retains_nominal_scope(tmp_path,label,identity):
    d=document(tmp_path,'<article><h1>Data types</h1><section><h2><code>'+label+
        '</code><a href="#type">#</a></h2><p>Stores a parameterized value.</p>'
        '<h3>Examples</h3><pre>SELECT CAST(NULL AS '+label+')</pre></section></article>')
    heading=next(b for b in d['blocks'] if b['block_kind']=='HEADING' and label in b['text'])
    parsed=declarations(heading['text'],heading)
    assert parsed==[{'identity':identity,'feature_type':'DATATYPE','formal_value':None}]
    p=next(p for p in execute(d,'B') if p['primary_identity']==identity)
    assert p['feature_type']=='DATATYPE'
    texts=[b['text'] for b in d['blocks'] if b['block_id'] in p['block_ids']]
    assert 'Stores a parameterized value.' in texts
    assert 'SELECT CAST(NULL AS '+label+')' in texts
