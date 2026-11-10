"""Finite structural relation plans; no selectors or database knowledge."""

_PLANS = (
    {'relation':'SIGNATURE_CONTAINER','identity_slot':'LEADING_SIGNATURE','boundary':'CURRENT_CONTAINER'},
    {'relation':'SELF_DEFINITION','identity_slot':'EXPLICIT_SELF_SUBJECT','boundary':'CURRENT_BLOCK'},
    {'relation':'PARAGRAPH_SIGNATURE','identity_slot':'LABEL_AND_SIGNATURE','boundary':'NEXT_LABEL_OR_HEADING'},
    {'relation':'STORAGE_CLASS','identity_slot':'EXPLICIT_TYPE_LABEL','boundary':'CURRENT_BLOCK'},
    {'relation':'TERM_BODY_PAIR','identity_slot':'TERM','boundary':'NEXT_TERM'},
    {'relation':'HEADING_SECTION','identity_slot':'HEADING','boundary':'NEXT_PEER_OR_PARENT_HEADING'},
    {'relation':'CATALOG_ROW','identity_slot':'VERIFIED_IDENTITY_CELL','boundary':'CURRENT_ROW'},
    {'relation':'CATALOG_MEMBER_CONTEXT','identity_slot':'TYPED_LITERAL_MEMBER','boundary':'LIST_AND_SHARED_PARENT'},
    {'relation':'GRAMMAR_MEMBER_CONTEXT','identity_slot':'GRAMMAR_MEMBER','boundary':'HEADING_SECTION'},
    {'relation':'PAGE_SUBJECT_RELATION','identity_slot':'TYPED_SUBJECT_HEADING','boundary':'DEFINITION_PREFIX'},
)


def default_scope_patterns():
    return [dict(p) for p in _PLANS]


def validate_scope_patterns(patterns):
    if not isinstance(patterns,list):raise ValueError('definition_scope_patterns must be a list')
    seen=set()
    for p in patterns:
        if not isinstance(p,dict) or p not in _PLANS:
            raise ValueError('unsupported definition scope relation/slot/boundary')
        if p['relation'] in seen:raise ValueError('duplicate definition scope relation')
        seen.add(p['relation'])
    return patterns


def compile_scope_plan(profile=None):
    hints=(profile or {}).get('hint_contract') or {}
    if 'definition_scope_patterns' in hints:
        plans = validate_scope_patterns(hints['definition_scope_patterns'])
    else:
        plans = default_scope_patterns()
    # Missing-field compatibility only. An explicit empty plan stays empty.
    from agents.owner.structure_execution import enabled_relations
    allowed = enabled_relations(profile)
    return [dict(p) for p in plans if p['relation'] in allowed]
