"""Source-grounded interpretation of Type votes, without discovering identities.

Only positive structural counterevidence removes a definition vote. Original
observations, IDs and strength remain auditable; no global score adjustment.
"""
from collections import defaultdict
import copy
import hashlib
import json
import re


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\u200b", "")).strip().casefold()


def _heads(text):
    prefix, sep, arguments = str(text).partition("(")
    if not sep or not re.fullmatch(r"[\w\s{}|$]+", prefix):
        return set(), ""
    return {_norm(s) for s in re.findall(r"[A-Za-z_][\w$]*", prefix)}, arguments


def _all_callable_heads(text):
    """Conservatively retain callable alternatives even after an earlier form.

    E.g. PRIMARY(x) | { PRIMARY | ALTERNATE } (x, y). Presence of another
    callable head is ambiguity, never evidence that it is an argument token.
    """
    output = set()
    for match in re.finditer(r'\(', str(text)):
        prefix = str(text)[:match.start()].rstrip()
        if not prefix:continue
        if prefix.endswith('}'):
            level = 0;start = None
            for i in range(len(prefix)-1, -1, -1):
                if prefix[i] == '}':level += 1
                elif prefix[i] == '{':
                    level -= 1
                    if level == 0:start = i;break
            group = prefix[start:] if start is not None else ''
            if group and re.fullmatch(r'[\w\s{}|$]+', group):
                output.update(_norm(s) for s in re.findall(r'[A-Za-z_][\w$]*', group))
        else:
            head = re.search(r'([A-Za-z_][\w$]*)$', prefix)
            if head:output.add(_norm(head.group(1)))
    return output


def _explicit_catalog_definition(block, subject, candidate_type):
    metadata = block.get('table_metadata') or {}
    headers = metadata.get('headers') or []
    cells = metadata.get('cells') or []
    for i, header in enumerate(headers):
        if i >= len(cells):continue
        value = str(cells[i])
        exact = _norm(value) == subject or _heads(value)[0] == {subject}
        if not exact:continue
        if candidate_type == 'FUNCTION' and re.fullmatch(r'(?:function|routine|signature)(?:\s+name)?', str(header).strip(), re.I):
            return True
        if candidate_type == 'DATATYPE' and re.fullmatch(r'(?:SQL\s+)?(?:data\s*)?type(?:\s+name)?', str(header).strip(), re.I):
            return True
    return False


def _callable_usage_counterproof(block, subject):
    """Exclude only positively identified non-definition occurrences.

    This does not require CODE/heading for a real function, or penalize all
    prose/supporting Owners. The original observation is retained for audit.
    """
    text = str(block.get('text') or '')
    occurrence = re.search(r'(?<![\w$])' + re.escape(subject) + r'\s*\([^)]*\)', text, re.I)
    if not occurrence:
        return None
    metadata = block.get('table_metadata') or {}
    cells = metadata.get('cells') or []
    reason = None
    if re.match(r'^\s*(?:\w+>\s*)?CREATE\s+(?:TEMPORARY\s+)?TABLE\b', text, re.I):
        reason = 'CALLABLE_IN_CREATE_TABLE_EXAMPLE'
    elif cells and _norm(cells[0]) != subject and not re.search(r'(?<![\w$])' + re.escape(subject) + r'\s*\(', str(cells[0]), re.I) and any(
        re.search(r'(?<![\w$])' + re.escape(subject) + r'\s*\(', str(cell), re.I) for cell in cells[1:]):
        reason = 'CALLABLE_IN_NON_IDENTITY_TABLE_CELL'
    elif re.search(r'\btype\s+of\s+(?:the\s+)?[^.;]{0,100}\b(?:column|field)\s+is\s*$', text[:occurrence.start()], re.I):
        reason = 'COLUMN_TYPE_USAGE_NOT_CALLABLE_DEFINITION'
    elif re.search(r'\bCHARACTER\s+SET\b', text, re.I) and re.match(
        r'^\s*[A-Za-z_]\w*\s*\([^)]*\)\s+CHARACTER\s+SET\b', text, re.I):
        reason = 'CHARACTER_SET_DECLARATION_REFERENCE'
    elif (str(block.get('block_kind') or '').upper() in {'CODE', 'PRE'}
          and re.fullmatch(r'\s*[A-Za-z_]\w*\s+' + re.escape(subject) + r'\s*\([\d\s,]+\)\s*;?\s*', text, re.I)
          and any(re.search(r'\b(?:data[ -]?types?|type syntax)\b', str(h), re.I)
                  for h in block.get('heading_ancestry') or [])):
        reason = 'COLUMN_DATATYPE_DECLARATION_EXAMPLE'
    elif (str(block.get('block_kind') or '').upper() == 'HEADING'
          and occurrence.start() > 0
          and re.search(r'\s+\([A-Z][A-Z0-9]{1,9}\)\s+(?:format|notation|representation)\b', text, re.I)
          and not re.search(r'\b(?:function|routine)\b', text, re.I)):
        reason = 'PROSE_ACRONYM_HEADING_NOT_CALLABLE'
    if reason:
        return {'rule': reason + '/v1', 'block_id': block.get('block_id'),
                'raw_text': text, 'matched_text': occurrence.group(0),
                'raw_cells': cells, 'heading_ancestry': block.get('heading_ancestry') or []}
    return None


