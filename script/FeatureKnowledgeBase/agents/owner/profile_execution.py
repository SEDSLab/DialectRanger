"""Finite executable identity/field locations, shared by all five adapters.

No DBMS vocabulary, selectors, inventory lookup, or publication permission.
Matching text stays immutable; fields are re-read through the frozen source
reader before becoming semantic claims.
"""
import copy
import hashlib
import json
import re
from agents.contracts.base import stable_id
from agents.contracts.identity_key import normalize_identity

PATTERN_MODE='PROFILE_EXECUTED_SLOT'
ENTRY_MODE='PROFILE_EXECUTED_ENTRY'
ROLES={'DESCRIPTION','FORMAL_SYNTAX','EXAMPLE','EXAMPLE_RESULT','CONSTRUCTION_EXAMPLE',
       'DECLARATION_EXAMPLE','PARAMETER','RETURN_TYPE','NOTE','CONSTRAINT'}
IDENTITY_KINDS={'COLUMN_HEADER','CELL_INDEX','FIRST_DATA_CELL','FIRST_PARAGRAPH_CODE',
                'LEADING_CODE','FIRST_BLOCK_TEXT','VERIFIED_ANCHOR','HEADING'}
FIELD_KINDS={'COLUMN_HEADER','CELL_INDEX','SECTION_LABEL','BODY_PROSE','DECLARATION_ANCHORS',
             'OWNER_SQL_APPLICATION','DEFINITION_EXPRESSION'}
NEGATIVE=re.compile(r'\b(?:API|SDK|JDBC|ODBC|programming|tutorial|configuration|connector|'
    r'mappings?|navigation|example queries|query results|output columns)\b|C/C\+\+',re.I)


def norm(value):return ' '.join(str(value or '').split()).strip().rstrip(':').casefold()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf8')).hexdigest()


def source_observation(citation,block,source_hash=None,source_url=None):
    from agents.semantic_stage.source_structure import SourceStructureIndex
    provenance=citation.get('provenance') or {}
    path=provenance.get('html_path');content_hash=source_hash or provenance.get('page_hash')
    global _proof_source_reader
    if '_proof_source_reader' not in globals():_proof_source_reader=SourceStructureIndex()
    return _proof_source_reader.observe({'html_path':path,'page_hash':content_hash,
        'url':source_url or provenance.get('source_url'),
        'page_id':stable_id('proof_source',[path,content_hash])},block)


def identity_authorized(owner,block_id,name=None):
    """One authority check for primary and bounded recovery consumers."""
    if block_id not in owner.get('profile_controlled_block_ids',[]):return True
    return any((p.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2'
        and block_id in p.get('definition_anchor_block_ids',[])
        and (name is None or normalize_identity(name) in {normalize_identity(n) for n in p.get('identity_values',[])})
        for p in owner.get('definition_section_proofs',[]))


def validate_locator(value,*,identity=False,structure=None):
    if not isinstance(value,dict) or set(value)-({'kind','headers','index','labels'} | ({'heading_level','text_pattern','capture_group','strip_suffix','split_pattern','name_pattern','token_pattern'} if identity else set())):
        raise ValueError('unsupported execution locator fields')
    options={'heading_level','text_pattern','capture_group','strip_suffix','split_pattern','name_pattern','token_pattern'}
    if identity:
        for key in ('text_pattern','split_pattern','name_pattern','token_pattern'):
            if key in value:
                if not isinstance(value[key],str) or not value[key] or len(value[key])>1000:raise ValueError('invalid identity pattern')
                try:re.compile(value[key])
                except re.error as exc:raise ValueError('invalid identity pattern') from exc
        if 'capture_group' in value:
            group=value['capture_group']
            if not isinstance(group,(int,str)) or isinstance(group,bool) or 'text_pattern' not in value:raise ValueError('invalid capture group')
            pattern=re.compile(value['text_pattern'])
            if (isinstance(group,int) and not 0<=group<=pattern.groups or isinstance(group,str) and group not in pattern.groupindex):raise ValueError('invalid capture group')
        if 'strip_suffix' in value and (not isinstance(value['strip_suffix'],str) or not value['strip_suffix']):raise ValueError('invalid suffix')
        if 'heading_level' in value and (value.get('kind')!='HEADING' or type(value['heading_level']) is not int or not 1<=value['heading_level']<=6):raise ValueError('invalid heading level')
        if value.get('kind')=='FIRST_BLOCK_TEXT' and not any(k in value for k in ['text_pattern','token_pattern']):raise ValueError('text identity slot requires an extraction pattern')
        value={k:v for k,v in value.items() if k not in options}
    kind=value.get('kind')
    if kind not in (IDENTITY_KINDS if identity else FIELD_KINDS):raise ValueError('unsupported execution locator')
    if kind in {'COLUMN_HEADER','SECTION_LABEL'}:
        key='headers' if kind=='COLUMN_HEADER' else 'labels';names=value.get(key)
        if (set(value)!={'kind',key} or not isinstance(names,list) or not names
                or any(not isinstance(n,str) or not n.strip() or len(n)>120 for n in names)
                or len({norm(n) for n in names})!=len(names)):
            raise ValueError('locator labels must be distinct nonempty literals')
    elif kind=='CELL_INDEX':
        if set(value)!={'kind','index'} or type(value.get('index')) is not int or not 0<=value['index']<100:
            raise ValueError('cell index must be a bounded nonnegative integer')
    elif set(value)!={'kind'}:raise ValueError('unexpected locator option')
    if identity and structure!='D' and kind in {'COLUMN_HEADER','CELL_INDEX','FIRST_DATA_CELL'}:
        raise ValueError('cell identity locator requires structure D')
    if identity and structure=='D' and kind in {'FIRST_PARAGRAPH_CODE','LEADING_CODE','FIRST_BLOCK_TEXT','HEADING'}:
        raise ValueError('entry identity locator cannot locate a row')


def validate_literals(values):
    if not isinstance(values,list) or not values or len(values)>64 or any(not isinstance(v,str) or not v.strip() or len(v)>240 for v in values):
        raise ValueError('bounded nonempty literals required')


def explicit_declarations(value,block,locator,cfg):
    """Profile authority reads only the selected literal slot; no SQL parser."""
    if locator.get('heading_level') is not None and block.get('heading_level')!=locator['heading_level']:return []
    text=' '.join(str(value or '').split()).strip()
    if 'text_pattern' in locator:
        match=re.fullmatch(locator['text_pattern'],text)
        if not match:return []
        text=match.group(locator.get('capture_group',0)) or ''
    if 'strip_suffix' in locator:
        if not text.endswith(locator['strip_suffix']):return []
        text=text[:-len(locator['strip_suffix'])].strip()
    names=([m.group(0)for m in re.finditer(locator['token_pattern'],text)] if 'token_pattern' in locator
           else re.split(locator['split_pattern'],text) if 'split_pattern' in locator else [text])
    if len(names)>128:return []
    names=[n.strip() for n in names]
    if not names or any(not n or len(n)>500 or locator.get('name_pattern') and not re.fullmatch(locator['name_pattern'],n) for n in names):return []
    return [{'identity':normalize_identity(n),'feature_type':cfg['declaration_kind'],'formal_value':None} for n in dict.fromkeys(names)]


def compile_execution(plan):
    value=plan.get('execution')
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'version','identity','fields','when','anchor_roles','declaration_kind','authority','boundary'} or value.get('version')!=1:
        raise ValueError('unsupported Profile execution contract')
    identity=value.get('identity')
    if not isinstance(identity,dict) or set(identity)!={'alternatives','cardinality'}:
        raise ValueError('execution identity requires alternatives and cardinality')
    alternatives=identity['alternatives']
    if not isinstance(alternatives,list) or not 1<=len(alternatives)<=8:raise ValueError('bounded identity alternatives required')
    for locator in alternatives:validate_locator(locator,identity=True,structure=plan['structure'])
    if identity['cardinality'] not in {'ONE','ONE_OR_MORE'}:raise ValueError('unsupported identity cardinality')
    fields=value.get('fields',[])
    if not isinstance(fields,list) or len(fields)>32:raise ValueError('invalid field bindings')
    occupied={}
    for binding in fields:
        if (not isinstance(binding,dict) or set(binding)-{'role','locator','cardinality','sharing','block_kinds','exclude_text_prefixes'}
                or binding.get('role') not in ROLES):raise ValueError('unsupported field binding')
        validate_locator(binding.get('locator'))
        for key in ('block_kinds','exclude_text_prefixes'):
            if key in binding:validate_literals(binding[key])
        if 'block_kinds' in binding and any(k not in {'CODE','HEADING','TEXT','DEFINITION','TABLE_ROW','LIST_ITEM','SIGNATURE'} for k in binding['block_kinds']):raise ValueError('invalid block kinds')
        if binding.get('cardinality','ONE_OR_MORE') not in {'ONE','ONE_OR_MORE'}:raise ValueError('invalid field cardinality')
        if binding.get('sharing','SINGLE_SUBJECT') not in {'SINGLE_SUBJECT','DECLARED_MEMBERS','SHARED_CONTEXT'}:
            raise ValueError('unsupported field applicability')
        key=json.dumps(binding['locator'],sort_keys=True)
        if key in occupied:raise ValueError('one physical field locator cannot claim multiple roles')
        occupied[key]=binding['role']
    when=value.get('when',{})
    if not isinstance(when,dict) or set(when)-{'table_headers_any','table_headers_all','heading_context_any','page_path_prefixes'}:
        raise ValueError('unsupported execution applicability')
    for key,names in when.items():
        validate_locator({'kind':'COLUMN_HEADER','headers':names})
        if key=='page_path_prefixes' and any(not p.startswith('/') or '?' in p or '#' in p or '..' in p.split('/') for p in names):
            raise ValueError('page scope must be a literal absolute path prefix')
    explicit=value.get('authority')=='EXPLICIT_DEFINITION'
    if 'authority' in value and not explicit:raise ValueError('unsupported execution authority')
    if explicit:
        if plan['structure']=='E':raise ValueError('EXPLICIT_DEFINITION_UNSUPPORTED_STRUCTURE_E')
        if value.get('declaration_kind') not in {'FUNCTION','OPERATOR','DATATYPE','UNCERTAIN'}:raise ValueError('explicit declaration kind required')
        allowed={'D':{'COLUMN_HEADER','CELL_INDEX','FIRST_DATA_CELL'},'C':{'FIRST_PARAGRAPH_CODE','LEADING_CODE','FIRST_BLOCK_TEXT'},'A':{'HEADING'},'B':{'HEADING'}}
        if any(l['kind'] not in allowed[plan['structure']] for l in alternatives):raise ValueError('unsupported authority slot')
        if not when.get('page_path_prefixes'):raise ValueError('explicit authority requires page scope')
        if plan['structure'] in {'A','B'} and any(l.get('heading_level')!=(1 if plan['structure']=='A' else l.get('heading_level')) or not l.get('heading_level') for l in alternatives):raise ValueError('explicit heading level required')
    boundary=value.get('boundary',{})
    if not isinstance(boundary,dict) or set(boundary)-{'stop_headings','exclude_text_prefixes'}:raise ValueError('invalid execution boundary')
    for labels in boundary.values():validate_literals(labels)
    if value.get('declaration_kind') is not None and not explicit:
        if (value['declaration_kind'] not in {'OPERATOR','DATATYPE'} or plan['structure'] not in {'A','B'}
                or not (when.get('page_path_prefixes') or when.get('heading_context_any'))):
            raise ValueError('declaration grammar requires a scoped heading plan')
    anchors=value.get('anchor_roles',{})
    if not isinstance(anchors,dict) or set(anchors)-{'FUNCTION','OPERATOR','DATATYPE'}:raise ValueError('unsupported anchor role')
    used=set()
    for role,prefixes in anchors.items():
        if not isinstance(prefixes,list) or not prefixes:raise ValueError('anchor role prefixes required')
        for prefix in prefixes:
            if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,60}[_-]',prefix) or prefix in used:
                raise ValueError('anchor roles require distinct structural prefixes')
            used.add(prefix)
    result=copy.deepcopy(value);result['digest']=digest(value)
    return result


