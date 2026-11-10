"""Target Feature Evidence and Identity Stability shadow replay.

This module deliberately sits after the frozen DOM-first Owner shadow and
before semantic extraction.  It does not replace ``agents.identity.production``
or ``agents.owner.generic``.  Its job is to distinguish a document-defined
term from a target SQL feature using deterministic structural evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from agents.contracts.base import stable_id
from agents.contracts.target_feature_evidence import (
    EVIDENCE_STRENGTHS,
    TARGET_TYPES,
    make_target_feature_evidence,
)
from agents.identity.production import build_identity_occurrences


SCHEMA_VERSION = "mysql-identity-shadow-v2/v1"
TARGET_SCOPE = list(TARGET_TYPES)

_CALL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n()]*\)$")
_SYMBOL_RE = re.compile(r"^[+*/<>=!|&^~%?-]{1,3}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_EXAMPLE_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|EXPLAIN|SHOW|SET)\b|"
    r"(?:mysql>|psql>|duckdb>|sqlite>|clickhouse>|postgres(?:ql)?>)",
    re.I,
)
_TYPE_CONTEXT_RE = re.compile(
    r"\b(?:data\s+type|datatype|type\s+(?:name|declaration|definition|syntax)|"
    r"column\s+(?:type|definition)|storage\s+class|type\s+affinity|"
    r"numeric\s+types?|string\s+types?|date\s+and\s+time\s+types?|"
    r"spatial\s+types?|character\s+types?)\b",
    re.I,
)
_FUNCTION_CONTEXT_RE = re.compile(
    r"\b(?:built[- ]in\s+functions?|function(?:s|\s+reference)?|routine(?:s)?|"
    r"callable|signature|function\s+name)\b",
    re.I,
)
_OPERATOR_CONTEXT_RE = re.compile(
    r"\b(?:operator(?:s)?|precedence|predicate(?:s)?|expression(?:s)?|"
    r"comparison|logical|non[- ]typed)\b",
    re.I,
)
_STATEMENT_CONTEXT_RE = re.compile(
    r"\b(?:statement(?:s)?|command(?:s)?|clause(?:s)?|DDL|DML|SQL\s+syntax|"
    r"ALTER\s+TABLE|CREATE\s+TABLE|SET\s+OPTION)\b",
    re.I,
)
_CONFIG_CONTEXT_RE = re.compile(
    r"\b(?:configuration|config(?:uration)?\s+option|server\s+option|"
    r"system\s+variable|setting(?:s)?|variable(?:s)?|parameter(?:s)?)\b",
    re.I,
)
_API_CONTEXT_RE = re.compile(
    r"\b(?:API|protocol|connector|client|driver|C\s+API|wire\s+protocol)\b",
    re.I,
)
_EXPRESSION_SYNTAX_RE = re.compile(
    r"\b(?:CASE\b.*\bWHEN\b.*\bTHEN\b|"
    r"[A-Za-z_][A-Za-z0-9_$]*\s+IS\s+(?:NOT\s+)?(?:NULL|TRUE|FALSE|UNKNOWN)|"
    r"[A-Za-z_][A-Za-z0-9_$]*\s+IN\s*\(|"
    r"[A-Za-z_][A-Za-z0-9_$]*\s+(?:NOT\s+)?LIKE\s+|"
    r"[A-Za-z_][A-Za-z0-9_$]*\s+BETWEEN\s+.*\s+AND\s+)",
    re.I | re.S,
)
_EXPRESSION_IDENTITIES = {"CASE", "IS", "IN", "LIKE", "BETWEEN"}
_TARGET_HEADER_WORDS = {
    "FUNCTION", "FUNCTIONS", "OPERATOR", "OPERATORS", "DATATYPE", "DATATYPES",
    "TYPE", "TYPES", "DATA", "NAME", "NAMES", "SYNTAX", "SIGNATURE",
    "SIGNATURES", "EXPRESSION", "EXPRESSIONS", "PREDICATE", "PREDICATES",
    "DECLARATION", "DECLARATIONS",
}
_SUPPORT_HEADER_WORDS = {
    "DESCRIPTION", "DESCRIPTIONS", "PARAMETER", "PARAMETERS", "ARGUMENT",
    "ARGUMENTS", "FIELD", "FIELDS", "COLUMN", "COLUMNS", "DEFAULT", "VALUE",
    "VALUES", "EXAMPLE", "EXAMPLES", "OPTION", "OPTIONS", "VARIABLE", "VARIABLES",
}
_CONFIG_WORDS = {"CONFIGURATION", "OPTION", "OPTIONS", "VARIABLE", "SETTING", "SETTINGS"}
_ORDINARY_STOP = {
    "A", "AN", "AND", "AS", "AT", "BY", "FOR", "FROM", "IN", "IS", "OF", "ON", "OR",
    "THE", "TO", "WITH", "FUNCTION", "FUNCTIONS", "OPERATOR", "OPERATORS", "TYPE", "TYPES",
    "DATA", "EXAMPLE", "EXAMPLES", "DESCRIPTION", "RETURN", "RETURNS", "VALUE", "VALUES",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _words(value: Any) -> set[str]:
    return {word.upper() for word in _WORD_RE.findall(_text(value))}


def _is_callable(value: Any) -> bool:
    return bool(_CALL_RE.fullmatch(_text(value)))


def _is_symbol(value: Any) -> bool:
    return bool(_SYMBOL_RE.fullmatch(_text(value)))


def _is_identifier(value: Any) -> bool:
    text = _text(value)
    return bool(_IDENTIFIER_RE.fullmatch(text)) and text.upper() not in _ORDINARY_STOP


def _context_for_occurrence(
    occurrence: Mapping[str, Any],
    block: Mapping[str, Any] | None,
    owner: Mapping[str, Any] | None,
    page: Mapping[str, Any] | None,
) -> dict[str, Any]:
    block = block or {}
    owner = owner or {}
    page = page or {}
    metadata = block.get("table_metadata") or {}
    headers = [_text(value) for value in metadata.get("headers") or [] if _text(value)]
    cells = [_text(value) for value in metadata.get("cells") or [] if _text(value)]
    ancestry = [_text(value) for value in block.get("heading_ancestry") or [] if _text(value)]
    source_context = occurrence.get("source_context") or {}
    if not headers:
        headers = [_text(value) for value in source_context.get("table_headers") or [] if _text(value)]
    if not ancestry:
        ancestry = [_text(value) for value in source_context.get("heading_ancestry") or [] if _text(value)]
    page_text = " ".join(
        [
            _text(page.get("url")),
            *ancestry,
            _text(block.get("text")),
            " ".join(headers),
            " ".join(cells),
        ]
    )
    return {
        "block": block,
        "owner": owner,
        "page": page,
        "headers": headers,
        "cells": cells,
        "ancestry": ancestry,
        "block_kind": str(block.get("block_kind") or source_context.get("block_kind") or ""),
        "owner_kind": str(owner.get("candidate_kind") or owner.get("owner_kind") or source_context.get("owner_kind") or ""),
        "template": str(owner.get("template") or source_context.get("template") or ""),
        "page_text": page_text,
        "heading_text": " ".join(ancestry),
        "url_text": _text(page.get("url")),
    }


def _is_table_header_row(context: Mapping[str, Any], identity: str) -> bool:
    if str(context.get("block_kind") or "").upper() != "TABLE_ROW":
        return False
    headers = [value.casefold() for value in context.get("headers") or [] if value]
    cells = [value.casefold() for value in context.get("cells") or [] if value]
    if not headers or not cells:
        return False
    if len(cells) >= len(headers) and all(cells[index] == headers[index] for index in range(len(headers))):
        return True
    return identity.casefold() in headers and len(cells) <= len(headers)


def _is_example_occurrence(occurrence: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if str(occurrence.get("occurrence_role") or "") == "EXAMPLE_TOKEN":
        return True
    if str(context.get("block_kind") or "").upper() != "CODE":
        return False
    evidence = _text(occurrence.get("raw_evidence"))
    ancestry = _text(context.get("heading_text"))
    return bool(_EXAMPLE_RE.search(evidence) or re.search(r"\b(?:example|usage|invocation)\b", ancestry, re.I))


def _is_glossary_context(context: Mapping[str, Any]) -> bool:
    return bool(re.search(r"\bglossary\b", f"{context.get('url_text', '')} {context.get('heading_text', '')}", re.I))


def _has_type_context(context: Mapping[str, Any]) -> bool:
    text = f"{context.get('page_text', '')} {context.get('heading_text', '')}"
    if _TYPE_CONTEXT_RE.search(text):
        return True
    header_words = set().union(*(_words(value) for value in context.get("headers") or [])) if context.get("headers") else set()
    return bool(header_words & {"DATATYPE", "DATATYPES", "TYPE", "TYPES", "DECLARATION", "DECLARATIONS"})


def _has_function_context(context: Mapping[str, Any]) -> bool:
    return bool(_FUNCTION_CONTEXT_RE.search(f"{context.get('page_text', '')} {context.get('heading_text', '')}"))


def _has_operator_context(context: Mapping[str, Any]) -> bool:
    return bool(_OPERATOR_CONTEXT_RE.search(f"{context.get('page_text', '')} {context.get('heading_text', '')}"))


def _has_statement_context(context: Mapping[str, Any]) -> bool:
    return bool(_STATEMENT_CONTEXT_RE.search(f"{context.get('page_text', '')} {context.get('heading_text', '')}"))


def _has_config_context(context: Mapping[str, Any]) -> bool:
    return bool(_CONFIG_CONTEXT_RE.search(f"{context.get('page_text', '')} {context.get('heading_text', '')}"))


def _has_api_context(context: Mapping[str, Any]) -> bool:
    return bool(_API_CONTEXT_RE.search(f"{context.get('page_text', '')} {context.get('heading_text', '')}"))


def _explicit_expression_evidence(identity: str, context: Mapping[str, Any]) -> bool:
    text = f"{context.get('page_text', '')} {context.get('heading_text', '')}"
    if identity == "CASE":
        return bool(re.search(r"\bCASE\b.*\bWHEN\b.*\bTHEN\b", text, re.I | re.S))
    if identity == "IS":
        return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*\s+IS\s+(?:NOT\s+)?(?:NULL|TRUE|FALSE|UNKNOWN)\b", text, re.I))
    if identity == "IN":
        return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*\s+IN\s*\(", text, re.I))
    if identity == "LIKE":
        return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*\s+(?:NOT\s+)?LIKE\s+", text, re.I))
    if identity == "BETWEEN":
        return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*\s+BETWEEN\s+.*\s+AND\s+", text, re.I | re.S))
    return bool(_EXPRESSION_SYNTAX_RE.search(text))


def _add_evidence(
    evidence: dict[str, list[dict[str, Any]]],
    feature_type: str,
    strength: str,
    reason: str,
    source: str,
) -> None:
    if feature_type in TARGET_TYPES and strength in EVIDENCE_STRENGTHS and strength != "NONE":
        evidence[feature_type].append({"strength": strength, "reason": reason, "source": source})


def _non_target(code: str, reason: str, source: str) -> dict[str, str]:
    return {"code": code, "reason": reason, "source": source}


def classify_target_evidence(
    occurrence: Mapping[str, Any],
    *,
    block: Mapping[str, Any] | None = None,
    owner: Mapping[str, Any] | None = None,
    page: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one occurrence as document-defining and/or target-defining.

    This is deliberately conservative: a complete definition entry alone is
    not target evidence, and profile/discovery hints are never sufficient.
    """
    identity = _text(occurrence.get("normalized_identity") or occurrence.get("raw_identity")).upper()
    raw = _text(occurrence.get("raw_identity") or identity)
    context = _context_for_occurrence(occurrence, block, owner, page)
    role = str(occurrence.get("occurrence_role") or "UNKNOWN")
    source_type = str(occurrence.get("source_type") or "")
    header_words = set().union(*(_words(value) for value in context.get("headers") or [])) if context.get("headers") else set()
    example_only = _is_example_occurrence(occurrence, context)
    header_only = _is_table_header_row(context, identity)
    document_defining = bool(occurrence.get("defining")) or role in {"DEFINING_IDENTITY", "FORMAL_VARIANT"}
    if example_only or header_only or role in {"MENTION", "PARAMETER_OR_FIELD", "OUTPUT_TEXT", "CROSS_REFERENCE"}:
        document_defining = False

    evidence: dict[str, list[dict[str, Any]]] = {name: [] for name in TARGET_TYPES}
    reasons: list[str] = []
    non_target: list[dict[str, str]] = []
    callable_shape = _is_callable(raw)
    symbol_shape = _is_symbol(raw)
    identifier_shape = _is_identifier(raw)
    explicit_expression = _explicit_expression_evidence(identity, context) if identity in _EXPRESSION_IDENTITIES else False
    catalog_surface = (
        str(context.get("block_kind") or "").upper() in {"TABLE_ROW", "DEFINITION", "HEADING", "LIST_ITEM"}
        and (str(occurrence.get("surface_role") or "") in {"FEATURE_CATALOG", "FEATURE_DEFINITION"} or bool(header_words & _TARGET_HEADER_WORDS))
    )

    if not example_only and not header_only and document_defining:
        if callable_shape:
            _add_evidence(evidence, "FUNCTION", "STRONG", "standalone_callable_signature", source_type or "identity")
            reasons.append("CALLABLE_SIGNATURE")
            if catalog_surface:
                _add_evidence(evidence, "FUNCTION", "STRONG", "function_catalog_or_definition_surface", "structure")
        elif symbol_shape:
            if _has_operator_context(context) or catalog_surface:
                _add_evidence(evidence, "OPERATOR", "STRONG", "symbolic_operator_definition", source_type or "identity")
                reasons.append("SYMBOLIC_OPERATOR")
        elif identifier_shape or identity in _EXPRESSION_IDENTITIES:
            if identity in _EXPRESSION_IDENTITIES and (explicit_expression or _has_operator_context(context)):
                strength = "STRONG" if explicit_expression else "MEDIUM"
                _add_evidence(evidence, "OPERATOR", strength, "expression_operator_syntax" if explicit_expression else "operator_reference_context", "structure")
                reasons.append("EXPRESSION_OPERATOR")
            if _has_type_context(context) and not _has_function_context(context):
                strength = "STRONG" if bool(header_words & {"DATATYPE", "DATATYPES", "DECLARATION", "DECLARATIONS"}) or _TYPE_CONTEXT_RE.search(context.get("page_text", "")) else "MEDIUM"
                _add_evidence(evidence, "DATATYPE", strength, "datatype_declaration_or_type_context", "structure")
                reasons.append("DATATYPE_CONTEXT")
            if _has_function_context(context) and catalog_surface and identity not in _EXPRESSION_IDENTITIES:
                _add_evidence(evidence, "FUNCTION", "MEDIUM", "function_catalog_label", "structure")
                reasons.append("FUNCTION_CATALOG")

    if not evidence["OPERATOR"] and symbol_shape and not example_only and not header_only:
        if _has_operator_context(context):
            _add_evidence(evidence, "OPERATOR", "MEDIUM", "operator_reference_context", "structure")
            reasons.append("OPERATOR_CONTEXT")

    if example_only:
        non_target.append(_non_target("EXAMPLE_ONLY", "example invocation or code token", "occurrence"))
        reasons.append("EXAMPLE_ONLY")
    if header_only:
        non_target.append(_non_target("TABLE_HEADER_OR_LABEL", "table header row or label", "table_structure"))
        reasons.append("TABLE_HEADER_OR_LABEL")
    if role in {"PARAMETER_OR_FIELD", "OUTPUT_TEXT"}:
        non_target.append(_non_target("PARAMETER_OR_FIELD", "parameter, field, or output mention", "occurrence_role"))
        reasons.append("NON_DEFINING_OCCURRENCE")
    if role in {"MENTION", "CROSS_REFERENCE"}:
        non_target.append(_non_target("ORDINARY_MENTION", "mention or cross-reference only", "occurrence_role"))
        reasons.append("NON_DEFINING_OCCURRENCE")

    if not any(evidence.values()):
        if _is_glossary_context(context):
            non_target.append(_non_target("GLOSSARY_TERM", "definition entry in glossary context without target syntax", "document_context"))
            reasons.append("DOCUMENT_TERM_ONLY")
        elif _has_config_context(context):
            non_target.append(_non_target("CONFIGURATION_TERM", "configuration/setting context without target syntax", "document_context"))
            reasons.append("NON_TARGET_CONFIGURATION")
        elif _has_api_context(context):
            non_target.append(_non_target("API_OR_PROTOCOL_TERM", "API/protocol context without target syntax", "document_context"))
            reasons.append("NON_TARGET_API_OR_PROTOCOL")
        elif _has_statement_context(context):
            non_target.append(_non_target("SQL_STATEMENT_OR_CLAUSE", "statement/command/clause context", "document_context"))
            reasons.append("NON_TARGET_STATEMENT")
        elif str(context.get("block_kind") or "").upper() == "HEADING" or source_type in {"heading", "table_row"}:
            non_target.append(_non_target("HEADING_OR_LABEL", "heading or structural label without target evidence", "surface"))
            reasons.append("HEADING_OR_LABEL")
        elif document_defining:
            non_target.append(_non_target("NARRATIVE_TERM", "document-defined prose term without target evidence", "document_context"))
            reasons.append("DOCUMENT_TERM_ONLY")
        else:
            non_target.append(_non_target("ORDINARY_NOUN", "non-defining ordinary mention", "occurrence"))

    strength_rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1}
    max_strength = max(
        (strength_rank.get(str(item.get("strength")), 0) for values in evidence.values() for item in values),
        default=0,
    )
    strength = {3: "STRONG", 2: "MEDIUM", 1: "WEAK"}.get(max_strength, "NONE")
    target_types = sorted({feature_type for feature_type, values in evidence.items() if values})
    target_plausible = bool(target_types) and not example_only and not header_only
    target_defining = bool(target_plausible and document_defining)
    if target_defining:
        reasons.append("TARGET_FEATURE_EVIDENCE")
    else:
        reasons.append("NO_TARGET_FEATURE_EVIDENCE")

    provenance = {
        "dbms": str((page or {}).get("dbms") or "mysql"),
        "source_url": str((page or {}).get("url") or occurrence.get("source_url") or ""),
        "page_id": str((page or {}).get("page_id") or occurrence.get("page_id") or ""),
        "page_hash": str((page or {}).get("page_hash") or ""),
        "html_path": str((page or {}).get("html_path") or ""),
        "owner_id": str(occurrence.get("owner_id") or ""),
        "source_block_id": str(occurrence.get("source_block_id") or ""),
        "producer_stage": "target_feature_evidence_shadow",
        "producer_version": SCHEMA_VERSION,
        "parent_ids": [value for value in [occurrence.get("occurrence_id"), occurrence.get("owner_id"), occurrence.get("source_block_id")] if value],
        "reason_codes": sorted(set(reasons)),
    }
    return make_target_feature_evidence(
        document_defining=document_defining,
        target_feature_defining=target_defining,
        target_feature_plausible=target_plausible,
        target_types=target_types,
        evidence_by_type=evidence,
        non_target_evidence=non_target,
        strength=strength,
        reason_codes=reasons,
        provenance=provenance,
    )


