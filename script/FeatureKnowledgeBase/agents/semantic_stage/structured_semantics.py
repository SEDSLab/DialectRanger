"""Semantic interpretation of observable, owner-local evidence (no retrieval).

Old inferred hints are reviewable, not a substitute for source structure.
All emitted values are literal block spans or explicitly associated cells.
"""
from __future__ import annotations
import re
from typing import Any, Mapping


def _norm(value):
    return ' '.join(str(value or '').replace('\u200b','').split()).strip()


def _ref(b): return str(b.get('evidence_ref') or b.get('block_id') or '')
def _kind(b): return str(b.get('block_kind') or '').upper()
def _context(b): return b.get('observed_context') or {}
def _labels(b):
    return [str(x.get('text') or '').strip().rstrip(':').casefold() for x in _context(b).get('labels',[])] + list(b.get('_verified_structure_labels',[]))


def _label_value(b, names):
    for label in _context(b).get('labels',[]):
        if str(label.get('text') or '').casefold().strip().rstrip(':') in names and label.get('origin')=='INLINE_LABEL':
            return str(label.get('value_text') or '').strip()
    return None


def _proof(b, identity):
    for p in b.get('definition_section_proofs') or []:
        names=[p.get('primary_identity'),*(p.get('identity_values') or [])]
        if any(_norm(n).casefold()==_norm(identity).casefold() for n in names):
            return True
    return False


def _code_surface(b):
    if _kind(b) in {'CODE','SIGNATURE'} or _context(b).get('raw_node_kind') in {'CODE','PRE'}:
        return True
    # H2, among other renderers, wraps example code in paragraph/inline CODE.
    # Require complete code coverage, not an incidental inline identifier.
    codes=[n.get('text','') for n in b.get('inline_nodes') or [] if n.get('element_name')=='code']
    return bool(codes) and _norm(' '.join(codes))==_norm(b.get('text'))


def _sql_shape(text):
    return bool(re.match(r'\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|VALUES|CALL|EXPLAIN|CREATE|ALTER)\b',text,re.I)
                or re.match(r'\s*[A-Za-z_][\w$]*\s*\(',text)
                or re.fullmatch(r"\s*'(?:[^']|'')*'\s*",text))


def _commented_example_shape(block):
    """Recognize actual source code after leading SQL comments, never comment text."""
    raw=_context(block).get('literal_code_text')
    if not isinstance(raw,str):return False
    rest=raw.lstrip()
    while rest.startswith(('--','/*')):
        match=re.match(r'--[^\r\n]*(?:\r?\n|$)|/\*.*?\*/',rest,re.S)
        if not match:return False
        rest=rest[match.end():].lstrip()
    return bool(rest) and _sql_shape(rest)


def _cli_example_shape(block,identity,feature_type='FUNCTION'):
    """Official interactive sessions: recognize commands, retain whole raw text.

    No executable SQL is generated. Arbitrary output/prompt-only/API transcripts
    cannot qualify without a SQL command using the current subject.
    """
    raw=_context(block).get('literal_code_text')
    if not isinstance(raw,str):return False
    commands=[]
    for line in raw.splitlines():
        match=re.match(r'^\s*[A-Za-z][\w.-]*>\s+(.+)$',line)
        continuation=re.match(r'^\s*(?:->|>)\s+(.*)$',line)
        if match:commands.append(match.group(1))
        elif continuation and commands:commands[-1]+=' '+continuation.group(1)
    for sql in commands:
        subject_sql=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|/\*.*?\*/|--.*",' ',sql)
        # A type constructor in CREATE TABLE is not a function invocation;
        # INSERT ... SET is not use of the SET datatype. Name occurrence alone
        # cannot establish the semantic subject of an interactive command.
        if feature_type=='FUNCTION' and re.match(r'\s*(?:SELECT|VALUES|CALL)\b',subject_sql,re.I):
            for call in re.finditer(r'(?<![\w$])'+re.escape(identity)+r'\s*\(',subject_sql,re.I):
                if not re.search(r'(?:\bAS\s*|::\s*)$',subject_sql[:call.start()],re.I):return True
        if (feature_type=='DATATYPE' and _proof(block,identity)
                and re.match(r'\s*(?:CREATE|ALTER)\s+TABLE\b',subject_sql,re.I)
                and re.search(r'(?:\(|,)\s*[A-Za-z_][\w$]*\s+'+re.escape(identity)+r'(?=\s*\(|\s*[,)]|\s+(?:NOT|NULL|DEFAULT|PRIMARY|UNIQUE)\b)',subject_sql,re.I)):
            return True
        if feature_type=='OPERATOR' and re.match(r'\s*(?:SELECT|VALUES|UPDATE|SET)\b',subject_sql,re.I):
            symbolic=bool(re.fullmatch(r'[!<>=~%&|^+*/:@?-]+',identity))
            edge=r'[!<>=~%&|^+*/:@?-]' if symbolic else r'[\w$]'
            if re.search(r'(?<!'+edge+r')'+re.escape(identity)+r'(?!'+edge+r')',subject_sql,re.I):return True
    return False


def _literal_example_expression(text):
    """Whole callable expressions in explicitly labelled prose, not mentions."""
    value=text.strip();position=0;found=False
    while position<len(value):
        match=re.match(r'\s*[A-Za-z_][\w$]*\s*\(',value[position:])
        if not match:return False
        position+=match.end();depth=1;quote=None
        while position<len(value) and depth:
            ch=value[position];position+=1
            if quote:
                if ch==quote:
                    if position<len(value) and value[position]==quote:position+=1
                    else:quote=None
            elif ch in "'\"":quote=ch
            elif ch=='(':depth+=1
            elif ch==')':depth-=1
        if depth or quote:return False
        found=True
        while position<len(value) and (value[position].isspace() or value[position]==';'):position+=1
    return found


def _expression_result_example(text, block, identity):
    """Official expression/result pairs, not invented executable statements."""
    if not _proof(block, identity) or '\u2192' not in text:
        return False
    expression, result = text.rsplit('\u2192', 1)
    if not _literal_example_expression(expression) or not re.match(
            r'^\s*'+re.escape(identity)+r'\s*\(', expression, re.I):
        return False
    return bool(re.fullmatch(r"\s*(?:'(?:[^']|'')*'|NULL|true|false|[-+]?\d+(?:\.\d+)?)\s*", result, re.I))


def _formal_anchor(b, identity):
    for p in b.get('definition_section_proofs') or []:
        if _ref(b) in (p.get('identity_anchor_block_ids') or {}).get(_norm(identity).upper(),[]):return True
        # A bounded declaration-grammar proof is stronger than a leading-name
        # regex. Brace alternatives and metavariable-prefixed declarations
        # must not be discarded solely for their spelling/layout.
        if 'LOCAL_DECLARATION_GRAMMAR' in (p.get('boundary_evidence') or []) and _ref(b) in (p.get('definition_anchor_block_ids') or []):return True
    return False


