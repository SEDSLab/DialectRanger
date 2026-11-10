import copy
import json
import pytest
from tests.test_profile_definition_execution import document,run
from tests.test_five_structure_executors import profile_for
from agents.owner.profile_execution import compile_execution,field_candidates
from agents.owner.structure_execution import execute_structures
from tests.test_five_structure_executors import CASES


def configured(d,family='D'):
    p=profile_for(d,family)
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'COLUMN_HEADER','headers':['Declaration']}],
                                  'cardinality':'ONE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Behavior']}},
                  {'role':'CONSTRUCTION_EXAMPLE','locator':{'kind':'COLUMN_HEADER','headers':['Build from values']}}]}
    return p


def test_custom_second_column_and_field_names_are_executed(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Behavior</th><th>Declaration</th><th>Build from values</th></tr>'
        '<tr><td>Returns a rotation.</td><td>TURN(x)</td><td>TURN(2)</td></tr></table>')
    p=configured(d)
    proof=execute_structures(d,p)['proofs']
    assert any(x['identity_values']==['TURN'] for x in proof)
    rows=run(tmp_path,d,p)
    row=next(r for r in rows if r['name']=='TURN')
    assert [v['value'] for v in row['semantics']['description']]==['Returns a rotation.']
    assert any('TURN(2)' in str(v) for v in row['semantics']['examples'])


def test_configured_identity_no_match_cannot_fallback(tmp_path):
    d=document(tmp_path,'Data Types','<table><tr><th>Type</th><th>Description</th></tr>'
        '<tr><td>WHOLE</td><td>Exact values.</td></tr></table>')
    assert not execute_structures(d,configured(d))['proofs']
    # The existing empty-input contract stops Semantic explicitly; it must not
    # manufacture an adapted identity to keep the run going.
    with pytest.raises(ValueError,match='No identity groups found'):
        run(tmp_path,d,configured(d))
    assert not (tmp_path/'run/identity/groups.jsonl').read_text(encoding='utf8').strip()


def test_applicability_preserves_uncontrolled_legacy_rows(tmp_path):
    d=document(tmp_path,'Data Types','<table><tr><th>Type</th><th>Description</th></tr>'
        '<tr><td>WHOLE</td><td>Exact values.</td></tr></table>')
    p=configured(d)
    p['hint_contract']['definition_structure_patterns'][0]['execution']['when']={'table_headers_any':['Declaration']}
    result=execute_structures(d,p)
    assert result['proofs'] and not result['controlled_block_ids']
    assert all(x['structure_execution']['schema_version']=='StructureExecution/v1' for x in result['proofs'])


@pytest.mark.parametrize('change',[
    lambda e:e.update(selector='td:first-child'),
    lambda e:e['identity'].update(cardinality='ANY'),
    lambda e:e['fields'].append(copy.deepcopy(e['fields'][0])),
])
def test_unsupported_or_conflicting_contract_rejected(tmp_path,change):
    d=document(tmp_path,'Functions','<p>Reference</p>');p=configured(d)
    plan=p['hint_contract']['definition_structure_patterns'][0];change(plan['execution'])
    with pytest.raises(ValueError):compile_execution(plan)


def test_source_or_contract_forgery_not_a_claim():
    unit={'definition_unit_id':'u','structure_execution':{'schema_version':'StructureExecution/v2',
        'plan':{},'profile_execution_digest':'forged'}}
    candidates,diagnostics=field_candidates(unit,{},'TURN')
    assert not candidates and diagnostics[0]['reason']=='PROFILE_EXECUTION_DIGEST_MISMATCH'


@pytest.mark.parametrize('family,title,body,name',CASES)
def test_five_adapters_can_carry_same_explicit_contract(tmp_path,family,title,body,name):
    from agents.owner.source_relations import prepare_relation_documents
    d=document(tmp_path,title,body);p=profile_for(d,family)
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'VERIFIED_ANCHOR'}],'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    prepared=prepare_relation_documents([d],p)[0]
    result=execute_structures(prepared,p)
    assert any(name in x['identity_values'] for x in result['proofs']),result
    assert all(x['structure_execution']['schema_version']=='StructureExecution/v2' for x in result['proofs'])


