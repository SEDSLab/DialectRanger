"""Finite, source-grounded definition relations over the supplied corpus.

No URL/name inventories, retrieval, alias inference or publication permission.
Initial adapters cover datatype member lists and explicit subject pages.
"""
import re
from agents.contracts.base import stable_id

PATTERN='SOURCE_RELATION_DEFINITION'
_NOISE=re.compile(r'(?:^| > )(?:nav|aside|footer|header|details|blockquote)(?:[.#:]| > |$)|'
                  r'(?:#|\.)(?:toc|sidebar|breadcrumb)',re.I)
_NON_TARGET=re.compile(r'\b(?:examples?|tutorial|mapping|connector|API|SDK|JDBC|ODBC|Java|Python|parameters?|affinit(?:y|ies))\b',re.I)

def norm(value):return ' '.join(str(value or '').split())
def main(block):return not _NOISE.search(str(block.get('dom_path') or ''))
def atomic(value):return bool(re.fullmatch(r'[A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*){0,2}',norm(value)))
def category(block):
    headings=' / '.join(block.get('heading_ancestry') or [])
    return ('DATATYPE' if not _NON_TARGET.search(headings) and
            re.search(r'\b(?:data[ -]?types?|datatype|type declarations?)\b',headings,re.I) else None)

def page_subject(text):
    value=re.sub(r'^\d+(?:\.\d+)*[.)]?\s+','',norm(text))
    match=re.fullmatch(r'(?:The\s+)?([A-Za-z_][\w$]*)\s+(Data\s+Type|Datatype|Type|Class)',value,re.I)
    return (match.group(1).upper(),match.group(2).upper()) if match else (None,None)

def citation(document,block):
    return {'page_id':document['page_id'],'source_url':document['url'],'source_hash':document['page_hash'],
            'block_id':block['block_id'],'dom_path':block.get('dom_path'),'text':block.get('text')}

def relation(document,anchor,kind,refs,*,members=()):
    return {'schema_version':'DefinitionSourceRelation/v1',
            'relation_id':stable_id('source_relation',[document['page_id'],anchor['block_id'],kind,list(members)]),
            'relation':kind,'identities':list(members),'feature_type':'DATATYPE',
            'status':'VERIFIED_STRUCTURE','evidence':[citation(document,b) for b in refs]}

def scope(document,anchor,name,members,relations,*,role,shared=()):
    ordered=sorted({b['block_id']:b for b in members}.values(),key=lambda b:b.get('order') or 0)
    return {'section_id':stable_id('relation_definition',[document['page_id'],anchor['block_id'],name]),
            'definition_pattern':PATTERN,'feature_type':'DATATYPE','source_role':role,
            'heading_block_id':anchor['block_id'],'identity_values':[name],'primary_identity':name,
            'identity_anchor_block_ids':{name:[anchor['block_id']]},'definition_anchor_block_ids':[anchor['block_id']],
            'block_ids':[b['block_id'] for b in ordered],'start_order':ordered[0].get('order'),
            'end_order_exclusive':int(ordered[-1].get('order') or 0)+1,'end_heading_block_id':None,
            'dom_path':anchor.get('dom_path'),'dom_parent':str(anchor.get('dom_path') or '').rsplit(' > ',1)[0],
            'scope_status':'COMPLETE','example_start_order':None,'source_anchor':document['url'],
            'association':'SOURCE_VERIFIED_DEFINITION_RELATION','association_block_id':anchor['block_id'],
            'boundary_evidence':['VERIFIED_RELATION_ENDPOINTS','EXPLICIT_DATATYPE_CONTEXT'],
            'anchor_citation':citation(document,anchor),'source_relations':relations,
            'shared_context_block_ids':[b['block_id'] for b in shared]}

