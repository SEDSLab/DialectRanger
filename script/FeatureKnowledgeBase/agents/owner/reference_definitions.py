"""Profile-selected, source-verifiable reference declarations.

Typed headings, literal grammar declarations and typed catalog cells share one
proof contract. Names come from the local document, never an external inventory.
"""
import re
from agents.owner.function_definitions import norm
from agents.contracts.definition_entry import declaration_names

PATTERN='REFERENCE_DECLARATION_SECTION'
NEGATIVE=re.compile(r'\b(?:examples?|tutorial|connector|mapping|API|JDBC|ODBC|metadata|reserved words)\b',re.I)

# SQL lexical spelling normalization, not a DBMS feature inventory. A terminal
# is emitted ONLY when its named token occurs in a formal production below.
SQL_LEXEMES={'plus sign':'+','minus sign':'-','asterisk':'*','solidus':'/',
    'equals operator':'=','not equals operator':'<>','less than operator':'<',
    'greater than operator':'>','less than or equals operator':'<=','greater than or equals operator':'>='}

def bnf_names(block):
    text=norm(block.get('text'));m=re.fullmatch(r'<([^>]+)>\s*::=\s*(.+)',text)
    if not m:return None
    lhs,rhs=m.groups()
    if not re.search(r'(?:predicate(?: part \d+)?|operator|comp op|boolean (?:value expression|term|factor|test)|numeric value expression|term|factor)$',lhs):return None
    if re.search(r'\b(?:function|case|value specification|parameter)\b',lhs):return None
    if re.search(r'\bnull predicate\b',lhs):values=['IS NULL','IS NOT NULL'] if 'IS [ NOT ] NULL' in rhs else []
    else:
        values=[SQL_LEXEMES[t] for t in re.findall(r'<([^>]+)>',rhs) if t in SQL_LEXEMES]
        terminals=re.sub(r'<[^>]+>',' ',rhs)
        words=re.findall(r'\b[A-Z][A-Z_]*\b',terminals)
        if re.search(r'boolean ',lhs):values+=words
        elif 'predicate' in lhs:
            stems=set(lhs.upper().split())-{'PREDICATE','PART','CHARACTER','OCTET'}
            values += [w for w in words if w in stems]
            # Preserve a required multiword predicate rather than emitting a
            # modifier such as DISTINCT as a standalone feature. Optional
            # words remain explicit source-derived invocation alternatives.
            for chunk in re.split(r'<[^>]+>',rhs):
                if re.match(r'^\s*IS\s',chunk) and any(w in stems for w in re.findall(r'[A-Z]+',chunk)):
                    base=norm(re.sub(r'\[[^]]*\]',' ',chunk))
                    expanded=norm(chunk.replace('[',' ').replace(']',' '))
                    if re.fullmatch(r'[A-Z ]+',base) and re.fullmatch(r'[A-Z ]+',expanded):
                        values=list(dict.fromkeys([base,expanded]))
        elif 'operator' in lhs:
            values += re.findall(r'[!<>=~%&|^+*/-]+',terminals)
        values=[v for v in values if v!='|']
    return ('OPERATOR',list(dict.fromkeys(values))) if values else None

def names_from_heading(text):
    m=re.fullmatch(r'(.+?)\s+(data type|datatype|operator|predicate)',norm(text),re.I)
    if not m:return None
    name=m.group(1)
    if re.fullmatch(r'[A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*){0,5}|[!<>=~%&|^+*/-]+',name):
        return ('DATATYPE' if 'type' in m.group(2).lower() else 'OPERATOR'),[name]

def descriptive_operator_heading(heading,following):
    text=norm(heading.get('text'))
    if not text.lower().endswith(' operator'):return None
    for b in following:
        m=re.match(r'^The\s+'+re.escape(text)+r',\s*([!<>=~%&|^+*/-]+)\s*,',norm(b.get('text')),re.I)
        if m:return 'OPERATOR',[m.group(1)]
    return None

