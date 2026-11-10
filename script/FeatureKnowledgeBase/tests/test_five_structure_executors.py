"""A--E execute the same source contracts across layouts, not DB names."""
import copy
import json

import pytest

from agents.owner.structure_execution import (
    compile_structure_plan, default_structure_patterns, execute_structures,
    verified_field_labels,
)
from agents.owner.generic import generate_owner_hypotheses
from agents.owner.source_relations import prepare_relation_documents
from agents.contracts.definition_unit import build_definition_units
from agents.profile_bootstrap.cold_start import build_cold_start_profile, validate_cold_start_profile
from tests.test_profile_definition_execution import document, run


def profile_for(d, families):
    p=build_cold_start_profile(dbms='sample',documents=[d],run_id='five-structures')
    p['hint_contract']['definition_structure_patterns']=[
        v for v in default_structure_patterns() if v['structure'] in families]
    return p


CASES=[
    ('A','TURN function','<h2>Syntax</h2><pre>TURN(x)</pre>'
        '<h2>Purpose</h2><p>Returns a rotation.</p>'
        '<h2>Examples</h2><pre>SELECT TURN(1);</pre>','TURN'),
    ('B','Functions','<h2>TURN</h2><h3>Syntax</h3><pre>TURN(x)</pre>'
        '<h3>Description</h3><p>Returns a rotation.</p>'
        '<h2>SHIFT</h2><pre>SHIFT(x)</pre><p>Returns a displacement.</p>','TURN'),
    ('C','Functions','<dl><dt>TURN(x)</dt><dd>Returns a rotation.</dd>'
        '<dt>SHIFT(x)</dt><dd>Returns a displacement.</dd></dl>','TURN'),
    ('D','Data Types','<table><tr><th>Type</th><th>Description</th></tr>'
        '<tr><td>WHOLE, EXACT</td><td>Exact values.</td></tr></table>','WHOLE'),
    ('E','Data Types','<p>The following data types are supported.</p><ul>'
        '<li><code>WHOLE</code></li><li><code>EXACT</code></li></ul>'
        '<p>These types share storage rules.</p>','WHOLE'),
]


@pytest.mark.parametrize('family,title,body,name',CASES)
def test_all_families_produce_common_source_contract(tmp_path,family,title,body,name):
    d=document(tmp_path,title,body)
    p=profile_for(d,family)
    original=copy.deepcopy(d);saved=copy.deepcopy(p)
    prepared=prepare_relation_documents([d],p)[0]
    result=execute_structures(prepared,p)
    assert result['proofs'],result
    assert set(result['counts'])=={family}
    proof=next(x for x in result['proofs'] if name in x['identity_values'])
    execution=proof['structure_execution']
    assert execution['status']=='VERIFIED_STRUCTURE'
    assert execution['source_hash']==d['page_hash']
    assert execution['publication_permission'] is False
    assert name in {m['identity'] for m in execution['declarations']}
    assert all(not m['alias_inferred'] for m in execution['declarations'])
    assert [m['block_id'] for m in execution['members']]==proof['block_ids']
    assert all(not m['semantic_claim'] for m in execution['members'])
    assert p==saved and d==original
    units=build_definition_units([prepared],generate_owner_hypotheses([prepared],p),p)
    assert any((u.get('structure_execution') or {}).get('structure')==family for u in units)
    if family=='E':
        assert any(m['role']=='SHARED_CONTEXT' for m in execution['members'])
    if family in {'B','C'}:
        assert not any('displacement' in b['text'] for b in d['blocks'] if b['block_id'] in proof['block_ids'])


@pytest.mark.parametrize('family,title,body,name',CASES)
def test_explicit_empty_plan_cannot_reactivate_legacy_scope(tmp_path,family,title,body,name):
    d=document(tmp_path,title,body)
    p=profile_for(d,[])
    prepared=prepare_relation_documents([d],p)[0]
    assert execute_structures(prepared,p)['proofs']==[]
    # This controls definition execution, not a global candidate blacklist.
    owners=generate_owner_hypotheses([prepared],p)
    assert not build_definition_units([prepared],owners,p)