def applies(plan,block,document=None):
    from urllib.parse import urlsplit
    when=(plan.get('execution') or {}).get('when') or {}
    locators=(plan.get('execution') or {}).get('identity',{}).get('alternatives',[])
    if block.get('block_kind')=='HEADING' and block.get('heading_level') is not None and locators and all(l.get('kind')=='HEADING' and l.get('heading_level') is not None for l in locators):
        if block['heading_level'] not in {l['heading_level']for l in locators}:return False
    headers={norm(h) for h in (block.get('table_metadata') or {}).get('headers',[])}
    if when.get('table_headers_any') and not headers & {norm(h) for h in when['table_headers_any']}:return False
    if when.get('table_headers_all') and not {norm(h) for h in when['table_headers_all']}<=headers:return False
    if when.get('heading_context_any') and not {norm(h) for h in block.get('heading_ancestry',[])} & {norm(h) for h in when['heading_context_any']}:return False
    if when.get('page_path_prefixes'):
        url=(document or {}).get('url') or (block.get('provenance') or {}).get('source_url','')
        path=urlsplit(url).path.rstrip('/') or '/'
        if not any(path==p.rstrip('/') or path.startswith(p.rstrip('/')+'/') for p in when['page_path_prefixes']):return False
    return True


def _cells(block,source=False):
    table=block.get('table_context') or {}
    if table.get('cells'):
        return [{'index':c['cell_index'],'text':c.get('text',''),'locator':c.get('dom_locator'),
            'headers':[h['text'] for h in c.get('headers',[])],
            'valid':c.get('header_status') in {'EXPLICIT','OBSERVED_GRID','HEADER_CELL'}
                and not c.get('nested_table') and c.get('colspan',1)==1} for c in table['cells']]
    if source:return []
    metadata=block.get('table_metadata') or {};values=metadata.get('literal_cells') or metadata.get('cells') or []
    headers=metadata.get('headers') or []
    return [{'index':i,'text':text,'locator':None,'headers':headers[i:i+1],'valid':True} for i,text in enumerate(values)]


def locate_cells(block,locator,source=False):
    cells=_cells(block,source);kind=locator['kind']
    if kind=='COLUMN_HEADER':
        return [c for c in cells if c['valid'] and {norm(h) for h in c['headers']} & {norm(h) for h in locator['headers']}]
    if kind=='CELL_INDEX':return [c for c in cells if c['index']==locator['index'] and c['valid']]
    if kind=='FIRST_DATA_CELL':return [cells[0]] if cells and cells[0]['valid'] else []
    return []