def catalog_names(block):
    meta=block.get('table_metadata') or {};cells=meta.get('cells',[]);headers=meta.get('headers',[])
    if len(cells)<2 or not headers:return None
    # Case-insensitive declarations must not turn the column labels themselves
    # into type names. This is a structural header guard, not a word blacklist.
    if cells==headers or any(p.split(':')[0].split('#')[0]=='thead'
                             for p in str(block.get('dom_path') or '').split(' > ')):
        return None
    from agents.owner.catalog_surfaces import catalog_surfaces
    observed=catalog_surfaces(headers,cells,' / '.join(block.get('heading_ancestry') or []))
    first=[m for m in observed if m['cell_index']==0 and m['feature_type']=='OPERATOR']
    if first and len({m['feature_type'] for m in first})==1:
        return first[0]['feature_type'],list(dict.fromkeys(m['identity'] for m in observed
            if m['feature_type']==first[0]['feature_type']))
    name_catalog = re.fullmatch(r'name',headers[0],re.I)
    if name_catalog:
        # A Name column alone has no type. Require an actual, nearest typed
        # region and explicit Description column, not a Profile suggestion.
        kinds=set()
        headings=block.get('heading_ancestry') or []
        if NEGATIVE.search(' / '.join(headings)) or re.search(r'\b(?:formatting|templates?|patterns?|parameters?|arguments?|mappings?|connectors?|configuration|navigation|results?|output)\b',' / '.join(headings),re.I):
            return None
        for heading in headings:
            local=set()
            for k,pattern in [('DATATYPE',r'\b(?:data[ -]?types?|types|storage classes)\b'),
                              ('FUNCTION',r'\bfunctions?\b'),('OPERATOR',r'\boperators?\b')]:
                if re.search(pattern,str(heading),re.I):local.add(k)
            if local:kinds=local
        if kinds!={'DATATYPE'} or not any(re.fullmatch(r'description',h,re.I) for h in headers):
            return None
        primary=declaration_names(cells[0])
        if not primary:return None
        aliases=[n for i,h in enumerate(headers) if i<len(cells) and re.fullmatch(r'alias(?:es)?',h,re.I)
                 for n in declaration_names(cells[i])]
        return 'DATATYPE',list(dict.fromkeys(primary+aliases))
    if re.fullmatch(r'(?:data\s*)?types?',headers[0],re.I):kind='DATATYPE'
    elif re.fullmatch(r'operators?',headers[0],re.I):kind='OPERATOR'
    elif re.fullmatch(r'expression type',headers[0],re.I) and re.search(r'\b(?:unary|binary)\b',cells[0],re.I):
        values=list(dict.fromkeys(re.findall(r'[!<>=~%&|^+*/-]+',cells[0])))
        return ('OPERATOR',values) if values else None
    else:return None
    if kind=='DATATYPE':
        values=declaration_names(cells[0])
        return (kind,values) if values else None
    text=re.sub(r'\([^)]*\)','',cells[0]).strip()
    if re.search(r'\b(?:expression|expressions|comparison|comparisons)\b',text,re.I):
        # Category row: take terminals only from the formal syntax cell, never examples.
        indexes=[i for i,h in enumerate(headers) if re.fullmatch(r'syntax|grammar',h,re.I)]
        if not indexes:return None
        text=cells[indexes[0]]
        values=re.findall(r'(?<![\w<>=!])[!<>=~%&|^+*/-]+(?![\w<>=!])',text)
        values=[v for v in values if v not in {'|','*'}]
        values+=re.findall(r'\b(?:accepts?|supports?)\s+the\s+([!<>=~%&|^+*/-]+)\s+operator\b',' '.join(cells[1:]),re.I)
    else:values=re.split(r',\s*|\s+or\s+',text)
    values=[norm(v) for v in values]
    atom=r'[A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*){0,5}|[!<>=~%&|^+*/-]+'
    if not values or not all(re.fullmatch(atom,v) for v in values):return None
    return kind,list(dict.fromkeys(values))


