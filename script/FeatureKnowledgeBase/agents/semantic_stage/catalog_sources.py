"""Bind a catalog summary to an independently defined, same-typed subject.

No new Identity/Type decisions: the actual selected identity cell must link to
an actual anchor of an already bound definition. Profile positions remain
authoritative even when the catalog prints only a name stub.
"""
import copy
from urllib.parse import urljoin
from agents.contracts.base import stable_id
from agents.contracts.catalog_membership import catalog_membership
from agents.owner.profile_execution import applies,locate_cells
from agents.owner.structure_execution import compile_structure_plan,execute_rows
from agents.owner.surface_patterns import formal_signatures,operator_label
from agents.profile_bootstrap.cold_start import profile_digest


def bind_catalog_sources(group,owners,indexes):
    if group.get('adapted_identity_kind'):return []
    profile=indexes.get('definition_profile') or {}
    plan=next((p for p in compile_structure_plan(profile) if p['structure']=='D' and p.get('execution')),None)
    if not plan:return []
    identity=str(group.get('canonical_identity') or '').upper();kind=group.get('feature_type')
    if kind not in {'FUNCTION','OPERATOR','DATATYPE'}:return []
    documents=indexes['documents'];reader=indexes['source_structure'];targets=set()
    for owner in owners:
        doc=documents.get(owner.get('page_id'),{});blocks={b['block_id']:b for b in doc.get('blocks',[])}
        for p in owner.get('definition_section_proofs',[]):
            if identity not in p.get('identity_values',[]):continue
            for bid in set((p.get('identity_anchor_block_ids') or {}).get(identity,[]))|{p.get('heading_block_id')}:
                if bid in blocks:targets.update(reader.observe(doc,blocks[bid]).get('definition_targets',[]))
    if not targets:return []
    cache=getattr(reader,'_catalog_source_index',None)
    cachekey=(profile_digest(profile),tuple(sorted(d['page_hash']for d in documents.values())))
    if cache is None or cache[0]!=cachekey:
        found={};rowowners={}
        for o in indexes['owners'].values():
            if o.get('owner_kind')=='TableRowOwner':
                for bid in o.get('member_block_ids',[]):rowowners.setdefault(bid,o)
        for doc in documents.values():
            for block in doc['blocks']:
                if block.get('block_kind')!='TABLE_ROW' or not applies(plan,block) or block['block_id'] not in rowowners:continue
                actual=reader.observe(doc,block)
                source={**block,**actual,'source_url':doc['url'],'source_hash':doc['page_hash']}
                slots=[]
                for locator in plan['execution']['identity']['alternatives']:
                    selected=locate_cells(source,locator,source=True)
                    if selected:
                        slots=selected;break
                if not slots or plan['execution']['identity']['cardinality']=='ONE' and len(slots)!=1:continue
                cells={c['cell_index']:c for c in (actual.get('table_context') or {}).get('cells',[])}
                for slot in slots:
                    names={n for n,_ in formal_signatures(slot['text'])};op=operator_label(slot['text'])
                    if op:names.add(op[0])
                    names.add(slot['text'].strip().upper())
                    for name in names:
                        for witness in catalog_membership(source,name):
                            if witness['dom_locator']!=slot['locator']:continue
                            cell=cells[slot['index']]
                            links={urljoin(doc['url'],l.get('href',''))for l in cell.get('links',[])}
                            found.setdefault((name,witness['feature_type']),[]).append((doc,block,rowowners[block['block_id']],witness,links))
        reader._catalog_source_index=(cachekey,found);cache=reader._catalog_source_index
    result=[]
    for doc,block,owner,witness,links in cache[1].get((identity,kind),[]):
        shared=sorted(targets&links)
        if not shared:continue
        sid=stable_id('bound_catalog',[doc['page_id'],block['block_id'],identity,kind])
        proof={'section_id':sid,'definition_pattern':'REFERENCE_DECLARATION_SECTION','reference_mode':'CATALOG_ROW',
            'heading_block_id':block['block_id'],'definition_anchor_block_ids':[block['block_id']],
            'identity_values':[identity],'primary_identity':identity,'feature_type':kind,
            'identity_anchor_block_ids':{identity:[block['block_id']]},'block_ids':[block['block_id']],
            'source_role':'CATALOG_ENTRY','dom_parent':block.get('dom_path'),
            'start_order':block.get('order'),'end_order_exclusive':(block.get('order')or 0)+1,
            'boundary_evidence':['CURRENT_ROW','EXPLICIT_LINK_TO_BOUND_DEFINITION'],
            'catalog_membership_proof':copy.deepcopy(witness),'source_citations':{block['block_id']:copy.deepcopy(block)}}
        matched=execute_rows(doc,[proof],plan)
        if not matched:continue
        execution=matched[0]['structure_execution']
        unit={'schema_version':'DefinitionUnit/v1','definition_unit_id':sid,'page_id':doc['page_id'],
            'source_url':doc['url'],'source_hash':doc['page_hash'],'owner_ids':[owner['owner_id']],
            'identity_values':[identity],'identity_anchor_block_ids':{identity:[block['block_id']]},
            'member_block_ids':[block['block_id']],'structural_members':execution['members'],
            'type_context':kind,'source_role':'CATALOG_ENTRY','structure_execution':execution,
            'declarations':execution['declarations'],'boundary':execution['boundary'],
            'profile_digest':profile_digest(profile),'status':'VERIFIED_STRUCTURE','reason_codes':[],
            'source_proof':proof,'producer_pattern':'EXPLICIT_CATALOG_DEFINITION_REFERENCE',
            'definition_targets':shared,'source_only':True,
            'catalog_reference_proof':{'source_block_id':block['block_id'],'source_hash':doc['page_hash'],
                'source_owner_id':owner['owner_id'],'source_url':doc['url'],
                'definition_targets':shared,'identity':identity,'feature_type':kind}}
        result.append({'owner':owner,'unit':unit})
    return result


