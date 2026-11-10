"""Source-verifiable definition units over immutable DocumentIR blocks.

This is a normalized view of existing definition proofs, not another owner
engine or permission to publish. Roles are structural candidates, not claims.
"""
from agents.contracts.base import stable_id
from agents.profile_bootstrap.cold_start import profile_digest


def build_definition_units(documents, owners, profile, *, source_structure=None, reference_documents=None):
    from agents.owner.scope_plan import compile_scope_plan
    catalog_plan=next((p for p in compile_scope_plan(profile) if p['relation']=='CATALOG_ROW'),None)
    pages={d['page_id']:d for d in documents}
    units={}
    from agents.owner.structure_execution import compile_structure_plan
    from agents.owner.profile_execution import applies
    explicit=[p for p in compile_structure_plan(profile) if p['structure']=='D' and p.get('execution')]
    for owner in owners:
        document=pages.get(owner.get('page_id'),{})
        blocks={b['block_id']:b for b in document.get('blocks',[])}
        for proof in owner.get('definition_section_proofs',[]):
            if proof.get('source_role')=='CATALOG_ENTRY' and not catalog_plan:
                continue
            anchors=proof.get('definition_anchor_block_ids') or []
            members=proof.get('block_ids') or []
            key=stable_id('definition_unit',[owner.get('page_id'),proof.get('section_id')])
            if key in units:
                units[key]['owner_ids']=sorted(set(units[key]['owner_ids']+[owner['owner_id']]))
                if (set(units[key]['member_block_ids'])!=set(members)
                        or units[key]['identity_values']!=list(proof.get('identity_values') or [])):
                    units[key]['status']='INVALID'
                    units[key]['reason_codes']=sorted(set(units[key]['reason_codes']+['DEFINITION_UNIT_SOURCE_CONFLICT']))
                continue
            errors=[]
            execution=proof.get('structure_execution') or {}
            if execution and execution.get('status')!='VERIFIED_STRUCTURE':
                errors.extend(execution.get('reason_codes') or ['STRUCTURE_EXECUTION_INVALID'])
            if not anchors or not set(anchors)<=set(members):errors.append('ANCHOR_OUTSIDE_DEFINITION')
            if any(b not in blocks for b in members):errors.append('SOURCE_BLOCK_MISSING')
            container=proof.get('dom_parent')
            if proof.get('definition_pattern')=='PROFILE_LOCAL_DEFINITION':
                if not container or any(not blocks.get(b,{}).get('dom_path','').startswith(container+' > ') for b in members):
                    errors.append('CROSS_CONTAINER')
            ordered=sorted((blocks[b] for b in members if b in blocks),key=lambda b:b.get('order') or 0)
            identity_anchors={b for values in (proof.get('identity_anchor_block_ids') or {}).values() for b in values}
            if proof.get('heading_block_id'):identity_anchors.add(proof['heading_block_id'])
            units[key]={
                'schema_version':'DefinitionUnit/v1','definition_unit_id':key,
                'page_id':owner.get('page_id'),'source_url':document.get('url'),
                'source_hash':document.get('page_hash'),'owner_ids':[owner['owner_id']],
                'identity_values':list(proof.get('identity_values') or []),
                'identity_anchor_block_ids':dict(proof.get('identity_anchor_block_ids') or {}),
                'member_block_ids':[b['block_id'] for b in ordered],
                'structural_members':execution.get('members') or [{
                    'block_id':b['block_id'],'order':b.get('order'),
                    'role':'SHARED_CONTEXT' if b['block_id'] in proof.get('shared_context_block_ids',[]) else
                           'IDENTITY_ANCHOR' if b['block_id'] in identity_anchors else
                           'CODE_CANDIDATE' if b.get('block_kind')=='CODE' else 'BODY_CANDIDATE'
                } for b in ordered],
                'type_context':proof.get('feature_type'),
                'source_role':proof.get('source_role','DEFINITION'),
                'structure_execution':proof.get('structure_execution'),
                'declarations':(proof.get('structure_execution') or {}).get('declarations',[]),
                'source_relations':list(proof.get('source_relations',[])),
                'boundary':{'container':container,'start_order':proof.get('start_order'),
                            'end_order_exclusive':proof.get('end_order_exclusive'),
                            'evidence':list(proof.get('boundary_evidence') or [])},
                'execution_plan':proof.get('pattern_contract') or proof.get('scope_execution_plan'),
                'producer_pattern':proof.get('definition_pattern') or proof.get('association'),
                'profile_digest':profile_digest(profile),
                'status':'INVALID' if errors else 'VERIFIED_STRUCTURE',
                'reason_codes':errors,'source_proof':proof,
            }
    if catalog_plan:
        from agents.semantic_stage.source_structure import SourceStructureIndex
        from agents.contracts.catalog_membership import catalog_membership
        from agents.owner.surface_patterns import formal_signatures,operator_label
        reader=source_structure or SourceStructureIndex()
        for owner in owners:
            if owner.get('owner_kind')!='TableRowOwner':continue
            document=pages.get(owner.get('page_id'),{})
            blocks={b['block_id']:b for b in document.get('blocks',[])}
            for bid in owner.get('member_block_ids',[]):
                block=blocks.get(bid,{})
                if block.get('block_kind')!='TABLE_ROW':continue
                if any(applies(plan,block,document) for plan in explicit):continue
                observed=reader.observe(document,block)
                source={**block,**observed,'source_url':document.get('url'),
                        'source_hash':document.get('page_hash'),'page_id':document.get('page_id')}
                names=set()
                for cell in (source.get('table_context') or {}).get('cells',[]):
                    text=' '.join(str(cell.get('text') or '').split())
                    signatures=formal_signatures(text)
                    op=operator_label(text)
                    names.update(n for n,_ in signatures)
                    if op:names.add(op[0])
                    names.add(text.upper())
                for name in sorted(names):
                    for witness in catalog_membership(source,name):
                        key=stable_id('definition_unit',[owner.get('page_id'),'catalog',bid,name,witness['feature_type']])
                        if key in units:
                            units[key]['owner_ids']=sorted(set(units[key]['owner_ids']+[owner['owner_id']]))
                            continue
                        units[key]={
                            'schema_version':'DefinitionUnit/v1','definition_unit_id':key,
                            'page_id':document.get('page_id'),'source_url':document.get('url'),
                            'source_hash':document.get('page_hash'),'owner_ids':[owner['owner_id']],
                            'identity_values':[name],'identity_anchor_block_ids':{name:[bid]},
                            'member_block_ids':[bid],
                            'structural_members':[{'block_id':bid,'order':block.get('order'),'role':'CATALOG_ROW'}],
                            'type_context':witness['feature_type'],'source_role':'CATALOG_ENTRY',
                            'boundary':{'container':block.get('dom_path'),'evidence':['SOURCE_VERIFIED_CATALOG_CELL']},
                            'execution_plan':dict(catalog_plan),'producer_pattern':'VERIFIED_CATALOG_MEMBERSHIP',
                            'profile_digest':profile_digest(profile),'status':'VERIFIED_STRUCTURE','reason_codes':[],
                            'source_proof':witness,'definition_targets':witness.get('definition_targets',[]),
                        }
    from agents.owner.structure_execution import execute_rows, compile_structure_plan
    row_plan=next((p for p in compile_structure_plan(profile) if p['structure']=='D'),None)
    if row_plan:
        for unit in units.values():
            if unit.get('structure_execution') or unit.get('producer_pattern')!='VERIFIED_CATALOG_MEMBERSHIP':
                continue
            proof={'section_id':unit['definition_unit_id'],'scope_execution_plan':catalog_plan,
                   'identity_values':unit['identity_values'],
                   'identity_anchor_block_ids':unit['identity_anchor_block_ids'],
                   'heading_block_id':unit['member_block_ids'][0],
                   'definition_anchor_block_ids':unit['member_block_ids'],
                   'block_ids':unit['member_block_ids'],'source_role':'CATALOG_ENTRY'}
            matched=execute_rows(pages[unit['page_id']],[proof],row_plan)
            if not matched:continue
            executed=matched[0]['structure_execution']
            unit['structure_execution']=executed
            unit['declarations']=executed['declarations']
    # Preserve physical catalog -> detail links when an explicit row contract
    # replaces the legacy catalog-membership unit. This is a source relation,
    # not permission to merge same-spelled features.
    reader=source_structure
    for unit in units.values():
        execution=unit.get('structure_execution') or {}
        if execution.get('schema_version')!='StructureExecution/v2' or execution.get('structure')!='D':continue
        from agents.semantic_stage.source_structure import SourceStructureIndex
        from agents.contracts.catalog_membership import catalog_membership
        reader=reader or SourceStructureIndex()
        document=pages[unit['page_id']]
        block=next((b for b in document.get('blocks',[]) if b['block_id']==unit['source_proof'].get('heading_block_id')),None)
        if block is None:continue
        observed=reader.observe(document,block)
        source={**block,**observed,'source_url':document.get('url'),'source_hash':document.get('page_hash')}
        targets=[]
        for name in unit['identity_values']:
            for witness in catalog_membership(source,name):
                targets.extend(t for t in witness.get('definition_targets',[]) if t not in targets)
        # Explicit identity columns also retain their physical declaration
        # links when a generic chapter title has no type vocabulary.
        if observed.get('source_structure_status')=='RESOLVED':
            from urllib.parse import urljoin,urlparse
            indexes={b.get('cell_index') for b in execution.get('identity_bindings',[]) if b.get('cell_index') is not None}
            for cell in (observed.get('table_context') or {}).get('cells',[]):
                if cell.get('cell_index') not in indexes:continue
                for link in cell.get('links',[]):
                    from agents.contracts.definition_entry import identifier_key
                    label=str(link.get('text') or '')
                    linked_names={identifier_key(label)}|{identifier_key(n) for n,_ in formal_signatures(label)}
                    op=operator_label(label)
                    if op:linked_names.add(identifier_key(op[0]))
                    if not linked_names.intersection(identifier_key(n) for n in unit['identity_values']):continue
                    target=urljoin(unit['source_url'],str(link.get('href') or ''))
                    if (urlparse(target).fragment and urlparse(target).hostname==urlparse(unit['source_url']).hostname
                            and target not in targets):targets.append(target)
        unit['definition_targets']=targets
    if any((u.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2' for u in units.values()):
        from agents.contracts.profile_references import bind_subject_references
        from agents.semantic_stage.source_structure import SourceStructureIndex
        reader=reader or SourceStructureIndex()
        bind_subject_references(list(units.values()),reference_documents if reference_documents is not None else documents,profile,reader)
    return sorted(units.values(),key=lambda u:u['definition_unit_id'])
