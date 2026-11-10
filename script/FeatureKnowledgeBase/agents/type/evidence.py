"""Owner-local TypedFeatureEvidence/v1 construction.

This module deliberately starts from an already resolved IdentityUnit.  It
does not discover identities, consult a known-feature inventory, call an LLM,
or use a page role as a type decision.  Page and URL text are context signals;
the decisive observations must be tied to a local owner slot.
"""

from __future__ import annotations

import hashlib
import re
from agents.contracts.identity_key import normalize_identity
from typing import Any, Dict, Iterable, List, Mapping, Sequence


TARGET_TYPES = {"FUNCTION", "OPERATOR", "DATATYPE"}
NON_TARGET = "NON_TARGET"
TARGET_UNCERTAIN = "TARGET_FEATURE_UNCERTAIN"

ALIGN_EXACT = "EXACT_HEAD"
ALIGN_SLOT = "FEATURE_SLOT"
ALIGN_INCIDENTAL = "INCIDENTAL_MENTION"
ALIGN_PARAMETER = "PARAMETER_SLOT"
ALIGN_EXAMPLE = "EXAMPLE_SLOT"
ALIGN_PROSE = "PROSE_ONLY"

STRONG = "STRONG"
MEDIUM = "MEDIUM"
WEAK = "WEAK"

_WORD_SUBJECT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\s+[A-Za-z_][A-Za-z0-9_$]*)*$")
_CALL_HEAD = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*(?:\s+[A-Za-z_][A-Za-z0-9_$]*)*)\s*\("
)
_CALL_ANY = re.compile(
    r"(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_$]*)\s*\("
)
_TYPE_WORDS = re.compile(
    r"\b(?:data[- ]?types?|datatypes?|type\s+name|declared\s+type|type\s+declaration|"
    r"storage\s+class|type\s+affinity|column\s+type|database\s+type|"
    r"type\s+mapping|type\s+support|supported\s+types?|built[- ]in\s+types?|"
    r"type\s+(?:constructor|reference|catalog|system|definition)|"
    r"data[- ]?type\s+constructor|declaration\s+syntax)\b", re.I
)
_TYPE_MAPPING_WORDS = re.compile(
    r"\b(?:type\s+mapping|mapping\s+(?:of|to|from)|supported\s+types?|"
    r"data[- ]?type\s+constructor|type\s+constructor|type\s+declaration|"
    r"type\s+support|built[- ]in\s+types?|data[- ]?types?)\b", re.I
)
_FUNCTION_WORDS = re.compile(
    r"\b(?:function|functions|routine|routines|callable|aggregate|procedure|method)\b", re.I
)
_OPERATOR_WORDS = re.compile(
    r"\b(?:operator|operators|predicate|predicates|precedence|operand|expression)\b", re.I
)
_OPERATOR_SECTION_WORDS = re.compile(
    r"\b(?:operator|operators|predicate|predicates|precedence|operation|operations)\b", re.I
)
_SQL_WORDS = re.compile(
    r"\b(?:sql|database|query|queries|language|syntax|reference|built[- ]in|"
    r"expression|statement)\b", re.I
)
_CONFIG_WORDS = re.compile(
    r"\b(?:configuration|config(?:uration)?\s+file|setting|settings|option|options|"
    r"parameter|parameters|key\s+name|command[- ]line|session\s+setting|"
    r"server\s+setting|runtime\s+parameter)\b", re.I
)
_SCHEMA_WORDS = re.compile(
    r"\b(?:system\s+table|system\s+schema|system\s+column|information[_ ]schema|"
    r"system\s+catalog|catalog\s+column|metadata\s+column)\b", re.I
)
_METRIC_WORDS = re.compile(
    r"\b(?:system\s+metric|metrics?|monitoring|telemetry|profile\s+events?|"
    r"event\s+logs?|performance\s+counters?)\b", re.I
)
_ENGINE_WORDS = re.compile(
    r"\b(?:table\s+engine|storage\s+engine|engine|integration|integrations|"
    r"connector|connectors|extension|extensions|plugin)\b", re.I
)
_FORMAT_WORDS = re.compile(
    r"\b(?:input\s+format|output\s+format|format|protocol|protocols|"
    r"serialization|serialisation|encoding|wire\s+format|packet)\b", re.I
)
_API_WORDS = re.compile(
    r"\b(?:c[- ]?api|c[- ]?language|client\s+api|client\s+library|sdk|"
    r"application\s+programming\s+interface|programming\s+interface|api\s+reference)\b", re.I
)
_EXAMPLE_WORDS = re.compile(
    r"\b(?:example|examples|sample|samples|tutorial|demo|output|result|"
    r"test\s+case|illustration|usage)\b", re.I
)
_FIELD_WORDS = re.compile(r"\b(?:field|fields|column|columns|member|members|attribute|attributes)\b", re.I)
_LABEL_WORDS = re.compile(r"\b(?:chapter|section|overview|contents|index|table\s+of\s+contents)\b", re.I)
_EXAMPLE_VALUE = re.compile(
    r"(?:['\"]|\bselect\b|\binsert\b|\bupdate\b|\bdelete\b|\bfrom\b|"
    r"\btrue\b|\bfalse\b|\bnull\b|\b(?:rows?|seconds?|milliseconds?|bytes?)\b)", re.I
)
_RESULT_LIKE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:sec|secs|ms|milliseconds?|rows?|bytes?)\b", re.I)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    value = _clean(value)
    return [value] if value else []


