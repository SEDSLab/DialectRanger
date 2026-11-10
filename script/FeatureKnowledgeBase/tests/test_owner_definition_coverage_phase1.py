"""Definition ownership is complete only within a proven structural boundary."""
import copy

from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.identity.production import run_identity_owner_path
from agents.owner.definition_scope import definition_scopes, normalized_name
from agents.semantic_stage.evidence_bundle import build_indexes, build_evidence_bundle
from agents.semantic_stage.definition_section import project_definition_owner


def document(tmp_path, body):
    path=tmp_path/'reference.html'
    path.write_text('<main>'+body+'</main>',encoding='utf8')
    page=build_page_topology({'url':'https://example.test/reference','html_path':str(path)},
                             {'main_selectors':['main'],'noise_selectors':[]})
    return from_v2_page(page,dbms='synthetic',html_path=path)


def pipeline(doc, name):
    result=run_identity_owner_path([doc])
    group=next(g for g in result['units'] if g['canonical_identity']==name)
    indexes=build_indexes(result['units'],result['occurrences'],result['owners'],[doc])
    return result,group,build_evidence_bundle(group,indexes=indexes)


def test_complex_section_keeps_all_raw_blocks_and_excludes_peer_and_toc(tmp_path):
    doc=document(tmp_path,'''<nav><ul><li>FEATURE_A</li></ul></nav><article>
    <h2 id="a">FEATURE_A&#8203;</h2><p>Returns the value for A.</p>
    <ul><li>Syntax: FEATURE_A(x)</li><li>Available metadata</li></ul>
    <details><summary>Example</summary><h3>A demonstration</h3><p>Explain this example.</p>
    <pre>SELECT FEATURE_A(1)</pre><p>Result:</p><table><tr><td>1</td></tr></table></details>
    <h2 id="b">FEATURE_B</h2><p>Returns the value for B.</p><pre>FEATURE_B(x)</pre></article>''')
    result,group,bundle=pipeline(doc,'FEATURE_A')
    wanted={b['block_id'] for b in doc['blocks'] if 'article' in b['dom_path'] and '#b' not in b['dom_path']
            and b['order']<next(x['order'] for x in doc['blocks'] if x['text']=='FEATURE_B')}
    actual={b['block_id'] for b in bundle['source_blocks']}
    assert actual==wanted
    assert not any('nav' in b['dom_path'] for b in bundle['source_blocks'])
    # Navigation may already have been removed by Owner inference. If an
    # exclusion reaches Evidence, it must still be explicitly accounted for.
    assert all(o['reason']=='EXCLUDED_NAVIGATION' for o in bundle['excluded_owners'])
    assert bundle['evidence_completeness']['definition_scope_status']=='COMPLETE'
    # Scope restoration never renumbers discovery anchors or fabricates blocks.
    assert actual <= {b['block_id'] for b in doc['blocks']}
    assert all(o['member_block_ids'] for o in result['owners'])
    assert group['candidate_ids']


def test_signature_optional_when_heading_has_local_definition_prose(tmp_path):
    doc=document(tmp_path,'<article><h2 id="a">FEATURE_A</h2><p>Represents a portable value.</p>'
                  '<ul><li>Additional information.</li></ul><h2>FEATURE_B</h2><p>Other content.</p></article>')
    result,group,bundle=pipeline(doc,'FEATURE_A')
    assert any(b['text']=='Represents a portable value.' for b in bundle['source_blocks'])
    assert not any(b['text']=='Other content.' for b in bundle['source_blocks'])
    assert any('LOCAL_DEFINITION_PROSE' in p['boundary_evidence'] for o in bundle['owners'] for p in o['definition_section_proofs'])


def test_incidental_call_and_book_metadata_cannot_prove_a_definition(tmp_path):
    doc=document(tmp_path,'<article><h2>Usage</h2><p>This page mentions FUNCTION_X in an example.</p>'
                  '<pre>SELECT FUNCTION_X(1)</pre></article>')
    assert definition_scopes(doc)==[]
    result=run_identity_owner_path([doc])
    assert not any(u['canonical_identity']=='FUNCTION_X' for u in result['units'])
    observed=[o for o in result['occurrences'] if o['normalized_identity']=='FUNCTION_X']
    assert observed and all(o['occurrence_role']=='EXAMPLE_TOKEN' and not o['defining'] for o in observed)


def test_same_spelling_does_not_expand_another_definition(tmp_path):
    doc=document(tmp_path,'<article><h2 id="a">FEATURE_A</h2><p>Returns A.</p><pre>FEATURE_A(x)</pre>'
                  '<h2 id="b">FEATURE_B</h2><p>Returns B.</p><pre>SELECT FEATURE_A(1)</pre></article>')
    result,group,bundle=pipeline(doc,'FEATURE_A')
    assert not any(b['text'] in {'FEATURE_B','Returns B.','SELECT FEATURE_A(1)'} for b in bundle['source_blocks'])
    # The foreign use is now rejected before candidate/group construction,
    # rather than being attached and excluded only by the Evidence builder.
    foreign=[o for o in result['occurrences'] if o['raw_identity']=='FEATURE_A(1)']
    assert foreign and all(not o['owner_local_binding'] and not o['defining'] for o in foreign)
    assert not ({o['owner_id'] for o in foreign} & set(group['owner_ids']))