def classify_root_cause(evidence: Mapping[str, Any], occurrence: Mapping[str, Any] | None = None) -> str:
    """Map a no/weak-target-evidence case to an audit bucket."""
    non_target = {str(item.get("code") or "") for item in evidence.get("non_target_evidence") or []}
    if "GLOSSARY_TERM" in non_target:
        return "GLOSSARY_TERM"
    if "CONFIGURATION_TERM" in non_target:
        return "CONFIGURATION_TERM"
    if "API_OR_PROTOCOL_TERM" in non_target:
        return "API_OR_PROTOCOL_TERM"
    if "SQL_STATEMENT_OR_CLAUSE" in non_target:
        return "SQL_STATEMENT_OR_CLAUSE"
    if "TABLE_HEADER_OR_LABEL" in non_target or "HEADING_OR_LABEL" in non_target:
        return "HEADING_OR_LABEL"
    if "EXAMPLE_ONLY" in non_target:
        return "NARRATIVE_TERM"
    targets = set(str(value) for value in evidence.get("target_types") or [])
    if targets == {"FUNCTION"}:
        return "FUNCTION_LIKE_IDENTITY"
    if targets == {"OPERATOR"}:
        return "OPERATOR_LIKE_IDENTITY"
    if targets == {"DATATYPE"}:
        return "DATATYPE_LIKE_IDENTITY"
    if targets:
        return "OTHER"
    if occurrence and str(occurrence.get("surface_role") or "") in {"FEATURE_CATALOG", "FEATURE_DEFINITION"}:
        return "CATALOG_IDENTITY"
    if "NARRATIVE_TERM" in non_target or "DOCUMENT_TERM_ONLY" in evidence.get("reason_codes", []):
        return "NARRATIVE_TERM"
    return "OTHER"