def _is_symbol(subject: str) -> bool:
    return bool(subject) and not bool(_WORD_SUBJECT.fullmatch(subject))


def _subject_in_text(subject: str, text: str) -> bool:
    subject = _clean(subject)
    text = _clean(text)
    if not subject or not text:
        return False
    if _is_symbol(subject):
        return subject in text
    return bool(re.search(r"(?<![A-Za-z0-9_$])" + re.escape(subject) + r"(?![A-Za-z0-9_$])", text, re.I))


def _surface_equals(subject: str, value: str) -> bool:
    subject = _clean(subject)
    value = _clean(value)
    if not subject or not value:
        return False
    if '"' in subject or '.' in subject:
        return normalize_identity(subject)==normalize_identity(value)
    if _is_symbol(subject):
        stripped = value.strip(" \t\r\n'\"`()[]{}")
        return stripped == subject
    pattern = (
        r"^" + re.escape(subject)
        + r"(?:\s*\([^)]*\))?(?:\s*#)?(?:\s*(?:→|->).*)?$"
    )
    return bool(re.match(pattern, value, re.I))


def _signature_head(value: str) -> str:
    match = _CALL_HEAD.match(_clean(value))
    return _clean(match.group(1)) if match else ""


def _signature_matches(subject: str, value: str) -> bool:
    subject = _clean(subject)
    value = _clean(value)
    if not subject or _is_symbol(subject):
        return False
    return bool(re.search(
        r"^\s*" + re.escape(subject) + r"\s*\(", value, re.I
    ) or re.search(
        r"(?<![A-Za-z0-9_$])" + re.escape(subject) + r"\s*\(", value, re.I
    ))


def _local_callable_definition_span(subject, block, context, headers, headings):
    """Locate a defining callable surface, rather than incidental prose."""
    pattern = re.compile(r"^\s*(?:\{\s*)?" + re.escape(subject) + r"\s*\([^)]*\)", re.I)
    kind = _clean(block.get("block_type")).lower()
    hint = context.get("profile_hint") or context.get("owner_profile_hint") or {}
    feature_bearing = isinstance(hint, Mapping) and hint.get("feature_bearing") is True
    header_defines = bool(_FUNCTION_WORDS.search(" ".join(headers)))
    heading_defines = bool(_FUNCTION_WORDS.search(" ".join(headings)))
    values = [(_clean(block.get("text")), "block")]
    if re.search(r"signature", _clean(context.get("owner_kind")), re.I):
        values.extend((part.strip(), "signature_alternative") for part in _clean(block.get("text")).split("|"))
    cells = [_clean(x) for x in block.get("cells") or []]
    if cells:
        values.append((cells[0], "first_cell"))
    for value, source in values:
        match = pattern.search(value)
        if not match:
            continue
        dedicated = kind in {"code", "pre", "signature", "syntax", "definition"}
        defining = dedicated or header_defines or heading_defines or feature_bearing
        # A matched heading identifies the subject only in a definition-like
        # owner; a parenthetical book title is not independently callable.
        if not defining:
            continue
        return {"source": source, "start": match.start(), "end": match.end(),
                "text": match.group(0).strip(),
                "reason": "LOCAL_CALLABLE_DEFINITION_SURFACE"}
    return None


def _callable_definition_span(subject, block, context, headers, headings):
    """Veto proven document metadata; retain existing ambiguous candidates."""
    defining = _local_callable_definition_span(subject, block, context, headers, headings)
    if defining:
        return defining
    hint = context.get("profile_hint") or context.get("owner_profile_hint") or {}
    text = _clean(block.get("text"))
    supporting = isinstance(hint, Mapping) and hint.get("surface_role") == "SUPPORTING_CONTEXT" and hint.get("feature_bearing") is False
    # Two explicit bibliographic fields establish document metadata. A
    # solitary word in prose is insufficient, as is SUPPORTING_CONTEXT alone.
    metadata = bool(re.search(r"\bAuthors?\s*:", text, re.I) and re.search(r"\bPublisher\s*:", text, re.I))
    if supporting and metadata:
        return None
    match = re.search(r"(?<![\w$])" + re.escape(subject) + r"\s*\([^)]*\)", text, re.I)
    return {"source": "block", "start": match.start() if match else None,
            "end": match.end() if match else None, "text": match.group(0) if match else "",
            "reason": "BASELINE_CALLABLE_WITHOUT_EXPLICIT_METADATA_VETO"}


def _operator_surface(subject: str, value: str) -> bool:
    """Return whether ``value`` presents ``subject`` as an operator surface.

    This is deliberately structural.  It does not contain a database operator
    dictionary: the local block must contain the subject and either a symbolic
    operand shape (``x @ y``) or an explicit operator-shaped heading/table.
    """

    subject = _clean(subject)
    value = _clean(value)
    if not subject or not value or not _subject_in_text(subject, value):
        return False
    if not _is_symbol(subject):
        # Word operators are established by an exact operator heading or an
        # explicit operator/operation table column.  Treating any prose word
        # in an operator-themed section as an operator would turn datatypes
        # such as ARRAY into operators.
        return False
    # A symbol surrounded by SQL-ish operands is stronger than an incidental
    # occurrence in a code sample.  The surrounding section/header still has
    # to establish that this is an operator owner.
    escaped = re.escape(subject)
    return bool(re.search(
        r"(?:[A-Za-z0-9_$\])}'\"]|\b\w+)\s*" + escaped +
        r"\s*(?:[A-Za-z0-9_$\[('_{]|\b\w+)", value
    )) or value.strip(" \t\r\n`()[]{}'") == subject


