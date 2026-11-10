import copy
import pytest
from agents.owner.typed_page import literal_heading, datatype_metadata
from agents.owner.profile_execution import compile_execution
from agents.owner.structure_execution import execute_structures
from agents.owner.definition_patterns import verified_pattern
from tests.test_profile_definition_execution import document, run
from tests.test_subject_contract_repair import section_profile


@pytest.mark.parametrize('separator',[' or ', ', ', ', and ', ' | '])
def test_literal_heading_permalinks_and_complete_member_lists(separator):
    b={'block_kind':'HEADING','text':'ONE'+separator+'TWO #',
       'inline_nodes':[{'element_name':'code','text':'ONE'}, {'element_name':'code','text':'TWO'}]}
    assert literal_heading(b)
    assert not literal_heading(dict(b,text='Checking '+b['text']))
    assert not literal_heading(dict(b,text='ONE and OTHER #'))


def test_code_type_heading_and_alias_keep_separate_identity_proofs(tmp_path):
    d=document(tmp_path,'Data Types','<h2><code>WHOLE</code> or <code>EXACT</code>'
        '<a class="headerlink" href="#type">#</a></h2><p>A numeric representation.</p>')
    p=section_profile(d)
    proofs=execute_structures(d,p)['proofs']
    proof=next(v for v in proofs if set(v['identity_values'])=={'WHOLE','EXACT'})
    anchor=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id'])
    assert verified_pattern(proof,anchor,'WHOLE')['feature_type']=='DATATYPE'
    rows=run(tmp_path,d,p)
    assert {'WHOLE','EXACT'}<={r['name'] for r in rows if r['feature_type']=='datatype'}


def test_scoped_operator_grammar_disambiguates_mixed_reference_title(tmp_path):
    d=document(tmp_path,'Functions and Operators','<h2><code>IS EMPTY</code> and <code>IS NOT EMPTY</code>'
        '<a class="headerlink" href="#empty">#</a></h2><p>Tests whether an operand is empty.</p>')
    p=section_profile(d);cfg=p['hint_contract']['definition_structure_patterns'][0]['execution']
    cfg.update(declaration_kind='OPERATOR',when={'page_path_prefixes':['/reference']})
    rows=run(tmp_path,d,p)
    assert {'IS EMPTY','IS NOT EMPTY'}<={r['name'] for r in rows if r['feature_type']=='operator'}


def test_hint_cannot_override_a_conflicting_definition_context(tmp_path):
    d=document(tmp_path,'Functions','<h2><code>SPIN</code></h2><p>Returns a rotation.</p>')
    p=section_profile(d);cfg=p['hint_contract']['definition_structure_patterns'][0]['execution']
    cfg.update(declaration_kind='DATATYPE',when={'page_path_prefixes':['/reference']})
    assert not any(v['feature_type']=='DATATYPE' for v in execute_structures(d,p)['proofs'])


def test_unscoped_grammar_and_function_override_are_rejected(tmp_path):
    d=document(tmp_path,'Types','');plan=section_profile(d)['hint_contract']['definition_structure_patterns'][0]
    plan['execution']['declaration_kind']='DATATYPE'
    with pytest.raises(ValueError,match='scoped heading'):compile_execution(plan)
    plan['execution'].update(declaration_kind='FUNCTION',when={'page_path_prefixes':['/reference']})
    with pytest.raises(ValueError,match='scoped heading'):compile_execution(plan)


def test_multiple_title_types_need_their_own_page_metadata(tmp_path):
    d=document(tmp_path,'SmallFloat | LargeFloat Types',
        '<p>Documentation for floating-point data types: SmallFloat and LargeFloat.</p>'
        '<p>Numeric storage formats.</p>')
    h=next(b for b in d['blocks'] if b['block_kind']=='HEADING')
    meta=datatype_metadata(h)
    assert [v['identity'] for v in meta['declarations']]==['SMALLFLOAT','LARGEFLOAT']
    assert all(v['formal_value'] is None for v in meta['declarations'])
    p=section_profile(d)
    rows=run(tmp_path,d,p)
    assert {'SMALLFLOAT','LARGEFLOAT'}<={r['name'] for r in rows if r['feature_type']=='datatype'}


def test_unrelated_metadata_does_not_certify_multiple_names(tmp_path):
    d=document(tmp_path,'SmallFloat | Other Types',
        '<p>Documentation for floating-point data types: SmallFloat and LargeFloat.</p>')
    h=next(b for b in d['blocks'] if b['block_kind']=='HEADING')
    assert datatype_metadata(h) is None


