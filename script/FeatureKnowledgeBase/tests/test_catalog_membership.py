"""Generic catalog proof tests: no DB names or Feature vocabulary in rules."""
import copy

import pytest

from agents.contracts.catalog_membership import catalog_membership, catalog_citation_valid
from agents.contracts.publication_integrity import evaluate_publication_integrity
from agents.contracts.admission import decide_admission
from agents.semantic_stage.source_structure import table_structure
from bs4 import BeautifulSoup


def row(text='FROB()', heading='Scalar Functions'):
    return {'block_id': 'b', 'block_kind': 'TABLE_ROW', 'owner_ids': ['o'],
            'source_structure_status': 'RESOLVED', 'source_url': 'https://official.invalid/ref',
            'source_hash': 'abc', 'heading_ancestry': [heading],
            'table_context': {'table_id': 't', 'cells': [
                {'text': text, 'headers': [{'text': 'Name'}], 'dom_locator': 'table > tr > td:1'},
                {'text': '', 'headers': [{'text': 'Description'}], 'dom_locator': 'table > tr > td:2'}]}}


@pytest.mark.parametrize('text,heading,name,kind', [
    ('FROB()', 'Scalar Functions', 'FROB', 'FUNCTION'),
    ('FROB(x)', 'Functions and Operators', 'FROB', 'FUNCTION'),
    ('%%', 'Functions and Operators', '%%', 'OPERATOR'),
    ('MATCHES', 'Logical Operators', 'MATCHES', 'OPERATOR'),
    ('MOMENT', 'Data Types', 'MOMENT', 'DATATYPE')])
def test_typed_membership_without_description_value(text, heading, name, kind):
    assert catalog_membership(row(text, heading), name)[0]['feature_type'] == kind


@pytest.mark.parametrize('heading', ['Functions / Examples', 'Function Parameters',
    'Function Results', 'API Functions', 'Connector Type Mapping', 'Configuration Types',
    'Functions / Navigation', 'Functions / Programming'])
def test_negative_section(heading):
    assert not catalog_membership(row(heading=heading), 'FROB')


@pytest.mark.parametrize('change', ['foreign_name', 'prose', 'example', 'wrong_column',
    'missing_locator', 'missing_hash', 'unresolved', 'profile_only', 'embedded', 'mixed_word'])
def test_fail_closed(change):
    block = row()
    if change == 'foreign_name': block['table_context']['cells'][0]['text'] = 'OTHER()'
    if change == 'prose': block['table_context']['cells'][0]['text'] = 'FROB() returns a value'
    if change == 'example': block['table_context']['cells'][0]['text'] = 'SELECT FROB(x);'
    if change == 'wrong_column': block['table_context']['cells'][0]['headers'] = [{'text': 'Parameter'}]
    if change == 'missing_locator': block['table_context']['cells'][0].pop('dom_locator')
    if change == 'missing_hash': block.pop('source_hash')
    if change == 'unresolved': block['source_structure_status'] = 'UNKNOWN'
    if change == 'profile_only':
        block['heading_ancestry'] = []
        block['profile_hint'] = {'surface_role': 'FEATURE_CATALOG', 'type_hint': 'FUNCTION'}
    if change == 'embedded': block['observed_context'] = {'feature_domain': 'EMBEDDED_QUERY_LANGUAGE'}
    if change == 'mixed_word':
        block = row('FROB', 'Functions and Operators')
    assert not catalog_membership(block, 'FROB')


def test_integrity_and_structural_only_admission_with_empty_fields():
    unit = {'identity_unit_id': 'u', 'canonical_identity': 'FROB',
            'feature_type': 'FUNCTION', 'owner_ids': ['o']}
    bundle = {'source_blocks': [row()]}
    decision = {'primary_type': 'FUNCTION', 'status': 'CONFIRMED'}
    integrity = evaluate_publication_integrity(unit, bundle, decision)
    assert integrity['state'] == 'CONFIRMED'
    assert integrity['subject_witnesses'][0]['origin'] == 'VERIFIED_CATALOG_MEMBERSHIP'
    admission = decide_admission(dbms='unseen', page_id='p', source_url='https://official.invalid/ref',
        page_hash='abc', html_path='raw.html', identity_unit_id='u', identity_credible=True,
        official_source=True, owner_evidence_correct=True, lineage_complete=True,
        semantic_status='STRUCTURAL_ONLY', publication_integrity=integrity)
    assert admission['decision'] == 'ADMITTED_PARTIAL'
    foreign = copy.deepcopy(bundle)
    foreign['source_blocks'][0]['owner_ids'] = ['foreign']
    assert evaluate_publication_integrity(unit, foreign, decision)['state'] == 'REVIEW'
    decision['status'] = 'REVIEW_REQUIRED'
    assert evaluate_publication_integrity(unit, bundle, decision)['state'] == 'REVIEW'
    decision['status'] = 'CONFIRMED'
    invalid = evaluate_publication_integrity(unit, bundle, decision, source_identity_valid=False)
    assert invalid['subject_witnesses'] == []
    assert invalid['state'] == 'REVIEW'


def test_real_html_accessible_row_header_and_empty_description():
    soup = BeautifulSoup('<table><thead><tr><th>Name</th><th>Description</th></tr></thead>'
                         '<tbody><tr><th scope="row">FROB()</th><td></td></tr></tbody></table>', 'lxml')
    context = list(table_structure(soup.table).values())[1]
    block = row()
    block['table_context'] = context
    assert context['cells'][0]['header_role'] == 'ROW_HEADER'
    assert context['cells'][0]['headers'] == []
    assert catalog_membership(block, 'FROB')[0]['identity_column'] == 'ROW_HEADER'
    context['cells'][0]['column_start'] = 1
    context['cells'][0]['column_end'] = 1
    assert not catalog_membership(block, 'FROB')


@pytest.mark.parametrize('bad', ['block_id', 'page_id', 'source_url', 'corpus_hash', 'owner_id', None])
def test_adapted_catalog_requires_actual_source_citation(bad):
    block = row()
    block['page_id'] = 'p'
    citation = {'block_id': 'b', 'page_id': 'p', 'source_url': block['source_url'],
                'corpus_hash': block['source_hash'], 'owner_id': 'o'}
    unit = {'adapted_identity_kind': 'FUNCTION', 'owner_ids': ['o'],
            'adaptation_provenance': [citation]}
    assert catalog_citation_valid(unit, block)
    if bad is None:
        unit['adaptation_provenance'] = []
    else:
        citation[bad] = 'foreign'
    assert not catalog_citation_valid(unit, block)
