"""Generic document-surface roles used by the identity ledger.

The roles in this module describe how a document presents a value.  They are
deliberately independent from semantic feature types such as FUNCTION,
OPERATOR, or DATATYPE.  The vocabulary is intentionally small and generic so
that a new documentation site can use the same interpretation without a
database-specific selector.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


STRUCTURAL_ROLES = (
    "FEATURE_NAME",
    "SIGNATURE",
    "ALIAS",
    "DESCRIPTION",
    "PARAMETER",
    "EXAMPLE",
    "PROSE",
    "CODE_CONTEXT",
)

_ALIAS_HEADER = re.compile(r"\b(?:alias|aliases|synonym|synonyms|alternative(?:\s+name)?s?)\b", re.I)
_DESCRIPTION_HEADER = re.compile(r"\b(?:description|descriptions|meaning|purpose|details?|notes?|remarks?)\b", re.I)
_PARAMETER_HEADER = re.compile(r"\b(?:parameter|parameters|argument|arguments|arg|args|input|inputs|return|returns|result\s+type)\b", re.I)
_EXAMPLE_HEADER = re.compile(r"\b(?:example|examples|sample|samples|result|output|outputs|usage|usages)\b", re.I)
_SIGNATURE_HEADER = re.compile(r"\b(?:signature|signatures|syntax|call|calls|invocation|invocations|function(?:\s+name)?s?|routine(?:\s+name)?s?|command(?:\s+name)?s?)\b", re.I)
_FEATURE_HEADER = re.compile(r"\b(?:name|names|feature|features|keyword|keywords|operator|operators|predicate|predicates|specifier|specifiers|expression|expressions|type\s+name|declared\s+type)\b", re.I)
_TYPE_HEADER = re.compile(r"\b(?:data\s*[- ]?type|datatype|type\s+name|declared\s+type)\b", re.I)


def normalize_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def infer_column_role(header: Any, index: int) -> tuple[str, str | None]:
    """Return ``(structural_role, optional semantic type hint)``.

    A type hint here is only an explicit column label signal.  It is not an
    identity classifier and is later combined with owner/page evidence.
    """
    text = normalize_header(header)
    if not text:
        return ("FEATURE_NAME" if index == 0 else "PROSE", None)
    if _ALIAS_HEADER.search(text):
        return "ALIAS", None
    if _DESCRIPTION_HEADER.search(text):
        return "DESCRIPTION", None
    if _PARAMETER_HEADER.search(text):
        return "PARAMETER", None
    if _EXAMPLE_HEADER.search(text):
        return "EXAMPLE", None
    if _SIGNATURE_HEADER.search(text):
        hint = "FUNCTION" if re.search(r"\b(?:function|routine|call|invocation)s?\b", text, re.I) else None
        return "SIGNATURE", hint
    if _TYPE_HEADER.search(text):
        return "FEATURE_NAME", "DATATYPE"
    if _FEATURE_HEADER.search(text):
        hint = "OPERATOR" if re.search(r"\b(?:operator|predicate|expression)s?\b", text, re.I) else None
        return "FEATURE_NAME", hint
    # A first, short, identifier-like column is the safest generic fallback.
    # Later columns without a recognized label are supporting content, not an
    # invitation to tokenize prose.
    if index == 0:
        return "FEATURE_NAME", None
    return "PROSE", None


def infer_table_column_roles(headers: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "header": normalize_header(header),
            "structural_role": infer_column_role(header, index)[0],
            "surface_type_hint": infer_column_role(header, index)[1],
        }
        for index, header in enumerate(headers)
    ]


def split_surface_items(value: Any, *, split_separators: bool = True) -> list[str]:
    """Split a cell without splitting commas inside a signature.

    Alias cells commonly use comma, slash, semicolon, or pipe separators.  A
    small delimiter-aware scanner handles those forms while preserving calls
    such as ``foo(a, b)``.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return []
    if not split_separators:
        return [text]
    values: list[str] = []
    start = 0
    depth = {"(": 0, "[": 0, "{": 0}
    separators = {",", ";", "/", "|"}
    for index, char in enumerate(text):
        if char in depth:
            depth[char] += 1
        elif char == ")":
            depth["("] = max(depth["("] - 1, 0)
        elif char == "]":
            depth["["] = max(depth["["] - 1, 0)
        elif char == "}":
            depth["{"] = max(depth["{"] - 1, 0)
        elif char in separators and not any(depth.values()):
            item = text[start:index].strip()
            if item:
                values.append(item)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        values.append(tail)
    return values


def table_metadata_roles(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = block.get("table_metadata") or {}
    headers = list(metadata.get("headers") or [])
    return infer_table_column_roles(headers)


def is_header_row(block: Mapping[str, Any], roles: list[Mapping[str, Any]]) -> bool:
    metadata = block.get("table_metadata") or {}
    cells = [normalize_header(value).casefold() for value in (metadata.get("cells") or [])]
    headers = [normalize_header(row.get("header")).casefold() for row in roles]
    return bool(headers and cells and cells == headers)
