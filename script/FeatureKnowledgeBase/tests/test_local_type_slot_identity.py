"""Local declarations reach Identity and Type without a DB-specific vocabulary."""
import copy
import json

import pytest

from agents.owner.reference_definitions import catalog_names, reference_scopes, verified_reference
from agents.owner.scope_plan import compile_scope_plan
from agents.pipeline.generic_unified import _typed_block
from agents.type.evidence import build_typed_feature_evidence


@pytest.mark.parametrize('text,signature',[
    ('frob ( "any" ) → bigint','frob ( "any" )'),
    ('frob (x) WITHIN GROUP (ORDER BY y) → bigint','frob (x) WITHIN GROUP (ORDER BY y)'),
    ('frob (x) OVER (PARTITION BY y) → numeric','frob (x) OVER (PARTITION BY y)'),
])
def test_formal_quoted_types_and_ordered_set_forms(text,signature):
    from agents.contracts.definition_entry import callable_signature,compound_header_role
    assert callable_signature(text)==('FROB',signature,'bigint' if 'bigint' in text else 'numeric')
    assert compound_header_role('Function/Operator Description Example(s)','FUNCTION')
    assert compound_header_role('Function/Operator Description Example(s)','OPERATOR')
    assert not compound_header_role('Parameter Description','FUNCTION')
    assert callable_signature("frob('literal') → numeric") is None
    assert callable_signature('frob (x) prose about the result → numeric') is None


def row():
    return {'block_id':'r','block_kind':'TABLE_ROW','text':'moment(p) instant8 A temporal value.',
            'dom_path':'main > table > tbody > tr','order':0,'heading_ancestry':['Data Types'],
            'table_metadata':{'headers':['Name','Aliases','Description'],
                              'cells':['moment(p)','instant8','A temporal value.']}}


def test_type_projection_keeps_the_source_structure_needed_for_revalidation():
    b=row();doc={'blocks':[b],'url':'https://fixture.invalid/reference'}
    proof=reference_scopes(doc,{p['relation']:p for p in compile_scope_plan()})[0]
    typed=_typed_block(b)
    assert typed['table_metadata']==b['table_metadata']
    assert verified_reference(proof,typed,'MOMENT')
    context={'canonical_subject':'MOMENT','owner_id':'o','identity_unit_id':'u',
             'blocks':[typed],'definition_section_proofs':[proof]}
    votes=build_typed_feature_evidence(context)
    assert len(votes)==1 and votes[0]['candidate_type']=='DATATYPE'
    assert votes[0]['strength']=='STRONG'
    assert proof['source_role']=='CATALOG_ENTRY'
    changed=copy.deepcopy(typed);changed['heading_ancestry']=['Scalar Functions']
    assert verified_reference(proof,changed,'MOMENT') is None
    changed=copy.deepcopy(typed);changed['table_metadata']['cells'][0]='foreign'
    assert verified_reference(proof,changed,'MOMENT') is None


@pytest.mark.parametrize('heading',['Scalar Functions','Data Type Formatting Functions','Data Types / Examples',
                                   'Data Types / Connector Mappings','Data Types / API','Data Types / Parameters'])
def test_name_column_is_not_a_global_type_vote(heading):
    b=row();b['heading_ancestry']=[heading]
    assert catalog_names(b) is None


def test_same_name_function_and_type_have_distinct_final_subjects(tmp_path):
    from tests.test_paragraph_label_definition import doc
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    from agents.pipeline.generic_unified import run_generic_downstream
    d=doc(tmp_path,'<h2>Data Types</h2><table><thead><tr><th>Name</th><th>Aliases</th><th>Description</th></tr></thead>'
          '<tbody><tr><td>frob(p)</td><td>frob_alias</td><td>A fixed-precision storage value.</td></tr></tbody></table>'
          '<h2>Functions</h2><h3>FROB function</h3><p>The FROB function returns the supplied argument.</p>'
          '<h4>Syntax</h4><pre>FROB(x)</pre><h4>Example</h4><pre>VALUES FROB(2)</pre>', 'SQL Reference')
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='dual-proof')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    typed={(f['name'],f['feature_type']):f for f in fs}
    assert ('FROB','datatype') in typed and ('FROB','function') in typed
    assert {c['value'] for c in typed['FROB','datatype']['semantics']['description']}=={'A fixed-precision storage value.'}
    assert not typed['FROB','datatype']['semantics']['examples']
    assert typed['FROB','function']['semantics']['examples']
    assert all('storage value' not in c['value'] for c in typed['FROB','function']['semantics']['description'])


