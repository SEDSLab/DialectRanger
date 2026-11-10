from agents.contracts.publication_scope import definition_publication_review
from agents.semantic_extractor.frontend_projection_v2 import project_feature
from tests.test_frontend_projection_v2 import _record


def bundle(ancestries, formal=True):
    blocks=[];proofs=[]
    for i,ancestry in enumerate(ancestries):
        blocks.append({'block_id':str(i),'heading_ancestry':ancestry,'text':'Synthetic name','structural_roles':['IDENTITY']})
        proofs.append({'section_id':str(i),'heading_block_id':str(i),'association':'EXACT_DEFINITION_HEADING'})
    if formal:blocks.append({'block_id':'s','text':'SYNTHETIC(x)','structural_roles':['FORMAL_SYNTAX']})
    return {'source_blocks':blocks,'owners':[{'definition_section_proofs':proofs}]}


def test_type_mismatch_and_cross_subject_review_not_reclassification():
    for kind in ('FUNCTION','DATATYPE','OPERATOR'):
        ancestry={'FUNCTION':['Functions'],'DATATYPE':['Data Types'],'OPERATOR':['Operators']}[kind]
        assert not definition_publication_review({'feature_type':kind},bundle([ancestry]))['review_required']
    assert definition_publication_review({'feature_type':'FUNCTION'},bundle([['Data Types']]))['review_required']
    assert definition_publication_review({'feature_type':'DATATYPE'},bundle([['Data Types'],['Functions']]))['review_required']
    assert not definition_publication_review({'feature_type':'DATATYPE'},bundle([['Data Types'],['Data Types']]))['review_required']
    assert not definition_publication_review({'feature_type':'FUNCTION'},bundle([['Functions and Operators','Mathematical Functions']]))['review_required']
    assert not definition_publication_review({'feature_type':'DATATYPE'},bundle([['Functions, Operators and Data Types']]))['review_required']


def test_editorial_positive_evidence_not_absent_syntax_alone():
    b=bundle([],False);b['source_blocks']=[{'block_id':'p','text':'Click on the header to switch diagrams.','structural_roles':['DEFINITION']}]
    assert definition_publication_review({'feature_type':'DATATYPE'},b)['review_required']
    b['source_blocks'][0]['text']='A type that stores fixed length binary values.'
    assert not definition_publication_review({'feature_type':'DATATYPE'},b)['review_required']


def test_all_descriptions_formal_only_examples_and_variants_separate():
    r=_record(example='SELECT 1;\nSELECT 2;')
    r['semantics']['description'].append({'value':'Second paragraph','evidence':r['semantics']['description'][0]['evidence']})
    r['structural']['signatures'].append('IDENTITY_VARIANT')
    r['semantics']['syntax'].append({'value':'A mentioned call','evidence':{'evidence_role':'DESCRIPTION'}})
    result=project_feature(r)
    assert len(result['descriptions'])==2
    assert result['syntax']==['ABS(number)']
    assert 'IDENTITY_VARIANT' in result['identity_variants']
    assert result['examples']==['SELECT 1;\nSELECT 2;']
    assert result['official_example_claims']==r['semantics']['examples']
