"""Bounded reconciliation of proven duplicate Canonical representations.

Never groups identities by spelling. Only an explicit adapted representation
of a native, source-covering record may be absorbed. The Phase4 release gate
is called unchanged for every contributing member and the final dataset.
"""
import copy
import json
from collections import defaultdict

from agents.pipeline.publication import select_publication


def _uid(record):
    return record.get('provenance', {}).get('identity_unit_id')


def _unique(values):
    seen = set();output = []
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key);output.append(copy.deepcopy(value))
    return output


def _blocks(bundle):
    return {b['block_id']: b for b in bundle.get('source_blocks', [])}


def _duplicate_proof(native, adapted, representative_bundle, adapted_bundle, native_admission, adapted_admission):
    """Prove representation identity without rewriting either Unit."""
    reasons = []
    kind = str(native.get('feature_type') or '').upper()
    # Recovery kind records the proposal's origin, not the final TypeDecision.
    # Equivalence still requires current identity/type, defining witnesses,
    # source-scope subset and verified adaptation citations below.
    if (not adapted.get('adapted_identity_kind') or not adapted.get('adaptation_provenance')
            or adapted.get('canonical_identity') != native.get('canonical_identity')
            or adapted.get('feature_type') != native.get('feature_type')):
        return None, ['TYPE_OR_IDENTITY_CONFLICT']
    left, right = _blocks(representative_bundle), _blocks(adapted_bundle)
    if not right or not set(right).issubset(left):
        reasons.append('UNPROVEN_ADAPTED_SOURCE_COVERAGE')
    for bid, block in right.items():
        source = left.get(bid, {})
        if any(not block.get(k) or not source.get(k) for k in ('source_url','source_hash','page_id','dom_path')):
            reasons.append('SOURCE_PROVENANCE_INCOMPLETE')
        if any(block.get(k) != source.get(k) for k in ('text','source_url','source_hash','page_id','dom_path')):
            reasons.append('SOURCE_CONTENT_OR_PROVENANCE_MISMATCH')
        if not set(block.get('owner_ids') or []).issubset(set(native.get('owner_ids') or [])):
            reasons.append('FOREIGN_OWNER_IN_DUPLICATE_REPRESENTATION')
    a = native_admission.get('publication_integrity') or {}
    b = adapted_admission.get('publication_integrity') or {}
    def witness_keys(contract):
        return {(w.get('block_id'), w.get('feature_type'), w.get('surface'), w.get('source_url'), w.get('source_hash'))
                for w in contract.get('subject_witnesses', [])}
    native_witnesses = witness_keys(a);adapted_witnesses = witness_keys(b)
    if not adapted_witnesses or not adapted_witnesses.issubset(native_witnesses):
        reasons.append('UNPROVEN_DEFINING_SUBJECT_EQUIVALENCE')
    cited = []
    for item in adapted.get('adaptation_provenance') or []:
        bid = item.get('block_id');block = right.get(bid)
        if not block:
            reasons.append('ADAPTATION_SOURCE_NOT_IN_SCOPE');continue
        if (item.get('source_url') != block.get('source_url')
                or item.get('page_id') != block.get('page_id')
                or (item.get('corpus_hash') or item.get('page_hash')) != block.get('source_hash')
                or item.get('owner_id') not in (block.get('owner_ids') or [])
                or item.get('owner_id') not in (native.get('owner_ids') or [])):
            reasons.append('INVALID_ADAPTATION_PROVENANCE');continue
        cited.append({'block_id': bid, 'owner_id': item.get('owner_id'), 'page_id': item.get('page_id'),
                      'source_url': item.get('source_url'), 'source_hash': block.get('source_hash'),
                      'adaptation_rule_id': item.get('adaptation_rule_id')})
    # At least one actual defining witness, not just a supporting shared row.
    if not {c['block_id'] for c in cited}.intersection(w[0] for w in adapted_witnesses):
        reasons.append('ADAPTATION_DEFINING_SOURCE_UNVERIFIED')
    if reasons:return None, sorted(set(reasons))
    return {'native_unit_id': native['identity_unit_id'], 'adapted_unit_id': adapted['identity_unit_id'],
            'adaptation_source_refs': _unique(cited), 'covered_block_ids': sorted(right),
            'proof': 'EXPLICIT_ADAPTED_REPRESENTATION_WITH_SUBSET_DEFINITION_AND_SOURCE_SCOPE'}, []