def declarations(value,block,header='',kind_hint=None):
    """Parse only a selected declaration surface, never a body mention."""
    from agents.owner.declaration_binding import signature_term
    from agents.owner.definition_patterns import _kind,_balanced
    from agents.owner.catalog_surfaces import operator_form
    from agents.contracts.definition_entry import observed_callable_declaration
    text=' '.join(str(value or '').split()).strip()
    context=' / '.join(block.get('heading_ancestry') or [])
    statement=re.match(r'^(?:SELECT|VALUES|CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|WITH|CALL)\b',text,re.I)
    callable_head=re.match(r'^[A-Za-z_][\w$]*\s*\(',text)
    if (not text or NEGATIVE.search(context) or statement and not callable_head
            or re.search(r'\bAuthors?\s*:|\bPublisher\s*:',text,re.I)):
        return []
    term=signature_term(block)
    if term and norm(value)==norm(block.get('text')):return term
    kinds=_kind(block);typed=bool(re.fullmatch(r'(?:sql\s+)?(?:data\s*type|type)(?:\s+name)?|SQL Name',header,re.I))
    if kind_hint:
        if block.get('block_kind')!='HEADING' or (kinds and kind_hint not in kinds):return []
        kinds={kind_hint}
    if 'OPERATOR' in kinds:
        from agents.owner.typed_page import operator_metadata
        operator=operator_metadata(block)
        if operator:return operator['declarations']
    from agents.owner.typed_page import datatype_metadata
    page_type=datatype_metadata(block)
    if page_type:return page_type.get('declarations') or [{'identity':page_type['identity'],'feature_type':'DATATYPE','formal_value':page_type['formal_value']}]
    from agents.owner.typed_page import nominal_heading
    if (block.get('block_kind')=='HEADING' and kinds & {'DATATYPE','OPERATOR'}
            and not nominal_heading(block)
            and (block.get('definition_context') or {}).get('role')!='DATATYPE_DEFINITION_HEADING'):
        return []
    function_column=bool(re.fullmatch(r'(?:(?:statistic|aggregate|window)\s+)?(?:functions?|routines?|signature)',header,re.I))
    headers={norm(h) for h in (block.get('table_metadata') or {}).get('headers',[])}
    usage_table=bool(headers & {'parameter','parameters','argument','arguments','column','column name','field','field name'})
    if usage_table:return []
    if typed and kinds!={'DATATYPE'}:
        if kinds:return []  # Competing local definition context stays unresolved.
        # An explicit declaration column plus a definition field is observable
        # local evidence; an English category keyword is not mandatory.
        if not headers & {'description','purpose'} or NEGATIVE.search(context):return []
    from agents.owner.declaration_binding import heading_member,callable_members,display_qualifier
    heading=heading_member(text,block,kind_hint=kind_hint)
    if heading:return heading
    # A matching local Syntax slot can still establish a display heading.
    if display_qualifier(text,block):return []
    if kinds=={'DATATYPE'} and not function_column or typed:
        parts=re.split(r'\s*,\s*',text) if re.fullmatch(r'[A-Z][A-Z_0-9 ]*(?:\s*,\s*[A-Z][A-Z_0-9 ]*)+',text) else [text]
        result=[]
        for part in parts:
            from agents.contracts.definition_entry import declaration_names
            names=declaration_names(part)
            if len(names)!=1 or not _balanced(part) or re.search(r'[;=\'"{}]',part):return []
            result.append({'identity':names[0],'feature_type':'DATATYPE',
                'formal_value':part if '(' in part or '[' in part else None})
        return result
    inline=block.get('inline_nodes',[])
    parsed=observed_callable_declaration(text,inline,
        formal_marker=function_column or any('replaceable' in n.get('semantic_classes',[]) for n in inline))
    if parsed:
        if kinds=={'OPERATOR'} and not function_column:return []
        if re.fullmatch(r'[A-Za-z_][\w$]*\(\s*\)',text) and kinds!={'FUNCTION'} and not function_column:return []
        return [{'identity':parsed[0],'feature_type':'FUNCTION','formal_value':parsed[1]}]
    if kinds=={'FUNCTION'} and block.get('block_kind') in {'CODE','SIGNATURE'}:
        members=callable_members(text)
        if members:return members
    explicit_op=bool(re.fullmatch(r'(?:sql\s+)?operators?|predicates?',header,re.I)) or kinds=={'OPERATOR'}
    parts=re.split(r'\s+(?:or|and)\s+|\s*,\s*',text,flags=re.I)
    whole=operator_form(text,bare=explicit_op)
    ops=[whole] if whole else [operator_form(p,bare=explicit_op) for p in parts]
    if not ops or not all(ops):return []
    if any(re.search(r'[A-Za-z]',op) for op in ops) and not explicit_op and 'OPERATOR' not in kinds:return []
    return [{'identity':n,'feature_type':'OPERATOR','formal_value':text if whole and text!=n else None} for n in dict.fromkeys(ops)]


def identity_bindings(proof,document,plan):
    cfg=plan['execution'];blocks={b['block_id']:b for b in document.get('blocks',[])}
    anchor=blocks.get(proof.get('heading_block_id'),{})
    if not anchor.get('heading_ancestry') and document.get('title'):
        anchor=dict(anchor,heading_ancestry=[document['title']])
    partial=[]
    located=False
    for alternative,locator in enumerate(cfg['identity']['alternatives']):
        kind=locator['kind'];found=[]
        if kind in {'COLUMN_HEADER','CELL_INDEX','FIRST_DATA_CELL'}:
            cells=locate_cells(anchor,locator)
            located=located or bool(cells)
            for cell in cells:
                parsed=explicit_declarations(cell['text'],anchor,locator,cfg) if cfg.get('authority')=='EXPLICIT_DEFINITION' else declarations(cell['text'],anchor,' '.join(cell['headers']))
                if not parsed and cfg.get('authority')!='EXPLICIT_DEFINITION' and proof.get('catalog_membership_proof'):
                    # A source-only catalog association cannot create Identity
                    # or Type. Re-read the actual selected cell and its existing
                    # membership contract, rather than bypassing the locator.
                    from agents.contracts.catalog_membership import catalog_membership
                    actual=source_observation(anchor,anchor)
                    provenance=anchor.get('provenance') or {}
                    source={**anchor,**actual,'source_url':provenance.get('source_url'),
                            'source_hash':provenance.get('page_hash')}
                    for name in proof.get('identity_values',[]):
                        if any(w['feature_type']==proof.get('feature_type') and w['surface']==cell['text']
                               and w['dom_locator']==next((c['dom_locator'] for c in (actual.get('table_context') or {}).get('cells',[])
                                   if c['cell_index']==cell['index']),None)
                               for w in catalog_membership(source,name)):
                            parsed.append({'identity':name,'feature_type':proof['feature_type'],'formal_value':None})
                if parsed:found.append({'block_id':anchor['block_id'],'cell_index':cell['index'],
                    'value':cell['text'],'declarations':parsed,'locator':locator})
        elif kind in {'FIRST_PARAGRAPH_CODE','LEADING_CODE','FIRST_BLOCK_TEXT'}:
            members=sorted((blocks[b] for b in proof.get('block_ids',[]) if b in blocks),key=lambda b:b.get('order',0))
            candidates=[b for b in members if b.get('block_kind') in ({'TEXT','DEFINITION','CODE','LIST_ITEM'} if kind=='FIRST_BLOCK_TEXT' else {'TEXT','DEFINITION','CODE'})]
            first=candidates[0] if candidates else {}
            codes=[n for n in first.get('inline_nodes',[]) if n.get('element_name')=='code']
            if kind=='FIRST_BLOCK_TEXT' and cfg.get('authority')=='EXPLICIT_DEFINITION' and first:
                codes=[{'text':first.get('text',''),'dom_path':first.get('dom_path')}]
            if first.get('block_kind')=='CODE':codes=[{'text':first['text'],'dom_path':first.get('dom_path')}]
            located=located or bool(codes)
            for node in codes:
                parsed=explicit_declarations(node['text'],first,locator,cfg) if cfg.get('authority')=='EXPLICIT_DEFINITION' else declarations(node['text'],first)
                if not parsed and cfg.get('authority')!='EXPLICIT_DEFINITION' and proof.get('linked_syntax_proof') and first.get('block_id')==proof.get('heading_block_id'):
                    from agents.owner.linked_syntax import verified
                    actual=verified(proof,first,(proof.get('identity_values') or [''])[0])
                    if actual:parsed=[{'identity':actual['identity_values'][0],'feature_type':actual['feature_type'],'formal_value':actual.get('formal_value')}]
                if parsed:found.append({'block_id':first['block_id'],'node_locator':node.get('dom_path'),
                    'value':node['text'],'declarations':parsed,'locator':locator})
        elif kind=='HEADING' and anchor.get('block_kind')=='HEADING':
            located=True
            parsed=explicit_declarations(anchor.get('text'),anchor,locator,cfg) if cfg.get('authority')=='EXPLICIT_DEFINITION' else declarations(anchor.get('text'),anchor,kind_hint=cfg.get('declaration_kind'))
            if cfg.get('authority')=='EXPLICIT_DEFINITION':
                if parsed:found=[{'block_id':anchor['block_id'],'value':anchor['text'],'declarations':parsed,'locator':locator,'alternative_index':alternative}]
                if found:
                    if cfg['identity']['cardinality']=='ONE' and len(parsed)!=1:return [],'AMBIGUOUS_IDENTITY_LOCATION'
                    return found,None
                continue
            if not parsed and proof.get('linked_syntax_proof'):
                from agents.owner.linked_syntax import verified
                actual=verified(proof,anchor,normalize_identity(anchor.get('text')))
                if actual:parsed=[{'identity':actual['identity_values'][0],'feature_type':actual['feature_type'],'formal_value':None}]
            if not parsed:
                # A bare heading needs a matching local formal declaration;
                # the heading alone is never sufficient authority.
                from agents.owner.definition_scope import normalized_name
                name=normalized_name(anchor.get('text'))
                for bid in proof.get('block_ids',[]):
                    candidate=blocks.get(bid,{})
                    if candidate.get('block_kind')!='CODE':continue
                    if not any(norm(h) in {'syntax','signature','synopsis','grammar'} for h in candidate.get('heading_ancestry',[])):continue
                    from agents.owner.declaration_binding import syntax_declarations
                    parsed=[d for d in (declarations(candidate.get('text'),candidate) or syntax_declarations(candidate.get('text'))) if d['identity']==name]
                    if parsed:break
            if parsed:found=[{'block_id':anchor['block_id'],'value':anchor['text'],'declarations':parsed,'locator':locator}]
        elif kind=='VERIFIED_ANCHOR':
            if proof.get('reference_mode') in {PATTERN_MODE,ENTRY_MODE}:
                continue  # A configured slot cannot prove itself recursively.
            from agents.owner.definition_patterns import verified_pattern
            for name in proof.get('identity_values',[]):
                parsed=verified_pattern({k:v for k,v in proof.items() if k!='profile_identity_binding'},anchor,name)
                if parsed:found.append({'block_id':anchor['block_id'],'value':anchor.get('text'),
                    'declarations':[{'identity':name,'feature_type':parsed['feature_type'],
                                     'formal_value':parsed.get('formal_value')}],'locator':locator})
                else:
                    # Native DT/DD and heading/signature proofs predate the
                    # pattern verifier. Re-read only their actual identity
                    # anchors, not every callable mentioned in the owner body.
                    for bid in (proof.get('identity_anchor_block_ids') or {}).get(name,[]):
                        block=blocks.get(bid,{})
                        if bid not in proof.get('block_ids',[]):continue
                        declared=declarations(block.get('text'),block)
                        if not declared and block.get('block_kind')=='HEADING':
                            heading_plan=copy.deepcopy(plan)
                            heading_plan['execution']['identity']['alternatives']=[{'kind':'HEADING'}]
                            heading_proof=dict(proof,heading_block_id=bid,identity_values=[name])
                            bound,_=identity_bindings(heading_proof,document,heading_plan)
                            declared=[d for f in bound for d in f['declarations']]
                        if not declared and block.get('block_kind')=='CODE' and any(norm(h) in {'syntax','signature','synopsis','grammar'} for h in block.get('heading_ancestry',[])):
                            from agents.owner.declaration_binding import syntax_declarations
                            declared=syntax_declarations(block.get('text'))
                        local=[d for d in declared if d['identity']==name]
                        if local:found.append({'block_id':bid,'value':block['text'],
                                               'declarations':local,'locator':locator})
        expected=set(proof.get('identity_values',[]))
        if found and expected:
            found=[dict(f,declarations=[d for d in f['declarations'] if d['identity'] in expected]) for f in found]
            found=[f for f in found if f['declarations']]
            if not expected<={d['identity'] for f in found for d in f['declarations']}:
                if plan['structure']=='B':
                    partial.extend(dict(f,alternative_index=alternative) for f in found)
                continue
        if found:
            if cfg['identity']['cardinality']=='ONE' and (len(found)!=1 or cfg.get('authority')=='EXPLICIT_DEFINITION' and sum(len(f['declarations']) for f in found)!=1):return [],'AMBIGUOUS_IDENTITY_LOCATION'
            for item in found:item['alternative_index']=alternative
            return found,None
    if partial:return partial,None
    return [],'DECLARATION_PARSE_FAILED' if located else 'IDENTITY_LOCATION_NO_MATCH'


