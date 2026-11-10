"""Generic Type definition/use and Canonical representation contracts."""
import copy
import inspect
import json
from pathlib import Path

import pytest

from agents.type.definition_roles import interpret_type_evidence, revise_type_decisions
from agents.type.decision import decide_type
from agents.contracts.publication_integrity import evaluate_publication_integrity
from agents.contracts.canonical_integrity import resolve_canonical_representations
from agents.pipeline.publication import select_publication


def grammar_fixture(subject='VALUE_KIND', grammar='CALCULATE(value_kind)', headings=None):
    unit = {'identity_unit_id':'u','canonical_identity':subject, 'feature_type':'DATATYPE', 'owner_ids':['o','t']}
    doc = {'page_id':'p', 'url':'https://official.invalid/ref', 'blocks':[
        {'block_id':'syntax','block_kind':'CODE','section_id':'s','order':1,'text':grammar},
        {'block_id':'argument','block_kind':'TABLE_ROW','section_id':'s','order':2,
         'heading_ancestry':headings or ['Functions','CALCULATE'], 'text':subject,
         'table_metadata':{'cells':['', subject, ''],'headers':[]}},
        {'block_id':'type','block_kind':'HEADING','section_id':'type','order':3,'text':subject,'heading_ancestry':['Data Types',subject]},
    ]}
    votes = [
        {'identity_unit_id':'u','evidence_id':'f','owner_id':'o','block_id':'argument', 'subject':subject,
         'candidate_type':'FUNCTION','strength':'STRONG','subject_alignment':'FEATURE_SLOT','signature':'','evidence_kind':'FUNCTION_SLOT'},
        {'identity_unit_id':'u','evidence_id':'d','owner_id':'t','block_id':'type', 'subject':subject,
         'candidate_type':'DATATYPE','strength':'STRONG','subject_alignment':'EXACT_HEAD','evidence_kind':'DATATYPE_SLOT','heading_ancestry':['Data Types',subject]},
    ]
    return unit, votes, doc


def test_grammar_parameter_is_not_conflicting_function_definition():
    u,v,d = grammar_fixture()
    assert decide_type(u,v)['type_status']=='REVIEW_REQUIRED'
    interpreted=interpret_type_evidence([u],v,[d])
    assert interpreted[0]['evidence_role']=='GRAMMAR_PLACEHOLDER'
    assert interpreted[0]['strength']=='STRONG' and interpreted[0]['evidence_id']=='f'
    decision=decide_type(u,interpreted)
    assert decision['primary_type']=='DATATYPE' and decision['type_status']=='CONFIRMED'
    assert decision['supporting_evidence_ids']==['d']
    assert 'f' in decision['evidence_summary']['definition_vote_excluded_ids']


@pytest.mark.parametrize('change',['no_grammar','wrong_section','missing_section','explicit_header','identity_heading','unpad_cells','later_grammar'])
def test_shapes_or_context_alone_cannot_remove_a_vote(change):
    u,v,d=grammar_fixture()
    if change=='no_grammar':d['blocks'][0]['text']='Text referring to a type'
    elif change=='wrong_section':d['blocks'][0]['section_id']='other'
    elif change=='missing_section':d['blocks'][0].pop('section_id');d['blocks'][1].pop('section_id')
    elif change=='explicit_header':d['blocks'][1]['table_metadata']['headers']=['Function']
    elif change=='identity_heading':d['blocks'][1]['heading_ancestry']=['Functions','VALUE_KIND']
    elif change=='unpad_cells':d['blocks'][1]['table_metadata']['cells']=['VALUE_KIND','description']
    elif change=='later_grammar':d['blocks'][0]['order']=10
    r=interpret_type_evidence([u],v,[d])
    assert r[0].get('definition_vote_eligible') is not False


@pytest.mark.parametrize('grammar',['CALCULATE(x) | { CALCULATE | ALTERNATE } (x,y)', '{ CALCULATE | ALTERNATE } (x,y)', 'CALCULATE(ALTERNATE(x))'])
def test_callable_alternative_or_nested_callable_is_not_declared_a_parameter(grammar):
    u,v,d=grammar_fixture(subject='ALTERNATE',grammar=grammar)
    assert interpret_type_evidence([u],v,[d])[0].get('definition_vote_eligible') is not False


