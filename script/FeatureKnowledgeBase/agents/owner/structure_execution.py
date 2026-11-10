"""Five composable structural executors on the existing source verifiers.

This is the production dispatcher, not a second Owner/Identity pipeline. Profile
selects finite boundary/slot recipes and field labels; it cannot supply names,
types, selectors or publication permission. Raw blocks are never rewritten.
"""
import re
from collections import Counter

from agents.contracts.base import stable_id


RECIPES = {
    'A': ('PAGE_SUBJECT', 'SUBJECT_PAGE'),
    'B': ('HEADING', 'NEXT_PEER_OR_PARENT_HEADING'),
    'C': ('LEADING_TERM', 'CURRENT_ENTRY'),
    'D': ('VERIFIED_IDENTITY_CELL', 'CURRENT_ROW'),
    'E': ('TYPED_MEMBER', 'SHARED_PARENT'),
}
RELATIONS = {
    'A': {'HEADING_SECTION', 'SELF_DEFINITION', 'PAGE_SUBJECT_RELATION'},
    'B': {'HEADING_SECTION'},
    'C': {'SIGNATURE_CONTAINER', 'TERM_BODY_PAIR', 'PARAGRAPH_SIGNATURE', 'SELF_DEFINITION'},
    'D': {'CATALOG_ROW'},
    'E': {'STORAGE_CLASS', 'CATALOG_MEMBER_CONTEXT','GRAMMAR_MEMBER_CONTEXT'},
}
FIELD_LABELS = {
    'DESCRIPTION': ['description', 'purpose'],
    'FORMAL_SYNTAX': ['syntax', 'signature', 'grammar'],
    'EXAMPLE': ['example', 'examples', 'usage example'],
    'PARAMETER': ['arguments', 'parameters'],
    'RETURN_TYPE': ['return type', 'return types', 'returned value', 'returns'],
}


def default_structure_patterns():
    return [{'structure': k, 'identity_slot': v[0], 'boundary': v[1]}
            for k, v in RECIPES.items()]


def compile_structure_plan(profile=None):
    hints = (profile or {}).get('hint_contract') or {}
    raw = hints.get('definition_structure_patterns', default_structure_patterns())
    if not isinstance(raw, list):
        raise ValueError('definition_structure_patterns must be a list')
    seen = set()
    families = {}
    plans = []
    if len(raw)>20:raise ValueError('at most twenty structural execution plans')
    for value in raw:
        if not isinstance(value, dict) or set(value) - {
                'structure', 'identity_slot', 'boundary', 'field_labels','execution','plan_id'}:
            raise ValueError('unsupported structural executor fields')
        key = value.get('structure')
        identifier=value.get('plan_id')
        if identifier is not None and (not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}',identifier)):
            raise ValueError('plan_id must be a bounded identifier')
        if key not in RECIPES or (identifier or key) in seen:
            raise ValueError('unknown or duplicate structural executor')
        if key in families and (not identifier or not value.get('execution') or
                any(not p.get('plan_id') or not p.get('execution') for p in families[key])):
            raise ValueError('multiple plans for one structure require explicit plan_id and execution')
        if (value.get('identity_slot'), value.get('boundary')) != RECIPES[key]:
            raise ValueError('unsupported structural executor slot/boundary')
        from agents.owner.profile_execution import compile_execution
        compile_execution(value)
        labels = value.get('field_labels', {})
        if not isinstance(labels, dict) or set(labels) - set(FIELD_LABELS):
            raise ValueError('unsupported structural field role')
        used = set()
        for role, names in labels.items():
            if (not isinstance(names, list) or not names
                    or any(not isinstance(n, str) or not re.fullmatch(
                        r'[A-Za-z][A-Za-z /_-]{0,79}', n) for n in names)):
                raise ValueError('field labels must be finite literal heading names')
            normalized = [n.strip().casefold() for n in names]
            if len(set(normalized)) != len(normalized) or used.intersection(normalized):
                raise ValueError('ambiguous structural field labels')
            used.update(normalized)
        merged={role:labels.get(role,defaults) for role,defaults in FIELD_LABELS.items()}
        role_by_label={}
        for role,names in merged.items():
            for name in names:
                label=name.strip().casefold()
                if label in role_by_label and role_by_label[label]!=role:
                    raise ValueError('ambiguous structural field labels')
                role_by_label[label]=role
        seen.add(identifier or key)
        families.setdefault(key,[]).append(value)
        plans.append({**value, 'field_labels': {k: list(v) for k, v in labels.items()}})
    return plans


