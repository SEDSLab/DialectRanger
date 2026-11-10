"""Subject-local physical field witnesses shared by all semantic consumers.

Profile slots are revalidated, not trusted as model annotations. Unclassified
source stays a candidate; it cannot override a known physical field slot.
"""
import re
from agents.contracts.field_validation import digest
from agents.contracts.identity_key import normalize_identity
from agents.contracts.literal_source import verified_literal_text

VERSION='FieldEvidence/v1'
ROLE_FIELDS={'DESCRIPTION':'description','FORMAL_SYNTAX':'formal_syntax','EXAMPLE':'official_examples',
    'CONSTRUCTION_EXAMPLE':'official_examples','DECLARATION_EXAMPLE':'official_examples',
    'EXAMPLE_RESULT':'official_examples','PARAMETER':'parameters','RETURN_TYPE':'return_type',
    'NOTE':'notes','CONSTRAINT':'notes'}
HEADERS={'description':'description','definition':'description','meaning':'description',
    'syntax':'formal_syntax','signature':'formal_syntax','function':'formal_syntax','function name':'formal_syntax',
    'example':'official_examples','example query':'official_examples','examples':'official_examples',
    'result':'official_examples','return type':'return_type','returns':'return_type',
    'parameter':'parameters','argument':'parameters','parameter name':'parameters','argument name':'parameters'}