def catalog_scopes(document):
    from agents.owner.definition_patterns import container_groups,_literal
    blocks=document.get('blocks',[]);groups={}
    containers=container_groups(document,'LIST_ITEM')
    for b in blocks:
        path=str(b.get('dom_path') or '')
        if b.get('block_kind')=='LIST_ITEM' and path not in containers:
            containers[path]=[b]
    for path,parts in containers.items():
        if len(parts)!=1:continue
        b=parts[0];value=norm(b.get('text'))
        if not main(b) or not atomic(value) or _literal(b)!=value or category(b)!='DATATYPE':continue
        # Group siblings by the same list, not by spelling or a page-wide title.
        groups.setdefault(path.rsplit(' > ',1)[0],[]).append(b)
    result=[]
    for container,anchors in groups.items():
        if len(anchors)<2:continue
        first=min(int(a.get('order') or 0) for a in anchors)
        last=max(int(a.get('order') or 0) for a in anchors)
        preceding=sorted([b for b in blocks if main(b) and int(b.get('order') or 0)<first
                          and not str(b.get('dom_path') or '').startswith(container+' > ')],
                         key=lambda b:b.get('order') or 0)
        intro=preceding[-1] if preceding else {}
        parent=str(intro.get('dom_path') or '').rsplit(' > ',1)[0]
        # Adjacent prose may be outside transparent list wrappers. Require
        # its actual ancestor containment, not an arbitrary preceding paragraph.
        intros=[intro] if (intro.get('block_kind')=='TEXT' and category(intro)=='DATATYPE'
                and container.startswith(parent+' > ')
                and re.search(r'\b(?:data[ -]?types?|datatype|types)\b',intro.get('text',''),re.I)) else []
        if not intros:continue
        shared=list(intros)
        following=sorted([b for b in blocks if main(b) and int(b.get('order') or 0)>last],key=lambda b:b.get('order') or 0)
        if following:
            tail=following[0]
            if (tail.get('block_kind')=='TEXT' and category(tail)=='DATATYPE'
                and str(tail.get('dom_path') or '').rsplit(' > ',1)[0]==parent
                and not re.search(r'\bexamples?\b',tail.get('text',''),re.I)):
                shared.append(tail)
        names=[norm(a['text']) for a in anchors]
        for anchor in anchors:
            name=norm(anchor['text'])
            rs=[relation(document,anchor,'CATALOG_MEMBER',[anchor,*intros],members=[name])]
            if shared:rs.append(relation(document,anchor,'SHARED_CONTEXT_FOR',shared,members=names))
            result.append(scope(document,anchor,name,[anchor,*shared],rs,role='CATALOG_MEMBER',shared=shared))
    return result

def page_scopes(document,membership):
    blocks=sorted([b for b in document.get('blocks',[]) if main(b)],key=lambda b:b.get('order') or 0)
    headings=[b for b in blocks if b.get('block_kind')=='HEADING']
    if not headings:return []
    heading=headings[0];name,label=page_subject(heading.get('text'))
    if not name or _NON_TARGET.search(str(heading.get('text') or '')):return []
    witnesses=membership.get(name,[])
    if label=='CLASS' and not witnesses:return []
    start=blocks.index(heading);end=len(blocks)
    for i in range(start+1,len(blocks)):
        if blocks[i].get('block_kind')=='HEADING':end=i;break
    region=blocks[start+1:end]
    positive=None
    for b in region:
        if b.get('block_kind')!='TEXT':continue
        text=norm(b.get('text'))
        if re.search(r'\b(?:does not|not a|not an|has no)\b',text,re.I):continue
        explicit=(re.match(r'^(?:A |An |The )?'+re.escape(name)+r'\s+(?:is|stores|represents|holds)\b',text,re.I)
                  or re.search(r'\bsupports?\s+(?:(?:a|an|native|built-in)\s+)*'+re.escape(name)+
                               r'(?:\s*\([^)]*\))?\s+(?:data\s+)?type\b',text,re.I))
        if explicit:positive=b;break
    if not positive:return []
    local=[heading,*region[region.index(positive):]]
    rs=[relation(document,heading,'DEFINITION_OF',[heading,positive],members=[name])]
    for witness in witnesses:
        rs.append({'schema_version':'DefinitionSourceRelation/v1','relation_id':stable_id('source_relation',
                  [document['page_id'],heading['block_id'],witness['relation_id']]),
                  'relation':'CATALOG_MEMBER','identities':[name],'feature_type':'DATATYPE',
                  'status':'VERIFIED_STRUCTURE','evidence':witness['evidence'],
                  'related_definition_block_id':heading['block_id']})
    return [scope(document,heading,name,local,rs,role='DEFINITION')]

def prepare_relation_documents(documents,profile):
    """Return additive document copies. Raw blocks and frozen inputs unchanged."""
    from agents.owner.scope_plan import compile_scope_plan
    plan_by_relation={p['relation']:p for p in compile_scope_plan(profile)}
    plans=set(plan_by_relation)
    if not plans & {'CATALOG_MEMBER_CONTEXT','PAGE_SUBJECT_RELATION'}:return documents
    catalogs={d['page_id']:catalog_scopes(d) if 'CATALOG_MEMBER_CONTEXT' in plans else [] for d in documents}
    membership={}
    for proofs in catalogs.values():
        for proof in proofs:
            for r in proof['source_relations']:
                if r['relation']=='CATALOG_MEMBER':membership.setdefault(proof['primary_identity'],[]).append(r)
    output=[]
    for d in documents:
        proofs=list(catalogs[d['page_id']])
        if 'PAGE_SUBJECT_RELATION' in plans:proofs+=page_scopes(d,membership)
        for proof in proofs:
            key='CATALOG_MEMBER_CONTEXT' if proof['source_role']=='CATALOG_MEMBER' else 'PAGE_SUBJECT_RELATION'
            proof['scope_execution_plan']=dict(plan_by_relation[key])
        relations={r['relation_id']:r for p in proofs for r in p['source_relations']}
        headings=[b for b in d.get('blocks',[]) if main(b) and b.get('block_kind')=='HEADING']
        if headings and 'PAGE_SUBJECT_RELATION' in plans:
            name,label=page_subject(headings[0].get('text'))
            if name in membership and not any(p['source_role']=='DEFINITION' for p in proofs):
                r=relation(d,headings[0],'RELATED_REFERENCE',[headings[0]],members=[name])
                r.update(status='REVIEW_REQUIRED',reason_codes=['SUBJECT_LOCAL_DEFINITION_NOT_PROVEN'])
                relations[r['relation_id']]=r
        output.append(dict(d,definition_source_relation_scopes=proofs,
                           definition_source_relations=sorted(relations.values(),key=lambda r:r['relation_id'])))
    from agents.owner.linked_syntax import prepare
    from agents.owner.local_callable import prepare as prepare_callables
    return prepare_operator_catalogs(prepare_callables(prepare(output,profile),profile),profile)


