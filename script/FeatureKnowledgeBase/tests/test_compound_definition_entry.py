"""Compositional source contracts, independent of DB or feature vocabulary."""
import copy
import hashlib

import pytest
from bs4 import BeautifulSoup

from agents.contracts.definition_entry import compound_entry, declaration_names, identifier_key
from agents.contracts.catalog_membership import catalog_membership
from agents.owner.reference_definitions import catalog_names
from agents.semantic_stage.source_structure import table_structure
from agents.semantic_stage.structured_semantics import extract_structured_semantics
from agents.semantic_stage.evidence_bundle import build_indexes, build_evidence_bundle
from agents.document_structure_v2.topology import _dom_path


def source(html, kind='FUNCTION', name='FROB'):
    soup = BeautifulSoup(html, 'lxml')
    row = [r for r in soup.find_all('tr') if r.find_parent('table') is soup.table][-1]
    context = table_structure(soup.table)[_dom_path(row)]
    return soup, row, {'block_id': 'r', 'block_kind': 'TABLE_ROW', 'source_structure_status': 'RESOLVED',
        'source_hash': 'hash', 'source_url': 'https://fixture.invalid/reference',
        'text': row.get_text(' ', strip=True), 'table_context': context, 'heading_ancestry': ['Functions'], 'owner_ids': ['o'],
        'owner_local_binding': True, 'identity_unit_id': 'u'}


HTML = '''<table><thead><tr><th>Function Description</th><th>Partial Mode</th></tr></thead>
<tbody><tr><td><p><code>frob</code> (x) → numeric</p><p><code>frob</code> (x, y) → numeric</p>
<p>Returns the chosen value.</p><p>Null inputs are ignored.</p>
<p><code>frob(1)</code> → 2</p></td><td>Yes</td></tr></tbody></table>'''


def test_compound_roles_keep_overloads_paragraphs_and_example_separate():
    _, _, row = source(HTML)
    p = compound_entry(row, 'FROB', 'FUNCTION')
    assert [f['role'] for f in p['fragments']] == ['FORMAL_SYNTAX', 'FORMAL_SYNTAX', 'DESCRIPTION', 'DESCRIPTION', 'EXAMPLE']
    assert p['fragments'][0]['value'] == 'frob (x)'
    assert compound_entry(row, 'OTHER', 'FUNCTION') is None


def test_empty_anchors_and_transparent_links_do_not_hide_signatures():
    html=HTML.replace('<code>frob</code>', '<a id="anchor"></a><a href="#ref"><code>frob</code></a>')
    _, _, row=source(html)
    p=compound_entry(row,'FROB','FUNCTION')
    assert len(p['signature_locators'])==2


def test_explicit_prose_introduced_pre_is_an_example_not_syntax():
    html=HTML.replace('<p>Null inputs are ignored.</p>', '<p>For example,</p><pre>SELECT frob(2);</pre>')
    _,_,row=source(html)
    p=compound_entry(row,'FROB','FUNCTION')
    assert next(f for f in p['fragments'] if f['node_kind']=='PRE')['role']=='EXAMPLE'
    _,_,foreign=source(html.replace('SELECT frob(2);','SELECT other(2);'))
    assert next(f for f in compound_entry(foreign,'FROB','FUNCTION')['fragments'] if f['node_kind']=='PRE')['role']=='UNRESOLVED'


def test_short_description_without_ir_child_uses_exact_parent_span():
    _,_,row=source(HTML)
    entry=compound_entry(row,'FROB','FUNCTION')
    fragment=entry['fragments'][2]
    start=row['text'].index(fragment['text'])
    entry['parent_span_fragments']=[dict(fragment,span_start=start,span_end=start+len(fragment['text']))]
    row['compound_definition_entry']=entry
    result=extract_structured_semantics({'normalized_identity':'FROB','feature_type':'FUNCTION','evidence':[row]})
    assert result['description'][0]['text']==fragment['text']
    assert result['description'][0]['evidence_refs']==['r']
    entry['parent_span_fragments'][0]['span_start']+=1
    assert not extract_structured_semantics({'normalized_identity':'FROB','feature_type':'FUNCTION','evidence':[row]})['description']


def test_dom_nbsp_does_not_look_like_a_missing_ir_paragraph():
    _,_,row=source(HTML.replace('chosen value','chosen&nbsp;value'))
    p=compound_entry(row,'FROB','FUNCTION')
    assert p['fragments'][2]['text']=='Returns the chosen value.'