def comparable(text):
    """Permit presentation whitespace only; never erase SQL tokens or case."""
    # Token boundaries make TURN( x ) equal to TURN(x), but keep SELECT x
    # distinct from SELECTx and '-' '-' distinct from '--'. Quoted text is
    # opaque, including its internal whitespace and case.
    tokens=re.findall(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[\w$]+|[!<>=:~%^&|+*/-]+|[^\s]",str(text or ''))
    return '\x1f'+'\x1f'.join(tokens)+'\x1f' if tokens else ''


def source_text(block):
    literal=verified_literal_text(block)
    return literal if literal is not None else str(block.get('text') or '')


def role_hypothesis_supported(fragment,field,bundle):
    """Unlabelled evidence needs a local surface basis, not provider assertion."""
    if fragment.get('basis') in {'OTHER_SUBJECT_EXAMPLE','OTHER_SUBJECT_DECLARATION'}:return False
    if fragment['field'] is not None:return fragment['field']==field
    text=fragment['raw_text'].strip()
    if field=='formal_syntax':
        from agents.contracts.definition_entry import observed_callable_declaration
        parsed=observed_callable_declaration(text,formal_marker=True)
        if parsed:return not bundle.get('normalized_identity') or normalize_identity(parsed[0])==normalize_identity(bundle['normalized_identity'])
        if re.match(r'^(?:SELECT|INSERT|UPDATE|DELETE|WITH)\b',text,re.I):return False
        if bundle.get('feature_type')=='OPERATOR':
            from agents.owner.surface_patterns import operator_label
            operator=operator_label(text)
            if operator and normalize_identity(operator[0])==normalize_identity(bundle.get('normalized_identity')):return True
        return bool(re.search(r'(?:\s[!<>=~%&|^+*/-]+\s|\[[^\]]+\]|\{[^}]+\})',text))
    if field=='official_examples':
        if re.search(r'(?m)^\s*[A-Za-z][\w.-]*>\s*(?:SET|SELECT|WITH|CALL|VALUES)\b',text,re.I):return True
        return bool(re.match(r'^(?:SELECT|WITH|VALUES|CALL|INSERT|UPDATE|DELETE|CREATE|ALTER|--|/\*)\b',text,re.I)
            or re.match(r'^[\w.\"]+\s*\(',text))
    return True


def _same(left, right):
    return normalize_identity(left) == normalize_identity(right)


def field_subject_bound(block, payload):
    uid = payload.get('identity_group_id')
    subject = payload.get('normalized_identity', '')
    if block.get('identity_unit_id') not in (None, uid):
        return False
    if block.get('owner_local_binding') is not True:
        return False
    if not block.get('source_url') or not block.get('source_hash'):
        return False
    context = block.get('observed_context') or {}
    if context.get('feature_domain') in {'DEVELOPER_API', 'EMBEDDED_QUERY_LANGUAGE'}:
        return False
    heading = ' / '.join(str(x) for x in block.get('heading_ancestry', []))
    identity_header=r'(?:(?:function(?: name)?|signature)(?:\s*(?:->|\u2192)\s*returns?)?|syntax|operator|expression|data ?type|sql type|type name|type|name)'
    cells=(block.get('table_context') or {}).get('cells', [])
    local_identity_slot=block.get('block_kind')=='TABLE_ROW' and any(
        re.fullmatch(identity_header,' '.join(str(h.get('text') or '').split()).casefold())
        for c in cells for h in c.get('headers',[]))
    if block.get('block_kind')=='TABLE_ROW' and re.search(r'\b(?:examples?|parameters?|arguments?|mapping|connectors?|jdbc|odbc|api|sdk|configuration|navigation)\b', heading, re.I):
        return False
    for unit in payload.get('definition_units', []):
        if (unit.get('binding_status') == 'COMPLETE'
                and any(_same(n, subject) for n in unit.get('identity_values', []))
                and block.get('block_id') in unit.get('member_evidence_refs', [])
                and unit.get('source_url') == block.get('source_url')
                and unit.get('source_hash') == block.get('source_hash')):
            # A containing multi-member definition does not assign every row
            # to every member. An explicit row identity has priority over the
            # surrounding section, including tables of quantified expressions.
            if local_identity_slot or (block.get('block_kind')=='TABLE_ROW' and len(unit.get('identity_values',[]))!=1):
                continue
            return True
    if block.get('block_kind') != 'TABLE_ROW':
        return any(any(_same(n, subject) for n in proof.get('identity_values', []))
                   and block.get('block_id') in proof.get('block_ids', [])
                   for proof in block.get('definition_section_proofs', []))
    # A header is a physical field witness, not proof of the row's subject.
    # Use actual defining occurrences, never a name mentioned in prose.
    from agents.contracts.definition_entry import observed_callable_declaration, declaration_names
    if re.search(r'\b(?:examples?|parameters?|arguments?|mapping|connectors?|jdbc|odbc|api|sdk|configuration|navigation)\b', heading, re.I):
        return False
    kind = str(payload.get('feature_type', '')).upper()
    for cell in (block.get('table_context') or {}).get('cells', []):
        headers = {' '.join(str(h.get('text') or '').split()).casefold() for h in cell.get('headers', [])}
        row_head = cell.get('header_role') == 'ROW_HEADER' and cell.get('column_start') == 0
        typed = any(re.fullmatch(identity_header,h) for h in headers)
        if row_head and not typed:
            pattern={'FUNCTION':r'\bfunctions?\b','OPERATOR':r'\boperators?\b',
                     'DATATYPE':r'\b(?:data[ -]?types?|storage classes)\b'}.get(kind)
            row_head=bool(pattern and re.search(pattern,heading,re.I))
        if not (row_head or typed):
            continue
        value = str(cell.get('text') or '').strip()
        if kind == 'FUNCTION':
            signature = re.split(r'\s*(?:->|\u2192)\s*', value, maxsplit=1)[0]
            parsed = observed_callable_declaration(signature, formal_marker=True)
            if parsed and _same(parsed[0], subject):
                return True
        elif kind == 'DATATYPE' and any(_same(n, subject) for n in declaration_names(value)):
            return True
        elif kind == 'OPERATOR':
            from agents.owner.surface_patterns import operator_label
            parsed = operator_label(value)
            if parsed and _same(parsed[0], subject):
                return True
    return False



def build_field_evidence(bundle):
    blocks={str(b.get('evidence_ref') or b.get('block_id')):b for b in bundle.get('source_blocks',[])}
    uid=bundle.get('identity_group_id');subject=bundle.get('normalized_identity','')
    fragments=[];controlled=set()
    def add(bid,value,field,locator=None,cell=None,unit=None,level='CANDIDATE',basis='SOURCE_BLOCK'):
        b=blocks.get(bid)
        if not b or b.get('evidence_usage')=='DISCOVERY_ONLY' or not str(value or '').strip():return
        if (field=='official_examples' and basis in {'OBSERVED_CELL_HEADER','EXAMPLE_RESULT_PAIR'}
                and str(bundle.get('feature_type','')).upper()=='FUNCTION'):
            expression=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/",' ',str(value),flags=re.S)
            if not re.search(r'(?<![\w$])'+re.escape(subject)+r'\s*\(',expression,re.I):
                level='CANDIDATE';basis='OTHER_SUBJECT_EXAMPLE'
        if field=='formal_syntax' and basis=='OBSERVED_CELL_HEADER':
            from agents.contracts.definition_entry import observed_callable_declaration
            declaration=observed_callable_declaration(str(value),formal_marker=True)
            if declaration and normalize_identity(declaration[0])!=normalize_identity(subject):
                level='CANDIDATE';basis='OTHER_SUBJECT_DECLARATION'
        # A catalog name stub is an identity, not a formal declaration. Resolve
        # this once at the field boundary, not separately in each consumer.
        if (field=='formal_syntax' and basis=='OBSERVED_CELL_HEADER'
                and (normalize_identity(value)==normalize_identity(subject)
                     or re.fullmatch(re.escape(subject)+r'\s*\(\s*\)',str(value),re.I))):
            labels={str(h.get('text') or '').strip().casefold()
                for c in (b.get('table_context') or {}).get('cells',[]) if c.get('cell_index')==cell
                for h in c.get('headers',[])}
            if labels & {'function','function name'}:level='CANDIDATE';basis='CATALOG_IDENTITY_LABEL'
        row={'schema_version':VERSION,'identity_unit_id':uid,'subject':subject,
            'block_id':bid,'source_url':b.get('source_url'),'source_hash':b.get('source_hash'),
            'page_id':b.get('page_id'),'owner_ids':sorted(b.get('owner_ids') or []),
            'dom_locator':locator or b.get('dom_path'),'cell_index':cell,
            'definition_unit_id':(unit or {}).get('definition_unit_id'),
            'profile_digest':(unit or {}).get('profile_digest'),
            'field':field,'raw_text':str(value),'source_order':b.get('source_order',0),
            'proof_level':level,'basis':basis}
        row['fragment_id']='field_fragment:'+digest(row)[:32];fragments.append(row)
    from agents.owner.profile_execution import field_candidates
    for unit in bundle.get('definition_units',[]):
        from agents.owner.composite_definition import verified_fields
        members=verified_fields(unit,blocks,subject)
        for m in members:
            if m['field_role'] in ROLE_FIELDS:
                b=blocks[m['block_id']];cells=(b.get('table_context') or {}).get('cells',[])
                cell=next((c for c in cells if c['cell_index']==m['cell_index']),{})
                add(m['block_id'],m['value'],ROLE_FIELDS[m['field_role']],cell.get('dom_locator'),m['cell_index'],unit,'VERIFIED','VERIFIED_FIELD_TABLE')
                controlled.add(m['block_id'])
        for i,m in enumerate(members):
            if not i or m['field_role']!='EXAMPLE_RESULT':continue
            first=members[i-1]
            if first['field_role']!='EXAMPLE' or first['table_locator']!=m['table_locator']:continue
            parent=next((b for b in blocks.values() if b.get('dom_path')==m['table_locator'] and b.get('source_hash')==unit.get('source_hash') and b.get('source_url')==unit.get('source_url') and b.get('block_id') in unit.get('member_evidence_refs',[])),None)
            if parent:
                value=first['field_label']+' '+first['value']+'\n'+m['field_label']+' '+m['value']
                add(parent['block_id'],value,'official_examples',unit=unit,level='VERIFIED',basis='VERIFIED_FIELD_TABLE_PAIR')
        candidates,_=field_candidates(unit,blocks,subject)
        local=[]
        for c in candidates:
            field=ROLE_FIELDS.get(c['role'])
            if not field or c.get('sharing')!='SUBJECT':continue
            add(c['block_id'],c['value'],field,c.get('source_locator'),c.get('cell_index'),unit,'VERIFIED','PROFILE_PHYSICAL_SLOT')
            controlled.add(c['block_id']);local.append(c)
        for c in local:
            if c['role']!='EXAMPLE' or c.get('cell_index') is None:continue
            pair=[v for v in local if v['role']=='EXAMPLE_RESULT' and v['block_id']==c['block_id'] and v.get('cell_index')==c['cell_index']+1]
            if len(pair)==1:
                add(c['block_id'],c['value']+'\n'+pair[0]['value'],'official_examples',c.get('source_locator'),c['cell_index'],unit,'VERIFIED','EXAMPLE_RESULT_PAIR')
    for bid,b in blocks.items():
        if bid in controlled:continue
        # A DT/heading may contain several validated signatures. Verify every
        # variant at its own defining occurrence instead of requiring the
        # whole container to parse as one callable.
        from agents.contracts.definition_entry import observed_callable_declaration
        variants=[]
        if b.get('block_kind')!='TABLE_ROW' and set(b.get('occurrence_roles') or []) & {'DEFINING_IDENTITY','FORMAL_VARIANT'}:
            for value in bundle.get('defining_variants',[]):
                parsed=observed_callable_declaration(value,formal_marker=True)
                if parsed and normalize_identity(parsed[0])==normalize_identity(subject) and comparable(value) in comparable(source_text(b)):
                    variants.append(value)
            for value in variants:add(bid,value,'formal_syntax',level='VERIFIED',basis='VALIDATED_DEFINING_VARIANT')
        if variants:continue
        from agents.owner.definition_patterns import verified_pattern
        declarations=[v for p in b.get('definition_section_proofs',[])
            for v in [verified_pattern(p,b,subject)] if v and v.get('formal_value')]
        if declarations and b.get('block_kind')!='TABLE_ROW':
            for v in declarations:add(bid,v['formal_value'],'formal_syntax',level='VERIFIED',basis='VERIFIED_LOCAL_DECLARATION')
            for variant in bundle.get('defining_variants',[]):
                if comparable(variant) in comparable(source_text(b)):
                    add(bid,variant,'formal_syntax',level='VERIFIED',basis='VALIDATED_VARIANT_IN_DECLARATION_ANCHOR')
            continue
        from agents.contracts.definition_entry import compound_entry
        compound=compound_entry(b,subject,bundle.get('feature_type',''))
        if compound:
            for part in compound.get('fragments',[]):
                field=ROLE_FIELDS.get(part['role'])
                if field:
                    add(bid,part.get('value') or part['text'],field,part.get('dom_locator'),level='VERIFIED',basis='COMPOUND_DEFINITION_CELL')
                    if part.get('return_type'):
                        add(bid,part['return_type'],'return_type',part.get('dom_locator'),level='VERIFIED',basis='COMPOUND_RETURN_DECLARATION')
                        for leaf_id,leaf in blocks.items():
                            if leaf.get('dom_path')==part.get('dom_locator') and leaf.get('source_hash')==b.get('source_hash') and leaf.get('source_url')==b.get('source_url'):
                                add(leaf_id,part['return_type'],'return_type',level='VERIFIED',basis='COMPOUND_RETURN_DECLARATION')
                    if part.get('result_locator'):
                        result=next((p for p in compound['fragments'] if p['dom_locator']==part['result_locator'] and p['role']=='EXAMPLE_RESULT'),None)
                        if result:add(bid,part['text']+'\n'+result['text'],field,b.get('dom_path'),level='VERIFIED',basis='COMPOUND_EXAMPLE_RESULT')
            continue
        cells=(b.get('table_context') or {}).get('cells',[]);known=[]
        parameter_table=any(str(h.get('text') or '').strip().casefold() in {'parameter','argument','parameter name','argument name'} for c in cells for h in c.get('headers',[]))
        for c in cells:
            headers={' '.join(str(h.get('text') or '').split()).rstrip(':').casefold() for h in c.get('headers',[])}
            # Combined declaration/return columns expose two literal fields.
            # Interpret their structure once here; downstream never needs the
            # display header spelling to recover an independently proven role.
            if (any(re.fullmatch(r'(?:function|signature)\s*(?:->|\u2192)\s*returns?',h) for h in headers)
                    and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}
                    and field_subject_bound(b,bundle)):
                parts=re.split(r'\s*(?:->|\u2192)\s*',str(c.get('text') or ''),maxsplit=1)
                parsed=observed_callable_declaration(parts[0],formal_marker=True)
                if parsed and normalize_identity(parsed[0])==normalize_identity(subject):
                    add(bid,parts[0],'formal_syntax',c.get('dom_locator'),c.get('cell_index'),
                        level='VERIFIED',basis='OBSERVED_DECLARATION_RETURN_COLUMN')
                    if len(parts)==2 and re.fullmatch(r'[A-Za-z_][\w .\[\](),]*',parts[1]):
                        add(bid,parts[1],'return_type',c.get('dom_locator'),c.get('cell_index'),
                            level='VERIFIED',basis='OBSERVED_DECLARATION_RETURN_COLUMN')
            fields={HEADERS[h] for h in headers if h in HEADERS}
            if len(fields)==1 and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'} and not c.get('nested_table'):
                field=next(iter(fields));known.append((c,field))
                if parameter_table and field=='description':field='parameters';known[-1]=(c,field)
                bound=field_subject_bound(b,bundle)
                add(bid,c.get('text'),field,c.get('dom_locator'),c.get('cell_index'),
                    level='VERIFIED' if bound else 'CANDIDATE',
                    basis='OBSERVED_CELL_HEADER' if bound else 'UNPROVEN_SUBJECT_CELL_HEADER')
        if known:
            for c,field in known:
                if field!='official_examples':continue
                other=[v for v,f in known if f==field and v.get('cell_index')==c.get('cell_index',-2)+1]
                if len(other)==1:add(bid,str(c.get('text'))+'\n'+str(other[0].get('text')),field,c.get('dom_locator'),c.get('cell_index'),
                    level='VERIFIED' if field_subject_bound(b,bundle) else 'CANDIDATE',basis='EXAMPLE_RESULT_PAIR')
            continue
        labels={str(l.get('text') or '').strip().rstrip(':').casefold() for l in (b.get('observed_context') or {}).get('labels',[])}
        from agents.owner.structure_execution import verified_field_labels
        profile_labels=verified_field_labels(b,bundle.get('definition_units',[]),blocks,subject)
        if profile_labels:labels=set(profile_labels)
        if labels & {'example','examples'}:labels={'examples'}
        fields={HEADERS[l] for l in labels if l in HEADERS}
        # Visible labels constrain code, while explanatory prose may occur in
        # a Syntax section. Structural inference is retained as candidate only.
        if len(fields)==1:
            field=next(iter(fields))
            if field=='formal_syntax' and b.get('block_kind') not in {'CODE','SIGNATURE'}:field=None
            add(bid,source_text(b),field,level='VERIFIED' if field else 'CANDIDATE',basis='OBSERVED_FIELD_LABEL')
        else:
            roles=set(b.get('structural_roles') or [])
            fields={ROLE_FIELDS[r] for r in roles if r in ROLE_FIELDS}
            add(bid,source_text(b),next(iter(fields)) if len(fields)==1 else None,
                basis='STRUCTURAL_CANDIDATE' if fields else 'UNKNOWN_SOURCE_ROLE')
    return fragments