def _signature_variants(context: Mapping[str, Any]) -> List[str]:
    result: List[str] = []
    for value in context.get("signature_variants") or []:
        if isinstance(value, Mapping):
            value = value.get("value") or value.get("signature") or value.get("text")
        value = _clean(value)
        if value:
            result.append(value)
    result.extend(_clean(value) for value in context.get("raw_variants") or [] if _clean(value))
    return list(dict.fromkeys(result))


def _classify_context(context: Mapping[str, Any]) -> Dict[str, str]:
    parts: List[str] = []
    for key in (
        "page_title", "url", "heading_ancestry", "page_heading_context",
        "table_headers",
    ):
        parts.extend(_values(context.get(key)))
    profile_hint = context.get("profile_hint") or context.get("owner_profile_hint") or {}
    if isinstance(profile_hint, Mapping):
        parts.extend(_values(profile_hint.get("page_hint")))
        parts.extend(_values(profile_hint.get("owner_strategy")))
        parts.extend(_values(profile_hint.get("surface_role")))
    text = " | ".join(parts)
    if _CONFIG_WORDS.search(text):
        category = "CONFIGURATION"
    elif _SCHEMA_WORDS.search(text):
        category = "SYSTEM_TABLE_COLUMN"
    elif _METRIC_WORDS.search(text):
        category = "SYSTEM_METRIC"
    elif _ENGINE_WORDS.search(text):
        category = "ENGINE_OR_INTEGRATION"
    elif _FORMAT_WORDS.search(text):
        category = "FORMAT_OR_PROTOCOL"
    elif _API_WORDS.search(text):
        category = "API"
    elif _EXAMPLE_WORDS.search(text):
        category = "EXAMPLE_IDENTIFIER"
    else:
        category = ""
    return {"category": category, "text": text}


def _block_text(block: Mapping[str, Any]) -> str:
    values = [_clean(block.get("text"))]
    values.extend(_values(block.get("cells")))
    values.extend(_values(block.get("heading_ancestry")))
    values.extend(_values(block.get("table_headers")))
    return " | ".join(value for value in values if value)


def _block_is_example(block: Mapping[str, Any], local: str) -> bool:
    block_type = _clean(block.get("block_type")).lower()
    heading = " ".join(_values(block.get("heading_ancestry")))
    if block_type in {"example", "output", "sample"}:
        return True
    if _EXAMPLE_WORDS.search(heading):
        return True
    # A code-like block containing a query/value is an example only when it
    # lacks an explicit local reference slot.  Formal signatures can also be
    # code blocks, so this is never used alone as a negative page veto.
    # Do not search heading ancestry here: words such as ``row`` in a
    # structural heading (for example ``Row pattern expressions``) are not
    # example values and would incorrectly demote every sibling signature.
    content = " | ".join(
        value for value in [
            _clean(block.get("text")),
            " ".join(_values(block.get("cells"))),
        ] if value
    )
    return block_type in {"code", "pre"} and bool(_EXAMPLE_VALUE.search(content))


def _stable_id(parts: Iterable[Any]) -> str:
    payload = "|".join(_clean(value) for value in parts)
    return "typed_evidence:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _evidence(
    context: Mapping[str, Any],
    subject: str,
    owner_id: str,
    candidate_type: str,
    evidence_kind: str,
    alignment: str,
    strength: str,
    block: Mapping[str, Any] | None,
    reason_codes: Sequence[str],
    signature: str = "",
    non_target_category: str = "",
) -> Dict[str, Any]:
    block = block or {}
    block_id = _clean(block.get("block_id"))
    source_url = _clean(context.get("source_url") or context.get("url"))
    html_path = _clean(context.get("html_path"))
    owner_text = _clean(context.get("raw_text"))
    effective_reasons = list(reason_codes)
    if context.get("owner_local_binding") is False:
        effective_reasons.append("OWNER_LOCAL_BINDING_GAP")
    return {
        "schema_version": "TypedFeatureEvidence/v1",
        "evidence_id": _stable_id((context.get("identity_unit_id"), owner_id, candidate_type, evidence_kind, block_id, signature, owner_text)),
        "identity_unit_id": _clean(context.get("identity_unit_id")),
        "owner_id": owner_id,
        "subject": subject,
        "candidate_type": candidate_type,
        "evidence_kind": evidence_kind,
        "subject_alignment": alignment,
        "strength": strength,
        "owner_kind": _clean(context.get("owner_kind")) or "UNKNOWN",
        "owner_text": owner_text,
        "heading_ancestry": list(_values(context.get("heading_ancestry"))),
        "table_headers": list(_values(context.get("table_headers"))),
        "signature": signature,
        "source_url": source_url,
        "html_path": html_path,
        "block_id": block_id,
        "owner_local_binding": bool(context.get("owner_local_binding", True)),
        "owner_binding_reason": _clean(context.get("owner_binding_reason")),
        "owner_scope_identity_values": list(_values(context.get("owner_scope_identity_values"))),
        "owner_scope_block_ids": list(_values(context.get("owner_scope_block_ids"))),
        "owner_scope_mode": _clean(context.get("owner_scope_mode")),
        "owner_scope_locality": _clean(context.get("owner_scope_locality")),
        "reason_codes": list(dict.fromkeys(_clean(value) for value in effective_reasons if _clean(value))),
        "non_target_category": non_target_category,
        "provenance": {
            "dbms": _clean(context.get("dbms")),
            "page_id": _clean(context.get("page_id")),
            "source_url": source_url,
            "html_path": html_path,
            "owner_id": owner_id,
            "block_id": block_id,
            "profile_local_context": [dict(pattern) for pattern in
                (context.get('profile_hint') or {}).get('local_type_patterns', [])
                if pattern.get('page_id') == context.get('page_id')
                and pattern.get('block_id') in context.get('owner_scope_block_ids', [])],
        },
    }