def configured_row_proofs(document,plan,diagnostics=None):
    """A configured column can locate declarations outside legacy headers."""
    result=[]
    for b in document.get('blocks',[]):
        if b.get('block_kind')!='TABLE_ROW' or not applies(plan,b,document):continue
        cells=(b.get('table_metadata') or {}).get('cells') or []
        if not cells or cells==(b.get('table_metadata') or {}).get('headers'):continue
        proof={'heading_block_id':b['block_id'],'block_ids':[b['block_id']]}
        found,error=identity_bindings(proof,document,plan)
        if error:
            if diagnostics is not None:diagnostics.append({'structure':'D','plan_id':plan.get('plan_id'),
                'block_id':b['block_id'],'reason':error,'status':'REJECTED_BINDING',
                'raw_surface':str(b.get('text',''))[:240]})
            continue
        ds=[d for f in found for d in f['declarations']]
        for kind in sorted({d['feature_type'] for d in ds}):
            own=[d for d in ds if d['feature_type']==kind];names=list(dict.fromkeys(d['identity'] for d in own))
            key=[document.get('page_id'),b['block_id'],kind]
            if plan.get('plan_id'):key.append(plan['plan_id'])
            result.append({'section_id':stable_id('configured_definition',key),
                'definition_pattern':'REFERENCE_DECLARATION_SECTION','reference_mode':PATTERN_MODE,
                'source_role':'CATALOG_ENTRY','feature_type':kind,'heading_block_id':b['block_id'],
                'identity_values':names,'primary_identity':names[0],
                'identity_anchor_block_ids':{n:[b['block_id']] for n in names},
                'definition_anchor_block_ids':[b['block_id']],'block_ids':[b['block_id']],
                'start_order':b.get('order',0),'end_order_exclusive':b.get('order',0)+1,
                'end_heading_block_id':None,'example_start_order':None,'dom_path':b.get('dom_path'),
                'dom_parent':b.get('dom_path'),'source_anchor':document.get('url'),
                'scope_status':'COMPLETE','association':'PROFILE_DECLARATION_LOCATION',
                'association_block_id':b['block_id'],'boundary_evidence':['PROFILE_IDENTITY_LOCATION','CURRENT_ROW'],
                'configured_identity_plan':copy.deepcopy(plan['execution']['identity']),
                'configured_document_title':document.get('title') if not b.get('heading_ancestry') else None,
                'source_citations':{b['block_id']:copy.deepcopy(b)}})
    return result