def prepare_operator_catalogs(documents,profile):
    """E also consumes explicit prose catalogs, not only native list tags."""
    from agents.owner.scope_plan import compile_scope_plan
    if 'CATALOG_MEMBER_CONTEXT' not in {p['relation'] for p in compile_scope_plan(profile)}:return documents
    result=[]
    for document in documents:
        proofs=[]
        for block in document.get('blocks',[]):
            observed=block.get('definition_context') or {}
            if (block.get('block_kind') not in {'TEXT','DEFINITION','LIST_ITEM'}
                    or observed.get('role')!='OPERATOR_CATALOG' or observed.get('domain')!='DOCUMENT_FEATURE'
                    or observed.get('declaration_origin')=='EXPLICIT_OPERATOR_SELF_DEFINITION'
                    or _NON_TARGET.search(' / '.join(block.get('heading_ancestry',[])))):continue
            for member in observed.get('members',[]):
                name=member['identity']
                rel=relation(document,block,'CATALOG_MEMBER',[block],members=[name]);rel['feature_type']='OPERATOR'
                p=scope(document,block,name,[block],[rel],role='CATALOG_MEMBER')
                p.update(feature_type='OPERATOR',configured_structure='E',operator_catalog_proof=citation(document,block),
                         scope_execution_plan={'relation':'CATALOG_MEMBER_CONTEXT'})
                proofs.append(p)
        result.append(dict(document,definition_source_relation_scopes=list(document.get('definition_source_relation_scopes',[]))+proofs))
    return result

def verified_relation(proof,block,name):
    if proof.get('operator_catalog_proof'):
        from agents.owner.linked_syntax import frozen_node
        citation=proof['operator_catalog_proof'];provenance=block.get('provenance') or {}
        if block['block_id']!=citation['block_id'] or name not in proof['identity_values']:return None
        actual=dict(citation,html_path=provenance.get('html_path'))
        if provenance.get('page_hash')!=citation['source_hash'] or provenance.get('source_url')!=citation['source_url']:return None
        node=frozen_node(actual)
        if node is None:return None
        from agents.document_structure_v2.definition_observations import observe_definition
        observed=observe_definition(node)
        if (observed.get('role')=='OPERATOR_CATALOG' and observed.get('domain')=='DOCUMENT_FEATURE'
                and any(m['identity']==name for m in observed.get('members',[]))):
            return {'feature_type':'OPERATOR','identity_values':[name],'formal_value':None}
        return None
    if proof.get('local_callable_proof'):
        from agents.owner.local_callable import verified
        return verified(proof,block,name)
    if proof.get('linked_syntax_proof'):
        from agents.owner.linked_syntax import verified
        return verified(proof,block,name)
    if proof.get('member_collection'):
        from agents.owner.member_collections import verified
        return verified(proof,block,name)
    if proof.get('definition_pattern')!=PATTERN or name not in proof.get('identity_values',[]):return None
    c=proof.get('anchor_citation') or {}
    if (block.get('block_id')!=proof.get('heading_block_id') or block.get('block_id')!=c.get('block_id')
        or block.get('dom_path')!=c.get('dom_path') or block.get('text')!=c.get('text')
        or not main(block) or block['block_id'] not in proof.get('block_ids',[])):return None
    if any(block.get(k) and block[k]!=c.get(k) for k in ['source_url','source_hash','page_id']):return None
    if proof.get('source_role')=='CATALOG_MEMBER':
        if norm(block.get('text'))!=name or category(block)!='DATATYPE':return None
    elif page_subject(block.get('text'))[0]!=name:return None
    if not proof.get('source_relations') or any(r.get('status')!='VERIFIED_STRUCTURE' for r in proof['source_relations']):return None
    return {'feature_type':'DATATYPE','identity_values':[name],'formal_value':None}
