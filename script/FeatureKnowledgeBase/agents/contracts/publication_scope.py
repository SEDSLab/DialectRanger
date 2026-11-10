"""Conservative release review based on existing local definition provenance."""
import re


def definition_publication_review(unit, bundle):
    from agents.contracts.profile_authority import verified_authority
    authority = verified_authority(unit, bundle)
    if authority:
        return {'review_required': False, 'reason_codes': [], 'definition_sections': {},
                'policy': 'source-verified-explicit-profile', 'identity_unit_id': unit.get('identity_unit_id')}
    blocks = {b.get('block_id'): b for b in bundle.get('source_blocks', [])}
    sections = {}
    for owner in bundle.get('owners', []):
        for proof in owner.get('definition_section_proofs', []):
            heading = blocks.get(proof.get('heading_block_id'), {})
            ancestry = ' / '.join(heading.get('heading_ancestry') or [])
            kinds = set()
            # Mixed reference-book titles are not a local type assertion.
            # Use the nearest unambiguous category, not a global word union.
            for label in heading.get('heading_ancestry') or []:
                local = set()
                if re.search(r'\b(?:data[ -]?types?|type declarations?|storage classes?)\b', label, re.I):local.add('DATATYPE')
                if re.search(r'\b(?:functions?|routines?)\b', label, re.I):local.add('FUNCTION')
                if re.search(r'\boperators?\b', label, re.I):local.add('OPERATOR')
                if len(local)==1:kinds=local
            # The common source-verified declaration is more local than a
            # chapter title (e.g. an operator cell in a Functions chapter).
            # Never replace context with an unverified Profile/type assertion.
            from agents.owner.definition_patterns import verified_pattern
            parsed=verified_pattern(proof,heading,str(unit.get('canonical_identity') or '')) if heading.get('source_structure_status')=='RESOLVED' else None
            if parsed:kinds={parsed['feature_type']}
            sections[proof['section_id']] = {'types': sorted(kinds), 'heading_block_id': proof.get('heading_block_id'),
                                             'association': proof.get('association'), 'ancestry': ancestry,
                                             'type_context_source':'SOURCE_VERIFIED_DEFINITION' if parsed else 'SECTION_ANCESTRY'}
    kinds = set(k for section in sections.values() for k in section['types'])
    reasons = []
    if len(kinds) > 1:
        reasons.append('DEFINITION_SECTION_TYPE_CONFLICT')
    elif kinds and str(unit.get('feature_type') or '').upper() not in kinds:
        reasons.append('DEFINITION_SECTION_TYPE_MISMATCH')
    definitions = [b.get('text', '') for b in blocks.values() if set(b.get('structural_roles') or []) & {'DEFINITION','DESCRIPTION'}]
    formal = any('FORMAL_SYNTAX' in (b.get('structural_roles') or []) for b in blocks.values())
    if not sections and not formal and any(re.search(r'\b(?:click|toggle|hover)\b.*\b(?:header|button|diagram|highlight)\b', t, re.I) for t in definitions):
        reasons.append('EDITORIAL_INSTRUCTION_WITHOUT_DEFINITION_PROOF')
    return {'review_required': bool(reasons), 'reason_codes': reasons, 'definition_sections': sections,
            'policy': 'conservative-review/no-type-reassignment', 'identity_unit_id': unit.get('identity_unit_id')}