def configured_entry_proofs(document,plan,profile,native,diagnostics=None):
    """Execute a declared repeated-entry boundary without prose-verb tests."""
    from agents.owner.definition_patterns import container_groups,default_patterns
    from agents.owner.surface_patterns import formal_signatures
    containers={p['container'] for p in (profile or {}).get('hint_contract',{}).get('definition_patterns',default_patterns())}
    existing={p.get('heading_block_id') for p in native}
    groups={}
    for kind in ['LIST_ITEM','REPEATED_CONTAINER']:
        if kind not in containers:continue
        for path,members in container_groups(document,kind).items():
            ids={b['block_id'] for b in members}
            if any(ids & {b['block_id'] for b in own} for own in groups.values()):continue
            groups[path]=members
    if plan['execution'].get('authority')=='EXPLICIT_DEFINITION':
        for block in document.get('blocks',[]):
            path=block.get('dom_path','')
            if block.get('block_kind')=='LIST_ITEM' and path and path not in groups:
                if not any(b.get('dom_path','').startswith(path+' > ')for b in document.get('blocks',[])):
                    groups[path]=[block]
    result=[]
    from agents.semantic_stage.source_structure import SourceStructureIndex
    reader=SourceStructureIndex() if plan['execution'].get('anchor_roles') else None
    for path,members in groups.items():
        leaves=sorted((b for b in members if b.get('block_kind')!='LIST_ITEM'),key=lambda b:b.get('order') or 0)
        if not leaves and plan['execution'].get('authority')=='EXPLICIT_DEFINITION':
            leaves=sorted(members,key=lambda b:b.get('order') or 0)
        stops={norm(v) for v in plan['execution'].get('boundary',{}).get('stop_headings',[])}
        stop=next((i for i,b in enumerate(leaves) if b.get('block_kind')=='HEADING' and norm(b.get('text')) in stops),len(leaves))
        leaves=leaves[:stop]
        if not leaves or (leaves[0]['block_id'] in existing and not reader and plan['execution'].get('authority')!='EXPLICIT_DEFINITION'):continue
        first=leaves[0]
        if not applies(plan,first,document):continue
        body='\n'.join(str(b.get('text') or '') for b in leaves)
        if re.search(r'\bAuthors?\s*:',body,re.I) and re.search(r'\bPublisher\s*:',body,re.I):continue
        if plan['execution'].get('authority')!='EXPLICIT_DEFINITION' and not any(b.get('block_kind') in {'TEXT','DEFINITION'} and b is not first for b in leaves):continue
        sketch={'heading_block_id':first['block_id'],'block_ids':[b['block_id'] for b in leaves]}
        bindings,error=identity_bindings(sketch,document,plan)
        if error:
            if diagnostics is not None:
                diagnostics.append({'structure':'C','plan_id':plan.get('plan_id'),
                    'block_id':first['block_id'],'reason':error,'status':'REJECTED_BINDING'})
            continue
        # Parameters inside a declaration are not additional identities. An
        # actual leading declaration must occupy the selected physical slot.
        leading=[f for f in bindings if norm(first.get('text')).startswith(norm(f['value']))]
        if not leading:continue
        ds=[d for f in leading for d in f['declarations']]
        anchor_role=None
        if reader:
            from urllib.parse import urlparse
            observed=reader.observe(document,first)
            roles=[{'feature_type':role,'prefix':prefix,'target_url':target,
                    'source_hash':document.get('page_hash'),'source_url':document.get('url')}
                for target in observed.get('definition_targets',[])
                for role,prefixes in plan['execution']['anchor_roles'].items() for prefix in prefixes
                if urlparse(target).fragment.startswith(prefix)]
            if len({r['feature_type'] for r in roles})>1:continue
            if roles:
                anchor_role=roles[0];ds=[dict(d,feature_type=anchor_role['feature_type']) for d in ds]
        if first['block_id'] in existing and plan['execution'].get('authority')!='EXPLICIT_DEFINITION':
            if not anchor_role:continue
            from agents.owner.definition_patterns import verified_pattern
            previous=[p for p in native if p.get('heading_block_id')==first['block_id']]
            previous_types={p.get('feature_type') or (verified_pattern(p,first,n) or {}).get('feature_type')
                            for p in previous for n in p.get('identity_values',[])}-{None}
            if previous_types=={anchor_role['feature_type']}:continue
        # The physical entry contains nested explanatory lists too. The
        # nearest-container grouping only locates its leading declaration;
        # it must not truncate the verified outer entry to direct leaves.
        foreign={bid for proof in native if proof.get('heading_block_id')!=first['block_id']
                 and not set(proof.get('identity_values',[]))<={d['identity'] for d in ds}
                 for bid in proof.get('block_ids',[])}
        leaves=sorted((b for b in document.get('blocks',[]) if (b.get('block_kind')!='LIST_ITEM' or b['block_id']==first['block_id'])
            and (b.get('dom_path','').startswith(path+' > ') or b['block_id']==first['block_id']) and b['block_id'] not in foreign),
            key=lambda b:b.get('order') or 0)
        stop=next((i for i,b in enumerate(leaves) if b.get('block_kind')=='HEADING' and norm(b.get('text')) in stops),len(leaves))
        leaves=leaves[:stop]
        if not leaves or first['block_id'] not in {b['block_id'] for b in leaves}:
            if diagnostics is not None:
                diagnostics.append({'structure':'C','plan_id':plan.get('plan_id'),
                    'block_id':first['block_id'],'status':'REJECTED_BINDING',
                    'reason':'ENTRY_ANCHOR_OUTSIDE_RETAINED_SCOPE'})
            continue
        for kind in sorted({d['feature_type'] for d in ds}):
            own=[d for d in ds if d['feature_type']==kind];names=list(dict.fromkeys(d['identity'] for d in own))
            if plan['execution'].get('authority')!='EXPLICIT_DEFINITION' and kind=='DATATYPE' and not any(d.get('formal_value') for d in own):
                from agents.owner.surface_patterns import datatype_definition_name
                if not any(datatype_definition_name(b.get('text')) in names for b in leaves[1:]):continue
            anchors=[first['block_id']];formal={}
            if kind=='FUNCTION':
                for block in leaves:
                    values=formal_signatures(block.get('text'))
                    marked=any('replaceable' in n.get('semantic_classes',[]) for n in block.get('inline_nodes',[]))
                    if values and {n for n,_ in values}<=set(names) and (block is first or marked):
                        anchors.append(block['block_id']);formal[block['block_id']]=[v for _,v in values]
            anchors=list(dict.fromkeys(anchors))
            result.append({'section_id':stable_id('configured_entry',[document.get('page_id'),path,kind]),
                'definition_pattern':'REFERENCE_DECLARATION_SECTION','reference_mode':ENTRY_MODE,
                'explicit_definition_authority':plan['execution'].get('authority')=='EXPLICIT_DEFINITION',
                'source_role':'DEFINITION','feature_type':kind,'heading_block_id':first['block_id'],
                'identity_values':names,'primary_identity':names[0],
                'identity_anchor_block_ids':{n:anchors for n in names},'definition_anchor_block_ids':anchors,
                'formal_signature_values':formal,'block_ids':[b['block_id'] for b in leaves],
                'start_order':first.get('order'),'end_order_exclusive':(leaves[-1].get('order') or 0)+1,
                'end_heading_block_id':None,'example_start_order':None,'dom_path':first.get('dom_path'),
                'dom_parent':path,'source_anchor':document.get('url'),'scope_status':'COMPLETE',
                'association':'PROFILE_ENTRY_DECLARATION_LOCATION','association_block_id':first['block_id'],
                'boundary_evidence':['PROFILE_IDENTITY_LOCATION','PHYSICAL_CURRENT_ENTRY'],
                'configured_identity_plan':copy.deepcopy(plan['execution']['identity']),
                'configured_anchor_roles':copy.deepcopy(plan['execution'].get('anchor_roles',{})),
                'configured_anchor_role':anchor_role,
                'source_citations':{first['block_id']:copy.deepcopy(first)}})
    return result


def verified_configured(proof,block,name):
    if block.get('block_id')!=proof.get('heading_block_id'):return None
    citation=(proof.get('source_citations') or {}).get(block.get('block_id'),{})
    if not citation or any(citation.get(k)!=block.get(k) for k in ['text','dom_path','block_kind']):return None
    execution_plan=(proof.get('structure_execution') or {}).get('plan') or {}
    if not applies(execution_plan,block):return None
    identity=proof.get('configured_identity_plan')
    entry=proof.get('reference_mode')==ENTRY_MODE
    if not entry:
        if 'table_metadata' in block and citation.get('table_metadata')!=block['table_metadata']:return None
        if 'table_metadata' not in block and (block.get('source_structure_status')!='RESOLVED' or not block.get('table_context')):return None
    plan={'structure':'C' if entry else 'D','execution':{'version':1,'identity':identity,'fields':[]}}
    try:compile_execution(plan)
    except (ValueError,TypeError):return None
    document={'blocks':[block]}
    if proof.get('configured_document_title'):
        actual=source_observation(citation,block)
        if actual.get('source_structure_status')!='RESOLVED' or (actual.get('observed_context') or {}).get('document_title')!=proof['configured_document_title']:return None
        document['title']=proof['configured_document_title']
    found,error=identity_bindings(proof,document,plan)
    if error:return None
    role=proof.get('configured_anchor_role')
    if role:
        from urllib.parse import urlparse
        if (role.get('prefix') not in proof.get('configured_anchor_roles',{}).get(role.get('feature_type'),[])
                or not urlparse(role.get('target_url','')).fragment.startswith(role['prefix'])):return None
        targets=block.get('definition_targets') if block.get('source_structure_status')=='RESOLVED' else None
        if targets is None:
            targets=source_observation(citation,block,role['source_hash'],role['source_url']).get('definition_targets',[])
        if role['target_url'] not in targets:return None
        found=[dict(f,declarations=[dict(d,feature_type=role['feature_type']) for d in f['declarations']]) for f in found]
    own=[d for f in found for d in f['declarations'] if normalize_identity(d['identity'])==normalize_identity(name) and d['feature_type']==proof.get('feature_type')]
    if len(own)!=1 or name not in proof.get('identity_values',[]):return None
    return {'feature_type':own[0]['feature_type'],'identity_values':proof['identity_values'],
            'formal_value':own[0]['formal_value']}


