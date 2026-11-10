"""Publication isolation proofs; never borrow fields or permission from peers."""


def review_alternative_isolation(native, alternative, *, bundles, admissions):
    """An invalid representation is not positive contradictory source evidence.

    Positive witnesses from an independently credible alternative remain a
    blocker, even if its Semantic/Grounding/Target decision needs review.
    """
    uid=alternative.get('identity_unit_id');ad=admissions.get(uid,{})
    if (alternative.get('adapted_identity_kind')!=native.get('feature_type')
        or alternative.get('feature_type')!=native.get('feature_type')
        or alternative.get('canonical_identity')!=native.get('canonical_identity')):
        return None
    integrity=ad.get('publication_integrity') or {}
    if integrity.get('subject_conflicts'):return None
    witnesses=integrity.get('subject_witnesses') or []
    credible=all(ad.get(k) for k in ('identity_credible','official_source','owner_evidence_correct','lineage_complete'))
    if witnesses and credible:return None
    # Do not quarantine valid observations just because extraction is partial,
    # a request failed, or a later gate asks for review. Require an explicit
    # source/identity rejection when positive witnesses exist.
    invalid_source=(ad.get('decision')=='REJECTED' and not credible
                    and bool(set(ad.get('reason_codes',[])) &
                             {'IDENTITY_NOT_CREDIBLE','OWNER_EVIDENCE_INVALID','LINEAGE_INCOMPLETE'}))
    if witnesses and not invalid_source:return None
    bundle=bundles.get(uid,{})
    return {'schema_version':'RepresentationResolution/v1','decision':'ISOLATE_REVIEW_ALTERNATIVE',
            'native_unit_id':native['identity_unit_id'],'alternative_unit_id':uid,
            'reason':'EXPLICIT_SOURCE_IDENTITY_REJECTION' if witnesses else 'NO_DEFINING_SUBJECT_WITNESS',
            'alternative_admission':ad.get('decision'),'positive_witnesses_trusted':False,
            'raw_witnesses_retained':witnesses,
            'source_block_ids':[b['block_id'] for b in bundle.get('source_blocks',[])],
            'field_merge_allowed':False,'publication_permission_inherited':False}
