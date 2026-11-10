"""B-bounded, E-member declarations; no database names or feature inventories."""
import re
from agents.contracts.base import stable_id

PLAN={'member_kind':'SYMBOLIC_OPERATOR','declaration_form':'GRAMMAR',
      'scope':'HEADING_SECTION','description_sharing':'EXPLICIT_APPLICABILITY'}
SYMBOL=re.compile(r'\|\||[!<>=~%&^+*/-]+')
NEGATIVE=re.compile(r'\b(?:literals?|datetime fields?|comments?|identifiers?|parameters?|'
    r'examples?|navigation|options?|affinit(?:y|ies)|API|SDK|JDBC|ODBC)\b',re.I)


def validate_plans(plans):
    if not isinstance(plans,list) or any(p!=PLAN for p in plans) or len(plans)>1:
        raise ValueError('unsupported or duplicate member collection plan')
    return plans


def symbols(text):
    # Quoted SQL literals and bracket/brace/pipe BNF punctuation are not members.
    masked=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",lambda m:' '*len(m[0]),text)
    return list(dict.fromkeys(m[0] for m in SYMBOL.finditer(masked) if len(m[0])<=4))


def grammar_members(code,context,prose):
    if NEGATIVE.search(context) or not re.search(r'\b(?:sql grammar|expressions?|operators?|predicates?)\b',context,re.I):return []
    if re.match(r'^\s*(?:SELECT|VALUES|CREATE|ALTER|INSERT|UPDATE|DELETE)\b',code,re.I):return []
    names=symbols(code)
    if not names:return []
    catalog=bool(re.fullmatch(r'[\s{}|!<>=~%&^+*/-]+',code)
                 and re.search(r'\boperators?\b',context+' '+prose,re.I))
    infix=bool(re.match(r'^\s*[a-z][\w]*\s+[\[{]',code)
               and re.search(r'[}\]]\s*[a-z][\w]*|\|\|\s+[a-z][\w]*',code))
    return names if catalog or infix else []


def _citation(d,b):
    return dict(b,source_url=d['url'],source_hash=d['page_hash'],page_id=d['page_id'])


def _sentences(text):
    # Keep exact source spans; no paraphrase or fabricated shared description.
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])',text) if s.strip()]


def fields(blocks,members,name,example_order):
    result=[]
    for b in blocks:
        if b.get('block_kind') not in {'TEXT','CODE','SIGNATURE'}:continue
        text=b.get('text','')
        if text.rstrip(':').casefold() in {'example','examples'}:continue
        example=example_order is not None and b.get('order',0)>example_order
        if example:
            if name in symbols(text):result.append({'field':'official_examples','value':text,'block_id':b['block_id']})
            continue
        if b.get('block_kind')!='TEXT':continue
        for sentence in _sentences(text):
            mentioned=symbols(sentence)
            explicit=bool(re.search(r'\boperators?\b',sentence,re.I) and mentioned)
            # Inline code supplies another explicit reference to an operator.
            inline={str(n.get('text','')).strip() for n in b.get('inline_nodes',[]) if n.get('element_name')=='code'}
            specific=bool(explicit or inline.intersection(mentioned))
            if specific and name not in mentioned:continue
            result.append({'field':'description','value':sentence,'block_id':b['block_id']})
    return result


def scopes(document,profile):
    plans=(profile or {}).get('hint_contract',{}).get('member_collection_patterns',[])
    validate_plans(plans)
    if not plans:return []
    from agents.owner.structure_execution import enabled_structures
    from agents.owner.scope_plan import compile_scope_plan
    if not {'B','E'}<=enabled_structures(profile):return []
    scope_plan=next((p for p in compile_scope_plan(profile) if p['relation']=='GRAMMAR_MEMBER_CONTEXT'),None)
    if not scope_plan:return []
    blocks=sorted(document.get('blocks',[]),key=lambda b:b.get('order',0));output=[]
    for i,h in enumerate(blocks):
        if h.get('block_kind')!='HEADING':continue
        end=next((j for j in range(i+1,len(blocks)) if blocks[j].get('block_kind')=='HEADING'
                  and blocks[j].get('heading_level',6)<=h.get('heading_level',6)),len(blocks))
        body=blocks[i+1:end]
        example=next((b.get('order') for b in body if b.get('text','').strip().rstrip(':').casefold() in {'example','examples'}),None)
        code=next((b for b in body if b.get('block_kind')=='CODE' and (example is None or b.get('order',0)<example)),None)
        if not code:continue
        context=' / '.join(h.get('heading_ancestry',[]));prose=' '.join(b['text'] for b in body if b.get('block_kind')=='TEXT' and (example is None or b.get('order',0)<example))
        names=grammar_members(code['text'],context,prose)
        if not names:continue
        citations=[_citation(document,b) for b in body if b.get('block_kind') in {'CODE','SIGNATURE','TEXT'}]
        for name in names:
            bids=[b['block_id'] for b in citations];claims=fields(citations,names,name,example)
            output.append({'section_id':stable_id('grammar_member',[document['page_id'],code['block_id'],name]),
                'definition_pattern':'SOURCE_RELATION_DEFINITION','source_role':'GRAMMAR_MEMBER',
                'member_collection':{'plan':dict(PLAN),'members':names,'name':name,'parent_heading':_citation(document,h),
                                     'citations':citations,'example_start_order':example,'claims':claims},
                'feature_type':'OPERATOR','identity_values':[name],'primary_identity':name,
                'heading_block_id':code['block_id'],'identity_anchor_block_ids':{name:[code['block_id']]},
                'definition_anchor_block_ids':[code['block_id']],'block_ids':bids,'dom_path':code.get('dom_path'),
                'dom_parent':str(code.get('dom_path','')).rsplit(' > ',1)[0],
                'start_order':code.get('order'),'end_order_exclusive':blocks[end].get('order') if end<len(blocks) else None,
                'end_heading_block_id':blocks[end]['block_id'] if end<len(blocks) else None,
                'scope_status':'COMPLETE','example_start_order':example,'source_anchor':document['url'],
                'boundary_evidence':['HEADING_BOUNDED_MEMBER_COLLECTION','PHYSICAL_GRAMMAR_MEMBER'],
                'scope_execution_plan':dict(scope_plan),'anchor_citation':_citation(document,code)})
    return output