def test_field_hash_and_owner_locality_are_required():
    cfg={'version':1,'identity':{'alternatives':[{'kind':'VERIFIED_ANCHOR'}],'cardinality':'ONE'},
         'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    plan={'structure':'C','execution':cfg}
    unit={'definition_unit_id':'u','source_hash':'actual','source_url':'official',
          'identity_values':['TURN'],'binding_status':'COMPLETE','member_evidence_refs':['b'],
          'structure_execution':{'schema_version':'StructureExecution/v2','plan':plan,
              'profile_execution_digest':compile_execution(plan)['digest'],'field_bindings':cfg['fields'],
              'declarations':[]}}
    block={'block_id':'b','block_kind':'TEXT','text':'Foreign description',
           'source_structure_status':'RESOLVED','source_hash':'foreign','source_url':'official','owner_local_binding':True}
    assert not field_candidates(unit,{'b':block},'TURN')[0]
    block.update(source_hash='actual',owner_local_binding=False)
    assert not field_candidates(unit,{'b':block},'TURN')[0]


def test_two_identity_columns_do_not_silently_choose_first(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Declaration</th><th>Other declaration</th><th>Behavior</th></tr>'
        '<tr><td>TURN(x)</td><td>SHIFT(x)</td><td>Different subjects.</td></tr></table>')
    p=configured(d)
    p['hint_contract']['definition_structure_patterns'][0]['execution']['identity']['alternatives'][0]['headers'].append('Other declaration')
    assert not execute_structures(d,p)['proofs']


def test_overlapping_field_positions_are_reviewed_not_reinterpreted():
    cfg={'version':1,'identity':{'alternatives':[{'kind':'VERIFIED_ANCHOR'}],'cardinality':'ONE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Behavior']}},
                  {'role':'EXAMPLE','locator':{'kind':'COLUMN_HEADER','headers':['Behavior','Details']}}]}
    plan={'structure':'D','execution':cfg}
    unit={'definition_unit_id':'u','source_hash':'h','source_url':'official','identity_values':['TURN'],
        'binding_status':'COMPLETE','member_evidence_refs':['b'],
        'structure_execution':{'schema_version':'StructureExecution/v2','plan':plan,
            'profile_execution_digest':compile_execution(plan)['digest'],'field_bindings':cfg['fields'],'declarations':[]}}
    block={'block_id':'b','block_kind':'TABLE_ROW','text':'TURN(x) Returns a rotation.',
        'source_structure_status':'RESOLVED','source_hash':'h','source_url':'official','owner_local_binding':True,
        'table_context':{'cells':[{'cell_index':1,'text':'Returns a rotation.','dom_locator':'row > td',
            'header_status':'EXPLICIT','headers':[{'text':'Behavior'}]}]}}
    candidates,checks=field_candidates(unit,{'b':block},'TURN')
    assert len(candidates)==2 and all(x['sharing']=='CONFLICT' for x in candidates)


def test_configured_slot_cannot_use_itself_as_fallback():
    from agents.owner.profile_execution import verified_configured,PATTERN_MODE
    b={'block_id':'b','text':'Not a declaration','dom_path':'table > tr','block_kind':'TABLE_ROW'}
    proof={'heading_block_id':'b','reference_mode':PATTERN_MODE,'definition_pattern':'REFERENCE_DECLARATION_SECTION',
        'identity_values':['TURN'],'feature_type':'FUNCTION','source_citations':{'b':copy.deepcopy(b)},
        'configured_identity_plan':{'alternatives':[{'kind':'COLUMN_HEADER','headers':['Name']},
                                                    {'kind':'VERIFIED_ANCHOR'}],'cardinality':'ONE'}}
    assert verified_configured(proof,b,'TURN') is None


def test_reserved_sql_word_can_be_callable_but_statement_is_not():
    from agents.owner.profile_execution import declarations
    block={'heading_ancestry':['Functions']}
    assert declarations('INSERT(str,pos,len,newstr)',block,'Function')[0]['feature_type']=='FUNCTION'
    assert not declarations('SELECT INSERT(str,1,2,newstr)',block,'Function')
    assert not declarations('TURN(x)',{'heading_ancestry':['Operators']},'Name')
    assert declarations('TURN(x)',{'heading_ancestry':['Operators']},'Function')
    assert not declarations('TURN()',{'heading_ancestry':['Functions and Operators']},'Name')
    assert declarations('TURN()',{'heading_ancestry':['Functions and Operators']},'Function')