def test_no_alias_relation_from_incidental_example_call(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><p>Returns A.</p><pre>FEATURE_A(x)</pre>'
                  '<p>Example:</p><pre>SELECT FEATURE_OTHER(1)</pre></article>')
    proof=definition_scopes(doc)[0]
    assert 'FEATURE_OTHER' not in proof['identity_values']


def test_only_example_explanation_does_not_prove_heading_definition(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><p>Example:</p>'
                  '<pre>SELECT FEATURE_A(1)</pre><p>Returns the example result.</p></article>')
    assert definition_scopes(doc)==[]


def test_overload_signatures_keep_one_scope_but_unrelated_heads_do_not(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><p>Returns A.</p>'
                  '<pre>FEATURE_A(x)</pre><pre>FEATURE_A(x,y)</pre></article>')
    assert len(definition_scopes(doc))==1
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><p>Returns A.</p>'
                  '<pre>FEATURE_A(x)</pre><pre>FEATURE_B(x)</pre></article>')
    assert definition_scopes(doc)==[]


def test_bare_and_callable_alternatives_in_same_formal_block_are_compatible(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A Function</h2><pre>FEATURE_A | FEATURE_ALIAS()</pre>'
                  '<p>Returns A.</p><p>Example:</p><pre>FEATURE_ALIAS()</pre></article>')
    proof=definition_scopes(doc)[0]
    assert set(proof['identity_values'])=={'FEATURE_A','FEATURE_ALIAS'}


def test_table_catalog_owners_stay_rows_and_preserve_cell_structure(tmp_path):
    doc=document(tmp_path,'<article><h2>Reference catalog</h2><table><tr><th>Function</th><th>Description</th></tr>'
                  '<tr><td>FEATURE_A(x)</td><td>Returns A.</td></tr>'
                  '<tr><td>FEATURE_B(x)</td><td>Returns B.</td></tr></table></article>')
    _,_,bundle=pipeline(doc,'FEATURE_A')
    assert len(bundle['source_blocks'])==1
    block=bundle['source_blocks'][0]
    assert block['table_metadata']['cells']==['FEATURE_A(x)','Returns A.']
    assert not bundle['owners'][0]['definition_section_proofs']


def test_no_silent_window_on_long_proven_definition(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><pre>FEATURE_A(x)</pre><p>Returns A.</p>'+
                  ''.join('<p>Supporting paragraph %s.</p>'%i for i in range(90))+'</article>')
    _,_,bundle=pipeline(doc,'FEATURE_A')
    assert len(bundle['source_blocks'])==len(doc['blocks'])
    assert bundle['evidence_completeness']['definition_scope_status']=='COMPLETE'
    assert bundle['evidence_completeness']['semantic_input_status']=='INCOMPLETE'
    assert bundle['evidence_completeness']['semantic_prompt_main_overflow']>0


def test_missing_original_block_is_explicit_incomplete(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><pre>FEATURE_A(x)</pre><p>Returns A.</p></article>')
    result,group,_=pipeline(doc,'FEATURE_A')
    owners=copy.deepcopy(result['owners'])
    for owner in owners:
        for proof in owner.get('definition_section_proofs',[]):proof['block_ids'].append('missing:block')
    bundle=build_evidence_bundle(group,indexes=build_indexes([group],result['occurrences'],owners,[doc]))
    assert 'missing:block' in bundle['evidence_completeness']['missing_block_ids']
    assert bundle['evidence_completeness']['definition_scope_status']=='INCOMPLETE'


def test_unicode_normalization_is_matching_only(tmp_path):
    doc=document(tmp_path,'<article><h2>1.2 FEATURE_A&#8203; ¶</h2><p>Returns A.</p><pre>FEATURE_A(x)</pre></article>')
    proof=definition_scopes(doc)[0]
    assert proof['primary_identity']=='FEATURE_A'
    assert doc['blocks'][0]['text']!='FEATURE_A'
    assert normalized_name('FEATURE_A\n ( x )\ufeff')=='FEATURE_A'


def test_frozen_v1_definition_proof_is_revalidated_without_new_fields(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><pre>FEATURE_A(x)</pre><p>Returns A.</p></article>')
    proof=definition_scopes(doc)[0]
    old={k:v for k,v in proof.items() if k in {'heading_block_id','section_id','block_ids','association','association_block_id'}}
    owner={'owner_id':'old','page_id':doc['page_id'],'member_block_ids':proof['block_ids'],
           'owner_kind':'SignatureOwner','definition_section_proofs':[old]}
    result=project_definition_owner({'identity_unit_id':'group','canonical_identity':'FEATURE_A'},owner,doc)
    assert result['evidence_scope_status']=='COMPLETE'
    assert set(result['member_block_ids'])==set(proof['block_ids'])


def test_unknown_nested_feature_boundary_is_not_certified_complete(tmp_path):
    doc=document(tmp_path,'<article><h2>FEATURE_A</h2><p>Returns A.</p><pre>FEATURE_A(x)</pre>'
                  '<h3>FEATURE_B</h3><p>Returns B.</p><pre>FEATURE_B(x)</pre></article>')
    assert not any(p['primary_identity']=='FEATURE_A' for p in definition_scopes(doc))
    _,_,bundle=pipeline(doc,'FEATURE_A')
    assert not any(b['text']=='Returns B.' for b in bundle['source_blocks'])
    assert bundle['evidence_completeness']['definition_scope_status']!='COMPLETE'
