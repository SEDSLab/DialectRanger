import copy
import json
import pytest

from agents.contracts.field_evidence import build_field_evidence
from agents.semantic_stage.pipeline import build_semantic_prompt
from agents.semantic_stage.structured_semantics import extract_structured_semantics
from agents.semantic_stage.extractive_fragments import validate_extractive_fragments


def bundle(header='Function -> Returns'):
    labels=[(header,'TURN(x) -> integer'),('Description','Returns a rotation.'),
            ('Example','SELECT TURN(1);')]
    cells=[{'cell_index':i,'text':value,'dom_locator':'main > table > tr > td:'+str(i),
            'headers':[{'text':label}],'header_status':'EXPLICIT','header_role':'DATA_CELL'}
           for i,(label,value) in enumerate(labels)]
    block={'block_id':'b','evidence_ref':'b','block_kind':'TABLE_ROW',
           'source_structure_status':'RESOLVED','source_hash':'h','source_url':'https://official.invalid/reference',
           'identity_unit_id':'u','owner_local_binding':True,'owner_ids':['o'],
           'dom_path':'main > table > tr','heading_ancestry':['Functions'],
           'text':' '.join(c['text'] for c in cells),'table_context':{'cells':cells}}
    b={'identity_group_id':'u','identity_unit_id':'u','feature_type':'FUNCTION','normalized_identity':'TURN',
       'source_blocks':[block],'main_evidence':[block]}
    b['field_evidence']=build_field_evidence(b)
    return b


def payload(b):
    prompt=build_semantic_prompt(b,{'identity_group_id':'u','normalized_identity':'TURN','feature_type':'FUNCTION'})
    return json.loads(prompt.split('INPUT:\n',1)[1].split('\n\nOUTPUT SCHEMA:',1)[0])


@pytest.mark.parametrize('header',['Function -> Returns','Function \u2192 Returns','Signature -> Return'])
def test_combined_column_consumes_verified_fields_without_second_header_guess(header):
    b=bundle(header);p=payload(b);parsed=extract_structured_semantics(p)
    result=validate_extractive_fragments(parsed,b)
    assert result['fields']['description'][0]['text']=='Returns a rotation.'
    assert result['fields']['formal_syntax'][0]['text']=='TURN(x)'
    assert result['fields']['return_type']['text']=='integer'
    assert result['fields']['official_examples'][0]['sql']=='SELECT TURN(1);'
    consumed=parsed['semantic_extensions']['verified_field_consumption']
    assert {'description','formal_syntax','return_type','official_examples'} <= {f['field'] for f in consumed if f['status']=='CONSUMED'}


def test_required_field_survives_supplemental_budget():
    b=bundle();b['main_evidence']=[{'block_id':'extra'+str(i),'text':'Supplement','block_kind':'TEXT'} for i in range(85)]
    p=payload(b)
    assert p['input_selection']['required_field_evidence_refs']==['b']
    assert any(e['evidence_ref']=='b' for e in p['evidence'])
    assert extract_structured_semantics(p)['description'][0]['text']=='Returns a rotation.'


def test_absent_precomputed_witnesses_are_reconstructed_at_transport():
    b=bundle();b.pop('field_evidence')
    parsed=extract_structured_semantics(payload(b))
    r=validate_extractive_fragments(parsed,b)
    assert r['fields']['description'][0]['text']=='Returns a rotation.'
    assert r['fields']['formal_syntax'][0]['text']=='TURN(x)'


@pytest.mark.parametrize('nonlocal_subject',[False,True])
def test_example_pairs_do_not_upgrade_unproven_subject(nonlocal_subject):
    b=bundle();block=b['source_blocks'][0]
    block['table_context']['cells'].append({'cell_index':3,'text':'1','headers':[{'text':'Result'}],
        'header_status':'EXPLICIT','header_role':'DATA_CELL','dom_locator':'main > table > tr > td:3'})
    block['text']+=' 1'
    if nonlocal_subject:block['owner_local_binding']=False
    else:b['normalized_identity']='SHIFT'
    assert not any(f['proof_level']=='VERIFIED' for f in build_field_evidence(b))