def test_optional_prefix_datatype_keeps_declared_syntax(tmp_path):
    d=document(tmp_path,'Data Types','<ul><li><p><code>[GLOBAL] PIECE(M)</code></p>'
               '<p>Stores a piece.</p></li></ul>')
    p=profile_for(d,'C')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'},{'kind':'VERIFIED_ANCHOR'}],
                                  'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}},
                  {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    rs=run(tmp_path,d,p)
    row=next(r for r in rs if r['name']=='PIECE')
    assert row['feature_type']=='datatype'
    assert [x['value']for x in row['semantics']['syntax']]==['[GLOBAL] PIECE(M)']


def test_explicit_first_cell_handles_html_row_header(tmp_path):
    d=document(tmp_path,'Functions','<table><thead><tr><th>Declaration</th><th>Behavior</th></tr></thead>'
        '<tbody><tr><th scope="row">TURN(x)</th><td>Returns a rotation.</td></tr></tbody></table>')
    p=configured(d)
    p['hint_contract']['definition_structure_patterns'][0]['execution']['identity']['alternatives'].append({'kind':'FIRST_DATA_CELL'})
    row=next(r for r in run(tmp_path,d,p)if r['name']=='TURN')
    assert row['feature_type']=='function'
    assert [x['value']for x in row['semantics']['description']]==['Returns a rotation.']


def test_custom_catalog_link_preserves_same_subject_definition(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Behavior</th><th>Declaration</th></tr>'
        '<tr><td>Short rotation summary.</td><td><a href="#turn">TURN(x)</a></td></tr></table>'
        '<h2 id="turn">TURN(x)</h2><p>Returns a complete rotation.</p><pre>TURN(x)</pre>')
    p=profile_for(d,'BD')
    next(v for v in p['hint_contract']['definition_structure_patterns'] if v['structure']=='D')['execution']=configured(d)['hint_contract']['definition_structure_patterns'][0]['execution']
    rs=run(tmp_path,d,p)
    rows=[r for r in rs if r['name']=='TURN']
    assert len(rows)==1
    descriptions=[x['value'] for x in rows[0]['semantics']['description']]
    assert 'Returns a complete rotation.' in descriptions and 'Short rotation summary.' in descriptions


def test_identity_anchor_can_also_be_verified_definition_prose(tmp_path):
    d=document(tmp_path,'Data Types','<p>The WHOLE data type stores whole numbers.</p>')
    p=profile_for(d,'C')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'VERIFIED_ANCHOR'}],'cardinality':'ONE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    row=next(r for r in run(tmp_path,d,p)if r['name']=='WHOLE')
    assert [x['value']for x in row['semantics']['description']]==['The WHOLE data type stores whole numbers.']


def test_configured_second_identity_column_wins_over_legacy_name_column(tmp_path):
    d=document(tmp_path,'Functions','<table><tr><th>Name</th><th>Declaration</th><th>Behavior</th></tr>'
        '<tr><td>OTHER(x)</td><td>TURN(x)</td><td>Returns a rotation.</td></tr></table>')
    rs=run(tmp_path,d,configured(d))
    assert any(r['name']=='TURN' for r in rs)
    assert not any(r['name']=='OTHER' for r in rs)


def test_datatype_example_does_not_redeclare_subject_as_function(tmp_path):
    d=document(tmp_path,'Data Types','<ul><li><p><code>PIECE(M,D)</code></p><p>Stores a piece.</p>'
        '<pre>CREATE TABLE t(x PIECE(5,2));</pre></li></ul>')
    p=profile_for(d,'C')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'},{'kind':'VERIFIED_ANCHOR'}],
                                  'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}},
                  {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}},
                  {'role':'EXAMPLE','locator':{'kind':'OWNER_SQL_APPLICATION'}}]}
    rs=run(tmp_path,d,p)
    assert any(r['name']=='PIECE' and r['feature_type']=='datatype' for r in rs)
    decisions=[json.loads(l)for l in(tmp_path/'run/type/type_decisions.jsonl').read_text(encoding='utf8').splitlines()]
    assert not any(r.get('feature_type')=='FUNCTION' for r in decisions)