def enabled_structures(profile=None):
    return {p['structure'] for p in compile_structure_plan(profile)}


def enabled_relations(profile=None):
    return set().union(*(RELATIONS[k] for k in enabled_structures(profile)))


def composed_definition(proof):
    """Shared capability check; consumers do not need a new pattern-name list."""
    execution = proof.get('structure_execution') or {}
    return (execution.get('status') == 'VERIFIED_STRUCTURE'
            and (bool(execution.get('field_members')) or execution.get('schema_version')=='StructureExecution/v2')
            and set(proof.get('identity_values') or []) ==
                {d['identity'] for d in execution.get('declarations', [])})


def _labels(plan):
    # Defaults are shared field vocabulary, never SQL feature names.
    return {k: list(plan.get('field_labels', {}).get(k, v)) for k, v in FIELD_LABELS.items()}


def verified_field_labels(block, source_units, evidence, identity):
    """Consume observed Profile labels only with the same bound source proof.

    A copied annotation or a matching word on another page is insufficient.
    The legacy reader still owns default labels; explicit custom labels are
    normalized here after checking their actual included heading occurrence.
    """
    canonical={'DESCRIPTION':'description', 'FORMAL_SYNTAX':'syntax',
               'EXAMPLE':'examples', 'PARAMETER':'parameters', 'RETURN_TYPE':'return type'}
    result=set()
    bid=block.get('block_id') or block.get('evidence_ref')
    for unit in source_units:
        execution=unit.get('structure_execution') or {}
        plan=execution.get('plan') or {}
        if (not plan.get('field_labels') or execution.get('status')!='VERIFIED_STRUCTURE'
                or unit.get('source_hash')!=block.get('source_hash')
                or unit.get('source_url')!=block.get('source_url')
                or bid not in unit.get('member_evidence_refs',[])
                or len(execution.get('declarations',[]))!=1
                or execution['declarations'][0]['identity'].casefold()!=identity.casefold()):
            continue
        for member in execution.get('members',[]):
            role=member.get('field_role')
            if member.get('block_id')!=bid or role not in canonical:
                continue
            label=evidence.get(member.get('role_label_block_id'),{})
            if (label.get('block_kind')!='HEADING'
                    or label.get('source_hash')!=block.get('source_hash')
                    or label.get('source_url')!=block.get('source_url')
                    or member.get('role_label_block_id') not in unit.get('member_evidence_refs',[])):
                continue
            from agents.owner.definition_scope import heading_field_label
            wanted={s.casefold() for s in _labels(plan)[role]}
            text=heading_field_label(label.get('text'),identity,wanted)
            if text in wanted:
                result.add(canonical[role])
    return sorted(result)


def field_labels(profile=None):
    return {s.strip().casefold() for p in compile_structure_plan(profile)
            for values in _labels(p).values() for s in values}


def structure_family(proof, document):
    if proof.get('configured_structure') in RECIPES:
        return proof['configured_structure']
    relation = (proof.get('scope_execution_plan') or {}).get('relation')
    if relation in {'CATALOG_ROW'} or proof.get('reference_mode') in {'CATALOG_ROW', 'COMPOUND_CATALOG_ROW','PROFILE_EXECUTED_SLOT'}:
        return 'D'
    if relation in {'STORAGE_CLASS', 'CATALOG_MEMBER_CONTEXT','GRAMMAR_MEMBER_CONTEXT'} or proof.get('source_role') == 'CATALOG_MEMBER':
        return 'E'
    if relation == 'PAGE_SUBJECT_RELATION':
        return 'A'
    blocks = {b['block_id']: b for b in document.get('blocks', [])}
    anchor = blocks.get(proof.get('heading_block_id'), {})
    if anchor.get('block_kind') == 'HEADING':
        from agents.owner.definition_scope import navigation_block
        headings = sorted((b for b in blocks.values() if b.get('block_kind') == 'HEADING'
                           and not navigation_block(b)), key=lambda b: b.get('order') or 0)
        if headings and anchor['block_id'] == headings[0]['block_id']:
            return 'A'
        return 'B'
    return 'C'


