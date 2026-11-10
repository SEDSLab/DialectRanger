"""Finite Profile-selected definition grammars, verified against original IR.

Profiles select a grammar/slot/boundary, never names or publication decisions.
The same parser is reused when Type and Publication verify a structural proof.
"""
import re
from agents.owner.surface_patterns import formal_signatures

GRAMMARS=('DATATYPE_DECLARATION','OPERATOR_EXPRESSION','CALLABLE_DEFINITION')
CONTAINERS=('LIST_ITEM','REPEATED_CONTAINER')
PATTERN='PROFILE_LOCAL_DEFINITION'


def default_patterns():
    return [{'grammar':g,'container':'LIST_ITEM','identity_slot':'LEADING_LITERAL',
             'boundary':'CURRENT_CONTAINER'} for g in GRAMMARS[:2]]


def validate_patterns(patterns):
    if not isinstance(patterns,list):raise ValueError('definition_patterns must be a list')
    seen=set()
    for p in patterns:
        if not isinstance(p,dict) or set(p)!={'grammar','container','identity_slot','boundary'}:
            raise ValueError('definition pattern must use the finite structural contract')
        if (p['grammar'] not in GRAMMARS or p['container'] not in CONTAINERS
                or p['identity_slot']!='LEADING_LITERAL' or p['boundary']!='CURRENT_CONTAINER'):
            raise ValueError('unsupported definition grammar, slot or boundary')
        key=tuple(p[k] for k in sorted(p))
        if key in seen:raise ValueError('duplicate definition pattern')
        seen.add(key)
    return patterns


def pattern_plan(profile=None):
    if profile is None:return default_patterns()
    hints=profile.get('hint_contract') or {}
    if 'definition_patterns' in hints:return validate_patterns(hints['definition_patterns'])
    # Backward-compatible compilation of retained profiles, without mutating
    # their JSON or pretending that SUPPORTING_CONTEXT is a definitive veto.
    return default_patterns() if any(s.get('owner_strategy')=='list_item' for s in profile.get('surfaces',[])) else []


def _norm(t):return ' '.join(str(t or '').split())


def _kind(block):
    for h in reversed(block.get('heading_ancestry') or []):
        kinds=set()
        if re.search(r'\b(?:data[ -]?types?|types|type syntax|type declarations?|storage classes?)\b',h,re.I):kinds.add('DATATYPE')
        if re.search(r'\b(?:functions?|routines?)\b',h,re.I):kinds.add('FUNCTION')
        if re.search(r'\b(?:operators?|predicates?)\b',h,re.I):kinds.add('OPERATOR')
        if kinds:return kinds
        if re.search(r'\b(?:comparison|ordering|serialization|representation|conversion|internal|value categories)\b|'
                     r'\b(?:regular expression|pattern|path|template) (?:syntax|grammar)\b',h,re.I):return set()
    return set()


def _literal(block):
    text=_norm(block.get('text'))
    nodes=[n for n in block.get('inline_nodes',[]) if n.get('element_name')=='code']
    outer=[n for n in nodes if not any(n is not other and str(n.get('dom_path','')).startswith(str(other.get('dom_path',''))+' > ') for other in nodes)]
    if any(_norm(n.get('text'))==text for n in outer):return text
    if outer and re.sub(r'\s+','',','.join(_norm(n.get('text')) for n in outer))==re.sub(r'\s+','',text):return text
    if block.get('block_kind') in {'CODE','SIGNATURE'}:return text
    return None


def _balanced(text):
    stack=[];quote=None
    for ch in text:
        if quote:
            if ch==quote:quote=None
        elif ch in "'\"":quote=ch
        elif ch in '([':stack.append(ch)
        elif ch in ')]':
            if not stack or stack.pop()!= {')':'(',']':'['}[ch]:return False
    return not stack and not quote


def _alternatives(text):
    depth=0;quote=None;start=0;parts=[]
    for i,ch in enumerate(text):
        if quote:
            if ch==quote:quote=None
        elif ch in "'\"":quote=ch
        elif ch in '([':depth+=1
        elif ch in ')]':depth-=1
        elif ch==',' and depth==0:
            parts.append(text[start:i].strip());start=i+1
    parts.append(text[start:].strip())
    return parts