def test_field_binding_recipe_order_does_not_reorder_description(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p>'
        '<p>Returns a rotation.</p><pre>TURN(x) = x + 1</pre><p>Returns a longer rotation.</p></li></ul>')
    p=profile_for(d,'C')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'}],'cardinality':'ONE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'DEFINITION_EXPRESSION'}},
                  {'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    row=next(r for r in run(tmp_path,d,p)if r['name']=='TURN')
    assert [x['value']for x in row['semantics']['description']]==[
        'Returns a rotation.','TURN(x) = x + 1','Returns a longer rotation.']


@pytest.mark.parametrize('tag,container',[('li','LIST_ITEM'),('div','REPEATED_CONTAINER'),('section','REPEATED_CONTAINER')])
def test_profile_executes_variadic_entry_without_description_verb_whitelist(tmp_path,tag,container):
    body=''.join('<'+tag+'><p><code>'+name+'(<em class="replaceable">x</em>, ...)</code></p>'
                 '<p>With two inputs the selected transformation applies.</p></'+tag+'>' for name in ['TURN','SHIFT'])
    d=document(tmp_path,'Functions',body if tag!='li' else '<ul>'+body+'</ul>')
    p=profile_for(d,'C');p['hint_contract']['definition_patterns']=[{
        'grammar':'DATATYPE_DECLARATION','container':container,'identity_slot':'LEADING_LITERAL','boundary':'CURRENT_CONTAINER'}]
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'}],'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}},
                  {'role':'FORMAL_SYNTAX','locator':{'kind':'DECLARATION_ANCHORS'}}]}
    rs=run(tmp_path,d,p)
    for name in ['TURN','SHIFT']:
        row=next(r for r in rs if r['name']==name)
        assert row['semantics']['description'] and row['semantics']['syntax']


def test_profile_entry_does_not_prove_bibliographic_metadata(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(<em class="replaceable">x</em>, ...)</code></p>'
        '<p>Authors: A. Writer</p><p>Publisher: A. Press</p></li></ul>')
    p=profile_for(d,'C');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'}],'cardinality':'ONE_OR_MORE'},'fields':[]}
    assert not any(x.get('reference_mode')=='PROFILE_EXECUTED_ENTRY' for x in execute_structures(d,p)['proofs'])


@pytest.mark.parametrize('heading',['Functions','Parameters','Query results'])
def test_type_column_outside_datatype_reference_cannot_prove_datatype(heading):
    from agents.owner.profile_execution import declarations
    assert not declarations('OBJECT',{'heading_ancestry':[heading]},'Type')
    assert declarations('WHOLE',{'heading_ancestry':['Data Types']},'Type')[0]['feature_type']=='DATATYPE'


def test_frame_clause_word_is_not_operator_declaration():
    from agents.owner.profile_execution import declarations
    assert not declarations('PRECEDING',{'heading_ancestry':['Window frame specification']},'Name')


def test_mixed_row_keeps_independent_function_and_operator_subjects(tmp_path):
    d=document(tmp_path,'Functions and Operators','<table><tr><th>Function</th><th>Operator</th><th>Description</th></tr>'
        '<tr><td>TURN(x)</td><td>~</td><td>Applies a transformation.</td></tr></table>')
    p=profile_for(d,'D')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'COLUMN_HEADER','headers':['Function','Operator']}],
                                  'cardinality':'ONE_OR_MORE'},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'COLUMN_HEADER','headers':['Description']},
                   'sharing':'DECLARED_MEMBERS'}]}
    rows=run(tmp_path,d,p)
    assert any(r['name']=='TURN' and r['feature_type']=='function' for r in rows)
    assert any(r['name']=='~' and r['feature_type']=='operator' for r in rows)


def test_profile_named_anchor_role_is_source_verified(tmp_path):
    d=document(tmp_path,'Functions and Operators','<ul><li><a id="predicate_turn"></a><p>'
        '<code>TURN(<em class="replaceable">x</em>)</code></p><p>Tests the supplied input.</p></li></ul>')
    p=profile_for(d,'C')
    p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'}],'cardinality':'ONE'},
        'anchor_roles':{'OPERATOR':['predicate_']},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    proofs=execute_structures(d,p)['proofs']
    assert any(x.get('feature_type')=='OPERATOR' and x.get('configured_anchor_role') for x in proofs)
    assert any(r['name']=='TURN' and r['feature_type']=='operator' for r in run(tmp_path,d,p))


