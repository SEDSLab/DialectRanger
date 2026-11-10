import copy
import pytest
from tests.test_type_canonical_integrity_phase4b import canonical_fixture
from agents.contracts.canonical_integrity import resolve_canonical_representations
from agents.pipeline.publication import select_publication


def test_invalid_source_witness_does_not_suppress_independently_eligible_native():
    records,k=canonical_fixture();before=copy.deepcopy(records)
    ad=k['admissions'][1];ad.update(decision='REJECTED',identity_credible=False,
                                  reason_codes=['IDENTITY_NOT_CREDIBLE'])
    out,audit=resolve_canonical_representations(records,**k)
    final,review,_=select_publication(out,k['admissions'],k['validations'],k['lineage'],integrity_required=True)
    assert len(final)==1 and final[0]==before[0]
    assert len(review)==1 and review[0]['record']['semantics']==before[1]['semantics']
    assert audit[0]['proofs'][0]['reason']=='EXPLICIT_SOURCE_IDENTITY_REJECTION'
    assert records==before
    reverse,_=resolve_canonical_representations(list(reversed(records)),**k)
    assert out==reverse


@pytest.mark.parametrize('risk',['credible_witness','positive_conflict','two_native','type_conflict','grounding_only'])
def test_real_conflicts_and_non_source_failures_are_not_quarantined(risk):
    records,k=canonical_fixture()
    ad=k['admissions'][1];ad.update(decision='REJECTED',identity_credible=False,
                                  reason_codes=['IDENTITY_NOT_CREDIBLE'])
    if risk=='credible_witness':ad['identity_credible']=True
    if risk=='positive_conflict':ad['publication_integrity']['subject_conflicts']=[{'reason':'FOREIGN_DEFINITION_SUBJECT'}]
    if risk=='two_native':k['units'][1].pop('adapted_identity_kind')
    if risk=='type_conflict':k['units'][1]['feature_type']='DATATYPE'
    if risk=='grounding_only':ad.update(decision='NEEDS_REVIEW',reason_codes=['SEMANTIC_PARTIAL'])
    out,audit=resolve_canonical_representations(records,**k)
    assert not audit[0]['resolved']
    assert not select_publication(out,k['admissions'],k['validations'],k['lineage'],integrity_required=True)[0]