def parse_surface(block,grammar):
    text=_literal(block);context=' / '.join(block.get('heading_ancestry') or [])
    if not text or re.search(r'\b(?:examples?|tutorial|mapping|compatibility|connector|API|SDK|JDBC|ODBC|programming|parameters?|results?)\b',context,re.I):return None
    if re.match(r'^(?:SELECT|VALUES|CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|WITH)\b',text):return None
    kinds=_kind(block)
    if grammar=='CALLABLE_DEFINITION':
        signatures=formal_signatures(text)
        names=list(dict.fromkeys(n for n,_ in signatures))
        if 'FUNCTION' not in kinds or len(names)!=1:return None
        return {'feature_type':'FUNCTION','identity_values':names,'formal_value':text}
    if grammar=='DATATYPE_DECLARATION':
        if kinds!={'DATATYPE'} or not _balanced(text):return None
        labels=[p.strip() for p in text.split(',')]
        if len(labels)>1 and all(re.fullmatch(r'[A-Z][A-Z0-9_]*(?:\s+[A-Z][A-Z0-9_]*){0,2}',p) for p in labels):
            return {'feature_type':'DATATYPE','identity_values':labels,'formal_value':None}
        # Split only outside optional/parameter groups. Each declaration gets
        # its OWN literal syntax; co-location does not establish an alias.
        parts=_alternatives(text)
        if len(parts)>1:
            values=[]
            for part in parts:
                local=dict(block,text=part,inline_nodes=[{'element_name':'code','text':part,'dom_path':'literal'}])
                parsed=parse_surface(local,grammar)
                if not parsed:return None
                values.append(parsed)
            names=list(dict.fromkeys(n for v in values for n in v['identity_values']))
            return {'feature_type':'DATATYPE','identity_values':names,'formal_value':None,
                    'formal_values_by_identity':{n:v['formal_value'] for v in values for n in v['identity_values']}}
        match=re.fullmatch(r'(?:\[[A-Z][A-Z_ ]*\]\s*)?([A-Z][A-Z0-9_]*(?:\s+[A-Z][A-Z0-9_]*){0,2})(.*)',text)
        if not match:return None
        name=match.group(1);suffix=match.group(2).strip()
        if suffix and (suffix[0] not in '([' or re.search(r'[;=<>+*/]',suffix)):return None
        return {'feature_type':'DATATYPE','identity_values':[name],'formal_value':text}
    if 'OPERATOR' not in kinds:return None
    # A true call signature is not automatically an operator in a mixed chapter.
    if formal_signatures(text):return None
    # Bracketed type parameters / embedded character-class grammars are not
    # SQL operator expressions merely because an ancestor mentions operators.
    if text.startswith('[') or re.match(r'^[A-Z][A-Z0-9_]*\s*\[',text):return None
    parts=_alternatives(text)
    atomic=lambda p:bool(re.fullmatch(r'[!<>=~%&|^+*/-]+|[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,3}',p))
    if all(atomic(p) for p in parts):
        if kinds!={'OPERATOR'} and any(re.search(r'[A-Z]',p) for p in parts):return None
        return {'feature_type':'OPERATOR','identity_values':parts,'formal_value':None}
    if len(parts)>1:
        parsed=[]
        for part in parts:
            local=dict(block,text=part,inline_nodes=[{'element_name':'code','text':part,'dom_path':'literal'}])
            value=parse_surface(local,grammar)
            if not value:return None
            parsed.append(value)
        names=list(dict.fromkeys(n for v in parsed for n in v['identity_values']))
        return {'feature_type':'OPERATOR','identity_values':names,'formal_value':None,
                'formal_values_by_identity':{n:v['formal_value'] for v in parsed for n in v['identity_values']}}
    if not _balanced(text) or not re.search(r'\b[a-z][a-z0-9_]*\b',text):return None
    if re.search(r'[^\w\s.,()\[\]!<>=~%&|^+*/?-]',text):return None
    # Metavariables separate keyword chunks. Keep an entire composite identity,
    # rather than extracting AND/NOT from inside another operator expression.
    tokens=re.findall(r'[A-Za-z_][A-Za-z0-9_]*|[!<>=~%&|^+*/-]+',text)
    chunks=[];between=False
    for token in tokens:
        if token.isupper() or re.fullmatch(r'[!<>=~%&|^+*/-]+',token):
            if between and chunks:chunks.append('...')
            chunks.append(token);between=False
        elif chunks:between=True
    if not chunks:return None
    name=' '.join(chunks)
    if '...' in chunks:name+=' ...'
    return {'feature_type':'OPERATOR','identity_values':[name],'formal_value':text}


