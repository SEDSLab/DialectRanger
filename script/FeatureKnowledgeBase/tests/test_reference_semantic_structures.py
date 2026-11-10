import copy
import pytest
from agents.contracts.definition_entry import observed_callable_declaration
from agents.owner.definition_scope import navigation_block, heading_container
from agents.owner.structure_execution import execute_structures
from agents.semantic_stage.source_structure import SourceStructureIndex
from tests.test_profile_definition_execution import document
from tests.test_subject_contract_repair import section_profile
from tests.test_profile_definition_execution import run


def test_signature_markup_keeps_native_term():
    nodes=[{'element_name':'span','semantic_classes':['sig-name']},
           {'element_name':'em','semantic_classes':['sig-param']}]
    assert observed_callable_declaration('spin ( x ) → [same as input] #',nodes)[0]=='SPIN'
    assert observed_callable_declaration('spin ( x ) → [same as input] #') is None
    assert observed_callable_declaration('spin ( 42 ) → integer #',nodes) is None


def test_multiple_formal_heads_in_one_syntax_slot():
    from agents.owner.declaration_binding import syntax_declarations
    assert [x['identity'] for x in syntax_declarations('SPIN(x) ROTATE(x)')]==['SPIN','ROTATE']
    assert syntax_declarations('Mention SPIN(x) in prose')==[]
    assert syntax_declarations('SELECT SPIN(1);')==[]


def test_cached_evidence_lineage_restores_only_matching_source():
    from agents.semantic_stage.evidence_bundle import restore_source_lineage
    document={'url':'https://official.test/spin','page_hash':'hash','blocks':[
        {'block_id':'b','dom_path':'main > h1','provenance':{'html_path':'frozen.html','page_hash':'hash'}}]}
    citation={'block_id':'b','dom_path':'main > h1','source_url':document['url'],'source_hash':'hash','text':'SPIN'}
    bundle={'source_blocks':[copy.deepcopy(citation)]}
    assert restore_source_lineage([bundle],[document])==1
    assert bundle['source_blocks'][0]['text']=='SPIN'
    assert bundle['source_blocks'][0]['provenance']['html_path']=='frozen.html'
    wrong={'source_blocks':[dict(citation,source_hash='foreign')]}
    with pytest.raises(ValueError,match='LINEAGE_MISMATCH'):restore_source_lineage([wrong],[document])


def test_page_context_cache_tracks_actual_context_not_object_identity():
    from agents.identity.quality import _page_context
    page={'title':'SQL functions','blocks':[]}
    first=_page_context(page)
    page['title']='JDBC API Reference'
    second=_page_context(page)
    assert first[0]!=second[0]
    assert second[0]==_page_context(dict(page))[0]


def test_body_page_header_and_its_sibling_syntax(tmp_path):
    d=document(tmp_path,'SPIN','<div><header><h1>SPIN</h1><p>A rotation.</p></header>'
        '<div><h2>Syntax</h2><pre>SPIN(x)</pre><h2>Description</h2><p>Rotates input.</p>'
        '<h2>Examples</h2><pre>SELECT SPIN(1);</pre></div></div>')
    anchor=next(b for b in d['blocks'] if b['text']=='SPIN' and ' > header > ' in b['dom_path'])
    assert not navigation_block(anchor)
    assert heading_container(anchor).endswith('main > div')
    proofs=execute_structures(d,section_profile(d))['proofs']
    proof=next(p for p in proofs if p['primary_identity']=='SPIN')
    assert any(b['text']=='Rotates input.' for b in d['blocks'] if b['block_id'] in proof['block_ids'])


def test_aria_row_has_physical_cells_and_source_grid(tmp_path):
    d=document(tmp_path,'Functions','<div role="table"><div role="row">'
        '<div role="columnheader">Function</div><div role="columnheader">Description</div></div>'
        '<div role="row"><div role="cell">SPIN(x)</div><div role="cell">Rotates input.</div></div></div>')
    rows=[b for b in d['blocks'] if b['block_kind']=='TABLE_ROW']
    assert len(rows)==2
    assert rows[1]['table_metadata']['cells']==['SPIN(x)','Rotates input.']
    obs=SourceStructureIndex().observe(d,rows[1])
    assert obs['table_context']['cells'][0]['headers'][0]['text']=='Function'


def test_bounded_page_owner_delivers_heading_identity_to_final(tmp_path):
    from agents.contracts.document_ir import from_v2_page
    from agents.document_structure_v2.topology import build_page_topology
    html=tmp_path/'page.html'
    html.write_text('<main><div><header><h1>SPIN</h1></header><div><h2>Syntax</h2>'
        '<pre>SPIN(x)</pre><h2>Description</h2><p>Returns a rotation.</p>'
        '<h2>Examples</h2><pre>SELECT SPIN(1);</pre></div></div></main>',encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':'https://official.test/spin','html_path':str(html)},{}),dbms='sample',html_path=html)
    d['document_id']=d['page_id']
    profile=section_profile(d)
    plan=profile['hint_contract']['definition_structure_patterns'][0]
    plan.update(structure='A',identity_slot='PAGE_SUBJECT',boundary='SUBJECT_PAGE')
    result=run(tmp_path,d,profile)
    f=next(f for f in result if f['name']=='SPIN')
    assert f['feature_type']=='function'
    assert f['semantics']['description'] and f['semantics']['syntax'] and f['semantics']['examples']


def test_co_declared_heads_keep_physical_syntax_anchors(tmp_path):
    d=document(tmp_path,'Functions','<section><h2>SPIN</h2><h3>Syntax</h3>'
        '<pre>SPIN(x) ROTATE(x)</pre><h3>Description</h3><p>Returns a rotation.</p>'
        '<h3>Examples</h3><pre>SELECT OTHER(1);</pre></section>')
    ps=execute_structures(d,section_profile(d))['proofs']
    p=next(p for p in ps if 'SPIN' in p['identity_values'])
    assert set(p['identity_values'])=={'SPIN','ROTATE'}
    alias_anchor=p['identity_anchor_block_ids']['ROTATE'][0]
    assert next(b for b in d['blocks'] if b['block_id']==alias_anchor)['block_kind']=='CODE'
    assert 'OTHER' not in p['identity_values']
    result=run(tmp_path,d,section_profile(d))
    assert {'SPIN','ROTATE'} <= {f['name'] for f in result}
    primary=next(f for f in result if f['name']=='SPIN')
    assert any(c['value']=='Returns a rotation.' for c in primary['semantics']['description'])
    secondary=next(f for f in result if f['name']=='ROTATE')
    assert not secondary['semantics']['description']
