"""Generic FeatureScopeDecision primitives.

The scope layer sits after IdentityResolution and before type production.  It
classifies documentation/page and owner semantics from local structural
evidence.  It deliberately contains no database names, feature-name lists,
exact URL rules, or human-review lookup table.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


PAGE_SQL_FUNCTION = "SQL_FUNCTION_REFERENCE"
PAGE_SQL_OPERATOR = "SQL_OPERATOR_REFERENCE"
PAGE_SQL_DATATYPE = "SQL_DATATYPE_REFERENCE"
PAGE_SQL_SYNTAX = "SQL_SYNTAX_REFERENCE"
PAGE_FEATURE_CATALOG = "FEATURE_CATALOG"
PAGE_CONFIG = "CONFIGURATION_REFERENCE"
PAGE_SYSTEM_SCHEMA = "SYSTEM_SCHEMA_REFERENCE"
PAGE_SYSTEM_METRIC = "SYSTEM_METRIC_REFERENCE"
PAGE_ENGINE = "ENGINE_INTEGRATION_REFERENCE"
PAGE_FORMAT = "FORMAT_PROTOCOL_REFERENCE"
PAGE_API = "API_REFERENCE"
PAGE_EXAMPLE = "EXAMPLE_TUTORIAL"
PAGE_UNKNOWN = "UNKNOWN"

OWNER_DEFINITION = "FEATURE_DEFINITION"
OWNER_SIGNATURE = "FEATURE_SIGNATURE"
OWNER_CATALOG = "FEATURE_CATALOG_ENTRY"
OWNER_CONFIG = "CONFIG_ENTRY"
OWNER_SYSTEM_COLUMN = "SYSTEM_COLUMN"
OWNER_SYSTEM_METRIC = "SYSTEM_METRIC"
OWNER_ENGINE = "ENGINE_ENTRY"
OWNER_FORMAT = "FORMAT_PROTOCOL_ENTRY"
OWNER_API = "API_SYMBOL"
OWNER_EXAMPLE = "EXAMPLE_IDENTIFIER"
OWNER_PROSE = "PROSE_MENTION"
OWNER_UNKNOWN = "UNKNOWN"

SCOPE_IN = "IN_SCOPE"
SCOPE_OUT = "OUT_OF_SCOPE"
SCOPE_REVIEW = "REVIEW_REQUIRED"

ROLE_FUNCTION = "SQL_FUNCTION"
ROLE_OPERATOR = "SQL_OPERATOR"
ROLE_DATATYPE = "SQL_DATATYPE"
ROLE_UNCERTAIN = "SQL_FEATURE_UNCERTAIN"
ROLE_CONFIG = "CONFIGURATION"
ROLE_SYSTEM_SCHEMA = "SYSTEM_SCHEMA"
ROLE_SYSTEM_METRIC = "SYSTEM_METRIC"
ROLE_ENGINE = "ENGINE_OR_INTEGRATION"
ROLE_FORMAT = "FORMAT_OR_PROTOCOL"
ROLE_API = "API"
ROLE_EXAMPLE = "EXAMPLE"
ROLE_PROSE = "PROSE"
ROLE_OTHER = "OTHER"

_SQL_PAGE_ROLES = {
    PAGE_SQL_FUNCTION,
    PAGE_SQL_OPERATOR,
    PAGE_SQL_DATATYPE,
    PAGE_SQL_SYNTAX,
    PAGE_FEATURE_CATALOG,
}
_POSITIVE_OWNER_ROLES = {OWNER_DEFINITION, OWNER_SIGNATURE, OWNER_CATALOG}
_NEGATIVE_OWNER_ROLES = {
    OWNER_CONFIG,
    OWNER_SYSTEM_COLUMN,
    OWNER_SYSTEM_METRIC,
    OWNER_ENGINE,
    OWNER_FORMAT,
    OWNER_API,
    OWNER_EXAMPLE,
    OWNER_PROSE,
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [clean(item) for item in value if clean(item)]
    return [clean(value)] if clean(value) else []


def context_text(context: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "page_title",
        "url",
        "breadcrumb",
        "heading_ancestry",
        "table_headers",
        "owner_text",
        "surrounding_before",
        "surrounding_after",
        "raw_variants",
        "signature_variants",
        "source_types",
        "owner_template",
    ):
        values.extend(_values(context.get(key)))
    return " | ".join(values)


def _matches(pattern: re.Pattern[str], value: str) -> bool:
    return bool(pattern.search(value))


_CONFIG_RE = re.compile(
    r"\b(?:configuration|config(?:uration)?\s+files?|settings?|options?|"
    r"profile\s+settings?|command[- ]line\s+options?|session\s+settings?|"
    r"server\s+settings?|runtime\s+parameters?)\b",
    re.I,
)
_SYSTEM_SCHEMA_RE = re.compile(
    r"\b(?:system\s+tables?|system\s+schema|system\s+columns?|"
    r"information[_ ]schema|system\s+catalog(?:s)?)\b|\bsystem\.[a-z_][a-z0-9_]*\b",
    re.I,
)
_SYSTEM_METRIC_RE = re.compile(
    r"\b(?:system\s+metrics?|metrics?|profile\s+events?|"
    r"monitoring|telemetry|event\s+logs?|performance\s+counters?)\b",
    re.I,
)
_ENGINE_RE = re.compile(
    r"\b(?:table\s+engines?|storage\s+engines?|engine(?:s)?|"
    r"integrations?|connectors?|extensions?)\b",
    re.I,
)
_FORMAT_RE = re.compile(
    r"\b(?:input\s+formats?|output\s+formats?|formats?|wire\s+formats?|"
    r"protocols?|serialization|serialisation|encoding formats?)\b",
    re.I,
)
_API_RE = re.compile(
    r"\b(?:c[- ]api|c[- ]language|client\s+api|client\s+library|"
    r"application\s+programming\s+interface|api\s+reference|sdk|"
    r"programming\s+interface)\b",
    re.I,
)
_EXAMPLE_RE = re.compile(
    r"\b(?:examples?|tutorials?|samples?|how[- ]to|getting\s+started|"
    r"demo(?:nstration)?)\b",
    re.I,
)
_FUNCTION_RE = re.compile(r"\b(?:built[- ]in\s+)?functions?|routines?|callables?\b", re.I)
_OPERATOR_RE = re.compile(r"\boperators?|predicates?\b", re.I)
_DATATYPE_RE = re.compile(
    r"\b(?:data[- ]?types?|datatypes?|type\s+system|type\s+definitions?|"
    r"type\s+affinity|storage\s+classes?|declared\s+types?)\b",
    re.I,
)
_SYNTAX_RE = re.compile(
    r"\b(?:syntax|statements?|commands?|clauses?|expressions?|"
    r"language\s+elements?|grammar)\b",
    re.I,
)
_CATALOG_HEADER_RE = re.compile(
    r"\b(?:function|functions|operator|operators|routine|routines|"
    r"data[- ]?type|datatype|type\s+name|signature|syntax|feature|"
    r"callable|predicate|name)\b",
    re.I,
)
_CONFIG_HEADER_RE = re.compile(
    r"\b(?:setting|settings|option|options|parameter|parameters|"
    r"default|configuration|flag|value)\b",
    re.I,
)
_SYSTEM_COLUMN_HEADER_RE = re.compile(r"\b(?:column|columns|field|fields)\b", re.I)
_METRIC_HEADER_RE = re.compile(r"\b(?:metric|metrics|event|events|counter|counters)\b", re.I)
_API_SYMBOL_RE = re.compile(
    r"\b(?:api|client|library|header|callback|application\s+interface|"
    r"programming\s+interface)\b",
    re.I,
)


def infer_page_semantic_role(context: Mapping[str, Any]) -> dict[str, Any]:
    """Infer a page role from generic title/path/local structure signals."""

    title = clean(context.get("page_title"))
    url = clean(context.get("url"))
    url_path = urlsplit(url).path if url else ""
    headings = " ".join(_values(context.get("heading_ancestry")))
    headers = " ".join(_values(context.get("table_headers")))
    local = " | ".join(value for value in (title, url, headings, headers) if value)
    title_path = " | ".join(value for value in (title, url_path) if value)
    signals: list[str] = []
    negative: list[str] = []

    # Explicit SQL family references take precedence over the word "system"
    # in names such as "system information functions".
    family_matches: list[tuple[str, re.Pattern[str], str]] = [
        (PAGE_SQL_FUNCTION, _FUNCTION_RE, "FUNCTION_CONTEXT"),
        (PAGE_SQL_OPERATOR, _OPERATOR_RE, "OPERATOR_CONTEXT"),
        (PAGE_SQL_DATATYPE, _DATATYPE_RE, "DATATYPE_CONTEXT"),
    ]
    family_hits = [(role, marker) for role, pattern, marker in family_matches if _matches(pattern, title_path)]

    negative_patterns: list[tuple[str, re.Pattern[str], str]] = [
        (PAGE_CONFIG, _CONFIG_RE, "CONFIGURATION_CONTEXT"),
        (PAGE_SYSTEM_SCHEMA, _SYSTEM_SCHEMA_RE, "SYSTEM_SCHEMA_CONTEXT"),
        (PAGE_SYSTEM_METRIC, _SYSTEM_METRIC_RE, "SYSTEM_METRIC_CONTEXT"),
        (PAGE_ENGINE, _ENGINE_RE, "ENGINE_INTEGRATION_CONTEXT"),
        (PAGE_FORMAT, _FORMAT_RE, "FORMAT_PROTOCOL_CONTEXT"),
        (PAGE_API, _API_RE, "API_CONTEXT"),
        (PAGE_EXAMPLE, _EXAMPLE_RE, "EXAMPLE_TUTORIAL_CONTEXT"),
    ]
    negative_hits = [(role, marker) for role, pattern, marker in negative_patterns if _matches(pattern, title_path)]

    # Function/operator/datatype pages are not demoted merely because a local
    # heading contains "Examples" or "system".  Explicit configuration,
    # schema, metric, engine, format, or API titles remain negative.
    explicit_negative = [item for item in negative_hits if item[0] != PAGE_EXAMPLE]
    if family_hits and not explicit_negative:
        role, marker = family_hits[0]
        signals.extend(marker for _, marker in family_hits)
        if len(family_hits) > 1:
            signals.append("MIXED_SQL_FAMILY_CONTEXT")
        return {
            "page_semantic_role": role,
            "positive_signals": sorted(set(signals)),
            "negative_signals": [],
            "confidence": 0.86 if len(family_hits) == 1 else 0.72,
        }

    for role, marker in negative_hits:
        negative.append(marker)
    if negative:
        role = negative_hits[0][0]
        return {
            "page_semantic_role": role,
            "positive_signals": [],
            "negative_signals": sorted(set(negative)),
            "confidence": 0.84 if len(negative_hits) == 1 else 0.72,
        }

    if _matches(_SYNTAX_RE, title_path):
        return {
            "page_semantic_role": PAGE_SQL_SYNTAX,
            "positive_signals": ["SYNTAX_CONTEXT"],
            "negative_signals": [],
            "confidence": 0.72,
        }
    if _matches(_CATALOG_HEADER_RE, headers):
        return {
            "page_semantic_role": PAGE_FEATURE_CATALOG,
            "positive_signals": ["FEATURE_CATALOG_HEADER"],
            "negative_signals": [],
            "confidence": 0.68,
        }
    return {
        "page_semantic_role": PAGE_UNKNOWN,
        "positive_signals": [],
        "negative_signals": [],
        "confidence": 0.35,
    }


def infer_owner_semantic_role(
    context: Mapping[str, Any], page_role: str
) -> dict[str, Any]:
    """Infer an owner role using page role plus owner-local evidence."""

    owner_kind = clean(context.get("owner_kind"))
    template = clean(context.get("owner_template"))
    text = context_text(context)
    headers = " ".join(_values(context.get("table_headers")))
    headings = " ".join(_values(context.get("heading_ancestry")))
    signatures = _values(context.get("signature_variants"))
    raw_variants = _values(context.get("raw_variants"))
    structural = bool(
        owner_kind in {"TableRowOwner", "ListItemOwner", "SignatureOwner", "DefinitionListOwner", "SectionOwner"}
        or template in {"table_row", "list_item", "signature", "definition_list", "heading", "section"}
    )
    negative: list[str] = []

    page_to_owner = {
        PAGE_CONFIG: (OWNER_CONFIG, ROLE_CONFIG, "PAGE_CONFIGURATION_ROLE"),
        PAGE_SYSTEM_SCHEMA: (OWNER_SYSTEM_COLUMN, ROLE_SYSTEM_SCHEMA, "PAGE_SYSTEM_SCHEMA_ROLE"),
        PAGE_SYSTEM_METRIC: (OWNER_SYSTEM_METRIC, ROLE_SYSTEM_METRIC, "PAGE_SYSTEM_METRIC_ROLE"),
        PAGE_ENGINE: (OWNER_ENGINE, ROLE_ENGINE, "PAGE_ENGINE_ROLE"),
        PAGE_FORMAT: (OWNER_FORMAT, ROLE_FORMAT, "PAGE_FORMAT_ROLE"),
        PAGE_API: (OWNER_API, ROLE_API, "PAGE_API_ROLE"),
        PAGE_EXAMPLE: (OWNER_EXAMPLE, ROLE_EXAMPLE, "PAGE_EXAMPLE_ROLE"),
    }
    if page_role in page_to_owner:
        owner_role, _, marker = page_to_owner[page_role]
        return {
            "owner_semantic_role": owner_role,
            "positive_signals": [],
            "negative_signals": [marker],
            "confidence": 0.84,
        }

    if _matches(_CONFIG_HEADER_RE, headers) and structural:
        return {
            "owner_semantic_role": OWNER_CONFIG,
            "positive_signals": [],
            "negative_signals": ["CONFIGURATION_TABLE_HEADERS"],
            "confidence": 0.8,
        }
    if _matches(_SYSTEM_COLUMN_HEADER_RE, headers) and page_role == PAGE_SYSTEM_SCHEMA:
        return {
            "owner_semantic_role": OWNER_SYSTEM_COLUMN,
            "positive_signals": [],
            "negative_signals": ["SYSTEM_COLUMN_HEADERS"],
            "confidence": 0.8,
        }
    if _matches(_METRIC_HEADER_RE, headers) and page_role == PAGE_SYSTEM_METRIC:
        return {
            "owner_semantic_role": OWNER_SYSTEM_METRIC,
            "positive_signals": [],
            "negative_signals": ["SYSTEM_METRIC_HEADERS"],
            "confidence": 0.8,
        }
    if _matches(_API_SYMBOL_RE, text) and page_role == PAGE_UNKNOWN:
        return {
            "owner_semantic_role": OWNER_API,
            "positive_signals": [],
            "negative_signals": ["LOCAL_API_CONTEXT"],
            "confidence": 0.67,
        }

    sql_page = page_role in _SQL_PAGE_ROLES
    has_signature = bool(signatures) or any("(" in value and ")" in value for value in raw_variants)
    has_catalog_header = _matches(_CATALOG_HEADER_RE, headers)
    has_feature_heading = _matches(_FUNCTION_RE, headings) or _matches(_OPERATOR_RE, headings) or _matches(_DATATYPE_RE, headings)
    if sql_page and (template == "signature" or owner_kind == "SignatureOwner") and has_signature:
        return {
            "owner_semantic_role": OWNER_SIGNATURE,
            "positive_signals": ["SQL_PAGE_ROLE", "SIGNATURE_SURFACE"],
            "negative_signals": [],
            "confidence": 0.9,
        }
    if sql_page and structural and (has_catalog_header or has_feature_heading or has_signature):
        return {
            "owner_semantic_role": OWNER_CATALOG if owner_kind in {"TableRowOwner", "ListItemOwner"} else OWNER_DEFINITION,
            "positive_signals": ["SQL_PAGE_ROLE", "STRUCTURAL_OWNER", "FEATURE_CONTEXT"],
            "negative_signals": [],
            "confidence": 0.82 if has_catalog_header or has_feature_heading else 0.7,
        }
    if page_role == PAGE_SQL_SYNTAX and structural and (has_signature or raw_variants):
        return {
            "owner_semantic_role": OWNER_DEFINITION,
            "positive_signals": ["SQL_SYNTAX_ROLE", "STRUCTURAL_IDENTITY_SURFACE"],
            "negative_signals": [],
            "confidence": 0.68,
        }
    if structural and _matches(_EXAMPLE_RE, headings) and not sql_page:
        return {
            "owner_semantic_role": OWNER_EXAMPLE,
            "positive_signals": [],
            "negative_signals": ["LOCAL_EXAMPLE_CONTEXT"],
            "confidence": 0.66,
        }
    if owner_kind in {"SectionOwner", "PageOwner", "UNKNOWN"} and not has_signature:
        return {
            "owner_semantic_role": OWNER_PROSE,
            "positive_signals": [],
            "negative_signals": ["NON_STRUCTURAL_OWNER"],
            "confidence": 0.62,
        }
    return {
        "owner_semantic_role": OWNER_UNKNOWN,
        "positive_signals": [],
        "negative_signals": [],
        "confidence": 0.35,
    }


def _scope_role(page_role: str, owner_roles: Sequence[str], has_symbol: bool) -> str:
    if page_role == PAGE_SQL_FUNCTION:
        return ROLE_FUNCTION
    if page_role == PAGE_SQL_OPERATOR or has_symbol:
        return ROLE_OPERATOR
    if page_role == PAGE_SQL_DATATYPE:
        return ROLE_DATATYPE
    if any(role in _POSITIVE_OWNER_ROLES for role in owner_roles):
        return ROLE_UNCERTAIN
    if OWNER_CONFIG in owner_roles:
        return ROLE_CONFIG
    if OWNER_SYSTEM_COLUMN in owner_roles:
        return ROLE_SYSTEM_SCHEMA
    if OWNER_SYSTEM_METRIC in owner_roles:
        return ROLE_SYSTEM_METRIC
    if OWNER_ENGINE in owner_roles:
        return ROLE_ENGINE
    if OWNER_FORMAT in owner_roles:
        return ROLE_FORMAT
    if OWNER_API in owner_roles:
        return ROLE_API
    if OWNER_EXAMPLE in owner_roles:
        return ROLE_EXAMPLE
    if OWNER_PROSE in owner_roles:
        return ROLE_PROSE
    return ROLE_OTHER


# The functions below are the production owner-local evidence contract used by
# the final P2.8 replay. PageSemanticRole is deliberately only a prior here:
# an exact feature slot inside an owner must be able to survive a negative page
# title such as "format", "system", or "api". Conversely, a page role or a
# generic owner kind is never sufficient to create a strong feature identity.
_LOCAL_FEATURE_ROLES = {ROLE_FUNCTION, ROLE_OPERATOR, ROLE_DATATYPE, ROLE_UNCERTAIN}
_LOCAL_STRONG_ALIGNMENTS = {
    "EXACT_FEATURE_HEAD",
    "STRUCTURAL_FEATURE_SLOT",
}
_IDENTITY_HEAD_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*(?:\s+[A-Za-z_][A-Za-z0-9_$]*)*)\s*\(",
    re.I,
)
_DECLARATION_RE = re.compile(
    r"\b(?:create|declare|cast|returns?|returning|type|datatype|data\s+type|"
    r"column\s+of\s+type|declared\s+as)\b",
    re.I,
)
_DEFINITION_RE = re.compile(
    r"\b(?:function|operator|predicate|routine|datatype|data\s+type|type)\b",
    re.I,
)
_EXAMPLE_RE_LOCAL = re.compile(
    r"\b(?:example|examples|sample|samples|tutorial|output|result|returns?)\b",
    re.I,
)


def _local_norm(value: Any) -> str:
    return clean(value).strip("\u200b\u200c\u200d")


def _local_subject_match(subject: str, text: str) -> bool:
    subject = _local_norm(subject)
    text = _local_norm(text)
    if not subject or not text:
        return False
    if re.fullmatch(r"[^A-Za-z0-9_\s]+", subject):
        return subject in text
    return bool(re.search(r"(?<![A-Za-z0-9_$])" + re.escape(subject) + r"(?![A-Za-z0-9_$])", text, re.I))


def _local_surface_equals(subject: str, value: str) -> bool:
    subject = _local_norm(subject)
    value = _local_norm(value)
    if not subject or not value:
        return False
    if re.fullmatch(r"[^A-Za-z0-9_\s]+", subject):
        return value == subject or value.strip("'\"()[]") == subject
    # A catalog cell may contain a callable signature or a return annotation.
    # Only accept the feature head; never accept a subject occurring later in
    # a description cell.
    pattern = r"^" + re.escape(subject) + r"(?:\s*\([^)]*\))?(?:\s*(?:→|->).*)?$"
    return bool(re.match(pattern, value, re.I))


def _local_signature_head(value: str) -> str:
    match = _IDENTITY_HEAD_RE.match(_local_norm(value))
    return _local_norm(match.group(1)) if match else ""


def _local_signature_matches(subject: str, text: str) -> bool:
    subject = _local_norm(subject)
    text = _local_norm(text)
    if not subject or not text or re.fullmatch(r"[^A-Za-z0-9_\s]+", subject):
        return False
    pattern = r"(?<![A-Za-z0-9_$])" + re.escape(subject) + r"(?![A-Za-z0-9_$])\s*\("
    return bool(re.search(pattern, text, re.I))


def _local_header_flags(headers: Sequence[Any]) -> dict[str, bool]:
    text = " ".join(_local_norm(value) for value in headers if _local_norm(value))
    return {
        "function": bool(re.search(r"\b(?:function|functions|routine|routines|callable|callables)\b", text, re.I)),
        "operator": bool(re.search(r"\b(?:operator|operators|predicate|predicates)\b", text, re.I)),
        "datatype": bool(re.search(r"\b(?:data[- ]?type|datatype|type\s+name|declared\s+type)\b", text, re.I)),
        "generic_type": bool(re.search(r"\btype\b", text, re.I)),
        "negative": bool(re.search(
            r"\b(?:setting|settings|option|options|parameter|parameters|key\s+name|"
            r"column|columns|field|fields|metric|metrics|event|events|format|formats|"
            r"engine|engines|api|library|client)\b",
            text,
            re.I,
        )),
    }


def _local_role_from_context(
    *,
    subject: str,
    headers: Sequence[Any],
    headings: Sequence[Any],
    page_role: str,
    block_text: str,
) -> str:
    flags = _local_header_flags(headers)
    heading_text = " ".join(_local_norm(value) for value in headings)
    context_text_value = " | ".join((heading_text, block_text, page_role))
    # A mixed "functions and operators" page must resolve the local table
    # header first.  Page title/path role is only a prior.
    if flags["function"]:
        return ROLE_FUNCTION
    if flags["operator"]:
        return ROLE_OPERATOR
    if flags["datatype"]:
        return ROLE_DATATYPE
    if page_role == PAGE_SQL_FUNCTION or _matches(_FUNCTION_RE, context_text_value):
        return ROLE_FUNCTION
    if page_role == PAGE_SQL_OPERATOR or _matches(_OPERATOR_RE, context_text_value):
        return ROLE_OPERATOR
    if page_role == PAGE_SQL_DATATYPE or _matches(_DATATYPE_RE, context_text_value):
        return ROLE_DATATYPE
    if flags["generic_type"] and _matches(_DECLARATION_RE, context_text_value):
        return ROLE_DATATYPE
    if page_role == PAGE_SQL_SYNTAX:
        return ROLE_UNCERTAIN
    return ROLE_UNCERTAIN


def build_local_feature_evidence(context: Mapping[str, Any]) -> dict[str, Any]:
    """Build a LocalFeatureEvidence/v1 record for one identity/owner pair.

    This is intentionally owner-local. It never treats a page title, a
    candidate surface, or a generic TableRowOwner/SignatureOwner label as a
    feature proof. The caller may pass a page role hint, but the hint remains
    lower precedence than a structural slot observation.
    """

    subject = _local_norm(context.get("canonical_subject") or context.get("subject"))
    owner_id = _local_norm(context.get("owner_id"))
    owner_kind = _local_norm(context.get("owner_kind")) or "UNKNOWN"
    owner_template = _local_norm(context.get("owner_template"))
    page_role = _local_norm(context.get("page_semantic_role"))
    if not page_role:
        page_role = str(infer_page_semantic_role(context).get("page_semantic_role") or PAGE_UNKNOWN)
    blocks = [row for row in context.get("blocks") or [] if isinstance(row, Mapping)]
    if not blocks and _local_norm(context.get("raw_text")):
        blocks = [{
            "block_id": "synthetic:owner_text",
            "block_type": "text",
            "text": context.get("raw_text"),
            "cells": [],
            "table_headers": list(context.get("table_headers") or []),
            "heading_ancestry": list(context.get("heading_ancestry") or []),
        }]
    resolution_signatures = []
    for value in context.get("signature_variants") or []:
        if isinstance(value, Mapping):
            value = value.get("value") or value.get("signature") or value.get("text")
        value = _local_norm(value)
        if value:
            resolution_signatures.append(value)
    raw_variants = [_local_norm(value) for value in context.get("raw_variants") or [] if _local_norm(value)]
    headers = list(context.get("table_headers") or [])
    headings = list(context.get("heading_ancestry") or [])
    for block in blocks:
        headers.extend(block.get("table_headers") or [])
        headings.extend(block.get("heading_ancestry") or [])
    headers = sorted({_local_norm(value) for value in headers if _local_norm(value)})
    headings = sorted({_local_norm(value) for value in headings if _local_norm(value)})
    page_negative = page_role in {
        PAGE_CONFIG,
        PAGE_SYSTEM_SCHEMA,
        PAGE_SYSTEM_METRIC,
        PAGE_ENGINE,
        PAGE_FORMAT,
        PAGE_API,
        PAGE_EXAMPLE,
    }

    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []

    def observation(
        *,
        feature_role: str,
        strength: str,
        alignment: str,
        signal: str,
        block: Mapping[str, Any] | None,
        raw_text: str,
    ) -> dict[str, Any]:
        return {
            "feature_role": feature_role,
            "evidence_strength": strength,
            "subject_alignment": alignment,
            "signal": signal,
            "block_id": _local_norm((block or {}).get("block_id")) or None,
            "block_type": _local_norm((block or {}).get("block_type")) or None,
            "raw_text": clean(raw_text),
        }

    for block in blocks:
        block_type = _local_norm(block.get("block_type"))
        text = _local_norm(block.get("text"))
        cells = [_local_norm(value) for value in block.get("cells") or [] if _local_norm(value)]
        block_headers = [_local_norm(value) for value in block.get("table_headers") or [] if _local_norm(value)] or headers
        block_headings = [_local_norm(value) for value in block.get("heading_ancestry") or [] if _local_norm(value)] or headings
        local = " | ".join(value for value in (text, " ".join(cells), " ".join(block_headings)) if value)
        subject_head = (
            _local_subject_match(subject, text)
            and bool(re.match(r"^\s*" + re.escape(subject) + r"(?:\s|$|\(|\[|<|=|:)", text, re.I))
            if subject and not re.fullmatch(r"[^A-Za-z0-9_\s]+", subject)
            else False
        )
        exact_heading = any(_local_surface_equals(subject, heading) or _local_subject_match(subject, heading) for heading in block_headings)
        signature_in_block = _local_signature_matches(subject, local)
        signature_variant_match = any(
            _local_signature_head(value).casefold() == subject.casefold()
            for value in resolution_signatures + raw_variants
        )
        exact_signature = signature_in_block or signature_variant_match
        first_cell = bool(cells and _local_surface_equals(subject, cells[0]))
        operator_cell = bool(
            cells
            and _local_subject_match(subject, cells[0])
            and not first_cell
        )
        nonfirst_cell = bool(any(_local_subject_match(subject, value) for value in cells[1:]))
        local_role = _local_role_from_context(
            subject=subject,
            headers=block_headers,
            headings=block_headings,
            page_role=page_role,
            block_text=local,
        )
        flags = _local_header_flags(block_headers)
        structural = block_type in {
            "heading", "table_row", "list_item", "definition_list", "section", "paragraph", "code"
        }
        example_context = bool(
            block_type in {"code", "example", "output"}
            or _EXAMPLE_RE_LOCAL.search(" ".join(block_headings))
            or (_EXAMPLE_RE_LOCAL.search(local) and block_type in {"paragraph", "text"})
        )
        function_slot = (
            signature_in_block and local_role == ROLE_FUNCTION and not example_context
        ) or (
            signature_variant_match and local_role == ROLE_FUNCTION and structural
            and (subject_head or first_cell or exact_heading)
        ) or (
            first_cell and flags["function"]
        ) or (
            exact_heading and local_role == ROLE_FUNCTION and (exact_signature or _DEFINITION_RE.search(local))
        )
        operator_slot = (
            (first_cell or operator_cell) and flags["operator"]
        ) or (
            exact_heading and local_role == ROLE_OPERATOR and (_DEFINITION_RE.search(local) or exact_signature)
        ) or (
            _local_subject_match(subject, local)
            and local_role == ROLE_OPERATOR
            and bool(re.search(r"\b(?:operator|predicate|precedence|operand|syntax|expression)\b", local, re.I))
            and not example_context
        )
        type_context = " ".join([_local_norm(context.get("page_title")), *block_headings, *block_headers])
        datatype_slot = (
            first_cell and (flags["datatype"] or (flags["generic_type"] and _matches(_DATATYPE_RE, type_context)))
        ) or (
            exact_heading and local_role == ROLE_DATATYPE and (_DECLARATION_RE.search(local) or _DEFINITION_RE.search(local))
        ) or (
            subject_head and local_role == ROLE_DATATYPE and bool(_DECLARATION_RE.search(local) or flags["datatype"])
        )

        if function_slot:
            positives.append(observation(
                feature_role=ROLE_FUNCTION,
                strength="STRONG",
                alignment="EXACT_FEATURE_HEAD" if exact_signature or exact_heading else "STRUCTURAL_FEATURE_SLOT",
                signal="CALLABLE_SIGNATURE_HEAD" if exact_signature else "FUNCTION_CATALOG_SLOT",
                block=block,
                raw_text=text or " | ".join(cells),
            ))
        elif operator_slot:
            positives.append(observation(
                feature_role=ROLE_OPERATOR,
                strength="STRONG",
                alignment="EXACT_FEATURE_HEAD" if exact_heading else "STRUCTURAL_FEATURE_SLOT",
                signal="OPERATOR_STRUCTURAL_SLOT",
                block=block,
                raw_text=text or " | ".join(cells),
            ))
        elif datatype_slot:
            positives.append(observation(
                feature_role=ROLE_DATATYPE,
                strength="STRONG",
                alignment="EXACT_FEATURE_HEAD" if exact_heading else "STRUCTURAL_FEATURE_SLOT",
                signal="DATATYPE_DECLARATION_OR_CATALOG_SLOT",
                block=block,
                raw_text=text or " | ".join(cells),
            ))
        elif exact_heading or (first_cell and structural):
            positives.append(observation(
                feature_role=local_role,
                strength="MEDIUM",
                alignment="EXACT_FEATURE_HEAD" if exact_heading else "STRUCTURAL_FEATURE_SLOT",
                signal="UNRESOLVED_STRUCTURAL_IDENTITY_SURFACE",
                block=block,
                raw_text=text or " | ".join(cells),
            ))

        if nonfirst_cell:
            negatives.append(observation(
                feature_role=ROLE_OTHER,
                strength="STRONG",
                alignment="PARAMETER_OR_FIELD",
                signal="NON_IDENTITY_TABLE_CELL",
                block=block,
                raw_text=text or " | ".join(cells),
            ))
        if first_cell and flags["negative"] and not (function_slot or operator_slot or datatype_slot or exact_heading):
            negatives.append(observation(
                feature_role=ROLE_OTHER,
                strength="STRONG",
                alignment="STRUCTURAL_FEATURE_SLOT",
                signal="NON_FEATURE_STRUCTURAL_SLOT",
                block=block,
                raw_text=text or " | ".join(cells),
            ))
        if _local_subject_match(subject, local) and not (function_slot or operator_slot or datatype_slot or exact_heading or first_cell):
            negatives.append(observation(
                feature_role=ROLE_OTHER,
                strength="STRONG" if example_context else "MEDIUM",
                alignment="EXAMPLE_VALUE" if example_context else "INCIDENTAL_MENTION",
                signal="EXAMPLE_ONLY_OCCURRENCE" if example_context else "INCIDENTAL_OWNER_MENTION",
                block=block,
                raw_text=text or " | ".join(cells),
            ))

    if not blocks and page_negative:
        negatives.append(observation(
            feature_role=ROLE_OTHER,
            strength="MEDIUM",
            alignment="PROSE_ONLY",
            signal="NEGATIVE_OWNER_CONTEXT",
            block=None,
            raw_text=context.get("raw_text") or context.get("page_title") or "",
        ))
    if page_negative:
        negatives.append(observation(
            feature_role=ROLE_OTHER,
            strength="WEAK",
            alignment="PROSE_ONLY",
            signal="PAGE_ROLE_CONTEXT_ONLY",
            block=None,
            raw_text=context.get("page_title") or page_role,
        ))
    if not positives and not negatives:
        negatives.append(observation(
            feature_role=ROLE_OTHER,
            strength="WEAK",
            alignment="PROSE_ONLY",
            signal="NO_OWNER_LOCAL_FEATURE_SURFACE",
            block=None,
            raw_text=context.get("raw_text") or context.get("page_title") or "",
        ))

    role_set = {
        str(item["feature_role"])
        for item in positives
        if item.get("evidence_strength") == "STRONG" and item.get("feature_role") in _LOCAL_FEATURE_ROLES
    }
    if len(role_set) > 1:
        feature_role = ROLE_UNCERTAIN
    elif role_set:
        feature_role = sorted(role_set)[0]
    elif positives:
        feature_role = str(positives[0].get("feature_role") or ROLE_UNCERTAIN)
    else:
        feature_role = ROLE_OTHER
    rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1, "NONE": 0}
    strongest_positive = max((rank.get(str(item.get("evidence_strength")), 0) for item in positives), default=0)
    strongest_negative = max((rank.get(str(item.get("evidence_strength")), 0) for item in negatives), default=0)
    evidence_strength = max(
        (str(item.get("evidence_strength")) for item in positives),
        key=lambda value: rank.get(value, 0),
        default="NONE",
    )
    alignment = max(
        (str(item.get("subject_alignment")) for item in positives),
        key=lambda value: rank.get(
            next(
                (str(item.get("evidence_strength")) for item in positives if item.get("subject_alignment") == value),
                "NONE",
            ),
            0,
        ),
        default="PROSE_ONLY",
    )
    owner_role = OWNER_UNKNOWN
    if feature_role == ROLE_FUNCTION:
        owner_role = OWNER_SIGNATURE if any(item.get("signal") == "CALLABLE_SIGNATURE_HEAD" for item in positives) else OWNER_CATALOG
    elif feature_role == ROLE_OPERATOR:
        owner_role = OWNER_SIGNATURE if any(
            item.get("signal") == "OPERATOR_STRUCTURAL_SLOT"
            and item.get("block_type") in {"code", "paragraph", "heading"}
            for item in positives
        ) else OWNER_CATALOG
    elif feature_role == ROLE_DATATYPE:
        owner_role = OWNER_DEFINITION if any(item.get("alignment") == "EXACT_FEATURE_HEAD" for item in positives) else OWNER_CATALOG
    elif negatives:
        signal = str(max(negatives, key=lambda item: rank.get(str(item.get("evidence_strength")), 0)).get("signal") or "")
        owner_role = {
            "NON_IDENTITY_TABLE_CELL": OWNER_SYSTEM_COLUMN,
            "NON_FEATURE_STRUCTURAL_SLOT": OWNER_CONFIG,
            "EXAMPLE_ONLY_OCCURRENCE": OWNER_EXAMPLE,
            "INCIDENTAL_OWNER_MENTION": OWNER_PROSE,
        }.get(signal, OWNER_UNKNOWN)
    provenance = {
        "source_url": context.get("source_url") or context.get("url") or "",
        "page_id": context.get("page_id") or "",
        "html_path": context.get("html_path") or "",
        "owner_id": owner_id,
        "block_ids": sorted({str(item.get("block_id")) for item in positives + negatives if item.get("block_id")}),
    }
    signals = sorted({str(item.get("signal")) for item in positives if item.get("signal")})
    negative_signals = sorted({str(item.get("signal")) for item in negatives if item.get("signal")})
    return {
        "schema_version": "LocalFeatureEvidence/v1",
        "identity_unit_id": context.get("identity_unit_id") or "",
        "owner_id": owner_id,
        "subject": subject,
        "feature_role": feature_role,
        "owner_semantic_role": owner_role,
        "evidence_strength": evidence_strength if strongest_positive else "NONE",
        "subject_alignment": alignment if strongest_positive else (
            str(negatives[0].get("subject_alignment") or "PROSE_ONLY") if negatives else "PROSE_ONLY"
        ),
        "signals": signals,
        "negative_signals": negative_signals,
        "provenance": provenance,
        "page_semantic_role": page_role,
        "positive_evidence": positives,
        "negative_evidence": negatives,
        "strongest_positive_rank": strongest_positive,
        "strongest_negative_rank": strongest_negative,
    }


def _decide_from_local_feature_evidence(context: Mapping[str, Any]) -> dict[str, Any]:
    evidence = [row for row in context.get("local_feature_evidence") or [] if isinstance(row, Mapping)]
    rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1, "NONE": 0}
    strong_positive = [
        row for row in evidence
        if rank.get(str(row.get("evidence_strength")), 0) >= 3
        and str(row.get("feature_role")) in _LOCAL_FEATURE_ROLES
        and str(row.get("subject_alignment")) in _LOCAL_STRONG_ALIGNMENTS
    ]
    strong_negative = [
        row for row in evidence
        if rank.get(str(row.get("strongest_negative_rank")), 0) >= 3
        or any(str(item.get("evidence_strength")) == "STRONG" for item in row.get("negative_evidence") or [])
    ]
    conflict_owner_ids = {
        str(row.get("owner_id"))
        for row in strong_positive
        if any(str(item.get("evidence_strength")) == "STRONG" for item in row.get("negative_evidence") or [])
    }
    positive_roles = {str(row.get("feature_role")) for row in strong_positive}
    page = infer_page_semantic_role(context)
    page_role = str(page.get("page_semantic_role") or PAGE_UNKNOWN)
    owner_roles = [str(row.get("owner_semantic_role") or OWNER_UNKNOWN) for row in evidence]
    positive_signals = sorted({str(signal) for row in evidence for signal in row.get("signals") or []})
    negative_signals = sorted({str(signal) for row in evidence for signal in row.get("negative_signals") or []})
    reasons: list[str] = []
    if strong_positive and conflict_owner_ids:
        status = SCOPE_REVIEW
        reasons.extend(["STRONG_LOCAL_EVIDENCE_CONFLICT", "OWNER_LOCAL_NEGATIVE_PRECEDENCE"])
    elif strong_positive:
        status = SCOPE_IN
        reasons.append("STRONG_LOCAL_FEATURE_EVIDENCE")
        if page_role in {PAGE_CONFIG, PAGE_SYSTEM_SCHEMA, PAGE_SYSTEM_METRIC, PAGE_ENGINE, PAGE_FORMAT, PAGE_API, PAGE_EXAMPLE}:
            reasons.append("PAGE_NEGATIVE_CONTEXT_OVERRIDDEN_BY_OWNER")
    elif strong_negative:
        status = SCOPE_OUT
        reasons.append("STRONG_LOCAL_NON_FEATURE_EVIDENCE")
    else:
        status = SCOPE_REVIEW
        reasons.append("INSUFFICIENT_OWNER_LOCAL_EVIDENCE")
    if page_role in {PAGE_CONFIG, PAGE_SYSTEM_SCHEMA, PAGE_SYSTEM_METRIC, PAGE_ENGINE, PAGE_FORMAT, PAGE_API, PAGE_EXAMPLE} and not strong_positive:
        reasons.append("PAGE_ROLE_CONTEXT_ONLY")
    if len(positive_roles) > 1:
        status = SCOPE_REVIEW
        reasons.append("MULTIPLE_LOCAL_FEATURE_ROLES")
    if status == SCOPE_IN:
        role = sorted(positive_roles)[0] if len(positive_roles) == 1 else ROLE_UNCERTAIN
    elif strong_positive and len(positive_roles) > 1:
        role = ROLE_UNCERTAIN
    else:
        role = ROLE_OTHER
    confidence = 0.95 if status == SCOPE_IN and len(positive_roles) == 1 else 0.78 if status == SCOPE_IN else 0.88 if status == SCOPE_OUT else 0.58
    return {
        "schema_version": "FeatureScopeDecision/v1",
        "scope_status": status,
        "scope_role": role,
        "page_semantic_role": page_role,
        "owner_semantic_roles": owner_roles,
        "positive_signals": positive_signals,
        "negative_signals": negative_signals,
        "reason_codes": sorted(set(reasons)),
        "confidence": confidence,
        "page_role_evidence": page,
        "owner_role_evidence": evidence,
        "local_feature_evidence": evidence,
        "identity_readiness": {
            SCOPE_IN: "READY_FOR_DOWNSTREAM",
            SCOPE_REVIEW: "REVIEW_REQUIRED",
            SCOPE_OUT: "OUT_OF_SCOPE",
        }[status],
    }


def decide_scope(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return a FeatureScopeDecision/v1-compatible generic decision."""

    # Final P2.8 uses the explicit LocalFeatureEvidence/v1 contract. Keep the
    # legacy structural fallback below for older callers and fixtures; it is
    # not used by the final replay when owner-local evidence is available.
    if context.get("local_feature_evidence") is not None:
        return _decide_from_local_feature_evidence(context)

    page = infer_page_semantic_role(context)
    page_role = str(page["page_semantic_role"])
    owner_contexts = context.get("owner_contexts") or context.get("owners") or []
    if not owner_contexts:
        owner_contexts = [context]
    owner_decisions: list[dict[str, Any]] = []
    for owner in owner_contexts:
        local = dict(context)
        local.update(owner)
        local["owner_context"] = owner
        owner_decisions.append(infer_owner_semantic_role(local, page_role))
    owner_roles = [str(item["owner_semantic_role"]) for item in owner_decisions]
    positive = sorted({role for role in owner_roles if role in _POSITIVE_OWNER_ROLES})
    negative_roles = sorted({role for role in owner_roles if role in _NEGATIVE_OWNER_ROLES})
    has_symbol = bool(re.fullmatch(r"[^A-Za-z0-9_\s]+", clean(context.get("canonical_subject"))))

    positive_signals = sorted(set(page.get("positive_signals") or []))
    negative_signals = sorted(set(page.get("negative_signals") or []))
    for item in owner_decisions:
        positive_signals.extend(item.get("positive_signals") or [])
        negative_signals.extend(item.get("negative_signals") or [])
    positive_signals = sorted(set(positive_signals))
    negative_signals = sorted(set(negative_signals))

    reasons: list[str] = []
    if positive and negative_roles:
        status = SCOPE_REVIEW
        reasons.append("POSITIVE_NEGATIVE_OWNER_CONFLICT")
    elif negative_roles or page_role in {
        PAGE_CONFIG,
        PAGE_SYSTEM_SCHEMA,
        PAGE_SYSTEM_METRIC,
        PAGE_ENGINE,
        PAGE_FORMAT,
        PAGE_API,
        PAGE_EXAMPLE,
    }:
        status = SCOPE_OUT
        reasons.append("NON_SQL_DOCUMENTATION_ROLE")
    elif positive and page_role in _SQL_PAGE_ROLES:
        status = SCOPE_IN
        reasons.append("SQL_PAGE_AND_OWNER_ROLE")
    elif positive and (page_role == PAGE_UNKNOWN or page_role == PAGE_SQL_SYNTAX):
        status = SCOPE_REVIEW
        reasons.append("SQL_OWNER_WITH_UNRESOLVED_PAGE_ROLE")
    elif page_role == PAGE_UNKNOWN:
        status = SCOPE_REVIEW
        reasons.append("UNKNOWN_PAGE_ROLE")
    else:
        status = SCOPE_REVIEW
        reasons.append("INSUFFICIENT_SCOPE_EVIDENCE")

    role = _scope_role(page_role, owner_roles, has_symbol)
    confidence = min(
        [float(page.get("confidence") or 0.0)]
        + [float(item.get("confidence") or 0.0) for item in owner_decisions]
    ) if owner_decisions else float(page.get("confidence") or 0.0)
    if status == SCOPE_REVIEW:
        confidence = min(confidence, 0.65)
    if status == SCOPE_OUT:
        confidence = max(confidence, 0.72)

    return {
        "schema_version": "FeatureScopeDecision/v1",
        "scope_status": status,
        "scope_role": role,
        "page_semantic_role": page_role,
        "owner_semantic_roles": owner_roles,
        "positive_signals": positive_signals,
        "negative_signals": negative_signals,
        "reason_codes": sorted(set(reasons)),
        "confidence": round(max(0.0, min(0.99, confidence)), 3),
        "page_role_evidence": page,
        "owner_role_evidence": owner_decisions,
        "identity_readiness": {
            SCOPE_IN: "READY_FOR_DOWNSTREAM",
            SCOPE_REVIEW: "REVIEW_REQUIRED",
            SCOPE_OUT: "OUT_OF_SCOPE",
        }[status],
    }


__all__ = [
    "PAGE_SQL_FUNCTION",
    "PAGE_SQL_OPERATOR",
    "PAGE_SQL_DATATYPE",
    "PAGE_SQL_SYNTAX",
    "PAGE_FEATURE_CATALOG",
    "PAGE_CONFIG",
    "PAGE_SYSTEM_SCHEMA",
    "PAGE_SYSTEM_METRIC",
    "PAGE_ENGINE",
    "PAGE_FORMAT",
    "PAGE_API",
    "PAGE_EXAMPLE",
    "PAGE_UNKNOWN",
    "OWNER_DEFINITION",
    "OWNER_SIGNATURE",
    "OWNER_CATALOG",
    "OWNER_CONFIG",
    "OWNER_SYSTEM_COLUMN",
    "OWNER_SYSTEM_METRIC",
    "OWNER_ENGINE",
    "OWNER_FORMAT",
    "OWNER_API",
    "OWNER_EXAMPLE",
    "OWNER_PROSE",
    "OWNER_UNKNOWN",
    "SCOPE_IN",
    "SCOPE_OUT",
    "SCOPE_REVIEW",
    "decide_scope",
    "build_local_feature_evidence",
    "infer_owner_semantic_role",
    "infer_page_semantic_role",
]
