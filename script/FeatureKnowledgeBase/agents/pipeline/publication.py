"""Feature-local release projection; audit records remain immutable."""
from collections import Counter
from agents.contracts.publication_integrity import publication_integrity_reasons
from agents.contracts.field_validation import record_fields_valid


def select_publication(records, admissions, validations, lineage, *, integrity_required=False):
    ai={r['identity_unit_id']:r for r in admissions}
    vi={r['identity_group_id']:r for r in validations}
    keys=Counter(r['canonical_identity']['key'] for r in records
                 if (r.get('release_boundary') or {}).get('state')!='REVIEW_ONLY_ALTERNATIVE')
    authoritative = {}
    for record in records:
        uid = record['provenance']['identity_unit_id']
        contract = ai.get(uid, {}).get('publication_integrity') or {}
        if (contract.get('explicit_profile_authority') and contract.get('source_identity_valid')
                and not publication_integrity_reasons(contract, uid)
                and (record.get('release_boundary') or {}).get('state') != 'REVIEW_ONLY_ALTERNATIVE'):
            authoritative.setdefault(record['canonical_identity']['key'], set()).add(uid)
    authority_types = {key: {ai[uid]['publication_integrity']['type_hypothesis'] for uid in uids}
                       for key, uids in authoritative.items()}
    proven_challengers = set()
    for record in records:
        uid = record['provenance']['identity_unit_id']
        key = record['canonical_identity']['key']
        contract = ai.get(uid, {}).get('publication_integrity')
        if (uid not in authoritative.get(key, set()) and contract
                and not publication_integrity_reasons(contract, uid)):
            proven_challengers.add(key)
    final=[];review=[];decisions=[]
    for record in records:
        uid=record['provenance']['identity_unit_id'];a=ai.get(uid,{});v=vi.get(uid,{})
        reasons=[]
        if (record.get('release_boundary') or {}).get('state')=='REVIEW_ONLY_ALTERNATIVE':
            reasons.append('ISOLATED_REVIEW_ALTERNATIVE_NOT_MERGED')
        integrity=a.get('publication_integrity')
        if integrity_required and not integrity:reasons.append('PUBLICATION_INTEGRITY_NOT_EVALUATED')
        if integrity and publication_integrity_reasons(integrity,uid):
            reasons.append('PUBLICATION_INTEGRITY_UNRESOLVED')
            reasons.extend(publication_integrity_reasons(integrity,uid))
        if a.get('decision') not in {'ADMITTED','ADMITTED_PARTIAL'}:reasons.append('ADMISSION_NOT_RELEASE_READY')
        if not all(a.get(k) for k in ('identity_credible','official_source','owner_evidence_correct','lineage_complete')):reasons.append('SOURCE_OWNER_LINEAGE_INVALID')
        if v.get('field_isolation'):
            if not record_fields_valid(record,vi,ai):reasons.append('ACCEPTED_FIELD_CONTRACT_INVALID')
        else:
            # Historical validation artifacts have no claim-level contract;
            # preserve their fail-closed behavior rather than assuming clean.
            if not v or any(v.get(k,0) for k in ('invalid_evidence_refs','ungrounded_claims')):reasons.append('GROUNDING_NOT_RELEASE_READY')
            if v.get('semantic_status') in {'NOT_RUN','REVIEW_REQUIRED'}:reasons.append('SEMANTIC_NOT_RELEASE_READY')
        key = record['canonical_identity']['key']
        authorities = authoritative.get(key, set())
        if integrity and integrity.get('explicit_profile_authority'):
            from agents.contracts.identity_key import normalize_identity
            authority = integrity['explicit_profile_authority']
            name = record.get('canonical_name') or record.get('name') or record['canonical_identity'].get('canonical_name')
            kind = record.get('feature_type') or record['canonical_identity'].get('feature_type')
            if name and normalize_identity(name) != normalize_identity(authority.get('identity', '')):
                reasons.append('PROFILE_AUTHORITY_IDENTITY_MISMATCH')
            if kind and str(kind).upper() != authority.get('feature_type'):
                reasons.append('PROFILE_AUTHORITY_TYPE_MISMATCH')
        if keys[key] > 1:
            if authorities and key not in proven_challengers:
                if len(authority_types[key]) > 1:
                    reasons.append('PROFILE_AUTHORITY_TYPE_CONFLICT')
                elif uid not in authorities:
                    reasons.append('NON_AUTHORITY_ALTERNATIVE_TO_EXPLICIT_PROFILE')
            else:
                reasons.append('CANONICAL_IDENTITY_COLLISION')
        if lineage.get('status')!='PASS':reasons.append('LINEAGE_VALIDATION_FAILED')
        row={'feature_id':record['feature_id'],'identity_unit_id':uid,'published':not reasons,'reason_codes':reasons}
        if len(authorities) > 1 and not reasons:
            row['representation_status'] = 'UNRESOLVED_SEPARATE_AUTHORITATIVE_RECORDS'
            record['representation_status']=row['representation_status']
        if integrity:
            row['integrity_decision_fingerprint']=integrity.get('decision_fingerprint')
            row['publication_integrity_state']=integrity.get('state')
        decisions.append(row)
        if reasons:review.append({'record':record,'publication':row,'admission':a,'validation':v})
        else:final.append(record)
    return final,review,decisions
