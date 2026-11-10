"""Evidence-only local applicability; no inventory, fetch or new identities."""
import re

BINDING_PATTERNS=('EXPLICIT_OPERATOR_EXPLANATION',)
def validate_binding_patterns(patterns):
    if patterns is None:return list(BINDING_PATTERNS)
    if not isinstance(patterns,list) or any(p not in BINDING_PATTERNS for p in patterns) or len(set(patterns))!=len(patterns):
        raise ValueError('unsupported/duplicate content binding pattern')
    return patterns

def norm(v):return ' '.join(str(v or '').split())

def operator_explanation_spans(text):
    """Explicit declarative subjects, not occurrences inside examples/prose.

    A paragraph may explain several unrelated operators. Keep source spans
    separate at the next declarative subject; never distribute the whole page.
    """
    text=norm(text);matches=[]
    atom=r'(?:[!<>=~%&|^+*/-]+|[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*)*)'
    label=atom+r'(?:\s+(?:and|or)\s+'+atom+r')*'
    patterns=[r'\b[Tt]he\s+(?:(?:unary|binary)\s+)?('+label+r')\s+operators?\s+(?:is|are|work|works|casts|tests|evaluates|means)\b',
              r'\b[Tt]he\s+(?:(?:unary|binary)\s+)?operator\s+('+label+r')\s+(?:is|are|work|works|casts|tests|evaluates|means)\b',
              r'\bIf the optional\s+([A-Z][A-Z ]*)\s+clause is present\b']
    for pattern in patterns:
        # A declarative sentence subject is evidence; an incidental phrase
        # such as "the left operand of the MATCH operator is ..." is not.
        for m in re.finditer(r'(?:^|(?<=[.!?])\s+)'+pattern,text):
            label=m.group(1).strip(' "`')
            names=[n.strip(' "`') for n in re.split(r'\s*,\s*|\s+and\s+|\s+or\s+',label)]
            if names and all(re.fullmatch(r'[!<>=~%&|^+*/-]+|[A-Z][A-Z_]*(?: [A-Z][A-Z_]*)*',n) for n in names):
                matches.append((m.start(),m.end(),names))
    # Conventional operator vocabulary, only in an explicit declarative head.
    # This is a lexical SQL terminal mapping (as with solidus), not a DB list.
    for m in re.finditer(r'(?:^|(?<=[.!?])\s+)(?:Integer\s+)?(?:divide|division)\s+(?:yields|returns|produces)\b',text,re.I):
        matches.append((m.start(),m.end(),['/']))
    matches=sorted(set((a,b,tuple(ns)) for a,b,ns in matches))
    result=[]
    for i,(start,end,names) in enumerate(matches):
        stop=matches[i+1][0] if i+1<len(matches) else len(text)
        value=text[start:stop].strip()
        value=re.sub(r'\s*Likewise,\s*$','',value)
        if value:result.append({'members':list(names),'text':value})
    return result

def verified_supplement(unit,block,owner,document,reader=None):
    """Revalidate an explicit association, never pretend DOM containment.

    Called by the existing credibility gate for blocks outside the original
    Owner. Arbitrary same-page blocks still fail that gate.
    """
    from agents.semantic_stage.source_structure import SourceStructureIndex
    if block.get('definition_entry_member'):
        from agents.contracts.definition_entry import verified_entry_child
        return verified_entry_child(unit,block,owner,document,reader or SourceStructureIndex())
    proof=block.get('content_applicability') or {}
    name=norm(unit.get('canonical_identity')).upper()
    raw=next((b for b in document.get('blocks',[]) if b['block_id']==block.get('block_id')),None)
    if (str(unit.get('feature_type','')).upper()!='OPERATOR' or not raw
            or proof.get('relation')!='EXPLICIT_OPERATOR_EXPLANATION'
            or proof.get('identity')!=name or proof.get('feature_type')!='OPERATOR'
            or proof.get('owner_id')!=owner.get('owner_id') or proof.get('page_id')!=document.get('page_id')
            or proof.get('source_hash')!=document.get('page_hash') or proof.get('source_url')!=document.get('url')
            or block.get('text')!=raw.get('text')):return False
    observed=(reader or SourceStructureIndex()).observe(document,raw)
    if observed.get('source_structure_status')!='RESOLVED' or observed['observed_context'].get('raw_node_kind')!='P':return False
    if (re.search(r'\b(?:examples?|tutorial|API|JDBC|ODBC)\b',' / '.join(raw.get('heading_ancestry') or []),re.I)
            or observed['observed_context'].get('feature_domain') in {'EMBEDDED_QUERY_LANGUAGE','DEVELOPER_API'}):return False
    expected=[s for s in operator_explanation_spans(raw['text']) if name in s['members']]
    return bool(expected) and proof.get('spans')==expected