def annotate(proof,document,plan):
    found,error=identity_bindings(proof,document,plan)
    names={d['identity'] for item in found for d in item['declarations']}
    missing=set(proof.get('identity_values',[]))-names
    if not error and missing and plan['structure']!='B':error='IDENTITY_LOCATION_SUBJECT_MISMATCH'
    if error:return None,error
    if missing:
        proof=dict(proof,identity_values=[n for n in proof['identity_values'] if n in names],
            binding_input_identity_values=list(proof['identity_values']),
            unresolved_identity_bindings=[{'identity':n,'reason':'DECLARATION_LOCATION_UNRESOLVED'} for n in sorted(missing)])
        proof['primary_identity']=proof['identity_values'][0]
    contract=proof['structure_execution'];contract.update(schema_version='StructureExecution/v2',
        profile_execution_digest=compile_execution(plan)['digest'],identity_bindings=found,
        field_bindings=copy.deepcopy(plan['execution'].get('fields',[])),field_authority='EXPLICIT_PROFILE')
    if plan['execution'].get('authority')=='EXPLICIT_DEFINITION':
        contract['authority']='EXPLICIT_DEFINITION'
        contract['declaration_kind']=plan['execution']['declaration_kind']
        contract['publication_permission']=False
        anchors={f['block_id'] for f in found}
        citation_keys={'block_id','text','dom_path','block_kind','heading_level','heading_ancestry','inline_nodes','provenance','table_metadata','order'}
        proof['source_citations']={b['block_id']:copy.deepcopy({k:v for k,v in b.items() if k in citation_keys}) for b in document['blocks'] if b['block_id'] in anchors}
        proof_keys={'heading_block_id','block_ids','identity_values','identity_anchor_block_ids','end_order_exclusive','end_heading_block_id','dom_parent','source_citations'}
        contract['authority_proof']=copy.deepcopy({k:v for k,v in proof.items() if k in proof_keys})
        contract['authority_source']={k:document.get(k) for k in ('url','html_path','page_hash','page_id','dbms')}
    # Field completeness is independent from identity success.
    contract['field_diagnostics']=[]
    if plan['structure']=='B' and plan['execution'].get('authority')!='EXPLICIT_DEFINITION':
        from agents.owner.declaration_binding import callable_members
        blocks={b['block_id']:b for b in document.get('blocks',[])}
        contract['declarations']=[d for d in contract['declarations'] if d['identity'] in names]
        # Identity and literal formal grammar are separate observations. Only
        # grammar before the explicit example boundary in this proven section.
        for d in contract['declarations']:
            bound=[f['block_id'] for f in found if any(v['identity']==d['identity'] for v in f['declarations'])]
            d['anchor_block_ids']=list(dict.fromkeys(bound))
            for bid in proof['block_ids']:
                b=blocks.get(bid,{})
                if (b.get('block_kind') not in {'CODE','SIGNATURE'} or
                    proof.get('example_start_order') is not None and b.get('order',0)>=proof['example_start_order']):continue
                for v in callable_members(b.get('text')):
                    if v['identity']==d['identity'] and not any(x['block_id']==bid and x['value']==v['formal_value'] for x in d['formal_variants']):
                        d['formal_variants'].append({'block_id':bid,'value':v['formal_value']})
        proof['identity_anchor_block_ids']={d['identity']:d['anchor_block_ids'] for d in contract['declarations']}
        proof['definition_anchor_block_ids']=list(dict.fromkeys([proof['heading_block_id']]+[
            bid for d in contract['declarations'] for bid in d['anchor_block_ids']]))
    return proof,None


def inline_field_label(block, labels):
    """Read a leading, explicitly marked label from hash-verified frozen HTML.

    A prose prefix alone is not a label. This uses the same source index as
    identity verification and never changes the immutable DocumentIR text.
    """
    if block.get('block_kind') not in {'TEXT', 'DEFINITION'}:
        return None
    if block.get('source_structure_status')!='RESOLVED':return None
    marked=(block.get('observed_context') or {}).get('inline_field_label') or {}
    if (norm(marked.get('label')) not in labels
            or norm(marked.get('source_text'))!=norm(block.get('text'))
            or marked.get('source_locator')!=block.get('dom_path')
            or not str(marked.get('label_locator','')).startswith(str(block.get('dom_path'))+' > ')
            or not marked.get('value')):return None
    return dict(marked,label=norm(marked['label']),block_id=block['block_id'],inline_label=True)


