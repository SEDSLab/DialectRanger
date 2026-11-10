import copy
import pytest
from tests.test_profile_definition_execution import document,run
from tests.test_subject_contract_repair import section_profile
from agents.owner.member_collections import PLAN,grammar_members,scopes,verified


def profile(d):
    p=section_profile(d);h=p['hint_contract'];h['member_collection_patterns']=[dict(PLAN)]
    h['definition_structure_patterns'].append({'structure':'E','identity_slot':'TYPED_MEMBER','boundary':'SHARED_PARENT'})
    h['definition_scope_patterns'].append({'relation':'GRAMMAR_MEMBER_CONTEXT','identity_slot':'GRAMMAR_MEMBER','boundary':'HEADING_SECTION'})
    return p


def reference(tmp_path):
    return document(tmp_path,'SQL Grammar','<h2 id="compare">Comparison</h2>'
        '<pre>&lt;= | &gt;= | = | &amp;&amp;</pre>'
        '<p>Comparison operators. The operator &amp;&amp; tests geometric overlap.</p>'
        '<p>Example:</p><pre>x &lt;= 7</pre>'
        '<h2 id="next">Next rule</h2><p>Must never leak.</p>')


def test_collection_members_are_separate_features_and_sharing_is_local(tmp_path):
    d=reference(tmp_path);rows=run(tmp_path,d,profile(d));m={r['name']:r for r in rows}
    assert {'<=','>=','=','&&'}<=set(m)
    for name in ['<=','>=','=']:
        assert m[name]['feature_type']=='operator'
        descriptions=[c['value'] for c in m[name]['semantics']['description']]
        assert 'Comparison operators.' in descriptions
        assert not any('geometric' in s or 'Must never leak' in s for s in descriptions)
    assert any('geometric overlap' in c['value'] for c in m['&&']['semantics']['description'])
    assert m['<=']['semantics']['examples']
    assert not m['>=']['semantics']['examples']
    assert not m['=']['semantics']['syntax']  # A catalog symbol is not a full signature.


@pytest.mark.parametrize('grammar,expected',[
    ('term [ { { * | / } term } [...] ]',['*','/']),
    ('value [ { || value } [...] ]',['||']),
    ('factor [ { { + | - } factor } [...] ]',['+','-']),
])
def test_infix_member_grammar_ignores_bnf_delimiters(grammar,expected):
    assert grammar_members(grammar,'SQL Grammar / Calculation','A value or numeric calculation.')==expected


@pytest.mark.parametrize('grammar,context',[
    ('<> | =','SQL Grammar / Literals'),
    ('SELECT a + b FROM t','SQL Grammar / Example'),
    ('operand [ "+" ]','SQL Grammar / Quoted Name'),
    ('TURN(x)','Functions'),
    ('x + 7','SQL Grammar / Example'),
    ('+ | -','Developer API'),
])
def test_non_definition_and_quoted_grammar_do_not_create_members(grammar,context):
    assert not grammar_members(grammar,context,'')


def test_profile_must_explicitly_enable_both_structures(tmp_path):
    d=reference(tmp_path);p=profile(d);p['hint_contract']['definition_structure_patterns']=p['hint_contract']['definition_structure_patterns'][:1]
    assert not scopes(d,p)
    assert not scopes(d,section_profile(d))


@pytest.mark.parametrize('bad',['member','text','hash','heading','claims'])
def test_member_proof_cannot_be_forged(tmp_path,bad):
    d=reference(tmp_path);proof=copy.deepcopy(scopes(d,profile(d))[0]);b=next(b for b in d['blocks'] if b['block_id']==proof['heading_block_id']);name=proof['primary_identity']
    assert verified(proof,b,name)
    if bad=='member':proof['member_collection']['members'].append('>>')
    elif bad=='text':proof['anchor_citation']['text']='>>'
    elif bad=='hash':proof['anchor_citation']['source_hash']='0'*64
    elif bad=='heading':proof['member_collection']['parent_heading']['dom_path']='html > body > main > h2#next'
    else:proof['member_collection']['claims'][0]['value']='A fabricated definition.'
    assert verified(proof,b,name) is None
