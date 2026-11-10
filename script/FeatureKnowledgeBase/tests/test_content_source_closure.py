import copy
import json
import pytest
from tests.test_profile_definition_execution import document,run
from tests.test_five_structure_executors import profile_for


def entry_profile(d):
    p=profile_for(d,'CD')
    for plan in p['hint_contract']['definition_structure_patterns']:
        plan['execution']={'version':1,'identity':{'alternatives':(
            [{'kind':'COLUMN_HEADER','headers':['Name','Function']},{'kind':'VERIFIED_ANCHOR'}] if plan['structure']=='D'
            else [{'kind':'FIRST_PARAGRAPH_CODE'},{'kind':'VERIFIED_ANCHOR'}]),'cardinality':'ONE_OR_MORE'},
            'fields':([{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Description']}}] if plan['structure']=='D' else [
                {'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}},
                {'role':'FORMAL_SYNTAX','locator':{'kind':'SECTION_LABEL','labels':['Syntax']}},
                {'role':'EXAMPLE','locator':{'kind':'SECTION_LABEL','labels':['Examples','Usage']}},
                {'role':'EXAMPLE','locator':{'kind':'OWNER_SQL_APPLICATION'}},
                {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}])}
    return p


@pytest.mark.parametrize('link,summary_expected',[('#turn',True),('#shift',False),('',False)])
def test_catalog_stub_summary_requires_explicit_link_to_bound_definition(tmp_path,link,summary_expected):
    label='<a href="'+link+'">TURN()</a>' if link else 'TURN()'
    d=document(tmp_path,'Functions and Operators','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>'+label+'</td><td>A brief rotation summary.</td></tr></table>'
        '<ul><li><a id="turn"></a><p><code>TURN(x)</code></p><p>Returns a complete rotation.</p></li></ul>')
    rows=run(tmp_path,d,entry_profile(d));r=next(r for r in rows if r['name']=='TURN')
    assert ('A brief rotation summary.' in [x['value']for x in r['semantics']['description']])==summary_expected
    assert 'Returns a complete rotation.' in [x['value']for x in r['semantics']['description']]


def test_catalog_source_cannot_override_profile_identity_column(tmp_path):
    d=document(tmp_path,'Functions and Operators','<table><tr><th>Name</th><th>Other slot</th><th>Description</th></tr>'
        '<tr><td><a href="#turn">TURN()</a></td><td>ordinary prose</td><td>Must not leak.</td></tr></table>'
        '<ul><li><a id="turn"></a><p><code>TURN(x)</code></p><p>Returns a rotation.</p></li></ul>')
    p=entry_profile(d)
    next(p for p in p['hint_contract']['definition_structure_patterns']if p['structure']=='D')['execution']['identity']['alternatives']=[{'kind':'COLUMN_HEADER','headers':['Other slot']}]
    row=next(r for r in run(tmp_path,d,p)if r['name']=='TURN')
    assert 'Must not leak.' not in json.dumps(row['semantics'])


def test_syntax_does_not_swallow_return_description_or_become_example(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p><p>Returns a rotation.</p>'
        '<p><strong>Syntax:</strong></p><pre>STRING TURN(x)</pre><p><strong>Return value:</strong></p>'
        '<p>A string containing the rotation.</p></li></ul>')
    row=next(r for r in run(tmp_path,d,entry_profile(d))if r['name']=='TURN')
    assert 'STRING TURN(x)' in [c['value']for c in row['semantics']['syntax']]
    assert 'A string containing the rotation.' in [c['value']for c in row['semantics']['description']]
    assert 'STRING TURN(x)' not in [c['value']for c in row['semantics']['examples']]


@pytest.mark.parametrize('code',[
    "db> SELECT 'a' 'b';\n -> 'ab'",
    'db> SELECT column,\n TURN(value)\n FROM t;',
    'SET @value = TURN(1);',
    'TURN(42)'])
def test_official_owner_local_sql_context_and_multiline_examples_survive(tmp_path,code):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p><p>Returns a rotation.</p><pre>'+code+'</pre></li></ul>')
    row=next(r for r in run(tmp_path,d,entry_profile(d))if r['name']=='TURN')
    assert code in [c['value'].replace('\r\n','\n')for c in row['semantics']['examples']]


def test_complete_owner_formal_grammar_is_not_example(tmp_path):
    syntax='TURN([DISTINCT] expr [,expr ...])'
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p><p>Returns a rotation.</p><pre>'+syntax+'</pre></li></ul>')
    row=next(r for r in run(tmp_path,d,entry_profile(d))if r['name']=='TURN')
    assert syntax in [c['value']for c in row['semantics']['syntax']]
    assert syntax not in [c['value']for c in row['semantics']['examples']]


@pytest.mark.parametrize('code',['ERROR 12345: failure','python> helper(value)','42'])
def test_output_or_non_sql_cli_not_promoted_as_context_example(tmp_path,code):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p><p>Returns a rotation.</p><pre>'+code+'</pre></li></ul>')
    row=next(r for r in run(tmp_path,d,entry_profile(d))if r['name']=='TURN')
    assert not row['semantics']['examples']


def test_cross_page_reference_merges_only_existing_same_subject_sources(tmp_path):
    from agents.document_structure_v2.topology import build_page_topology
    from agents.contracts.document_ir import from_v2_page
    from agents.pipeline.generic_unified import run_generic_downstream
    docs=[]
    for slug,body in [('overview',"Provides a short definition. See this function's description in <a href='detail'>the detailed chapter</a>."),
                      ('detail','Returns the complete transformation. Multiple inputs are supported.')]:
        path=tmp_path/(slug+'.html');path.write_text('<main><h1>Functions</h1><ul><li><a id="function_turn"></a>'
            '<p><code>TURN(x)</code></p><p>'+body+'</p></li></ul></main>',encoding='utf8')
        docs.append(from_v2_page(build_page_topology({'url':'https://official.test/'+slug,'html_path':str(path)},{}),
                                dbms='sample',html_path=str(path)))
    run_generic_downstream(dbms='sample',documents=docs,profile=entry_profile(docs[0]),output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    rows=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    subject=[r for r in rows if r['name']=='TURN'];assert len(subject)==1
    description=json.dumps(subject[0]['semantics']['description'])
    assert 'short definition' in description and 'complete transformation' in description


@pytest.mark.parametrize('field,value',[('source_hash','forged'),('feature_type','DATATYPE'),('definition_targets',['https://official.test/reference#wrong'])])
def test_catalog_association_does_not_disable_owner_integrity(tmp_path,field,value):
    from agents.contracts.identity_credibility import evaluate_identity_credibility
    d=document(tmp_path,'Functions and Operators','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td><a href="#turn">TURN()</a></td><td>A short summary.</td></tr></table>'
        '<ul><li><a id="turn"></a><p><code>TURN(x)</code></p><p>Returns a rotation.</p></li></ul>')
    run(tmp_path,d,entry_profile(d));stage=tmp_path/'run'
    unit=json.loads((stage/'identity/groups.jsonl').read_text().splitlines()[0])
    owners=[json.loads(l)for l in(stage/'owners/owners.jsonl').read_text().splitlines()]
    docs=[json.loads(l)for l in(stage/'document_ir/documents.jsonl').read_text().splitlines()]
    bundle=json.loads((stage/'evidence/bundles.jsonl').read_text().splitlines()[0])
    block=next(b for b in bundle['source_blocks'] if b.get('catalog_reference_proof'))
    block['catalog_reference_proof'][field]=value
    result=evaluate_identity_credibility(unit,owners=owners,documents=docs,bundle=bundle)
    assert not result['owner_evidence_correct']


@pytest.mark.parametrize('mode,expected',[
    ('captured','FINAL_CAPTURED'),('lost','FINAL_FIELD_GAP'),
    ('moved','FINAL_ROLE_CHANGED_REVIEW'),('review','NOT_PUBLISHED_REVIEW')])
def test_explicit_field_accounting_never_silently_accepts_a_loss(mode,expected):
    from agents.semantic_stage.content_completeness import audit_profile_dispositions
    rows=[{'identity_group_id':'u','block_id':'b','role':'DESCRIPTION','value':'Official description.',
           'status':'CONSUMED','source_url':'https://official.test/reference','source_hash':'h'}]
    units=[{'identity_unit_id':'u','canonical_identity':'TURN','feature_type':'FUNCTION'}]
    record={'name':'TURN','feature_type':'function','provenance':{'identity_unit_id':'u'},'semantics':{}}
    if mode in {'captured','moved'}:
        record['semantics']['description' if mode=='captured' else 'syntax']=[{
            'value':'Official description.','evidence':{'block_id':'b'}}]
    result=audit_profile_dispositions(rows,units,[]if mode=='review'else[record])
    assert result[0]['disposition']==expected