def field_candidates(unit,evidence,identity):
    """Revalidate physical locations against source-observed cells and labels."""
    execution=unit.get('structure_execution') or {}
    if execution.get('schema_version')!='StructureExecution/v2':return [],[]
    plan=execution.get('plan') or {}
    try:compiled=compile_execution(plan)
    except (ValueError,TypeError,KeyError):compiled=None
    if (not compiled or compiled['digest']!=execution.get('profile_execution_digest')
            or compiled.get('fields',[])!=execution.get('field_bindings',[])):
        return [],[{'status':'REVIEW','reason':'PROFILE_EXECUTION_DIGEST_MISMATCH',
                    'definition_unit_id':unit.get('definition_unit_id')}]
    selected=[];diagnostics=[]
    bindings=execution.get('field_bindings',[]);refs=set(unit.get('member_evidence_refs',[]))
    valid=[evidence[b] for b in refs if b in evidence and evidence[b].get('source_structure_status')=='RESOLVED'
        and evidence[b].get('source_hash')==unit.get('source_hash')
        and evidence[b].get('source_url')==unit.get('source_url')
        and evidence[b].get('owner_local_binding') is True
        and evidence[b].get('evidence_usage')!='DISCOVERY_ONLY']
    valid.sort(key=lambda b:b.get('source_order',0))
    if unit.get('binding_status')!='COMPLETE' or normalize_identity(identity) not in {normalize_identity(n) for n in unit.get('identity_values',[])}:
        return [],[{'status':'REVIEW','reason':'PROFILE_SOURCE_BINDING_INCOMPLETE','definition_unit_id':unit['definition_unit_id']}]
    all_labels={norm(s) for rule in bindings if rule['locator']['kind']=='SECTION_LABEL' for s in rule['locator']['labels']}
    residual_excluded=set()
    if compiled.get('authority')=='EXPLICIT_DEFINITION':
        section_roles={norm(label):rule['role'] for rule in bindings if rule['locator']['kind']=='SECTION_LABEL' for label in rule['locator']['labels']}
        active_sections=[]
        from agents.owner.definition_scope import heading_field_label
        for block in valid:
            if block.get('block_kind')=='HEADING':
                level=block.get('heading_level') or 1
                active_sections=[entry for entry in active_sections if entry[0]<level]
                label=heading_field_label(block.get('text'),identity,set(section_roles))
                if label in section_roles:active_sections.append((level,section_roles[label]))
            if active_sections and active_sections[-1][1]!='DESCRIPTION':residual_excluded.add(block['block_id'])
    inline={b['block_id']:item for b in valid
            for item in [inline_field_label(b,all_labels)] if item} if all_labels else {}
    # Parameter tables remain evidence, but their cell text is not residual
    # subject description. No parameter semantics are invented here.
    parameter_tables=set()
    from agents.owner.composite_definition import table_path
    for block in valid:
        if block.get('block_kind')!='TABLE_ROW':continue
        cells=(block.get('table_context') or {}).get('cells',[])
        headers=[norm(h.get('text')) for c in cells for h in c.get('headers',[])]
        if any(h in {'parameter','parameters','argument','arguments'} for h in headers) and 'description' in headers:
            parameter_tables.add(table_path(block))
    for index,rule in enumerate(bindings):
        role=rule['role'];locator=rule['locator'];kind=locator['kind'];found=[]
        if kind in {'COLUMN_HEADER','CELL_INDEX'}:
            for block in valid:
                if block.get('block_kind')!='TABLE_ROW':continue
                for cell in locate_cells(block,locator,source=True):
                    if str(cell['text']).strip():found.append({'block_id':block['block_id'],'cell_index':cell['index'],
                        'source_locator':cell['locator'],'value':cell['text']})
        elif kind=='DECLARATION_ANCHORS':
            if compiled.get('authority')=='EXPLICIT_DEFINITION' and plan.get('structure') in {'C','D'}:
                for binding in execution.get('identity_bindings',[]):
                    if any(normalize_identity(d['identity'])==normalize_identity(identity) for d in binding.get('declarations',[])):
                        found.append({'block_id':binding['block_id'],'value':binding['value'],'source_locator':binding.get('node_locator')})
            for declaration in execution.get('declarations',[]):
                if normalize_identity(declaration['identity'])!=normalize_identity(identity):continue
                for variant in declaration.get('formal_variants',[]):
                    if variant['block_id'] in {b['block_id'] for b in valid}:
                        found.append({'block_id':variant['block_id'],'value':variant['value'],
                            'source_locator':evidence[variant['block_id']].get('dom_path')})
            # A complete same-subject grammar block can occur after a short
            # entry label. Literal invocations and other subjects do not qualify.
            from agents.contracts.definition_entry import observed_callable_declaration
            from agents.contracts.literal_source import verified_literal_text
            existing={f['block_id']for f in found}
            for block in valid:
                if block['block_id'] in existing or block.get('block_kind')!='CODE':continue
                if any(norm(v.get('text')).startswith('example') for v in (block.get('observed_context') or {}).get('labels',[])):continue
                value=verified_literal_text(block)
                if not value:continue
                parsed=observed_callable_declaration(value,block.get('inline_nodes',[]))
                if parsed and normalize_identity(parsed[0])==normalize_identity(identity) and norm(parsed[1])==norm(value):
                    found.append({'block_id':block['block_id'],'value':value,'source_locator':block.get('dom_path'),
                                  'inferred_signature':True})
        elif kind=='SECTION_LABEL':
            from agents.owner.definition_scope import heading_field_label
            active=None
            for block in valid:
                marked=inline.get(block['block_id'])
                if marked:
                    active=None
                    if marked['label'] in {norm(s) for s in locator['labels']}:
                        children=[b for b in valid if b.get('block_kind')=='CODE'
                                  and str(b.get('dom_path') or '').startswith(str(block.get('dom_path'))+' > ')]
                        if role=='FORMAL_SYNTAX' and children:
                            for child in children:
                                found.append({'block_id':child['block_id'],
                                    'value':(child.get('observed_context') or {}).get('literal_code_text') or child.get('text'),
                                    'source_locator':child.get('dom_path'),'label_block_id':block['block_id'],
                                    'label_locator':marked['label_locator']})
                        else:found.append(dict(marked))
                    continue
                observed_mark=(block.get('observed_context') or {}).get('inline_field_label') or {}
                if observed_mark and norm(observed_mark.get('label')) not in all_labels:
                    # A new explicitly marked field ends the preceding field;
                    # its unknown semantics do not inherit "Example".
                    active=None
                    continue
                label=(heading_field_label(block.get('text'),identity,all_labels)
                       if block.get('block_kind')=='HEADING' else norm(block.get('text')))
                if label in all_labels:
                    active=block if label in {norm(s) for s in locator['labels']} else None
                    continue
                observed_label=norm((block.get('observed_context') or {}).get('visible_label'))
                authoritative=compiled.get('authority')=='EXPLICIT_DEFINITION'
                if authoritative and active and active.get('block_kind')=='HEADING':
                    if block.get('block_kind')=='HEADING':
                        if (block.get('heading_level') or 1)<=(active.get('heading_level') or 1):active=None
                        elif 'HEADING' not in rule.get('block_kinds',[]):continue
                    # A deeper unconfigured subsection inherits the configured
                    # field role; its observed label does not replace that role.
                elif block.get('block_kind')=='HEADING' or (active and observed_label and observed_label!=norm(active.get('text'))):active=None
                if active and active.get('block_kind')=='HEADING':
                    from agents.owner.definition_scope import heading_container
                    container=heading_container(active)
                    if container and not str(block.get('dom_path') or '').startswith(container+' > '):active=None
                if active and not block.get('shared_definition_context'):
                    if role=='FORMAL_SYNTAX':
                        from agents.semantic_stage.structured_semantics import _code_surface
                        if not (rule.get('block_kinds')==['CODE'] and block.get('block_kind')=='CODE') and not _code_surface(block):continue
                    found.append({'block_id':block['block_id'],'value':block.get('literal_text') or block.get('text'),
                        'source_locator':block.get('dom_path'),'label_block_id':active['block_id']})
        elif kind=='BODY_PROSE':
            anchors={b for d in execution.get('declarations',[]) for b in d['anchor_block_ids']}
            for block in valid:
                from agents.owner.definition_patterns import verified_pattern
                self_prose=any((verified_pattern(p,block,identity) or {}).get('content_role')=='DESCRIPTION'
                    for p in block.get('definition_section_proofs',[])
                    if (p.get('structure_execution') or {}).get('execution_id')==execution.get('execution_id'))
                in_parameter_table=any(str(block.get('dom_path') or '').startswith(p+' > ') for p in parameter_tables)
                if ((block['block_id'] not in anchors or self_prose) and block.get('block_kind') in {'TEXT','DEFINITION','LIST_ITEM'}
                    and not in_parameter_table and block['block_id'] not in residual_excluded
                    and norm(block.get('text')) not in all_labels and not block.get('shared_definition_context')):
                    found.append({'block_id':block['block_id'],'value':block['text'],'source_locator':block.get('dom_path')})
        elif kind in {'OWNER_SQL_APPLICATION','DEFINITION_EXPRESSION'}:
            from agents.contracts.literal_source import verified_literal_text
            from agents.contracts.definition_entry import observed_callable_declaration
            for block in valid:
                if block.get('block_kind')!='CODE':continue
                value=verified_literal_text(block)
                if value is None:continue
                expression=bool(re.match(r'^\s*'+re.escape(identity)+r'\s*\([^\n]*\)\s*=',value,re.I))
                parsed=observed_callable_declaration(value,block.get('inline_nodes',[]))
                if (kind=='OWNER_SQL_APPLICATION' and parsed and normalize_identity(parsed[0])==normalize_identity(identity)
                        and norm(parsed[1])==norm(value)):continue
                if (kind=='DEFINITION_EXPRESSION')==expression:
                    found.append({'block_id':block['block_id'],'value':value,'source_locator':block.get('dom_path')})
        prefixes=compiled.get('boundary',{}).get('exclude_text_prefixes',[])+rule.get('exclude_text_prefixes',[])
        found=[f for f in found if (not rule.get('block_kinds') or evidence[f['block_id']].get('block_kind') in rule['block_kinds']) and not any(norm(f.get('value')).startswith(norm(p)) for p in prefixes)]
        status='NO_MATCH' if not found else 'MATCHED'
        if rule.get('cardinality','ONE_OR_MORE')=='ONE' and len(found)>1:status='AMBIGUOUS';found=[]
        diagnostic={'definition_unit_id':unit['definition_unit_id'],'binding_index':index,'role':role,
            'locator':locator,'status':status,'candidate_count':len(found)}
        diagnostics.append(diagnostic)
        for item in found:
            shared=(rule.get('sharing','SINGLE_SUBJECT')=='SHARED_CONTEXT' or
                    len(unit['identity_values'])>1 and rule.get('sharing','SINGLE_SUBJECT')!='DECLARED_MEMBERS')
            proof=unit.get('source_proof') or {}
            if (shared and rule.get('sharing','SINGLE_SUBJECT')=='SINGLE_SUBJECT'
                    and (proof.get('association') or unit.get('definition_association')) in {'PROFILE_HEADING_LOCATION','EXACT_DEFINITION_HEADING'}
                    and normalize_identity(proof.get('primary_identity') or unit.get('primary_identity') or '')==normalize_identity(identity)):
                # Additional co-declared heads must not erase the heading's
                # existing fields. Secondary heads still require explicit
                # sharing or their own local field occurrence.
                shared=False
            selected.append(dict(item,role=role,definition_unit_id=unit['definition_unit_id'],
                binding_index=index,sharing='SHARED_CONTEXT' if shared else 'SUBJECT',
                profile_execution_digest=execution['profile_execution_digest']))
    # Explicit labels take precedence over the residual body selector. Two
    # explicit roles for the same physical location are a review, never a vote.
    residual={'BODY_PROSE','OWNER_SQL_APPLICATION'}
    explicit={(x['block_id'],x.get('cell_index')) for x in selected
              if bindings[x['binding_index']]['locator']['kind'] not in residual and not x.get('inferred_signature')}
    explicit.update((bid,None) for bid in inline)
    selected=[x for x in selected if (bindings[x['binding_index']]['locator']['kind'] not in residual and not x.get('inferred_signature'))
              or (x['block_id'],x.get('cell_index')) not in explicit]
    positions={}
    for item in selected:positions.setdefault((item['block_id'],item.get('cell_index')),set()).add(item['role'])
    for item in selected:
        if len(positions[(item['block_id'],item.get('cell_index'))])>1:
            item['sharing']='CONFLICT'
    selected.sort(key=lambda item:((evidence[item['block_id']].get('source_order') or 0),
                                  item.get('cell_index') if item.get('cell_index') is not None else -1))
    return selected,diagnostics


