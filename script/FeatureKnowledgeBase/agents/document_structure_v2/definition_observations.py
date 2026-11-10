"""Literal, local definition observations shared by IR and frozen-source readers.

No DBMS vocabulary, inventories, network or inferred semantic content.
"""
import html
import re
from bs4 import Tag, NavigableString
from agents.document_structure_v2.semantic_regions import semantic_region_role


def paragraph(node):
    return node.name=='p' or semantic_region_role(node)[0]=='PARAGRAPH'


def _text(node):
    return ' '.join(node.get_text(' ',strip=True).split())


def _headings(node):
    root=node
    while root.parent is not None:root=root.parent
    index=root.__dict__.get('_feature_heading_index')
    if index is None:
        index={};active={}
        for current in root.descendants:
            if not isinstance(current,Tag):continue
            if re.fullmatch(r'h[1-6]',current.name or ''):
                level=int(current.name[1]);active={k:v for k,v in active.items() if k<level};active[level]=_text(current)
            index[id(current)]=tuple(active.values())
        root.__dict__['_feature_heading_index']=index
    return list(index.get(id(node),()))


def feature_domain(node):
    context=' / '.join(_headings(node))
    if any(re.search(r'\b(?:full[- ]text\b.*\bquer(?:y|ies)|enhanced query|jsonpath|xpath)\b',h,re.I) for h in _headings(node)):
        return 'EMBEDDED_QUERY_LANGUAGE'
    # Native SQL functions can contain a bounded tutorial of a different
    # language. An adjacent introduction scopes only its list/table, not the
    # entire page or the native SQL functions elsewhere on that page.
    container=node if node.name in {'ul','ol','table'} else node.find_parent(['ul','ol','table'])
    if container is not None:
        wrapper=container.parent
        if isinstance(wrapper,Tag) and wrapper.name=='div' and _text(wrapper)==_text(container):
            container=wrapper
        intro=container.find_previous_sibling()
        if (isinstance(intro,Tag) and intro.name=='p'
                and re.search(r'\b(?:XPath|JSONPath|XQuery)\b',_text(intro),re.I)
                and re.search(r'\b(?:expressions?|syntax|operators?)\b',_text(intro),re.I)):
            return 'EMBEDDED_QUERY_LANGUAGE'
    if re.search(r'\b(?:API reference|programming interface|C/C\+\+)\b',context,re.I):
        return 'DEVELOPER_API'
    return 'DOCUMENT_FEATURE'


def _visible(node):
    if isinstance(node,NavigableString):return str(node)
    if not isinstance(node,Tag) or node.name=='sup':return ''
    if node.name=='br':return '\n'
    if node.name=='small':return '@'
    return ''.join(_visible(c) for c in node.children)