def compound_catalog_declaration(block, descendants):
    """Locally declared callable members in a composite definition column.

    Declarations come from whole IR paragraphs, never the first callable found
    in flattened prose. Literal invocation/results and other cells cannot vote.
    """
    headers=(block.get('table_metadata') or {}).get('headers') or []
    from agents.contracts.definition_entry import compound_header_role
    if not headers or not compound_header_role(headers[0],'FUNCTION'):
        return None
    if NEGATIVE.search(' / '.join(block.get('heading_ancestry') or [])):return None
    from agents.contracts.definition_entry import observed_callable_declaration
    signatures=[];body_started=False
    prefix=str(block.get('dom_path') or '')+' > '
    for child in descendants:
        path=str(child.get('dom_path') or '')
        if not path.startswith(prefix):continue
        cell=path[len(prefix):].split(' > ')[0]
        if not re.fullmatch(r't[dh](?:#[^: ]+|:nth-of-type\(1\))?',cell):continue
        nodes=child.get('inline_nodes') or []
        parsed=observed_callable_declaration(norm(child.get('text')),nodes)
        nominal=bool(parsed and any(n.get('element_name')=='code'
            and 'function' in n.get('semantic_classes',[]) and norm(n.get('text')).upper()==parsed[0]
            for n in nodes))
        # An explicit return arrow distinguishes the declaration from a code
        # mention, while literal results are rejected by callable_signature.
        if parsed and (parsed[2] or nominal):
            if body_started and parsed[0] not in {p[0] for _,p in signatures}:return None
            if body_started:continue
            signatures.append((child['block_id'],parsed))
        elif signatures and child.get('block_kind')=='TEXT':body_started=True
    names=sorted({p[0] for _,p in signatures})
    if not names:return None
    return {'names':names,'values':list(dict.fromkeys(p[1] for _,p in signatures)),
            'signature_block_ids':[bid for bid,_ in signatures],
            'subject_formal_values':{name:{bid:[p[1]] for bid,p in signatures if p[0]==name}
                                     for name in names}}

def reference_scopes(document,plans,*,include_semantic_subsections=False):
    from agents.owner.definition_scope import navigation_block
    blocks=sorted(document.get('blocks',[]),key=lambda b:int(b.get('order') or 0));proofs=[]
    for i,b in enumerate(blocks):
        if navigation_block(b):continue
        context=' / '.join(b.get('heading_ancestry') or [])
        if NEGATIVE.search(context) or (b.get('definition_context') or {}).get('domain')=='EMBEDDED_QUERY_LANGUAGE':continue
        parsed=None;members=[];mode=None
        if b.get('block_kind')=='HEADING' and 'HEADING_SECTION' in plans:
            parsed=names_from_heading(b['text'])
            if not parsed:parsed=descriptive_operator_heading(b,blocks[i+1:i+3])
            if parsed:
                level=b.get('heading_level') or 1
                members=[b]
                for c in blocks[i+1:]:
                    if navigation_block(c):break
                    if c.get('block_kind')=='HEADING' and (c.get('heading_level') or 1)<=level:break
                    if c.get('block_kind')=='HEADING' and names_from_heading(c['text']):break
                    # A semantic subsection is content of this proven owner,
                    # not a new feature/example-only page. Peer headings above
                    # still terminate scope; tutorial/API/mapping sections do.
                    if (NEGATIVE.search(c.get('text','')) and c.get('block_kind')=='HEADING'
                            and not (include_semantic_subsections and re.fullmatch(
                                r'\s*(?:examples?|samples?|usage)\s*:?\s*',c.get('text',''),re.I))):break
                    members.append(c)
                if not any(c.get('block_kind') in {'TEXT','CODE','SIGNATURE'} for c in members[1:]):continue
                mode='HEADING_SECTION'
        elif b.get('block_kind')=='TABLE_ROW' and 'CATALOG_ROW' in plans:
            parsed=catalog_names(b);mode='CATALOG_ROW'
            compound=None
            if not parsed:
                descendants=[c for c in blocks[i+1:] if c.get('dom_path','').startswith(b.get('dom_path','')+' > ')]
                compound=compound_catalog_declaration(b,descendants)
                if compound:parsed=('FUNCTION',compound['names']);mode='COMPOUND_CATALOG_ROW'
            if parsed:
                path=b.get('dom_path','');members=[b]+[c for c in blocks[i+1:] if c.get('dom_path','').startswith(path+' > ')]
        elif 'PARAGRAPH_SIGNATURE' in plans:
            parsed=bnf_names(b);mode='PARAGRAPH_SIGNATURE'
            if parsed:
                members=[b]
                for c in blocks[i+1:]:
                    if c.get('block_kind')=='HEADING' or '::=' in c.get('text','') or navigation_block(c):break
                    if any(n.get('element_name') in {'strong','b'} and norm(n.get('text'))==norm(c.get('text')) for n in c.get('inline_nodes',[])):break
                    members.append(c)
        if not parsed:continue
        kind,names=parsed
        proof={'section_id':'reference_definition:'+b['block_id'],'definition_pattern':PATTERN,
            'feature_type':kind,'identity_values':names,'primary_identity':names[0],
            'heading_block_id':b['block_id'],'label_block_id':b['block_id'],
            'definition_anchor_block_ids':[b['block_id']],'identity_anchor_block_ids':{n:[b['block_id']] for n in names},
            'block_ids':[c['block_id'] for c in members],'start_order':b.get('order'),
            'end_order_exclusive':int(members[-1].get('order') or 0)+1,'end_heading_block_id':None,
            'example_start_order':None,'dom_parent':b.get('dom_path','').rsplit(' > ',1)[0],
            'dom_path':b.get('dom_path'),'scope_status':'COMPLETE','source_anchor':document.get('url'),
            'association':'VERIFIED_REFERENCE_DECLARATION','boundary_evidence':['TYPED_LOCAL_DECLARATION',mode],
            'scope_execution_plan':dict(plans['CATALOG_ROW' if mode=='COMPOUND_CATALOG_ROW' else mode]),'reference_mode':mode,
            'source_citations':{c['block_id']:{'block_id':c['block_id'],'order':c.get('order'),
                'text':c['text'],'dom_path':c.get('dom_path'),
                'table_metadata':c.get('table_metadata'),'block_kind':c.get('block_kind'),
                'inline_nodes':c.get('inline_nodes',[]),'heading_ancestry':list(c.get('heading_ancestry') or [])} for c in members}}
        if mode=='CATALOG_ROW' and re.fullmatch(r'name',((b.get('table_metadata') or {}).get('headers') or [''])[0],re.I):
            proof['source_role']='CATALOG_ENTRY'
            proof['boundary_evidence'].append('EXPLICIT_NAME_ALIAS_DATATYPE_SLOT')
            proof['source_citations'][b['block_id']]['heading_ancestry']=list(b.get('heading_ancestry') or [])
        if mode=='COMPOUND_CATALOG_ROW':
            proof['formal_signature_values']={b['block_id']:compound['values']}
            proof['subject_formal_values']=compound['subject_formal_values']
            proof['identity_anchor_block_ids']={n:[b['block_id'],*compound['subject_formal_values'][n]]
                                               for n in names}
            proof['signature_block_ids']=compound['signature_block_ids']
            proof['boundary_evidence'].append('COMPLETE_LOCAL_CALLABLE_DECLARATIONS')
        proofs.append(proof)
    return proofs