def _operator_usage_counterproof(block, subject):
    # A result comparison referring to an operator is not its definition.
    # Do not veto headings, standalone expressions or definitional prose.
    text = str(block.get('text') or '')
    operator_head = re.match(r'^\s*The\s+([!<>=~%&|^+*/-]+)\s+operator\b',text,re.I)
    if (str(block.get('block_kind') or '').upper() == 'TEXT' and operator_head
        and re.fullmatch(r'[A-Za-z_][\w$]*',subject)
        and re.search(r'(?<![\w$])'+re.escape(subject)+r'\s*\(',text,re.I)):
        # The prose explicitly names a different symbolic subject. A callable
        # used to explain its equivalence is not itself that operator.
        return {'rule':'FOREIGN_SYMBOL_OPERATOR_CALLABLE_REFERENCE/v1',
                'block_id':block.get('block_id'),'raw_text':text,
                'operator_head':operator_head.group(1),'matched_subject':subject,
                'heading_ancestry':block.get('heading_ancestry') or []}
    if (str(block.get('block_kind') or '').upper() == 'TEXT'
        and re.match(r'^The\s+result\s+of\b', text, re.I)
        and re.search(r'\b(?:with|without)\s+(?:the\s+)?' + re.escape(subject)
                      + r'\s+(?:operator|predicate)\b', text, re.I)):
        return {'rule': 'RESULT_COMPARISON_OPERATOR_REFERENCE/v1', 'block_id': block.get('block_id'),
                'raw_text': text, 'heading_ancestry': block.get('heading_ancestry') or []}
    return None