def test_explicit_function_column_preserves_default_argument_signature():
    from agents.owner.profile_execution import declarations
    assert declarations('TURN(x, size = 8192)',{'heading_ancestry':['Approximate Aggregates']},'Function')==[
        {'identity':'TURN','feature_type':'FUNCTION','formal_value':'TURN(x, size = 8192)'}]
    assert not declarations('TURN(1, 8192)',{'heading_ancestry':['Examples']},'Example')


def test_actual_document_title_guides_row_without_local_headings(tmp_path):
    d=document(tmp_path,'Text Types','<table><tr><th>Name</th><th>Description</th></tr>'
        '<tr><td>PIECE(n)</td><td>A variable length piece.</td></tr></table>')
    for b in d['blocks']:b['heading_ancestry']=[]
    d['title']='Text Types'  # Still present in the actual frozen H1.
    p=configured(d);p['hint_contract']['definition_structure_patterns'][0]['execution']['identity']['alternatives'][0]['headers']=['Name']
    rs=run(tmp_path,d,p)
    assert any(r['name']=='PIECE' and r['feature_type']=='datatype' for r in rs)
    assert not any(r['name']=='PIECE' and r['feature_type']=='function' for r in rs)


def test_matching_anchor_type_keeps_complete_nested_native_scope(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><a id="function_turn"></a><p><code>TURN(x)</code></p>'
        '<p>Returns a rotation.</p><ul><li><p>Null inputs preserve null values.</p></li></ul></li></ul>')
    p=profile_for(d,'C');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'},{'kind':'VERIFIED_ANCHOR'}],
        'cardinality':'ONE_OR_MORE'},'anchor_roles':{'FUNCTION':['function_']},
        'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    row=next(r for r in run(tmp_path,d,p)if r['name']=='TURN')
    assert any('Null inputs preserve null values.' in c['value'] for c in row['semantics']['description'])


def test_nested_independent_definition_is_not_outer_description(tmp_path):
    d=document(tmp_path,'Functions','<ul><li><p><code>TURN(x)</code></p><p>Returns a rotation.</p>'
        '<ul><li><p><code>SHIFT(x)</code></p><p>Returns a displacement.</p></li></ul></li></ul>')
    p=profile_for(d,'C');p['hint_contract']['definition_structure_patterns'][0]['execution']={
        'version':1,'identity':{'alternatives':[{'kind':'FIRST_PARAGRAPH_CODE'},{'kind':'VERIFIED_ANCHOR'}],
        'cardinality':'ONE_OR_MORE'},'fields':[{'role':'DESCRIPTION','locator':{'kind':'BODY_PROSE'}}]}
    row=next(r for r in run(tmp_path,d,p)if r['name']=='TURN')
    assert 'displacement' not in json.dumps(row['semantics']['description'])


@pytest.mark.parametrize('adjacent',[True,False])
def test_example_result_pair_requires_same_row_physical_adjacency(tmp_path,adjacent):
    d=document(tmp_path,'Functions','<table><tr><th>Function</th><th>Example</th>'+
        ('' if adjacent else '<th>Note</th>')+'<th>Result</th></tr><tr><td>TURN(x)</td><td>TURN(2)</td>'+
        ('' if adjacent else '<td>A separate note.</td>')+'<td>3</td></tr></table>')
    p=configured(d);plan=p['hint_contract']['definition_structure_patterns'][0]
    plan['execution']['identity']['alternatives'][0]['headers']=['Function']
    plan['execution']['fields']=[{'role':role,'locator':{'kind':'COLUMN_HEADER','headers':[header]}}
                               for role,header in [('EXAMPLE','Example'),('EXAMPLE_RESULT','Result')]]
    row=next(r for r in run(tmp_path,d,p)if r['name']=='TURN')
    values=[c['value']for c in row['semantics']['examples']]
    assert ('TURN(2)\n3' in values)==adjacent