def bind_claim(text,refs,bundle,field,proposal=None,*,fragments=None):
    """Return independently reconstructed witnesses, never provider locators."""
    fragments=build_field_evidence(bundle) if fragments is None else fragments
    supplied=bundle.get('field_evidence')
    if supplied is not None and supplied!=fragments:return None,'FIELD_EVIDENCE_CONTEXT_MISMATCH'
    wanted=comparable(text)
    candidates=[f for f in fragments if f['block_id'] in refs]
    compatible=[f for f in candidates if f['proof_level']!='VERIFIED' or f['field'] in {None,field}]
    # A strong structural role is still a candidate, but cannot be relabelled
    # by a provider merely because identical text occurs in its block.
    compatible=[f for f in compatible if role_hypothesis_supported(f,field,bundle)]
    matches=[f for f in compatible if wanted and wanted in comparable(f['raw_text'])]
    if proposal and proposal.get('fragment_refs'):
        named=proposal['fragment_refs'];index={f['fragment_id']:f for f in compatible}
        if not isinstance(named,list) or not named or any(i not in index for i in named):return None,'INVALID_FIELD_FRAGMENT_REF'
        selected=[index[i] for i in named]
        if len(selected)>1:
            units={f['definition_unit_id'] for f in selected}
            if None in units or len(units)!=1 or selected!=sorted(selected,key=lambda f:f['source_order']):return None,'UNPROVEN_FIELD_COMPOSITION'
        if comparable('\n'.join(f['raw_text'] for f in selected))!=wanted:return None,'UNGROUNDED'
        matches=selected
    if proposal and proposal.get('source_locations'):
        for location in proposal['source_locations']:
            if not any(f['block_id']==location.get('block_id') and f['dom_locator']==location.get('dom_locator')
                       and (location.get('cell_index') is None or f['cell_index']==location['cell_index'])
                       and (location.get('source_hash') is None or f['source_hash']==location['source_hash'])
                       and f['field'] in {None,field} and (f in matches or comparable(f['raw_text']) in wanted)
                       for f in fragments):
                return None,'FIELD_LOCATION_MISMATCH'
    if proposal and proposal.get('field_evidence') is not None and proposal['field_evidence']!=matches:
        return None,'FIELD_EVIDENCE_CONTEXT_MISMATCH'
    if not matches:return None,'FIELD_ROLE_MISMATCH' if candidates and not compatible else 'UNGROUNDED'
    return matches,'OK'
