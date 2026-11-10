"""Heading wrappers are presentation, not new subjects or permission to publish."""
import json
import pytest
from agents.owner.definition_scope import navigation_block, normalized_name, heading_container
from agents.owner.structure_execution import execute_structures
from tests.test_profile_definition_execution import document, run
from tests.test_subject_contract_repair import section_profile


@pytest.mark.parametrize('label', ['8.2.1. TURN()', '8 . 2 . 1 . TURN()', '12) TURN()'])
def test_chapter_number_is_not_identity(label):
    assert normalized_name(label) == 'TURN'
    assert normalized_name('HASH64()') == 'HASH64'
    assert normalized_name('64-bit') == '64-BIT'


@pytest.mark.parametrize('prefix,expected', [
    ('html > body > header', True),
    ('html > body > main > header', True),
    ('html > body > main > section#one > header', False),
    ('html > body > main > nav > section#one > header', True),
    ('html > body > main > section.sidebar > header', True),
])
def test_body_header_and_navigation_are_distinct(prefix, expected):
    b={'dom_path':prefix+' > h2', 'block_kind':'HEADING'}
    assert navigation_block(b) is expected


def test_wrapped_definition_range_excludes_peer(tmp_path):
    d=document(tmp_path,'Functions',
        '<section id="turn"><header><h2><span>8<span>.</span>2<span>.</span>1</span>. '
        '<code>TURN()</code></h2></header><p>A rotation.</p><pre>TURN(x)</pre>'
        '<h3>Examples</h3><pre>SELECT TURN(1);</pre></section>'
        '<section id="shift"><header><h2>8.2.2. SHIFT()</h2></header>'
        '<p>A displacement.</p><pre>SHIFT(x)</pre></section>')
    p=section_profile(d)
    proof=next(v for v in execute_structures(d,p)['proofs'] if v['primary_identity']=='TURN')
    blocks=[b for b in d['blocks'] if b['block_id'] in proof['block_ids']]
    assert any(b['text']=='A rotation.' for b in blocks)
    assert any(b['text']=='SELECT TURN(1);' for b in blocks)
    assert not any('SHIFT' in b['text'] or 'displacement' in b['text'] for b in blocks)
    heading=blocks[0]
    assert heading_container(heading).endswith('section#turn')
    result=run(tmp_path,d,p)
    r=next(v for v in result if v['name']=='TURN')
    assert r['semantics']['description'] and r['semantics']['syntax'] and r['semantics']['examples']
    assert 'displacement' not in json.dumps(r['semantics'])


def test_navigation_cannot_borrow_section_declaration(tmp_path):
    d=document(tmp_path,'Functions','<header><h2>FAKE()</h2><pre>FAKE(x)</pre>'
        '<p>Returns a fake value.</p></header><section id="turn"><header><h2>TURN()</h2>'
        '</header><pre>TURN(x)</pre><p>Returns a rotation.</p></section>')
    proofs=execute_structures(d,section_profile(d))['proofs']
    assert any(p['primary_identity']=='TURN' for p in proofs)
    assert not any(p['primary_identity']=='FAKE' for p in proofs)


def test_inline_label_value_is_not_description_or_next_field(tmp_path):
    d=document(tmp_path,'Functions','<section id="turn"><header><h2>1 . 2 . TURN()</h2></header>'
        '<p>A rotation.</p><p><span class="title">Result type</span> '
        '<span class="para">DOUBLE PRECISION</span></p>'
        '<p><span class="title">Syntax</span><span class="para"><pre>TURN(x)</pre></span></p>'
        '<p><b>Example</b> SELECT TURN(1);</p></section>')
    p=section_profile(d)
    fields=p['hint_contract']['definition_structure_patterns'][0]['execution']['fields']
    fields.append({'role':'FORMAL_SYNTAX','locator':{'kind':'SECTION_LABEL','labels':['Syntax']}})
    fields.append({'role':'RETURN_TYPE','locator':{'kind':'SECTION_LABEL','labels':['Result type']}})
    rows=run(tmp_path,d,p);r=next(v for v in rows if v['name']=='TURN')
    descriptions=[x['value'] for x in r['semantics']['description']]
    assert descriptions==['A rotation.']
    assert [x['value'] for x in r['semantics']['examples']]==['SELECT TURN(1);']


def test_unmarked_prose_is_not_inline_field(tmp_path):
    from agents.owner.profile_execution import inline_field_label
    d=document(tmp_path,'Functions','<p>Result type depends on configuration.</p>')
    b=next(b for b in d['blocks'] if b['block_kind']=='TEXT')
    assert inline_field_label(b,{'result type'}) is None