def verified_catalog_reference(unit,block,owner,page,reader,owner_index,documents):
    """Positive content-association proof; never reassign an Identity owner."""
    proof=block.get('catalog_reference_proof') or {}
    identity=str(unit.get('canonical_identity') or '').upper();kind=unit.get('feature_type')
    bid=block.get('block_id')
    if (not proof or proof.get('identity')!=identity or proof.get('feature_type')!=kind
            or proof.get('source_owner_id')!=owner.get('owner_id')
            or owner.get('page_id')!=page.get('page_id') or bid not in owner.get('member_block_ids',[])
            or proof.get('source_block_id')!=bid or proof.get('source_hash')!=page.get('page_hash')
            or proof.get('source_url')!=page.get('url') or block.get('source_hash')!=page.get('page_hash')):return False
    raw=next((b for b in page.get('blocks',[])if b['block_id']==bid),None)
    if not raw:return False
    observed=reader.observe(page,raw)
    source={**raw,**observed,'source_url':page['url'],'source_hash':page['page_hash']}
    links={t for w in catalog_membership(source,identity)if w['feature_type']==kind for t in w['definition_targets']}
    permitted=links&set(proof.get('definition_targets',[]))
    if not permitted:return False
    for oid in unit.get('owner_ids',[]):
        defining=owner_index.get(oid,{})
        doc=documents.get(defining.get('page_id'),{});blocks={b['block_id']:b for b in doc.get('blocks',[])}
        for p in defining.get('definition_section_proofs',[]):
            if identity not in p.get('identity_values',[]):continue
            for anchor in set((p.get('identity_anchor_block_ids')or{}).get(identity,[]))|{p.get('heading_block_id')}:
                if anchor in blocks and permitted&set(reader.observe(doc,blocks[anchor]).get('definition_targets',[])):return True
    return False
