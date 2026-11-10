"""Conservative same-subject reference graph, not spelling-based grouping."""
from urllib.parse import urlparse,urldefrag
import re


def _local_declaration_links(unit,block,witness):
    """An explicit same-document declaration link, not a name-based edge."""
    from agents.contracts.publication_integrity import _call_head
    observed=block.get('observed_context') or {};local=observed.get('local_definition') or {}
    name=unit['canonical_identity']
    if (witness.get('origin')!='OBSERVED_LOCAL_FUNCTION_DECLARATION'
            or block.get('source_structure_status')!='RESOLVED'
            or local.get('role')!='FUNCTION_DECLARATION_LIST' or observed.get('raw_node_kind')!='LI'
            or _call_head(local.get('item_text'))!=name or local.get('item_text')!=block.get('text')):return set()
    return {(r['target_url'],block['source_hash']) for r in observed.get('definition_references',[])
        if (str(r.get('label') or '').strip().upper()==name
            and urldefrag(r['target_url'])[0]==urldefrag(block['source_url'])[0]
            and urlparse(r['target_url']).fragment and r.get('dom_locator'))}


def _local_catalog_section(unit,block,witness):
    from agents.contracts.publication_integrity import _call_head
    observed=block.get('observed_context') or {};section=observed.get('definition_section') or {}
    name=unit['canonical_identity'];heading=section.get('heading_text','')
    if (witness.get('origin')!='TYPED_CATALOG_CELL' or block.get('source_structure_status')!='RESOLVED'
            or not section.get('heading_locator') or not re.search(r'\bfunctions?\b',heading,re.I)
            or re.search(r'\b(?:examples?|mapping|api|jdbc|odbc|tutorial)\b',heading,re.I)
            or not re.search(r'(?<![\w$])'+re.escape(name)+r'\s*\(',heading,re.I)):return set()
    cells=(block.get('table_context') or {}).get('cells',[])
    cell=next((c for c in cells if c.get('dom_locator')==witness.get('dom_locator')),None)
    if (not cell or cell.get('header_role')!='DATA_CELL' or cell.get('header_status') not in {'EXPLICIT','OBSERVED_GRID'}
            or _call_head(cell.get('text'))!=name or cell.get('text')!=witness.get('surface')
            or not any(re.fullmatch(r'\s*(?:function|routine|signature)\s*',h.get('text',''),re.I) for h in cell.get('headers',[]))):return set()
    return {(target,block['source_hash']) for target in section.get('anchor_urls',[])
        if urldefrag(target)[0]==urldefrag(block['source_url'])[0] and urlparse(target).fragment}


def prove_reference_equivalence(members, units, bundles, admissions):
    members=sorted(members,key=lambda r:r['provenance']['identity_unit_id'])
    ids=[r['provenance']['identity_unit_id'] for r in members]
    identities={(units.get(uid,{}).get('canonical_identity'),units.get(uid,{}).get('feature_type')) for uid in ids}
    if len(identities)!=1 or any(uid not in units for uid in ids):return None
    # Adapted identities retain their existing stricter citation/subset contract.
    if any(units[uid].get('adapted_identity_kind') for uid in ids):return None
    targets={};citations={};verified_citations={};declaration_links={};catalog_sections={}
    for uid,record in zip(ids,members):
        blocks={b['block_id']:b for b in bundles.get(uid,{}).get('source_blocks',[])}
        # Validate every imported claim against its OWN unchanged source scope.
        for values in record.get('semantics',{}).values():
            for claim in values:
                if not isinstance(claim,dict):return None
                evidence=claim.get('evidence') or {};source=blocks.get(evidence.get('block_id'))
                if not source:return None
                if evidence.get('url') and evidence['url']!=source.get('source_url'):return None
                if evidence.get('page_hash') and evidence['page_hash']!=source.get('source_hash'):return None
        targets[uid]=set();citations[uid]=set();verified_citations[uid]=set()
        declaration_links[uid]=set();catalog_sections[uid]=set()
        for w in (admissions.get(uid,{}).get('publication_integrity') or {}).get('subject_witnesses',[]):
            b=blocks.get(w.get('block_id'))
            if not b or any(not b.get(k) or b[k]!=w.get(k) for k in ('source_url','source_hash')):continue
            if w.get('feature_type')!=units[uid].get('feature_type'):continue
            declaration_links[uid].update(_local_declaration_links(units[uid],b,w))
            catalog_sections[uid].update(_local_catalog_section(units[uid],b,w))
            observed=set(b.get('definition_targets') or [])
            if w.get('origin')=='VERIFIED_CATALOG_MEMBERSHIP':
                from agents.contracts.catalog_membership import catalog_membership
                observed={t for p in catalog_membership(b,units[uid]['canonical_identity'])
                          if p['feature_type']==w['feature_type'] and p['dom_locator']==w.get('dom_locator')
                          for t in p['definition_targets']}
            targets[uid].update(t for t in w.get('definition_targets') or [] if t in observed and urlparse(t).fragment)
            citation=(b['block_id'],w['feature_type'],w.get('surface'),w['source_url'],w['source_hash'],w.get('dom_locator'))
            citations[uid].add(citation)
            from agents.owner.definition_patterns import verified_pattern
            if w.get('origin')=='DEFINITION_SECTION' and any(verified_pattern(p,b,units[uid]['canonical_identity'])
                    for p in b.get('definition_section_proofs',[])):
                verified_citations[uid].add(citation)
    edges=[];reached={ids[0]}
    for i,left in enumerate(ids):
        for right in ids[i+1:]:
            shared=sorted(targets[left]&targets[right]);same=verified_citations[left]&verified_citations[right]
            linked=sorted((declaration_links[left]&catalog_sections[right])|(declaration_links[right]&catalog_sections[left]))
            if shared or same or linked:
                edges.append({'left_unit_id':left,'right_unit_id':right,
                    'relation':'SAME_OBSERVED_DEFINITION_TARGET' if shared else
                        'SAME_VERIFIED_DEFINITION_CITATION' if same else 'EXPLICIT_DECLARATION_LINK_TO_VERIFIED_MEMBER_SECTION',
                    'definition_targets':shared,'shared_source_citations':[list(c) for c in sorted(same)],
                    'same_document_hash_links':[{'target_url':target,'source_hash':digest} for target,digest in linked]})
    changed=True
    while changed:
        changed=False
        for edge in edges:
            pair={edge['left_unit_id'],edge['right_unit_id']}
            if reached&pair and not pair<=reached:reached.update(pair);changed=True
    if reached!=set(ids):return None
    return {'proof':'CONNECTED_SOURCE_VERIFIED_REFERENCE_GRAPH','edges':edges,'identity_unit_ids':sorted(ids)}