def verified_reference(proof,block,name):
    if proof.get('reference_mode') in {'PROFILE_EXECUTED_SLOT','PROFILE_EXECUTED_ENTRY'}:
        from agents.owner.profile_execution import verified_configured
        return verified_configured(proof,block,name)
    if proof.get('definition_pattern')!=PATTERN or block.get('block_id')!=proof.get('heading_block_id'):return None
    c=(proof.get('source_citations') or {}).get(block['block_id']) or {}
    if any(c.get(k)!=block.get(k) for k in ['text','dom_path','table_metadata','block_kind']):return None
    if ('EXPLICIT_NAME_ALIAS_DATATYPE_SLOT' in proof.get('boundary_evidence',[])
            and c.get('heading_ancestry')!=block.get('heading_ancestry')):return None
    mode=proof.get('reference_mode')
    if mode=='HEADING_SECTION':
        # JSON object-key order is not document order. Cached proofs may be
        # serialized with sort_keys=True; revalidate the same first paragraphs
        # using their observed source order, never dictionary insertion order.
        following=sorted((v for v in (proof.get('source_citations') or {}).values()
                          if v.get('order',-1)>c.get('order',-1)),key=lambda v:v.get('order',0))
        parsed=names_from_heading(block['text']) or descriptive_operator_heading(block,following[:2])
    elif mode=='COMPOUND_CATALOG_ROW':
        compound=compound_catalog_declaration(block,sorted([c for bid,c in (proof.get('source_citations') or {}).items() if bid!=block['block_id']],key=lambda c:c.get('order') or 0))
        if (not compound or proof.get('formal_signature_values')!={block['block_id']:compound['values']}
                or proof.get('subject_formal_values')!=compound['subject_formal_values']):return None
        parsed=('FUNCTION',compound['names'])
    else:parsed=bnf_names(block) if mode=='PARAGRAPH_SIGNATURE' else catalog_names(block)
    if not parsed or parsed[0]!=proof.get('feature_type') or parsed[1]!=proof.get('identity_values') or name not in parsed[1]:return None
    return {'feature_type':parsed[0],'identity_values':parsed[1],'formal_value':None}