@pytest.mark.parametrize('explicit', [False, True])
def test_heading_proof_without_legacy_pattern_builds_bounded_owner(tmp_path, explicit):
    d=document(tmp_path,'Functions',
        '<h2 id="turn">TURN</h2><pre>TURN(x)</pre><p>Returns a rotation.</p>'
        '<p>Example:</p><pre>TURN(2)</pre>'
        '<h2 id="shift">SHIFT</h2><pre>SHIFT(x)</pre><p>Returns a displacement.</p>')
    p=profile_for(d,'B')
    p['hint_contract']['definition_patterns']=[]
    p['hint_contract']['definition_scope_patterns']=[{
        'relation':'HEADING_SECTION','identity_slot':'HEADING',
        'boundary':'NEXT_PEER_OR_PARENT_HEADING'}]
    if explicit:
        p['hint_contract']['definition_structure_patterns'][0]['execution']={
            'version':1,'identity':{'alternatives':[{'kind':'HEADING'},{'kind':'VERIFIED_ANCHOR'}],
                                    'cardinality':'ONE_OR_MORE'},
            'fields':[{'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}},
                      {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    prepared=prepare_relation_documents([d],p)[0]
    proof=next(v for v in execute_structures(prepared,p)['proofs']
               if v['identity_values']==['TURN'])
    assert 'definition_pattern' not in proof
    owners=generate_owner_hypotheses([prepared],p)
    owner=next(o for o in owners if set(o['member_block_ids'])==set(proof['block_ids']))
    if explicit:
        assert owner['owner_kind']=='HeadingOwner'
        assert set(owner['identity_signals'])==set(proof['boundary_evidence'])
        assert 'NATIVE_LI_CONTAINMENT' not in owner['identity_signals']
    assert owner['evidence_scope_status']=='COMPLETE'
    assert not any('displacement' in b['text'] for b in d['blocks']
                   if b['block_id'] in owner['member_block_ids'])
    assert any(u['identity_values']==['TURN'] for u in build_definition_units([prepared],owners,p))


@pytest.mark.parametrize('bad',[
    None,{},['A'],[{'structure':'Z','identity_slot':'PAGE_SUBJECT','boundary':'SUBJECT_PAGE'}],
    [{'structure':'A','identity_slot':'ANY_WORD','boundary':'WHOLE_SITE'}],
    [dict(default_structure_patterns()[0],selector='anywhere')],
    [default_structure_patterns()[0],default_structure_patterns()[0]],
    [dict(default_structure_patterns()[0],field_labels={'DESCRIPTION':['Purpose'],'EXAMPLE':['Purpose']})],
])
def test_invalid_contracts_fail_explicitly(tmp_path,bad):
    d=document(tmp_path,'Functions','<p>Reference text.</p>')
    p=profile_for(d,'A')
    p['hint_contract']['definition_structure_patterns']=bad
    with pytest.raises(ValueError):validate_cold_start_profile(p)


def test_page_and_section_are_separately_selectable(tmp_path):
    d=document(tmp_path,'Functions',CASES[1][2])
    assert not execute_structures(d,profile_for(d,'A'))['proofs']
    assert execute_structures(d,profile_for(d,'B'))['proofs']


@pytest.mark.parametrize('tag',['li','div','section'])
def test_entry_recipe_is_not_tied_to_li(tmp_path,tag):
    body=''.join('<{0}><p><code>{1}(x)</code></p><p>Returns a value.</p></{0}>'.format(tag,n)
                 for n in ['TURN','SHIFT'])
    d=document(tmp_path,'Functions',body)
    p=profile_for(d,'C')
    from tests.test_definition_unit_execution import plan
    p['hint_contract']['definition_patterns']=[plan('CALLABLE_DEFINITION')]
    proofs=execute_structures(d,p)['proofs']
    assert {'TURN','SHIFT'}<={n for v in proofs for n in v['identity_values']}
    assert all(v['structure_execution']['structure']=='C' for v in proofs)


def test_custom_field_labels_reach_real_semantic_input(tmp_path):
    d=document(tmp_path,'TURN function','<h2>Syntax</h2><pre>TURN(x)</pre>'
        '<h2>Behavior</h2><p>Returns a rotation.</p><p>Null inputs stay null.</p>'
        '<h2>Worked queries</h2><pre>SELECT TURN(1);</pre>')
    p=profile_for(d,'A')
    p['hint_contract']['definition_structure_patterns'][0]['field_labels']={
        'DESCRIPTION':['Behavior'],'EXAMPLE':['Worked queries']}
    records=run(tmp_path,d,p)
    record=next(r for r in records if r['name']=='TURN' and r['feature_type']=='function')
    assert record['semantics']['examples']
    raw=[json.loads(l) for l in (tmp_path/'run/semantic_stage/semantic/semantic_raw_responses.jsonl').read_text(encoding='utf8').splitlines()]
    prompt=next(r['prompt'] for r in raw if 'INPUT:\n' in str(r.get('prompt')) and 'Worked queries' in r['prompt'])
    payload=json.loads(prompt.split('INPUT:\n')[1].split('\n\nOUTPUT SCHEMA:')[0])
    assert any(u.get('structure_execution') for u in payload['definition_units'])
    assert any(b.get('structure_field_roles') for b in payload['evidence'])


def test_copied_field_annotations_do_not_prove_a_role():
    b={'block_id':'code','source_hash':'h','source_url':'https://official.test/a',
       'structure_field_roles':[{'field_role':'EXAMPLE'}]}
    assert verified_field_labels(b,[],{},'TURN')==[]
    execution={'status':'VERIFIED_STRUCTURE','plan':dict(default_structure_patterns()[0],
        field_labels={'EXAMPLE':['Worked queries']}),
        'declarations':[{'identity':'TURN'}],
        'members':[{'block_id':'code','field_role':'EXAMPLE','role_label_block_id':'label'}]}
    unit={'source_hash':'h','source_url':b['source_url'],'member_evidence_refs':['code','label'],
          'structure_execution':execution}
    label={'block_kind':'HEADING','text':'Worked queries','source_hash':'h','source_url':b['source_url']}
    assert verified_field_labels(b,[unit],{'label':label},'TURN')==['examples']
    assert not verified_field_labels(b,[unit],{'label':dict(label,source_hash='foreign')},'TURN')
    assert not verified_field_labels(b,[unit],{'label':label},'SHIFT')


def test_compound_row_keeps_multiple_declarations_but_examples_are_subject_local(tmp_path):
    from agents.contracts.definition_entry import compound_entry
    from tests.test_compound_definition_entry import source
    html='<table><tr><th>Function Description Example(s)</th></tr><tr><td>' \
         '<p class="func_signature"><code>turn</code>(x) → numeric</p>' \
         '<p class="func_signature"><code>shift</code>(x) → numeric</p>' \
         '<p>Returns a transformed value.</p>' \
         '<p><code>turn(1)</code> → {1,2}</p><p><code>shift(2)</code> → red</p></td></tr></table>'
    _,_,row=source(html)
    for subject in ['TURN','SHIFT']:
        entry=compound_entry(row,subject,'FUNCTION')
        assert len(entry['signature_locators'])==1
        formal=[f['value'] for f in entry['fragments'] if f['role']=='FORMAL_SYNTAX']
        examples=[f['value'] for f in entry['fragments'] if f['role']=='EXAMPLE']
        assert len(formal)==len(examples)==1
        assert formal[0].lower().startswith(subject.lower())
        assert examples[0].lower().startswith(subject.lower())
    d=document(tmp_path,'Functions',html)
    p=profile_for(d,'D')
    proofs=execute_structures(d,p)['proofs']
    proof=next(v for v in proofs if set(v['identity_values'])=={'TURN','SHIFT'})
    ds=proof['structure_execution']['declarations']
    assert {x['identity'] for x in ds}=={'TURN','SHIFT'}
    assert all(x['formal_variants'] for x in ds)


def test_example_output_does_not_override_explicit_signature_markup():
    from agents.contracts.definition_entry import compound_entry
    from tests.test_compound_definition_entry import source
    html='<table><tr><th>Function Description</th></tr><tr><td>' \
         '<p class="func_signature"><code>turn</code>(anyenum) → anyenum</p>' \
         '<p>Returns an enum.</p><p><code>turn(NULL::palette)</code> → red</p></td></tr></table>'
    _,_,row=source(html)
    entry=compound_entry(row,'TURN','FUNCTION')
    assert [f['role'] for f in entry['fragments']]==['FORMAL_SYNTAX','DESCRIPTION','EXAMPLE']


def test_compound_operator_examples_require_exact_symbol_occurrence():
    from agents.contracts.definition_entry import compound_entry
    from tests.test_compound_definition_entry import source
    html='<table><tr><th>Operator Description</th></tr><tr><td>' \
         '<p><code>x &gt; y → boolean</code></p>' \
         '<p><code>x &gt;= y → boolean</code></p>' \
         '<p>Compare values.</p><p><code>2 &gt;= 1 → t</code></p></td></tr></table>'
    _,_,row=source(html)
    entry=compound_entry(row,'>','OPERATOR')
    assert entry is not None
    assert not any(f['role']=='EXAMPLE' for f in entry['fragments'])


def test_shared_member_source_is_retained_without_invented_individual_description(tmp_path):
    d=document(tmp_path,CASES[4][1],CASES[4][2])
    records=run(tmp_path,d,profile_for(d,'E'))
    for name in ['WHOLE','EXACT']:
        record=next(r for r in records if r['name']==name and r['feature_type']=='datatype')
        assert not record['semantics']['description']
        assert not record['semantics']['syntax']
    bundles=[json.loads(s) for s in (tmp_path/'run/evidence/bundles.jsonl').read_text(encoding='utf8').splitlines()]
    assert any(b.get('shared_definition_context') for bundle in bundles for b in bundle['source_blocks'])


def test_container_example_prefix_inside_syntax_does_not_become_formal_syntax(tmp_path):
    d=document(tmp_path,'WHOLE data type','<p>WHOLE provides exact numeric values.</p>'
        '<h2>Syntax</h2><pre>WHOLE[(p)]</pre>'
        '<div>For example: <pre>-- concrete use\nVALUES CAST(1 AS WHOLE);</pre></div>')
    # The reader now hashes frozen bytes, including Windows line endings.
    # Do not rewrite the source after its provenance has been recorded.
    records=run(tmp_path,d,profile_for(d,'A'))
    record=next(r for r in records if r['name']=='WHOLE' and r['feature_type']=='datatype')
    assert [v['value'] for v in record['semantics']['syntax']]==['WHOLE[(p)]']
    assert any('VALUES CAST' in v['value'] for v in record['semantics']['examples'])


def test_structurally_marked_grammar_and_overloads_share_one_identity(tmp_path):
    # Quoted terminals in formal SQL grammar must not be mistaken for a
    # concrete invocation. No feature-name list or same-name merge shortcut.
    d=document(tmp_path,'Functions','<table><tr><th>Function Description</th></tr>'
        '<tr><td><p class="func_signature"><code class="function">build_pair</code>'
        '([<em class="replaceable">key</em> { VALUE | \':\' } '
        '<em class="replaceable">value</em>])</p>'
        '<p>Constructs a value from pairs.</p></td></tr>'
        '<tr><td><p class="func_signature"><code class="function">build_pair</code>(keys, vals) → json</p>'
        '<p class="func_signature"><code class="function">build_binary_pair</code>(keys, vals) → binary</p>'
        '<p>Constructs values from arrays.</p></td></tr></table>')
    p=profile_for(d,'D')
    proofs=execute_structures(d,p)['proofs']
    assert any(v['identity_values']==['BUILD_PAIR'] for v in proofs)
    records=run(tmp_path,d,p)
    assert any(r['name']=='BUILD_PAIR' and r['feature_type']=='function' for r in records)
    record=next(r for r in records if r['name']=='BUILD_PAIR' and r['feature_type']=='function')
    assert any("VALUE | ':'" in v['value'] for v in record['semantics']['syntax'])
    groups=[json.loads(s) for s in (tmp_path/'run/identity/groups.jsonl').read_text(encoding='utf8').splitlines()]
    assert len([u for u in groups if u['canonical_identity']=='BUILD_PAIR'])==1


def test_quoted_formal_terminals_with_typed_return_are_not_invocations():
    from agents.contracts.definition_entry import observed_callable_declaration
    nodes=[{'element_name':'code','semantic_classes':['function'],'text':'build_pair'},
           {'element_name':'em','semantic_classes':['replaceable'],'text':'key'}]
    text="build_pair(key { VALUE | ':' } value) → json"
    assert observed_callable_declaration(text,nodes)==('BUILD_PAIR',"build_pair(key { VALUE | ':' } value)",'json')
    assert observed_callable_declaration(text,[]) is None


def test_inline_metavariable_does_not_hide_definition_prose():
    from agents.contracts.definition_entry import compound_entry
    from tests.test_compound_definition_entry import source
    html='<table><tr><th>Function Description Example(s)</th></tr><tr><td>' \
         '<p class="func_signature"><code>raise_value</code>(a,b) → numeric</p>' \
         '<p><code>a</code> raised to the power of <code>b</code></p>' \
         '<p><code>raise_value(2,3)</code> → 8</p></td></tr></table>'
    _,_,row=source(html)
    entry=compound_entry(row,'RAISE_VALUE','FUNCTION')
    assert [f['role'] for f in entry['fragments']]==['FORMAL_SYNTAX','DESCRIPTION','EXAMPLE']


@pytest.mark.parametrize('title,body',[
    ('Function Examples','<pre>SELECT TURN(1);</pre>'),
    ('API Functions','<h2>TURN function</h2><pre>TURN(x)</pre><p>Returns an object.</p>'),
    ('Functions','<p>The article mentions TURN(x) in passing.</p>'),
    ('Functions','<nav><h2>TURN</h2><p>Returns a value.</p></nav>'),
])
def test_profile_does_not_declare_unproven_subject(tmp_path,title,body):
    d=document(tmp_path,title,body)
    assert execute_structures(d,profile_for(d,'ABCDE'))['proofs']==[]