def test_plain_operator_heading_requires_its_own_declaration_paragraph(tmp_path):
    d=document(tmp_path,'Functions and Operators','<h2>IS BRIGHT and IS NOT BRIGHT</h2>'
        '<p>The <code>IS [NOT] BRIGHT</code> operators test a light value.</p>')
    p=section_profile(d);cfg=p['hint_contract']['definition_structure_patterns'][0]['execution']
    cfg.update(declaration_kind='OPERATOR',when={'page_path_prefixes':['/reference']})
    rows=run(tmp_path,d,p)
    assert {'IS BRIGHT','IS NOT BRIGHT'}<={r['name'] for r in rows if r['feature_type']=='operator'}


def test_plain_heading_and_example_mention_are_not_an_operator_proof(tmp_path):
    d=document(tmp_path,'Functions and Operators','<h2>DETAILS</h2>'
        '<p>Details about usage.</p><h3>Examples</h3>'
        '<p>The <code>DETAILS</code> operator appears in this example.</p>')
    p=section_profile(d);cfg=p['hint_contract']['definition_structure_patterns'][0]['execution']
    cfg.update(declaration_kind='OPERATOR',when={'page_path_prefixes':['/reference']})
    assert not any('DETAILS' in v['identity_values'] for v in execute_structures(d,p)['proofs'])


def test_predicate_operands_do_not_become_separate_declared_subjects(tmp_path):
    d=document(tmp_path,'Functions and Operators','<h2>IS TRUE, IS FALSE, and IS UNKNOWN</h2>'
        '<p>The <code>IS [NOT] TRUE</code>, <code>IS [NOT] FALSE</code>, and '
        '<code>IS [NOT] UNKNOWN</code> operators test a boolean operand. '
        'Unlike <code>TRUE</code> and <code>FALSE</code>, these are predicates.</p>')
    p=section_profile(d);cfg=p['hint_contract']['definition_structure_patterns'][0]['execution']
    cfg.update(declaration_kind='OPERATOR',when={'page_path_prefixes':['/reference']})
    rows=run(tmp_path,d,p);names={r['name'] for r in rows}
    assert {'IS TRUE','IS FALSE','IS UNKNOWN'}<=names
    assert not names&{'TRUE','FALSE','UNKNOWN'}


def test_sidebar_names_do_not_create_competing_owners(tmp_path):
    d=document(tmp_path,'Functions','<nav><ul><li>WHOLE</li><li>SPIN(x)</li></ul></nav>'
        '<h2>SPIN(x)</h2><p>Returns a rotation.</p>')
    from agents.owner.generic import generate_owner_hypotheses
    p=section_profile(d);owners=generate_owner_hypotheses([d],p)
    nav={b['block_id'] for b in d['blocks'] if ' > nav > ' in b.get('dom_path','')}
    assert nav
    assert not any(nav&set(o['member_block_ids']) for o in owners)


def test_signature_term_does_not_inherit_the_same_named_datatype(tmp_path):
    d=document(tmp_path,'Functions and Data Types','<h2>Data Types</h2><h3><code>ALLOY</code></h3>'
        '<p>A storage representation.</p><h2>Functions</h2><dl><dt class="sig sig-object">'
        '<span class="sig-name">alloy</span>(<em>x</em>)<span class="sig-return"> → alloy</span>'
        '<a class="headerlink" href="#alloy">#</a></dt><dd><p>Converts a value.</p></dd></dl>')
    from agents.owner.profile_execution import declarations
    b=next(b for b in d['blocks'] if b.get('source_node_kind')=='DT')
    assert declarations(b['text'],b)==[{'identity':'ALLOY','feature_type':'FUNCTION','formal_value':'alloy ( x )'}]
    p=section_profile(d)
    c=copy.deepcopy(p['hint_contract']['definition_structure_patterns'][0])
    c.update(structure='C',identity_slot='LEADING_TERM',boundary='CURRENT_ENTRY')
    c['execution']['identity']['alternatives']=[{'kind':'VERIFIED_ANCHOR'}]
    p['hint_contract']['definition_structure_patterns'].append(c)
    p['hint_contract']['definition_scope_patterns'].append({'relation':'TERM_BODY_PAIR','identity_slot':'TERM','boundary':'NEXT_TERM'})
    rows=run(tmp_path,d,p)
    assert {r['feature_type'] for r in rows if r['name']=='ALLOY'}=={'datatype','function'}