@pytest.mark.parametrize('html', [
    HTML.replace('<p>Null inputs are ignored.</p>', '<p><code>other</code> (x) → numeric</p>'),
    HTML.replace('<td><p>', '<td><table><tr><td>Neighbor</td></tr></table><p>', 1),
    HTML.replace('Function Description', 'Parameter Description'),
])
def test_foreign_heads_nested_tables_and_non_feature_slots_do_not_share(html):
    _, _, row = source(html)
    assert compound_entry(row, 'FROB', 'FUNCTION') is None


def test_child_annotation_needs_parent_and_actual_locator():
    _, _, row = source(HTML)
    proof = compound_entry(row, 'FROB', 'FUNCTION')
    evidence = [row]
    for i, f in enumerate(proof['fragments']):
        evidence.append({'block_id': 'c' + str(i), 'text': f['text'], 'block_kind': 'TEXT',
            'dom_path': f['dom_locator'], 'source_url': row['source_url'], 'source_order': i + 1,
            'source_hash': row['source_hash'], 'owner_ids': ['o'],
            'definition_entry_member': {'parent_block_id': 'r'}})
    payload = {'identity_group_id': 'u', 'normalized_identity': 'FROB', 'feature_type': 'FUNCTION', 'evidence': evidence}
    result = extract_structured_semantics(payload)
    assert [c['text'] for c in result['description']] == ['Returns the chosen value.', 'Null inputs are ignored.']
    assert len(result['formal_syntax']) == 2
    assert result['official_examples'][0]['sql'] == 'frob(1) → 2'
    payload['evidence'] = evidence[1:]
    assert not extract_structured_semantics(payload)['description']
    payload['evidence'] = copy.deepcopy(evidence)
    payload['evidence'][3]['source_hash']='foreign'
    assert len(extract_structured_semantics(payload)['description'])==1
    # A child can already have a narrower SignatureOwner. Its independently
    # verified containing row remains a contributing owner, not a replacement.
    payload['evidence']=copy.deepcopy(evidence)
    payload['evidence'][-1]['owner_ids']=['signature_owner']
    payload['evidence'][-1]['contributing_owner_ids']=['o','signature_owner']
    assert extract_structured_semantics(payload)['official_examples']
    payload['evidence'][-1]['contributing_owner_ids']=['foreign']
    assert not extract_structured_semantics(payload)['official_examples']


def test_overload_return_types_do_not_collapse_to_last_value():
    _, _, row = source(HTML.replace('(x, y) → numeric', '(x, y) → text'))
    proof=compound_entry(row,'FROB','FUNCTION')
    ev=[row]+[{'block_id':str(i),'text':f['text'],'block_kind':'TEXT','dom_path':f['dom_locator'],
              'source_hash':row['source_hash'],'source_url':row['source_url'],'owner_ids':['o'],
              'definition_entry_member':{'parent_block_id':'r'}} for i,f in enumerate(proof['fragments'])]
    result=extract_structured_semantics({'normalized_identity':'FROB','feature_type':'FUNCTION','evidence':ev})
    assert result['return_type'] is None
    assert len(result['formal_syntax'])==2


@pytest.mark.parametrize('spelling', ['moment', 'MOMENT', 'moment(n)', 'moment(n, m)', 'moment(n), instant', 'moment [ ( p ) ] [ fields ]'])
def test_declaration_case_and_parameters_are_not_subject_vetoes(spelling):
    from tests.test_catalog_membership import row
    b = row(spelling, 'Data Types')
    assert catalog_membership(b, 'MOMENT')[0]['feature_type'] == 'DATATYPE'
    b['heading_ancestry'] = ['Data Type Formatting Functions']
    assert not any(p['feature_type']=='DATATYPE' for p in catalog_membership(b, 'MOMENT'))


def test_name_table_needs_type_context_and_is_not_all_name_tables():
    block = {'heading_ancestry': ['Data Types'], 'table_metadata': {
        'headers': ['Name', 'Aliases', 'Description'], 'cells': ['moment(n)', 'instant', 'A temporal value.']}}
    # The explicit local datatype slot can now participate upstream too;
    # an untyped Name table still cannot create a declaration proof.
    from tests.test_catalog_membership import row
    assert catalog_membership(row('moment(n)', 'Data Types'), 'MOMENT')
    assert catalog_names(block)==('DATATYPE',['MOMENT','INSTANT'])
    assert catalog_names(dict(block,heading_ancestry=[])) is None
    block['heading_ancestry'] = ['Data Type Formatting Functions']
    assert catalog_names(block) is None
    assert identifier_key('"MiXeD"') != identifier_key('"mixed"')
    assert declaration_names('not a type: prose') == []


