"""Owner-local declarations: identity does not require a complete SQL grammar.

These readers are used only at a source-proven definition location. They never
scan prose for names, consult an inventory, or grant publication permission.
"""
import re
from agents.contracts.base import stable_id


def signature_term(block):
    """A physical reference signature term, with its return annotation excluded."""
    if block.get('block_kind')!='DEFINITION':return []
    if not any('sig-name' in n.get('semantic_classes',[]) for n in block.get('inline_nodes',[])):return []
    from agents.owner.linked_syntax import frozen_node
    pr=block.get('provenance') or {}
    node=frozen_node({'html_path':pr.get('html_path'),'source_hash':pr.get('page_hash'),
                      'dom_path':block.get('dom_path'),'text':block.get('text')})
    if node is None or node.name!='dt' or 'sig' not in node.get('class',[]):return []
    import copy
    term=copy.deepcopy(node)
    for annotation in term.select('.sig-return, .headerlink'):annotation.decompose()
    return syntax_declarations(term.get_text(' ',strip=True))


def verified_term(proof,block,name):
    """Consume the same C term proof in Identity and Type, not only Owner."""
    execution=proof.get('structure_execution') or {}
    if (execution.get('schema_version')!='StructureExecution/v2' or execution.get('structure')!='C'
            or execution.get('status')!='VERIFIED_STRUCTURE'
            or proof.get('association')!='NATIVE_DEFINITION_TERM_BODY'
            or block.get('block_id') not in proof.get('definition_anchor_block_ids',[])
            or name not in proof.get('identity_values',[])):return None
    from agents.owner import profile_execution as pe
    plan=execution.get('plan') or {}
    try:cfg=pe.compile_execution(plan)
    except (ValueError,TypeError,KeyError):return None
    if not cfg or cfg['digest']!=execution.get('profile_execution_digest') or not pe.applies(plan,block):return None
    pr=block.get('provenance') or {}
    if pr.get('page_hash')!=execution.get('source_hash') or pr.get('source_url')!=execution.get('source_url'):return None
    if not any(b.get('block_id')==block['block_id'] and b.get('locator') in cfg['identity']['alternatives']
               and pe.norm(b.get('value'))==pe.norm(block.get('text')) for b in execution.get('identity_bindings',[])):return None
    for d in signature_term(block):
        if d['identity']==name:
            return {'feature_type':'FUNCTION','identity_values':proof['identity_values'],'formal_value':d['formal_value']}
    return None


def syntax_declarations(value):
    """Whole adjacent formal signatures in an explicitly selected Syntax slot."""
    from agents.contracts.definition_entry import callable_signature
    text=str(value or '').strip();result=[];pos=0
    while pos<len(text):
        while pos<len(text) and text[pos].isspace():pos+=1
        if pos==len(text):break
        match=re.match(r'[A-Za-z_][\w$]*\s*\(',text[pos:])
        if not match:return []
        start=pos;pos+=match.end();depth=1
        while pos<len(text) and depth:
            depth+=(text[pos]=='(')-(text[pos]==')');pos+=1
        if depth:return []
        parsed=callable_signature(text[start:pos])
        if not parsed:return []
        result.append({'identity':parsed[0],'feature_type':'FUNCTION','formal_value':parsed[1]})
    return result


def callable_members(value):
    text=' '.join(str(value or '').split()).strip()
    # Literal calls, examples and explanatory prose cannot establish a head.
    if (not text or re.search(r"[;'`]|\b\d+\b",text)
            or re.match(r'^(?:SELECT|VALUES|WITH|CREATE|ALTER|DROP)\b',text,re.I)):
        return []
    matches=list(re.finditer(r'(?:^|[|{])\s*([A-Za-z_][\w$]*)\s*\(',text))
    result=[]
    for m in matches:
        start=m.start(1);pos=m.end();depth=1
        while pos<len(text) and depth:
            depth += (text[pos]=='(')-(text[pos]==')');pos+=1
        if depth:continue
        # A callable head is enough to bind identity; a complex optional tail
        # remains literal syntax evidence, not a prerequisite for that identity.
        tail=text[pos:]
        if len(matches)==1 and tail and not re.match(r'^\s*(?:\[|OVER\b|FILTER\b|WITHIN\s+GROUP\b|\})',tail,re.I):
            continue
        if len(matches)==1:
            formal=text[start:].strip()
        else:
            formal=text[start:pos]
        result.append({'identity':m.group(1).upper(),'feature_type':'FUNCTION','formal_value':formal})
    return result


