"""Direct explicit heading-location execution, independent of native nomination."""
from agents.contracts.base import stable_id


def configured_heading_proofs(document,plan,diagnostics):
    from agents.owner.profile_execution import applies,identity_bindings,declarations,norm
    from agents.owner.definition_scope import navigation_block,execution_heading_container,heading_field_label,heading_text,_LABELS
    import re
    if plan['structure'] not in {'A','B'} or not plan.get('execution'):return []
    if not any(l['kind']=='HEADING' for l in plan['execution']['identity']['alternatives']):return []
    authoritative=plan['execution'].get('authority')=='EXPLICIT_DEFINITION'
    stops={norm(v) for v in plan['execution'].get('boundary',{}).get('stop_headings',[])}
    blocks=sorted(document.get('blocks',[]),key=lambda b:b.get('order',0))
    fields=set(_LABELS)
    fields.update(norm(label) for f in plan['execution'].get('fields',[]) for label in f['locator'].get('labels',[]))
    headings=[b for b in blocks if b.get('block_kind')=='HEADING' and not navigation_block(b)]
    result=[]
    for anchor in headings:
        if plan['structure']=='A' and not authoritative and anchor is not headings[0]:continue
        if not applies(plan,anchor,document) or not authoritative and norm(anchor['text']) in fields:continue
        start=blocks.index(anchor);level=anchor.get('heading_level') or 1
        end=next((i for i in range(start+1,len(blocks)) if blocks[i].get('block_kind')=='HEADING'
                  and not navigation_block(blocks[i])
                  and ((blocks[i].get('heading_level') or 1)<=level or norm(blocks[i].get('text')) in stops)),len(blocks))
        parent=execution_heading_container(anchor)
        members=[b for b in blocks[start:end] if not navigation_block(b) and
                 (b is anchor or not parent or b.get('dom_path','').startswith(parent+' > '))]
        proof={'heading_block_id':anchor['block_id'],'block_ids':[b['block_id'] for b in members]}
        found,error=identity_bindings(proof,document,plan)
        if error:
            diagnostics.append({'structure':plan['structure'],'plan_id':plan.get('plan_id'),
                'block_id':anchor['block_id'],'reason':error,'status':'REJECTED_BINDING'})
            continue
        ds=[d for f in found for d in f['declarations']]
        names={d['identity'] for d in ds}
        # Co-declared heads in the same complete Syntax block are identities,
        # not arbitrary callable mentions. Keep their physical code anchors;
        # this establishes co-definition, never an inferred canonical alias.
        from agents.owner.declaration_binding import syntax_declarations
        co_bindings=[]
        for b in ([] if authoritative else members[1:]):
            if b.get('block_kind')!='CODE' or not any(norm(h) in {'syntax','signature','synopsis','grammar'} for h in b.get('heading_ancestry',[])):continue
            local_labels=[norm(h) for h in reversed(b.get('heading_ancestry',[])) if norm(h) in fields]
            if local_labels and local_labels[0] not in {'syntax','signature','synopsis','grammar'}:continue
            declared=syntax_declarations(b['text'])
            if (plan['execution']['identity']['cardinality']!='ONE_OR_MORE'
                    or not ds or any(d['feature_type']!='FUNCTION' for d in ds)):continue
            if not names.intersection(d['identity'] for d in declared):continue
            extra=[d for d in declared if d['identity'] not in names]
            if extra:
                co_bindings.append({'block_id':b['block_id'],'value':b['text'],'declarations':extra,'locator':{'kind':'VERIFIED_ANCHOR'}})
                ds.extend(extra);names.update(d['identity'] for d in extra)
        found.extend(co_bindings)
        # A bare category-like heading is not sufficient. Require a literal
        # declaration in the same scope or an explicitly observed type heading.
        formal=any(d.get('formal_value') for d in ds)
        for b in members[1:]:
            if b.get('block_kind')=='HEADING' and norm(b['text']) in {'example','examples'}:break
            if b.get('block_kind')=='CODE':
                formal= formal or any(d['identity'] in names for d in declarations(b['text'],b))
        from agents.owner.typed_page import literal_heading,datatype_metadata,operator_metadata
        typed_literal=bool(ds and all(d['feature_type'] in {'DATATYPE','OPERATOR'} for d in ds)
            and literal_heading(anchor)
            and any(b.get('block_kind')=='TEXT' for b in members[1:]))
        from agents.owner.reference_definitions import names_from_heading
        labeled=names_from_heading(re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]','',anchor['text']).strip())
        typed_label=bool(labeled and set(labeled[1])==names and any(b.get('block_kind')=='TEXT' for b in members[1:]))
        if not authoritative and not formal and not typed_literal and not typed_label and not datatype_metadata(anchor) and not operator_metadata(anchor) and (anchor.get('definition_context') or {}).get('role')!='DATATYPE_DEFINITION_HEADING':
            diagnostics.append({'structure':plan['structure'],'plan_id':plan.get('plan_id'),
                'block_id':anchor['block_id'],'reason':'LOCAL_DEFINITION_NOT_PROVEN','status':'REJECTED_BINDING'})
            continue
        def foreign_heading(block):
            if block.get('block_kind')!='HEADING':return False
            text=heading_text(block['text']).rstrip(':').strip().casefold()
            if any(heading_field_label(block['text'],name,fields) for name in names):return False
            # Named field headings identify their own subject even without a
            # callable signature ("OTHER Examples", "Syntax for OTHER").
            named_field=any(re.fullmatch(r'.+?\s+'+re.escape(label),text)
                            or re.fullmatch(re.escape(label)+r'\s+(?:of|for)\s+.+',text)
                            for label in fields)
            candidates=declarations(block['text'],block)
            explicit=any(n.get('element_name')=='code' for n in block.get('inline_nodes',[]))
            from agents.owner.definition_scope import normalized_name
            subject=normalized_name(block['text'])
            # Bare nested subjects need their own local declaration before
            # they can invalidate a containing single-subject scope.
            local=[];defining_prose=False
            from agents.owner.definition_scope import _DEFINITION
            for following in members[members.index(block)+1:]:
                if (following.get('block_kind')=='HEADING' and
                        (following.get('heading_level') or 1)<=(block.get('heading_level') or 1)):break
                if following.get('block_kind')=='CODE':
                    local.extend(syntax_declarations(following['text']))
                if following.get('block_kind')=='TEXT' and _DEFINITION.search(following.get('text','')):
                    defining_prose=True
            if subject not in names and any(d['identity']==subject for d in local):return True
            if (subject not in names and defining_prose and re.fullmatch(r'[A-Za-z_][\w$]*',block['text'])
                    and candidates):return True
            return bool(named_field or any(d['identity'] not in names and
                (d.get('formal_value') or explicit) for d in candidates))
        foreign=[] if authoritative else [b for b in members[1:] if foreign_heading(b)]
        if foreign:
            diagnostics.append({'structure':plan['structure'],'plan_id':plan.get('plan_id'),
                'block_id':anchor['block_id'],'reason':'AMBIGUOUS_NESTED_SUBJECT_BOUNDARY','status':'REJECTED_BOUNDARY'})
            continue
        for kind in sorted({d['feature_type'] for d in ds}):
            own=[d for d in ds if d['feature_type']==kind];identities=[d['identity'] for d in own]
            result.append({**proof,'section_id':stable_id('configured_heading',[document['page_id'],anchor['block_id'],plan.get('plan_id'),kind]),
                'explicit_definition_authority':authoritative,
                'configured_structure':plan['structure'],'feature_type':kind,'identity_values':identities,
                'identity_anchor_block_ids':{n:[f['block_id'] for f in found if any(d['identity']==n for d in f['declarations'])] for n in identities},
                'definition_anchor_block_ids':list(dict.fromkeys(f['block_id'] for f in found)),'primary_identity':identities[0],
                'source_role':'DEFINITION','association':'PROFILE_HEADING_LOCATION','dom_path':anchor.get('dom_path'),
                'dom_parent':parent,'source_anchor':document.get('url'),
                'start_order':anchor.get('order'),'end_order_exclusive':blocks[end].get('order') if end<len(blocks) else None,
                'end_heading_block_id':blocks[end]['block_id'] if end<len(blocks) else None,
                'example_start_order':next((b['order'] for b in members if b.get('block_kind')=='HEADING'
                                          and norm(b['text']) in {'example','examples'}),None),
                'scope_status':'COMPLETE','boundary_evidence':['PROFILE_IDENTITY_LOCATION','HEADING_LEVEL','DOM_PARENT_CONTAINMENT'],
                'subject_formal_values':{d['identity']:{anchor['block_id']:[d['formal_value']]} for d in own if d.get('formal_value')},
                'identity_bindings':found})
    return result
