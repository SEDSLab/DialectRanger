import copy
import json
from agents.semantic_stage.local_provider import _semantic_from_payload, DeterministicEvidenceClient
from agents.semantic_stage.pipeline import build_semantic_prompt
from agents.semantic_stage.extractive_fragments import validate_extractive_fragments


def block(ref,text,kind='TEXT',label=None,proof=True,order=0):
    return {'evidence_ref':ref,'block_id':ref,'text':text,'block_kind':kind,'source_order':order,
      'source_url':'https://official.test/ref','owner_ids':['owner:a'],'contributing_owner_ids':['owner:a'],
      'dom_path':'html > body > main > p','structural_roles':['DEFINITION'],
      'observed_context':{'raw_node_kind':'PRE' if kind=='CODE' else 'P','labels':[
        {'text':label,'origin':'DETAILS_SUMMARY'}] if label else [],'visible_label':label},
      'definition_section_proofs':[{'primary_identity':'FEATURE_A','identity_values':['FEATURE_A'],
         'identity_anchor_block_ids':{'FEATURE_A':['syntax']}}] if proof else []}


def extract(evidence,kind='FUNCTION',name='FEATURE_A',**kw):
    p={'identity_group_id':'unit:a','normalized_identity':name,'feature_type':kind,
       'structure_contract':{'version':'observable-evidence/v1'},'evidence':evidence,**kw}
    return _semantic_from_payload(p)


def test_short_multi_paragraph_description_order_dedupe_and_no_threshold():
    a=block('a','Adds.',order=1);b=block('b','Conditional limitation.',order=2)
    r=extract([b,a,a]);assert [c['text'] for c in r['description']]==['Adds.','Conditional limitation.']


def test_inline_syntax_value_not_metadata_or_description():
    s=block('s','Syntax: FEATURE_A(x)','LIST_ITEM','Syntax')
    s['observed_context']['labels']=[{'text':'Syntax','origin':'INLINE_LABEL','value_text':'FEATURE_A(x)'}]
    m=block('m','Function type: Scalar','LIST_ITEM','Function type')
    r=extract([s,m]);assert r['description'] is None
    assert [c['text'] for c in r['formal_syntax']]==['FEATURE_A(x)']


def test_explicit_example_wins_over_legacy_formal_and_result_table():
    c=block('e','SELECT FEATURE_A(1)','CODE','Example');c['structural_roles']=['FORMAL_SYNTAX']
    explanation=block('p','The following example uses a column.','TEXT','Example')
    result=block('r','1','TABLE_ROW','Example')
    intro=block('i','Returns the following:','TEXT','Example')
    r=extract([c,explanation,result,intro]);assert not r['formal_syntax'] and r['description'] is None
    assert [x['sql'] for x in r['official_examples']]==['SELECT FEATURE_A(1)']


def test_explicit_sql_statement_syntax_not_blanket_select_exclusion():
    r=extract([block('s','SELECT expression FROM relation','CODE','Syntax')])
    assert r['formal_syntax'][0]['text']=='SELECT expression FROM relation'
    assert not r['official_examples']


def test_unlabelled_query_not_syntax_just_because_name_occurs():
    r=extract([block('e','SELECT FEATURE_A(1)','CODE')]);assert not r['formal_syntax']


def test_no_example_invented_when_official_has_none():
    r=extract([block('d','Returns A.'),block('syntax','FEATURE_A(x)','CODE')])
    assert r['description'] and r['formal_syntax'] and not r['official_examples']


def test_bare_formal_syntax_with_explicit_label():
    b=block('s','FEATURE_A','CODE','Syntax');assert extract([b])['formal_syntax'][0]['text']=='FEATURE_A'


def test_text_expression_under_explicit_example_is_not_explanation():
    r=extract([block('e',"FEATURE_A(I) FEATURE_A(CAST(I AS BIGINT))",label='Example'),
               block('p','Use FEATURE_A(I) to compute a value.',label='Example')])
    assert len(r['official_examples'])==1


def test_multiple_multiline_examples_preserved():
    r=extract([block('e1','SELECT\n FEATURE_A(1)','CODE','Example'),block('e2','FEATURE_A(2)','CODE','Example')])
    assert len(r['official_examples'])==2
    assert '\n' in r['official_examples'][0]['sql']