def test_shared_members_cannot_borrow_another_members_example():
    b=bundle();block=b['source_blocks'][0]
    c=block['table_context']['cells'][2];c['text']='SELECT SHIFT(1);'
    block['text']=block['text'].replace('SELECT TURN(1);',c['text'])
    b['definition_units']=[{'definition_unit_id':'shared','binding_status':'COMPLETE',
        'identity_values':['TURN','SHIFT'],'member_evidence_refs':['b'],
        'source_hash':'h','source_url':block['source_url']}]
    b['field_evidence']=build_field_evidence(b)
    p=dict(b,evidence=[block]);parsed=extract_structured_semantics(p)
    r=validate_extractive_fragments(parsed,b)
    assert not r['fields']['official_examples']


def test_multi_member_section_cannot_override_foreign_row_expression():
    b=bundle();b.update(normalized_identity='SOME_MEMBER',feature_type='OPERATOR')
    block=b['source_blocks'][0]
    block['heading_ancestry']=['Comparison operators']
    block['table_context']['cells']=block['table_context']['cells'][:2]
    block['table_context']['cells'][0].update(text='A = OTHER_MEMBER (...)',headers=[{'text':'Expression'}])
    block['text']='A = OTHER_MEMBER (...) Returns a rotation.'
    b['definition_units']=[{'definition_unit_id':'shared','binding_status':'COMPLETE',
        'identity_values':['SOME_MEMBER','OTHER_MEMBER'],'member_evidence_refs':['b'],
        'source_hash':'h','source_url':block['source_url']}]
    b['field_evidence']=build_field_evidence(b)
    assert not any(f['proof_level']=='VERIFIED' for f in b['field_evidence'])
    assert not extract_structured_semantics(dict(b,evidence=[block]))['description']


@pytest.mark.parametrize('mutation',['identity','unit','local','api','example'])
def test_wrong_subject_or_nondefinition_never_gets_verified_field_authority(mutation):
    b=bundle();block=b['source_blocks'][0]
    if mutation=='identity':b['normalized_identity']='SHIFT'
    if mutation=='unit':block['identity_unit_id']='foreign'
    if mutation=='local':block['owner_local_binding']=False
    if mutation=='api':block['observed_context']={'feature_domain':'DEVELOPER_API'}
    if mutation=='example':block['heading_ancestry']=['Functions / Examples']
    fields=build_field_evidence(b)
    assert not any(f['proof_level']=='VERIFIED' for f in fields)


def test_supplied_role_annotation_cannot_grant_a_source_role():
    b=bundle();p=payload(b)
    for f in p['field_evidence']:
        if f['field']=='description':f['field']='formal_syntax'
    parsed=extract_structured_semantics(p)
    assert not any(c['text']=='Returns a rotation.' for c in parsed['formal_syntax'])
    assert any(f['reason']=='FIELD_WITNESS_TRANSPORT_MISMATCH'
               for f in parsed['semantic_extensions']['verified_field_consumption'])


def test_unknown_role_remains_unknown():
    b=bundle();c=b['source_blocks'][0]['table_context']['cells'][1]
    c['headers']=[{'text':'Unclassified details'}];b['field_evidence']=build_field_evidence(b)
    p=payload(b)
    assert not any(f['field']=='description' for f in p['field_evidence'])
    assert not extract_structured_semantics(p)['description']


def test_combined_column_reaches_final_kb_without_paid_provider(tmp_path):
    from tests.test_profile_definition_execution import document,run
    d=document(tmp_path,'SQL Functions','<table><tr><th>Function -&gt; Returns</th>'
        '<th>Description</th><th>Example</th></tr><tr><td><code>TURN(x) -&gt; integer</code></td>'
        '<td>Returns a rotation.</td><td><code>SELECT TURN(1);</code></td></tr></table>')
    rows=run(tmp_path,d)
    r=next(r for r in rows if r['name']=='TURN' and r['feature_type']=='function')
    assert [c['value'] for c in r['semantics']['description']]==['Returns a rotation.']
    assert [c['value'] for c in r['semantics']['syntax']]==['TURN(x)']
    assert [c['value'] for c in r['semantics']['examples']]==['SELECT TURN(1);']