def test_parameter_cells_are_not_residual_description(tmp_path):
    d=document(tmp_path,'Functions','<section><header><h2>TURN()</h2></header><p>A rotation.</p>'
        '<pre>TURN(x)</pre><table><tr><th>Parameter</th><th>Description</th></tr>'
        '<tr><td>x</td><td>A numeric argument.</td></tr></table></section>')
    rows=run(tmp_path,d,section_profile(d));r=next(v for v in rows if v['name']=='TURN')
    assert [c['value'] for c in r['semantics']['description']]==['A rotation.']


def test_publication_collapse_is_a_health_issue_not_admission_override(tmp_path):
    from agents.pipeline.orchestrator import definition_publication_health
    stage=tmp_path/'unified_pipeline'
    for name,rows in [('type/type_decisions.jsonl',[{'identity_unit_id':'one','primary_type':'FUNCTION'}]),
        ('canonical/admission_decisions.jsonl',[{'identity_unit_id':'one','reason_codes':['INDEPENDENT_SUBJECT_TYPE_PROOF_MISSING']}])]:
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf8')
    (tmp_path/'kb').mkdir();final=tmp_path/'kb/final_feature_kb.json';final.write_text('[]')
    assert definition_publication_health(tmp_path)['issues'][0]['code']=='TYPE_PUBLICATION_COLLAPSE'
    final.write_text('[{"feature_type":"function"}]')
    assert not definition_publication_health(tmp_path)['issues']


def test_unexecuted_heading_has_explicit_diagnostic(tmp_path):
    d=document(tmp_path,'Functions','<section><header><h2>TURN()</h2></header>'
        '<p>See another page for details.</p></section>')
    result=execute_structures(d,section_profile(d))
    attempts=[v for r in result['diagnostics'] for v in r.get('heading_attempts',[])]
    assert any(v['normalized_identity']=='TURN' and v['reason']=='LOCAL_DEFINITION_NOT_PROVEN' for v in attempts)


def test_coarse_ir_section_ids_do_not_merge_distinct_definitions(tmp_path):
    from agents.owner.generic import generate_owner_hypotheses
    from agents.contracts.definition_unit import build_definition_units
    d=document(tmp_path,'Functions','<section id="turn"><header><h2>TURN()</h2></header>'
        '<p>Returns a rotation.</p><pre>TURN(x)</pre></section>'
        '<section id="shift"><header><h2>SHIFT()</h2></header>'
        '<p>Returns a displacement.</p><pre>SHIFT(x)</pre></section>')
    for b in d['blocks']:b['section_id']=d['page_id']+'#document'
    p=section_profile(d)
    units=build_definition_units([d],generate_owner_hypotheses([d],p),p)
    assert len(units)==2
    assert all(u['status']=='VERIFIED_STRUCTURE' for u in units)
    assert {tuple(u['identity_values']) for u in units}=={('TURN',),('SHIFT',)}


def test_subject_named_numbered_examples_are_fields_not_new_definitions(tmp_path):
    d=document(tmp_path,'Functions','<section id="turn"><header><h2>7 . 2 . TURN()</h2></header>'
        '<p>A rotation.</p><pre>TURN(x)</pre><section id="turn-example"><header>'
        '<h3>7 . 2 . 1 . TURN Examples</h3></header><pre>SELECT TURN(1);</pre></section></section>'
        '<section id="shift"><header><h2>7 . 3 . SHIFT()</h2></header><p>A displacement.</p>'
        '<pre>SHIFT(x)</pre></section>')
    rows=run(tmp_path,d,section_profile(d));r=next(v for v in rows if v['name']=='TURN')
    assert [c['value'] for c in r['semantics']['description']]==['A rotation.']
    assert [c['value'] for c in r['semantics']['examples']]==['SELECT TURN(1);']
    assert not any(v['name']=='TURN EXAMPLES' for v in rows)


def test_foreign_subject_examples_do_not_extend_a_definition(tmp_path):
    d=document(tmp_path,'Functions','<section><header><h2>TURN()</h2></header>'
        '<pre>TURN(x)</pre><p>A rotation.</p><section><header><h3>SHIFT Examples</h3></header>'
        '<pre>SELECT SHIFT(1);</pre></section></section>')
    proofs=execute_structures(d,section_profile(d))['proofs']
    assert not any(p['primary_identity']=='TURN' for p in proofs)


