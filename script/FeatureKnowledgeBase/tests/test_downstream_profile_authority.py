import copy
import pytest
from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.profile_bootstrap.cold_start import build_cold_start_profile
from agents.owner.generic import generate_owner_hypotheses
from agents.contracts.profile_authority import verified_authority
from agents.contracts.publication_integrity import evaluate_publication_integrity, publication_integrity_reasons


def fixture(tmp_path, kind='UNCERTAIN', name='@@METRIC', url='https://official.test/reference'):
    path=tmp_path/'reference.html'
    path.write_text('<main><h1>Reference</h1><h2>'+name+'</h2><p>Configured subject.</p></main>',encoding='utf-8')
    d=from_v2_page(build_page_topology({'url':url,'html_path':str(path)},{}),dbms='sample',html_path=str(path))
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='authority')
    profile.update(generated=False,generation_mode='MANUAL_PREPARED',profile_provenance={'author':'test','authored_at':'2026-11-08','revision':'1'})
    profile['hint_contract']['definition_structure_patterns']=[{
        'structure':'B','identity_slot':'HEADING','boundary':'NEXT_PEER_OR_PARENT_HEADING',
        'execution':{'version':1,'authority':'EXPLICIT_DEFINITION','declaration_kind':kind,
                     'identity':{'alternatives':[{'kind':'HEADING','heading_level':2}],'cardinality':'ONE'},
                     'fields':[],'when':{'page_path_prefixes':['/reference']}}}]
    owners=generate_owner_hypotheses([d],profile)
    owner=next(o for o in owners if any((p.get('structure_execution') or {}).get('authority')=='EXPLICIT_DEFINITION' for p in o.get('definition_section_proofs',[])))
    unit={'identity_unit_id':'unit','canonical_identity':name,'owner_ids':[owner['owner_id']], 'feature_type':kind}
    evidence={'owners':[owner],'source_blocks':[dict(b,source_hash=d['page_hash'],source_url=d['url']) for b in d['blocks']]}
    return unit,evidence,path


def test_explicit_unknown_no_grammar(tmp_path):
    unit,evidence,_=fixture(tmp_path)
    assert verified_authority(unit,evidence)
    c=evaluate_publication_integrity(unit,evidence,{'type_status':'REVIEW'}, {'admission_status':'REVIEW'})
    assert not publication_integrity_reasons(c,'unit')
    assert c['type_hypothesis']=='UNCERTAIN'
    assert evaluate_publication_integrity(unit,evidence,None,source_identity_valid=False)['state']=='REVIEW'


def test_transported_partial(tmp_path):
    unit,evidence,_=fixture(tmp_path)
    proof=next(p for p in evidence['owners'][0]['definition_section_proofs'] if (p.get('structure_execution') or {}).get('authority'))
    partial={'structure_execution':proof['structure_execution']}
    assert verified_authority(partial,{'source_blocks':evidence['source_blocks']},unit['canonical_identity'])


@pytest.mark.parametrize('tamper',['hash','slot','source','name','fake'])
def test_tamper_and_nonmatching(tmp_path,tamper):
    unit,evidence,path=fixture(tmp_path)
    if tamper=='hash':
        for b in evidence['source_blocks']:b['source_hash']='forged'
    elif tamper=='slot':
        for p in evidence['owners'][0]['definition_section_proofs']:
            if (p.get('structure_execution') or {}).get('authority'):
                p['structure_execution']['identity_bindings'][0]['declarations'][0]['identity']='OTHER'
    elif tamper=='source':path.write_text('<main>different</main>',encoding='utf-8')
    elif tamper=='name':unit['canonical_identity']='OTHER'
    else:evidence={'source_blocks':evidence['source_blocks'],'definition_units':[{'structure_execution':{'authority':'EXPLICIT_DEFINITION'}}]}
    assert verified_authority(unit,evidence) is None


def test_configured_only_release(tmp_path):
    unit,evidence,_=fixture(tmp_path)
    unit.update(canonical_identity='UNCONFIGURED',profile_definition_required=True)
    assert 'OUTSIDE_CONFIGURED_DEFINITION' in evaluate_publication_integrity(unit,evidence,None)['reason_codes']

@pytest.mark.parametrize('name,kind',[('@@METRIC','UNCERTAIN'),('AND','FUNCTION'),('SELECT','FUNCTION')])
def test_production_configured_identity(tmp_path,name,kind):
    from agents.pipeline.generic_unified import run_generic_downstream
    from agents.profile_bootstrap.cold_start import build_cold_start_profile
    import json
    unit,evidence,path=fixture(tmp_path,kind,name)
    d=from_v2_page(build_page_topology({'url':'https://official.test/reference','html_path':str(path)},{}),dbms='sample',html_path=str(path))
    profile=build_cold_start_profile(dbms='sample',documents=[d],run_id='authority')
    profile.update(generated=False,generation_mode='MANUAL_PREPARED',profile_provenance={'author':'test','authored_at':'2026-11-08','revision':'1'})
    proof=next(p for p in evidence['owners'][0]['definition_section_proofs'] if (p.get('structure_execution') or {}).get('authority'))
    profile['hint_contract']['definition_structure_patterns']=[proof['structure_execution']['plan']]
    run_generic_downstream(dbms='sample',documents=[d],profile=profile,output_root=tmp_path/'run',semantic=True,semantic_provider='deterministic')
    admissions=[json.loads(s) for s in (tmp_path/'run/canonical/admission_decisions.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(a.get('publication_integrity',{}).get('explicit_profile_authority',{}).get('identity')==name for a in admissions)


def test_authority_collision_release_boundary(tmp_path):
    from agents.pipeline.publication import select_publication
    unit,evidence,_=fixture(tmp_path)
    contract=evaluate_publication_integrity(unit,evidence,None)
    def record(uid):return {'feature_id':uid,'provenance':{'identity_unit_id':uid},'canonical_identity':{'key':'same'}}
    def admission(uid,c):return dict(identity_unit_id=uid,decision='ADMITTED',identity_credible=True,official_source=True,owner_evidence_correct=True,lineage_complete=True,publication_integrity=c)
    validations=[{'identity_group_id':uid,'semantic_status':'PARTIAL'} for uid in ['unit','alternative']]
    records=[record('unit'),record('alternative')]
    admissions=[admission('unit',contract),admission('alternative',{})]
    final,review,_=select_publication(records,admissions,validations,{'status':'PASS'},integrity_required=True)
    assert [r['feature_id'] for r in final]==['unit']
    assert 'NON_AUTHORITY_ALTERNATIVE_TO_EXPLICIT_PROFILE' in review[0]['publication']['reason_codes']
    other=dict(unit,identity_unit_id='alternative')
    admissions[1]=admission('alternative',evaluate_publication_integrity(other,evidence,None))
    final,review,_=select_publication(records,admissions,validations,{'status':'PASS'},integrity_required=True)
    assert len(final)==2 and not review
