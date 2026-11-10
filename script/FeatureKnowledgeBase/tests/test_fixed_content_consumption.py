"""Field roles must consume source proof without granting a new subject."""
import pytest
from tests.test_profile_definition_execution import document, run
from tests.test_five_structure_executors import profile_for


def test_verified_operator_row_retains_example_and_result(tmp_path):
    d=document(tmp_path,'SQL Operators','<table><tr><th>Operator</th><th>Description</th><th>Example</th><th>Result</th></tr>'
        '<tr><td>@@</td><td>Combines two values.</td><td>2 @@ 3</td><td>5</td></tr></table>')
    r=next(r for r in run(tmp_path,d) if r['name']=='@@')
    assert [c['value'] for c in r['semantics']['examples']]==['2 @@ 3\n5']
    assert not r['semantics']['syntax']


def test_catalog_example_cannot_be_borrowed_from_another_operator(tmp_path):
    d=document(tmp_path,'SQL Operators','<table><tr><th>Operator</th><th>Description</th><th>Example</th></tr>'
        '<tr><td>@@</td><td>Combines two values.</td><td>2 + 3</td></tr></table>')
    r=next(r for r in run(tmp_path,d) if r['name']=='@@')
    assert not r['semantics']['examples']


def test_operator_identity_column_is_not_syntax(tmp_path):
    d=document(tmp_path,'Functions and Operators','<table><tr><th>Function</th><th>Operator</th><th>Description</th></tr>'
        '<tr><td>PICK(v)</td><td>-&gt;&gt;</td><td>Extracts a string.</td></tr></table>')
    rows=run(tmp_path,d)
    r=next(r for r in rows if r['name']=='->>')
    assert r['semantics']['description']
    assert not r['semantics']['syntax']
    assert any(r['name']=='PICK' and r['feature_type']=='function' for r in rows)


def test_setup_session_literal_and_continuation_survive_final(tmp_path):
    body='<ul><li><p><code>EDGE(v)</code></p><p>Returns the edge.</p><pre>' \
        "local&gt; SET @x = '    data  ';\nlocal&gt; SELECT\n    -&gt; EDGE(@x);\n" \
        '</pre></li><li><p><code>OTHER(v)</code></p><p>Other subject.</p></li></ul>'
    d=document(tmp_path,'SQL Functions',body)
    r=next(r for r in run(tmp_path,d) if r['name']=='EDGE')
    assert len(r['semantics']['examples'])==1
    value=r['semantics']['examples'][0]['value']
    assert "'    data  '" in value and '\n' in value
    assert value in r['semantics']['examples'][0]['evidence']['evidence_span']['text']


def test_definition_equation_is_retained_but_not_as_syntax(tmp_path):
    d=document(tmp_path,'SQL Functions','<ul><li><p><code>EDGE(v)</code></p>'
        '<p>Returns an edge. Equivalently:</p><pre>EDGE(v) = OTHER(v) + 1</pre></li></ul>')
    r=next(r for r in run(tmp_path,d) if r['name']=='EDGE')
    assert not any('=' in c['value'] for c in r['semantics']['syntax'])
    assert any('EDGE(v) = OTHER(v)' in c['value'] for c in r['semantics']['description'])


def test_operator_session_requires_unquoted_exact_application():
    from agents.semantic_stage.structured_semantics import _cli_example_shape
    block={'observed_context':{'literal_code_text':"local> SELECT '!';\nlocal> SELECT 1 != 2;"}}
    assert not _cli_example_shape(block,'!','OPERATOR')
    block['observed_context']['literal_code_text']+='\nlocal> SELECT ! 0;'
    assert _cli_example_shape(block,'!','OPERATOR')


def test_literal_observation_preserves_structural_citation_text(tmp_path):
    import json
    d=document(tmp_path,'Data Types','<table><tr><th>Type</th><th>Description</th></tr>'
        '<tr><td>OPAQUE</td><td>A value.\nMore information.</td></tr></table>')
    records=run(tmp_path,d)
    assert any(r['name']=='OPAQUE' and r['feature_type']=='datatype' for r in records)
    original={b['block_id']:b for b in d['blocks']}
    bundles=[json.loads(l) for l in (tmp_path/'run/evidence/bundles.jsonl').open(encoding='utf8')]
    observed=[b for bundle in bundles for b in bundle['source_blocks'] if b.get('source_text_origin')]
    assert observed
    for b in observed:
        assert b['text']==original[b['block_id']]['text']
        assert b['literal_text']==b['observed_context']['literal_row_text'].strip()
    assert any(b['literal_text']!=b['text'] for b in observed)
