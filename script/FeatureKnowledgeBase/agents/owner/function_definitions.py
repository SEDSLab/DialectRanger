"""Source-verified function declaration sections, including bare SQL forms.

Names come only from visible labels and whole formal declarations. Co-definition
does not imply a canonical alias merge. Profile selects existing heading/paragraph
recipes; examples, APIs and incidental callable mentions never prove a subject.
"""
import re
from agents.owner.surface_patterns import formal_signatures

PATTERN='FUNCTION_DECLARATION_SECTION'
LABELS={'syntax','signature','grammar','description','parameters','arguments','returns',
        'return type','examples','example','usage','notes','note','results'}
NOISE=re.compile(r'\b(?:examples?|tutorial|API|SDK|JDBC|ODBC|programming|mapping|connector)\b',re.I)
def norm(v):return ' '.join(str(v or '').split())
def subjects(text,*,heading=False):
    value=re.sub(r'^\d+(?:\.\d+)*[.)]?\s+','',norm(text))
    if heading:
        m=re.fullmatch(r'(?:The\s+)?(.+?)\s+(?:SQL\s+)?(?:function|routine)',value,re.I)
        if not m:return []
        value=m.group(1)
    parts=re.split(r'\s+or\s+|\s*,\s*',value,flags=re.I)
    names=[]
    for part in parts:
        m=re.fullmatch(r'([A-Z][A-Z0-9_$]*(?: [A-Z][A-Z0-9_$]*){0,2})(?:\([^()]*\))?',part)
        if not m or NOISE.search(part):return []
        names.append(m.group(1))
    return list(dict.fromkeys(names))

def declarations(block,names):
    text=norm(block.get('text'));sigs=formal_signatures(text)
    if sigs:return [(n,v) for n,v in sigs if n in names] if all(n in names for n,_ in sigs) else []
    from agents.owner.definition_patterns import _balanced
    for name in names:
        if re.fullmatch(re.escape(name)+r'\s*\[.+\]',text) and ';' not in text and _balanced(text):
            return [(name,text)]
    # Bare multiword spelling is not inferred as an alias of an underscore name.
    return [(text,text)] if text in names else []

def literal(b):
    text=norm(b.get('text'))
    return b.get('block_kind') in {'CODE','SIGNATURE'} or any(
        n.get('element_name')=='code' and norm(n.get('text'))==text for n in b.get('inline_nodes',[]))

