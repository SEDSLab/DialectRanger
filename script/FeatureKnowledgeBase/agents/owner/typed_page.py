"""Explicit page-subject metadata, re-read from frozen source, not URL hints."""
import re


def datatype_metadata(block):
    if block.get('block_kind')!='HEADING':return None
    from agents.contracts.definition_entry import declaration_names
    value=block.get('text','').strip()
    from agents.owner.definition_patterns import _alternatives
    title=re.sub(r'\s+(?:data\s+)?types$','',value,flags=re.I)
    parts=[piece for group in _alternatives(title) for piece in re.split(r'\s+\|\s+',group)]
    parsed=[declaration_names(piece.strip()) for piece in parts]
    if not parsed or not all(len(n)==1 for n in parsed):return None
    names=list(dict.fromkeys(n[0] for n in parsed))
    name=names[0]
    from agents.owner.linked_syntax import frozen_node
    provenance=block.get('provenance') or {}
    citation={'html_path':provenance.get('html_path'),'source_hash':provenance.get('page_hash'),
              'dom_path':block.get('dom_path'),'text':block.get('text')}
    node=frozen_node(citation)
    if node is None or not re.fullmatch(r'h[1-6]',node.name):return None
    container=node.find_parent(['header','article','main'])
    if container is None:return None
    from agents.document_structure_v2.semantic_regions import semantic_region_role
    from agents.document_structure_v2.topology import _dom_path
    seen=0
    for following in node.find_all_next():
        if following.name in {'h1','h2','h3','h4','h5','h6'}:break
        if not any(parent is container for parent in following.parents):break
        if following.name!='p' and semantic_region_role(following)[0]!='PARAGRAPH':continue
        seen+=1
        if seen>8:break
        text=' '.join(following.get_text(' ',strip=True).split())
        metadata=(node.name=='h1' and node.find_previous('h1') is None and re.match(
            r'^(?:Documentation|Reference)\s+(?:for|of)\s+(?:the\s+)?'+re.escape(name)+r'\s+data\s+types?\b',text,re.I))
        # A multi-name page title is authoritative only with the page's own
        # explicit type metadata. No names are collected from examples/body.
        listed_metadata=(len(names)>1 and node.name=='h1' and node.find_previous('h1') is None
            and re.match(r'^(?:Documentation|Reference)\s+(?:for|of)\b',text,re.I)
            and re.search(r'\bdata\s+types\b',text,re.I)
            and all(re.search(r'\b'+re.escape(n)+r'\b',text,re.I) for n in names))
        if len(names)>1 and metadata:
            metadata=all(n==name or (n.startswith(name) and any(
                ns[0]==n and ('(' in part or '[' in part) for ns,part in zip(parsed,parts))) for n in names)
        self_definition=re.match(r'^(?:The\s+)?'+re.escape(name)+r'\s+(?:data\s+type\s+is|is\s+(?:an?\s+)?'
            r'(?:system-defined|built-in|native|SQL|data)\s+(?:data\s+)?type)\b',text,re.I)
        if metadata or listed_metadata or (len(names)==1 and self_definition):
            return {'feature_type':'DATATYPE','identity':name,'heading_citation':citation,
                    'metadata_locator':_dom_path(following),'metadata_text':text,
                    'formal_value':value if len(names)==1 and ('(' in value or '[' in value) else None,
                    'declarations':[{'identity':n,'feature_type':'DATATYPE',
                        'formal_value':next((part.strip() for part,ns in zip(parts,parsed)
                                            if ns[0]==n and ('(' in part or '[' in part)),None)} for n in names]}
    return None


def literal_heading(block):
    """A code-formatted complete subject, not a code word inside a prose title."""
    from agents.owner.definition_patterns import _literal
    if _literal(block) is not None:return True
    if block.get('block_kind')!='HEADING':return False
    # Heading permalinks are presentation, not part of a declaration. Require
    # every remaining character to belong to literal names or list separators;
    # a code word embedded in an English heading still cannot pass.
    clean=lambda s:' '.join(re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]','',str(s or '')).split())
    text=clean(block.get('text'))
    codes=list(dict.fromkeys(clean(n.get('text')) for n in block.get('inline_nodes',[])
                             if n.get('element_name')=='code' and clean(n.get('text'))))
    if not 1<=len(codes)<=8:return False
    names=r'\s*(?:,\s*(?:(?:or|and)\s+)?|(?:or|and)\s+|\|\s*)'
    pattern=names.join(re.escape(c) for c in codes)
    return bool(re.fullmatch(pattern+r'(?:\s+[#¶])?',text,re.I))


def nominal_heading(block):
    text=' '.join(re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]','',str(block.get('text') or '')).split()).strip().rstrip('#¶').strip()
    return (literal_heading(block)
            or bool(re.fullmatch(r'[^\w\s]+',text))
            or bool(re.fullmatch(r'(?:The\s+)?[A-Za-z_]\w*\s*\([^)]*\)\s+(?:SQL\s+)?function',text,re.I))
            or bool(re.fullmatch(r'(?:The\s+)?[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,4}\s+(?:[Oo]perator|[Pp]redicate)',text))
            or bool(text and text==text.upper() and re.fullmatch(r'[A-Z_][A-Z0-9_ ]*',text)))


def operator_metadata(block):
    """A heading's own explicit operator declaration, never usage examples."""
    if block.get('block_kind')!='HEADING':return None
    from agents.owner.catalog_surfaces import operator_form
    clean=lambda s:' '.join(re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]','',str(s or '')).split()).rstrip('#¶').strip()
    title=clean(block.get('text'))
    title=re.sub(r'^[A-Za-z -]*(?:operator|predicate|comparison|test)s?\s*:\s*','',title,flags=re.I)
    headings=re.split(r'\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+',title)
    names=[operator_form(v,bare=True) for v in headings]
    if not names or not all(names):return None
    from agents.owner.linked_syntax import frozen_node
    pr=block.get('provenance') or {}
    node=frozen_node({'html_path':pr.get('html_path'),'source_hash':pr.get('page_hash'),
                      'dom_path':block.get('dom_path'),'text':block.get('text')})
    if node is None or not re.fullmatch(r'h[1-6]',node.name):return None
    from agents.document_structure_v2.definition_observations import paragraph
    for following in node.find_all_next():
        if re.fullmatch(r'h[1-6]',following.name or ''):break
        if not paragraph(following):continue
        text=clean(following.get_text(' ',strip=True))
        declaration=re.match(r'^The\s+(.+?)\s+(?:operators?|predicates?|quantifiers?)\b',text)
        if not declaration:return None
        prefix=declaration[1];declared=[];offset=0
        for code in following.find_all('code'):
            value=clean(code.get_text(' ',strip=True))
            position=text.find(value,offset)
            if position<0:continue
            offset=position+len(value)
            if position<declaration.start(1) or offset>declaration.end(1):continue
            alternatives=[value]
            if re.search(r'\[\s*NOT\s*\]',value):
                alternatives=[re.sub(r'\s*\[\s*NOT\s*\]\s*',' ',value),value.replace('[','').replace(']','')]
            declared.extend(operator_form(v,bare=True) for v in alternatives)
        declared=list(dict.fromkeys(n for n in declared if n))
        if set(names)<=set(declared):
            return {'declarations':[{'identity':n,'feature_type':'OPERATOR','formal_value':None} for n in declared],
                    'metadata_text':text}
        return None
    return None