def _declarations(proof, blocks):
    result = []
    for name in proof.get('identity_values', []):
        refs = (proof.get('identity_anchor_block_ids') or {}).get(name) or [proof.get('heading_block_id')]
        refs = [bid for bid in refs if bid in blocks and bid in proof.get('block_ids', [])]
        formal = (proof.get('subject_formal_values') or {}).get(name, {})
        values = [{'block_id': bid, 'value': v} for bid, vs in formal.items() for v in vs]
        if not values:
            for bid, vs in (proof.get('formal_signature_values') or {}).items():
                for v in vs:
                    head = re.match(r'^\s*([A-Za-z_][\w$]*)\s*\(', v)
                    if head and head.group(1).upper() == name.upper():
                        values.append({'block_id': bid, 'value': v})
        result.append({'identity': name, 'anchor_block_ids': refs,
                       'formal_variants': values, 'relation': 'DECLARED_MEMBER',
                       'alias_inferred': False})
    return result


def _members(proof, document, plan):
    anchors = {bid for ds in (proof.get('identity_anchor_block_ids') or {}).values() for bid in ds}
    anchors.add(proof.get('heading_block_id'))
    shared = set(proof.get('shared_context_block_ids', []))
    labels = {s.strip().casefold(): role for role, values in _labels(plan).items() for s in values}
    active = None
    heading_ref = None
    result = []
    for b in sorted((b for b in document.get('blocks', []) if b['block_id'] in proof.get('block_ids', [])),
                    key=lambda b: b.get('order') or 0):
        bid = b['block_id']
        from agents.owner.definition_scope import heading_field_label
        label = heading_field_label(b.get('text'),proof.get('primary_identity'),labels)
        if b.get('block_kind') == 'HEADING':
            active, heading_ref = labels.get(label), bid
        role = ('SHARED_CONTEXT' if bid in shared else 'IDENTITY_ANCHOR' if bid in anchors else
                (active + '_CANDIDATE') if active and b.get('block_kind') != 'HEADING' else
                'CODE_CANDIDATE' if b.get('block_kind') == 'CODE' else 'BODY_CANDIDATE')
        result.append({'block_id': bid, 'order': b.get('order'), 'role': role,
                       'field_role': active if active and bid != heading_ref and bid not in shared else None,
                       'role_label_block_id': heading_ref if active else None,
                       'role_origin': 'OBSERVED_PROFILE_FIELD_HEADING' if active else 'NATIVE_STRUCTURE',
                       'applies_to': list(proof.get('identity_values', [])),
                       'semantic_claim': False})
    return result


