import json
from pathlib import Path

from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.owner.definition_scope import definition_scopes
from agents.owner.generic import generate_owner_hypotheses, _logical_signature_member
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.pipeline.generic_unified import run_generic_downstream
from agents.identity.production import run_identity_owner_path, _resolve_mixed_catalog_context


def document(tmp_path, body, url='https://official.test/reference'):
    path = tmp_path / 'source.html'
    path.write_text('<main><h1>Scalar functions</h1>' + body + '</main>', encoding='utf-8')
    raw = build_page_topology({'url':url,'html_path':str(path)}, {})
    d = from_v2_page(raw, dbms='sample', html_path=str(path))
    d['document_id'] = d['page_id']
    return d


BODY = '''<table><tr><th>Name</th><th>Description</th></tr>
<tr><td>ALPHA()</td><td>Summary only</td></tr></table>
<ul><li><p><code>ALPHA(x)</code></p><p><code>ALPHA(x,y)</code></p>
<p>Returns the absolute value.</p><p>Handles negative values without changing neighboring features.</p>
<pre>sql&gt; SELECT ALPHA(-2);\n2</pre></li>
<li><p><code>BETA(x)</code></p><p>Returns a different result.</p><pre>SELECT BETA(1);</pre></li></ul>'''


def test_list_proof_and_owner_preserve_trigger_and_siblings(tmp_path):
    d = document(tmp_path, BODY)
    proofs = [p for p in definition_scopes(d) if p.get('definition_pattern') == 'SIGNATURE_LED_LIST_ITEM']
    assert {p['primary_identity'] for p in proofs} == {'ALPHA','BETA'}
    a = next(p for p in proofs if p['primary_identity'] == 'ALPHA')
    b = next(p for p in proofs if p['primary_identity'] == 'BETA')
    assert len(a['definition_anchor_block_ids']) == 2
    assert not set(a['block_ids']) & set(b['block_ids'])
    i = next(i for i,r in enumerate(d['blocks']) if r['text']=='ALPHA(x)')
    m = _logical_signature_member(d['blocks'],i)
    assert d['blocks'][i] in m
    assert any('Handles negative' in r['text'] for r in m)
    assert not any('BETA' in r['text'] for r in m)
    owners=generate_owner_hypotheses([d])
    owner=next(o for o in owners if o.get('template')=='signature_led_list_item' and o.get('owner_definition_identities')==['ALPHA'])
    assert set(a['block_ids']) == set(owner['member_block_ids'])


def test_generic_pipeline_list_semantics_are_grounded_and_not_foreign(tmp_path):
    d=document(tmp_path,BODY)
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='test')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    final=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    a=next(r for r in final if r['name']=='ALPHA')
    assert {c['value'] for c in a['semantics']['description']} == {
        'Summary only','Returns the absolute value.',
        'Handles negative values without changing neighboring features.'}
    assert {v['value'] for v in a['semantics']['syntax']} == {'ALPHA(x)','ALPHA(x,y)'}
    assert len(a['semantics']['examples']) == 1
    assert all('BETA' not in c['value'] for f in ['description','syntax','examples'] for c in a['semantics'][f])


def test_examples_navigation_and_api_are_not_list_definitions(tmp_path):
    for heading in ['Examples','API reference','Tutorial']:
        d=document(tmp_path,'<h2>'+heading+'</h2><ul><li><p>ALPHA(x)</p><p>Returns a value.</p></li></ul>')
        assert not any(p.get('definition_pattern')=='SIGNATURE_LED_LIST_ITEM' for p in definition_scopes(d))
    d=document(tmp_path,'<nav><ul><li><p>ALPHA(x)</p><p>Returns a value.</p></li></ul></nav>')
    assert not any(p.get('definition_pattern')=='SIGNATURE_LED_LIST_ITEM' for p in definition_scopes(d))


def test_reference_only_or_mixed_subject_list_does_not_prove_definition(tmp_path):
    for body in ['<p>ALPHA(x)</p>', '<p>ALPHA(x)</p><p>See BETA for details.</p>',
                 '<p>ALPHA(x)</p><p>Returns a value.</p><p>BETA(y)</p><p>Returns another value.</p>',
                 '<p>ALPHA(x), BETA(y)</p><p>Returns a value.</p>']:
        d=document(tmp_path,'<ul><li>'+body+'</li></ul>')
        assert not any(p.get('definition_pattern')=='SIGNATURE_LED_LIST_ITEM' for p in definition_scopes(d))


