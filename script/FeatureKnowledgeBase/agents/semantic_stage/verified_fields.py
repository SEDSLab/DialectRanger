"""Consume reconstructed physical witnesses before optional role inference.

This module does not recognize table layouts. FieldEvidence owns role/location
resolution; consumers check lineage, subject and literal continuity instead of
requiring the same header spelling a second time.
"""
import re

from agents.contracts.field_evidence import build_field_evidence, comparable, field_subject_bound
from agents.contracts.identity_key import normalize_identity



def consume(payload, evidence, emit, review, decision, extension):
    """Return per-fragment dispositions; supplied annotations cannot grant roles."""
    view = dict(payload, source_blocks=list(evidence.values()))
    fragments = build_field_evidence(view)
    supplied = payload.get('field_evidence')
    identity = payload.get('normalized_identity', '')
    audit = []
    role_names = {'description': 'DESCRIPTION', 'formal_syntax': 'FORMAL_SYNTAX',
                  'official_examples': 'EXAMPLE', 'return_type': 'RETURN_TYPE', 'notes': 'NOTE'}
    def signature(f):
        return tuple(str(f.get(k)) for k in ('block_id', 'field', 'raw_text', 'cell_index', 'dom_locator', 'source_url', 'source_hash'))
    supplied_keys = {signature(f) for f in supplied or []}
    for fragment in fragments:
        if fragment.get('proof_level') != 'VERIFIED' or not fragment.get('field'):
            continue
        block = evidence[fragment['block_id']]
        field = fragment['field']
        value = fragment['raw_text']
        reason = None
        # Profile and composite witnesses have role-specific schemas (shared
        # members, invocation/result composition, named parameters). Their
        # existing verified executors own that qualification; a physical slot
        # alone must not bypass it or re-emit individual composition parts.
        if fragment.get('basis') not in {'OBSERVED_CELL_HEADER','OBSERVED_DECLARATION_RETURN_COLUMN','EXAMPLE_RESULT_PAIR'}:
            audit.append(dict(fragment,status='ROLE_EXECUTOR',reason='VERIFIED_ROLE_EXECUTOR_REQUIRED'))
            continue
        if supplied is not None and signature(fragment) not in supplied_keys:
            reason = 'FIELD_WITNESS_TRANSPORT_MISMATCH'
        elif not field_subject_bound(block, payload):
            reason = 'FIELD_SUBJECT_SCOPE_UNPROVEN'
        elif comparable(value) not in comparable(block.get('text')):
            reason = 'FIELD_LITERAL_CONTINUITY_UNPROVEN'
        elif field == 'official_examples':
            siblings=[f for f in fragments if f.get('basis')=='EXAMPLE_RESULT_PAIR'
                      and f['block_id']==fragment['block_id'] and comparable(value) in comparable(f['raw_text'])]
            if siblings and fragment.get('basis')!='EXAMPLE_RESULT_PAIR':
                audit.append(dict(fragment,status='COMPOSED',reason='SAME_ROW_INVOCATION_RESULT_PAIR'))
                continue
            kind=str(payload.get('feature_type','')).upper()
            if kind=='OPERATOR':
                expression=re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",' ',value)
                edge=r'[!<>=~%&|^+*/:@?-]' if re.fullmatch(r'[!<>=~%&|^+*/:@?-]+',identity) else r'[\w$]'
                if not re.search(r'(?<!'+edge+r')'+re.escape(identity)+r'(?!'+edge+r')',expression,re.I):
                    reason='FIELD_EXAMPLE_OTHER_MEMBER'
            # Ordinary multi-member examples must actually use this member.
            if str(payload.get('feature_type', '')).upper() == 'FUNCTION':
                members = next((u.get('identity_values', []) for u in payload.get('definition_units', [])
                                if u.get('definition_unit_id') == fragment.get('definition_unit_id')), [])
                if len(members) > 1 and not re.search(r'(?<![\w$])' + re.escape(identity) + r'\s*\(', value, re.I):
                    reason = 'FIELD_EXAMPLE_OTHER_MEMBER'
        if field not in role_names:
            # Parameters are structured name/description objects; never turn
            # an arbitrary cell into a fabricated parameter schema.
            reason = reason or 'FIELD_REQUIRES_STRUCTURED_SCHEMA'
        status = 'REVIEW' if reason else 'CONSUMED'
        audit.append(dict(fragment, status=status, reason=reason))
        if reason:
            review(block, field, reason)
            continue
        cell = next((c for c in (block.get('table_context') or {}).get('cells', [])
                     if c.get('cell_index') == fragment.get('cell_index')), None)
        emit(field, block, value, cell)
        decision(block, role_names[field], 'VERIFIED_FIELD_WITNESS_CONSUMED', cell)
    extension['verified_field_consumption'] = audit
    return audit


def finalize(result, extension):
    """Resolve specialized witnesses against actual emitted claims, not intent."""
    for row in extension.get('verified_field_consumption', []):
        if row['status'] != 'ROLE_EXECUTOR':
            continue
        values=result.get(row['field']) or []
        values=values if isinstance(values,list) else [values]
        texts=[str(v.get('text') or v.get('sql') or '') for v in values if isinstance(v,dict)]
        wanted=comparable(row['raw_text'])
        if wanted and any(wanted in comparable(value) for value in texts):
            row.update(status='CONSUMED',reason='ROLE_EXECUTOR_EMITTED_SOURCE_FIELD')
            continue
        checks=[c for c in extension.get('profile_field_consumption',[])
                if c.get('block_id')==row['block_id'] and c.get('reason')]
        decisions=[c for c in extension.get('role_decisions',[])
                   if c.get('evidence_ref')==row['block_id'] and c.get('reason')]
        reasons=sorted({c['reason'] for c in checks or decisions})
        row.update(status='REVIEW',reason=';'.join(reasons) if reasons else 'FIELD_ROLE_EXECUTOR_DID_NOT_EMIT')