def _execute_family(family, document, proofs, plan, diagnostics=None):
    blocks = {b['block_id']: b for b in document.get('blocks', [])}
    result = []
    for proof in proofs:
        if structure_family(proof, document) != family:
            continue
        members = list(proof.get('block_ids') or [])
        anchors = list(proof.get('definition_anchor_block_ids') or [])
        errors = []
        if not members or any(bid not in blocks for bid in members):
            errors.append('SOURCE_BLOCK_MISSING')
        if not anchors or not set(anchors) <= set(members):
            errors.append('ANCHOR_OUTSIDE_DEFINITION')
        declarations = _declarations(proof, blocks)
        if not declarations or any(not d['anchor_block_ids'] for d in declarations):
            errors.append('DECLARATION_ANCHOR_MISSING')
        contract = {'schema_version': 'StructureExecution/v1', 'structure': family,
                    'execution_id': stable_id('structure_execution', [document.get('page_id'), proof.get('section_id'), family]),
                    'status': 'INVALID' if errors else 'VERIFIED_STRUCTURE', 'reason_codes': errors,
                    'page_id': document.get('page_id'), 'source_url': document.get('url'),
                    'source_hash': document.get('page_hash'), 'plan': plan,
                    'declarations': declarations, 'members': _members(proof, document, plan),
                    'boundary': {'start_order': proof.get('start_order'),
                                 'end_order_exclusive': proof.get('end_order_exclusive'),
                                 'dom_parent': proof.get('dom_parent')},
                    'publication_permission': False}
        from agents.owner.composite_definition import field_members
        shared = set(proof.get('shared_context_block_ids', []))
        contract['field_members'] = field_members([blocks[bid] for bid in members if bid in blocks and bid not in shared], plan)
        contract['compositions'] = [{'parent_structure': family, 'child_structure': 'FIELD_TABLE',
            'table_locator': path, 'subject_identities': list(proof.get('identity_values', [])),
            'member_block_ids': [m['block_id'] for m in contract['field_members'] if m['table_locator'] == path]}
            for path in sorted({m['table_locator'] for m in contract['field_members']})]
        # Preserve legacy proof fields/IDs: all downstream stages consume the
        # same augmented proof instead of independently rediscovering a range.
        executed=dict(proof, structure_execution=contract)
        if plan.get('execution'):
            from agents.owner.profile_execution import annotate
            executed,error=annotate(executed,document,plan)
            if error:
                if diagnostics is not None:diagnostics.append({'structure':family,'plan_id':plan.get('plan_id'),
                    'block_id':proof.get('heading_block_id'),'section_id':proof.get('section_id'),
                    'reason':error,'status':'REJECTED_BINDING'})
                continue
            declared_kinds={d['feature_type'] for b in executed['structure_execution'].get('identity_bindings',[])
                            for d in b.get('declarations',[])}
            if not executed.get('feature_type') and len(declared_kinds)==1:
                # Carry the verifier's result into the source contract. An
                # omitted type must not turn it into a wildcard downstream.
                executed['feature_type']=next(iter(declared_kinds))
            for declaration in contract['declarations']:
                if declaration['formal_variants']:continue
                for binding in contract['identity_bindings']:
                    for local in binding['declarations']:
                        value=local.get('formal_value')
                        if local['identity']==declaration['identity'] and value and not (
                            family=='D' and re.fullmatch(r'[A-Za-z_][\w$]*\(\s*\)',value)):
                            declaration['formal_variants'].append({'block_id':binding['block_id'],'value':value})
        result.append(executed)
    return result


def execute_page(document, proofs, plan, **kwargs):
    return _execute_family('A', document, proofs, plan, **kwargs)


def execute_sections(document, proofs, plan, **kwargs):
    return _execute_family('B', document, proofs, plan, **kwargs)


def execute_entries(document, proofs, plan, **kwargs):
    return _execute_family('C', document, proofs, plan, **kwargs)


def execute_rows(document, proofs, plan, **kwargs):
    return _execute_family('D', document, proofs, plan, **kwargs)


def execute_member_sets(document, proofs, plan, **kwargs):
    return _execute_family('E', document, proofs, plan, **kwargs)


EXECUTORS = {'A': execute_page, 'B': execute_sections, 'C': execute_entries,
             'D': execute_rows, 'E': execute_member_sets}


