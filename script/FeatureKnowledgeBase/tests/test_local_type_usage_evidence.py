"""Positive usage counterevidence must not impose a new function threshold."""
import pytest
from agents.type.definition_roles import interpret_type_evidence


def interpret(block):
    block = dict({'block_id': 'b', 'block_kind': 'TEXT', 'heading_ancestry': ['Functions']}, **block)
    vote = {'identity_unit_id': 'u', 'evidence_id': 'e', 'block_id': 'b', 'owner_id': 'o',
            'candidate_type': 'FUNCTION', 'evidence_kind': 'CALLABLE_SIGNATURE', 'strength': 'STRONG',
            'subject_alignment': 'EXACT_HEAD', 'callable_definition_span': {'text': 'TURN(x)'}}
    return interpret_type_evidence([{'identity_unit_id': 'u', 'canonical_identity': 'TURN'}], [vote],
        [{'page_id': 'p', 'url': 'https://official.test/ref', 'blocks': [block]}])[0]


@pytest.mark.parametrize('block,rule', [
    ({'text': 'CREATE TABLE t(x TURN(6));', 'block_kind': 'CODE'}, 'CALLABLE_IN_CREATE_TABLE_EXAMPLE'),
    ({'text': 'f A TURN(x) result', 'block_kind': 'TABLE_ROW',
      'table_metadata': {'headers': ['Specifier', 'Description'], 'cells': ['%f', 'A TURN(x) result']}}, 'CALLABLE_IN_NON_IDENTITY_TABLE_CELL'),
    ({'text': 'In this example, the type of the test column is TURN(4).'}, 'COLUMN_TYPE_USAGE_NOT_CALLABLE_DEFINITION'),
    ({'text': 'TURN(10) CHARACTER SET binary', 'block_kind': 'CODE'}, 'CHARACTER_SET_DECLARATION_REFERENCE'),
    ({'text': 'salary TURN(5,2)', 'block_kind': 'CODE', 'heading_ancestry': ['Numeric Data Types']}, 'COLUMN_DATATYPE_DECLARATION_EXAMPLE'),
    ({'text': 'Well Known TURN (WKT) Format', 'block_kind': 'HEADING'}, 'PROSE_ACRONYM_HEADING_NOT_CALLABLE'),
])
def test_positive_non_definition_evidence_is_retained_but_cannot_vote(block, rule):
    row = interpret(block)
    assert row['definition_vote_eligible'] is False
    assert row['role_proof']['rule'] == rule + '/v1'
    assert row['role_proof']['raw_text'] == block['text']
    assert row['evidence_id'] == 'e' and row['strength'] == 'STRONG'


@pytest.mark.parametrize('block', [
    {'text': 'TURN(x) returns the rotation of x.'},
    {'text': 'The TURN(x) function returns a rotation.'},
    {'text': 'TURN(x)', 'block_kind': 'CODE'},
    {'text': 'TURN(x) Rotation.', 'block_kind': 'TABLE_ROW',
     'table_metadata': {'headers': ['Function', 'Description'], 'cells': ['TURN(x)', 'Rotation.']}},
    {'text': 'TURN TURN(x) returns a rotation.', 'block_kind': 'TABLE_ROW',
     'table_metadata': {'headers': ['Function', 'Description'], 'cells': ['TURN', 'TURN(x) returns a rotation.']}},
    {'text': 'TURN(x)', 'block_kind': 'HEADING'},
    {'text': 'TURN(x) | TURN(x,y)', 'block_kind': 'CODE'},
])
def test_actual_function_definitions_in_prose_code_heading_and_table_preserved(block):
    assert interpret(block).get('definition_vote_eligible') is not False


def test_operator_result_reference_is_not_independent_definition():
    from agents.type.definition_roles import _operator_usage_counterproof
    ref = {'block_id': 'b', 'block_kind': 'TEXT',
           'text': 'The result of both expressions is null, just as without the TURN operator.'}
    assert _operator_usage_counterproof(ref, 'turn')['rule'] == 'RESULT_COMPARISON_OPERATOR_REFERENCE/v1'
    for text in ['The TURN operator converts a value.', 'value TURN argument', 'TURN']:
        assert _operator_usage_counterproof(dict(ref, text=text), 'turn') is None


def test_symbol_operator_equivalence_does_not_retype_referenced_function():
    from agents.type.definition_roles import _operator_usage_counterproof
    block={'block_id':'b','block_kind':'TEXT',
           'text':'The ~~> operator can be used wherever TURN(LOOKUP()) would be allowed.'}
    proof=_operator_usage_counterproof(block,'turn')
    assert proof and proof['rule']=='FOREIGN_SYMBOL_OPERATOR_CALLABLE_REFERENCE/v1'
    assert proof['operator_head']=='~~>' and proof['matched_subject']=='turn'
    assert _operator_usage_counterproof(block,'~~>') is None
    assert _operator_usage_counterproof(dict(block,block_kind='CODE'),'turn') is None
    assert _operator_usage_counterproof(dict(block,text='The TURN operator transforms a value.'),'turn') is None