def test_compound_function_catalog_and_type_reference_are_not_mixed(tmp_path):
    from tests.test_paragraph_label_definition import doc
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    from agents.pipeline.generic_unified import run_generic_downstream
    d=doc(tmp_path,'<h2>Character Types</h2><table><tr><th>Name</th><th>Description</th></tr>'
          '<tr><td>frob</td><td>A storage value.</td></tr></table>'
          '<h2>Functions and Operators</h2><table><tr><th>Function/Operator Description Example(s)</th></tr>'
          '<tr><td><p><code>frob</code> (x) → numeric</p><p><code>frob</code> (x, y) → numeric</p>'
          '<p>Returns the chosen value.</p><p><code>frob(2)</code> → 2</p></td></tr></table>'
          '<h2>Function Output</h2><table><tr><th>Column Name</th><th>Data Type</th></tr>'
          '<tr><td>item</td><td>frob</td></tr><tr><td>other</td><td>reference_only</td></tr></table>', 'SQL Reference')
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='compound-dual')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    typed={(f['name'],f['feature_type']):f for f in fs}
    assert ('FROB','datatype') in typed and ('FROB','function') in typed
    assert not any(f['name']=='REFERENCE_ONLY' for f in fs)
    assert [c['value'] for c in typed['FROB','datatype']['semantics']['description']]==['A storage value.']
    assert len(typed['FROB','function']['semantics']['syntax'])==2
    assert len(typed['FROB','function']['semantics']['examples'])==1
    owners=[json.loads(l) for l in (tmp_path/'run/owners/owners.jsonl').read_text(encoding='utf8').splitlines()]
    compound=[p for o in owners for p in o.get('definition_section_proofs',[]) if p.get('reference_mode')=='COMPOUND_CATALOG_ROW']
    blocks={b['block_id']:b for b in d['blocks']}
    assert compound
    proof=json.loads(json.dumps(compound[0],sort_keys=True))
    assert verified_reference(proof,blocks[proof['heading_block_id']],'FROB')


def test_column_type_is_a_reference_not_a_second_type_definition():
    from agents.contracts.type_slots import datatype_usage_slot
    b={'block_id':'b','table_metadata':{'headers':['Column Name','Data Type'],'cells':['result_field','moment(p)']}}
    assert datatype_usage_slot(b,'MOMENT')['type_cell_index']==1
    assert not datatype_usage_slot(b,'RESULT_FIELD')
    assert not datatype_usage_slot(row(),'MOMENT')
    from agents.type.definition_roles import interpret_type_evidence
    unit={'identity_unit_id':'u','canonical_identity':'MOMENT'}
    votes=interpret_type_evidence([unit],[{'identity_unit_id':'u','candidate_type':'DATATYPE','block_id':'b'}],
                                 [{'page_id':'p','blocks':[b]}])
    assert votes[0]['definition_vote_eligible'] is False
    assert votes[0]['evidence_role']=='USAGE_REFERENCE'


def test_ordered_set_proof_survives_identity_surface_projection(tmp_path):
    from tests.test_paragraph_label_definition import doc
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    from agents.pipeline.generic_unified import run_generic_downstream
    from agents.contracts.definition_entry import callable_signature
    signature='frob (fraction numeric) WITHIN GROUP (ORDER BY value numeric)'
    assert callable_signature(signature)==('FROB',signature,'')
    d=doc(tmp_path,'<h2>Aggregate Functions</h2><table><tr><th>Function Description</th><th>Partial Mode</th></tr>'
          '<tr><td><p><code>'+signature+'</code> → numeric</p>'
          '<p>Returns an ordered value.</p></td><td>No</td></tr></table>','SQL Reference')
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='ordered-proof')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    f=next(f for f in fs if f['name']=='FROB' and f['feature_type']=='function')
    assert [c['value'] for c in f['semantics']['syntax']]==[signature]
    assert [c['value'] for c in f['semantics']['description']]==['Returns an ordered value.']


def test_compound_example_introduction_may_explain_the_case(tmp_path):
    from tests.test_paragraph_label_definition import doc
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    from agents.pipeline.generic_unified import run_generic_downstream
    d=doc(tmp_path,'<h2>Functions</h2><table><tr><th>Function Description</th></tr>'
          '<tr><td><p><code>frob</code> (x numeric) → numeric</p><p>Returns a value.</p>'
          '<p>For example, to select the supplied value:</p><pre>SELECT frob(2);</pre>'
          '<p>For example, to select another subject:</p><pre>SELECT foreign_subject(3);</pre>'
          '</td></tr></table>','SQL Reference')
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='example-introduction')
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',
                           semantic=True,semantic_provider='deterministic')
    fs=json.loads((tmp_path/'run/canonical/promotion_ready_kb.json').read_text(encoding='utf8'))
    f=next(f for f in fs if f['name']=='FROB' and f['feature_type']=='function')
    assert [c['value'] for c in f['semantics']['examples']]==['SELECT frob(2);']


def test_compound_parser_does_not_replace_other_definition_grammars():
    from agents.identity.production import _surface_records
    signature='frob([DISTINCT] expr) [ over_clause ]'
    block={'block_id':'b','block_kind':'LIST_ITEM','text':signature+' Returns the supplied value.'}
    proof={'definition_pattern':'SIGNATURE_LED_LIST_ITEM','feature_type':'FUNCTION',
           'definition_anchor_block_ids':['b'],'identity_values':['FROB'],'section_id':'s',
           'formal_signature_values':{'b':[signature]}}
    observed=_surface_records(block,page={},owner={'definition_section_proofs':[proof]},blocks=[block])
    assert [r['raw'] for r in observed]==[signature]
