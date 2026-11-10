"""Evidence-only projection of the shared, existing Owner definition scope."""
from agents.owner.definition_scope import definition_scopes, normalized_name, scope_for_owner


def _name(value):
    return normalized_name(value)


def project_definition_owner(group, owner, document, *, scopes=None):
    result = dict(owner)
    if str(owner.get('evidence_scope_status') or '').startswith('EXCLUDED_'):
        return result
    proofs = list(owner.get('definition_section_proofs') or [])
    if proofs and any(not p.get('identity_values') for p in proofs):
        # Frozen v1 projected owners carry heading/block proofs but no subject
        # map. Revalidate those original headings instead of treating the
        # absent additive field as a foreign subject (or trusting a name alone).
        headings = {p.get('heading_block_id') for p in proofs}
        members = set(owner.get('member_block_ids') or [])
        available = definition_scopes(document) if scopes is None else scopes
        proofs = [p for p in available if p['heading_block_id'] in headings and members <= set(p['block_ids'])]
        result['definition_section_proofs'] = proofs
    if not proofs:
        if owner.get('owner_kind') in {'PageOwner', 'Page'} or owner.get('owner_scope_locality') == 'BROAD':
            return result
        annotation = scope_for_owner(owner, document, definition_scopes(document) if scopes is None else scopes)
        result.update(annotation)
        proofs = list(annotation.get('definition_section_proofs') or [])
    if not proofs:
        return result
    subject = normalized_name(group.get('canonical_identity'))
    kind=str(group.get('feature_type') or '').upper()
    compatible = [p for p in proofs if subject and subject in p.get('identity_values', [p.get('primary_identity')])
                  and (kind not in {'FUNCTION','OPERATOR','DATATYPE'} or not p.get('feature_type') or p['feature_type']==kind)]
    if not compatible:
        result.update({'evidence_scope_status': 'EXCLUDED_FOREIGN_SUBJECT',
                       'owner_scope_block_ids': [], 'owner_scope_reason_codes': ['DEFINITION_SUBJECT_MISMATCH']})
        return result
    # Only Evidence projection may include semantic subsections. Keep original
    # production Owner/Identity/Type declarations unchanged. Reuse the selected
    # Profile plan and require the same verified anchor, subject and type.
    from agents.owner.reference_definitions import reference_scopes
    expanded=[]
    for proof in compatible:
        if proof.get('definition_pattern')=='REFERENCE_DECLARATION_SECTION' and proof.get('reference_mode')=='HEADING_SECTION':
            plan=proof.get('scope_execution_plan')
            if plan:
                match=next((p for p in reference_scopes(document,{'HEADING_SECTION':plan},include_semantic_subsections=True)
                    if p['section_id']==proof['section_id'] and p['identity_values']==proof['identity_values']
                    and p['feature_type']==proof['feature_type'] and set(proof['block_ids'])<=set(p['block_ids'])),None)
                if match:
                    proof=dict(match,content_scope_origin='EVIDENCE_ONLY_SEMANTIC_SUBSECTION_PROJECTION',
                               identity_scope_block_ids=list(proof['block_ids']),
                               structure_execution=proof.get('structure_execution'))
        expanded.append(proof)
    from agents.semantic_stage.applicability import bounded_proof
    compatible=[bounded_proof(p,document) for p in expanded]
    from agents.owner.structure_execution import EXECUTORS
    for index,proof in enumerate(compatible):
        execution=proof.get('structure_execution') or {}
        if execution.get('structure') in EXECUTORS:
            refreshed=EXECUTORS[execution['structure']](document,[proof],execution['plan'])
            if refreshed:compatible[index]=refreshed[0]
    block_ids = {bid for p in compatible for bid in p['block_ids']}
    ordered = [b['block_id'] for b in sorted(document.get('blocks') or [], key=lambda b: int(b.get('order') or 0)) if b['block_id'] in block_ids]
    proofs = [dict(p, identity_unit_id=group.get('identity_unit_id'),
                   association_block_id=p['heading_block_id'] if subject == p.get('primary_identity') else p['identity_anchor_block_ids'][subject][0],
                   association='EXACT_DEFINITION_HEADING' if subject == p.get('primary_identity') else 'EXPLICIT_FORMAL_SIGNATURE_ALIAS')
              for p in compatible]
    result.update({'original_member_block_ids': list(owner.get('original_member_block_ids') or owner.get('member_block_ids') or []),
                   'member_block_ids': ordered, 'owner_scope_block_ids': ordered,
                   'owner_scope_mode': 'DEFINITION_SECTION_PROJECTION',
                   'definition_section_proofs': proofs, 'evidence_scope_status': 'COMPLETE'})
    return result
