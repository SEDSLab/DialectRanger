"""Source observations survive the actual prompt; they are not semantic output."""
import copy
import json
from bs4 import BeautifulSoup
from agents.semantic_stage.source_structure import SourceStructureIndex, table_structure
from agents.semantic_stage.pipeline import build_semantic_prompt
from agents.semantic_stage.evidence_bundle import build_indexes, build_evidence_bundle
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology


def serialized(bundle):
    return json.loads(build_semantic_prompt(bundle,{}).split('INPUT:\n')[1].split('\n\nOUTPUT SCHEMA:')[0])


def fixture(tmp_path, body):
    p=tmp_path/'source.html';p.write_text('<html><body><main>'+body+'</main></body></html>',encoding='utf8')
    page=build_page_topology({'url':'https://example.test/ref','html_path':str(p)}, {'main_selectors':['main'],'noise_selectors':[]})
    doc=from_v2_page(page,dbms='synthetic',html_path=p)
    ids=[b['block_id'] for b in doc['blocks']]
    owner={'owner_id':'owner:test','page_id':doc['page_id'],'member_block_ids':ids,'owner_kind':'SectionOwner'}
    group={'identity_unit_id':'unit:test','canonical_identity':'FEATURE_A','owner_ids':['owner:test'],'occurrence_ids':[]}
    return doc,group,owner,build_evidence_bundle(group,indexes=build_indexes([group],[],[owner],[doc]))


def test_html_ir_bundle_prompt_observed_label_and_container(tmp_path):
    _,_,_,b=fixture(tmp_path,'<h2>FEATURE_A</h2><p>Returns A.</p><ul><li>Syntax: FEATURE_A(x)</li><li>Function type: Scalar</li></ul>'
                      '<details><summary>Example</summary><p>Example explanation</p><pre>SELECT FEATURE_A(1)</pre>'
                      '<table><tr><th>Value</th></tr><tr><td>1</td></tr></table></details>')
    inp=serialized(b)
    code=next(x for x in inp['evidence'] if x['block_kind']=='CODE')
    assert code['observed_context']['visible_label']=='Example'
    row=next(x for x in inp['evidence'] if x['text']=='1')
    assert row['block_kind']=='TABLE_ROW'
    assert row['observed_context']['container_kind']=='DETAILS'
    assert 'official_examples' not in row
    syntax=next(x for x in inp['evidence'] if x['text'].startswith('Syntax:'))
    assert syntax['observed_context']['visible_label']=='Syntax'
    metadata=next(x for x in inp['evidence'] if x['text'].startswith('Function type:'))
    assert metadata['observed_context']['visible_label']=='Function type'
    assert 'description' not in metadata
    assert all(x['dom_path'] and x['block_id'] and x['identity_unit_id']=='unit:test' for x in inp['evidence'])


def test_unlabelled_does_not_invent_syntax_or_example_label(tmp_path):
    _,_,_,b=fixture(tmp_path,'<h2>FEATURE_A</h2><p>Returns A.</p><pre>FEATURE_A(x)</pre>')
    assert all(x['observed_context']['label_status']=='UNKNOWN' for x in serialized(b)['evidence'])


def test_visible_paragraph_example_label_reaches_code(tmp_path):
    _,_,_,b=fixture(tmp_path,'<h2>FEATURE_A</h2><pre>FEATURE_A(x)</pre><p>Example:</p><pre>FEATURE_A(1)</pre>')
    c=next(x for x in serialized(b)['evidence'] if x['text']=='FEATURE_A(1)')
    assert c['observed_context']['visible_label']=='Example'
    assert c['observed_context']['labels'][0]['block_id']


def test_operator_columns_stay_separate_in_real_input(tmp_path):
    _,_,_,b=fixture(tmp_path,'<table><thead><tr><th>Operator</th><th>Description</th></tr></thead>'
                      '<tbody><tr><td>x + y</td><td>Add</td></tr><tr><td>x - y</td><td>Subtract</td></tr></tbody></table>')
    inp=serialized(b)
    rows=[x for x in inp['evidence'] if x['text']=='x + y Add']
    assert len(rows)==1
    cells=rows[0]['table_context']['cells']
    assert [(c['headers'][0]['text'],c['text']) for c in cells]==[('Operator','x + y'),('Description','Add')]
    assert all(c['dom_locator'] for c in cells)
    assert 'Subtract' not in str(rows)


def table(html):
    return table_structure(BeautifulSoup(html,'lxml').table)


def test_spans_logical_coordinates_and_hierarchical_headers():
    t=table('<table><thead><tr><th rowspan="2">Name</th><th colspan="2">Values</th></tr>'
            '<tr><th>Input</th><th>Output</th></tr></thead><tbody><tr><td>A</td><td>1</td><td>2</td></tr></tbody></table>')
    data=list(t.values())[-1]['cells']
    assert [c['column_start'] for c in data]==[0,1,2]
    assert [h['text'] for h in data[1]['headers']]==['Values','Input']
    assert list(t.values())[0]['cells'][0]['rowspan']==2
    assert list(t.values())[0]['cells'][1]['colspan']==2