def test_heading_description_before_code_still_preserved(tmp_path):
    d=document(tmp_path,'<h2>ALPHA</h2><p>Returns a value.</p><p>Syntax:</p><pre>ALPHA(x)</pre><h2>BETA</h2><p>Other definition.</p>')
    i=next(i for i,b in enumerate(d['blocks']) if b['text']=='ALPHA(x)')
    members=_logical_signature_member(d['blocks'],i)
    assert any(b['text']=='Returns a value.' for b in members)
    assert d['blocks'][i] in members
    assert not any(b['text']=='Other definition.' for b in members)


def test_overloads_nested_notes_and_explicit_subject_predicate(tmp_path):
    d=document(tmp_path,'<ul><li><p>ALPHA(x), ALPHA(x,y)</p><p>ALPHA() adds its arguments.</p>'
        '<ul><li><p>Returns NULL for absent arguments.</p></li></ul><pre>SELECT ALPHA(1);</pre></li></ul>')
    proof=next(p for p in definition_scopes(d) if p.get('definition_pattern')=='SIGNATURE_LED_LIST_ITEM')
    bs={b['block_id']:b for b in d['blocks']}
    assert any(bs[i]['text']=='Returns NULL for absent arguments.' for i in proof['block_ids'])


def test_formal_anchor_enters_identity_not_only_example(tmp_path):
    d=document(tmp_path,BODY)
    result=run_identity_owner_path([d])
    anchors=[o for o in result['occurrences'] if o.get('definition_section_id')]
    assert {o['raw_identity'] for o in anchors} >= {'ALPHA(x)','ALPHA(x,y)','BETA(x)'}
    assert all(o['structural_role']=='SIGNATURE' and o['defining'] and o['owner_local_binding'] for o in anchors)
    assert all(o['surface_role']=='FEATURE_DEFINITION' for o in anchors)
    assert not any('SELECT' in o['raw_evidence'] for o in anchors)


def test_mixed_catalog_context_does_not_split_proven_function(tmp_path):
    directory=tmp_path/'directory';directory.mkdir()
    catalog=document(directory,'<h2>Function and Operator Reference</h2><table>'
        '<tr><th>Name</th><th>Description</th></tr><tr><td>ALPHA()</td><td>Returns a value.</td></tr></table>',
        url='https://official.test/catalog')
    definition=document(tmp_path,BODY)
    result=run_identity_owner_path([catalog,definition])
    alpha=[u for u in result['units'] if u['canonical_identity']=='ALPHA']
    assert len(alpha)==1 and alpha[0]['feature_type']=='FUNCTION'
    resolved=[e['context_type_resolution'] for e in alpha[0]['evidence'] if e.get('context_type_resolution')]
    assert resolved and resolved[0]['definition_witnesses']
    assert any(r['definition_section_id'] for r in alpha[0]['occurrence_records'] if r.get('definition_section_id'))
    docs=[catalog,definition]
    profile=build_cold_start_profile(dbms='sample',documents=docs,run_id='mixed_catalog')
    run_generic_downstream(dbms='sample',documents=docs,profile=profile,
        output_root=tmp_path/'mixed_run',semantic=True,semantic_provider='deterministic')
    final=json.loads((tmp_path/'mixed_run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    published=[r for r in final if r['name']=='ALPHA']
    assert len(published)==1 and published[0]['feature_type']=='function'
    assert {r['value'] for r in published[0]['semantics']['syntax']}=={'ALPHA(x)','ALPHA(x,y)'}
    assert published[0]['semantics']['examples']


def test_competing_direct_type_evidence_not_erased():
    def row(cid,signals,proof=None):
        return {'candidate_id':cid,'owner_id':cid,'occurrence_records':[{
            'source_type':'table_row','structural_role':'SIGNATURE','defining':True,
            'source_block_id':cid,'definition_section_id':proof,'type_evidence':signals}]}
    def signal(t,reason):return {'type':t,'strength':'STRONG','reason':reason}
    native=row('native',[signal('FUNCTION','callable_signature')],'proof')
    explicit=row('explicit',[signal('FUNCTION','explicit_function_context'),
        signal('DATATYPE','type_declaration_context')])
    assert _resolve_mixed_catalog_context([native,explicit])==[native,explicit]
    ambiguous=row('context',[signal('FUNCTION','explicit_function_context'),
        signal('OPERATOR','explicit_operator_context')])
    # No definition proof: context alone must not be resolved by guessing.
    assert _resolve_mixed_catalog_context([ambiguous])==[ambiguous]
