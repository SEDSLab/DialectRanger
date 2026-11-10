"""Shared source membership checks and conservative optional-field isolation."""


def projected_owners(unit,bundle,owner_index):
    result=dict(owner_index);bound={o['owner_id']:o for o in bundle.get('owners',[])}
    for oid in unit.get('owner_ids',[]):
        if oid in result and oid in bound:
            original=result[oid];observed=bound[oid]
            result[oid]={**original,**observed,'page_id':original.get('page_id'),
                'member_block_ids':list(observed.get('owner_scope_block_ids') or observed.get('member_block_ids') or [])}
    return result


def block_binding(block,unit,owner_index,page_index,block_index,original_owners,reader,fallback_owners=()):
    bid=block.get('block_id') or block.get('evidence_ref');pair=block_index.get(bid)
    ids=set(block.get('owner_ids') or fallback_owners)
    if not pair or not ids:return False,0
    page,_=pair;associations=0
    if block.get('source_hash') and block['source_hash']!=page.get('page_hash'):return False,0
    if block.get('source_url') and block['source_url']!=page.get('url'):return False,0
    for oid in ids:
        owner=owner_index.get(oid)
        if block.get('evidence_usage')=='DISCOVERY_ONLY':owner=original_owners.get(oid,owner)
        if owner and oid not in unit.get('owner_ids',[]) and block.get('catalog_reference_proof'):
            from agents.semantic_stage.catalog_sources import verified_catalog_reference
            if verified_catalog_reference(unit,block,owner,page,reader,owner_index,page_index):
                associations+=1;continue
        if not owner or oid not in unit.get('owner_ids',[]) or owner.get('page_id')!=page.get('page_id'):return False,associations
        if bid not in owner.get('member_block_ids',[]):
            from agents.semantic_stage.applicability import verified_supplement
            if not verified_supplement(unit,block,owner,page,reader):return False,associations
            associations+=1
    return True,associations


def isolate_optional_sources(unit,bundle,owners,documents,reader):
    """A bad optional block cannot invalidate an intact independent identity.

    Unknown/core blocks are retained to fail closed. No rejected block is
    silently repaired, relabeled as trusted, or used as a semantic witness.
    """
    from agents.contracts.publication_integrity import subject_witnesses
    core={o.get('source_block_id') for o in unit.get('occurrence_records',[]) if o.get('defining')}
    for o in bundle.get('owners',[]):
        for p in o.get('definition_section_proofs',[]):core.update(p.get('definition_anchor_block_ids',[]))
    witnesses,_=subject_witnesses(unit,bundle)
    for w in witnesses:
        if w['feature_type']==unit.get('feature_type'):core.update(w['evidence_refs'])
    for p in unit.get('adaptation_provenance',[]):core.update(p.get('source_block_ids',[]))
    if not core:return bundle
    oi={o['owner_id']:o for o in owners};oi_projected=projected_owners(unit,bundle,oi)
    pi={d['page_id']:d for d in documents};bi={b['block_id']:(d,b) for d in documents for b in d.get('blocks',[])}
    bad=[]
    for b in bundle.get('source_blocks',[]):
        good,_=block_binding(b,unit,oi_projected,pi,bi,oi,reader,[o['owner_id'] for o in bundle.get('owners',[])])
        if not good and b['block_id'] not in core:bad.append(b)
    if not bad:return bundle
    ids={b['block_id'] for b in bad};result=dict(bundle)
    for key,values in bundle.items():
        if isinstance(values,list):result[key]=[v for v in values if not isinstance(v,dict) or (v.get('block_id') or v.get('evidence_ref')) not in ids]
    result['field_source_quarantine']=[{'block_id':b['block_id'],'reason':'INVALID_OPTIONAL_SOURCE_BINDING',
                                      'status':'QUARANTINED','raw_source':b} for b in bad]
    return result
