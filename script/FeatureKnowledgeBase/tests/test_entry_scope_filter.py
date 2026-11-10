from tests.test_profile_definition_execution import document
from tests.test_authoritative_profile_fields import configured
from agents.owner.profile_execution import configured_entry_proofs
from agents.owner.definition_patterns import default_patterns


def test_nested_reference_removed_by_owner_filter_does_not_crash(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><p><code>OUTER(X)</code></p>'
        '<p>Returns a value.</p><ul><li><p><code>RELATED()</code> is a related operation.</p>'
        '</li></ul></li></ul>')
    p=configured(d);p['hint_contract']['definition_patterns']=default_patterns()
    plan=p['hint_contract']['definition_structure_patterns'][0]
    plan.update(structure='C',identity_slot='LEADING_TERM',boundary='CURRENT_ENTRY')
    plan['execution']['identity']={'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE',
        'text_pattern':r'([A-Z]+)\(.*\)','capture_group':1}],'cardinality':'ONE'}
    anchor=next(b for b in d['blocks']if b['block_kind']=='TEXT' and b['text']=='OUTER(X)')
    native=[{'heading_block_id':anchor['block_id'],'identity_values':['OUTER'],
             'block_ids':[b['block_id']for b in d['blocks']]}]
    diagnostics=[]
    proofs=configured_entry_proofs(d,plan,p,native,diagnostics)
    assert any(x['identity_values']==['OUTER']for x in proofs)
    assert not any('RELATED' in x['identity_values']for x in proofs)
    assert any(x.get('reason')=='ENTRY_ANCHOR_OUTSIDE_RETAINED_SCOPE'for x in diagnostics)
    assert all(x['heading_block_id']in x['block_ids'] and x['block_ids']for x in proofs)