def test_real_independent_function_and_datatype_remain_review_even_with_adapter():
    from agents.type.adaptation import adapted_decide_type
    u,v,d=grammar_fixture()
    v[0].update(evidence_kind='CALLABLE_SIGNATURE',callable_definition_span={'text':'VALUE_KIND(x)'})
    v[1]['type_adaptation']=True
    d['blocks'][1].update(text='VALUE_KIND(x)',heading_ancestry=['Functions','VALUE_KIND'])
    r=interpret_type_evidence([u],v,[d])
    decision=adapted_decide_type(u,r)
    assert decision['type_status']=='REVIEW_REQUIRED'
    assert 'SUBJECT_TYPE_CONFLICT' in decision['reason_codes']


def test_zero_argument_catalog_function_conflicts_with_same_named_datatype():
    from agents.type.adaptation import adapted_decide_type
    u,v,d=grammar_fixture(subject='SAME_NAME')
    d['blocks'][1].update(text='SAME_NAME Returns a value',table_metadata={'headers':['Function','Description'],'cells':['SAME_NAME','Returns a value']})
    v[1]['type_adaptation']=True
    interpreted=interpret_type_evidence([u],v,[d])
    assert interpreted[0]['evidence_role']=='DEFINITION_SUPPORT'
    result=adapted_decide_type(u,interpreted)
    assert result['type_status']=='REVIEW_REQUIRED' and 'SUBJECT_TYPE_CONFLICT' in result['reason_codes']


def test_type_interpretation_is_immutable_and_does_not_change_ids():
    u,v,d=grammar_fixture();original=copy.deepcopy((u,v,d))
    r=interpret_type_evidence([u],v,[d])
    assert (u,v,d)==original
    assert [x['evidence_id'] for x in r]==[x['evidence_id'] for x in v]


def canonical_fixture():
    blocks=[]
    for bid,owner,surface,desc in [('b1','o1','x + y','Add'),('b2','o2','instant + duration','Add a duration')]:
        blocks.append({'block_id':bid,'owner_ids':[owner],'source_url':'https://official.invalid/operators',
            'page_id':'p','source_hash':'hash','text':surface+' '+desc,'dom_path':'table/'+bid,
            'table_context':{'cells':[{'text':surface,'headers':[{'text':'Operator'}]}]}})
    native={'identity_unit_id':'original','canonical_identity':'+','feature_type':'OPERATOR','candidate_ids':['c1','c2'],
            'owner_ids':['o1','o2'],'occurrence_ids':['occ1','occ2'],'variants':['+']}
    adapted={'identity_unit_id':'adapted','canonical_identity':'+','feature_type':'OPERATOR','owner_ids':['o1'],
             'adapted_identity_kind':'OPERATOR','adaptation_provenance':[{'block_id':'b1','owner_id':'o1','page_id':'p',
             'source_url':'https://official.invalid/operators','corpus_hash':'hash'}]}
    units=[native,adapted]
    bundles=[{'identity_group_id':'original','source_blocks':blocks},{'identity_group_id':'adapted','source_blocks':blocks[:1]}]
    records=[];admissions=[];validations=[]
    for unit,bundle in zip(units,bundles):
        uid=unit['identity_unit_id']
        integrity=evaluate_publication_integrity(unit,bundle,{'primary_type':'OPERATOR','status':'CONFIRMED'})
        admissions.append({'identity_unit_id':uid,'decision':'ADMITTED_PARTIAL','identity_credible':True,'official_source':True,
            'owner_evidence_correct':True,'lineage_complete':True,'publication_integrity':integrity})
        validations.append({'identity_group_id':uid,'semantic_status':'PARTIAL','invalid_evidence_refs':0,'ungrounded_claims':0})
        def claim(text,b):return {'value':text,'evidence':{'block_id':b['block_id'],'url':b['source_url'],'page_hash':'hash'}}
        owned=bundle['source_blocks']
        records.append({'feature_id':'canonical-'+uid,'name':'+','feature_type':'operator',
            'canonical_identity':{'key':'db|operator|+|default','dbms':'db','feature_type':'operator','canonical_name':'+','discriminator':'DEFAULT'},
            'provenance':{'identity_unit_id':uid,'admission_id':'a-'+uid,'semantic_record_id':'s-'+uid,'evidence_ids':[b['block_id'] for b in owned]},
            'structural':{'attributes':{},'signatures':['+'],'aliases':[],'return_types':[],
                          'primary_evidence':{'block_id':owned[0]['block_id']},'supporting_evidence':[{'block_id':b['block_id']} for b in owned[1:]]},
            'semantics':{'syntax':[claim('x + y' if b['block_id']=='b1' else 'instant + duration',b) for b in owned],
                         'description':[claim('Add' if b['block_id']=='b1' else 'Add a duration',b) for b in owned],'examples':[]}})
    return records, {'units':units,'bundles':bundles,'admissions':admissions,'validations':validations,'lineage':{'status':'PASS'}}


