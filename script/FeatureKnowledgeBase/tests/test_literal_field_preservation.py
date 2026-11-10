"""Source-visible field values are distinct from identity matching text."""
import copy
import pytest
from tests.test_profile_definition_execution import document, run
from tests.test_five_structure_executors import profile_for
from agents.owner.composite_definition import field_members


@pytest.mark.parametrize('spaces',['    ','\u00a0\u00a0\u00a0\u00a0','\t\t'])
def test_literal_string_survives_ir_owner_semantic_and_final(tmp_path,spaces):
    invocation="TRIM_EDGE('"+spaces+"test"+spaces+"')"
    d=document(tmp_path,'Functions','<h2>TRIM_EDGE(value)</h2><table>'
        '<tr><td>Description</td><td>Removes edge whitespace.</td></tr>'
        '<tr><td>Example 1</td><td><code>'+invocation+'</code></td></tr>'
        '<tr><td>Result 1</td><td>test</td></tr></table>'
        '<h2>OTHER(value)</h2><p>Unrelated definition.</p>')
    row=next(b for b in d['blocks'] if b.get('table_metadata',{} ) and
        (b['table_metadata'].get('cells') or [''])[0]=='Example 1')
    assert row['table_metadata']['literal_cells'][1]==invocation
    assert row['table_metadata']['cells'][1]!=invocation
    records=run(tmp_path,d,profile_for(d,'B'))
    r=next(r for r in records if r['name']=='TRIM_EDGE')
    examples=r['semantics']['examples']
    assert len(examples)==1
    assert invocation in examples[0]['value']
    assert invocation in examples[0]['evidence']['evidence_span']['text']
    from agents.semantic_extractor.frontend_projection_v2 import project_feature
    assert invocation in project_feature(r)['examples'][0]
    assert 'Unrelated' not in str(r['semantics'])


def test_legacy_cells_still_work_without_literal_projection():
    block={'block_id':'b','block_kind':'TABLE_ROW','dom_path':'table > tr',
        'table_metadata':{'cells':['Description','A definition.']}}
    example=copy.deepcopy(block);example['block_id']='e'
    example['table_metadata']['cells']=['Example','EDGE(1)']
    assert [f['value'] for f in field_members([block,example],{})]==['A definition.','EDGE(1)']


def test_forged_literal_cell_cannot_supply_an_official_example(tmp_path):
    d=document(tmp_path,'Functions','<h2>EDGE(value)</h2><table>'
        '<tr><td>Description</td><td>Returns an edge.</td></tr>'
        '<tr><td>Example</td><td>EDGE(1)</td></tr>'
        '<tr><td>Result</td><td>1</td></tr></table>')
    for b in d['blocks']:
        if (b.get('table_metadata') or {}).get('cells',[])==['Example','EDGE(1)']:
            b['table_metadata']['literal_cells'][1]="EDGE('INJECTED')"
    records=run(tmp_path,d,profile_for(d,'B'))
    assert records
    assert 'INJECTED' not in str([r['semantics'] for r in records])


def test_frozen_hash_tracks_crlf_bytes_and_detects_later_changes(tmp_path):
    import hashlib
    from agents.document_structure_v2.topology import build_page_topology
    from agents.contracts.document_ir import from_v2_page
    from agents.semantic_stage.source_structure import SourceStructureIndex
    path=tmp_path/'reference.html'
    path.write_bytes(b'<main>\r\n<h1>Reference</h1>\r\n<p>A definition.</p>\r\n</main>')
    page=build_page_topology({'url':'https://official.test/reference','html_path':str(path)},{})
    assert page['page_hash']==hashlib.sha256(path.read_bytes()).hexdigest()
    d=from_v2_page(page,dbms='sample',html_path=str(path))
    assert SourceStructureIndex()._page(d)['status']=='RESOLVED'
    path.write_bytes(path.read_bytes().replace(b'A definition.',b'Changed definition.'))
    assert SourceStructureIndex()._page(d)['status']=='SOURCE_HASH_MISMATCH'


def test_grounding_consumes_verified_literal_without_rewriting_structure():
    from agents.semantic_stage.grounding import validate_semantic_output
    raw="SELECT EDGE(j->>'$.field');"
    block={'evidence_ref':'b','block_kind':'CODE','dom_path':'main > pre','source_hash':'hash',
        'source_structure_status':'RESOLVED','source_text_origin':'HASH_VERIFIED_LITERAL_NODE',
        'text':"SELECT EDGE ( j ->> '$.field' ) ;",'literal_text':raw,
        'observed_context':{'literal_code_text':raw}}
    result=validate_semantic_output({'official_examples':[{'sql':raw,'evidence_refs':['b']}]},
        {'source_blocks':[block]})
    assert result['ungrounded_claims']==0
    assert result['fields']['official_examples'][0]['sql']==raw
    assert block['text']!=raw


def test_literal_annotation_alone_cannot_ground_another_text():
    from agents.semantic_stage.grounding import validate_semantic_output
    block={'evidence_ref':'b','block_kind':'CODE','dom_path':'main > pre','source_hash':'hash',
        'source_structure_status':'RESOLVED','source_text_origin':'HASH_VERIFIED_LITERAL_NODE',
        'text':'SELECT EDGE(1);','literal_text':'SELECT OTHER(1);',
        'observed_context':{'literal_code_text':'SELECT EDGE(1);'}}
    result=validate_semantic_output({'official_examples':[{'sql':'SELECT OTHER(1);','evidence_refs':['b']}]},
        {'source_blocks':[block]})
    assert result['ungrounded_claims']==1
