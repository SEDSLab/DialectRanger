import pytest
from bs4 import BeautifulSoup
from agents.document_structure_v2.local_definition_context import observe_local_definition_context
from agents.document_structure_v2.local_definition_context import observe_page_function_definition
from agents.document_structure_v2.local_definition_context import observe_implementation_relation
from agents.pipeline.production_identity import execute_production_identity_path
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.type.evidence import build_typed_feature_evidence


@pytest.mark.parametrize('intro,value,role', [
    ('The datatype must be one of the following:', 'CUSTOM', 'DATATYPE_DECLARATION_LIST'),
    ('The following types are permitted:', 'DECIMAL', 'DATATYPE_DECLARATION_LIST'),
    ('Supports the following SQL functions:', 'COMPUTE(x)', 'FUNCTION_DECLARATION_LIST'),
    ('See examples of functions:', 'COMPUTE(x)', None),
    ('Download the following files:', 'CUSTOM', None),
    ('The API supports the following functions:', 'COMPUTE(x)', None),
    ('Examples use the following SQL functions:', 'COMPUTE(x)', None),
    ('The datatype must be one of the following:', 'SELECT x FROM t', None),
])
def test_local_intro_observation(intro, value, role):
    node = BeautifulSoup('<p>'+intro+'</p><ul><li>'+value+'</li></ul>', 'lxml').li
    assert observe_local_definition_context(node).get('role') == role


@pytest.mark.parametrize('role,subject,text,kind', [
    ('FUNCTION_DECLARATION_LIST', 'COMPUTE', 'COMPUTE(x)', 'FUNCTION'),
    ('DATATYPE_DECLARATION_LIST', 'CUSTOM', 'CUSTOM', 'DATATYPE'),
])
def test_declaration_evidence_stays_local(role, subject, text, kind):
    context = {'canonical_subject': subject, 'owner_id': 'o', 'identity_unit_id': 'u',
               'blocks': [{'block_id': 'b', 'block_type': 'list_item', 'text': text,
                           'definition_context': {'role': role, 'intro_text': 'observed local introduction'}}]}
    rows = build_typed_feature_evidence(context)
    assert any(r['candidate_type'] == kind and r['strength'] == 'STRONG' for r in rows)
    context['canonical_subject'] = 'FOREIGN'
    assert not any(r['candidate_type'] == kind and r['strength'] == 'STRONG'
                   for r in build_typed_feature_evidence(context))


def test_truth_table_values_do_not_receive_definition_votes():
    node = BeautifulSoup('<table><tr><th>Left operand is NULL</th><th>Result of OP operator</th></tr>'
                         '<tr><td>yes</td><td>false</td></tr></table>', 'lxml').find_all('tr')[1]
    observation = observe_local_definition_context(node)
    assert observation['role'] == 'BEHAVIOR_TRUTH_TABLE'
    rows = build_typed_feature_evidence({'canonical_subject': 'YES', 'owner_id': 'o', 'identity_unit_id': 'u',
        'blocks': [{'block_id': 'b', 'block_type': 'table_row', 'text': 'yes false', 'cells': ['yes','false'],
                    'heading_ancestry': ['Operators'], 'definition_context': observation}]})
    assert not any(r['candidate_type'] == 'OPERATOR' and r['strength'] == 'STRONG' for r in rows)
    catalog = BeautifulSoup('<table><tr><th>Operator</th><th>Result</th></tr><tr><td>+</td><td>sum</td></tr></table>', 'lxml').find_all('tr')[1]
    observed=observe_local_definition_context(catalog)
    assert observed['role']=='OPERATOR_CATALOG'
    assert [m['identity'] for m in observed['members']]==['+']


@pytest.mark.parametrize('text,expected', [
    ('The encode64() function is an SQL function implemented as an extension.', True),
    ('The encode64() function is not a standard part of the library.', False),
    ('Call encode64() in this example.', False),
    ('The other() function is an SQL function.', False),
    ('The encode64() function is a function from the C API.', False),
])
def test_page_subject_needs_explicit_positive_self_definition(text, expected):
    node = BeautifulSoup('<p>'+text+'</p>', 'lxml').p
    assert bool(observe_page_function_definition(node, 'The encode64() SQL Function')) == expected


def test_self_definition_forms_identity_without_capturing_references(tmp_path):
    path = tmp_path/'reference.html'
    path.write_text('<html><head><title>The encode64() SQL Function</title></head><body>'
                    '<h1>Overview</h1><p>The encode64() function is an SQL function implemented as an extension.</p>'
                    '<p>See decode64() for another function.</p></body></html>', encoding='utf-8')
    doc = from_v2_page(build_page_topology({'url':'https://example.test/reference.html','html_path':str(path)}, {}),
                       dbms='fixture',html_path=str(path))
    result = execute_production_identity_path([doc])
    assert {u['canonical_identity'] for u in result['units']} == {'ENCODE64'}


@pytest.mark.parametrize('text,kind', [
    ('The compare(X,Y) function is equivalent to the expression X COMPARE Y.', 'FUNCTION'),
    ('The infix COMPARE operator is implemented by calling the function compare(Y,X).', 'OPERATOR'),
])
def test_implementation_relationship_does_not_reverse_primary_type(text, kind):
    node = BeautifulSoup('<p>'+text+'</p>', 'lxml').p
    relation = observe_implementation_relation(node)
    assert relation['identity'].upper() == 'COMPARE'
    context = {'canonical_subject': 'COMPARE', 'identity_unit_id': 'u', 'owner_id': 'o',
               'blocks': [{'block_id': 'b', 'block_type': 'text', 'text': text,
                           'heading_ancestry': ['Functions and Operators'], 'definition_context': relation}]}
    votes = build_typed_feature_evidence(context)
    assert {v['candidate_type'] for v in votes if v['strength'] == 'STRONG'} == {kind}