def function_scopes(document,plans):
    from agents.owner.definition_scope import navigation_block
    blocks=sorted(document.get('blocks',[]),key=lambda b:int(b.get('order') or 0));proofs=[]
    for i,anchor in enumerate(blocks):
        if navigation_block(anchor):continue
        context=' / '.join(anchor.get('heading_ancestry') or [])
        if NOISE.search(context):continue
        heading=anchor.get('block_kind')=='HEADING'
        if heading:
            if 'HEADING_SECTION' not in plans:continue
            names=subjects(anchor.get('text'),heading=True)
        else:
            if 'PARAGRAPH_SIGNATURE' not in plans or anchor.get('block_kind')!='TEXT':continue
            text=norm(anchor.get('text'))
            bold=any(n.get('element_name') in {'strong','b'} and norm(n.get('text'))==text
                     for n in anchor.get('inline_nodes',[]))
            if not bold or not re.search(r'\b(?:functions?|routines?)\b',context,re.I):continue
            names=subjects(text)
        if not names:continue
        parent=str(anchor.get('dom_path') or '').rsplit(' > ',1)[0]
        region=[]
        for b in blocks[i+1:]:
            path=str(b.get('dom_path') or '')
            if parent and not path.startswith(parent+' > '):break
            if navigation_block(b):break
            if b.get('block_kind')=='HEADING':
                label=norm(b.get('text')).casefold().rstrip(':')
                if heading:
                    if (b.get('heading_level') or 1)<=(anchor.get('heading_level') or 1) or subjects(b.get('text'),heading=True):break
                elif label not in LABELS:break
            if not heading and b.get('block_kind')=='TEXT' and not literal(b) and any(
                    n.get('element_name') in {'strong','b'} and norm(n.get('text'))==norm(b.get('text'))
                    for n in b.get('inline_nodes',[])) and re.fullmatch(r'[A-Z][A-Z0-9_$ ,/()\[\]-]*',norm(b.get('text'))):break
            region.append(b)
        prefix=[]
        for b in region:
            if re.search(r'\b(?:example|examples|usage|results)\b',' / '.join(b.get('heading_ancestry') or []),re.I):break
            if b.get('block_kind')=='HEADING' and norm(b.get('text')).casefold() in {'example','examples','usage','results'}:break
            prefix.append(b)
        # Require defining prose AND a whole literal declaration for every head.
        prose=next((b for b in prefix if b.get('block_kind')=='TEXT' and (
            re.match(r'^(?:Returns?|Computes?|Converts?|Determines?|Produces?|Calculates?)\b',norm(b.get('text')),re.I)
            or re.match(r'^The\s+.+?\s+(?:function|routine)\s+(?:returns?|is|converts?|computes?)\b',norm(b.get('text')),re.I)
        )),None)
        if not prose and heading:
            # Heading and literal declarations prove the subject; defining
            # prose is not limited to a small list of English opening verbs.
            prose=next((b for b in prefix if b.get('block_kind')=='TEXT'
                and not literal(b) and not norm(b.get('text')).lower().startswith('see ')
                and all(re.search(r'\b'+re.escape(n)+r'\b',norm(b.get('text')),re.I) for n in names)),None)
        if not prose and heading:continue
        if any(literal(b) and formal_signatures(norm(b.get('text')))
               and any(n not in names for n,_ in formal_signatures(norm(b.get('text'))))
               for b in prefix):continue
        formal={n:{} for n in names}
        for b in prefix:
            if not literal(b):continue
            # Bare declarations require a Syntax region or the immediate label
            # sibling. A code mention further down prose is not a declaration.
            ds=declarations(b,names)
            if not ds:continue
            if not heading and prefix.index(b)>1 and not re.search(r'\b(?:syntax|signature|grammar)\b',' / '.join(b.get('heading_ancestry') or []),re.I):continue
            if heading and not re.search(r'\b(?:syntax|signature|grammar)\b',' / '.join(b.get('heading_ancestry') or []),re.I):continue
            for n,v in ds:formal[n].setdefault(b['block_id'],[]).append(v)
        if not all(formal.values()):continue
        if not heading and prose and len(names)==1 and prefix and formal_signatures(prefix[0].get('text')):
            # Already handled by the established label/signature recipe.
            # Do not introduce a competing owner for a working definition.
            continue
        # The local prose must refer to the declared subject, not another one.
        text=norm(prose.get('text')) if prose else ''
        if text.lower().startswith('the ') and not all(re.search(r'\b'+re.escape(n)+r'\b',text,re.I) for n in names):continue
        if prose:
            members=[anchor,*region]
        else:
            # A declaration pair is a catalog source, not permission for the
            # last member to absorb a following shared/concept paragraph.
            formal_ids={bid for values in formal.values() for bid in values}
            members=[anchor,*[b for b in region if b['block_id'] in formal_ids]]
        citations={b['block_id']:{'text':b.get('text'),'dom_path':b.get('dom_path'),
            'heading_ancestry':b.get('heading_ancestry',[])} for b in members}
        anchors=list(dict.fromkeys([anchor['block_id']]+[bid for n in names for bid in formal[n]]))
        proofs.append({'section_id':'function_definition:'+anchor['block_id'],'definition_pattern':PATTERN,
            'feature_type':'FUNCTION','heading_block_id':anchor['block_id'],'label_block_id':anchor['block_id'],
            'identity_values':names,'primary_identity':names[0],
            'identity_anchor_block_ids':{n:[anchor['block_id'],*formal[n]] for n in names},
            'definition_anchor_block_ids':anchors,'subject_formal_values':formal,'source_citations':citations,
            'definition_prose_block_id':prose['block_id'] if prose else None,'catalog_only':not bool(prose),'label_is_heading':heading,
            'relation':'CO_DEFINED_ALTERNATIVES' if len(names)>1 else 'DECLARATION_OF',
            'block_ids':[b['block_id'] for b in members],'start_order':anchor.get('order'),
            'end_order_exclusive':int(members[-1].get('order') or 0)+1,'end_heading_block_id':None,
            'dom_path':parent,'dom_parent':parent,'scope_status':'COMPLETE','example_start_order':None,
            'source_anchor':document.get('url'),'association':'SOURCE_VERIFIED_FUNCTION_DECLARATION',
            'boundary_evidence':['VISIBLE_FUNCTION_LABEL','SUBJECT_MATCHING_LITERAL_DECLARATIONS',
                                 'LOCAL_DEFINITION_PROSE' if prose else 'TYPED_DECLARATION_PAIR'],
            'scope_execution_plan':dict(plans['HEADING_SECTION' if heading else 'PARAGRAPH_SIGNATURE'])})
    return proofs

def verified_function(proof,block,name):
    if proof.get('definition_pattern')!=PATTERN or name not in proof.get('identity_values',[]):return None
    citations=proof.get('source_citations') or {};bid=block.get('block_id');c=citations.get(bid)
    if not c or block.get('text')!=c.get('text') or block.get('dom_path')!=c.get('dom_path'):return None
    label=citations.get(proof.get('heading_block_id'),{})
    if subjects(label.get('text'),heading=proof.get('label_is_heading'))!=proof['identity_values']:return None
    formal=(proof.get('subject_formal_values') or {}).get(name) or {}
    if not formal:return None
    if not citations.get(proof.get('definition_prose_block_id')):
        context=' / '.join(label.get('heading_ancestry') or [])
        if (not proof.get('catalog_only') or proof.get('label_is_heading')
                or NOISE.search(context) or not re.search(r'\b(?:functions?|routines?)\b',context,re.I)):return None
    if any(not declarations(citations.get(k,{}),proof['identity_values']) for k in formal):return None
    if bid==proof.get('heading_block_id'):
        return {'feature_type':'FUNCTION','identity_values':[name],'formal_value':None}
    if bid not in formal:return None
    return {'feature_type':'FUNCTION','identity_values':[name],'formal_value':formal[bid][0]}
