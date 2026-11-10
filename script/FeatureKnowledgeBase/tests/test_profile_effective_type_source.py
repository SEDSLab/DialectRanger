"""Focused function-level checks; never starts the pipeline."""
import copy
import inspect
import pytest
from tests.test_downstream_profile_authority import fixture
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.contracts.identity_credibility import evaluate_identity_credibility
from agents.contracts.type_decision import make_type_decision, validate_type_decision
from agents.pipeline.generic_unified import _effective_profile_types, _explicit_profile_mode


def data(tmp_path,kind='FUNCTION',url='https://official.test/reference'):
    unit,evidence,path=fixture(tmp_path,kind,url=url)
    page=from_v2_page(build_page_topology({'url':url,'html_path':str(path)},{}),dbms='sample',html_path=str(path))
    owner=evidence['owners'][0]
    unit.update(candidate_ids=['candidate'],profile_definition_required=True,provenance={
        'page_id':page['page_id'],'source_url':page['url'],'page_hash':page['page_hash'],'html_path':page['html_path']})
    evidence['source_blocks']=[dict(b,owner_ids=[owner['owner_id']]) for b in evidence['source_blocks'] if b['block_id'] in owner['member_block_ids']]
    return unit,evidence,page


@pytest.mark.parametrize('kind',['FUNCTION','DATATYPE','UNCERTAIN'])
def test_effective_decision_preserves_inference(tmp_path,kind):
    unit,evidence,page=data(tmp_path,kind)
    inference=make_type_decision(dbms='sample',identity_unit_id='unit',primary_type='OPERATOR',
        source_url=page['url'],page_id=page['page_id'],page_hash=page['page_hash'],html_path=page['html_path'],
        conflict_flags=['INFERENCE_CONFLICT'])
    original=copy.deepcopy(inference)
    units,decisions=_effective_profile_types([unit],[inference],evidence['owners'],[page],authority_required=True)
    assert inference==original
    effective=decisions[0]
    validate_type_decision(effective)
    assert effective['source']=='EXPLICIT_PROFILE'
    assert effective['primary_type']==units[0]['feature_type']==kind
    assert effective['type_status']==('UNCERTAIN' if kind=='UNCERTAIN' else 'CONFIRMED')
    assert not effective['conflict_flags'] and effective['source_evidence_refs']
    assert effective['inference_decision_sha256']


def test_forged_authority_no_type_override_and_legacy_unchanged(tmp_path):
    unit,evidence,page=data(tmp_path)
    inference={'identity_unit_id':'unit','primary_type':'OPERATOR'}
    units,decisions=_effective_profile_types([unit],[inference],[],[page],authority_required=True)
    assert decisions==[inference]
    assert units[0]['feature_type']==unit['feature_type']
    a,b=_effective_profile_types([unit],[inference],evidence['owners'],[page],authority_required=False)
    assert a==[unit] and b==[inference]


def test_mode_and_augmentation_switch_wiring():
    assert _explicit_profile_mode({'hint_contract':{'selection_authority':'EXPLICIT_PROFILE_DEFINITIONS'}})
    assert not _explicit_profile_mode({})
    # No pipeline call: assert the actual call's keywords are conditional.
    import ast
    import agents.pipeline.generic_unified as module
    tree=ast.parse(inspect.getsource(module.run_generic_downstream))
    call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run_frozen_presemantic')
    for keyword in call.keywords:
        if keyword.arg in {'type_adaptation','operator_closure'}:
            assert isinstance(keyword.value, ast.UnaryOp) and isinstance(keyword.value.op, ast.Not)
            assert isinstance(keyword.value.operand, ast.Name) and keyword.value.operand.id=='authority_required'


@pytest.mark.parametrize('mutation',['source_url','page_hash','html_path','page_id'])
def test_explicit_primary_source_mismatch(tmp_path,mutation):
    unit,evidence,page=data(tmp_path)
    other_path=tmp_path/'unrelated.html'
    other_path.write_text('<main><h1>Other</h1><p>Unrelated source.</p></main>',encoding='utf-8')
    other=from_v2_page(build_page_topology({'url':'https://another.test/other','html_path':str(other_path)},{}),dbms='sample',html_path=str(other_path))
    other_owner={'owner_id':'other_owner','page_id':other['page_id'],'member_block_ids':[b['block_id'] for b in other['blocks']]}
    owners=evidence['owners']+[other_owner]
    unit['owner_ids'].append('other_owner')
    documents=[page,other]
    assert evaluate_identity_credibility(unit,owners=owners,documents=documents,bundle=evidence)['identity_credible']
    replacements={'source_url':other['url'],'page_hash':'0'*64,'html_path':other['html_path'],'page_id':other['page_id']}
    unit['provenance'][mutation]=replacements[mutation]
    result=evaluate_identity_credibility(unit,owners=owners,documents=documents,bundle=evidence)
    assert not result['identity_credible'] and not result['lineage_complete']
    assert any(r.startswith('PRIMARY_SOURCE_') for r in result['reasons'])
    unit.pop('profile_definition_required')
    assert evaluate_identity_credibility(unit,owners=owners,documents=documents,bundle=evidence)['identity_credible']