def verified_pattern(proof,block,name):
    execution=proof.get('structure_execution') or {}
    if execution.get('structure')=='C' and proof.get('association')=='NATIVE_DEFINITION_TERM_BODY':
        from agents.owner.declaration_binding import verified_term
        return verified_term(proof,block,name)
    if execution.get('schema_version')=='StructureExecution/v2' and execution.get('structure') in {'A','B'}:
        from agents.owner.declaration_binding import verified_executed
        return verified_executed(proof,block,name)
    if (proof.get('definition_pattern')=='DATATYPE_SELF_DEFINITION'
            and proof.get('feature_type')=='DATATYPE' and proof.get('heading_block_id')==block.get('block_id')
            and proof.get('identity_values')==[name]):
        from agents.owner.surface_patterns import datatype_definition_name
        if datatype_definition_name(block.get('text'))==name:
            return {'feature_type':'DATATYPE','identity_values':[name],'formal_value':None,'content_role':'DESCRIPTION'}
    if proof.get('definition_pattern') == 'COMPOSITE_FIELD_DEFINITION':
        from agents.owner.composite_definition import verified
        return verified(proof, block, name)
    if proof.get('definition_pattern')=='REFERENCE_DECLARATION_SECTION':
        from agents.owner.reference_definitions import verified_reference
        return verified_reference(proof,block,name)
    if proof.get('definition_pattern')=='FUNCTION_DECLARATION_SECTION':
        from agents.owner.function_definitions import verified_function
        return verified_function(proof,block,name)
    if proof.get('definition_pattern')=='SOURCE_RELATION_DEFINITION':
        from agents.owner.source_relations import verified_relation
        return verified_relation(proof,block,name)
    if proof.get('definition_pattern')!=PATTERN or block.get('block_id')!=proof.get('heading_block_id'):return None
    grammar=proof.get('pattern_grammar')
    if grammar not in GRAMMARS:return None
    try:validate_patterns([proof.get('pattern_contract')])
    except (ValueError,TypeError):return None
    if proof['pattern_contract']['grammar']!=grammar:return None
    parsed=parse_surface(block,grammar)
    if grammar=='CALLABLE_DEFINITION' and _kind(block)!={'FUNCTION'}:return None
    if not parsed or parsed['feature_type']!=proof.get('feature_type') or parsed['identity_values']!=proof.get('identity_values'):return None
    if name not in parsed['identity_values']:return None
    path=str(block.get('dom_path') or '');container=proof.get('dom_parent') or ''
    if not container or not path.startswith(container+' > '):return None
    if block.get('block_id') not in proof.get('block_ids',[]):return None
    if 'formal_values_by_identity' in parsed:
        parsed=dict(parsed,formal_value=parsed['formal_values_by_identity'].get(name))
    return parsed


def container_groups(document, container_kind):
    """Shared containment primitive, not a site selector or feature parser.

    REPEATED_CONTAINER requires observed siblings in the original DOM paths.
    Only the nearest repeated enclosing unit is used, never an entire page.
    """
    blocks=document.get('blocks',[]);paths={}
    for b in blocks:
        parts=str(b.get('dom_path') or '').split(' > ')
        for i,part in enumerate(parts[:-1]):
            tag=re.split(r'[.#:]',part)[0]
            if tag in {'li','div','section','article'}:
                paths.setdefault((' > '.join(parts[:i]),tag),set()).add(part)
    groups={}
    for b in blocks:
        if b.get('block_kind')=='LIST_ITEM':continue
        parts=str(b.get('dom_path') or '').split(' > ')
        positions=[]
        for i,v in enumerate(parts[:-1]):
            tag=re.split(r'[.#:]',v)[0]
            if container_kind=='LIST_ITEM' and tag=='li':positions.append(i)
            elif container_kind=='REPEATED_CONTAINER' and len(paths.get((' > '.join(parts[:i]),tag),set()))>=2:
                positions.append(i)
        if positions:groups.setdefault(' > '.join(parts[:positions[-1]+1]),[]).append(b)
    return groups