def test_column_header_is_never_a_case_folded_type_declaration():
    assert catalog_names({'dom_path':'main > table > thead > tr','table_metadata':{
        'headers':['Data Type','Description'],'cells':['Data Type','Description']}}) is None
    assert catalog_names({'table_metadata':{'headers':['Type','Storage'],'cells':['Type','Storage']}}) is None
    assert catalog_names({'dom_path':'main > table > tbody > tr','table_metadata':{
        'headers':['Data Type','Description'],'cells':['moment','A temporal value.']}})==('DATATYPE',['MOMENT'])


def test_explicit_alias_column_is_proof_not_name_similarity():
    from tests.test_catalog_membership import row
    b=row('moment(n)','Data Types')
    b['table_context']['cells'].append({'text':'instant, instant8','headers':[{'text':'Aliases'}],
                                      'dom_locator':'table > tr > td:3'})
    assert catalog_membership(b,'INSTANT')[0]['identity_column']=='Aliases'
    assert not catalog_membership(b,'MOMENT2')
    b['table_context']['cells'][0]['headers']=[{'text':'Parameter'}]
    assert not catalog_membership(b,'INSTANT')


def test_real_source_bundle_delivers_children_with_original_ids(tmp_path):
    path = tmp_path / 'raw.html'
    path.write_text(HTML, encoding='utf8')
    soup, row, _ = source(HTML)
    blocks = [{'block_id': 'r', 'block_kind': 'TABLE_ROW', 'text': row.get_text(' ', strip=True),
               'dom_path': _dom_path(row), 'order': 0, 'heading_ancestry': ['Functions']}]
    for i, p in enumerate(row.find_all('p')):
        blocks.append({'block_id': 'c' + str(i), 'block_kind': 'TEXT', 'text': p.get_text(' ', strip=True),
                       'dom_path': _dom_path(p), 'order': i + 1, 'heading_ancestry': ['Functions']})
    d = {'page_id': 'p', 'url': 'https://fixture.invalid/reference', 'html_path': str(path),
         'page_hash': hashlib.sha256(path.read_bytes()).hexdigest(), 'blocks': blocks}
    owner = {'owner_id': 'o', 'page_id': 'p', 'owner_kind': 'TableRowOwner', 'member_block_ids': ['r']}
    group = {'identity_unit_id': 'u', 'canonical_identity': 'FROB', 'feature_type': 'FUNCTION', 'owner_ids': ['o']}
    bundle = build_evidence_bundle(group, indexes=build_indexes([group], [], [owner], [d]))
    assert {b['block_id'] for b in bundle['source_blocks']} == {'r', 'c0', 'c1', 'c2', 'c3', 'c4'}
    assert bundle['evidence_completeness']['definition_scope_status'] == 'COMPLETE'
    assert all(x['status'] == 'BOUND' for x in bundle['compound_entry_dispositions'])
    assert owner['member_block_ids'] == ['r']
    # Check the actual prompt boundary, not only direct interpreter calls.
    import json
    from agents.semantic_stage.pipeline import build_semantic_prompt
    prompt = build_semantic_prompt(bundle, {'identity_group_id': 'u', 'normalized_identity': 'FROB', 'feature_type': 'FUNCTION'})
    payload = json.loads(prompt.split('INPUT:\n', 1)[1].split('\n\nOUTPUT SCHEMA:', 1)[0])
    parsed = extract_structured_semantics(payload)
    assert len(parsed['description']) == 2
    assert len(parsed['formal_syntax']) == 2
    assert len(parsed['official_examples']) == 1
    # The publication credibility gate independently checks new child refs
    # against the immutable parent owner and source DOM, not their annotation.
    from agents.contracts.identity_credibility import evaluate_identity_credibility
    group.update(candidate_ids=['candidate'], provenance={'page_id':'p','source_url':d['url'],'page_hash':d['page_hash']})
    credibility=evaluate_identity_credibility(group,owners=[owner],documents=[d],bundle=bundle)
    assert credibility['identity_credible']
    assert credibility['verified_content_associations']==5
    from agents.contracts.definition_entry import verified_entry_child
    from agents.semantic_stage.source_structure import SourceStructureIndex
    child=next(b for b in bundle['source_blocks'] if b['block_id']=='c2')
    assert not verified_entry_child(group,child,dict(owner,member_block_ids=[]),d,SourceStructureIndex())
    assert not verified_entry_child(dict(group,canonical_identity='NEIGHBOR'),child,owner,d,SourceStructureIndex())