def _actual(citation):
    from agents.owner import profile_execution as pe
    p=citation.get('provenance') or {}
    observed=pe.source_observation(citation,citation,citation.get('source_hash'),citation.get('source_url'))
    if observed.get('source_structure_status')!='RESOLVED':return None
    page=pe._proof_source_reader._page({'page_id':stable_id('proof_source',[p.get('html_path'),citation.get('source_hash')]),
        'html_path':p.get('html_path'),'page_hash':citation.get('source_hash')})
    nodes=page.get('nodes',{}).get(citation.get('dom_path'),[])
    if len(nodes)!=1 or pe.norm(nodes[0].get_text(' ',strip=True))!=pe.norm(citation.get('text')):return None
    return nodes[0]


def verified(proof,block,name):
    c=proof.get('member_collection') or {};anchor=proof.get('anchor_citation') or {};h=c.get('parent_heading') or {}
    if (c.get('plan')!=PLAN or name!=c.get('name') or proof.get('identity_values')!=[name]
            or block.get('block_id')!=anchor.get('block_id') or block.get('text')!=anchor.get('text')
            or block.get('dom_path')!=anchor.get('dom_path') or proof.get('heading_block_id')!=anchor.get('block_id')):return None
    provenance=block.get('provenance') or {}
    if ((block.get('source_url') or provenance.get('source_url'))!=anchor.get('source_url')
        or (block.get('source_hash') or provenance.get('page_hash'))!=anchor.get('source_hash')):return None
    node=_actual(anchor);head=_actual(h)
    if node is None or head is None:return None
    nearest=node.find_previous(['h1','h2','h3','h4','h5','h6'])
    if nearest is not head:return None
    # The physical declaration must be the first PRE in this definition, not
    # a later application/example with similar symbols.
    first=None
    for element in head.next_elements:
        tag=getattr(element,'name',None)
        if tag in {'h1','h2','h3','h4','h5','h6'} and int(tag[1])<=int(head.name[1]):break
        if tag=='p' and element.get_text(' ',strip=True).rstrip(':').casefold() in {'example','examples'}:break
        if tag=='pre':first=element;break
    if first is not node:return None
    # Read classification from the real hierarchy, not copied heading hints.
    ancestry=[];cur=head;level=7
    while cur is not None:
        if int(cur.name[1])<level:ancestry.insert(0,cur.get_text(' ',strip=True));level=int(cur.name[1])
        if level==1:break
        cur=cur.find_previous(['h1','h2','h3','h4','h5','h6'])
    cited=c.get('citations',[])
    for b in cited:
        if b.get('source_hash')!=anchor.get('source_hash') or b.get('source_url')!=anchor.get('source_url'):return None
        n=_actual(b)
        if n is None:return None
        prev=n.find_previous(['h1','h2','h3','h4','h5','h6'])
        if prev is not head:return None
    prose=' '.join(b['text'] for b in cited if b.get('block_kind')=='TEXT' and
                   (c.get('example_start_order') is None or b.get('order',0)<c['example_start_order']))
    members=grammar_members(anchor['text'],' / '.join(ancestry),prose)
    if members!=c.get('members') or name not in members:return None
    if c.get('claims')!=fields(cited,members,name,c.get('example_start_order')):return None
    return {'feature_type':'OPERATOR','identity_values':[name],'formal_value':None}


def consume(payload,evidence,identity,emit,decision):
    controlled=set()
    for unit in payload.get('definition_units',[]):
        proof=unit.get('source_proof') or next((p for ref in unit.get('member_evidence_refs',[])
            for p in evidence.get(ref,{}).get('definition_section_proofs',[])
            if p.get('member_collection') and p.get('identity_values')==[identity]),{})
        c=proof.get('member_collection')
        if not c or unit.get('binding_status')!='COMPLETE' or unit.get('identity_values')!=[identity]:continue
        anchor=evidence.get(proof.get('heading_block_id'),{})
        if not verified(proof,anchor,identity):continue
        for bid in proof['block_ids']:
            if bid in evidence:controlled.add(bid)
        for claim in c['claims']:
            b=evidence.get(claim['block_id'])
            if b and claim['value'] in b.get('text',''):
                emit(claim['field'],b,claim['value']);decision(b,claim['field'].upper(),'VERIFIED_MEMBER_APPLICABILITY')
    return controlled