def test_data_rowspan_retains_source_row_without_duplicate_cell():
    t=table('<table><tr><th>A</th><th>B</th></tr><tr><td rowspan="2">X</td><td>Y</td></tr><tr><td>Z</td></tr></table>')
    row=list(t.values())[-1]
    assert row['cells'][0]['column_start']==1
    assert len(row['cells'])==1 and len(row['inherited_cells'])==1


def test_missing_headers_unknown_not_guessed():
    cells=list(table('<table><tr><td>INTEGER</td><td>DOUBLE</td></tr></table>').values())[0]['cells']
    assert all(c['header_status']=='UNKNOWN' and not c['headers'] for c in cells)


def test_ambiguous_colspan_association_unknown():
    cells=list(table('<table><tr><th>A</th><th>B</th></tr><tr><td colspan="2">X</td></tr></table>').values())[-1]['cells']
    assert cells[0]['header_status']=='UNKNOWN'


def test_partial_header_coverage_remains_unknown():
    cells=list(table('<table><tr><th>A</th><td></td></tr><tr><td colspan="2">X</td></tr></table>').values())[-1]['cells']
    assert cells[0]['header_status']=='UNKNOWN'


def test_nonstandard_explicit_header_and_links():
    cells=list(table('<table><tr class="table-header"><td id="a">Operator</td><td>Description</td></tr>'
               '<tr><td headers="a">x + y</td><td>Add</td></tr></table>').values())[-1]['cells']
    assert cells[0]['header_status']=='EXPLICIT'
    assert cells[1]['headers'][0]['text']=='Description'


def test_table_identifiers_are_page_scoped():
    soup=BeautifulSoup('<table><tr><td>A</td></tr></table>','lxml')
    a=list(table_structure(soup.table,page_id='page:a').values())[0]
    b=list(table_structure(soup.table,page_id='page:b').values())[0]
    assert a['table_id']!=b['table_id']


def test_notes_table_level_uncertain_not_assigned(tmp_path):
    _,_,_,b=fixture(tmp_path,'<table><tr><th>SQL type</th><th>Default<sup>*</sup></th></tr>'
       '<tr><td>DECIMAL</td><td>0.0</td></tr></table><p><sup>*</sup> Default is NULL.</p>')
    c=next(x for x in serialized(b)['evidence'] if x['text']=='DECIMAL 0.0')['table_context']
    assert c['cells'][1]['headers'][0]['text']=='Default *'
    assert c['shared_context'][0]['scope_status']=='UNCERTAIN'
    assert c['shared_context'][0]['feature_assignment'] is False


def test_legacy_ir_without_html_optional_and_immutable(tmp_path):
    doc,group,owner,_=fixture(tmp_path,'<p>Some text</p>')
    doc.pop('html_path',None);doc['provenance'].pop('html_path',None)
    original=copy.deepcopy(doc)
    b=build_evidence_bundle(group,indexes=build_indexes([group],[],[owner],[doc]))
    assert serialized(b)['evidence'][0]['source_structure_status']=='FROZEN_HTML_MISSING'
    assert doc==original


def test_source_hash_mismatch_does_not_read_untrusted_structure(tmp_path):
    doc,_,_,_=fixture(tmp_path,'<p>Example:</p>')
    doc['provenance']['page_hash']='bad'
    doc['page_hash']='bad'
    obs=SourceStructureIndex().observe(doc,doc['blocks'][0])
    assert obs['source_structure_status']=='SOURCE_HASH_MISMATCH'
    assert obs['observed_context']['label_status']=='UNKNOWN'


def test_prompt_budget_omission_refs_recoverable_and_order_unchanged():
    evidence=[{'block_id':f'b{i}','evidence_ref':f'b{i}','text':str(i),'dom_path':f'p{i}'} for i in range(82)]
    b={'main_evidence':evidence,'source_blocks':evidence}
    inp=serialized(b)
    assert [x['evidence_ref'] for x in inp['evidence']]==[f'b{i}' for i in range(80)]
    assert [x['evidence_ref'] for x in inp['input_selection']['omitted_evidence']]==['b80','b81']
    assert all(x['recover_from']=='EvidenceBundle.source_blocks' for x in inp['input_selection']['omitted_evidence'])


def test_multiple_owner_references_preserved_without_identity_changes(tmp_path):
    doc,g,o,_=fixture(tmp_path,'<p>Body</p>')
    other={**o,'owner_id':'owner:second'}
    g={**g,'owner_ids':[o['owner_id'],other['owner_id']]};before=copy.deepcopy(g)
    b=build_evidence_bundle(g,indexes=build_indexes([g],[],[o,other],[doc]))
    assert serialized(b)['evidence'][0]['contributing_owner_ids']==['owner:second','owner:test']
    assert g==before