def test_safe_duplicate_representation_retains_all_variants_and_lineage():
    records,kwargs=canonical_fixture();snapshot=copy.deepcopy((records,kwargs))
    assert not select_publication(records,kwargs['admissions'],kwargs['validations'],kwargs['lineage'],integrity_required=True)[0]
    projected,decisions=resolve_canonical_representations(records,**kwargs)
    assert len(projected)==1 and decisions[0]['resolved']
    assert projected[0]['feature_id']=='canonical-original'
    assert [x['value'] for x in projected[0]['semantics']['syntax']]==['x + y','instant + duration']
    assert len(projected[0]['semantics']['description'])==2
    assert len(projected[0]['structural']['attributes']['canonical_representation_lineage'])==2
    assert len(select_publication(projected,kwargs['admissions'],kwargs['validations'],kwargs['lineage'],integrity_required=True)[0])==1
    assert (records,kwargs)==snapshot


@pytest.mark.parametrize('risk',['two_native','type_conflict','missing_adaptation','changed_text','foreign_owner','extra_source','member_review','bad_grounding','bad_lineage','bad_hash','missing_hash'])
def test_unproven_collisions_remain_blocked(risk):
    records,k=canonical_fixture()
    if risk=='two_native':k['units'][1].pop('adapted_identity_kind')
    elif risk=='type_conflict':k['units'][1]['feature_type']='DATATYPE'
    elif risk=='missing_adaptation':k['units'][1].pop('adaptation_provenance')
    elif risk=='changed_text':k['bundles'][1]['source_blocks'][0]=dict(k['bundles'][1]['source_blocks'][0],text='different subject')
    elif risk=='foreign_owner':k['units'][0]['owner_ids']=['o2']
    elif risk=='extra_source':k['bundles'][1]['source_blocks'].append({'block_id':'foreign','owner_ids':['o1']})
    elif risk=='member_review':k['admissions'][1]['decision']='NEEDS_REVIEW'
    elif risk=='bad_grounding':k['validations'][1]['ungrounded_claims']=1
    elif risk=='bad_lineage':k['lineage']['status']='FAIL'
    elif risk=='bad_hash':k['units'][1]['adaptation_provenance'][0]['corpus_hash']='bad'
    elif risk=='missing_hash':k['bundles'][0]['source_blocks'][0].pop('source_hash');k['units'][1]['adaptation_provenance'][0].pop('corpus_hash')
    projected,decisions=resolve_canonical_representations(records,**k)
    assert len(projected)==2 and not decisions[0]['resolved']
    assert not select_publication(projected,k['admissions'],k['validations'],k['lineage'],integrity_required=True)[0]


def test_same_name_different_type_keys_never_merge():
    r,k=canonical_fixture();r[1]['canonical_identity']['key']='db|datatype|+|default'
    projected,decisions=resolve_canonical_representations(r,**k)
    assert len(projected)==2 and decisions==[]


def test_representative_order_is_stable_when_adapted_record_comes_first():
    r,k=canonical_fixture()
    first,_=resolve_canonical_representations(r,**k)
    second,_=resolve_canonical_representations(list(reversed(r)),**k)
    assert first==second


def test_unproven_adapted_alternative_cannot_suppress_verified_native():
    records,k=canonical_fixture();snapshot=copy.deepcopy(records)
    k['admissions'][1]['decision']='NEEDS_REVIEW'
    k['admissions'][1]['publication_integrity']=evaluate_publication_integrity(
        k['units'][1],{'source_blocks':[]},{'primary_type':'OPERATOR','status':'CONFIRMED'})
    projected,decisions=resolve_canonical_representations(records,**k)
    final,review,_=select_publication(projected,k['admissions'],k['validations'],k['lineage'],integrity_required=True)
    assert [r['feature_id'] for r in final]==['canonical-original']
    assert len(review)==1 and review[0]['record']['feature_id']=='canonical-adapted'
    assert final[0]==records[0]
    assert decisions[0]['merged'] is False
    assert records==snapshot
    assert 'ISOLATED_REVIEW_ALTERNATIVE_NOT_MERGED' in review[0]['publication']['reason_codes']








