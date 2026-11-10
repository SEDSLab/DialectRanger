import pytest
from tests.test_profile_definition_execution import document, run
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.owner.definition_patterns import execute_patterns, parse_surface
from agents.semantic_stage.structured_semantics import _expression_result_example
from agents.semantic_stage.structured_semantics import extract_structured_semantics
from agents.owner.generic import generate_owner_hypotheses
from agents.identity.production import run_identity_owner_path


@pytest.mark.parametrize('tag', ['li','div','section'])
def test_observed_callable_recipe_does_not_require_english_leading_verb(tmp_path,tag):
    d=document(tmp_path,'Functions','<{0}><p><code>TURN(x[, mode])</code></p>'
        '<p>This function rotates the input.</p></{0}><{0}><p><code>SHIFT(x)</code></p>'
        '<p>An operation on the input.</p></{0}>'.format(tag))
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='observed')
    assert any(r['grammar']=='CALLABLE_DEFINITION' for r in p['hint_contract']['definition_patterns'])
    assert any(r['identity_values']==['TURN'] for r in execute_patterns(d,p))
    fs=run(tmp_path,d,p)
    f=next(f for f in fs if f['name']=='TURN')
    assert any('rotates' in c['value'] for c in f['semantics']['description'])


@pytest.mark.parametrize('text,valid',[("TURN(1) → 'one'",True),
    ('TURN(1) → NULL',True),('TURN(1) → explanatory prose',False),
    ("OTHER(1) → 'one'",False),("TURN(1) and OTHER(2) → 'one'",False)])
def test_expression_result_requires_definition_subject_and_literal_result(text,valid):
    b={'definition_section_proofs':[{'identity_values':['TURN']} ]}
    assert _expression_result_example(text,b,'TURN') is valid
    assert not _expression_result_example(text,{},'TURN')


@pytest.mark.parametrize('failure', [None,'no_definition','no_example_label','foreign_subject'])
def test_expression_result_claim_preserves_literal_and_provenance(failure):
    text="TURN(1) → 'one'"
    block={'block_id':'observed-example','text':text,'block_kind':'TEXT',
           'source_url':'https://official.test/reference',
           'definition_section_proofs':[{'identity_values':['TURN']}],
           'observed_context':{'labels':[{'text':'Examples'}]}}
    if failure=='no_definition':block['definition_section_proofs']=[]
    if failure=='no_example_label':block['observed_context']['labels']=[]
    if failure=='foreign_subject':block['definition_section_proofs']=[{'identity_values':['OTHER']}]
    result=extract_structured_semantics({'normalized_identity':'TURN','feature_type':'FUNCTION','evidence':[block]})
    if failure:
        assert not result['official_examples']
    else:
        claim=result['official_examples'][0]
        assert claim['sql']==text
        assert claim['evidence_refs']==['observed-example']
        assert claim['source_url']==block['source_url']
        assert not result['formal_syntax']


def test_definition_keeps_nested_table_as_content_not_a_function_identity(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p>'
        '<p>This function rotates the input using format specifiers.</p>'
        '<table><tr><th>Specifier</th><th>Description</th></tr>'
        '<tr><td>%</td><td>A literal percent character.</td></tr></table></li>'
        '<li><p><code>SHIFT(x)</code></p><p>An operation on the input.</p></li></ul>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='nested_table')
    owners=generate_owner_hypotheses([d],p)
    owner=next(o for o in owners if o.get('owner_scope_identity_values')==['TURN'])
    assert any(b['block_id'] in owner['owner_scope_block_ids'] and b['block_kind']=='TABLE_ROW'
               for b in d['blocks'])
    result=run_identity_owner_path([d],p)
    for unit in result['units']:
        if unit['canonical_identity']=='%':
            assert not any(e['owner_id']==owner['owner_id'] for e in unit['evidence'])


@pytest.mark.parametrize('heading',['Functions and Operators','Operators'])
def test_callable_spelling_in_mixed_context_does_not_prove_function_type(tmp_path,heading):
    d=document(tmp_path,heading,'<ul><li><p><code>PREDICATE(query)</code></p>'
        '<p>This evaluates a query.</p></li><li><p><code>ALTERNATE(query)</code></p>'
        '<p>This evaluates an alternate query.</p></li></ul>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='mixed')
    assert not any(proof['pattern_grammar']=='CALLABLE_DEFINITION' for proof in execute_patterns(d,p))


@pytest.mark.parametrize('explicit_operator',[False,True])
def test_verified_function_body_cannot_retype_subject_from_a_chapter_reference(tmp_path,explicit_operator):
    from agents.type.definition_roles import interpret_type_evidence
    prose=('The TURN operator transforms the input.' if explicit_operator else
           'This function rotates a value. See Functions and Operators for details.')
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p><p>'+prose+
        '</p></li><li><p><code>SHIFT(x)</code></p><p>An operation on the input.</p></li></ul>')
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='type_reference')
    owners=generate_owner_hypotheses([d],p)
    owner=next(o for o in owners if o.get('owner_scope_identity_values')==['TURN'])
    b=next(b for b in d['blocks'] if b['text']==prose)
    vote={'identity_unit_id':'u','owner_id':owner['owner_id'],'block_id':b['block_id'],
          'candidate_type':'OPERATOR','evidence_kind':'OPERATOR_SLOT',
          'subject_alignment':'FEATURE_SLOT','evidence_id':'vote','strength':'STRONG'}
    result=interpret_type_evidence([{'identity_unit_id':'u','canonical_identity':'TURN'}],
                                  [vote],[d],owners)[0]
    assert (result.get('definition_vote_eligible') is False) is (not explicit_operator)
    if not explicit_operator:
        assert result['role_proof']['definition_anchor_block_id'] in owner['member_block_ids']
        assert result['evidence_id']=='vote' and result['strength']=='STRONG'
