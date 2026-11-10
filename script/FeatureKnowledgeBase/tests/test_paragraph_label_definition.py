import json
import pytest
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.owner.definition_scope import paragraph_definition_scopes
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.pipeline.generic_unified import run_generic_downstream


def doc(tmp_path, body, heading='Scalar Functions'):
    p=tmp_path/'reference.html'
    p.write_text('<main><h1>'+heading+'</h1>'+body+'</main>',encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':'https://official.test/chapter','html_path':str(p)},{}),dbms='sample',html_path=str(p))
    d['document_id']=d['page_id']
    return d


def entry(name,description):
    return '<p><strong>'+name+'</strong></p><p><code>'+name+'(x)</code></p><p>'+description+'</p>'


def test_sibling_definitions_complete_and_separated(tmp_path):
    d=doc(tmp_path,entry('ALPHA','Returns the first value.')+'<p>Null input returns null.</p><pre>SELECT ALPHA(1);</pre>'+entry('BETA','Returns another value.'))
    proofs=paragraph_definition_scopes(d)
    assert [p['primary_identity'] for p in proofs]==['ALPHA','BETA']
    assert not set(proofs[0]['block_ids']) & set(proofs[1]['block_ids'])
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='paragraph_test')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf-8'))
    a=next(r for r in rows if r['name']=='ALPHA')
    assert [c['value'] for c in a['semantics']['description']]==['Returns the first value.','Null input returns null.']
    assert [c['value'] for c in a['semantics']['syntax']]==['ALPHA(x)']
    assert a['semantics']['examples']
    assert 'BETA' not in json.dumps(a['semantics'])


def test_unsupported_compound_label_is_still_boundary(tmp_path):
    d=doc(tmp_path,entry('ALPHA','Returns the first value.')+'<p><strong>BETA, GAMMA</strong></p><p><code>BETA(x)</code></p><p>Foreign description.</p>')
    p=paragraph_definition_scopes(d)[0]
    assert all('Foreign' not in b['text'] for b in d['blocks'] if b['block_id'] in p['block_ids'])


@pytest.mark.parametrize('heading,body',[
    ('API Functions',entry('ALPHA','Returns an API value.')),
    ('Function Examples',entry('ALPHA','Returns an example value.')),
    ('Scalar Functions','<p>ALPHA</p><p><code>ALPHA(x)</code></p><p>Returns a value.</p>'),
    ('Scalar Functions','<p><strong>ALPHA</strong></p><p><code>BETA(x)</code></p><p>Returns a value.</p>'),
    ('Scalar Functions','<p><strong>ALPHA</strong></p><p><code>SELECT ALPHA(x)</code></p><p>Returns a value.</p>'),
])
def test_non_definitions_rejected(tmp_path,heading,body):
    assert paragraph_definition_scopes(doc(tmp_path,body,heading))==[]