def _native_term_signatures(text, identity):
    """Literal subject spans in a shared DT; never publish another head's syntax."""
    values=[]
    for match in re.finditer(r'(?<![\w$])'+re.escape(identity)+r'\s*\(',text,re.I):
        position=match.end();depth=1
        while position<len(text) and depth:
            if text[position]=='(':depth+=1
            elif text[position]==')':depth-=1
            position+=1
        if not depth:values.append(text[match.start():position])
    return values


def _literal_sql_tokens(text, identity, approved_words):
    """Whole uppercase SQL token/expressional examples under a proven owner.

    This admits zero-argument calls and typed declaration examples, not prose
    merely containing an inline signature. Ordinary words, sentence periods,
    or an unrelated subject do not qualify.
    """
    value=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",' Q ',text)
    words=re.findall(r'[A-Za-z_][A-Za-z0-9_]*',value)
    return bool(words and all(w.upper()==w for w in words)
        and all(w in approved_words or len(w)==1 or w=='CALL' for w in words)
        and re.fullmatch(r'[A-Z0-9_\s(),\[\]{}+*/%<>=!|&^:;-]+',value)
        and any(w in approved_words for w in words))


def _declaration_shape(text, identity):
    """Generic postfix type constructors and their literal declaration lists."""
    name=re.escape(_norm(identity))
    prefix=r'[A-Z][A-Z0-9_]*(?:\([^()]*\))?'
    return bool(name and re.fullmatch(r'(?:\s*'+prefix+r'\s+'+name+r'(?:\s*\[[^\]]*\])?\s*)+',text))


def _syntax_shape(text, identity):
    # A leading exact name is not the same as a name somewhere in an example.
    name=re.escape(_norm(identity))
    return bool(name and re.match(r'^\s*'+name+r'(?:\s*\(|\s*\[|\s*\||\s*$)',text,re.I))


def _header(cell):
    return ' '.join(_norm(h.get('text')).casefold() for h in cell.get('headers') or [])


def _operator_head(expression):
    """Conservative expression prefix, not a new Identity or SQL inventory.

    Lowercase metavariables are operands, uppercase words are operator syntax.
    AND inside BETWEEN / NOT IN is not mistaken for an independent AND / IN.
    A failed comparison is retained as an upstream-subject diagnostic.
    """
    value=_norm(expression)
    value=re.sub(r'^[a-z][a-z0-9_]*(?:\s*\([^)]*\))?\s+', '', value)
    symbolic=re.match(r'^([!<>=~%&|^+*/-]+)(?:\s|$)',value)
    if symbolic:return symbolic.group(1)
    words=re.match(r'^([A-Z][A-Z0-9_]*(?:\s+[A-Z][A-Z0-9_]*)*)\b',value)
    return words.group(1) if words else None