def test_additional_legitimate_source_allowed(tmp_path):
    unit,evidence,page=data(tmp_path)
    path=tmp_path/'supplement.html'
    path.write_text('<main><p>Additional documentation.</p></main>',encoding='utf-8')
    other=from_v2_page(build_page_topology({'url':'https://other.test/supplement','html_path':str(path)},{}),dbms='sample',html_path=str(path))
    owner={'owner_id':'supplement','page_id':other['page_id'],'member_block_ids':[b['block_id'] for b in other['blocks']]}
    unit['owner_ids'].append(owner['owner_id'])
    evidence['source_blocks'] += [dict(b,source_hash=other['page_hash'],source_url=other['url'],owner_ids=[owner['owner_id']]) for b in other['blocks']]
    result=evaluate_identity_credibility(unit,owners=evidence['owners']+[owner],documents=[page,other],bundle=evidence)
    assert result['identity_credible'] and result['lineage_complete']


def test_separate_authoritative_records_and_mismatches(tmp_path):
    from agents.pipeline.publication import select_publication
    from agents.contracts.publication_integrity import evaluate_publication_integrity
    unit,evidence,page=data(tmp_path)
    first=evaluate_publication_integrity(unit,evidence,None)
    second_dir=tmp_path/'second'
    second_dir.mkdir()
    other,other_evidence,_=data(second_dir,url='https://another.test/reference')
    other['identity_unit_id']='second'
    second=evaluate_publication_integrity(other,other_evidence,None)
    assert first['explicit_profile_authority']['source_url'] != second['explicit_profile_authority']['source_url']
    def rec(uid):return {'feature_id':uid,'provenance':{'identity_unit_id':uid},'canonical_identity':{'key':'same'},'name':unit['canonical_identity'],'feature_type':'function'}
    def adm(uid,c):return dict(identity_unit_id=uid,decision='ADMITTED',identity_credible=True,official_source=True,owner_evidence_correct=True,lineage_complete=True,publication_integrity=c)
    records=[rec('unit'),rec('second')]
    admissions=[adm('unit',first),adm('second',second)]
    validations=[{'identity_group_id':uid,'semantic_status':'PARTIAL'} for uid in ['unit','second']]
    final,review,decisions=select_publication(records,admissions,validations,{'status':'PASS'},integrity_required=True)
    assert len(final)==2 and not review
    assert len({r['feature_id'] for r in final})==2
    assert all(r['representation_status']=='UNRESOLVED_SEPARATE_AUTHORITATIVE_RECORDS' for r in decisions)
    for key,value,reason in [('name','OTHER','PROFILE_AUTHORITY_IDENTITY_MISMATCH'),('feature_type','datatype','PROFILE_AUTHORITY_TYPE_MISMATCH')]:
        changed=copy.deepcopy(records)
        changed[1][key]=value
        final,review,_=select_publication(changed,admissions,validations,{'status':'PASS'},integrity_required=True)
        assert [r['feature_id'] for r in final]==['unit']
        assert reason in review[0]['publication']['reason_codes']


def test_constructs_primary_from_verified_definition_then_rejects_tamper(tmp_path):
    unit,evidence,page=data(tmp_path)
    original=copy.deepcopy(unit['provenance'])
    unit['provenance']=dict(original,source_url='https://reference.test/mention',page_id='weak-reference',page_hash='weak',html_path='weak.html')
    weak=copy.deepcopy(unit['provenance'])
    inference=make_type_decision(dbms='sample',identity_unit_id='unit',primary_type='OPERATOR',
        source_url=weak['source_url'],page_id=weak['page_id'],page_hash=weak['page_hash'],html_path=weak['html_path'])
    units,decisions=_effective_profile_types([unit],[inference],evidence['owners'],[page],authority_required=True)
    effective=units[0]
    assert effective['inference_provenance']==weak
    for key in ['page_id','source_url','page_hash','html_path']:
        assert effective['provenance'][key]==original[key]
        assert decisions[0]['provenance'][key]==original[key]
    assert unit['provenance']==weak and inference['provenance']['source_url']==weak['source_url']
    assert evaluate_identity_credibility(effective,owners=evidence['owners'],documents=[page],bundle=evidence)['identity_credible']
    effective['provenance']['source_url']='https://different.test/forged'
    assert not evaluate_identity_credibility(effective,owners=evidence['owners'],documents=[page],bundle=evidence)['identity_credible']