def test_result_code_not_example_after_result_introduction():
    r=extract([block('e','FEATURE_A(1)','CODE','Example',order=1),
       block('p','Result:','TEXT','Example',order=2),block('r','FEATURE_A(1)','CODE','Example',order=3)])
    assert len(r['official_examples'])==1


def cell(i,header,text):
    return {'cell_index':i,'text':text,'dom_locator':'table > tr > td:nth-of-type('+str(i+1)+')',
      'header_role':'DATA_CELL','header_status':'OBSERVED_GRID','headers':[{'text':header,'dom_locator':'table > th'}]}


def row(ref,cells):
    b=block(ref,' '.join(c['text'] for c in cells),'TABLE_ROW')
    b['table_context']={'row_block_id':ref,'cells':cells,'shared_context':[]};return b


def test_operator_multiline_variants_reordered_columns_exact_cells():
    r1=row('r1',[cell(0,'Description','Add'),cell(1,'Operator','x + y')])
    r2=row('r2',[cell(0,'Description','Add time.'),cell(1,'Operator','timestamp_expr + interval_expr')])
    r=extract([r1,r2],'OPERATOR','+')
    assert [c['text'] for c in r['formal_syntax']]==['x + y','timestamp_expr + interval_expr']
    assert [c['text'] for c in r['description']]==['Add','Add time.']
    assert r['formal_syntax'][0]['source_locations'][0]['cell_index']==1


def test_operator_negated_rows_are_not_same_subject():
    a=row('a',[cell(0,'Operator','x IN (values)'),cell(1,'Description','Member')])
    b=row('b',[cell(0,'Operator','x NOT IN (values)'),cell(1,'Description','Not member')])
    r=extract([a,b],'OPERATOR','IN')
    assert [c['text'] for c in r['formal_syntax']]==['x IN (values)']
    assert r['semantic_extensions']['uncertain_fields']


def test_operator_fragment_does_not_get_redefined_in_semantic():
    b=row('b',[cell(0,'Operator','x IS NOT NULL'),cell(1,'Description','Not null')])
    r=extract([b],'OPERATOR','NOT NULL');assert not r['formal_syntax'] and not r['description']


def test_and_inside_between_is_not_boolean_and_definition():
    b=row('b',[cell(0,'Operator','x BETWEEN y AND z'),cell(1,'Description','Range')])
    assert not extract([b],'OPERATOR','AND')['formal_syntax']


def test_datatype_mapping_sidecar_not_return_or_syntax():
    b=row('d',[cell(0,'SQL type','TYPE_A'),cell(1,'Database runtime type','TYPE_B'),
        cell(2,'Default value *','0'),cell(3,'Notes','A restriction.')]);b['structural_roles']=['RETURN_TYPE']
    r=extract([b],'DATATYPE','TYPE_A');assert r['return_type'] is None and not r['formal_syntax']
    assert r['semantic_extensions']['datatype_mapping'][0]['text']=='TYPE_B'
    assert r['notes'][0]['text']=='A restriction.'
    assert r['description'] is None and not r['official_examples']
    assert r['semantic_extensions']['uncertain_fields'][0]['status']=='UNCERTAIN'


def test_table_header_or_unknown_header_never_becomes_field():
    b=row('b',[cell(0,'Operator','x + y'),cell(1,'Description','Add')])
    for c in b['table_context']['cells']:c['header_status']='UNKNOWN'
    assert not extract([b],'OPERATOR','+')['formal_syntax']


def test_foreign_datatype_row_not_assigned_to_current_identity():
    b=row('b',[cell(0,'SQL type','TYPE_B'),cell(1,'Native type','T')])
    assert not extract([b],'DATATYPE','TYPE_A')['semantic_extensions']['datatype_name']


def test_formal_alias_proof_consumed_without_new_alias_algorithm():
    b=block('syntax','{ FEATURE_B(x) } | { FEATURE_A(x) }','CODE')
    assert extract([b])['formal_syntax'][0]['text']==b['text']