def extract_structured_semantics(payload: Mapping[str, Any]) -> dict[str, Any]:
    identity=str(payload.get('normalized_identity') or '')
    feature_type=str(payload.get('feature_type') or 'UNCERTAIN').upper()
    result={'identity_group_id':payload.get('identity_group_id'),'description':None,
        'formal_syntax':[],'parameters':[],'return_type':None,'notes':[],
        'official_examples':[],'generated_examples':[]}
    extension={'version':'grounded-semantic-extension/v1','datatype_name':[], 'datatype_mapping':[],
        'uncertain_fields':[],'role_decisions':[],
        'input_completeness':{'evidence_completeness':payload.get('evidence_completeness',{}),
                              'input_selection':payload.get('input_selection',{})},
        'downstream_extension_consumption':'NOT_SUPPORTED_BY_CURRENT_CANONICAL_CONTRACT'}
    result['semantic_extensions']=extension
    seen={}; result_containers=set()
    evidence={}
    for b in payload.get('evidence') or []:
        if isinstance(b,Mapping) and _ref(b) and str(b.get('text') or '').strip():
            evidence.setdefault(_ref(b),{k:v for k,v in b.items() if k!='_verified_structure_labels'})
    from agents.owner.structure_execution import verified_field_labels
    for b in evidence.values():
        b['_verified_structure_labels']=verified_field_labels(b,payload.get('definition_units',[]),evidence,identity)
    blocks=sorted(evidence.values(),key=lambda b:(str(b.get('source_url') or ''),b.get('source_order',0)))
    declaration_words=set(re.findall(r'[A-Z][A-Z0-9_]*',_norm(identity).upper()))
    for candidate in blocks:
        if _kind(candidate)=='CODE' and _proof(candidate,identity) and not set(_labels(candidate)) & {'example','examples'}:
            if _formal_anchor(candidate,identity) or _syntax_shape(str(candidate.get('text') or ''),identity):
                declaration_words.update(re.findall(r'\b[A-Z][A-Z0-9_]*\b',str(candidate.get('text') or '')))

    def content_text(b):
        from agents.contracts.literal_source import verified_literal_text
        value=verified_literal_text(b)
        return value if value is not None else str(b.get('text') or '')

    def emit(field,b,value=None,cell=None):
        value=content_text(b) if value is None else str(value)
        if not value.strip():return
        key=(field,value)
        claim={'text':value,'evidence_refs':[_ref(b)],'source_url':b.get('source_url')}
        location={'block_id':_ref(b),'dom_locator':b.get('dom_path')}
        if cell is not None:
            location.update(cell_index=cell['cell_index'],dom_locator=cell.get('dom_locator'),
                            header_locators=[h.get('dom_locator') for h in cell.get('headers',[])])
        claim['source_locations']=[location]
        if key in seen:
            existing=seen[key]
            if _ref(b) not in existing['evidence_refs']:existing['evidence_refs'].append(_ref(b))
            if location not in existing['source_locations']:existing['source_locations'].append(location)
            return
        seen[key]=claim
        if field=='description':
            if result[field] is None:result[field]=[]
            result[field].append(claim)
        elif field=='return_type':
            if result[field] is None:result[field]=claim
            elif isinstance(result[field],list):result[field].append(claim)
            else:result[field]=[result[field],claim]
        elif field=='official_examples':
            claim['sql']=claim.pop('text');result[field].append(claim)
        elif field in {'datatype_name','datatype_mapping'}:extension[field].append(claim)
        else:result[field].append(claim)

    def decision(b,role,reason,cell=None):
        old=list(b.get('structural_roles') or [])
        extension['role_decisions'].append({'evidence_ref':_ref(b),'cell_index':cell.get('cell_index') if cell else None,
            'role':role,'reason':reason,'observed_labels':_labels(b),'legacy_inferred_roles':old,
            'source_locator':cell.get('dom_locator') if cell else b.get('dom_path')})

    def review(b,field,reason):
        extension['uncertain_fields'].append({'field':field,'status':'REVIEW_REQUIRED','reason':reason,
            'evidence_refs':[_ref(b)],'raw_text':b.get('text'),'source_url':b.get('source_url')})
        decision(b,'FIELD_REVIEW',reason)

    def catalog_examples(b,cells):
        """Only an already-verified subject row can supply labelled examples."""
        examples=[c for c in cells if c.get('header_role')=='DATA_CELL'
            and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}
            and re.fullmatch(r'examples?',_header(c)) and c.get('text','').strip()]
        results=[c for c in cells if c.get('header_role')=='DATA_CELL'
            and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}
            and re.fullmatch(r'(?:results?|outputs?)',_header(c)) and c.get('text','').strip()]
        if len(examples)!=1 or len(results)>1:return
        example=examples[0];value=example['text']
        if feature_type=='OPERATOR':
            expression=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",' ',value)
            edge=r'[!<>=~%&|^+*/:@?-]' if re.fullmatch(r'[!<>=~%&|^+*/:@?-]+',identity) else r'[\w$]'
            if not re.search(r'(?<!'+edge+r')'+re.escape(identity)+r'(?!'+edge+r')',expression,re.I):
                review(b,'official_examples','CATALOG_EXAMPLE_USES_OTHER_MEMBER');return
        if results:value+='\n'+results[0]['text']
        if _norm(value) not in _norm(b.get('text')):
            review(b,'official_examples','CATALOG_EXAMPLE_RESULT_NOT_CONTIGUOUS');return
        emit('official_examples',b,value,example)
        decision(b,'EXAMPLE_INVOCATION','VERIFIED_SUBJECT_EXAMPLE_COLUMN',example)
        extension.setdefault('catalog_example_cells',[]).append({'evidence_ref':_ref(b),
            'example_locator':example.get('dom_locator'),
            'result_locator':results[0].get('dom_locator') if results else None})

    from agents.semantic_stage.profile_fields import consume
    profile_controlled=consume(payload,evidence,identity,feature_type,emit,review,decision,extension)
    result['parameters'].extend(extension.pop('parameter_claims',[]))
    # Physical field witnesses are the common role contract. Optional legacy
    # interpretation below can enrich unknown surfaces, never veto these fields.
    from agents.semantic_stage.verified_fields import consume as consume_verified_fields
    consume_verified_fields(payload,evidence,emit,review,decision,extension)
    from agents.owner.member_collections import consume as consume_members
    profile_controlled.update(consume_members(payload,evidence,identity,emit,decision))

    # A parent definition and its nested field table are one source contract.
    # Re-read real cells before using a role, not just a copied role annotation.
    from agents.owner.composite_definition import verified_fields
    fields_by_ref = {}
    field_tables = set()
    field_compositions = []
    for unit in payload.get('definition_units', []):
        if (unit.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2':continue
        verified = verified_fields(unit, evidence, identity)
        for member in verified:
            fields_by_ref.setdefault(member['block_id'], member)
            field_tables.add((unit.get('source_url'), member['table_locator']))
        for index, member in enumerate(verified):
            if member['field_role'] != 'EXAMPLE_RESULT' or not index:
                continue
            invocation = verified[index - 1]
            if invocation['field_role'] != 'EXAMPLE' or invocation['table_locator'] != member['table_locator']:
                continue
            parent = next((b for b in blocks if b.get('dom_path') == member['table_locator']
                and b.get('source_url') == unit.get('source_url') and b.get('source_hash') == unit.get('source_hash')
                and _ref(b) in unit.get('member_evidence_refs', []) and _kind(b) == 'TABLE'
                and b.get('source_structure_status') == 'RESOLVED'), None)
            if parent is None:
                continue
            first, last = evidence[invocation['block_id']], evidence[member['block_id']]
            # Both fields were just revalidated against their physical cells.
            # IR row text remains normalized for matching; composing it here
            # would corrupt literal spaces even after the cells were fixed.
            first_text = invocation['field_label'] + ' ' + invocation['value']
            last_text = member['field_label'] + ' ' + member['value']
            value = first_text + '\n' + last_text
            if _norm(value) not in _norm(parent.get('text')):
                continue
            if feature_type == 'FUNCTION' and not re.search(r'(?<![\w$])' + re.escape(identity) + r'\s*\(', invocation['value'], re.I):
                continue
            emit('official_examples', parent, value)
            fields_by_ref[invocation['block_id']] = dict(invocation, composition_retained=True)
            fields_by_ref[member['block_id']] = dict(member, composition_retained=True)
            field_compositions.append({'field':'official_examples', 'value':value, 'identity':identity,
                'parent_evidence_ref':_ref(parent), 'definition_unit_id':unit['definition_unit_id'],
                'source_url':parent.get('source_url'), 'source_hash':parent.get('source_hash'),
                'parts':[{'evidence_ref':invocation['block_id'], 'text':first_text, 'role':'EXAMPLE'},
                         {'evidence_ref':member['block_id'], 'text':last_text, 'role':'EXAMPLE_RESULT'}],
                'proof':'REVALIDATED_SUBJECT_FIELD_TABLE_INVOCATION_RESULT'})

    # Compose only a source-revalidated invocation/result pair in one actual
    # compound cell. The row remains a grounding witness; the leaf refs retain
    # the fine-grained provenance. Never cross a cell, section or source page.
    grouped_paths=set()
    compositions=[]
    from agents.contracts.definition_entry import compound_entry
    for parent in blocks:
        if _ref(parent) in profile_controlled:continue
        if not _proof(parent,identity):continue
        entry=compound_entry(parent,identity,feature_type)
        if not entry:continue
        for f in entry['fragments']:
            if f['role']!='EXAMPLE' or not f.get('result_locator'):continue
            output=next((r for r in entry['fragments'] if r['dom_locator']==f['result_locator']
                         and r['role']=='EXAMPLE_RESULT' and r.get('invocation_locator')==f['dom_locator']),None)
            if not output:continue
            value=f['text']+'\n'+output['text']
            # Require literal continuity inside the retained parent evidence.
            if _norm(value) not in _norm(parent.get('text')):continue
            emit('official_examples',parent,value)
            claim=seen[('official_examples',value)]
            parts=[]
            for part in [f,output]:
                leaf=next((b for b in blocks if b.get('dom_path')==part['dom_locator']
                    and b.get('source_hash')==parent.get('source_hash')
                    and b.get('source_url')==parent.get('source_url') and b.get('text')==part['text']),None)
                if leaf:
                    # The public claim's witness must contain the whole value.
                    # Fine leaf refs belong to the composition record below;
                    # a sorted leaf ref must not become a false whole-span
                    # citation in the existing first-witness projection.
                    claim['source_locations'].append({'block_id':_ref(leaf),'dom_locator':part['dom_locator']})
                grouped_paths.add((parent.get('source_url'),parent.get('source_hash'),part['dom_locator']))
                parts.append({'evidence_ref':_ref(leaf) if leaf else _ref(parent),
                    'dom_locator':part['dom_locator'],'text':part['text'],'role':part['role']})
            compositions.append({'field':'official_examples','value':value,'identity':identity,
                'parent_evidence_ref':_ref(parent),'source_url':parent.get('source_url'),
                'source_hash':parent.get('source_hash'),'parts':parts,
                'proof':'REVALIDATED_SAME_CELL_EXPLICIT_INVOCATION_RESULT'})
    extension['content_compositions']=field_compositions + compositions

    for b in blocks:
        if _ref(b) in profile_controlled:continue
        text=str(b.get('text') or '');labels=set(_labels(b));kind=_kind(b)
        member = fields_by_ref.get(_ref(b))
        if member:
            role = member['field_role']
            target = {'DESCRIPTION':'description','FORMAL_SYNTAX':'formal_syntax','EXAMPLE':'official_examples'}.get(role)
            if member.get('composition_retained'):
                decision(b, role, 'VERIFIED_FIELD_TABLE_COMPOSITION');continue
            if target:
                if role != 'EXAMPLE' or feature_type != 'FUNCTION' or re.search(r'(?<![\w$])' + re.escape(identity) + r'\s*\(', member['value'], re.I):
                    emit(target, b, member['value'], (b.get('table_context') or {}).get('cells',[{},{}])[1])
                else:
                    review(b, 'official_examples', 'FIELD_TABLE_EXAMPLE_SUBJECT_UNPROVEN')
                decision(b, role, 'VERIFIED_SUBJECT_FIELD_CELL');continue
            decision(b, role, 'RETAINED_DEFINITION_FIELD_NOT_INFERRED_AS_FEATURE');continue
        if kind == 'TABLE' and (b.get('source_url'),b.get('dom_path')) in field_tables:
            decision(b,'FIELD_TABLE_CONTAINER','CHILD_FIELDS_ACCOUNTED_SEPARATELY');continue
        for proof in b.get('definition_section_proofs') or []:
            from agents.owner.composite_definition import verified as verified_composite
            declaration=verified_composite(proof,b,identity.upper())
            if declaration:
                if declaration['formal_value']:
                    emit('formal_syntax',b,declaration['formal_value'])
                    decision(b,'FORMAL_SYNTAX','VERIFIED_COMPOSITE_SUBJECT_DECLARATION')
                else:
                    decision(b,'CATALOG_LABEL','VERIFIED_SUBJECT_LABEL_NOT_FORMAL_SYNTAX')
                break
        else:
            declaration=None
        if declaration:continue
        if (b.get('source_url'),b.get('source_hash'),b.get('dom_path')) in grouped_paths:
            decision(b,'EXAMPLE_PART','SAME_SUBJECT_COMPOSITION_ALREADY_EMITTED');continue
        if b.get('compound_definition_entry'):
            from agents.contracts.definition_entry import compound_entry
            actual=compound_entry(b,identity,feature_type)
            for span in b['compound_definition_entry'].get('parent_span_fragments',[]):
                fragment=next((f for f in (actual or {}).get('fragments',[]) if f['dom_locator']==span['dom_locator']
                               and f['text']==span['text'] and f['role']==span['role']),None)
                if not fragment or text[span['span_start']:span['span_end']]!=fragment['text']:
                    decision(b,'UNKNOWN','COMPOUND_PARENT_SPAN_UNPROVEN');continue
                if (b.get('source_url'),b.get('source_hash'),fragment['dom_locator']) in grouped_paths:
                    continue
                field={'DESCRIPTION':'description','FORMAL_SYNTAX':'formal_syntax','EXAMPLE':'official_examples'}.get(fragment['role'])
                if field:
                    cell={'cell_index':next(c['cell_index'] for c in b['table_context']['cells']
                                           if c['dom_locator']==actual['cell_locator']),
                          'dom_locator':fragment['dom_locator'],'headers':[]}
                    emit(field,b,fragment['value'],cell)
                    decision(b,fragment['role'],'VERIFIED_COMPOUND_PARENT_SPAN',cell)
        member=b.get('definition_entry_member') or {}
        if member:
            # Annotation alone cannot grant subject ownership: revalidate the
            # child against its included, hash-verified source parent.
            from agents.contracts.definition_entry import compound_entry, identifier_key
            parent=evidence.get(member.get('parent_block_id'))
            entry=compound_entry(parent,identity,feature_type) if parent else None
            fragment=next((f for f in (entry or {}).get('fragments',[])
                           if f['dom_locator']==b.get('dom_path') and f['text']==text),None)
            aligned=bool(parent and b.get('source_hash') and b.get('source_hash')==parent.get('source_hash')
                         and b.get('source_url')==parent.get('source_url')
                         and (set(b.get('owner_ids') or []) | set(b.get('contributing_owner_ids') or []))
                             & set(parent.get('owner_ids') or []))
            if fragment and aligned and entry['subject']==identifier_key(identity):
                role=fragment['role']
                field={'DESCRIPTION':'description','FORMAL_SYNTAX':'formal_syntax','EXAMPLE':'official_examples'}.get(role)
                if field:emit(field,b,fragment['value'])
                returns={f['return_type'] for f in entry['fragments'] if f.get('return_type')}
                if fragment.get('return_type') and len(returns)==1:
                    emit('return_type',b,fragment['return_type'])
                elif fragment.get('return_type'):
                    extension['uncertain_fields'].append({'field':'return_type','status':'REVIEW_REQUIRED',
                        'reason':'OVERLOAD_DEPENDENT_RETURN_TYPE','raw_text':fragment['return_type'],
                        'evidence_refs':[_ref(b)]})
                decision(b,role,'VERIFIED_COMPOUND_DEFINITION_CHILD')
            else:decision(b,'UNKNOWN','COMPOUND_CHILD_SUBJECT_UNPROVEN')
            continue
        from agents.semantic_stage.applicability import foreign_type_reason
        conflict=foreign_type_reason(b,identity,feature_type)
        if conflict:
            review(b,'subject_applicability',conflict);continue
        if feature_type=='OPERATOR' and kind in {'TEXT','PARAGRAPH','PROSE','P'}:
            local_members=re.findall(r'(?:^|(?<=[.!?])\s+)(?:[Uu]nary|[Bb]inary)\s+([!<>=~%&|^+*/-]+)\s+(?:is|are|means|returns)\b',text)
            shared=any(len(p.get('identity_values',[]))>1 and _norm(identity).upper() in p.get('identity_values',[])
                       for p in b.get('definition_section_proofs',[]))
            if local_members and shared and _norm(identity) not in local_members:
                review(b,'description','EXPLICIT_OTHER_SHARED_MEMBER_PROPERTY');continue
        applicable=b.get('content_applicability') or {}
        if applicable.get('identity')==_norm(identity).upper() and applicable.get('feature_type')==feature_type:
            for span in applicable.get('spans',[]):
                if _norm(identity).upper() in span.get('members',[]) and span.get('text') in text:
                    emit('description',b,span['text'])
            decision(b,'DEFINITION_DESCRIPTION','EXPLICIT_LOCAL_SUBJECT_EXPLANATION');continue
        # A source-derived optional NOT member is not permission to attribute
        # the positive member's unqualified truth conditions to its opposite.
        positive=_norm(re.sub(r'\bNOT\b\s*','',identity)).upper()
        if (feature_type=='OPERATOR' and positive!=_norm(identity).upper()
                and kind in {'TEXT','PARAGRAPH','PROSE','P'}
                and len(re.findall(re.escape(identity),text,re.I))<2
                and any(p.get('primary_identity')==positive and _norm(identity).upper() in p.get('identity_values',[])
                        for p in b.get('definition_section_proofs',[]))
                and not _formal_anchor(b,identity)):
            review(b,'description','NEGATED_SHARED_MEMBER_APPLICABILITY_UNRESOLVED');continue
        if b.get('shared_definition_context'):
            decision(b,'SHARED_CONTEXT','NOT_A_SUBJECT_LOCAL_SEMANTIC_CLAIM');continue
        if any(p.get('definition_pattern')=='SOURCE_RELATION_DEFINITION'
               and p.get('source_role')=='CATALOG_MEMBER'
               and b.get('block_id')==p.get('heading_block_id') for p in b.get('definition_section_proofs',[])):
            decision(b,'CATALOG_LABEL','TYPED_MEMBER_NOT_FORMAL_SYNTAX');continue
        observed=_context(b);local=observed.get('local_definition') or {}
        if (observed.get('feature_domain')=='EMBEDDED_QUERY_LANGUAGE'
            or local.get('role')=='DEVELOPER_API_DECLARATION' or b.get('evidence_usage')=='DISCOVERY_ONLY'):
            decision(b,'UNKNOWN','NON_TARGET_LANGUAGE_OR_DISCOVERY_ONLY');continue
        if b.get('source_structure_status')=='RESOLVED':
            if local.get('declaration_origin')=='EXPLICIT_OPERATOR_SELF_DEFINITION':
                if (feature_type=='OPERATOR' and (any(_norm(m['identity'])==_norm(identity)
                                                    for m in local.get('members',[])) or _proof(b,identity))):
                    emit('description',b);decision(b,'DEFINITION_DESCRIPTION','EXPLICIT_OPERATOR_SELF_DEFINITION')
                else:decision(b,'UNKNOWN','DIFFERENT_DECLARATION_SUBJECT_OR_TYPE')
                continue
            if (local.get('role')=='OPERATOR_CATALOG' and not local.get('declaration_origin')
                    and not (kind=='TABLE_ROW' and b.get('table_context'))):
                # Catalog membership proves identity, not a full invocation.
                decision(b,'CATALOG_LABEL','OBSERVED_OPERATOR_MEMBER_NOT_FORMAL_SYNTAX');continue
            if local.get('role') in {'STORAGE_CLASS_DEFINITION'} and _norm(local.get('identity'))==_norm(identity):
                emit('description',b);decision(b,'DEFINITION_DESCRIPTION','OBSERVED_LOCAL_DATATYPE_DEFINITION');continue
        explicit_syntax=bool(labels & {'syntax','signature','grammar','form','forms','invocation'})
        explicit_example=bool(labels & {'example','examples','sample','samples','usage','usage example'})
        local_example=any(l.get('origin') in {'ADJACENT_EXAMPLE_INTRODUCTION',
            'CONTAINER_PREFIX_EXAMPLE_INTRODUCTION'} for l in observed.get('labels',[]))
        if (explicit_example and explicit_syntax and local_example and _proof(b,identity)
                and _code_surface(b) and (_sql_shape(text) or _commented_example_shape(b))):
            # Local concrete examples can occur inside a broader Syntax
            # section. A nearby introduction outranks the ancestor field
            # heading for this code only; other blocks retain their roles.
            explicit_syntax=False
        co_definition=next((p for p in b.get('definition_section_proofs') or []
            if p.get('definition_pattern')=='FUNCTION_DECLARATION_SECTION'
            and _norm(identity).upper() in p.get('identity_values',[])),None)
        if co_definition:
            own=(co_definition.get('subject_formal_values') or {}).get(_norm(identity).upper(),{})
            if (_ref(b) in co_definition.get('definition_anchor_block_ids',[])
                    and _ref(b)!=co_definition.get('heading_block_id') and _ref(b) not in own):
                decision(b,'IDENTITY_VARIANT','OTHER_CO_DEFINED_SUBJECT_SIGNATURE');continue
            others=[n for n in co_definition['identity_values'] if n!=_norm(identity).upper()]
            if explicit_example and others and not re.search(r'\b'+re.escape(identity)+r'\b',text,re.I) and any(
                    re.search(r'\b'+re.escape(n)+r'\b',text,re.I) for n in others):
                decision(b,'RELATED_EXAMPLE','OTHER_CO_DEFINED_SUBJECT_EXAMPLE');continue
        raw_path=str(b.get('dom_path') or '')
        if any(x.split(':')[0].split('#')[0] in {'nav','aside','footer','header'} for x in raw_path.split(' > ')):
            decision(b,'UNKNOWN','NAVIGATION_OR_CHROME');continue
        container=_context(b).get('container_locator') or '|'.join(b.get('contributing_owner_ids') or b.get('owner_ids') or [])
        container=(b.get('source_url'),container)
        if explicit_example:
            literal_code=observed.get('literal_code_text')
            if (isinstance(literal_code,str) and any(re.match(r'^\s*[A-Za-z][\w.-]*>\s+',line) for line in literal_code.splitlines())
                    and not _cli_example_shape(b,identity,feature_type)):
                decision(b,'RELATED_EXAMPLE','CLI_COMMAND_DOES_NOT_ESTABLISH_CURRENT_SUBJECT');continue
            # A grammar production can own a broad explanatory region. A new
            # adjacent intro there does not prove every later SQL example is
            # about this symbolic operator. Require an actual unquoted token;
            # don't mistake comments or string literals for its application.
            adjacent=any(l.get('origin')=='ADJACENT_EXAMPLE_INTRODUCTION' for l in observed.get('labels',[]))
            grammar_scope=any(p.get('definition_pattern')=='REFERENCE_DECLARATION_SECTION'
                and p.get('reference_mode')=='PARAGRAPH_SIGNATURE' for p in b.get('definition_section_proofs',[]))
            if adjacent and grammar_scope and feature_type=='OPERATOR' and re.fullmatch(r'[!<>=~%&|^+*/-]+',identity):
                raw=observed.get('literal_code_text') or text
                expression=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\r\n]*(?:\r?\n|$)|/\*.*?\*/",' ',raw,flags=re.S)
                if not re.search(r'(?<![!<>=~%&|^+*/-])'+re.escape(identity)+r'(?![!<>=~%&|^+*/-])',expression):
                    decision(b,'RELATED_EXAMPLE','GRAMMAR_CONTEXT_WITHOUT_SUBJECT_OPERATOR_APPLICATION');continue
            if re.fullmatch(r'\s*(?:Returns? the following|Results?|Outputs?)\s*:?\s*',text,re.I):
                result_containers.add(container);decision(b,'EXAMPLE_EXPLANATION','RESULT_INTRODUCTION_NOT_SQL');continue
            if kind in {'TABLE','TABLE_ROW'} or container in result_containers:
                decision(b,'EXAMPLE_RESULT','EXPLICIT_EXAMPLE_RESULT_SURFACE');continue
            if explicit_syntax:
                decision(b,'UNKNOWN','CONFLICTING_EXPLICIT_EXAMPLE_AND_SYNTAX');continue
            plain_statement=bool(re.match(r'^\s*(?:SELECT|WITH|CALL|INSERT|UPDATE|DELETE|VALUES)\b',text,re.I)
                                 and re.search(r'[();"\d]',text))
            literal_expression=_literal_example_expression(text) or _expression_result_example(text,b,identity)
            bare_declaration=feature_type=='DATATYPE' and _proof(b,identity) and _norm(text).casefold()==_norm(identity).casefold()
            type_declarations=feature_type=='DATATYPE' and _proof(b,identity) and _declaration_shape(text,identity)
            literal_tokens=_proof(b,identity) and _literal_sql_tokens(text,identity,declaration_words)
            owner_call_example = (_code_surface(b) and _proof(b,identity) and bool(identity)
                and bool(re.search(r'\b'+re.escape(identity)+r'\s*\(',text,re.I)))
            if (_code_surface(b) and (_sql_shape(text) or _commented_example_shape(b) or _cli_example_shape(b,identity,feature_type))) or owner_call_example or literal_expression or plain_statement or bare_declaration or type_declarations or literal_tokens:
                emit('official_examples',b);decision(b,'EXAMPLE_SQL','EXPLICIT_EXAMPLE_AND_CODE_SURFACE')
            else:decision(b,'EXAMPLE_EXPLANATION','NOT_AN_OBSERVABLE_SQL_CODE_SURFACE')
            continue
        if labels & {'function type','introduced in','added in','deprecated in','available since'} or re.match(r'^\s*(?:Function type|Introduced in|Added in|Deprecated in)\s*:',text,re.I):
            decision(b,'FEATURE_METADATA','VISIBLE_METADATA_LABEL');continue
        if kind=='HEADING' or re.fullmatch(r'\s*(?:Learn more|Read more|See also|Next|Previous)\s*',text,re.I):
            decision(b,'UNKNOWN','IDENTITY_OR_REFERENCE_LABEL_NOT_FIELD');continue

        table=b.get('table_context')
        if kind=='TABLE_ROW' and table:
            from agents.semantic_stage.profile_fields import named_parameter
            parameter=named_parameter(b)
            if parameter and _proof(b,identity) and b.get('owner_local_binding') is True:
                result['parameters'].append(parameter)
                decision(b,'PARAMETER','SOURCE_VERIFIED_PARAMETER_NAME_DESCRIPTION_COLUMNS');continue
            # Multi-member declarations need their actual member context,
            # not an inferred single-member interpretation of "respectively".
            # Revalidate the source row; a Type keyword or name mention alone
            # cannot authorize sharing another subject's description.
            from agents.owner.reference_definitions import verified_reference
            reference=next((p for p in b.get('definition_section_proofs',[])
                if p.get('reference_mode')=='CATALOG_ROW'
                and p.get('source_role')!='CATALOG_ENTRY'
                and p.get('feature_type')==feature_type
                and verified_reference(p,b,_norm(identity).upper())),None)
            if reference and feature_type in {'DATATYPE','OPERATOR'} and b.get('owner_local_binding') is True:
                descriptions=[c for c in table.get('cells') or []
                    if c.get('header_role')=='DATA_CELL' and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}
                    and re.fullmatch(r'(?:description|meaning|definition|explanation)',_header(c)) and c.get('text','').strip()]
                if descriptions:
                    catalog_examples(b,table.get('cells') or [])
                    members=reference['identity_values']
                    if len(members)>1:
                        emit('description',b)
                        decision(b,'SHARED_DEFINITION_DESCRIPTION','SOURCE_VERIFIED_MEMBER_GROUP_RETAINED_VERBATIM')
                        extension.setdefault('shared_description_contexts',[]).append({
                            'members':members,'applicability':'CO_DEFINED_GROUP_NOT_INDIVIDUAL_PROPERTY_ASSIGNMENT',
                            'evidence_refs':[_ref(b)]})
                    else:
                        for c in descriptions:emit('description',b,c['text'],c)
                        decision(b,'TABLE_DESCRIPTION','SOURCE_VERIFIED_REFERENCE_DESCRIPTION')
                    continue
            # Share the source-verified identity slot with Publication. A Name
            # column / row header proves subject membership, not formal syntax;
            # its Description is independently meaningful even without syntax.
            from agents.contracts.catalog_membership import catalog_membership
            membership = catalog_membership(b, _norm(identity).upper()) if (
                b.get('owner_local_binding') is True
                and b.get('identity_unit_id') in (None, payload.get('identity_group_id'))) else []
            membership = [p for p in membership if p['feature_type']==feature_type]
            if membership:
                catalog_examples(b,table.get('cells') or [])
                descriptions=[c for c in table.get('cells') or []
                    if c.get('header_role')=='DATA_CELL' and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}
                    and re.fullmatch(r'(?:description|meaning|definition)',_header(c))]
                for c in descriptions:
                    emit('description',b,c['text'],c)
                    decision(b,'TABLE_DESCRIPTION','VERIFIED_CATALOG_SUBJECT_DESCRIPTION',c)
                decision(b,'CATALOG_LABEL','VERIFIED_IDENTITY_NOT_FORMAL_SYNTAX')
                # Only an explicitly labelled syntax column can additionally
                # provide syntax. Never promote a bare catalog identity label.
                for c in table.get('cells') or []:
                    if (c.get('header_role')=='DATA_CELL' and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}
                        and re.fullmatch(r'(?:formal syntax|syntax|signature)',_header(c))
                        and _syntax_shape(c.get('text',''),identity)):
                        emit('formal_syntax',b,c['text'],c)
                        decision(b,'FORMAL_SYNTAX','EXPLICIT_CATALOG_SYNTAX_COLUMN',c)
                continue
            cells=[c for c in table.get('cells') or [] if c.get('header_role')=='DATA_CELL' and c.get('header_status') in {'EXPLICIT','OBSERVED_GRID'}]
            if not cells:
                decision(b,'UNKNOWN','NO_UNAMBIGUOUS_DATA_CELLS');continue
            if feature_type=='OPERATOR':
                expressions=[c for c in cells if re.fullmatch(r'(?:operator|expression|operator expression|syntax)',_header(c))]
                descriptions=[c for c in cells if re.fullmatch(r'(?:description|meaning|definition)',_header(c))]
                if len(expressions)==1 and _operator_head(expressions[0]['text'])==_norm(identity).upper():
                    if _norm(expressions[0]['text'])!=_norm(identity):
                        emit('formal_syntax',b,expressions[0]['text'],expressions[0]);decision(b,'OPERATOR_EXPRESSION','EXACT_LOCAL_OPERATOR_HEAD',expressions[0])
                    else:decision(b,'CATALOG_LABEL','BARE_OPERATOR_IDENTITY_NOT_FORMAL_SYNTAX',expressions[0])
                    catalog_examples(b,cells)
                    for c in descriptions:emit('description',b,c['text'],c);decision(b,'TABLE_DESCRIPTION','EXPLICIT_DESCRIPTION_COLUMN',c)
                else:
                    # A native operator declaration may live in a Name or
                    # Function column. Recheck the actual cells with the same
                    # generic declaration parser; chapter labels cannot supply
                    # its type, and the identity cell is not formal syntax.
                    from agents.owner.catalog_surfaces import catalog_surfaces
                    actual=catalog_surfaces([_header(c) for c in cells],
                        [c.get('text','') for c in cells],
                        ' '.join(b.get('heading_ancestry') or []))
                    declared=(b.get('source_structure_status')=='RESOLVED'
                        and b.get('owner_local_binding') is True
                        and local.get('role')=='DECLARATION_CATALOG'
                        and any(m.get('feature_type')=='OPERATOR'
                            and _norm(m.get('identity'))==_norm(identity) for m in actual))
                    if declared and descriptions:
                        catalog_examples(b,cells)
                        for c in descriptions:
                            emit('description',b,c['text'],c)
                            decision(b,'TABLE_DESCRIPTION','SOURCE_VERIFIED_DECLARATION_DESCRIPTION',c)
                        decision(b,'CATALOG_LABEL','DECLARATION_IDENTITY_NOT_FORMAL_SYNTAX')
                        continue
                    decision(b,'UNKNOWN','OPERATOR_SUBJECT_INCOMPATIBLE_OR_AMBIGUOUS')
                    extension['uncertain_fields'].append({'field':'operator_expression','status':'UNCERTAIN',
                        'reason':'Owner row present but complete expression does not establish this frozen Identity',
                        'evidence_refs':[_ref(b)]})
                continue
            if feature_type=='DATATYPE':
                names=[c for c in cells if re.fullmatch(r'(?:sql type|data ?type|type name|name|type)',_header(c))]
                matched=len(names)==1 and _norm(names[0]['text']).casefold()==_norm(identity).casefold()
                if not matched:
                    decision(b,'UNKNOWN','DATATYPE_NAME_CELL_SUBJECT_UNPROVEN');continue
                catalog_examples(b,cells)
                emit('datatype_name',b,names[0]['text'],names[0]);decision(b,'DATATYPE_NAME','EXACT_LOCAL_TYPE_NAME_CELL',names[0])
                for c in cells:
                    header=_header(c)
                    if re.search(r'\b(?:runtime|native|mapped|mapping|underlying|internal)\s+(?:data\s+)?type\b',header):
                        emit('datatype_mapping',b,c['text'],c);decision(b,'DATATYPE_MAPPING','EXPLICIT_MAPPING_COLUMN',c)
                    elif re.fullmatch(r'(?:notes?|remarks?|limitations?)',header):
                        emit('notes',b,c['text'],c);decision(b,'DATATYPE_NOTES','EXPLICIT_NOTES_COLUMN',c)
                    elif re.fullmatch(r'(?:description|definition)',header):
                        emit('description',b,c['text'],c);decision(b,'TABLE_DESCRIPTION','EXPLICIT_DESCRIPTION_COLUMN',c)
                    elif 'default' in header:
                        extension['uncertain_fields'].append({'field':'datatype_default','status':'UNCERTAIN',
                            'observed_value':c['text'],'evidence_refs':[_ref(b)],
                            'source_locations':[{'cell_index':c['cell_index'],'dom_locator':c.get('dom_locator')}],
                            'shared_context':table.get('shared_context',[]),
                            'reason':'Observed column value; no inference of runtime applicability or shared-note scope'})
                continue
            if feature_type=='FUNCTION':
                sigs=[c for c in cells if re.fullmatch(r'(?:function|function name|signature|syntax)',_header(c)) and _syntax_shape(c['text'],identity)]
                if len(sigs)==1:
                    catalog_examples(b,cells)
                    if (_header(sigs[0]) in {'function','function name'}
                            and re.fullmatch(re.escape(identity)+r'\s*\(\s*\)',sigs[0]['text'],re.I)):
                        decision(b,'CATALOG_LABEL','FUNCTION_NAME_STUB_NOT_FORMAL_SYNTAX',sigs[0]);continue
                    emit('formal_syntax',b,sigs[0]['text'],sigs[0]);decision(b,'FORMAL_SYNTAX','LOCAL_FUNCTION_SIGNATURE_COLUMN',sigs[0])
                    for c in cells:
                        if re.fullmatch(r'(?:description|definition)',_header(c)):
                            emit('description',b,c['text'],c);decision(b,'TABLE_DESCRIPTION','EXPLICIT_DESCRIPTION_COLUMN',c)
                    continue
            decision(b,'UNKNOWN','TABLE_ROLE_UNSUPPORTED_OR_SUBJECT_UNPROVEN');continue

        if explicit_syntax:
            value=_label_value(b,{'syntax','signature','grammar','form','forms','invocation'})
            if (value is None and not _code_surface(b) and not _formal_anchor(b,identity)
                    and not _syntax_shape(text,identity)
                    and not (feature_type=='DATATYPE' and _declaration_shape(text,identity))
                    and not (feature_type=='OPERATOR' and _operator_head(text)==_norm(identity))):
                # A heading labels a region, not the grammar role of every
                # descendant paragraph. Keep source-aligned explanatory prose.
                if _proof(b,identity) and kind in {'TEXT','PARAGRAPH','PROSE','P','DEFINITION','LIST_ITEM'}:
                    emit('description',b);decision(b,'DESCRIPTION','SYNTAX_REGION_EXPLANATORY_PROSE')
                else:decision(b,'UNKNOWN','SYNTAX_REGION_WITHOUT_FORMAL_DECLARATION')
                continue
            value=content_text(b) if value is None else value
            if value.strip() and not value.strip().rstrip(':').casefold() in {'syntax','grammar','signature'}:
                emit('formal_syntax',b,value);decision(b,'FORMAL_SYNTAX','EXPLICIT_VISIBLE_SYNTAX_LABEL')
            else:decision(b,'UNKNOWN','EMPTY_SYNTAX_LABEL')
            continue
        if labels & {'parameter','parameters','argument','arguments','operand','operands'}:
            match=re.match(r'^\s*(.+?)\s+(?:—|–|-|:)\s+(.+?)\s*$',text)
            if match:
                result['parameters'].append({'name':match.group(1),'description':match.group(2),'evidence_refs':[_ref(b)]})
                decision(b,'PARAMETER','EXPLICIT_PARAMETER_LABEL')
            else:decision(b,'UNKNOWN','PARAMETER_STRUCTURE_AMBIGUOUS')
            continue
        if labels & {'return type'}:
            value=_label_value(b,{'return type'})
            if value:emit('return_type',b,value);decision(b,'RETURN_TYPE','EXPLICIT_RETURN_TYPE_LABEL')
            else:decision(b,'UNKNOWN','RETURN_LABEL_WITHOUT_EXPLICIT_VALUE')
            continue
        if labels & {'note','notes','warning','warnings'}:
            if not re.fullmatch(r'\s*(?:Notes?|Warnings?)\s*:?\s*',text,re.I):emit('notes',b)
            decision(b,'NOTE','EXPLICIT_VISIBLE_NOTE_LABEL');continue
        proven=_proof(b,identity)
        from agents.owner.definition_patterns import verified_pattern
        structured=next((parsed for p in b.get('definition_section_proofs') or []
            for parsed in [verified_pattern(p,b,_norm(identity).upper())] if parsed),None)
        if structured:
            if structured.get('content_role')=='DESCRIPTION':
                emit('description',b)
                decision(b,'DESCRIPTION','SOURCE_VERIFIED_SELF_DEFINITION');continue
            value=structured['formal_value']
            if value:emit('formal_syntax',b,value)
            decision(b,'FORMAL_SYNTAX' if value else 'IDENTITY_VARIANT','SOURCE_VERIFIED_PROFILE_DEFINITION');continue
        list_proof = next((p for p in b.get('definition_section_proofs') or []
                           if p.get('definition_pattern') in {'SIGNATURE_LED_LIST_ITEM','OPERATOR_LED_LIST_ITEM','PARAGRAPH_LABEL_SIGNATURE_SECTION','PROFILE_LOCAL_DEFINITION'}
                           and _norm(p.get('primary_identity')).casefold() == _norm(identity).casefold()), None)
        if list_proof and b.get('block_id') == list_proof.get('label_block_id'):
            decision(b,'IDENTITY_VARIANT','PROVEN_DEFINITION_LABEL_NOT_DESCRIPTION');continue
        if list_proof and b.get('block_id') in list_proof.get('definition_anchor_block_ids', []):
            if list_proof.get('definition_pattern')=='OPERATOR_LED_LIST_ITEM':
                value=list_proof.get('operator_formal_expression')
                if value:emit('formal_syntax',b,value)
                decision(b,'FORMAL_SYNTAX' if value else 'IDENTITY_VARIANT','PROVEN_OPERATOR_DEFINITION_LABEL');continue
            values=(list_proof.get('formal_signature_values') or {}).get(b.get('block_id')) or _native_term_signatures(text, identity)
            for value in values: emit('formal_syntax', b, value)
            decision(b, 'FORMAL_SYNTAX' if values else 'UNKNOWN', 'PROVEN_LIST_DEFINITION_SIGNATURE');continue
        if list_proof and _code_surface(b) and re.match(
                r'^\s*(?:[\w.-]+>\s*)?(?:SELECT|WITH|CALL|INSERT|UPDATE|DELETE|VALUES)\b', text, re.I):
            emit('official_examples', b);decision(b, 'EXAMPLE_SQL', 'PROVEN_LIST_OWNER_SQL_EXAMPLE');continue
        local = _context(b).get('local_definition') or {}
        if b.get('source_structure_status') == 'RESOLVED':
            if local.get('role') == 'PAGE_FUNCTION_DEFINITION' and _norm(local.get('identity')).casefold() == _norm(identity).casefold():
                emit('description',b);decision(b,'DEFINITION_DESCRIPTION','OBSERVED_PAGE_SELF_DEFINITION');continue
            if local.get('role') == 'FUNCTION_DECLARATION_LIST' and re.match(r'^\s*'+re.escape(identity)+r'\s*\(',str(local.get('item_text') or ''),re.I):
                emit('formal_syntax',b);decision(b,'FORMAL_SYNTAX','OBSERVED_LOCAL_DECLARATION_LIST');continue
        if proven and _context(b).get('raw_node_kind') == 'DT' and _formal_anchor(b,identity):
            values=_native_term_signatures(text,identity)
            for value in values:emit('formal_syntax',b,value)
            decision(b,'FORMAL_SYNTAX' if values else 'UNKNOWN','PROVEN_NATIVE_DEFINITION_TERM_SUBJECT_SPANS');continue
        if any(_context(term).get('raw_node_kind') == 'DT' and _formal_anchor(term,identity)
               and term.get('source_url') == b.get('source_url')
               and raw_path.startswith(str(term.get('dom_path') or '')+' > ') for term in blocks):
            decision(b,'IDENTITY_VARIANT','NESTED_DEFINITION_TERM_ALREADY_REPRESENTED');continue
        if _context(b).get('raw_node_kind') == 'DD' and any(
                str(other.get('dom_path') or '').startswith(raw_path+' > ') for other in blocks if other is not b):
            decision(b,'UNKNOWN','DEFINITION_CONTAINER_HAS_LEAF_EVIDENCE');continue
        postfix_grammar=feature_type=='DATATYPE' and bool(re.match(r'^\s*[a-z][A-Za-z0-9_]*\s+'+re.escape(_norm(identity))+r'\b',text))
        if (proven and _code_surface(b) and b.get('owner_local_binding') is True
                and b.get('source_structure_status')=='RESOLVED'
                and _cli_example_shape(b,identity,feature_type)):
            emit('official_examples',b);decision(b,'EXAMPLE_SQL','PROVEN_OWNER_INTERACTIVE_SUBJECT_APPLICATION');continue
        if proven and _code_surface(b) and feature_type=='FUNCTION':
            signatures=_native_term_signatures(text,identity)
            if signatures and text.lstrip().startswith(signatures[0]) and re.match(r'\s*=(?!=|>)',text.lstrip()[len(signatures[0]):]):
                emit('description',b);decision(b,'DEFINITION_EXPRESSION','DEFINITION_EQUATION_NOT_INVOCATION_SYNTAX');continue
        if proven and _code_surface(b) and (_syntax_shape(text,identity) or _formal_anchor(b,identity) or postfix_grammar):
            emit('formal_syntax',b);decision(b,'FORMAL_SYNTAX','PROVEN_DEFINITION_GRAMMAR_CODE');continue
        label_only=bool(re.fullmatch(r'\s*(?:description|definition|meaning|syntax|examples?|parameters?|arguments?|returns?|notes?)\s*:\s*',text,re.I))
        if proven and kind in {'TEXT','PARAGRAPH','PROSE','P','LIST_ITEM','DEFINITION'} and not _code_surface(b) and not label_only:
            emit('description',b);decision(b,'DEFINITION_DESCRIPTION','PROVEN_OWNER_DEFINITION_PROSE');continue
        if labels & {'description','definition','meaning'} and kind not in {'CODE','HEADING','TABLE_ROW'}:
            if not label_only:emit('description',b)
            decision(b,'DEFINITION_DESCRIPTION','EXPLICIT_DESCRIPTION_LABEL');continue
        decision(b,'UNKNOWN','INSUFFICIENT_OBSERVABLE_ROLE_OR_SUBJECT_PROOF')
    from agents.semantic_stage.verified_fields import finalize
    finalize(result,extension)
    return result