def _best_strength(values: Iterable[Mapping[str, Any]]) -> str:
    rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1, "NONE": 0}
    best = max((str(value.get("strength") or "NONE") for value in values), key=lambda item: rank.get(item, 0), default="NONE")
    return best


def _aggregate_group(
    identity: str,
    occurrences: list[dict[str, Any]],
    *,
    group_type: str | None = None,
) -> dict[str, Any]:
    evidence_values = [row["target_feature_evidence"] for row in occurrences]
    type_values: dict[str, list[dict[str, Any]]] = {name: [] for name in TARGET_TYPES}
    for item in evidence_values:
        for feature_type in TARGET_TYPES:
            type_values[feature_type].extend(item.get("evidence_by_type", {}).get(feature_type, []))
    strength_rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1, "NONE": 0}
    type_scores = {
        feature_type: sum(strength_rank.get(str(item.get("strength") or "NONE"), 0) for item in values)
        for feature_type, values in type_values.items()
    }
    strong_types = sorted(feature_type for feature_type, values in type_values.items() if any(item.get("strength") == "STRONG" for item in values))
    medium_types = sorted(feature_type for feature_type, values in type_values.items() if any(item.get("strength") == "MEDIUM" for item in values))
    weak_types = sorted(feature_type for feature_type, values in type_values.items() if any(item.get("strength") == "WEAK" for item in values))
    if group_type and group_type in TARGET_TYPES:
        selected_types = [group_type]
    elif len(strong_types) == 1:
        selected_types = strong_types
    elif len(strong_types) > 1:
        selected_types = [max(strong_types, key=lambda value: (type_scores[value], value))]
    elif len(medium_types) == 1:
        selected_types = medium_types
    elif len(medium_types) > 1:
        selected_types = [max(medium_types, key=lambda value: (type_scores[value], value))]
    elif len(weak_types) == 1:
        selected_types = weak_types
    else:
        selected_types = []

    document_defining = any(item.get("document_defining") for item in evidence_values)
    target_plausible = bool(selected_types)
    if not target_plausible:
        final_type = "UNCERTAIN"
        status = "NON_FEATURE_ONLY"
    elif len(strong_types) > 1 and not group_type:
        final_type = "UNCERTAIN"
        status = "UNCERTAIN_FEATURE"
    elif strong_types:
        final_type = selected_types[0]
        status = "CONFIRMED_FEATURE"
    elif medium_types:
        final_type = selected_types[0]
        status = "PROBABLE_FEATURE"
    else:
        final_type = selected_types[0]
        status = "UNCERTAIN_FEATURE"
    if not document_defining and status != "NON_FEATURE_ONLY":
        status = "NON_FEATURE_ONLY"
        final_type = "UNCERTAIN"
        target_plausible = False

    non_target = []
    for item in evidence_values:
        non_target.extend(item.get("non_target_evidence") or [])
    non_target = sorted({(str(item.get("code") or ""), str(item.get("reason") or ""), str(item.get("source") or "")) for item in non_target})
    reasons = sorted({str(code) for item in evidence_values for code in item.get("reason_codes") or []})
    if status == "NON_FEATURE_ONLY" and "DOCUMENT_TERM_ONLY" not in reasons:
        reasons.append("NO_TARGET_FEATURE_EVIDENCE")
    target_feature_defining = bool(status in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE", "UNCERTAIN_FEATURE"} and document_defining)
    evidence_rows = []
    for occurrence in occurrences:
        evidence_rows.append({
            "occurrence_id": occurrence.get("occurrence_id"),
            "owner_id": occurrence.get("owner_id"),
            "source_url": occurrence.get("source_url"),
            "source_block_id": occurrence.get("source_block_id"),
            "raw_identity": occurrence.get("raw_identity"),
            "raw_evidence": occurrence.get("raw_evidence"),
            "document_defining": occurrence.get("target_feature_evidence", {}).get("document_defining", False),
            "target_feature_defining": occurrence.get("target_feature_evidence", {}).get("target_feature_defining", False),
            "target_feature_evidence": occurrence.get("target_feature_evidence"),
        })
    source_urls = sorted({str(row.get("source_url") or "") for row in occurrences if row.get("source_url")})
    owner_ids = sorted({str(row.get("owner_id") or "") for row in occurrences if row.get("owner_id")})
    variants = sorted({str(row.get("raw_identity") or "") for row in occurrences if row.get("raw_identity")})
    defining_variants = sorted({
        str(row.get("raw_identity") or "") for row in occurrences
        if row.get("target_feature_evidence", {}).get("document_defining") and row.get("raw_identity")
    })
    unit_type = group_type or final_type
    return {
        "schema_version": "IdentityUnit/target-shadow-v1",
        "identity_unit_id": stable_id("identity_shadow_unit", [identity, unit_type]),
        "canonical_identity": identity,
        "group_key": f"{identity}|{unit_type}",
        "feature_type": final_type,
        "type_hint": final_type,
        "group_status": status,
        "semantic_ready": status != "NON_FEATURE_ONLY",
        "document_defining": document_defining,
        "target_feature_defining": target_feature_defining,
        "target_feature_plausible": target_plausible,
        "target_type_scores": type_scores,
        "target_type_evidence": {name: values for name, values in type_values.items()},
        "target_evidence_strength": _best_strength(item for values in type_values.values() for item in values),
        "non_target_evidence": [
            {"code": code, "reason": reason, "source": source}
            for code, reason, source in non_target
        ],
        "reason_codes": reasons,
        "owner_ids": owner_ids,
        "source_urls": source_urls,
        "variants": variants,
        "defining_variants": defining_variants,
        "occurrence_ids": sorted({str(row.get("occurrence_id") or "") for row in occurrences if row.get("occurrence_id")}),
        "occurrence_roles": dict(sorted(Counter(str(row.get("occurrence_role") or "UNKNOWN") for row in occurrences).items())),
        "evidence": evidence_rows,
        "provenance": {
            "dbms": "mysql",
            "source_urls": source_urls,
            "owner_ids": owner_ids,
            "parent_ids": sorted({
                value for row in occurrences for value in (row.get("occurrence_id"), row.get("owner_id"), row.get("source_block_id")) if value
            }),
            "producer_stage": "target_identity_shadow",
            "producer_version": SCHEMA_VERSION,
            "reason_codes": reasons,
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _status_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("group_status") or "UNKNOWN") for row in rows).items()))