def test_unlabelled_unproven_prose_stays_unknown_not_false_complete():
    r=extract([block('a','This might describe some feature.',proof=False)])
    assert r['description'] is None
    assert r['semantic_extensions']['role_decisions'][0]['role']=='UNKNOWN'


def test_prompt_roundtrip_production_client_and_fragment_grounding():
    b=block('d','Adds.')
    bundle={'structure_contract_version':'observable-evidence/v1','main_evidence':[b],'source_blocks':[b]}
    admission={'identity_group_id':'unit:a','normalized_identity':'FEATURE_A','feature_type':'FUNCTION'}
    client=DeterministicEvidenceClient()
    parsed=json.loads(client.call(build_semantic_prompt(bundle,admission),'unit:a')['_raw_response'])
    valid=validate_extractive_fragments(parsed,bundle)
    assert valid['fields']['description'][0]['text']=='Adds.'
    assert valid['rejected_claims']==0 and len(client.calls)==1


def test_sidecar_is_optional_legacy_compatibility_and_inputs_immutable():
    old={'evidence':[{'evidence_ref':'d','text':'Returns the value.','block_kind':'TEXT','section_contexts':[{'section_role':'DESCRIPTION'}]}]}
    before=copy.deepcopy(old)
    assert 'semantic_extensions' not in _semantic_from_payload(old)
    assert old==before


def test_input_omission_diagnostics_preserved_no_completeness_claim():
    omissions={'omitted_evidence':[{'evidence_ref':'missing','reason':'NOISE_FILTER'}]}
    r=extract([block('d','Adds.')],input_selection=omissions)
    assert r['semantic_extensions']['input_completeness']['input_selection']==omissions
    assert 'semantic_status' not in r


def test_navigation_source_excluded_even_with_legacy_definition_vote():
    b=block('nav','FEATURE_A returns A.');b['dom_path']='html > body > nav > p'
    assert extract([b])['description'] is None


def test_function_table_catalog_signature_and_description():
    b=row('r',[cell(0,'Function','FEATURE_A(x)'),cell(1,'Description','Adds.')])
    r=extract([b]);assert r['description'][0]['text']=='Adds.'
    assert r['formal_syntax'][0]['text']=='FEATURE_A(x)'


def test_postfix_type_constructor_grammar_and_literal_example_list():
    syntax=block('s',"baseType FEATURE_A [ '[' size ']' ]",'CODE')
    example=block('e','BOOLEAN FEATURE_A VARCHAR(100) FEATURE_A INTEGER FEATURE_A[10]',label='Example')
    r=extract([syntax,example],'DATATYPE')
    assert r['formal_syntax'][0]['text']==syntax['text']
    assert r['official_examples'][0]['sql']==example['text']


def test_uppercase_explanation_not_a_postfix_declaration():
    b=block('e','FEATURE_A RESULT EXPLANATION',label='Example')
    assert not extract([b],'DATATYPE')['official_examples']


def test_bounded_brace_declaration_proof_not_leading_name_regex():
    b=block('s','{ FEATURE_A | FEATURE_B } ( argument )','CODE')
    b['definition_section_proofs'][0].update(identity_anchor_block_ids={},
        definition_anchor_block_ids=['s'],boundary_evidence=['LOCAL_DECLARATION_GRAMMAR'])
    assert extract([b])['formal_syntax'][0]['text']==b['text']


def test_zero_argument_and_arithmetic_examples_have_literal_subject():
    a=block('a','FEATURE_A FEATURE_A(9)',label='Example')
    b=block('b','FEATURE_A(A, B) A / FEATURE_A(B, 0)',label='Example')
    c=block('c','THIS IS THE RESULT',label='Example')
    r=extract([a,b,c]);assert len(r['official_examples'])==2


def test_literal_alias_example_uses_existing_formal_alternatives():
    syntax=block('s','{ FEATURE_A | FEATURE_B }','CODE')
    syntax['definition_section_proofs'][0].update(identity_anchor_block_ids={},
        definition_anchor_block_ids=['s'],boundary_evidence=['LOCAL_DECLARATION_GRAMMAR'])
    r=extract([syntax,block('e','FEATURE_B',label='Example'),block('x','UNRELATED',label='Example')])
    assert [x['sql'] for x in r['official_examples']]==['FEATURE_B']