def test_return_type_table_does_not_declare_a_second_section_subject(tmp_path):
    d=document(tmp_path,'Functions','<section><header><h2>TURN()</h2></header>'
        '<p>Returns a rotation.</p><pre>TURN(x)</pre><p>The result type depends on the argument:</p>'
        '<table><tr><td><p>DECIMALFLOAT(16)</p></td><td>DECIMALFLOAT(34)</td></tr></table>'
        '<section><header><h3>TURN Examples</h3></header><pre>SELECT TURN(1);</pre></section></section>')
    p=section_profile(d);proofs=execute_structures(d,p)['proofs']
    proof=next(v for v in proofs if v['primary_identity']=='TURN')
    assert proof['identity_values']==['TURN']
    rows=run(tmp_path,d,p)
    assert any(r['name']=='TURN' and r['feature_type']=='function' for r in rows)


def test_matching_table_signature_remains_definition_evidence(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN</h2><table><tr><td><pre>TURN(x)</pre></td>'
        '</tr></table><p>A rotation.</p>')
    rows=run(tmp_path,d,section_profile(d))
    r=next(r for r in rows if r['name']=='TURN')
    assert r['feature_type']=='function'
    assert [c['value'] for c in r['semantics']['syntax']]==['TURN(x)']


def test_foreign_table_signature_cannot_prove_heading(tmp_path):
    d=document(tmp_path,'Functions','<h2>TURN</h2><table><tr><td><pre>SHIFT(x)</pre></td>'
        '</tr></table><p>See the comparison table.</p>')
    assert not any(p['primary_identity']=='TURN' for p in execute_structures(d,section_profile(d))['proofs'])


def test_page_layout_table_is_not_a_nested_field_table(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><td><div><h2>TURN</h2>'
        '<pre>{TURN | SPIN}(x)</pre><p>A rotation.</p>'
        '<h2>SHIFT</h2><pre>SHIFT(x)</pre><p>A displacement.</p></div></td></tr></table>')
    proofs=execute_structures(d,section_profile(d))['proofs']
    assert any('TURN' in p['identity_values'] for p in proofs)
    assert any('SHIFT' in p['identity_values'] for p in proofs)


@pytest.mark.parametrize('label',['Examples of TURN','Example for TURN()','7 . 1 . Examples of TURN'])
def test_subject_named_field_prefix(label):
    from agents.owner.definition_scope import heading_field_label
    assert heading_field_label(label,'TURN',{'example','examples'}) in {'example','examples'}
    assert heading_field_label(label,'SHIFT',{'example','examples'}) is None


def test_explicit_syntax_disambiguates_explanatory_formula(tmp_path):
    d=document(tmp_path,'Functions','<section><header><h2>TURN()</h2></header>'
        '<p>Decimal (base-10) logarithm.</p><p><span class="title">Syntax</span>'
        '<span class="para"><div><pre>TURN(x)</pre></div></span></p>'
        '<p>The result can also be calculated as:</p><pre>SHIFT(x) / OTHER(x)</pre>'
        '<section><header><h3>Examples of TURN</h3></header><pre>SELECT TURN(1);</pre></section></section>')
    proofs=execute_structures(d,section_profile(d))['proofs']
    proof=next(p for p in proofs if p['primary_identity']=='TURN')
    assert proof['identity_values']==['TURN']
    assert not any(p['primary_identity'] in {'DECIMAL','SHIFT','OTHER'} for p in proofs)


def test_example_subsection_does_not_capture_seealso_or_formal_grammar(tmp_path):
    d=document(tmp_path,'Functions','<section><header><h2>TURN()</h2></header>'
        '<p>A rotation.</p><p><span class="title">Syntax</span><span class="para">'
        '<div><pre>TURN(x)\n&lt;x&gt; ::= SELECT | VALUE</pre></div></span></p>'
        '<section><header><h3>Examples of TURN</h3></header><pre>SELECT TURN(1);</pre>'
        '<p><span class="title">Related material</span><span>SHIFT</span></p></section>'
        '<p>See also SHIFT.</p></section>')
    p=section_profile(d)
    p['hint_contract']['definition_structure_patterns'][0]['execution']['fields'] += [
        {'role':'FORMAL_SYNTAX','locator':{'kind':'SECTION_LABEL','labels':['Syntax']}},
        {'role':'EXAMPLE','locator':{'kind':'OWNER_SQL_APPLICATION'}}]
    rows=run(tmp_path,d,p);r=next(v for v in rows if v['name']=='TURN')
    assert [v['value'] for v in r['semantics']['examples']]==['SELECT TURN(1);']
    assert any('::=' in v['value'] for v in r['semantics']['syntax'])