def build_typed_feature_evidence(context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Build owner-local typed evidence records for one IdentityUnit owner.

    The function is intentionally conservative about negative evidence: a
    negative page context creates only a weak context observation.  A target
    is rejected only when the subject itself occupies a local non-target slot.
    """

    subject = _clean(context.get("canonical_subject") or context.get("subject"))
    owner_id = _clean(context.get("owner_id"))
    if not subject or not owner_id:
        return []
    blocks = [row for row in context.get("blocks") or [] if isinstance(row, Mapping)]
    if not blocks:
        blocks = [{
            "block_id": "synthetic:owner_text",
            "block_type": "text",
            "text": _clean(context.get("raw_text")),
            "cells": [],
            "table_headers": list(_values(context.get("table_headers"))),
            "heading_ancestry": list(_values(context.get("heading_ancestry"))),
        }]
    variants = _signature_variants(context)
    page_context = _classify_context(context)
    page_text = page_context["text"]
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(**kwargs: Any) -> None:
        row = _evidence(context, subject, owner_id, **kwargs)
        if row["evidence_id"] not in seen:
            seen.add(row["evidence_id"])
            records.append(row)

    for block in blocks:
        if (block.get('table_metadata') or {}).get('container_witness'):
            continue  # Whole-span grounding witness, never a declaration slot.
        text = _clean(block.get("text"))
        cells = [_clean(value) for value in block.get("cells") or [] if _clean(value)]
        headers = [_clean(value) for value in block.get("table_headers") or [] if _clean(value)]
        if not headers:
            headers = list(_values(context.get("table_headers")))
        headings = [_clean(value) for value in block.get("heading_ancestry") or [] if _clean(value)]
        if not headings:
            headings = list(_values(context.get("heading_ancestry")))
        local = " | ".join(value for value in [text, " ".join(cells), " ".join(headers), " ".join(headings)] if value)
        header_text = " ".join(headers)
        heading_text = " ".join(headings)
        nearest_heading = headings[-1] if headings else ""
        local_context = " | ".join([page_text, heading_text, header_text, text])
        definition_context = block.get('definition_context') or {}
        declaration_role = definition_context.get('role')
        if declaration_role=='DEVELOPER_API_DECLARATION':continue
        if declaration_role=='DECLARATION_CATALOG':
            matched=False
            for member in definition_context.get('members',[]):
                if _surface_equals(subject,member['identity']):
                    matched=True
                    kind=member['feature_type']
                    add(candidate_type=kind,evidence_kind='OPERATOR_SLOT' if kind=='OPERATOR' else 'CALLABLE_SIGNATURE',
                        alignment=ALIGN_EXACT,strength=STRONG,block=block,
                        reason_codes=['OBSERVED_TYPED_DECLARATION_CELL'],signature=member['raw_entry'])
            if matched:continue
        from agents.owner.surface_patterns import operator_label, datatype_definition_name
        anchor_proofs=[p for p in context.get('definition_section_proofs') or []
                       if block.get('block_id') in p.get('definition_anchor_block_ids',[])
                       and normalize_identity(subject) in p.get('identity_values',[])]
        local_operator=operator_label(text)
        from agents.owner.definition_patterns import verified_pattern
        structural=next((parsed for p in anchor_proofs for parsed in [verified_pattern(p,block,normalize_identity(subject))] if parsed),None)
        if structural:
            kind=structural['feature_type']
            add(candidate_type=kind,evidence_kind={'DATATYPE':'DATATYPE_DECLARATION',
                'OPERATOR':'OPERATOR_SLOT','FUNCTION':'CALLABLE_SIGNATURE'}[kind],
                alignment=ALIGN_EXACT,strength=STRONG,block=block,reason_codes=['SOURCE_VERIFIED_PROFILE_DEFINITION'])
            continue
        # Relation scopes include descriptive/supporting content for semantic
        # preservation. Only their cited identity anchor declares a type;
        # incidental words such as "operator" in the body do not re-declare
        # the subject. Independently observed declarations retain their path.
        local_proofs=[p for p in context.get('definition_section_proofs') or []
                      if block.get('block_id') in p.get('block_ids',[])
                      and normalize_identity(subject) in p.get('identity_values',[])]
        if not anchor_proofs and any((p.get('structure_execution') or {}).get('schema_version')=='StructureExecution/v2'
                                     for p in local_proofs):
            continue  # The same source contract declares identity at its anchors, not in body examples.
        if not anchor_proofs and any((p.get('structure_execution') or {}).get('status')=='VERIFIED_STRUCTURE'
                and any(m['block_id']==block.get('block_id') for m in p['structure_execution'].get('field_members',[]))
                for p in local_proofs):
            continue  # A subject's description/example does not re-declare its type.
        if (not declaration_role and local_proofs
                and all(p.get('definition_pattern') in {'SOURCE_RELATION_DEFINITION','FUNCTION_DECLARATION_SECTION','REFERENCE_DECLARATION_SECTION','COMPOSITE_FIELD_DEFINITION'} for p in local_proofs)
                and not anchor_proofs):
            continue
        proven_operator=any(p.get('definition_pattern')=='OPERATOR_LED_LIST_ITEM' for p in anchor_proofs)
        proven_type=any(p.get('definition_pattern')=='DATATYPE_SELF_DEFINITION' for p in anchor_proofs)
        if proven_operator and local_operator and _surface_equals(subject,local_operator[0]):
            add(candidate_type='OPERATOR',evidence_kind='OPERATOR_SLOT',alignment=ALIGN_EXACT,
                strength=STRONG,block=block,reason_codes=['PROVEN_NATIVE_OPERATOR_DEFINITION'])
            continue
        if proven_type and _surface_equals(subject,datatype_definition_name(text) or ''):
            add(candidate_type='DATATYPE',evidence_kind='DATATYPE_DECLARATION',alignment=ALIGN_EXACT,
                strength=STRONG,block=block,reason_codes=['PROVEN_DATATYPE_SELF_DEFINITION'])
            continue
        if declaration_role=='OPERATOR_CATALOG':
            if definition_context.get('domain')=='EMBEDDED_QUERY_LANGUAGE':continue
            if any(_surface_equals(subject,m['identity']) for m in definition_context.get('members',[])):
                add(candidate_type='OPERATOR',evidence_kind='OPERATOR_SLOT',alignment=ALIGN_EXACT,
                    strength=STRONG,block=block,reason_codes=['OBSERVED_LOCAL_OPERATOR_CATALOG_MEMBER'])
            continue
        if declaration_role in {'DATATYPE_TARGET_ROW','STORAGE_CLASS_DEFINITION','DATATYPE_DEFINITION_HEADING'}:
            if _surface_equals(subject,str(definition_context.get('identity') or '')):
                add(candidate_type='DATATYPE',evidence_kind='DATATYPE_DECLARATION',alignment=ALIGN_EXACT,
                    strength=STRONG,block=block,reason_codes=['OBSERVED_LOCAL_DATATYPE_SUBJECT'])
            continue
        if (declaration_role in {'FUNCTION_IMPLEMENTATION_RELATION', 'OPERATOR_IMPLEMENTATION_RELATION'}
                and _surface_equals(subject, str(definition_context.get('identity') or ''))):
            kind = 'FUNCTION' if declaration_role.startswith('FUNCTION_') else 'OPERATOR'
            add(candidate_type=kind, evidence_kind='IMPLEMENTATION_RELATION_SUBJECT', alignment=ALIGN_EXACT,
                strength=STRONG, block=block, reason_codes=['PRIMARY_SUBJECT_NOT_REFERENCED_IMPLEMENTATION'])
            records[-1]['definition_context'] = dict(definition_context)
            continue
        if (declaration_role == 'PAGE_FUNCTION_DEFINITION'
                and _surface_equals(subject, str(definition_context.get('identity') or ''))):
            add(candidate_type='FUNCTION', evidence_kind='EXPLICIT_SELF_DEFINITION', alignment=ALIGN_EXACT,
                strength=STRONG, block=block, reason_codes=['TITLE_AND_LOCAL_SELF_DEFINITION'])
            records[-1]['definition_context'] = dict(definition_context)
            continue
        if declaration_role == 'BEHAVIOR_TRUTH_TABLE':
            add(candidate_type=NON_TARGET, evidence_kind='BEHAVIOR_TABLE_CELL',
                alignment='CONTEXT_ONLY', strength='MEDIUM', block=block,
                reason_codes=['BEHAVIOR_TABLE_IS_NOT_DEFINITION_CATALOG'])
            records[-1]['definition_context'] = dict(definition_context)
            continue
        if ((declaration_role == 'DATATYPE_DECLARATION_LIST' and _surface_equals(subject, text))
                or (declaration_role == 'FUNCTION_DECLARATION_LIST' and _signature_matches(subject, text))):
            kind = 'DATATYPE' if declaration_role == 'DATATYPE_DECLARATION_LIST' else 'FUNCTION'
            add(candidate_type=kind, evidence_kind='DATATYPE_DECLARATION' if kind == 'DATATYPE' else 'CALLABLE_SIGNATURE',
                alignment=ALIGN_EXACT, strength=STRONG, block=block,
                reason_codes=['EXPLICIT_LOCAL_DECLARATION_LIST'], signature=text if kind == 'FUNCTION' else '')
            records[-1]['definition_context'] = dict(definition_context)
            continue
        # An exact callable in an explicitly named Function/Signature column
        # declares that function. "See operator ..." in its Description cell
        # cannot add a competing Operator vote for the callable.
        from agents.owner.catalog_surfaces import catalog_surfaces
        declarations=catalog_surfaces(headers,cells,heading_text)
        if (headers and re.fullmatch(r'\s*(?:functions?|routines?|signatures?)\s*',headers[0],re.I)
                and any(m['feature_type']=='FUNCTION' and _surface_equals(subject,m['identity'])
                        for m in declarations)):
            add(candidate_type='FUNCTION',evidence_kind='CALLABLE_SIGNATURE',alignment=ALIGN_EXACT,
                strength=STRONG,block=block,reason_codes=['EXPLICIT_CALLABLE_CATALOG_COLUMN'],signature=cells[0])
            records[-1]['callable_definition_span']=_callable_definition_span(subject,block,context,headers,headings)
            continue
        first_cell = bool(cells and _surface_equals(subject, cells[0]))
        subject_in_first = bool(cells and _subject_in_text(subject, cells[0]))
        subject_in_later = any(_subject_in_text(subject, cell) for cell in cells[1:])
        exact_heading = any(_surface_equals(subject, heading) for heading in headings)
        # Variants describe the IdentityUnit, not every block in the owner.
        # The previous implementation selected the first matching variant
        # globally, which made every sibling block look like a callable
        # signature.  A signature must be present in this block (or one of its
        # local table cells) before it can affect type evidence.
        signature_text = ""
        for value in [text, *cells]:
            if _signature_matches(subject, value):
                signature_text = value
                break
        call_in_block = bool(signature_text)
        exact_signature = call_in_block
        block_type = _clean(block.get("block_type")).lower()
        example_like = _block_is_example(block, local)
        owner_kind = _clean(context.get("owner_kind"))
        profile_hint = context.get("profile_hint") or context.get("owner_profile_hint") or {}
        profile_type_hint = ""
        if isinstance(profile_hint, Mapping):
            profile_type_hint = _clean(profile_hint.get("type_hint")).upper()
        signature_owner_context = bool(re.search(
            r"(?:signature|callable|function|routine)", owner_kind, re.I
        ))
        profile_function_context = profile_type_hint == "FUNCTION"
        local_patterns = profile_hint.get('local_type_patterns', []) if isinstance(profile_hint, Mapping) else []
        local_types = {p['context_type'] for p in local_patterns
                       if p.get('block_id') == block.get('block_id') and p.get('page_id') == context.get('page_id')}
        profile_function_context = profile_function_context or local_types == {'FUNCTION'}
        definition_signature_surface = bool(
            call_in_block
            and exact_heading
            and block_type in {"definition", "signature"}
        )

        function_header = bool(_FUNCTION_WORDS.search(header_text))
        operator_header = bool(
            _OPERATOR_SECTION_WORDS.search(header_text)
            and not _FUNCTION_WORDS.search(header_text)
        )
        datatype_header = bool(_TYPE_WORDS.search(header_text))
        generic_type_header = bool(re.search(
            r"\b(?:data\s+type|(?:source|target|declared|database|trino)\s+type|"
            r"type\s+(?:name|declaration|mapping|support|constructor))\b",
            header_text,
            re.I,
        ))
        # Reference pages often use headings such as "functions and
        # operators".  In that mixed heading, the word ``functions`` is page
        # taxonomy, not proof that a callable example is a Function.  Let an
        # explicit operator/predicate context take precedence for local
        # callable-looking example tokens.
        # Evaluate each heading level independently.  A page-level heading
        # such as ``functions and operators`` is taxonomy, while a child
        # heading such as ``Extraction function`` is a local callable
        # surface.  Joining the ancestry first made the mixed parent mask the
        # more specific child and turned function table rows into operators.
        function_heading_context = any(
            _FUNCTION_WORDS.search(heading)
            and not _OPERATOR_SECTION_WORDS.search(heading)
            for heading in headings
        )
        operator_heading_context = any(
            _OPERATOR_SECTION_WORDS.search(heading)
            and not _FUNCTION_WORDS.search(heading)
            for heading in headings
        )
        function_context = bool(_FUNCTION_WORDS.search(text) or function_heading_context)
        # Keep the whole owner-local heading ancestry in view.  The nearest
        # heading is often the feature label (for example ``CASE`` or
        # ``IS NULL``), while the parent heading carries the operator/
        # predicate/expression role.
        operator_heading_context = bool(
            operator_heading_context
            or any(
                _OPERATOR_WORDS.search(heading)
                and not _FUNCTION_WORDS.search(heading)
                for heading in headings
            )
        )
        explicit_operator_heading = any(
            bool(re.search(
                r"\b(?:operator|operators|operation|operations|predicate|predicates)\b",
                heading,
                re.I,
            )) and not _FUNCTION_WORDS.search(heading)
            for heading in headings
        )
        explicit_operator_surface = bool(
            _subject_in_text(subject, text)
            and re.search(
                r"\b(?:operator|operators|predicate|predicates)\b|"
                r"\b(?:written|denoted|represented)\s+(?:with|by)\b",
                text,
                re.I,
            )
        )
        operator_context = bool(
            operator_heading_context
            or explicit_operator_surface
            or (block_type in {"heading", "signature"} and _OPERATOR_WORDS.search(text))
        )
        actual_subject_heading = bool(block_type == 'heading' and _surface_equals(subject,text))
        datatype_self_definition = bool(re.match(
            r'^\s*(?:The\s+)?'+re.escape(subject)+r'\s+(?:data\s+)?type\s+'
            r'(?:is|stores|represents|holds)\b',text,re.I))
        datatype_context = bool(
            _TYPE_WORDS.search(heading_text)
            or datatype_self_definition
        )
        type_mapping_context = bool(
            _TYPE_MAPPING_WORDS.search(heading_text)
            or _TYPE_MAPPING_WORDS.search(header_text)
            or _TYPE_MAPPING_WORDS.search(text)
        )
        type_constructor_context = bool(re.search(
            r"\b(?:data[- ]?type\s+constructor|type\s+constructor|cast\s+to)\b",
            " | ".join([heading_text, header_text, text]),
            re.I,
        ))
        sql_context = bool(_SQL_WORDS.search(local_context))

        subject_in_mapping = bool(
            type_mapping_context
            and any(_subject_in_text(subject, cell) for cell in cells)
        )
        operator_shape = _operator_surface(subject, " ".join(cells) or text)
        operator_heading_surface = bool(
            _is_symbol(subject)
            and operator_context
            and block_type in {"heading", "signature"}
            and _subject_in_text(subject, local)
            and _OPERATOR_WORDS.search(local)
        )
        datatype_surface = bool(
            first_cell
            or actual_subject_heading
            or (subject_in_mapping and not _is_symbol(subject))
            or (type_mapping_context and call_in_block and bool(
                re.match(r"^\s*" + re.escape(subject), text, re.I)
            ))
            or (type_constructor_context and _subject_in_text(subject, local))
        )
        explicit_datatype_surface = bool(datatype_context and datatype_surface)

        # Exact local slots.  The table header/heading is required for a
        # symbolic operator; a symbol in a query, regex, or format string is
        # not enough.
        function_slot = bool(
            not explicit_datatype_surface
            and (
                (call_in_block and not example_like and (
                    function_header
                    or function_context
                    or signature_owner_context
                    or profile_function_context
                ))
                or (first_cell and (function_header or (function_context and not operator_context)))
                or definition_signature_surface
                or (exact_heading and function_context and (call_in_block or exact_signature))
            )
        )
        operator_slot = bool(
            (
                _is_symbol(subject)
                and (subject_in_first or operator_shape or operator_heading_surface)
                and (operator_header or operator_context)
            )
            or (
                not _is_symbol(subject)
                and subject_in_first
                and (operator_header or explicit_operator_heading or explicit_operator_surface)
            )
            or (exact_heading and (operator_header or explicit_operator_heading or operator_context) and not example_like)
            or (explicit_operator_surface and not example_like)
            or (operator_context and _is_symbol(subject) and block_type in {"heading", "signature", "code", "table_row"} and subject_in_first)
        )
        datatype_slot = bool(
            not _is_symbol(subject)
            and (
                (datatype_surface and (datatype_header or generic_type_header or datatype_context))
                or (actual_subject_heading and datatype_context and not example_like)
                or (call_in_block and datatype_context and bool(re.match(r"^\s*" + re.escape(subject), text, re.I)) and not example_like)
            )
        )

        if context.get('definition_term_function'):
            operator_slot = False

        if function_slot:
            callable_span = _callable_definition_span(subject, block, context, headers, headings) if call_in_block else None
            if call_in_block and callable_span is None:
                add(candidate_type=TARGET_UNCERTAIN, evidence_kind="INCIDENTAL_CALLABLE_CONTEXT",
                    alignment="CONTEXT_ONLY", strength="WEAK", block=block,
                    reason_codes=["CALLABLE_DEFINITION_ATTRIBUTION_MISSING"], signature=signature_text)
                function_slot = False
        if function_slot:
            add(
                candidate_type="FUNCTION",
                evidence_kind="CALLABLE_SIGNATURE" if call_in_block else "FUNCTION_SLOT",
                alignment=ALIGN_EXACT if call_in_block or exact_heading else ALIGN_SLOT,
                strength=STRONG,
                block=block,
                reason_codes=["SUBJECT_CALLABLE_HEAD", "OWNER_LOCAL_FUNCTION_SLOT"],
                signature=signature_text,
            )
            if call_in_block:
                records[-1]["callable_definition_span"] = callable_span
        if operator_slot:
            add(
                candidate_type="OPERATOR",
                evidence_kind="OPERATOR_SLOT",
                alignment=ALIGN_EXACT if exact_heading else ALIGN_SLOT,
                strength=STRONG,
                block=block,
                reason_codes=["SUBJECT_OPERATOR_SLOT", "OWNER_LOCAL_OPERATOR_SURFACE"],
                signature=text if _is_symbol(subject) else signature_text,
            )
        if datatype_slot:
            add(
                candidate_type="DATATYPE",
                evidence_kind="DATATYPE_DECLARATION" if call_in_block else "DATATYPE_SLOT",
                alignment=ALIGN_EXACT if exact_heading else ALIGN_SLOT,
                strength=STRONG,
                block=block,
                reason_codes=["SUBJECT_DATATYPE_HEAD", "OWNER_LOCAL_DATATYPE_SLOT"],
                signature=signature_text,
            )

        # A structural SQL surface that is not safely typed is retained for
        # review instead of being silently discarded.
        if not (function_slot or operator_slot or datatype_slot):
            structural_surface = bool(first_cell or exact_heading or call_in_block)
            if structural_surface and sql_context and not example_like and not subject_in_later:
                add(
                    candidate_type=TARGET_UNCERTAIN,
                    evidence_kind="UNRESOLVED_SQL_FEATURE_SURFACE",
                    alignment=ALIGN_EXACT if exact_heading or call_in_block else ALIGN_SLOT,
                    strength=MEDIUM,
                    block=block,
                    reason_codes=["SQL_OWNER_LOCAL_SURFACE", "TYPE_NOT_RESOLVED"],
                    signature=signature_text,
                )

        # A subject in a non-identity cell is a parameter/field occurrence,
        # even when its page is a SQL reference page.
        if subject_in_later and not subject_in_mapping:
            category = "PARAMETER_OR_FIELD"
            add(
                candidate_type=NON_TARGET,
                evidence_kind="PARAMETER_OR_FIELD_SLOT",
                alignment=ALIGN_PARAMETER,
                strength=STRONG,
                block=block,
                reason_codes=["SUBJECT_NOT_IN_IDENTITY_SLOT", "PARAMETER_OR_FIELD_OCCURRENCE"],
                non_target_category=category,
            )
        elif not (function_slot or operator_slot or datatype_slot) and _subject_in_text(subject, local):
            if example_like or _RESULT_LIKE.search(local) or _EXAMPLE_VALUE.search(local):
                add(
                    candidate_type=NON_TARGET,
                    evidence_kind="EXAMPLE_OCCURRENCE",
                    alignment=ALIGN_EXAMPLE,
                    strength=STRONG,
                    block=block,
                    reason_codes=["SUBJECT_EXAMPLE_SLOT", "NO_FEATURE_OWNER_SLOT"],
                    non_target_category="EXAMPLE_IDENTIFIER",
                )
            elif exact_heading and _LABEL_WORDS.search(heading_text):
                add(
                    candidate_type=NON_TARGET,
                    evidence_kind="DOCUMENTATION_LABEL",
                    alignment=ALIGN_PROSE,
                    strength=MEDIUM,
                    block=block,
                    reason_codes=["DOCUMENTATION_LABEL_CONTEXT"],
                    non_target_category="DOCUMENTATION_LABEL",
                )

        # Explicit local negative slots.  These are not generated from the
        # page title alone: the subject must be the owner slot/field itself.
        negative_category = ""
        negative_pattern = None
        if first_cell:
            # A Parameters metadata column describes the named object; only
            # the identity column can make that object a configuration slot.
            if _CONFIG_WORDS.search(headers[0] if headers else '') or (_CONFIG_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header)):
                negative_category, negative_pattern = "CONFIGURATION", _CONFIG_WORDS
            elif _SCHEMA_WORDS.search(header_text) or (_SCHEMA_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header)):
                negative_category, negative_pattern = "SYSTEM_TABLE_COLUMN", _SCHEMA_WORDS
            elif _METRIC_WORDS.search(header_text) or (_METRIC_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header)):
                negative_category, negative_pattern = "SYSTEM_METRIC", _METRIC_WORDS
            elif _ENGINE_WORDS.search(header_text) or (_ENGINE_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header)):
                negative_category, negative_pattern = "ENGINE_OR_INTEGRATION", _ENGINE_WORDS
            elif _FORMAT_WORDS.search(header_text) or (_FORMAT_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header)):
                negative_category, negative_pattern = "FORMAT_OR_PROTOCOL", _FORMAT_WORDS
            elif _API_WORDS.search(header_text) or (_API_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header)):
                negative_category, negative_pattern = "API", _API_WORDS
            elif _EXAMPLE_WORDS.search(heading_text) and not (function_header or operator_header or datatype_header):
                negative_category, negative_pattern = "EXAMPLE_IDENTIFIER", _EXAMPLE_WORDS
        if negative_category and negative_pattern is not None:
            add(
                candidate_type=NON_TARGET,
                evidence_kind="NON_TARGET_OWNER_SLOT",
                alignment=ALIGN_SLOT,
                strength=STRONG,
                block=block,
                reason_codes=["SUBJECT_OCCUPIES_NON_TARGET_SLOT", negative_category],
                non_target_category=negative_category,
            )

        # Weak context evidence is kept for review/diagnostics only.  It is
        # deliberately never enough to create OUT_OF_TARGET.
        if not (function_slot or operator_slot or datatype_slot) and not subject_in_later and not negative_category:
            if _CONFIG_WORDS.search(page_text) or _SCHEMA_WORDS.search(page_text) or _ENGINE_WORDS.search(page_text) or _FORMAT_WORDS.search(page_text) or _API_WORDS.search(page_text):
                add(
                    candidate_type=NON_TARGET,
                    evidence_kind="PAGE_CONTEXT_ONLY",
                    alignment=ALIGN_PROSE,
                    strength=WEAK,
                    block=block,
                    reason_codes=["PAGE_CONTEXT_IS_NOT_OWNER_PROOF"],
                    non_target_category=page_context["category"] or "OTHER",
                )

    if not records:
        add(
            candidate_type=NON_TARGET,
            evidence_kind="NO_OWNER_LOCAL_EVIDENCE",
            alignment=ALIGN_PROSE,
            strength=WEAK,
            block={},
            reason_codes=["NO_OWNER_LOCAL_TYPE_EVIDENCE"],
            non_target_category="OTHER",
        )
    records.sort(key=lambda row: (row["identity_unit_id"], row["owner_id"], row["candidate_type"], row["evidence_id"]))
    return records


__all__ = [
    "ALIGN_EXACT", "ALIGN_SLOT", "ALIGN_INCIDENTAL", "ALIGN_PARAMETER",
    "ALIGN_EXAMPLE", "ALIGN_PROSE", "STRONG", "MEDIUM", "WEAK",
    "TARGET_TYPES", "TARGET_UNCERTAIN", "NON_TARGET",
    "build_typed_feature_evidence",
]