def resolve_canonical_representations(records, *, units, bundles, admissions, validations, lineage):
    """Return release-facing Canonical view and an immutable reconciliation audit.

    Per-Unit audit Canonical records are retained by the caller. No key suffix,
    new identity, field invention or publication permission is produced here.
    """
    ui = {u['identity_unit_id']: u for u in units}
    bi = {b['identity_group_id']: b for b in bundles}
    ai = {a['identity_unit_id']: a for a in admissions}
    groups = defaultdict(list)
    for record in records:groups[record['canonical_identity']['key']].append(record)
    output = [];decisions = []
    for key, members in groups.items():
        if len(members) == 1:
            output.append(copy.deepcopy(members[0]));continue
        originals = [r for r in members if not ui.get(_uid(r), {}).get('adapted_identity_kind')]
        row = {'canonical_key': key, 'source_identity_unit_ids': sorted(_uid(r) for r in members),
               'source_feature_ids': sorted(r['feature_id'] for r in members),
               'classification': 'UNRESOLVED_COLLISION', 'resolved': False, 'reason_codes': [], 'proofs': []}
        eligible=[]
        for member in members:
            good,_,_=select_publication([member],admissions,validations,lineage,integrity_required=True)
            if good:eligible.append(member)
        # A reference-only, unproved representation must not veto an independently
        # release-eligible definition. Preserve it in Review without borrowing
        # its fields or granting it the definition's permission.
        if len(eligible)==1:
            representative=eligible[0]
            alternatives=[r for r in members if r is not representative]
            def reference_only(r):
                a=ai.get(_uid(r),{});proof=a.get('publication_integrity') or {}
                return (not proof.get('subject_witnesses') and not proof.get('subject_conflicts')
                        and a.get('decision') in {'NEEDS_REVIEW','REJECTED'})
            if all(reference_only(r) for r in alternatives):
                output.append(copy.deepcopy(representative))
                for r in alternatives:
                    isolated=copy.deepcopy(r)
                    isolated['release_boundary']={'state':'REVIEW_ONLY_ALTERNATIVE',
                        'reason':'NO_INDEPENDENT_SUBJECT_WITNESS','representative_unit_id':_uid(representative)}
                    output.append(isolated)
                row.update(classification='PROVEN_DEFINITION_WITH_REFERENCE_ONLY_ALTERNATIVES',resolved=True,
                    representative_identity_unit_id=_uid(representative),field_merge_allowed=False,merged=False)
                decisions.append(row);continue
        # Independently eligible native/catalog representations may share a
        # source-verified definition target without either being a source subset.
        # Never borrow publication permission from another member.
        if len(eligible)==len(members):
            from agents.contracts.reference_equivalence import prove_reference_equivalence
            proof=prove_reference_equivalence(members,ui,bi,ai)
            if proof:
                def rank(r):
                    ws=(ai.get(_uid(r),{}).get('publication_integrity') or {}).get('subject_witnesses',[])
                    return (-sum(w.get('origin')=='DEFINITION_SECTION' for w in ws),
                            -sum(len(v) for v in r.get('semantics',{}).values()),_uid(r))
                ordered=sorted(members,key=rank);combined=copy.deepcopy(ordered[0])
                for field in combined.get('semantics',{}):
                    combined['semantics'][field]=_unique(v for r in ordered for v in r.get('semantics',{}).get(field,[]))
                for field in ('signatures','aliases','return_types'):
                    combined['structural'][field]=_unique(v for r in ordered for v in r.get('structural',{}).get(field,[]))
                evidence=[v for r in ordered for v in [r.get('structural',{}).get('primary_evidence'),*r.get('structural',{}).get('supporting_evidence',[])] if v]
                combined['structural']['supporting_evidence']=_unique(v for v in evidence if v!=combined['structural'].get('primary_evidence'))
                combined['structural']['attributes']['canonical_representation_lineage']=[
                    {'identity_unit_id':_uid(r),'feature_id':r['feature_id'],
                     'admission_id':r['provenance'].get('admission_id'),
                     'semantic_record_id':r['provenance'].get('semantic_record_id'),
                     'evidence_ids':r['provenance'].get('evidence_ids',[]),'owner_ids':ui[_uid(r)].get('owner_ids',[])} for r in ordered]
                combined['structural']['attributes']['canonical_relation_proof']=proof
                combined['structural']['attributes']['field_reviews']=_unique(v for r in ordered for v in r.get('structural',{}).get('attributes',{}).get('field_reviews',[]))
                populated={label for field,label in [('description','description'),('syntax','formal_syntax'),('examples','official_examples'),('notes','notes')]
                           if combined['semantics'].get(field)}
                combined.setdefault('quality',{})['fields_populated']=sorted(populated)
                combined['quality']['fields_missing']=sorted({'description','formal_syntax','official_examples','notes'}-populated)
                combined['semantic_status']='COMPLETE' if {'description','formal_syntax','official_examples'}<=populated else 'PARTIAL' if populated else 'STRUCTURAL_ONLY'
                row.update(classification='PROVEN_REFERENCE_EQUIVALENCE',resolved=True,
                    representative_identity_unit_id=_uid(ordered[0]),representative_feature_id=combined['feature_id'],
                    absorbed_identity_unit_ids=sorted(_uid(r) for r in ordered[1:]),proofs=[proof],reason_codes=[])
                output.append(combined);decisions.append(row);continue
        # This is publication isolation, NOT a merge by spelling. A verified
        # native definition may publish while unsupported adapted alternatives
        # remain immutable review rows. Genuine conflicting positive witnesses,
        # bad grounding and competing eligible natives still block merging.
        if len(originals)==1 and len(eligible)==1 and eligible[0] is originals[0]:
            representative=originals[0];native_unit=ui.get(_uid(representative),{})
            alternatives=[r for r in members if r is not representative]
            from agents.contracts.representation_resolution import review_alternative_isolation
            isolation=[review_alternative_isolation(native_unit,ui.get(_uid(r),{}),bundles=bi,admissions=ai)
                       for r in alternatives]
            if all(isolation):
                row.update(classification='VERIFIED_NATIVE_REVIEW_ALTERNATIVES_ISOLATED',resolved=True,
                    representative_identity_unit_id=_uid(representative),merged=False,
                    isolated_review_unit_ids=sorted(_uid(r) for r in alternatives),proofs=isolation)
                output.append(copy.deepcopy(representative))
                for alternative in sorted(alternatives,key=_uid):
                    review=copy.deepcopy(alternative)
                    review['release_boundary']={'state':'REVIEW_ONLY_ALTERNATIVE','representative_feature_id':representative['feature_id']}
                    output.append(review)
                decisions.append(row)
                continue
        if len(originals) != 1:
            row['reason_codes'].append('NO_UNIQUE_NATIVE_REPRESENTATIVE')
        else:
            representative = originals[0];native_id = _uid(representative)
            # Membership must not depend on list order or the representative's
            # good status concealing another member's Subject/Type review.
            for member in members:
                _, _, pub = select_publication([member], admissions, validations, lineage, integrity_required=True)
                if not pub[0]['published']:
                    row['reason_codes'].append('MEMBER_NOT_PUBLICATION_ELIGIBLE')
                    row.setdefault('member_failures', []).append(pub[0])
                uid = _uid(member)
                if uid == native_id:continue
                proof, reasons = _duplicate_proof(ui.get(native_id, {}), ui.get(uid, {}),
                    bi.get(native_id, {}), bi.get(uid, {}), ai.get(native_id, {}), ai.get(uid, {}))
                row['reason_codes'].extend(reasons)
                if proof:row['proofs'].append(proof)
            if not row['reason_codes']:
                combined = copy.deepcopy(representative)
                merge_members = [representative] + sorted((r for r in members if r is not representative), key=_uid)
                for field in combined.get('semantics', {}):
                    combined['semantics'][field] = _unique(v for r in merge_members for v in r.get('semantics', {}).get(field, []))
                for field in ('signatures','aliases','return_types'):
                    combined['structural'][field] = _unique(v for r in merge_members for v in r.get('structural', {}).get(field, []))
                evidence = [v for r in merge_members for v in [r.get('structural', {}).get('primary_evidence'), *r.get('structural', {}).get('supporting_evidence', [])] if v]
                combined['structural']['supporting_evidence'] = _unique(v for v in evidence if v != combined['structural'].get('primary_evidence'))
                combined['structural']['attributes']['canonical_representation_lineage'] = [
                    {'identity_unit_id': _uid(r), 'feature_id': r['feature_id'], 'candidate_ids': ui[_uid(r)].get('candidate_ids', []),
                     'owner_ids': ui[_uid(r)].get('owner_ids', []), 'occurrence_ids': ui[_uid(r)].get('occurrence_ids', []),
                     'admission_id': r['provenance'].get('admission_id'), 'semantic_record_id': r['provenance'].get('semantic_record_id'),
                     'evidence_ids': r['provenance'].get('evidence_ids', []),
                     'adaptation_provenance': ui[_uid(r)].get('adaptation_provenance', [])}
                    for r in sorted(members, key=lambda r: _uid(r))]
                # Every output claim must be present in a source record and
                combined['structural']['attributes']['field_reviews']=_unique(v for r in merge_members for v in r.get('structural',{}).get('attributes',{}).get('field_reviews',[]))
                # remain in the representative's unchanged official scope.
                source_scope = set(_blocks(bi[native_id]))
                if any(not isinstance(c, dict) or c.get('evidence', {}).get('block_id') not in source_scope
                       for claims in combined['semantics'].values() for c in claims):
                    row['reason_codes'].append('MERGED_CLAIM_OUTSIDE_REPRESENTATIVE_SCOPE')
                else:
                    row.update(classification='SAME_LOGICAL_FEATURE_DUPLICATE', resolved=True,
                               representative_identity_unit_id=native_id, representative_feature_id=combined['feature_id'],
                               absorbed_identity_unit_ids=sorted(_uid(r) for r in members if _uid(r) != native_id))
                    combined['structural']['attributes']['canonical_relation_proof']={
                        'rule':'VERIFIED_NATIVE_ADAPTED_REPRESENTATIONS','proofs':row['proofs']}
                    output.append(combined)
        if not row['resolved']:output.extend(copy.deepcopy(members))
        row['reason_codes'] = sorted(set(row['reason_codes']))
        decisions.append(row)
    return output, decisions
