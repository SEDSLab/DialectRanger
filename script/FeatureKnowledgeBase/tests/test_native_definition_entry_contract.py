import json
from agents.document_structure_v2.topology import build_page_topology
from agents.contracts.document_ir import from_v2_page
from agents.owner.definition_scope import definition_scopes
from agents.pipeline.generic_unified import run_generic_downstream
from agents.profile_bootstrap.cold_start import build_cold_start_profile


def document(tmp_path):
    path=tmp_path/'functions.html'
    path.write_text('<html><body><h1>Functions</h1><dl>'
                    '<dt>COMPUTE(X)</dt><dd><p>Returns the first value.</p></dd>'
                    '<dt>OTHER(X)</dt><dd><p>Returns the second value.</p></dd>'
                    '</dl></body></html>',encoding='utf-8')
    return from_v2_page(build_page_topology({'url':'https://example.test/functions.html','html_path':str(path)},{}),
                        dbms='fixture',html_path=str(path))


def test_definition_entry_scopes_do_not_cross_next_term(tmp_path):
    doc=document(tmp_path);proofs=definition_scopes(doc)
    first=next(p for p in proofs if p['primary_identity']=='COMPUTE')
    second=next(p for p in proofs if p['primary_identity']=='OTHER')
    assert not set(first['block_ids']) & set(second['block_ids'])
    texts={b['block_id']:b['text'] for b in doc['blocks']}
    assert any('Returns the first' in texts[x] for x in first['block_ids'])
    assert not any('Returns the second' in texts[x] for x in first['block_ids'])


def test_native_definition_description_and_signature_reach_publication(tmp_path):
    doc=document(tmp_path);profile=build_cold_start_profile(dbms='fixture',documents=[doc],run_id='test')
    root=tmp_path/'run'
    result=run_generic_downstream(dbms='fixture',documents=[doc],profile=profile,output_root=root,
                                  semantic=True,semantic_provider='deterministic')
    final=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    assert {r['name'] for r in final}=={'COMPUTE','OTHER'}
    first=next(r for r in final if r['name']=='COMPUTE')
    assert [c['value'] for c in first['semantics']['description']]==['Returns the first value.']
    assert [c['value'] for c in first['semantics']['syntax']]==['COMPUTE(X)']
    assert result['lineage']['status']=='PASS'


def test_nested_term_and_colon_description_example_roles(tmp_path):
    path=tmp_path/'functions.html'
    path.write_text('<html><body><h1>Functions</h1><dl>'
        '<dt><p><b>COMPUTE(X)</b></p></dt><dd>'
        '<p>The COMPUTE(X) function returns a value. For example, the following expressions are equivalent:</p>'
        '<blockquote><pre>COMPUTE(1); COMPUTE(2)</pre></blockquote>'
        '<p>Another detail is preserved.</p></dd>'
        '<dt><p>OTHER(X)</p></dt><dd><p>A foreign definition.</p></dd>'
        '</dl></body></html>',encoding='utf-8')
    doc=from_v2_page(build_page_topology({'url':'https://example.test/functions.html','html_path':str(path)},{}),
                        dbms='fixture',html_path=str(path))
    profile=build_cold_start_profile(dbms='fixture',documents=[doc],run_id='test')
    root=tmp_path/'run'
    run_generic_downstream(dbms='fixture',documents=[doc],profile=profile,output_root=root,
                           semantic=True,semantic_provider='deterministic')
    final=json.loads((root/'canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    first=next(r for r in final if r['name']=='COMPUTE')
    descriptions=[c['value'] for c in first['semantics']['description']]
    assert len(descriptions)==2
    assert descriptions[0].endswith('equivalent:')
    assert not any(x=='COMPUTE(X)' or 'foreign' in x for x in descriptions)
    assert len(first['semantics']['examples'])==1


def test_unlabelled_code_is_not_automatically_example():
    from agents.semantic_stage.structured_semantics import extract_structured_semantics
    result=extract_structured_semantics({'normalized_identity':'COMPUTE','feature_type':'FUNCTION',
        'evidence':[{'evidence_ref':'code','block_kind':'CODE','text':'COMPUTE(1)',
                     'definition_section_proofs':[{'primary_identity':'COMPUTE'}]}]})
    assert result['official_examples']==[]


def test_native_term_owner_does_not_lend_type_to_example_table():
    from agents.pipeline.generic_unified import _owner_type_context
    owner={'owner_id':'owner:definition','member_block_ids':['term','body'],
        'definition_section_proofs':[{'association':'NATIVE_DEFINITION_TERM_BODY','identity_values':['COMPUTE']}]}
    page={'blocks':[{'block_id':'term','text':'COMPUTE(X)','heading_ancestry':['Functions']},
                    {'block_id':'body','text':'CREATE TABLE demo(x REAL);','heading_ancestry':['Functions']}]}
    context=_owner_type_context({'canonical_identity':'DEMO'},owner,page)
    assert context['owner_local_binding'] is False
    assert context['owner_scope_identity_values']==['COMPUTE']