def observe_definition(node):
    text=_text(node)
    domain=feature_domain(node)
    # C pointer return types are not callable identities. Check native terms
    # and their children, not a whole page that merely mentions a C API.
    term=node if node.name=='dt' else node.find_parent('dt')
    if term is not None and re.search(r'\(\s*\*\s*[A-Za-z_]',_text(term)):
        return {'role':'DEVELOPER_API_DECLARATION','domain':'DEVELOPER_API','text':_text(term)}
    if node.name in {'h1','h2','h3','h4','h5','h6'}:
        value=re.sub(r'^\d+(?:\.\d+)*[.)]?\s+','',text)
        match=re.fullmatch(r'(?:The\s+)?([A-Za-z_]\w*)\s+(?:datatype|data type|type declaration)',value,re.I)
        if match:
            return {'role':'DATATYPE_DEFINITION_HEADING','identity':match[1].upper(),'domain':domain}
    if paragraph(node):
        # An explicit notation-to-callable mapping declares the operator in
        # the first literal slot, not the referenced implementation function.
        if domain=='DOCUMENT_FEATURE' and re.search(r'\boperators?\b',' / '.join(_headings(node)),re.I):
            codes=node.find_all('code')
            if len(codes)>=2:
                from agents.owner.catalog_surfaces import operator_form
                expression=_text(codes[0]);op=operator_form(expression,bare=False)
                unary=re.fullmatch(r'([!~+-])\s*[a-z][A-Za-z_0-9]*',expression)
                if not op and unary:op=unary[1]
                if op and (re.fullmatch(r'[A-Za-z_]\w*\s*\([^;]*\)',_text(codes[1]))
                           or re.search(r'\b(?:the same as|equivalent to)\b',text,re.I)):
                    return {'role':'OPERATOR_CATALOG','domain':domain,'members':[
                        {'identity':op,'cell_index':None,'raw_entry':expression}],
                        'declaration_origin':'OBSERVED_NOTATION_IMPLEMENTATION_MAPPING'}
            if codes and re.match(r'^Syntax\s*:?\s*SELECT\b',text,re.I):
                from agents.owner.catalog_surfaces import operator_form
                expression=re.sub(r'^SELECT\s+','',_text(codes[0]),flags=re.I)
                op=operator_form(expression,bare=False)
                if op:return {'role':'OPERATOR_CATALOG','domain':domain,'members':[
                    {'identity':op,'cell_index':None,'raw_entry':_text(codes[0])}],
                    'declaration_origin':'EXPLICIT_LABELED_OPERATOR_TEMPLATE'}
            if codes and re.fullmatch(r'(?:[A-Za-z_]\w*\s+)?supports?\s+(?:the\s+)?.+?\s+operators[.!]?',text,re.I):
                names=[_text(c) for c in codes]
                if all(re.fullmatch(r'[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,4}|[!<>=~%&|^+*/:@?-]+',n) for n in names):
                    return {'role':'OPERATOR_CATALOG','domain':domain,'members':[
                        {'identity':n,'cell_index':None,'raw_entry':text} for n in dict.fromkeys(names)],
                        'declaration_origin':'EXPLICIT_SUPPORTED_OPERATOR_ENUMERATION'}
        declaration=re.match(r'^The\s+([A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,3}|[!<>=~%&|^+*/:@?-]+)\s+operator\b',text)
        enumeration=re.match(r'^The following\s+(?:(?:SQL|logical|arithmetic|comparison)\s+)?operators\s+are\s+(?:available|supported)\s*:\s*([^.;]+)',text,re.I)
        names=[]
        if declaration:names=[declaration[1]]
        elif enumeration:
            literal=enumeration[1]
            values=[_text(c) for c in node.find_all('code')
                    if re.search(r'(?<![\w])'+re.escape(_text(c))+r'(?![\w])',literal)]
            if values and all(re.fullmatch(r'[A-Z][A-Z_]*(?:\s+[A-Z][A-Z_]*){0,3}|[!<>=~%&|^+*/:@?-]+',v) for v in values):names=values
        if names and domain=='DOCUMENT_FEATURE':
            return {'role':'OPERATOR_CATALOG','domain':domain,'members':[
                {'identity':name,'cell_index':None,'raw_entry':text} for name in dict.fromkeys(names)],
                'declaration_origin':'EXPLICIT_OPERATOR_SELF_DEFINITION' if declaration else 'EXPLICIT_OPERATOR_ENUMERATION'}
        match=re.match(r'^([A-Za-z_]\w*)\s*\.\s+(.+)',text)
        first=node.find(['b','strong','code'])
        if (match and first is not None and _text(first).rstrip('.').upper()==match[1].upper()
            and re.search(r'\bstorage classes?\b',' / '.join(_headings(node)),re.I)):
            return {'role':'STORAGE_CLASS_DEFINITION','identity':match[1].upper(),'domain':domain,'definition_text':text}
    if node.name!='tr':return {}
    table=node.find_parent('table')
    if table is None:return {}
    headers=[_visible(h).strip() for h in table.find_all('th')]
    cells=node.find_all(['td','th'],recursive=False)
    from agents.owner.catalog_surfaces import catalog_surfaces
    members=catalog_surfaces(headers,[_text(c) for c in cells],' / '.join(_headings(node)))
    members=[m for m in members if m['feature_type']=='OPERATOR']
    if members and all(c.name=='td' for c in cells):
        return {'role':'DECLARATION_CATALOG','domain':domain,'headers':headers,'members':members}
    if (cells and cells[0].name=='td' and re.fullmatch(r'[A-Za-z_]\w*',_text(cells[0]))
        and headers and re.search(r'\b(?:affinity|type[- ]name|declared type)\b',headers[0],re.I)):
        return {'role':'DATATYPE_TARGET_ROW','identity':_text(cells[0]).upper(),'domain':domain,'headers':headers}
    if not headers or not re.fullmatch(r'\s*(?:SQL\s+)?operators?\s*',headers[0],re.I):return {}
    members=[]
    for ci,cell in enumerate(cells):
        if cell.name!='td' or ci!=0:continue
        raw=html.unescape(html.unescape(_visible(cell)))
        raw=re.sub(r'\[[a-z][^\]]*\]|\([a-z][a-z -]*\)',' @ ',raw)
        for ordinal,entry in enumerate(re.split(r'(?:[^\S\r\n]{2,}|[\r\n]+)',raw)):
            value=' '.join(entry.split()).strip()
            if not value:continue
            value=re.sub(r'^(?:@\s*)+','',value)
            head=value.split('@',1)[0].strip()
            # Only literal complete uppercase words / symbol entries. No
            # ordinary prose, operands, examples or dictionary completion.
            if not re.fullmatch(r'(?:[A-Z][A-Z0-9_]*(?:\s+[A-Z][A-Z0-9_]*){0,4}|[!<>=~%&|^+*/-]+)',head):continue
            item={'identity':head,'cell_index':ci,'item_index':ordinal,'raw_entry':entry.strip()}
            if not any(x['identity']==head for x in members):members.append(item)
    return {'role':'OPERATOR_CATALOG','domain':domain,'headers':headers,'members':members} if members else {}