def display_qualifier(value,block):
    """Observe a spaced, single hyphenated label, not an argument list.

    Declaration markup takes precedence. This intentionally does not classify
    ordinary identifiers or comma-separated parameters as display qualifiers.
    """
    if block.get('block_kind')!='HEADING':return False
    if any(n.get('element_name')=='code' or set(n.get('semantic_classes',[])) &
           {'sig-name','sig-param','replaceable'} for n in block.get('inline_nodes',[])):return False
    from agents.owner.definition_scope import heading_text
    text=heading_text(value).rstrip('#¶').rstrip()
    return bool(re.fullmatch(r'[A-Za-z_][\w$]*\s+\([A-Za-z]+(?:-[A-Za-z]+)+\)',text))


def heading_member(value,block,kind_hint=None):
    if block.get('block_kind')!='HEADING':return []
    from agents.owner.definition_patterns import _kind
    from agents.owner.definition_scope import normalized_name
    name=normalized_name(value);kinds=_kind(block)
    if kind_hint:
        if kinds and kind_hint not in kinds:return []
        kinds={kind_hint}
    if len(kinds)!=1 or not re.fullmatch(r'[A-Z_][\w$]*(?: [A-Z_][\w$]*){0,5}',name):return []
    kind=next(iter(kinds))
    # Only function headings enter the callable grammar here. Parameterized
    # nominal types retain their code-marked datatype declaration handling.
    # Empty function parentheses still need local definition proof.
    if (kind=='FUNCTION' and not display_qualifier(value,block)
            and re.match(r'^[A-Za-z_][\w$]*\s*\((?!\s*\))',str(value))):return []
    from agents.owner.typed_page import nominal_heading
    if (kind in {'DATATYPE','OPERATOR'} and not nominal_heading(block)
            and (block.get('definition_context') or {}).get('role')!='DATATYPE_DEFINITION_HEADING'):return []
    from agents.owner.reference_definitions import names_from_heading
    labeled=names_from_heading(re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]','',value).strip())
    if labeled and labeled[0]==kind and len(labeled[1])==1:name=labeled[1][0]
    if kind=='DATATYPE':
        from agents.contracts.definition_entry import declaration_names
        literals=list(dict.fromkeys(n.get('text','') for n in block.get('inline_nodes',[]) if n.get('element_name')=='code'))
        visible=value.rstrip().rstrip('#¶').rstrip()
        if literals and re.sub(r'\s+','',visible).casefold()==re.sub(r'\s+','',' or '.join(literals)).casefold():
            parsed=[declaration_names(v) for v in literals]
            if all(len(v)==1 for v in parsed):
                return [{'identity':v[0],'feature_type':'DATATYPE','formal_value':None} for v in parsed]
    if kind=='OPERATOR':
        from agents.owner.typed_page import literal_heading
        from agents.owner.catalog_surfaces import operator_form
        literals=list(dict.fromkeys(n.get('text','') for n in block.get('inline_nodes',[]) if n.get('element_name')=='code'))
        if literals and literal_heading(block):
            names=[operator_form(v,bare=True) for v in literals]
            if all(names):return [{'identity':v,'feature_type':'OPERATOR','formal_value':None} for v in names]
    if kind=='FUNCTION' and ' ' in name:return []
    return [{'identity':name,'feature_type':kind,'formal_value':None}]