def interpret_type_evidence(units, evidence, documents, owners=()):
    """Interpret frozen or freshly generated votes using identical source facts."""
    by_unit = {u['identity_unit_id']: u for u in units}
    by_owner = {o['owner_id']: o for o in owners}
    blocks = {}
    grammars = defaultdict(list)
    for page in documents:
        for block in page.get('blocks') or []:
            blocks[block['block_id']] = (page, block)
            if str(block.get('block_kind') or '').upper() in {'CODE', 'PRE', 'SYNTAX', 'SIGNATURE'}:
                heads, arguments = _heads(block.get('text'))
                if heads:
                    grammars[(page.get('page_id'), block.get('section_id'))].append((block, heads, arguments))
    result = []
    for source in evidence:
        row = copy.deepcopy(source)
        unit = by_unit.get(row.get('identity_unit_id'), {})
        subject = _norm(unit.get('canonical_identity') or row.get('subject'))
        page, block = blocks.get(row.get('block_id'), ({}, {}))
        metadata = block.get('table_metadata') or {}
        cells = metadata.get('cells') or []
        nonempty = [(i, str(c)) for i, c in enumerate(cells) if str(c).strip()]
        headings = block.get('heading_ancestry') or []
        nearest = _norm(headings[-1]) if headings else ''
        if block.get('block_kind')=='TABLE':
            row.update(evidence_role='USAGE_REFERENCE',definition_vote_eligible=False,
                       role_proof={'rule':'TABLE_CONTAINER_NOT_LOCAL_DECLARATION/v1',
                                   'block_id':block.get('block_id')})
            result.append(row)
            continue
        if re.search(r'\b(?:JSONPath|XPath|XQuery|item methods)\b',' / '.join(headings),re.I):
            row.update(evidence_role='USAGE_REFERENCE',definition_vote_eligible=False,
                       role_proof={'rule':'EMBEDDED_LANGUAGE_LOCAL_REGION/v1',
                                   'block_id':block.get('block_id'),'heading_ancestry':headings})
            result.append(row)
            continue
        # A revalidated configured declaration column is the local subject
        # slot. Usage examples in a different cell cannot demote that slot.
        from agents.owner.definition_patterns import verified_pattern
        configured=next((p for p in by_owner.get(row.get('owner_id'),{}).get('definition_section_proofs',[])
            if (p.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2'
            and block.get('block_id') in p.get('definition_anchor_block_ids',[])
            and (verified_pattern(p,block,str(unit.get('canonical_identity') or row.get('subject'))) or {}).get('feature_type')==row.get('candidate_type')),None)
        if (configured and row.get('candidate_type')=='FUNCTION'
                and 'SOURCE_VERIFIED_PROFILE_DEFINITION' in row.get('reason_codes',[])):
            row.update(evidence_role='DEFINITION_SUPPORT',definition_vote_eligible=True,
                       role_proof={'rule':'REVALIDATED_PROFILE_IDENTITY_LOCATION/v1',
                                   'block_id':block['block_id'],'section_id':configured['section_id']})
            result.append(row)
            continue
        usage_proof = (_callable_usage_counterproof(block, subject)
                       if row.get('candidate_type') == 'FUNCTION'
                       and row.get('evidence_kind') == 'CALLABLE_SIGNATURE' else None)
        if row.get('candidate_type')=='DATATYPE':
            from agents.contracts.type_slots import datatype_usage_slot
            usage_proof=datatype_usage_slot(block,subject)
        # A casting/comparison matrix relates already named types. Its row
        # labels do not declare operators, even when the chapter says Operations.
        if (re.search(r'\bmatrix\b',' / '.join(headings),re.I) and len(cells)>=3
                and all(re.fullmatch(r'[A-Z?\u2713\u00d7-]{0,3}',str(c).strip()) for c in cells[1:])):
            usage_proof={'rule':'RELATION_MATRIX_NOT_FEATURE_DECLARATION/v1',
                         'block_id':block['block_id'],'raw_cells':cells,'heading_ancestry':headings}
        # A row schema inside a table-function reference defines output columns,
        # not a new no-argument callable bearing each column's name.
        headers=metadata.get('headers') or []
        if (row.get('candidate_type')=='FUNCTION' and cells and headers
                and re.fullmatch(r'(?:column\s+)?name',str(headers[0]).strip(),re.I)
                and any(re.fullmatch(r'(?:data\s*)?type',str(h).strip(),re.I) for h in headers[1:])
                and not re.search(r'\(',str(cells[0]))):
            usage_proof={'rule':'OUTPUT_SCHEMA_COLUMN_NOT_CALLABLE/v1',
                         'block_id':block['block_id'],'raw_cells':cells,'headers':headers}
        if row.get('candidate_type') == 'OPERATOR' and row.get('evidence_kind') == 'OPERATOR_SLOT':
            usage_proof = usage_proof or _operator_usage_counterproof(block, subject)
            if (not usage_proof and str(block.get('block_kind') or '').upper()=='TEXT'
                and re.fullmatch(r'[A-Za-z_][\w$]*',subject)
                and not re.search(r'\b'+re.escape(subject)+r'\s+(?:operator|predicate)\b',
                                  str(block.get('text') or ''),re.I)):
                # A source-verified function head defines the subject. Its body
                # may cite a chapter titled "Functions and Operators"; that is
                # not an independent operator declaration for this function.
                from agents.owner.definition_patterns import verified_pattern
                owner=by_owner.get(row.get('owner_id'),{})
                for proof in owner.get('definition_section_proofs',[]):
                    anchor=blocks.get(proof.get('heading_block_id'),({},{}))[1]
                    if (proof.get('feature_type')=='FUNCTION'
                        and block.get('block_id') in proof.get('block_ids',[])
                        and block.get('block_id') not in proof.get('definition_anchor_block_ids',[])
                        and verified_pattern(proof,anchor,str(unit.get('canonical_identity') or row.get('subject')))):
                        usage_proof={'rule':'VERIFIED_FUNCTION_BODY_NOT_OPERATOR_DECLARATION/v1',
                            'block_id':block['block_id'],'raw_text':block.get('text'),
                            'definition_section_id':proof['section_id'],
                            'definition_anchor_block_id':proof['heading_block_id'],
                            'heading_ancestry':block.get('heading_ancestry') or []}
                        break
        if usage_proof:
            row['evidence_role'] = 'USAGE_REFERENCE'
            row['definition_vote_eligible'] = False
            row['role_proof'] = dict(usage_proof, page_id=page.get('page_id'), source_url=page.get('url'))
            result.append(row)
            continue
        # Do not mistake the middle node of a railroad/grammar table for its
        # feature-name column after empty-cell filtering. A sibling formal
        # grammar must independently prove the token's argument relationship.
        possible_placeholder = (
            row.get('candidate_type') == 'FUNCTION' and row.get('evidence_kind') == 'FUNCTION_SLOT'
            and not row.get('signature') and str(block.get('block_kind') or '').upper() == 'TABLE_ROW'
            and bool(block.get('section_id')) and bool(page.get('page_id'))
            and not metadata.get('headers') and len(cells) >= 3 and len(nonempty) == 1
            and 0 < nonempty[0][0] < len(cells) - 1 and _norm(nonempty[0][1]) == subject
            and nearest and nearest != subject and re.search(r'\bfunctions?\b', ' '.join(headings), re.I)
        )
        proof = []
        if possible_placeholder:
            for grammar, heads, arguments in grammars.get((page.get('page_id'), block.get('section_id')), []):
                if (nearest in heads and subject not in _all_callable_heads(grammar.get('text', ''))
                        and int(grammar.get('order') or 0) < int(block.get('order') or 0)
                        and re.search(r'(?<![\w$])' + re.escape(subject) + r'(?![\w$])', _norm(arguments))):
                    proof.append({'grammar_block_id': grammar['block_id'], 'grammar_text': grammar.get('text'),
                                  'declared_heads': sorted(heads), 'source_url': page.get('url')})
        if proof:
            row['evidence_role'] = 'GRAMMAR_PLACEHOLDER'
            row['definition_vote_eligible'] = False
            row['role_proof'] = {'rule': 'PADDED_ARGUMENT_NODE_IN_FOREIGN_CALLABLE_GRAMMAR/v1',
                'block_id': block['block_id'], 'page_id': page.get('page_id'), 'section_id': block.get('section_id'),
                'raw_cells': list(cells), 'cell_index': nonempty[0][0], 'defining_heading': headings[-1],
                'grammar_definitions': proof}
        elif row.get('subject_alignment') in {'PARAMETER_SLOT', 'EXAMPLE_SLOT'}:
            row['evidence_role'] = 'EXAMPLE_OCCURRENCE' if row['subject_alignment'] == 'EXAMPLE_SLOT' else 'USAGE_REFERENCE'
            row['definition_vote_eligible'] = False
        else:
            # Positive annotations do not increase the old vote's weight.
            kind = row.get('candidate_type')
            own_head = subject and nearest == subject
            text_head = _norm(block.get('text')) == subject
            local = ' '.join(headings[:-1] if own_head else headings)
            category = ('DATATYPE' if re.search(r'\bdata[ -]?types?\b', local, re.I) else
                        'FUNCTION' if re.search(r'\bfunctions?\b', local, re.I) else None)
            explicit_function = bool(row.get('callable_definition_span'))
            if ((kind == category and own_head and (text_head or row.get('subject_alignment') == 'EXACT_HEAD'))
                    or (kind == 'FUNCTION' and explicit_function)
                    or _explicit_catalog_definition(block, subject, kind)):
                row['evidence_role'] = 'DEFINITION_SUPPORT'
                row['definition_vote_eligible'] = True
                row['role_proof'] = {'rule': 'EXISTING_EXACT_DEFINITION_ANCHOR/v1', 'block_id': row.get('block_id'),
                                     'heading_ancestry': headings, 'callable_definition_span': row.get('callable_definition_span')}
        result.append(row)
    return result


def revise_type_decisions(units, decisions, interpreted_evidence):
    """Offline Type-only replay; unchanged units/decisions retain exact content."""
    from agents.type.decision import decide_type
    from agents.type.adaptation import adapted_decide_type
    by_unit = {u['identity_unit_id']: u for u in units}
    evidence_by_unit = defaultdict(list)
    for row in interpreted_evidence:
        evidence_by_unit[row['identity_unit_id']].append(row)
    output = []
    for original in decisions:
        uid = original['identity_unit_id'];votes = evidence_by_unit[uid]
        excluded = [r for r in votes if r.get('definition_vote_eligible') is False and r.get('candidate_type') in {'FUNCTION','OPERATOR','DATATYPE'}]
        defining_types = {r['candidate_type'] for r in votes if r.get('evidence_role') == 'DEFINITION_SUPPORT' and r.get('strength') == 'STRONG'}
        if not excluded and len(defining_types) <= 1:
            output.append(copy.deepcopy(original));continue
        changed = copy.deepcopy(original)
        decide = adapted_decide_type if any(r.get('type_adaptation') for r in votes) else decide_type
        result = decide(by_unit[uid], votes)
        primary = result['primary_type'] if result['primary_type'] in {'FUNCTION','OPERATOR','DATATYPE','UNCERTAIN'} else 'UNCERTAIN'
        for key in ['type_status','confidence','reason_codes','alternate_types','supporting_evidence_ids','conflicting_evidence_ids','evidence_summary']:
            if key in result:changed[key] = result[key]
        changed.update(primary_type=primary, status=result['type_status'], conflict_flags=list(result['reason_codes']))
        changed['type_role_resolution'] = {
            'contract': 'DefinitionVsUsageTypeEvidence/v1', 'mechanism': 'RECOMPUTED_TYPE_NOT_TARGET_PROMOTION',
            'previous_decision_sha256': hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest(),
            'excluded_definition_vote_ids': [r['evidence_id'] for r in excluded],
            'definition_support_ids': [r['evidence_id'] for r in votes if r.get('evidence_role') == 'DEFINITION_SUPPORT'],
            'role_proofs': [r.get('role_proof') for r in excluded],
        }
        output.append(changed)
    return output