def _type_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("feature_type") or row.get("type_hint") or "UNCERTAIN") for row in rows).items()))


def _best_group(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    priority = {"CONFIRMED_FEATURE": 4, "PROBABLE_FEATURE": 3, "UNCERTAIN_FEATURE": 2, "NON_FEATURE_ONLY": 1, "CONFLICT": 0}
    values = list(rows)
    if not values:
        return None
    return sorted(values, key=lambda row: (-priority.get(str(row.get("group_status")), -1), str(row.get("feature_type")), str(row.get("identity_unit_id"))))[0]


def _occurrence_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row.get("normalized_identity") or "").upper()].append(dict(row))
    return result


def _first_evidence(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return dict(rows[0].get("target_feature_evidence") or {})


def _root_cause_row(identity: str, v1_group: Mapping[str, Any] | None, occurrences: list[Mapping[str, Any]], refined: Mapping[str, Any] | None) -> dict[str, Any]:
    first = dict(occurrences[0]) if occurrences else {}
    evidence = _first_evidence(occurrences)
    categories = Counter(classify_root_cause(row.get("target_feature_evidence") or {}, row) for row in occurrences)
    root = categories.most_common(1)[0][0] if categories else "OTHER"
    source_contexts = [row.get("source_context") or {} for row in occurrences]
    profile_hints = [context.get("profile_hint") or {} for context in source_contexts]
    return {
        "identity": identity,
        "v1_status": (v1_group or {}).get("group_status", "UNCERTAIN_FEATURE"),
        "v1_type": (v1_group or {}).get("feature_type", "UNCERTAIN"),
        "refined_status": (refined or {}).get("group_status"),
        "refined_type": (refined or {}).get("feature_type"),
        "root_cause": root,
        "root_cause_votes": dict(sorted(categories.items())),
        "target_evidence_present": any(bool(row.get("target_feature_evidence", {}).get("target_types")) for row in occurrences),
        "target_types": sorted({value for row in occurrences for value in row.get("target_feature_evidence", {}).get("target_types", [])}),
        "document_defining": any(bool(row.get("target_feature_evidence", {}).get("document_defining")) for row in occurrences),
        "owner_ids": sorted({str(row.get("owner_id") or "") for row in occurrences if row.get("owner_id")}),
        "owner_kinds": sorted({str(context.get("owner_kind") or "UNKNOWN") for context in source_contexts}),
        "page_structural_roles": sorted({str(row.get("surface_role") or profile.get("surface_role") or "UNKNOWN") for row, profile in zip(occurrences, profile_hints)}),
        "source_types": sorted({str(row.get("source_type") or "UNKNOWN") for row in occurrences}),
        "identity_signals": sorted({str(value) for row in occurrences for value in row.get("identity_signals") or []}),
        "defining_flags": sorted({bool(row.get("defining")) for row in occurrences}),
        "type_hints": sorted({str(row.get("type_hint") or profile.get("type_hint") or "UNKNOWN") for row, profile in zip(occurrences, profile_hints)}),
        "type_evidence": [row.get("type_evidence") or [] for row in occurrences],
        "v1_admission_reasons": list((v1_group or {}).get("merge_decisions") or []) + list((v1_group or {}).get("reconciliation_reason") and [{"reason": (v1_group or {}).get("reconciliation_reason")}] or []),
        "source_urls": sorted({str(row.get("source_url") or "") for row in occurrences if row.get("source_url")}),
        "sample_raw_evidence": first.get("raw_evidence", ""),
        "sample_target_evidence": evidence,
    }


def _supplement_expression_occurrences(
    documents: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Recover bounded SQL expression labels skipped by the old stopword set.

    This is a shadow-only generic primitive.  It reads only the selected
    Owner's bounded structural surface; it never scans arbitrary prose and it
    does not use a database or feature-name allowlist.
    """
    pages = {str(page.get("page_id")): page for page in documents}
    blocks_by_page = {
        str(page.get("page_id")): {str(block.get("block_id")): block for block in page.get("blocks") or []}
        for page in documents
    }
    existing_keys = {
        (
            str(row.get("owner_id") or ""),
            str(row.get("source_block_id") or ""),
            str(row.get("normalized_identity") or "").upper(),
        )
        for row in existing
    }
    additions: list[dict[str, Any]] = []
    for owner in owners:
        page_id = str(owner.get("page_id") or "")
        page = pages.get(page_id)
        if not page:
            continue
        for block_id in owner.get("member_block_ids") or []:
            block = blocks_by_page.get(page_id, {}).get(str(block_id))
            if not block:
                continue
            context = _context_for_occurrence(
                {
                    "source_context": {
                        "block_kind": block.get("block_kind"),
                        "table_headers": (block.get("table_metadata") or {}).get("headers") or [],
                        "heading_ancestry": block.get("heading_ancestry") or [],
                    }
                },
                block,
                owner,
                page,
            )
            kind = str(block.get("block_kind") or "").upper()
            labels: list[str] = []
            if kind == "TABLE_ROW":
                cells = [_text(value) for value in (block.get("table_metadata") or {}).get("cells") or [] if _text(value)]
                if cells:
                    labels = [cells[0]]
            elif kind in {"HEADING", "DEFINITION", "LIST_ITEM"}:
                labels = [_text(block.get("text"))]
            for raw in labels:
                identity = raw.upper()
                if identity not in _EXPRESSION_IDENTITIES or _is_table_header_row(context, identity):
                    continue
                key = (str(owner.get("owner_id") or ""), str(block_id), identity)
                if key in existing_keys:
                    continue
                source_type = {
                    "TABLE_ROW": "table_row",
                    "HEADING": "heading",
                    "DEFINITION": "definition_list",
                    "LIST_ITEM": "list_item",
                }.get(kind, "structural_surface")
                occurrence_id = stable_id(
                    "identity_occurrence_shadow",
                    [owner.get("owner_id"), page_id, block_id, identity, "expression_label"],
                )
                additions.append({
                    "schema_version": "IdentityOccurrence/target-shadow-v1",
                    "occurrence_id": occurrence_id,
                    "raw_identity": raw,
                    "normalized_identity": identity,
                    "owner_id": str(owner.get("owner_id") or ""),
                    "page_id": page_id,
                    "source_url": str(page.get("url") or ""),
                    "source_block_id": str(block_id),
                    "source_type": source_type,
                    "raw_evidence": str(block.get("text") or raw),
                    "identity_signals": [source_type, "IDENTITY_SHAPED_SURFACE", "SQL_EXPRESSION_CONSTRUCT", "DEFINING_SURFACE"],
                    "type_hint": "UNCERTAIN",
                    "occurrence_role": "DEFINING_IDENTITY",
                    "defining": True,
                    "surface_role": "FEATURE_CATALOG" if kind == "TABLE_ROW" else "FEATURE_DEFINITION",
                    "surface_confidence": 0.85,
                    "surface_confidence_label": "HIGH",
                    "surface_evidence": ["bounded_expression_label", "selected_owner_surface"],
                    "feature_evidence": ["expression_construct_label"],
                    "type_evidence": [],
                    "source_context": {
                        "owner_kind": str(owner.get("candidate_kind") or owner.get("owner_kind") or ""),
                        "template": str(owner.get("template") or ""),
                        "profile_hint": dict(owner.get("profile_hint") or {}),
                        "table_headers": list((block.get("table_metadata") or {}).get("headers") or []),
                        "heading_ancestry": list(block.get("heading_ancestry") or []),
                        "block_kind": kind,
                    },
                    "provenance": {
                        "dbms": str(page.get("dbms") or "mysql"),
                        "source_url": str(page.get("url") or ""),
                        "page_id": page_id,
                        "page_hash": str(page.get("page_hash") or ""),
                        "html_path": str(page.get("html_path") or ""),
                        "owner_id": str(owner.get("owner_id") or ""),
                        "source_block_id": str(block_id),
                        "discovery": dict(page.get("discovery_provenance") or {}),
                    },
                })
                existing_keys.add(key)
    return existing + additions, len(additions)


def _compare_snapshots(old: list[dict[str, Any]], v1: list[dict[str, Any]], refined: list[dict[str, Any]]) -> dict[str, Any]:
    def by_identity(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            identity = str(row.get("canonical_identity") or row.get("normalized_identity") or "").upper()
            if not identity:
                continue
            current = result.get(identity)
            if current is None or _best_group([current, dict(row)]) is row:
                result[identity] = dict(row)
            elif current.get("group_status") == "NON_FEATURE_ONLY" and row.get("group_status") != "NON_FEATURE_ONLY":
                result[identity] = dict(row)
        return result

    old_by = by_identity(old)
    v1_by = by_identity(v1)
    refined_by = by_identity(refined)
    identities = sorted(set(old_by) | set(v1_by) | set(refined_by))
    records = []
    type_corrections = []
    for identity in identities:
        old_row, v1_row, refined_row = old_by.get(identity), v1_by.get(identity), refined_by.get(identity)
        old_type = (old_row or {}).get("feature_type") or (old_row or {}).get("type_hint")
        v1_type = (v1_row or {}).get("feature_type") or (v1_row or {}).get("type_hint")
        refined_type = (refined_row or {}).get("feature_type") or (refined_row or {}).get("type_hint")
        record = {
            "identity": identity,
            "old": {"type": old_type, "status": (old_row or {}).get("group_status")},
            "owner_shadow_v1": {"type": v1_type, "status": (v1_row or {}).get("group_status")},
            "identity_refined_shadow": {"type": refined_type, "status": (refined_row or {}).get("group_status")},
            "type_correction": bool(v1_type and refined_type and v1_type != refined_type),
            "status_change_from_v1": bool((v1_row or {}).get("group_status") != (refined_row or {}).get("group_status")),
            "target_evidence": (refined_row or {}).get("target_type_evidence", {}),
            "reason_codes": (refined_row or {}).get("reason_codes", []),
        }
        records.append(record)
        if record["type_correction"]:
            type_corrections.append(record)
    return {
        "schema_version": "mysql-identity-shadow-comparison/v2",
        "old_production": {"units": len(old), "status": _status_counts(old), "types": _type_counts(old)},
        "owner_shadow_v1": {"units": len(v1), "status": _status_counts(v1), "types": _type_counts(v1)},
        "identity_refined_shadow": {"units": len(refined), "status": _status_counts(refined), "types": _type_counts(refined)},
        "identity_records": records,
        "type_corrections": type_corrections,
        "type_correction_count": len(type_corrections),
        "status_changes_from_v1": [row for row in records if row["status_change_from_v1"]],
        "status_changes_from_v1_count": sum(row["status_change_from_v1"] for row in records),
    }


def _old_confirmed_risk_audit(
    old_units: list[dict[str, Any]],
    refined: list[dict[str, Any]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    refined_by = defaultdict(list)
    for row in refined:
        refined_by[str(row.get("canonical_identity") or "").upper()].append(row)
    rows = []
    for old in old_units:
        if str(old.get("group_status") or "") != "CONFIRMED_FEATURE":
            continue
        identity = str(old.get("canonical_identity") or "").upper()
        current = _best_group(refined_by.get(identity, []))
        occurrences = occurrences_by_identity.get(identity, [])
        target_types = {value for row in occurrences for value in row.get("target_feature_evidence", {}).get("target_types", [])}
        variants = list(old.get("defining_variants") or [])
        reasons = []
        if not target_types:
            reasons.append("NO_CURRENT_TARGET_EVIDENCE")
        if not any(_is_callable(value) or _is_symbol(value) or str(value).upper() in _EXPRESSION_IDENTITIES for value in variants):
            reasons.append("NO_CALLABLE_OPERATOR_OR_DATATYPE_SHAPE")
        if all(str(row.get("occurrence_role") or "") in {"EXAMPLE_TOKEN", "MENTION", "UNKNOWN"} for row in occurrences) and occurrences:
            reasons.append("NON_DEFINING_CURRENT_OCCURRENCES")
        if current and current.get("group_status") == "NON_FEATURE_ONLY":
            reasons.append("REFINED_NON_FEATURE")
        if reasons:
            rows.append({
                "identity": identity,
                "old_type": old.get("feature_type") or old.get("type_hint"),
                "old_status": old.get("group_status"),
                "old_defining_variants": variants,
                "old_source_urls": list(old.get("source_urls") or []),
                "refined_type": (current or {}).get("feature_type"),
                "refined_status": (current or {}).get("group_status"),
                "risk_reasons": sorted(set(reasons)),
                "current_target_types": sorted(target_types),
                "current_evidence": [
                    {"source_url": row.get("source_url"), "raw_identity": row.get("raw_identity"), "raw_evidence": row.get("raw_evidence"), "target_feature_evidence": row.get("target_feature_evidence")}
                    for row in occurrences[:12]
                ],
            })
    return sorted(rows, key=lambda row: row["identity"])


def _audit_added_lost(
    v1_comparison: Mapping[str, Any],
    refined_by_identity: Mapping[str, list[dict[str, Any]]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def occurrence_dimensions(rows: list[Mapping[str, Any]]) -> dict[str, list[str]]:
        owner_kinds = set()
        page_roles = set()
        source_types = set()
        target_types = set()
        for item in rows:
            context = item.get("source_context") or {}
            profile = context.get("profile_hint") or {}
            owner_kinds.add(str(context.get("owner_kind") or "UNKNOWN"))
            page_roles.add(str(item.get("surface_role") or profile.get("surface_role") or "UNKNOWN"))
            source_types.add(str(item.get("source_type") or "UNKNOWN"))
            target_types.update(str(value) for value in item.get("target_feature_evidence", {}).get("target_types", []))
        return {
            "owner_kinds": sorted(owner_kinds),
            "page_structural_roles": sorted(page_roles),
            "source_types": sorted(source_types),
            "target_evidence_types": sorted(target_types),
        }

    added_rows = []
    for row in v1_comparison.get("added") or []:
        identity = str(row.get("identity") or "").upper()
        refined = _best_group(refined_by_identity.get(identity, []))
        occ = occurrences_by_identity.get(identity, [])
        target = bool(refined and refined.get("target_feature_plausible"))
        if target:
            classification = "CORRECTLY_VISIBLE_TARGET"
        elif row.get("status") == "UNCERTAIN_FEATURE":
            classification = "TARGET_ADMISSION_TOO_PERMISSIVE"
        else:
            classification = "NEW_NON_TARGET_OR_SUPPORT_TERM"
        dimensions = occurrence_dimensions(occ)
        added_rows.append({
            "identity": identity,
            "v1_status": row.get("status"),
            "v1_type": row.get("feature_type"),
            "refined_status": (refined or {}).get("group_status"),
            "refined_type": (refined or {}).get("feature_type"),
            "final_status": (refined or {}).get("group_status"),
            "classification": classification,
            **dimensions,
            "owner_ids": sorted({str(item.get("owner_id") or "") for item in occ if item.get("owner_id")}),
            "source_urls": sorted({str(item.get("source_url") or "") for item in occ if item.get("source_url")}),
            "target_evidence": (refined or {}).get("target_type_evidence", {}),
            "reason_codes": (refined or {}).get("reason_codes", []),
        })
    lost_rows = []
    for row in v1_comparison.get("lost") or []:
        identity = str(row.get("identity") or "").upper()
        refined = _best_group(refined_by_identity.get(identity, []))
        occ = occurrences_by_identity.get(identity, [])
        old_variants = list(row.get("defining_variants") or [])
        has_target = bool(refined and refined.get("target_feature_plausible"))
        old_target_shape = any(
            _is_callable(value) or _is_symbol(value) or str(value).upper() in _EXPRESSION_IDENTITIES
            for value in old_variants
        )
        if has_target:
            classification = "RECOVERED_BY_TARGET_TYPE_OR_IDENTITY_REFINEMENT"
        elif row.get("status") in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE"} and old_target_shape:
            classification = "TRUE_TARGET_POTENTIALLY_LOST"
        elif any(str(item.get("occurrence_role") or "") == "EXAMPLE_TOKEN" for item in occ):
            classification = "OLD_EXAMPLE_DERIVED_NOISE_REMOVED"
        elif old_variants and not any(_is_callable(value) or _is_symbol(value) or str(value).upper() in _EXPRESSION_IDENTITIES for value in old_variants):
            classification = "OLD_GENERIC_OR_WRONG_TYPE_REMOVED"
        elif not occ:
            classification = "NO_CURRENT_OWNER_OCCURRENCE"
        else:
            classification = "TRUE_TARGET_POTENTIALLY_LOST"
        dimensions = occurrence_dimensions(occ)
        lost_rows.append({
            "identity": identity,
            "old_status": row.get("status"),
            "old_type": row.get("feature_type"),
            "old_defining_variants": old_variants,
            "refined_status": (refined or {}).get("group_status"),
            "refined_type": (refined or {}).get("feature_type"),
            "final_status": (refined or {}).get("group_status"),
            "classification": classification,
            **dimensions,
            "owner_ids": sorted({str(item.get("owner_id") or "") for item in occ if item.get("owner_id")}),
            "source_urls": sorted({str(item.get("source_url") or "") for item in occ if item.get("source_url")}),
            "target_evidence": (refined or {}).get("target_type_evidence", {}),
            "reason_codes": (refined or {}).get("reason_codes", []),
        })
    return added_rows, lost_rows


def _status_change_audit(
    v1_comparison: Mapping[str, Any],
    refined_by_identity: Mapping[str, list[dict[str, Any]]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    changed = {(str(row.get("identity") or "").upper(), str(row.get("feature_type") or "UNCERTAIN")): row for row in v1_comparison.get("status_changed") or []}
    for (identity, feature_type), value in sorted(changed.items()):
        refined = _best_group(refined_by_identity.get(identity, []))
        occ = occurrences_by_identity.get(identity, [])
        rows.append({
            "identity": identity,
            "old_type": feature_type,
            "old_status": value.get("old_status"),
            "v1_type": feature_type,
            "v1_status": value.get("new_status"),
            "refined_type": (refined or {}).get("feature_type"),
            "refined_status": (refined or {}).get("group_status"),
            "old_defining_evidence": value.get("old_defining_evidence", []),
            "new_defining_evidence": [
                {"source_url": item.get("source_url"), "raw_identity": item.get("raw_identity"), "raw_evidence": item.get("raw_evidence"), "target_feature_evidence": item.get("target_feature_evidence")}
                for item in occ[:12]
            ],
            "target_feature_evidence_delta": (refined or {}).get("target_type_evidence", {}),
            "classification": "TARGET_EVIDENCE_REFINEMENT" if refined and refined.get("target_feature_plausible") else "DOCUMENT_TERM_RECLASSIFICATION",
        })
    return rows


def _dimension_counts(rows: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        values = row.get(field) or ["UNKNOWN"]
        for value in values:
            counts[str(value or "UNKNOWN")] += 1
    return dict(sorted(counts.items()))


def _render_evidence_section(identity: str, occurrences: list[Mapping[str, Any]], refined: Mapping[str, Any] | None, old: Mapping[str, Any] | None, v1: Mapping[str, Any] | None) -> list[str]:
    lines = [f"## {identity}", ""]
    lines.append(f"- Old Production: `{(old or {}).get('feature_type')}` / `{(old or {}).get('group_status')}`")
    lines.append(f"- Owner Shadow v1: `{(v1 or {}).get('feature_type')}` / `{(v1 or {}).get('group_status')}`")
    lines.append(f"- Refined Shadow: `{(refined or {}).get('feature_type')}` / `{(refined or {}).get('group_status')}`")
    lines.append("")
    if old and not occurrences:
        lines.extend([
            f"- Old defining variants: `{json.dumps(old.get('defining_variants', []), ensure_ascii=False)}`",
            f"- Old official source URLs: `{json.dumps(old.get('source_urls', []), ensure_ascii=False)}`",
            "- Current selected Owner evidence: **not present**; this is an upstream Owner/identity-surface loss and is not silently repaired here.",
            "",
        ])
    for row in occurrences[:12]:
        ev = row.get("target_feature_evidence") or {}
        lines.extend([
            f"### Owner `{row.get('owner_id')}` / `{row.get('source_url')}`",
            f"- Block: `{row.get('source_block_id')}`; source type: `{row.get('source_type')}`; occurrence role: `{row.get('occurrence_role')}`",
            f"- Identity: `{row.get('raw_identity')}`; document_defining: `{ev.get('document_defining')}`; target_feature_defining: `{ev.get('target_feature_defining')}`",
            f"- Raw evidence: `{_text(row.get('raw_evidence'))[:700]}`",
            f"- Target evidence: `{json.dumps(ev.get('evidence_by_type', {}), ensure_ascii=False, sort_keys=True)}`",
            f"- Non-target evidence: `{json.dumps(ev.get('non_target_evidence', []), ensure_ascii=False, sort_keys=True)}`",
            "",
        ])
    return lines


def _write_review_pack(
    path: Path,
    *,
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
    old_by_identity: Mapping[str, dict[str, Any]],
    v1_by_identity: Mapping[str, dict[str, Any]],
    refined_by_identity: Mapping[str, list[dict[str, Any]]],
    root_rows: list[dict[str, Any]],
    added_rows: list[dict[str, Any]],
    lost_rows: list[dict[str, Any]],
    type_corrections: list[dict[str, Any]],
) -> None:
    lines = [
        "# MySQL Target Feature Evidence Review Pack", "",
        "This is a deterministic, offline identity audit. Owner shadow v1 is frozen; no semantic, canonical, frontend, discovery, network, or LLM stage was run.", "",
        "## CASE / IS / NAME", "",
    ]
    for identity in ("CASE", "IS", "NAME"):
        lines.extend(_render_evidence_section(identity, occurrences_by_identity.get(identity, []), _best_group(refined_by_identity.get(identity, [])), old_by_identity.get(identity), v1_by_identity.get(identity)))
    lines.extend(["## Twenty glossary/document-term uncertain cases", ""])
    glossary = [row for row in root_rows if row.get("root_cause") == "GLOSSARY_TERM"][:20]
    for row in glossary:
        identity = row["identity"]
        lines.extend(_render_evidence_section(identity, occurrences_by_identity.get(identity, []), _best_group(refined_by_identity.get(identity, [])), old_by_identity.get(identity), v1_by_identity.get(identity)))
    lines.extend(["## All new probable target features", ""])
    for identity, refined_rows in sorted(refined_by_identity.items()):
        refined = _best_group(refined_rows)
        if refined and refined.get("group_status") == "PROBABLE_FEATURE":
            lines.extend(_render_evidence_section(identity, occurrences_by_identity.get(identity, []), refined, old_by_identity.get(identity), v1_by_identity.get(identity)))
    lines.extend(["## All old confirmed lost", ""])
    for row in lost_rows:
        if row.get("old_status") == "CONFIRMED_FEATURE":
            identity = row["identity"]
            lines.extend(_render_evidence_section(identity, occurrences_by_identity.get(identity, []), _best_group(refined_by_identity.get(identity, [])), old_by_identity.get(identity), v1_by_identity.get(identity)))
    lines.extend(["## Type corrections", ""])
    for row in type_corrections:
        identity = row["identity"]
        lines.extend(_render_evidence_section(identity, occurrences_by_identity.get(identity, []), _best_group(refined_by_identity.get(identity, [])), old_by_identity.get(identity), v1_by_identity.get(identity)))
    lines.extend(["## Remaining ambiguous target cases", ""])
    for identity, refined_rows in sorted(refined_by_identity.items()):
        refined = _best_group(refined_rows)
        if refined and refined.get("group_status") == "UNCERTAIN_FEATURE" and refined.get("target_feature_plausible"):
            lines.extend(_render_evidence_section(identity, occurrences_by_identity.get(identity, []), refined, old_by_identity.get(identity), v1_by_identity.get(identity)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    root = summary.get("new_uncertain_root_causes") or {}
    comparison = summary.get("comparison") or {}
    lines = [
        "# MySQL Target Feature Evidence Audit + Identity Stability Refinement", "",
        f"- Occurrence input: `{(summary.get('input') or {}).get('occurrence_source', 'unknown')}` from the frozen Owner shadow artifact.",
        "## 1. New UNCERTAIN Root Causes", "",
        f"- Owner shadow v1 newly added UNCERTAIN_FEATURE: **{root.get('total', 0)}**",
        f"- Root-cause distribution: `{json.dumps(root.get('by_root_cause', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Target-evidence-less uncertain: **{root.get('target_evidence_less', 0)}**",
        "",
        "## 2. Glossary / Document Term Problem", "",
        f"- Glossary/document terms among v1 new uncertain: **{root.get('by_root_cause', {}).get('GLOSSARY_TERM', 0)}**",
        "- They remain document-defining for provenance, but refined admission marks them NON_FEATURE_ONLY with DOCUMENT_TERM_ONLY rather than treating them as target features.", "",
        "## 3. Target Feature Evidence", "",
        "- FUNCTION: standalone callable signature or bounded function catalog/definition evidence.",
        "- OPERATOR: symbolic operator or explicit expression/operator reference evidence, including CASE/IS/IN/LIKE/BETWEEN when syntax is present.",
        "- DATATYPE: type declaration, datatype catalog, column/type syntax, or storage/type-system evidence.",
        "- NON_TARGET: glossary/narrative/configuration/API/statement/header/parameter/example signals.", "",
        "## 4. CASE / IS / NAME", "",
        "- CASE is evaluated from expression evidence, not restored merely because the old type was FUNCTION.",
        "- IS is evaluated as OPERATOR when expr IS NULL/TRUE/FALSE or operator-reference evidence is present.",
        "- NAME is rejected when it is only a table label/header or generic mention and has no NAME(...) target evidence.", "",
        "## 5. Added / Lost Identity Audit", "",
        f"- v1 added: **{summary.get('added_count', 0)}**; classification: `{json.dumps(summary.get('added_by_classification', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- v1 lost: **{summary.get('lost_count', 0)}**; classification: `{json.dumps(summary.get('lost_by_classification', {}), ensure_ascii=False, sort_keys=True)}`", "",
        "### Added identity dimensions", "",
        f"- by owner kind: `{json.dumps((summary.get('added_quality') or {}).get('by_owner_kind', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- by page structural role: `{json.dumps((summary.get('added_quality') or {}).get('by_page_structural_role', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- by source type: `{json.dumps((summary.get('added_quality') or {}).get('by_source_type', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- by target evidence type: `{json.dumps((summary.get('added_quality') or {}).get('by_target_evidence_type', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- by final status: `{json.dumps((summary.get('added_quality') or {}).get('by_final_status', {}), ensure_ascii=False, sort_keys=True)}`", "",
        "## 6. Old Confirmed Risk Audit", "",
        f"- High-risk old CONFIRMED_FEATURE rows: **{summary.get('old_confirmed_high_risk', 0)}**",
        f"- Old confirmed false-positive removals: **{summary.get('old_confirmed_false_positive_removed', 0)}**",
        f"- True target potentially lost: **{summary.get('true_target_potentially_lost', 0)}**", "",
        "## 7. Identity Shadow Replay", "",
    ]
    for label in ("old_production", "owner_shadow_v1", "identity_refined_shadow"):
        snap = comparison.get(label) or {}
        lines.append(f"- {label}: units={snap.get('units', 0)} status={json.dumps(snap.get('status', {}), ensure_ascii=False, sort_keys=True)} types={json.dumps(snap.get('types', {}), ensure_ascii=False, sort_keys=True)}")
    lines.extend([
        "", "## 8. Type Corrections", "",
        f"- Type corrections: **{comparison.get('type_correction_count', 0)}**",
        f"- Corrections: `{json.dumps(summary.get('type_corrections_by_transition', {}), ensure_ascii=False, sort_keys=True)}`", "",
        "## 9. Remaining UNCERTAIN", "",
        f"- Refined UNCERTAIN_FEATURE: **{(comparison.get('identity_refined_shadow') or {}).get('status', {}).get('UNCERTAIN_FEATURE', 0)}**",
        f"- Refined uncertain with target plausibility: **{summary.get('refined_uncertain_with_target_plausibility', 0)}**",
        "- The refined uncertain set is expected to contain only unresolved target-like evidence, not plain glossary definitions.", "",
        "## 10. Tests", "",
        f"- Target-evidence regression tests: **{summary.get('tests', {}).get('target_evidence_tests', 'not run by replay')}**",
        "- Replay is offline and deterministic.", "",
        "## 11. Integrity", "",
        f"- `{json.dumps(summary.get('integrity', {}), ensure_ascii=False, sort_keys=True)}`", "",
        "## 12. Decision", "",
    ])
    if summary.get("decision") == "IDENTITY_REFINEMENT_READY":
        lines.append("**Identity refinement is ready for a controlled productionization review; keep Owner and downstream stages frozen until manual review of the generated pack.**")
    else:
        lines.append("**Continue Identity shadow investigation; do not productionize Identity or enter Semantic replay yet.**")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_identity_shadow_v2(
    *,
    repo_root: str | Path,
    output_root: str | Path,
    documents_path: str | Path | None = None,
    selected_owners_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the frozen 250-page target-evidence identity shadow replay."""
    repo = Path(repo_root).resolve()
    out = Path(output_root).resolve()
    v1_root = repo / "output" / "mysql_owner_shadow_v1"
    prod_root = repo / "output" / "mysql_identity_quality_v3"
    documents_path = Path(documents_path or v1_root / "documentir" / "structural_documents.jsonl")
    selected_owners_path = Path(selected_owners_path or v1_root / "owners" / "ranked_owner_hypotheses.jsonl")
    documents = _read_jsonl(documents_path)
    owners = [row for row in _read_jsonl(selected_owners_path) if str(row.get("selection_status") or "SELECTED") == "SELECTED"]
    pages = {str(row.get("page_id")): row for row in documents}
    blocks = {
        str(page.get("page_id")): {str(block.get("block_id")): block for block in page.get("blocks") or []}
        for page in documents
    }
    owner_by_id = {str(row.get("owner_id")): row for row in owners}
    cached_occurrence_path = v1_root / "identity" / "occurrences.jsonl"
    occurrences = _read_jsonl(cached_occurrence_path)
    occurrence_source = "FROZEN_OWNER_SHADOW_V1_OCCURRENCES"
    if not occurrences:
        # This fallback is only for a caller that supplies a compatible corpus
        # without the v1 cache.  The normal replay deliberately uses the
        # occurrence artifact produced from the already selected Owner set so
        # target-evidence changes cannot be confounded with extraction churn.
        occurrences = build_identity_occurrences(documents, owners)
        occurrence_source = "REBUILT_FROM_SELECTED_SHADOW_OWNERS"
    occurrences, supplemental_expression_count = _supplement_expression_occurrences(documents, owners, occurrences)
    enriched = []
    for occurrence in occurrences:
        page = pages.get(str(occurrence.get("page_id")), {})
        block = blocks.get(str(occurrence.get("page_id")), {}).get(str(occurrence.get("source_block_id")), {})
        owner = owner_by_id.get(str(occurrence.get("owner_id")), {})
        evidence = classify_target_evidence(occurrence, block=block, owner=owner, page=page)
        row = dict(occurrence)
        row["document_defining"] = bool(evidence.get("document_defining"))
        row["target_feature_defining"] = bool(evidence.get("target_feature_defining"))
        row["target_feature_evidence"] = evidence
        row["target_feature_evidence_summary"] = {
            "target_types": evidence.get("target_types", []),
            "strength": evidence.get("strength"),
            "reason_codes": evidence.get("reason_codes", []),
            "non_target_codes": sorted({item.get("code") for item in evidence.get("non_target_evidence", []) if item.get("code")}),
        }
        enriched.append(row)
    occurrences_by_identity = _occurrence_index(enriched)
    refined_units = []
    for identity, rows in sorted(occurrences_by_identity.items()):
        refined_units.append(_aggregate_group(identity, rows))
    refined_units = sorted(refined_units, key=lambda row: (str(row.get("canonical_identity")), str(row.get("feature_type")), str(row.get("identity_unit_id"))))
    refined_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in refined_units:
        refined_by_identity[str(row.get("canonical_identity") or "").upper()].append(row)

    old_units = _read_jsonl(prod_root / "identity" / "identity_units.jsonl")
    v1_units = _read_jsonl(v1_root / "identity" / "groups.jsonl")
    old_by_identity = {str(row.get("canonical_identity") or "").upper(): row for row in old_units}
    v1_by_identity = {str(row.get("canonical_identity") or "").upper(): row for row in v1_units}
    v1_comparison = _read_json(v1_root / "identity" / "comparison.json", {}) or {}
    comparison = _compare_snapshots(old_units, v1_units, refined_units)
    root_rows = []
    v1_uncertain = [row for row in v1_comparison.get("added") or [] if row.get("status") == "UNCERTAIN_FEATURE"]
    for row in v1_uncertain:
        identity = str(row.get("identity") or "").upper()
        root_rows.append(_root_cause_row(identity, v1_by_identity.get(identity), occurrences_by_identity.get(identity, []), _best_group(refined_by_identity.get(identity, []))))
    added_rows, lost_rows = _audit_added_lost(v1_comparison, refined_by_identity, occurrences_by_identity)
    status_rows = _status_change_audit(v1_comparison, refined_by_identity, occurrences_by_identity)
    old_risk_rows = _old_confirmed_risk_audit(old_units, refined_units, occurrences_by_identity)

    old_confirmed_false_positive_removed = sum(
        row.get("classification") in {"OLD_GENERIC_OR_WRONG_TYPE_REMOVED", "NO_CURRENT_OWNER_OCCURRENCE"}
        and row.get("old_status") == "CONFIRMED_FEATURE"
        for row in lost_rows
    )
    true_target_potentially_lost = sum(row.get("classification") == "TRUE_TARGET_POTENTIALLY_LOST" for row in lost_rows)
    refined_uncertain_plausible = sum(
        row.get("group_status") == "UNCERTAIN_FEATURE" and row.get("target_feature_plausible")
        for row in refined_units
    )
    transitions = Counter()
    for row in comparison.get("type_corrections") or []:
        transitions[f"{row['owner_shadow_v1'].get('type')}->{row['identity_refined_shadow'].get('type')}"] += 1

    audit_root = out / "audit"
    identity_root = out / "identity"
    review_root = out / "review"
    runtime_root = out / "runtime"
    _write_jsonl(identity_root / "identity_occurrences.jsonl", enriched)
    _write_jsonl(identity_root / "identity_groups.jsonl", refined_units)
    _write_jsonl(identity_root / "candidates.jsonl", enriched)
    _write_jsonl(identity_root / "validated_candidates.jsonl", [row for row in enriched if row.get("target_feature_evidence", {}).get("document_defining")])
    _write_jsonl(identity_root / "rejected_candidates.jsonl", [])
    _write_jsonl(audit_root / "new_uncertain_root_causes.jsonl", root_rows)
    _write_jsonl(audit_root / "added_identity_audit.jsonl", added_rows)
    _write_jsonl(audit_root / "lost_identity_audit.jsonl", lost_rows)
    _write_jsonl(audit_root / "status_change_audit.jsonl", status_rows)
    _write_jsonl(audit_root / "old_confirmed_risk_audit.jsonl", old_risk_rows)
    _write_json(audit_root / "target_evidence_summary.json", {
        "schema_version": "mysql-target-feature-evidence-summary/v1",
        "occurrences": len(enriched),
        "target_feature_defining_occurrences": sum(row.get("target_feature_defining") for row in enriched),
        "document_defining_occurrences": sum(row.get("document_defining") for row in enriched),
        "by_target_type": dict(sorted(Counter(value for row in enriched for value in row.get("target_feature_evidence", {}).get("target_types", [])).items())),
        "by_strength": dict(sorted(Counter(row.get("target_feature_evidence", {}).get("strength", "NONE") for row in enriched).items())),
        "by_non_target_code": dict(sorted(Counter(item.get("code") for row in enriched for item in row.get("target_feature_evidence", {}).get("non_target_evidence", []) if item.get("code")).items())),
    })
    _write_json(identity_root / "comparison.json", comparison)
    _write_review_pack(
        review_root / "identity_review_pack.md",
        occurrences_by_identity=occurrences_by_identity,
        old_by_identity=old_by_identity,
        v1_by_identity=v1_by_identity,
        refined_by_identity=refined_by_identity,
        root_rows=root_rows,
        added_rows=added_rows,
        lost_rows=lost_rows,
        type_corrections=comparison.get("type_corrections") or [],
    )
    integrity = {
        "network_fetch": False,
        "llm_called": False,
        "owner_modified": False,
        "production_owner_replaced": False,
        "semantic_run": False,
        "canonical_modified": False,
        "frontend_modified": False,
        "db_specific_identity_rule": False,
        "inventory_used_for_extraction": False,
        "owner_input": str(selected_owners_path),
        "documents_input": str(documents_path),
    }
    _write_json(runtime_root / "run_manifest.json", {
        "schema_version": "mysql-identity-shadow-run/v2",
        "pipeline_id": SCHEMA_VERSION,
        "run_id": f"mysql_identity_shadow_v2_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "input_document_count": len(documents),
        "input_owner_count": len(owners),
        "input_mode": "FROZEN_MYSQL_OWNER_SHADOW_V1",
        "identity_occurrence_source": occurrence_source,
        "identity_occurrence_path": str(cached_occurrence_path),
        "supplemental_expression_occurrences": supplemental_expression_count,
        "target_scope": TARGET_SCOPE,
        "owner_ranking_replayed": False,
        "identity_refinement_only": True,
        "integrity": integrity,
    })
    summary = {
        "schema_version": SCHEMA_VERSION,
        "input": {
            "documents": len(documents), "selected_owners": len(owners), "occurrences": len(enriched),
            "occurrence_source": occurrence_source,
            "occurrence_path": str(cached_occurrence_path),
            "supplemental_expression_occurrences": supplemental_expression_count,
        },
        "new_uncertain_root_causes": {
            "total": len(root_rows),
            "by_root_cause": dict(sorted(Counter(row.get("root_cause") for row in root_rows).items())),
            "target_evidence_less": sum(not row.get("target_evidence_present") for row in root_rows),
        },
        "added_count": len(added_rows),
        "added_by_classification": dict(sorted(Counter(row.get("classification") for row in added_rows).items())),
        "lost_count": len(lost_rows),
        "lost_by_classification": dict(sorted(Counter(row.get("classification") for row in lost_rows).items())),
        "added_quality": {
            "by_owner_kind": _dimension_counts(added_rows, "owner_kinds"),
            "by_page_structural_role": _dimension_counts(added_rows, "page_structural_roles"),
            "by_source_type": _dimension_counts(added_rows, "source_types"),
            "by_target_evidence_type": _dimension_counts(added_rows, "target_evidence_types"),
            "by_final_status": dict(sorted(Counter(str(row.get("final_status") or "UNKNOWN") for row in added_rows).items())),
        },
        "status_change_count": len(status_rows),
        "old_confirmed_high_risk": len(old_risk_rows),
        "old_confirmed_false_positive_removed": old_confirmed_false_positive_removed,
        "true_target_potentially_lost": true_target_potentially_lost,
        "refined_uncertain_with_target_plausibility": refined_uncertain_plausible,
        "comparison": comparison,
        "type_corrections_by_transition": dict(sorted(transitions.items())),
        "tests": {"target_evidence_tests": "not run by replay"},
        "integrity": integrity,
        "decision": "IDENTITY_REFINEMENT_READY" if true_target_potentially_lost == 0 else "CONTINUE_IDENTITY_SHADOW",
    }
    _write_json(identity_root / "summary.json", summary)
    _write_json(out / "summary.json", summary)
    _write_report(repo / "reports" / "mysql_identity_shadow_v2.md", summary)
    return summary