def execute_structures(document, profile=None, *, native_executor=None):
    if native_executor is None:
        from agents.owner.definition_scope import _native_definition_scopes
        native_executor = _native_definition_scopes
    plans = compile_structure_plan(profile)
    from agents.owner.profile_execution import applies,configured_row_proofs,configured_entry_proofs,compile_execution
    from agents.owner.explicit_heading_plan import configured_heading_proofs
    details=[]
    direct=[]
    # Explicit locators run before native nomination. Native observations remain
    # the compatibility source for scopes outside an explicit plan.
    for plan in plans:
        if not plan.get('execution'):continue
        if plan['structure']=='D':direct.extend(configured_row_proofs(document,plan,details))
        if plan['structure'] in {'A','B'}:direct.extend(configured_heading_proofs(document,plan,details))
    raw = native_executor(document, profile)
    for proof in direct:
        if proof.get('explicit_definition_authority'):
            raw=[p for p in raw if p.get('heading_block_id')!=proof['heading_block_id']]
        # The explicit heading executor has checked the subject and structural
        # boundary. A wrapper-only native range must not shadow that proof.
        if proof.get('configured_structure') in {'A','B'}:
            raw=[p for p in raw if not (p.get('heading_block_id')==proof['heading_block_id']
                and set(p.get('identity_values',[]))==set(proof['identity_values'])
                and set(p.get('block_ids',[])) < set(proof['block_ids']))]
        # A richer co-declaration at the identical physical boundary replaces
        # a narrower nomination, rather than creating competing Owner scopes.
        raw=[p for p in raw if not (p.get('heading_block_id')==proof['heading_block_id']
            and set(p.get('block_ids',[]))==set(proof.get('block_ids',[]))
            and (not p.get('feature_type') or p.get('feature_type')==proof.get('feature_type'))
            and set(p.get('identity_values',[])) < set(proof['identity_values']))]
        if not any(p.get('heading_block_id')==proof['heading_block_id']
                   and (not p.get('feature_type') or p.get('feature_type')==proof.get('feature_type'))
                   and set(proof['identity_values'])<=set(p.get('identity_values',[])) for p in raw):
            raw.append(proof)
        else:
            details.append({'structure':proof.get('configured_structure','D'),
                'block_id':proof['heading_block_id'],'reason':'EXISTING_LOCAL_PROOF_REUSED',
                'status':'COMPATIBLE_PROOF_REUSED'})
    blocks={b['block_id']:b for b in document.get('blocks',[])}
    native=[]
    rejected=[]
    for proof in raw:
        anchor=blocks.get(proof.get('heading_block_id'),{})
        context=' / '.join(anchor.get('heading_ancestry') or [])
        if not proof.get('explicit_definition_authority') and ((anchor.get('definition_context') or {}).get('role')=='DEVELOPER_API_DECLARATION'
                or re.search(r'\b(?:API|SDK|JDBC|ODBC)\s+(?:functions?|reference|methods?)\b',context,re.I)):
            rejected.append({'section_id':proof.get('section_id'),
                             'reason':'EXPLICIT_DEVELOPER_API_CONTEXT'})
        else:native.append(proof)
    for plan in plans:
        if plan['structure']=='C' and plan.get('execution'):
            entries=configured_entry_proofs(document,plan,profile,native,details)
            # A source-observed role configured by the Profile replaces the
            # provisional callable inference at that same physical entry.
            replaced={p['heading_block_id'] for p in entries if p.get('configured_anchor_role') or p.get('explicit_definition_authority')}
            native=[p for p in native if p.get('heading_block_id') not in replaced]
            native.extend(entries)
    selected = []
    diagnostics = []
    controlled=set()
    explicit_by_family={k:[p for p in plans if p['structure']==k and p.get('execution')] for k in RECIPES}
    ambiguous=set()
    for block in blocks.values():
        for family,family_plans in explicit_by_family.items():
            if family=='D' and block.get('block_kind')!='TABLE_ROW':continue
            if family in {'A','B'} and block.get('block_kind')!='HEADING':continue
            if len(family_plans)>1 and sum(applies(p,block,document) for p in family_plans)>1:
                ambiguous.add((family,block['block_id']));controlled.add(block['block_id'])
                details.append({'structure':family,'block_id':block['block_id'],
                    'reason':'OVERLAPPING_EXPLICIT_PLANS','status':'AMBIGUOUS',
                    'plan_ids':[p['plan_id'] for p in family_plans if applies(p,block,document)]})
    for plan in plans:
        eligible=native
        if plan.get('execution'):
            eligible=[p for p in native if structure_family(p,document)!=plan['structure']
                      or applies(plan,blocks.get(p.get('heading_block_id'),{}),document)]
            eligible=[p for p in eligible if (plan['structure'],p.get('heading_block_id')) not in ambiguous]
            for proof in eligible:
                if structure_family(proof,document)==plan['structure']:controlled.update(proof.get('block_ids',[]))
            if plan['structure']=='D':
                rows=[b for b in blocks.values() if b.get('block_kind')=='TABLE_ROW' and applies(plan,b,document)]
                controlled.update(b['block_id'] for b in rows)
                paths=[b.get('dom_path','')+' > ' for b in rows if b.get('dom_path')]
                controlled.update(b['block_id'] for b in blocks.values()
                                  if any(b.get('dom_path','').startswith(path) for path in paths))
            if plan['structure'] in {'A','B'}:
                controlled.update(b['block_id'] for b in blocks.values() if b.get('block_kind')=='HEADING' and applies(plan,b,document))
        found = EXECUTORS[plan['structure']](document, eligible, plan,diagnostics=details)
        if plan.get('execution'):
            outside=[p for p in native if structure_family(p,document)==plan['structure']
                     and not any(applies(other,blocks.get(p.get('heading_block_id'),{}),document)
                                 for other in explicit_by_family[plan['structure']])]
            legacy={k:v for k,v in plan.items() if k!='execution'}
            found.extend(EXECUTORS[plan['structure']](document,outside,legacy))
        diagnostics.append({'structure': plan['structure'], 'plan_id':plan.get('plan_id'), 'status': 'MATCHED' if found else 'NO_MATCH',
                            'source_units': len(found),'authority':'EXPLICIT_PROFILE' if plan.get('execution') else 'LEGACY_INFERRED',
                            'profile_execution_digest': compile_execution(plan)['digest'] if plan.get('execution') else None,
                            'identity_no_match_or_ambiguous':sum(structure_family(p,document)==plan['structure'] for p in eligible)-len(found),
                            'invalid': sum(
                                p['structure_execution']['status'] != 'VERIFIED_STRUCTURE' for p in found)})
        selected.extend(found)
    # Preserve the source-verifier order, which existing tie-breakers use.
    def key(p):
        return (p.get('section_id'),p.get('association'),p.get('definition_pattern'),
                tuple(p.get('binding_input_identity_values',p.get('identity_values',[]))),tuple(p.get('block_ids',[])))
    by_section = {key(p): p for p in selected}
    ordered = [by_section[key(p)] for p in native if key(p) in by_section]
    if any(p['structure']=='B' and p.get('execution') for p in plans):
        from agents.owner.definition_scope import navigation_block, normalized_name, heading_container
        executed={p.get('heading_block_id') for p in ordered}
        proposed={p.get('heading_block_id') for p in native}
        attempts=[]
        for block in blocks.values():
            if block.get('block_kind')!='HEADING':continue
            bid=block['block_id'];name=normalized_name(block.get('text'))
            reason=('DEFINITION_EXECUTED' if bid in executed else
                    'NAVIGATION_NOT_DEFINITION' if navigation_block(block) else
                    'HEADING_IDENTITY_UNRESOLVED' if not re.fullmatch(r'[A-Z_][A-Z0-9_$]*(?: [A-Z_][A-Z0-9_$]*){0,3}',name) else
                    'PROFILE_IDENTITY_BINDING_UNRESOLVED' if bid in proposed else
                    'LOCAL_DEFINITION_NOT_PROVEN')
            attempts.append({'block_id':bid,'raw_heading':block.get('text'),
                             'normalized_identity':name,'container_locator':heading_container(block),'reason':reason})
        diagnostics.append({'structure':'B','status':'HEADING_EXECUTION_AUDIT',
                            'heading_attempts':attempts})
    for detail in details:
        detail['source_url']=document.get('url')
        detail['resolved_by_compatible_proof']=any(p.get('heading_block_id')==detail.get('block_id') for p in ordered)
    diagnostics.extend(details)
    failed=[d for d in details if d.get('status') in {'REJECTED_BINDING','REJECTED_BOUNDARY','AMBIGUOUS'}
            and not d.get('resolved_by_compatible_proof')]
    return {'proofs': ordered, 'diagnostics': diagnostics, 'rejected':rejected+failed,
            'counts': dict(Counter(p['structure_execution']['structure'] for p in ordered)),
            'controlled_block_ids':sorted(controlled)}
