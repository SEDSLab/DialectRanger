import copy
import json
import pytest
from agents.semantic_stage.structured_semantics import extract_structured_semantics


def payload(kind='FUNCTION', name='FROB', label='FROB()'):
    b={'block_id':'b','evidence_ref':'b','text':label+' Official description.',
       'block_kind':'TABLE_ROW','owner_local_binding':True,'identity_unit_id':'u',
       'source_structure_status':'RESOLVED','source_hash':'hash','source_url':'https://official.invalid/ref',
       'heading_ancestry':[{'FUNCTION':'Functions','OPERATOR':'Operators','DATATYPE':'Data Types'}[kind]],
       'table_context':{'cells':[
           {'text':label,'cell_index':0,'dom_locator':'table > tr > th','header_role':'ROW_HEADER',
            'column_start':0,'column_end':0,'rowspan':1,'headers':[]},
           {'text':'Official description.','cell_index':1,'dom_locator':'table > tr > td',
            'header_role':'DATA_CELL','header_status':'OBSERVED_GRID','headers':[{'text':'Description'}]}]}}
    return {'identity_group_id':'u','normalized_identity':name,'feature_type':kind,'evidence':[b]}


@pytest.mark.parametrize('kind,name,label',[('FUNCTION','FROB','FROB()'),('OPERATOR','%%','%%'),('DATATYPE','MOMENT','MOMENT')])
def test_description_does_not_require_formal_syntax(kind,name,label):
    result=extract_structured_semantics(payload(kind,name,label))
    assert result['description'][0]['text']=='Official description.'
    assert result['description'][0]['source_locations'][0]['cell_index']==1
    assert not result['formal_syntax'] and not result['official_examples']


@pytest.mark.parametrize('failure',['foreign_identity','foreign_unit','nonlocal','example','parameter','api','profile_only'])
def test_no_foreign_or_nonreference_description(failure):
    p=payload();b=p['evidence'][0]
    if failure=='foreign_identity':p['normalized_identity']='OTHER'
    if failure=='foreign_unit':b['identity_unit_id']='foreign'
    if failure=='nonlocal':b['owner_local_binding']=False
    if failure=='example':b['heading_ancestry']=['Functions / Examples']
    if failure=='parameter':b['heading_ancestry']=['Function Parameters']
    if failure=='api':b['heading_ancestry']=['Developer API Functions']
    if failure=='profile_only':b['heading_ancestry']=[];b['profile_hint']={'surface_role':'FEATURE_CATALOG'}
    assert not extract_structured_semantics(p)['description']


def test_empty_description_stays_empty_and_no_fake_fields():
    p=payload();p['evidence'][0]['table_context']['cells'][1]['text']=''
    r=extract_structured_semantics(p)
    assert r['description'] is None and not r['formal_syntax'] and not r['official_examples']


def test_prompt_preserves_catalog_binding_and_budget_required_member():
    from agents.semantic_stage.pipeline import build_semantic_prompt
    p=payload();b=p['evidence'][0]
    filler=[{'block_id':str(i),'text':'Supplement','block_kind':'TEXT'} for i in range(81)]
    bundle={'identity_unit_id':'u','normalized_identity':'FROB','main_evidence':filler,'source_blocks':[b]}
    prompt=build_semantic_prompt(bundle,{'identity_group_id':'u','normalized_identity':'FROB','feature_type':'FUNCTION'})
    data=json.loads(prompt.split('INPUT:\n')[1].split('\n\nOUTPUT SCHEMA:')[0])
    actual=next(r for r in data['evidence'] if r['evidence_ref']=='b')
    assert actual['owner_local_binding'] is True
    assert data['input_selection']['required_catalog_evidence_refs']==['b']
    assert extract_structured_semantics(data)['description'][0]['text']=='Official description.'