def verified_executed(proof,block,name):
    """Re-read the configured B identity slot and its real heading boundary.

The execution annotation is a locator, not authority. Hash, node text, local
scope, physical slot and declaration are all checked against frozen HTML.
"""
    execution=proof.get('structure_execution') or {}
    if execution.get('schema_version')!='StructureExecution/v2' or execution.get('structure') not in {'A','B'}:return None
    if execution.get('status')!='VERIFIED_STRUCTURE' or name not in proof.get('identity_values',[]):return None
    from agents.owner import profile_execution as pe
    try:cfg=pe.compile_execution(execution.get('plan') or {})
    except (ValueError,KeyError,TypeError):return None
    if not cfg or cfg['digest']!=execution.get('profile_execution_digest'):return None
    if not pe.applies(execution.get('plan') or {},block):return None
    bid=block.get('block_id')
    bindings=[b for b in execution.get('identity_bindings',[]) if b.get('block_id')==bid]
    if not bindings or bid not in proof.get('block_ids',[]):return None
    observation=pe.source_observation(block,block,execution.get('source_hash'),execution.get('source_url'))
    if observation.get('source_structure_status')!='RESOLVED':return None
    reader=pe._proof_source_reader
    provenance=block.get('provenance') or {}
    page=reader._page({'page_id':stable_id(
        'proof_source',[provenance.get('html_path'),execution.get('source_hash')]),
        'html_path':provenance.get('html_path'),'page_hash':execution.get('source_hash')})
    nodes=page.get('nodes',{}).get(block.get('dom_path'),[])
    headings=page.get('nodes',{}).get(proof.get('dom_path'),[])
    if len(nodes)!=1 or len(headings)!=1:return None
    node,heading=nodes[0],headings[0]
    if heading.name not in {'h1','h2','h3','h4','h5','h6'}:return None
    if pe.norm(node.get_text(' ',strip=True))!=pe.norm(block.get('text')):return None
    if provenance.get('source_url')!=execution.get('source_url') or provenance.get('page_hash')!=execution.get('source_hash'):return None
    # No later sibling definition may lend its declaration to this heading.
    if node is not heading:
        current=node.find_previous(['h1','h2','h3','h4','h5','h6'])
        while current is not None and current is not heading and int(current.name[1])>int(heading.name[1]):
            current=current.find_previous(['h1','h2','h3','h4','h5','h6'])
        if current is not heading:return None
    # Re-observe the category hierarchy rather than trusting copied ancestry.
    from agents.owner.definition_patterns import _kind
    # DocumentIR ancestry excludes the heading itself. Comparing it with a
    # re-observation that includes the subject manufactures a type conflict.
    is_heading=node.name in {'h1','h2','h3','h4','h5','h6'}
    ancestors=[];current=node.find_previous(['h1','h2','h3','h4','h5','h6'])
    level=int(node.name[1]) if is_heading else 7
    while current is not None:
        if int(current.name[1])<level:
            ancestors.insert(0,current.get_text(' ',strip=True));level=int(current.name[1])
        if level==1:break
        current=current.find_previous(['h1','h2','h3','h4','h5','h6'])
    if _kind(block)!=_kind({'heading_ancestry':ancestors}):return None
    if proof.get('linked_syntax_proof'):
        from agents.owner.linked_syntax import verified
        actual=verified(proof,block,name)
        if actual and any(d.get('identity')==name and d.get('feature_type')==actual['feature_type']
                          for b in bindings for d in b.get('declarations',[])):
            return actual
        return None
    for binding in bindings:
        if pe.norm(binding.get('value'))!=pe.norm(block.get('text')):continue
        locator=binding.get('locator') or {}
        if locator not in cfg['identity']['alternatives']:continue
        # Declared kinds are re-derived; a copied candidate_type is ignored.
        actual=pe.declarations(block.get('text'),block,kind_hint=cfg.get('declaration_kind'))
        if not actual and node.name=='pre':
            previous=node.find_previous(['h1','h2','h3','h4','h5','h6'])
            if previous is not None and pe.norm(previous.get_text(' ',strip=True)) in {'syntax','signature','synopsis','grammar'}:
                actual=syntax_declarations(block.get('text'))
        if not actual and node is heading and normalized_heading(block.get('text'))==name:
            # A bare page heading is proven by its own Syntax declaration,
            # physically re-read inside the same main/section boundary.
            from agents.owner.definition_scope import execution_heading_container
            scope_nodes=page.get('nodes',{}).get(execution_heading_container(block),[])
            scope=scope_nodes[0] if len(scope_nodes)==1 else heading.parent
            for pre in scope.find_all('pre'):
                previous=pre.find_previous(['h1','h2','h3','h4','h5','h6'])
                if previous is None or pe.norm(previous.get_text(' ',strip=True)) not in {'syntax','signature','synopsis','grammar'}:continue
                enclosing=previous
                while enclosing is not None and enclosing is not heading and int(enclosing.name[1])>int(heading.name[1]):
                    enclosing=enclosing.find_previous(['h1','h2','h3','h4','h5','h6'])
                if enclosing is not heading:continue
                value=pre.get_text(' ',strip=True)
                parsed=pe.declarations(value,{'block_kind':'CODE'}) or syntax_declarations(value)
                actual.extend(d for d in parsed if d['identity']==name)
        for d in actual:
            if d['identity']==name and any(x.get('identity')==name and x.get('feature_type')==d['feature_type']
                                          for x in binding.get('declarations',[])):
                return {'feature_type':d['feature_type'],'identity_values':proof['identity_values'],
                        'formal_value':d.get('formal_value')}
    return None


def normalized_heading(text):
    from agents.owner.definition_scope import normalized_name
    return normalized_name(text)
