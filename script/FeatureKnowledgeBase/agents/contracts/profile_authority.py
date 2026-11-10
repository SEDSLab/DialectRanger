"""Opt-in Profile authority, revalidated against frozen local source."""
from collections.abc import Mapping
from agents.contracts.identity_key import normalize_identity


def verified_authority(unit, evidence, identity=None):
    """Return a verified declaration or None; annotations alone grant nothing.

    Accept IdentityUnits and projected DefinitionUnits. All source parsing is
    delegated to the Owner verifier, without independent SQL grammar checks.
    """
    if not isinstance(unit, Mapping) or not isinstance(evidence, Mapping):
        return None
    name = identity or unit.get('canonical_identity') or unit.get('normalized_identity')
    if not name:
        return None
    blocks = {b.get('block_id') or b.get('evidence_ref'): b
              for b in evidence.get('source_blocks', []) if isinstance(b, Mapping)}
    candidates = [(unit, unit.get('owner_ids', []))]
    candidates += [(d, d.get('owner_ids', [])) for d in evidence.get('definition_units', [])]
    for owner in evidence.get('owners', []):
        candidates += [(p, [owner.get('owner_id')]) for p in owner.get('definition_section_proofs', [])]
    verified = []
    for candidate, owners in candidates:
        execution = candidate.get('structure_execution') or {}
        if execution.get('authority') != 'EXPLICIT_DEFINITION':
            continue
        if candidate.get('status') == 'INVALID':
            continue
        if unit.get('owner_ids') and not set(unit['owner_ids']).intersection(owners):
            continue
        proof = candidate.get('source_proof') or candidate
        if not proof.get('source_citations'):
            proof = dict(proof, source_citations=execution.get('source_citations') or {})
        bindings = execution.get('identity_bindings') or []
        for binding in bindings:
            declared = [d['identity'] for d in binding.get('declarations', [])
                        if normalize_identity(d.get('identity', '')) == normalize_identity(name)]
            if len(declared) != 1:
                continue
            block = blocks.get(binding.get('block_id'))
            if not block:
                continue
            if (block.get('source_hash') != execution.get('source_hash')
                    or block.get('source_url') != execution.get('source_url')):
                continue
            from agents.owner.profile_execution import revalidate_explicit_authority
            result = revalidate_explicit_authority(proof, block, declared[0])
            if result:
                verified.append(dict(result, evidence_refs=[block['block_id']],
                                     owner_ids=list(owners), identity=declared[0]))
    if not verified or len({r['feature_type'] for r in verified}) != 1:
        return None
    return verified[0]