def is_section_label(block):
    text=norm(block.get('text'))
    if not text or len(text)>120:return False
    if re.fullmatch(r'(?:Examples?|Samples?|Usage|Syntax|Notes?|Description|Returns?|Arguments?|Parameters?)\s*:?\s*',text,re.I):return False
    return any(norm(n.get('text'))==text and (n.get('element_name') in {'strong','b','em'}
        or 'emphasis' in (n.get('semantic_classes') or [])) for n in block.get('inline_nodes') or [])

def bounded_proof(proof,document):
    """Existing grammar proof may not cross a new emphasis-style section."""
    if proof.get('reference_mode')!='PARAGRAPH_SIGNATURE':return proof
    byid={b['block_id']:b for b in document.get('blocks',[])};kept=[]
    for bid in proof['block_ids']:
        b=byid.get(bid,{})
        if kept and is_section_label(b):break
        kept.append(bid)
    if kept==proof['block_ids']:return proof
    return dict(proof,block_ids=kept,content_scope_origin='EVIDENCE_ONLY_EMPHASIZED_SECTION_BOUNDARY',
        identity_scope_block_ids=list(proof['block_ids']),
        excluded_content_block_ids=[b for b in proof['block_ids'] if b not in kept])

def supplemental_operator_blocks(group,owners,documents,reader,profile):
    if str(group.get('feature_type','')).upper()!='OPERATOR':return {}
    if 'EXPLICIT_OPERATOR_EXPLANATION' not in validate_binding_patterns(((profile or {}).get('hint_contract') or {}).get('content_binding_patterns')):return {}
    name=norm(group.get('canonical_identity')).upper();result={}
    for owner in owners:
        pid=str(owner.get('page_id') or '');d=documents.get(pid,{})
        if not d:continue
        cache=reader.pages.setdefault('_operator_explanations',{})
        if pid not in cache:
            candidates=[]
            for b in d.get('blocks',[]):
                if b.get('block_kind') not in {'TEXT','PARAGRAPH'}:continue
                spans=operator_explanation_spans(b.get('text'))
                if not spans:continue
                obs=reader.observe(d,b)
                ctx=obs.get('observed_context') or {}
                if obs.get('source_structure_status')!='RESOLVED' or ctx.get('raw_node_kind')!='P':continue
                if ctx.get('feature_domain') in {'EMBEDDED_QUERY_LANGUAGE','DEVELOPER_API'}:continue
                if re.search(r'\b(?:examples?|tutorial|API|JDBC|ODBC)\b',' / '.join(b.get('heading_ancestry') or []),re.I):continue
                candidates.append((b,spans))
            cache[pid]=candidates
        for b,spans in cache[pid]:
            own=[s for s in spans if name in s['members']]
            if own:result[b['block_id']]={'page_id':pid,'owner_id':owner['owner_id'],
                'relation':'EXPLICIT_OPERATOR_EXPLANATION','identity':name,'feature_type':'OPERATOR',
                'spans':own,'source_hash':d.get('page_hash'),'source_url':d.get('url')}
    return result

def foreign_type_reason(block,identity,feature_type):
    """An exact typed heading for this spelling overrides incidental use."""
    heading=' / '.join((block.get('observed_context') or {}).get('heading_path') or [])
    for label,kind in [('function','FUNCTION'),('data type','DATATYPE'),('datatype','DATATYPE'),('operator','OPERATOR')]:
        if kind!=feature_type and re.search(r'(?:^| / )'+re.escape(identity)+r'\s+'+label+r'(?: / |$)',heading,re.I):
            return 'EXPLICIT_SAME_NAME_OTHER_FEATURE_TYPE'
    return None
