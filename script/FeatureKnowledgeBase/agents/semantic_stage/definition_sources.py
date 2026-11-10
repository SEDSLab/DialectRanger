"""Subject-local large source units with immutable fine-grained evidence refs."""
from agents.contracts.definition_unit import build_definition_units
from agents.contracts.catalog_membership import catalog_citation_valid
from agents.owner.definition_scope import normalized_name


def organize_definition_sources(group, owners, documents, source_blocks, *, profile=None, reader=None,
                                reference_documents=None, catalog_units=()):
    by_ref={b['block_id']:b for b in source_blocks}
    subject=normalized_name(group.get('canonical_identity'))
    kind=str(group.get('feature_type') or group.get('type_hint') or 'UNCERTAIN').upper()
    selected=[];excluded=[]
    units=build_definition_units(documents,owners,profile or {},source_structure=reader,
                                reference_documents=reference_documents)
    for unit in units+list(catalog_units):
        if subject not in unit['identity_values']:continue
        reason=None
        if unit['status']!='VERIFIED_STRUCTURE':reason='INVALID_DEFINITION_STRUCTURE'
        elif unit.get('type_context') and unit['type_context']!=kind:reason='DEFINITION_TYPE_CONTEXT_MISMATCH'
        elif unit['source_role']=='CATALOG_ENTRY' and not all(
                bid in by_ref and catalog_citation_valid(group,by_ref[bid]) for bid in unit['member_block_ids']):
            reason='CATALOG_CITATION_NOT_BOUND'
        if reason:
            excluded.append({'definition_unit_id':unit['definition_unit_id'],'reason':reason});continue
        missing=[]
        for bid in unit['member_block_ids']:
            b=by_ref.get(bid)
            if (not unit.get('source_url') or not unit.get('source_hash') or not b
                    or b.get('evidence_usage')=='DISCOVERY_ONLY' or b.get('noise_excluded_from_main')
                    or b.get('source_url')!=unit['source_url'] or b.get('source_hash')!=unit['source_hash']
                    or b.get('page_id')!=unit['page_id']):missing.append(bid)
        record=dict(unit,identity_unit_id=group.get('identity_unit_id'),
                    member_evidence_refs=list(unit['member_block_ids']),
                    binding_status='INCOMPLETE' if missing else 'COMPLETE',missing_evidence_refs=missing)
        selected.append(record)
        if not missing:
            for bid in unit['member_block_ids']:
                b=by_ref[bid]
                b.setdefault('definition_unit_ids',[]).append(unit['definition_unit_id'])
                b['definition_unit_ids']=sorted(set(b['definition_unit_ids']))
                b.setdefault('definition_source_roles',[]).append(unit['source_role'])
                b['definition_source_roles']=sorted(set(b['definition_source_roles']))
                if bid in (unit.get('source_proof') or {}).get('shared_context_block_ids',[]):
                    b['shared_definition_context']=True
                execution=unit.get('structure_execution') or {}
                if (unit.get('subject_reference_proofs') or unit.get('section_target_proof')) and bid==(unit.get('source_proof') or {}).get('heading_block_id'):
                    b['definition_targets']=sorted(set(b.get('definition_targets',[]))|set(unit.get('definition_targets',[])))
                    b.setdefault('profile_subject_reference_proofs',[]).extend(unit.get('subject_reference_proofs',[]))
                    if unit.get('section_target_proof'):
                        b.setdefault('profile_section_target_proofs',[]).append(unit['section_target_proof'])
                if (execution.get('schema_version')=='StructureExecution/v2' and execution.get('structure')=='D'
                        and bid in {a['block_id'] for a in execution.get('identity_bindings',[])}) :
                    b['definition_targets']=sorted(set(b.get('definition_targets',[]))|set(unit.get('definition_targets',[])))
                    b.setdefault('profile_definition_reference_proofs',[]).append({
                        'definition_unit_id':unit['definition_unit_id'],'source_hash':unit['source_hash'],
                        'identity_bindings':execution['identity_bindings'],'targets':unit.get('definition_targets',[])})
                if (execution.get('status')=='VERIFIED_STRUCTURE'
                        and execution.get('source_hash')==b.get('source_hash')
                        and execution.get('page_id')==b.get('page_id')):
                    for member in execution.get('members',[]):
                        if member['block_id']==bid and member.get('field_role'):
                            observed={'execution_id':execution['execution_id'],
                                      'structure':execution['structure'],
                                      'field_role':member['field_role'],
                                      'role_label_block_id':member['role_label_block_id'],
                                      'role_origin':member['role_origin'],
                                      'applies_to':member['applies_to']}
                            if observed not in b.setdefault('structure_field_roles',[]):
                                b['structure_field_roles'].append(observed)
    associations=[]
    pages={d['page_id']:d for d in documents}
    if reader:
        from urllib.parse import urldefrag
        for catalog in selected:
            if catalog['source_role']!='CATALOG_ENTRY' or catalog['binding_status']!='COMPLETE':continue
            for definition in selected:
                if definition['source_role']!='DEFINITION' or definition['binding_status']!='COMPLETE':continue
                for target in catalog.get('definition_targets',[]):
                    base,fragment=urldefrag(target)
                    if not fragment or base!=urldefrag(definition['source_url'])[0]:continue
                    anchor_ids={bid for values in definition['identity_anchor_block_ids'].values() for bid in values}
                    heading=definition['source_proof'].get('heading_block_id')
                    if heading:anchor_ids.add(heading)
                    refs=reader.fragment_block_refs(pages[definition['page_id']],fragment,anchor_ids,
                        container=definition['boundary'].get('container'))
                    if refs:
                        associations.append({'from_definition_unit_id':catalog['definition_unit_id'],
                            'to_definition_unit_id':definition['definition_unit_id'],
                            'relation':'EXPLICIT_CATALOG_DEFINITION_LINK','target_url':target,
                            'target_evidence_refs':refs,'production_identity_merge':False})
    relations={r['relation_id']:r for unit in selected for r in unit.get('source_relations',[])}
    return {'definition_units':selected,'definition_source_associations':associations,
            'definition_source_relations':sorted(relations.values(),key=lambda r:r['relation_id']),
            'excluded_definition_units':excluded,
            'definition_unit_integrity':{'status':'INCOMPLETE' if any(u['missing_evidence_refs'] for u in selected) else 'COMPLETE',
                'selected_units':len(selected),'complete_units':sum(u['binding_status']=='COMPLETE' for u in selected),
                'uncovered_evidence_refs':sorted(set(by_ref)-{b for u in selected if u['binding_status']=='COMPLETE' for b in u['member_evidence_refs']})}}