from functools import lru_cache


@lru_cache(maxsize=4)
def _authority_source_document(path,url,source_hash,dbms):
    """Bounded process cache only; no full DocumentIR is persisted in proofs."""
    from agents.document_structure_v2.topology import build_page_topology
    from agents.contracts.document_ir import from_v2_page
    page=build_page_topology({'url':url,'html_path':path},{})
    if page.get('page_hash')!=source_hash:raise ValueError('source hash mismatch')
    return from_v2_page(page,dbms=dbms,html_path=path)


def _revalidate_explicit_authority(proof, block, name):
    """Return source-verified authority metadata, or None. Never grants publication."""
    from pathlib import Path
    from bs4 import BeautifulSoup
    from agents.document_structure_v2.topology import _dom_path
    contract=proof.get('structure_execution') or {}
    plan=contract.get('plan') or {}
    if not proof.get('source_citations'):
        proof={**(contract.get('authority_proof') or {}),'structure_execution':contract}
    try:
        cfg=compile_execution(plan)
        if not cfg or cfg.get('authority')!='EXPLICIT_DEFINITION' or cfg['digest']!=contract.get('profile_execution_digest'):return None
        citation=(proof.get('source_citations') or {}).get(block.get('block_id'))
        if not citation or any(citation.get(k)!=block.get(k) for k in ('text','dom_path','block_kind','heading_level')):return None
        provenance=citation.get('provenance') or {}
        source_hash,soup,dom=_frozen_authority_source(provenance['html_path'],_source_stat(provenance['html_path']))
        if source_hash!=provenance.get('page_hash'):return None
        if provenance.get('page_hash')!=contract.get('source_hash') or provenance.get('source_url')!=contract.get('source_url'):return None
        nodes=dom.get(citation.get('dom_path'),[])
        if len(nodes)!=1:return None
        node=nodes[0]
        descriptor=contract.get('authority_source')
        if not descriptor or descriptor.get('page_hash')!=contract.get('source_hash') or descriptor.get('url')!=contract.get('source_url'):return None
        snapshot=_authority_source_document(descriptor['html_path'],descriptor['url'],descriptor['page_hash'],descriptor.get('dbms') or '')
        if snapshot.get('page_id')!=descriptor.get('page_id'):return None
        if plan['structure'] in {'A','B','C','D'}:
            if plan['structure'] in {'A','B'}:
                from agents.owner.explicit_heading_plan import configured_heading_proofs
                rebuilt=configured_heading_proofs(snapshot,plan,[])
            elif plan['structure']=='D':rebuilt=configured_row_proofs(snapshot,plan,[])
            else:
                rebuilt=configured_entry_proofs(snapshot,plan,None,[],[])
                # Native DT/DD boundaries are physical term/body pairs, not list containers.
                if node.name=='dt':
                    from agents.owner.definition_scope import _native_definition_scopes
                    from agents.owner.scope_plan import default_scope_patterns
                    native=_native_definition_scopes(snapshot,{'hint_contract':{'definition_scope_patterns':default_scope_patterns()}})
                    for candidate in native:
                        if candidate.get('heading_block_id')!=proof.get('heading_block_id'):continue
                        bound,error=identity_bindings(candidate,snapshot,plan)
                        if not error:
                            names=list(dict.fromkeys(d['identity']for b in bound for d in b['declarations']))
                            rebuilt.append(dict(candidate,identity_values=names))
            matches=[p for p in rebuilt if p['heading_block_id']==proof.get('heading_block_id') and p['identity_values']==proof.get('identity_values')]
            if len(matches)!=1:return None
            if any(matches[0].get(k)!=proof.get(k) for k in ('block_ids','end_order_exclusive','end_heading_block_id')):return None
            if plan['structure']=='D':
                # A reused native row proof names the immediate table section
                # as its parent; a configured row proof names the row itself.
                # Both bind exactly this row, never the surrounding table rows.
                if node.name!='tr' or proof.get('dom_parent') not in {citation.get('dom_path'),_dom_path(node.parent)}:return None
            elif matches[0].get('dom_parent')!=proof.get('dom_parent'):return None
        expected_declarations=[{'identity':n,'anchor_block_ids':(proof.get('identity_anchor_block_ids') or {}).get(n,[])} for n in proof.get('identity_values',[])]
        if [{k:d.get(k) for k in ('identity','anchor_block_ids')} for d in contract.get('declarations',[])]!=expected_declarations:return None
        canonical=next((b for b in snapshot.get('blocks',[]) if b['block_id']==citation.get('block_id') and b.get('dom_path')==citation.get('dom_path') and b.get('block_kind')==citation.get('block_kind')),None)
        if canonical is None or canonical.get('text')!=citation.get('text'):return None
        if citation.get('block_kind')=='HEADING' and node.name!='h'+str(citation.get('heading_level')):return None
        if not applies(plan,citation):return None
        # Rebuild cells from the current hash-verified source, never IR headers.
        observed=source_observation(citation,citation)
        if observed.get('source_structure_status')!='RESOLVED':return None
        source=dict(citation,**observed)
        sketch={'heading_block_id':source['block_id'],'block_ids':[source['block_id']]}
        bindings,error=identity_bindings(sketch,{'blocks':[source]},plan)
        if error:return None
        original=[b for b in contract.get('identity_bindings',[]) if b['block_id']==source['block_id']]
        if bindings!=original:return None
        own=[d for b in bindings for d in b['declarations'] if d['identity']==name]
        if len(own)!=1 or name not in proof.get('identity_values',[]):return None
        return {'authority':'EXPLICIT_DEFINITION','feature_type':own[0]['feature_type'],
                'identity_values':list(proof['identity_values']),'formal_value':None,
                'profile_execution_digest':cfg['digest'],'source_hash':contract['source_hash'],
                'source_url':contract['source_url'],'identity_bindings':bindings,'publication_permission':False}
    except (OSError,KeyError,TypeError,ValueError,AttributeError):return None


def _source_stat(path):
    from pathlib import Path
    stat=Path(path).stat()
    return (stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size)


@lru_cache(maxsize=4)
def _frozen_authority_source(path,stat_key):
    from pathlib import Path
    from bs4 import BeautifulSoup
    from agents.document_structure_v2.topology import _dom_path
    raw=Path(path).read_bytes()
    soup=BeautifulSoup(raw,'lxml')
    dom={}
    for node in soup.find_all(True):dom.setdefault(_dom_path(node),[]).append(node)
    return hashlib.sha256(raw).hexdigest(),soup,dom


_authority_results={}


def revalidate_explicit_authority(proof,block,name):
    """Revalidate an identity anchor, cached by source stat and proof fingerprint.

    Call once per identity anchor, then use field_candidates for member fields.
    Source mutations invalidate both successful and failed cached checks.
    """
    try:
        execution=proof.get('structure_execution') or {}
        source=execution.get('authority_source') or {}
        path=source.get('html_path')
        key=(digest(proof),digest(block),name,path,_source_stat(path))
        if key not in _authority_results:
            if len(_authority_results)>=128:_authority_results.pop(next(iter(_authority_results)))
            _authority_results[key]=_revalidate_explicit_authority(proof,block,name)
        return copy.deepcopy(_authority_results[key])
    except (OSError,ValueError,TypeError):return None
