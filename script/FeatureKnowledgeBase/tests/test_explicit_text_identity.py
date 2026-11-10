import copy
import pytest
from agents.owner.profile_execution import explicit_declarations,validate_locator,applies
from tests.test_authoritative_profile_fields import configured
from tests.test_profile_definition_execution import document,run
from agents.owner.scope_plan import default_scope_patterns

def test_literal_token_extraction_retains_compound_operator():
    names=explicit_declarations('-> ->> IS NOT DISTINCT FROM',{},
        {'token_pattern':r'IS NOT DISTINCT FROM|[->]+'},{'declaration_kind':'OPERATOR'})
    assert [r['identity']for r in names]==['->','->>','IS NOT DISTINCT FROM']

def test_text_slot_requires_explicit_pattern():
    with pytest.raises(ValueError):validate_locator({'kind':'FIRST_BLOCK_TEXT'},identity=True,structure='C')

def test_heading_levels_do_not_overlap():
    p={'execution':{'identity':{'alternatives':[{'kind':'HEADING','heading_level':3}]}}}
    assert not applies(p,{'block_kind':'HEADING','heading_level':4})
    assert applies(p,{'block_kind':'HEADING','heading_level':3})

def test_definition_term_bold_text_reaches_final(tmp_path):
    d=document(tmp_path,'Function reference','<dl><dt><p><b>sample_call(X)</b></p></dt>'
        '<dd><p>Returns the argument without modification.</p><pre>SELECT sample_call(2);</pre></dd></dl>')
    p=configured(d);p['hint_contract']['definition_scope_patterns']=default_scope_patterns()
    a=p['hint_contract']['definition_structure_patterns'][0]
    a.update(structure='C',identity_slot='LEADING_TERM',boundary='CURRENT_ENTRY')
    a['execution']['identity']={'alternatives':[{'kind':'FIRST_BLOCK_TEXT','text_pattern':r'[A-Za-z_]\w*\s*\(.*\)',
        'token_pattern':r'[A-Za-z_]\w*(?=\s*\()'}],'cardinality':'ONE_OR_MORE'}
    rows=run(tmp_path,d,p)
    feature=next(r for r in rows if r['name']=='SAMPLE_CALL')
    assert feature['feature_type']=='function'
    assert any('Returns the argument' in str(x)for x in feature['semantics']['description'])

def test_single_list_signature_without_body_is_not_dropped(tmp_path):
    from agents.owner.definition_patterns import default_patterns
    d=document(tmp_path,'Function reference','<ol><li>sample_call(X)</li></ol>')
    p=configured(d);p['hint_contract']['definition_patterns']=default_patterns()
    a=p['hint_contract']['definition_structure_patterns'][0]
    a.update(structure='C',identity_slot='LEADING_TERM',boundary='CURRENT_ENTRY')
    a['execution']['identity']={'alternatives':[{'kind':'FIRST_BLOCK_TEXT',
        'text_pattern':r'([A-Za-z_]\w*)\s*\(.*\)','capture_group':1}],'cardinality':'ONE'}
    rows=run(tmp_path,d,p)
    assert any(r['name']=='SAMPLE_CALL' and r['feature_type']=='function'for r in rows)

def test_definition_title_is_not_exported_as_formal_syntax(tmp_path):
    d=document(tmp_path,'Reference','<h2>2. SAMPLE_CALL(X) function</h2><p>Returns the argument.</p>')
    p=configured(d);a=p['hint_contract']['definition_structure_patterns'][0]
    a.update(structure='B',identity_slot='HEADING',boundary='NEXT_PEER_OR_PARENT_HEADING')
    a['execution']['identity']={'alternatives':[{'kind':'HEADING','heading_level':2,
        'text_pattern':r'2\. ([A-Z_]\w*)\(X\) function','capture_group':1}],'cardinality':'ONE'}
    a['execution']['fields'].append({'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}})
    rows=run(tmp_path,d,p)
    feature=next(r for r in rows if r['name']=='SAMPLE_CALL')
    assert not any('2.' in str(x) or 'function' in str(x.get('value',''))for x in feature['semantics']['syntax'])