def observed_container_patterns(documents):
    """Compile observed declaration layouts, including callable list entries."""
    found=set()
    for document in documents:
        for path,members in container_groups(document,'REPEATED_CONTAINER').items():
            is_list=bool(re.match(r'^li(?:[.#:]|$)',path.split(' > ')[-1]))
            members=sorted(members,key=lambda b:b.get('order') or 0)
            if len(members)<2:continue
            for grammar in GRAMMARS:
                if parse_surface(members[0],grammar) and (not is_list or grammar=='CALLABLE_DEFINITION'):
                    found.add((grammar,'LIST_ITEM' if is_list else 'REPEATED_CONTAINER'))
    return [{'grammar':g,'container':container,'identity_slot':'LEADING_LITERAL',
             'boundary':'CURRENT_CONTAINER'} for g,container in sorted(found)]


def execute_patterns(document,profile=None):
    plan=pattern_plan(profile);proofs=[];seen=set()
    groups_by_kind={kind:container_groups(document,kind) for kind in {p['container'] for p in plan}}
    for p in plan:
      for container,members in groups_by_kind[p['container']].items():
        members=sorted(members,key=lambda b:b.get('order') or 0);first=members[0]
        if first.get('block_kind') not in {'TEXT','CODE','SIGNATURE'}:continue
        if re.search(r'(?:^| > )(?:nav|aside|footer|header|blockquote|details)(?:[.#:]| > |$)',container):continue
        from agents.owner.composite_definition import field_members
        if not any(b.get('block_kind')=='TEXT' and re.search(r'[a-z]{3}',b.get('text','')) for b in members[1:]) and not field_members(members,{}):continue
        parsed=parse_surface(first,p['grammar'])
        # The declaration recipe establishes type, unlike a contextual hint.
        # Do not change the shared hint parser's historical mixed-page behavior.
        if p['grammar']=='CALLABLE_DEFINITION' and _kind(first)!={'FUNCTION'}:continue
        if not parsed:continue
        bid=first['block_id'];names=parsed['identity_values'];formal=parsed['formal_value']
        key=(bid,parsed['feature_type'],tuple(names))
        if key in seen:continue
        seen.add(key)
        from agents.profile_bootstrap.local_structure import owner_local_hint
        local_hint=owner_local_hint(document,members,profile,{})
        proofs.append({'section_id':'profile_definition:'+bid,'definition_pattern':PATTERN,
                'profile_local_type_patterns':[r for r in local_hint.get('local_type_patterns',[])
                                               if r['grammar']==p['grammar']],
                'pattern_grammar':p['grammar'],'pattern_contract':dict(p),'feature_type':parsed['feature_type'],
                'heading_block_id':bid,'identity_values':names,'primary_identity':names[0],
                'identity_anchor_block_ids':{n:[bid] for n in names},'definition_anchor_block_ids':[bid],
                'formal_signature_values':{bid:[formal]} if formal else {},
                'start_order':first.get('order'),'end_order_exclusive':int(members[-1].get('order') or 0)+1,
                'end_heading_block_id':None,'source_anchor':document.get('url'),'dom_path':container,'dom_parent':container,
                'block_ids':[b['block_id'] for b in members],'scope_status':'COMPLETE','example_start_order':None,
                'association':'PROFILE_SELECTED_SOURCE_VERIFIED_DEFINITION','association_block_id':bid,
                'boundary_evidence':['FINITE_PATTERN_PLAN','LEADING_LITERAL','LOCAL_TYPED_SECTION',
                                     'NATIVE_LI_CONTAINMENT' if p['container']=='LIST_ITEM' else 'OBSERVED_REPEATED_CONTAINER']})
    return proofs
