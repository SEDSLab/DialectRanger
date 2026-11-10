"""DOM-first Owner inference for an offline, shadow-only replay.

The module intentionally stops at Owner hypotheses.  It reads already frozen
DocumentIR metadata and local HTML, generates several auditable Owner
candidates, ranks them deterministically, and optionally feeds only the
selected hypotheses into the existing identity ledger as a dry replay.

It is not imported by :mod:`agents.owner.generic` and therefore cannot change
the current production Owner path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from agents.contracts.base import stable_id
from agents.contracts.owner import make_owner_hypothesis
from agents.contracts.owner_inference import (
    deterministic_candidate_key,
    make_owner_candidate,
)
from agents.document_structure_v2.containment import build_structural_document
from agents.identity.production import (
    build_candidate_ledger,
    build_identity_occurrences,
    reconcile_identity_groups,
    validate_candidate_ledger,
)
from agents.profile_bootstrap.cold_start import profile_hints_for_page, profile_structural_hints


SCHEMA_VERSION = "mysql-dom-first-owner-shadow/v1"
PARSER_PROFILE = {
    "main_selectors": ["#docs-body", "main", "article", "[role='main']", "body"],
    "noise_selectors": ["nav", "header", "footer", "aside", "script", "style"],
}

_CALL_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_$]*)\s*\(([^\n()]*)\)")
_FULL_CALL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*\([^\n()]*\)$")
_LABEL_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n()]*\)|[+*/<>=!|&^~%?\-\[\]]{1,4}")
_SYMBOL_RE = re.compile(r"^[+*/<>=!|&^~%?\-\[\]]{1,4}$")
_SYMBOL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[+*/<>=!|&^~%?\-\[\]]{1,4}(?![A-Za-z0-9_])")
_ATOMIC_RE = re.compile(r"^[A-Z][A-Z0-9_$]*(?:_[A-Z0-9_$]+)*$")
_PROMPT_RE = re.compile(r"^(?:mysql>|psql>|duckdb>|sqlite>|clickhouse>|postgres(?:ql)?>|[A-Za-z]:\\>)", re.I)
_EXAMPLE_COMMAND_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|EXPLAIN|SHOW|SET)\b|"
    r"(?:mysql>|psql>|duckdb>|sqlite>|clickhouse>|postgres(?:ql)?>)", re.I,
)
_EXAMPLE_HEADING_RE = re.compile(r"\b(?:example|examples|usage|invocation|try it|demonstrat)\b", re.I)
_STOPWORDS = {
    "A", "AN", "AND", "AS", "AT", "BY", "FOR", "FROM", "IN", "IS", "OF", "ON", "OR", "THE", "TO", "WITH",
    "FUNCTION", "FUNCTIONS", "OPERATOR", "OPERATORS", "DATA", "TYPE", "TYPES", "EXAMPLE", "EXAMPLES",
    "SYNTAX", "DESCRIPTION", "DEFINITION", "MEANING", "DETAILS", "PARAMETERS", "PARAMETER", "ARGUMENTS", "ARGUMENT",
    "RETURNS", "RETURN", "RETURNED", "REFERENCE", "OVERVIEW",
    "USAGE", "NOTE", "NOTES", "NULL", "TRUE", "FALSE", "SELECT", "INSERT", "UPDATE", "DELETE",
    "CREATE", "ALTER", "DROP", "TABLE", "VIEW", "INDEX", "WHERE", "GROUP", "ORDER", "BY", "CASE",
}

_OPERATOR_LABEL_RE = re.compile(
    r"\b(?:operator|operators|predicate|predicates|expression|expressions)\b\s*[:\-]\s*(.+)$",
    re.I,
)
_OPERATOR_TRAILING_LABEL_RE = re.compile(
    r"^(.+?)\s+\b(?:operator|operators|predicate|predicates)\b\s*#?$",
    re.I,
)
_OPERATOR_TEXT_RE = re.compile(
    r"\b(?:operator|operators|predicate|predicates)\b[^.]{0,80}?\b(?:is|are|written|denoted|sets|used)\b[^.]{0,80}",
    re.I,
)
_OPERATOR_STRUCTURAL_WORDS = {
    "OPERATOR", "OPERATORS", "PREDICATE", "PREDICATES", "EXPRESSION", "EXPRESSIONS",
    "FUNCTION", "FUNCTIONS", "ROUTINE", "ROUTINES",
    "COMPARISON", "QUANTIFIED", "RANGE", "LOGICAL", "MATHEMATICAL", "SUBSCRIPT",
    "CONDITIONAL", "CONCATENATION", "PATTERN", "CONVERSION", "TIME", "ZONE",
}

_STRUCTURAL_KIND_TO_OWNER = {
    "TABLE_ROW": ("TABLE_ROW_OWNER", "table_row"),
    "LIST_ITEM": ("LIST_ITEM_OWNER", "list_item"),
    "DEFINITION_ENTRY": ("DEFINITION_LIST_OWNER", "definition_list"),
    "SECTION": ("SECTION_OWNER", "section"),
    "GENERIC_BLOCK_CONTAINER": ("CONTAINER_OWNER", "container"),
    "ARTICLE_MAIN": ("PAGE_OWNER", "page"),
    "PAGE": ("PAGE_OWNER", "page"),
}

_OWNER_KIND_TO_LEGACY = {
    "PAGE_OWNER": "PageOwner",
    "SECTION_OWNER": "SectionOwner",
    "CONTAINER_OWNER": "ContainerOwner",
    "HEADING_SECTION_OWNER": "HeadingSectionOwner",
    "TABLE_ROW_OWNER": "TableRowOwner",
    "LIST_ITEM_OWNER": "ListItemOwner",
    "DEFINITION_LIST_OWNER": "DefinitionListOwner",
    "SIGNATURE_SPAN_OWNER": "SignatureOwner",
    "CARD_OWNER": "CardOwner",
}

_STRUCTURAL_OWNER_KINDS = {"TABLE_ROW_OWNER", "LIST_ITEM_OWNER", "DEFINITION_LIST_OWNER", "SECTION_OWNER", "CONTAINER_OWNER", "PAGE_OWNER"}
# DocumentIR uses ``repeated_unit`` as the generic DOM boundary for several
# repeated surfaces.  The old owner path only understood the semantic names
# above, so a page containing definition/list/table units could fall back to a
# page-wide owner.  Keep the semantic names as the owner contract and map the
# parser boundary to them at the production edge.
_REPEATED_PARENT_KINDS = {"TABLE_ROW", "LIST_ITEM", "DEFINITION_ENTRY", "REPEATED_UNIT"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm_identity(value: str) -> str:
    text = _compact(value).upper()
    match = _FULL_CALL_RE.fullmatch(text)
    if match:
        return match.group(1).upper()
    return text


def _is_atomic(value: str) -> bool:
    text = _compact(value).upper()
    return bool(_ATOMIC_RE.fullmatch(text)) and text not in _STOPWORDS and len(text) <= 96


def _is_call(value: str) -> bool:
    return bool(_FULL_CALL_RE.fullmatch(_compact(value)))


def _split_identity_label(value: Any) -> list[str]:
    """Split a bounded label containing equivalent/peer signatures.

    The split is accepted only when the whole surface consists of signature
    or symbolic tokens separated by punctuation.  This avoids turning prose
    paragraphs containing examples into defining anchors.
    """
    text = _compact(value)
    if not text:
        return []
    matches = list(_LABEL_TOKEN_RE.finditer(text))
    if len(matches) < 2:
        return []
    remainder = _LABEL_TOKEN_RE.sub("", text)
    if remainder.strip(" ,;|/"):
        return []
    return [_compact(match.group(0)) for match in matches]


def _table_subject_identity(raw: Any) -> str | None:
    """Return an explicit identity-shaped table-cell label, if any.

    A catalog/mapping row can contain more than one documented subject (for
    example an alternate vendor spelling and the corresponding MySQL type).
    The first cell remains the defining anchor used for ranking; the other
    explicit labels are retained as subject evidence so alias/mapping rows do
    not look like cross-owner contamination during evaluation.  Prose cells
    are intentionally excluded.
    """
    text = _compact(raw)
    if not text or _PROMPT_RE.match(text) or _EXAMPLE_COMMAND_RE.search(text):
        return None
    if _is_call(text) or _SYMBOL_RE.fullmatch(text):
        return _norm_identity(text)
    if not _is_atomic(text):
        return None
    # Table labels are normally uppercase, symbolic, underscored, or
    # versioned.  Requiring one of those shapes prevents cells such as
    # "Returns" or "Value" from becoming feature subjects.
    if text == text.upper() or any(ch in text for ch in "_$0123456789"):
        return text.upper()
    return None


def _table_subject_evidence(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    if _block_kind(block) != "TABLE_ROW":
        return []
    metadata = block.get("table_metadata") or {}
    cells = list(metadata.get("cells") or [])
    evidence: list[dict[str, Any]] = []
    for index, cell in enumerate(cells):
        identity = _table_subject_identity(cell)
        if identity:
            evidence.append({"identity": identity, "raw": _compact(cell), "cell_index": index})
    return evidence


def _block_kind(block: Mapping[str, Any]) -> str:
    return str(block.get("block_kind") or "OTHER").upper()


def _is_example_block(block: Mapping[str, Any]) -> bool:
    text = _compact(block.get("text"))
    ancestry = " ".join(str(x) for x in block.get("heading_ancestry") or [])
    kind = _block_kind(block)
    # Catalog rows and definition/heading labels can contain words such as
    # "Insert" or "Update" in their descriptions.  Those are not executable
    # examples; only code blocks or command-shaped text roots qualify.
    if kind in {"TABLE_ROW", "DEFINITION", "HEADING"}:
        return False
    if kind == "CODE" and _EXAMPLE_COMMAND_RE.search(text):
        return True
    if kind in {"TEXT", "LIST_ITEM"} and re.match(r"^(?:mysql>|psql>|duckdb>|sqlite>|clickhouse>|postgres(?:ql)?>|SELECT\b|INSERT\b|UPDATE\b|DELETE\b|CREATE\b|ALTER\b|DROP\b|WITH\b)", text, re.I):
        return True
    if _EXAMPLE_HEADING_RE.search(ancestry):
        return True
    return kind == "CODE" and not _is_call(text) and not bool(_SYMBOL_RE.fullmatch(text))


def _operator_phrase(value: Any, *, explicit: bool = False) -> str | None:
    """Return a bounded symbolic/compound operator surface.

    This is a grammar-level parser, not a database operator dictionary.  It
    only accepts symbols or short uppercase SQL phrases when the surrounding
    document has already supplied an operator/predicate surface cue.
    """
    text = _compact(value).strip("#:")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if _SYMBOL_RE.fullmatch(text):
        return text
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*){0,5}", text):
        return None
    words = text.upper().split()
    if not explicit and not all(word == word.upper() for word in text.split()):
        return None
    if not explicit and all(word in _OPERATOR_STRUCTURAL_WORDS for word in words):
        return None
    if any(word in {"THE", "THIS", "USED", "WITH", "THAT", "WHICH", "AND", "EXPRESSION", "EXPRESSIONS"} for word in words):
        return None
    return " ".join(words)


def _operator_heading_values(text: str, ancestry: str) -> list[str]:
    """Extract operator labels from generic heading grammars."""
    title = _compact(text).rstrip("#").strip()
    if not title:
        return []
    values: list[str] = []
    labelled = _OPERATOR_LABEL_RE.search(title)
    if labelled:
        payload = labelled.group(1)
        parts = re.split(r"\s*(?:,|;|\band\b)\s*", payload, flags=re.I)
        values.extend(value for value in (_operator_phrase(part, explicit=True) for part in parts) if value)
    trailing = _OPERATOR_TRAILING_LABEL_RE.match(title)
    if trailing:
        value = _operator_phrase(trailing.group(1), explicit=True)
        if value:
            values.append(value)
    # A heading such as "IS NULL and IS NOT NULL" has no literal role word,
    # but its bounded all-caps peer list is still a defining operator surface.
    if re.search(r"\b(?:operator|operators|predicate|predicates|expression|expressions)\b", ancestry + " " + title, re.I):
        parts = re.split(r"\s*(?:,|;|\band\b)\s*", title, flags=re.I)
        if len(parts) > 1:
            values.extend(value for value in (_operator_phrase(part) for part in parts) if value)
    return list(dict.fromkeys(values))


def _operator_text_values(text: str, ancestry: str) -> list[str]:
    """Extract only explicitly described operator surfaces from prose."""
    values: list[str] = []
    # "The AT TIME ZONE operator ..." and equivalent prose forms.
    for match in re.finditer(
        r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]*(?:\s+[A-Z][A-Z0-9]*){0,5})\s+\b(?:operator|predicate)\b",
        text,
    ):
        value = _operator_phrase(match.group(1), explicit=True)
        if value:
            values.append(value)
    # "expressions are written with ->" / "denoted by ||".
    for match in re.finditer(r"\b(?:written|denoted|represented)\s+(?:with|by)\s+([^\s,.;:)]+)", text, re.I):
        value = _operator_phrase(match.group(1), explicit=True)
        if value:
            values.append(value)
    return list(dict.fromkeys(values))


def _surface_values(block: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return bounded candidate surfaces, never all prose tokens."""
    kind = _block_kind(block)
    text = _compact(block.get("text"))
    if not text:
        return []
    if kind == "TABLE_ROW":
        metadata = block.get("table_metadata") or {}
        cells = list(metadata.get("cells") or [])
        headers = [_compact(value).casefold() for value in metadata.get("headers") or []]
        if cells:
            if headers and _compact(cells[0]).casefold() == headers[0] and all(
                index >= len(cells) or not _compact(cells[index]) or _compact(cells[index]).casefold() == headers[index]
                for index in range(min(len(headers), len(cells)))
            ):
                return []
            labels = _split_identity_label(cells[0])
            if labels:
                return [(label, "table_row") for label in labels]
            return [(_compact(cells[0]), "table_row")]
        return [(text.split(" ", 1)[0], "table_row")]
    if kind == "DEFINITION":
        # dt/dd are both represented as DEFINITION by DocumentIR.  A
        # definition pair starts with a bounded signature/label and is
        # followed by prose.  Only the leading surface is an owner anchor;
        # never scan the body for identities.
        signature = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*\(", text)
        if signature:
            return [(signature.group(1), "definition_list")]
        leading = re.split(r"\s+(?:returns?|is|means|describes?)\s+|[:;—–-]", text, maxsplit=1, flags=re.I)[0].strip()
        if _is_atomic(leading):
            return [(leading, "definition_list")]
        return []
    if kind == "HEADING":
        # Reference pages often label a symbolic section as
        # ``Subscript operator: []`` rather than using the symbol alone as the
        # heading.  The role word is structural context, not a feature-name
        # dictionary.
        title = text.rstrip("#").strip()
        ancestry = " ".join(str(value) for value in block.get("heading_ancestry") or [])
        operator_values = _operator_heading_values(title, ancestry)
        if operator_values:
            return [(value, "heading") for value in operator_values]
        alias_parts = [part.strip() for part in re.split(r"\s+(?:OR|/|,|\|)\s+", title, flags=re.I) if part.strip()]
        if len(alias_parts) > 1 and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", part) for part in alias_parts):
            return [(part, "heading") for part in alias_parts]
        return [(title, "heading")]
    if kind in {"TEXT", "CODE", "LIST_ITEM"}:
        ancestry = " ".join(str(value) for value in block.get("heading_ancestry") or [])
        operator_values = _operator_text_values(text, ancestry)
        if operator_values:
            return [(value, "text_operator") for value in operator_values]
        labels = _split_identity_label(text)
        if labels:
            return [(label, kind.casefold()) for label in labels]
        return [(text, kind.casefold())]
    return []


def _strong_identity(raw: str, source: str, block: Mapping[str, Any]) -> tuple[str, list[str]] | None:
    value = _compact(raw)
    if not value:
        return None
    if _PROMPT_RE.match(value) or _EXAMPLE_COMMAND_RE.search(value):
        return None
    if _is_call(value):
        name = _norm_identity(value)
        return (name, ["STANDALONE_SIGNATURE", source.upper(), "DEFINING_SURFACE"])
    if _SYMBOL_RE.fullmatch(value):
        return (value, ["ATOMIC_SYMBOL", source.upper(), "DEFINING_SURFACE"])
    if source in {"heading", "text_operator"}:
        operator_value = _operator_phrase(value, explicit=(source == "text_operator"))
        if operator_value:
            return (operator_value, ["COMPOUND_OPERATOR_SURFACE", source.upper(), "DEFINING_SURFACE"])
    if _is_atomic(value):
        # Atomic prose words are accepted only on bounded/label-like surfaces;
        # an ordinary sentence is never scanned for an identity.
        if source in {"table_row", "definition_list", "heading", "list_item"} or _block_kind(block) == "HEADING":
            return (value.upper(), ["ATOMIC_IDENTITY", source.upper(), "DEFINING_SURFACE"])
    # A word operator such as AND/OR/NOT is intentionally a stopword on
    # prose surfaces.  An explicit operator-labelled table is different: its
    # first cell is a bounded subject slot, so the table role—not the word
    # itself—is the generic evidence that makes it an identity anchor.
    if source == "table_row":
        headers = " ".join(
            _compact(item) for item in (block.get("table_metadata") or {}).get("headers") or []
        )
        if re.search(r"\b(?:operator|predicate|expression|keyword)\b", headers, re.I) and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_$]*(?:\s+[A-Za-z_][A-Za-z0-9_$]*){0,3}", value,
        ):
            return (value.upper(), ["STRUCTURAL_OPERATOR_COLUMN", "TABLE_ROW", "DEFINING_SURFACE"])
    return None


def _anchor_id(page_id: str, block_id: str, raw: str, role: str) -> str:
    return stable_id("feature_anchor", [page_id, block_id, raw, role])


def extract_feature_anchors(document: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract strong bounded anchors and non-defining identity-shaped tokens."""
    strong: list[dict[str, Any]] = []
    shaped: list[dict[str, Any]] = []
    page_id = str(document.get("page_id") or "")
    for block in document.get("blocks") or []:
        block_id = str(block.get("block_id") or "")
        text = _compact(block.get("text"))
        kind = _block_kind(block)
        example = _is_example_block(block)
        for raw, source in _surface_values(block):
            identity = _strong_identity(raw, source, block)
            if identity and not example:
                normalized, reasons = identity
                strong.append({
                    "anchor_id": _anchor_id(page_id, block_id, raw, "FEATURE_ANCHOR"),
                    "page_id": page_id, "block_id": block_id, "block_kind": kind,
                    "raw": _compact(raw), "identity": normalized, "source": source,
                    "role": "FEATURE_ANCHOR", "strong": True, "example_root": False,
                    "confidence": 0.98 if source in {"table_row", "definition_list", "heading"} else 0.90,
                    "reasons": sorted(set(reasons)),
                })
        for match in _CALL_RE.finditer(text):
            raw = _compact(match.group(0))
            normalized = _norm_identity(raw)
            if not normalized or normalized in _STOPWORDS:
                continue
            if any(row["block_id"] == block_id and row["identity"] == normalized for row in strong):
                continue
            role = "EXAMPLE_TOKEN" if example else "IDENTITY_SHAPED_TOKEN"
            shaped.append({
                "anchor_id": _anchor_id(page_id, block_id, raw, role),
                "page_id": page_id, "block_id": block_id, "block_kind": kind,
                "raw": raw, "identity": normalized, "source": "code_token" if kind == "CODE" else "text_token",
                "role": role, "strong": False, "example_root": bool(example),
                "confidence": 0.12 if example else 0.25,
                "reasons": ["EXAMPLE_CONTEXT" if example else "NON_ATOMIC_CONTEXT"],
            })
        if example:
            for match in _SYMBOL_TOKEN_RE.finditer(text):
                raw = match.group(0)
                if raw in {"-", ">", "<", "=", "+", "*", "/"} and _EXAMPLE_COMMAND_RE.search(text):
                    continue
                shaped.append({
                    "anchor_id": _anchor_id(page_id, block_id, raw, "EXAMPLE_TOKEN"),
                    "page_id": page_id, "block_id": block_id, "block_kind": kind,
                    "raw": raw, "identity": raw, "source": "code_symbol",
                    "role": "EXAMPLE_TOKEN", "strong": False, "example_root": True,
                    "confidence": 0.05, "reasons": ["EXAMPLE_CONTEXT", "SYMBOL_IN_EXAMPLE"],
                })
    return strong, shaped


def _ordered_blocks(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (dict(block) for block in document.get("blocks") or []),
        key=lambda row: (int(row.get("order", 0)), str(row.get("block_id"))),
    )


def _block_index(blocks: list[Mapping[str, Any]]) -> dict[str, int]:
    return {str(block.get("block_id")): index for index, block in enumerate(blocks)}


def _identity_anchors(anchors: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for anchor in anchors:
        if anchor.get("strong"):
            result[str(anchor.get("block_id"))].append(dict(anchor))
    return result


def _candidate_spec(
    document: Mapping[str, Any],
    blocks: list[Mapping[str, Any]],
    anchors: list[Mapping[str, Any]],
    shaped: list[Mapping[str, Any]],
    *,
    owner_kind: str,
    template: str,
    member_blocks: Iterable[Mapping[str, Any]],
    structural_parent_id: str | None,
    structural_parent_kind: str | None,
    structural_signals: Iterable[str],
    profile: Mapping[str, Any],
    origin: str,
) -> dict[str, Any] | None:
    members = sorted(
        (dict(row) for row in member_blocks),
        key=lambda row: (int(row.get("order", 0)), str(row.get("block_id"))),
    )
    if not members:
        return None
    member_ids = {str(row.get("block_id")) for row in members}
    member_anchors = [dict(row) for row in anchors if str(row.get("block_id")) in member_ids and row.get("strong")]
    if not member_anchors:
        return None
    member_shaped = [dict(row) for row in shaped if str(row.get("block_id")) in member_ids]
    identities = sorted({str(row.get("identity") or "") for row in member_anchors if row.get("identity")})
    # Keep explicitly labelled secondary table cells as subject evidence, but
    # do not turn them into peer anchors.  This is important for generic
    # catalog/mapping rows such as ``INT8 | BIGINT``: one structural Owner
    # legitimately represents both documented labels.
    table_subject_evidence: list[dict[str, Any]] = []
    for member in members:
        table_subject_evidence.extend(_table_subject_evidence(member))
    for evidence in table_subject_evidence:
        identity = str(evidence.get("identity") or "")
        if identity:
            identities.append(identity)
    identities = sorted(set(identities))
    primary_anchor = sorted(member_anchors, key=lambda row: (int(next((b.get("order", 0) for b in members if str(b.get("block_id")) == row.get("block_id")), 0)), str(row.get("anchor_id"))))[0]
    primary = str(primary_anchor.get("identity") or "")
    kind = str(structural_parent_kind or "")
    structural_score = {
        "TABLE_ROW_OWNER": 0.98, "LIST_ITEM_OWNER": 0.97, "DEFINITION_LIST_OWNER": 0.96,
        "SECTION_OWNER": 0.82, "CONTAINER_OWNER": 0.74, "PAGE_OWNER": 0.88,
        "HEADING_SECTION_OWNER": 0.84, "SIGNATURE_SPAN_OWNER": 0.36,
    }.get(owner_kind, 0.50)
    defining_score = float(primary_anchor.get("confidence") or 0.5)
    peer_count = 0
    if kind in _REPEATED_PARENT_KINDS:
        peer_count = sum(1 for block in blocks if str(block.get("structural_parent_kind") or "") == kind)
    peer_symmetry = 0.92 if kind in _REPEATED_PARENT_KINDS and peer_count >= 2 else (0.72 if owner_kind == "HEADING_SECTION_OWNER" else 0.35)
    kind_values = {_block_kind(row) for row in members}
    semantic = min(1.0, 0.22 * len(kind_values) + 0.06 * min(len(members), 8))
    boundary = {
        "TABLE_ROW_OWNER": 0.99, "LIST_ITEM_OWNER": 0.98, "DEFINITION_LIST_OWNER": 0.98,
        "HEADING_SECTION_OWNER": 0.88, "SECTION_OWNER": 0.82, "CONTAINER_OWNER": 0.70,
        "PAGE_OWNER": 0.76, "SIGNATURE_SPAN_OWNER": 0.42,
    }.get(owner_kind, 0.45)
    hint_rows = profile_hints_for_page(document, profile) if profile else []
    hint_names = {str(row.get("owner_strategy") or "") for row in hint_rows}
    profile_hint_bonus = 0.05 if template in hint_names else 0.0
    examples = [row for row in member_shaped if row.get("role") == "EXAMPLE_TOKEN"]
    root_is_example = _is_example_block(members[0]) or primary_anchor.get("role") == "EXAMPLE_TOKEN"
    example_penalty = 0.85 if root_is_example else (0.06 if examples else 0.0)
    # Secondary explicit table labels are subject/alias evidence, not peer
    # anchors.  Only additional strong anchor identities participate in the
    # cross-feature contamination penalty.
    anchor_identities = {str(row.get("identity") or "") for row in member_anchors if row.get("identity")}
    foreign_identities = sorted(anchor_identities - {primary})
    contamination_penalty = min(1.0, 0.25 * len(foreign_identities))
    weak_anchor_penalty = 0.0 if primary_anchor.get("source") in {"table_row", "definition_list", "heading", "text", "code", "list_item"} else 0.20
    contamination_flags = []
    if foreign_identities:
        contamination_flags.append("MULTIPLE_STRONG_PEER_ANCHORS")
    if examples and root_is_example:
        contamination_flags.append("EXAMPLE_ROOT")
    scores = {
        "structural_containment_score": structural_score,
        "defining_anchor_score": defining_score,
        "peer_symmetry_score": peer_symmetry,
        "semantic_completeness_score": semantic,
        "boundary_confidence": boundary,
        "profile_hint_bonus": profile_hint_bonus,
        "example_penalty": example_penalty,
        "contamination_penalty": contamination_penalty,
        "weak_anchor_penalty": weak_anchor_penalty,
    }
    positive = (
        0.30 * structural_score + 0.25 * defining_score + 0.15 * peer_symmetry
        + 0.12 * semantic + 0.10 * boundary + 0.03 * profile_hint_bonus
    )
    confidence = max(0.0, min(1.0, positive - 0.04 * example_penalty - 0.10 * contamination_penalty - 0.08 * weak_anchor_penalty))
    tier = "STRONG" if confidence >= 0.78 else "PROBABLE" if confidence >= 0.55 else "WEAK"
    reason_codes = ["DOM_CONTAINMENT", "DEFINING_ANCHOR", origin.upper()]
    if peer_symmetry >= 0.8:
        reason_codes.append("PEER_SYMMETRY")
    if profile_hint_bonus:
        reason_codes.append("PROFILE_HINT_AS_BONUS_ONLY")
    if examples:
        reason_codes.append("EXAMPLE_TOKEN_SEPARATED")
    if foreign_identities:
        reason_codes.append("CONTAMINATION_REVIEW")
    first_id = str(members[0].get("block_id"))
    last_id = str(members[-1].get("block_id"))
    candidate = make_owner_candidate(
        dbms=str(document.get("dbms") or "mysql"), page_id=str(document.get("page_id") or ""),
        source_url=str(document.get("url") or ""), page_hash=str(document.get("page_hash") or ""),
        html_path=str(document.get("html_path") or ""), owner_kind=owner_kind, template=template,
        start_block_id=first_id, end_block_id=last_id, root_block_id=str(primary_anchor.get("block_id") or first_id),
        member_block_ids=[str(row.get("block_id")) for row in members],
        structural_parent_id=structural_parent_id, structural_parent_kind=structural_parent_kind,
        ancestor_chain=list(primary_anchor.get("ancestor_chain") or next((row.get("ancestor_chain") for row in members if row.get("ancestor_chain")), [])),
        sibling_index=next((row.get("sibling_index") for row in members if row.get("sibling_index") is not None), None),
        identity_anchor_ids=[str(row.get("anchor_id")) for row in member_anchors],
        identity_shaped_token_ids=[str(row.get("anchor_id")) for row in member_shaped],
        structural_signals=structural_signals,
        profile_hints=sorted(hint_names), ranking_features=scores,
        contamination_flags=contamination_flags, confidence=confidence, tier=tier,
        status="CANDIDATE", parent_ids=[str(document.get("document_id") or ""), str(structural_parent_id or "")],
        reason_codes=reason_codes,
    )
    candidate.update({
        "primary_identity": primary,
        "identity_values": identities,
        "subject_identities": identities,
        "subject_evidence": table_subject_evidence,
        "anchor_evidence": member_anchors,
        "identity_shaped_evidence": member_shaped,
        "origin": origin,
        "primary_anchor_source": str(primary_anchor.get("source") or ""),
        "primary_anchor_block_id": str(primary_anchor.get("block_id") or ""),
        "root_is_example": bool(root_is_example),
        "foreign_identity_values": foreign_identities,
        "profile_hints_used": sorted(hint_names),
        "profile_hint_overridden_by_structure": bool(hint_names and template not in hint_names),
        "member_block_kinds": sorted(kind_values),
    })
    return candidate


def _section_end(blocks: list[Mapping[str, Any]], start: int) -> int:
    start_level = int(blocks[start].get("heading_level") or 1)
    for index in range(start + 1, len(blocks)):
        if _block_kind(blocks[index]) == "HEADING" and int(blocks[index].get("heading_level") or 1) <= start_level:
            return index - 1
    return len(blocks) - 1


def generate_owner_candidates(document: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    blocks = _ordered_blocks(document)
    block_positions = _block_index(blocks)
    strong, shaped = extract_feature_anchors(document)
    by_block = _identity_anchors(strong)
    candidates: list[dict[str, Any]] = []
    seen_specs: set[tuple[str, str, tuple[str, ...]]] = set()
    profile = profile or {}

    def add(**kwargs: Any) -> None:
        candidate = _candidate_spec(document, blocks, strong, shaped, profile=profile, **kwargs)
        if not candidate:
            return
        key = (str(candidate.get("owner_kind")), str(candidate.get("primary_identity")), tuple(candidate.get("member_block_ids") or []))
        if key not in seen_specs:
            seen_specs.add(key)
            candidates.append(candidate)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        kind = str(block.get("structural_parent_kind") or "")
        parent = str(block.get("structural_parent_id") or "")
        if kind and parent:
            groups[(kind, parent)].append(block)
    for (parent_kind, parent_id), members in sorted(groups.items()):
        # Structural metadata is not guaranteed to preserve the enum casing
        # across parser adapters (``table_row`` and ``TABLE_ROW`` both occur
        # in frozen DocumentIR).  Normalize before selecting an owner
        # primitive; otherwise explicit row boundaries silently become a
        # page-wide fallback.
        effective_parent_kind = str(parent_kind or "").upper()
        if effective_parent_kind == "REPEATED_UNIT":
            member_kinds = {_block_kind(member) for member in members}
            if "TABLE_ROW" in member_kinds:
                effective_parent_kind = "TABLE_ROW"
            elif "LIST_ITEM" in member_kinds:
                effective_parent_kind = "LIST_ITEM"
            elif "DEFINITION" in member_kinds:
                effective_parent_kind = "DEFINITION_ENTRY"
        if effective_parent_kind not in {"TABLE_ROW", "LIST_ITEM", "DEFINITION_ENTRY"}:
            continue
        owner_kind, template = {
            "TABLE_ROW": ("TABLE_ROW_OWNER", "table_row"),
            "LIST_ITEM": ("LIST_ITEM_OWNER", "list_item"),
            "DEFINITION_ENTRY": ("DEFINITION_LIST_OWNER", "definition_list"),
        }[effective_parent_kind]
        add(owner_kind=owner_kind, template=template, member_blocks=members,
            structural_parent_id=parent_id, structural_parent_kind=effective_parent_kind,
            structural_signals=["EXPLICIT_STRUCTURAL_PARENT", "SIBLING_BOUNDARY"], origin=f"{template}_container")

    # Explicit heading-bounded sections.  A page-level generic container is
    # admitted only when the section contains one primary defining anchor.
    for index, block in enumerate(blocks):
        if _block_kind(block) != "HEADING" or not by_block.get(str(block.get("block_id"))):
            continue
        end = _section_end(blocks, index)
        members = blocks[index:end + 1]
        anchors_in_section = [row for row in strong if str(row.get("block_id")) in {str(item.get("block_id")) for item in members}]
        primary = by_block.get(str(block.get("block_id")), [])
        primary_identity = str(primary[0].get("identity") or "") if primary else ""
        if primary_identity:
            add(owner_kind="HEADING_SECTION_OWNER", template="heading_section", member_blocks=members,
                structural_parent_id=str(block.get("structural_parent_id") or ""),
                structural_parent_kind=str(block.get("structural_parent_kind") or ""),
                structural_signals=["HEADING_BOUNDED_SECTION", "EXPLICIT_HEADING"], origin="heading_section")

    # A section/container can be a useful candidate for a single-feature page,
    # but it is never allowed to absorb a catalog with multiple strong peers.
    for (parent_kind, parent_id), members in sorted(groups.items()):
        if parent_kind not in {"SECTION", "GENERIC_BLOCK_CONTAINER", "ARTICLE_MAIN", "PAGE"}:
            continue
        anchors_in = [row for row in strong if str(row.get("block_id")) in {str(item.get("block_id")) for item in members}]
        identities = {str(row.get("identity")) for row in anchors_in}
        if len(identities) != 1:
            continue
        owner_kind, template = _STRUCTURAL_KIND_TO_OWNER.get(parent_kind, ("CONTAINER_OWNER", "container"))
        add(owner_kind=owner_kind, template=template, member_blocks=members,
            structural_parent_id=parent_id, structural_parent_kind=parent_kind,
            structural_signals=["SINGLE_FEATURE_CONTAINER", "SINGLE_PRIMARY_IDENTITY"], origin="single_feature_container")

    all_identities = {str(row.get("identity")) for row in strong}
    repeated_candidates = [row for row in candidates if row.get("owner_kind") in {"TABLE_ROW_OWNER", "LIST_ITEM_OWNER", "DEFINITION_LIST_OWNER"}]
    if len(all_identities) == 1 and not repeated_candidates and strong:
        add(owner_kind="PAGE_OWNER", template="page", member_blocks=blocks,
            structural_parent_id=None, structural_parent_kind="PAGE",
            structural_signals=["SINGLE_FEATURE_PAGE", "PAGE_BOUNDARY"], origin="page_singleton")

    # Signature spans are deliberately retained as fallbacks.  They are not
    # allowed to outrank an explicit repeated container with the same local
    # surface, but can complement it when a page has a separate prose span.
    for anchor in sorted(strong, key=lambda row: (block_positions.get(str(row.get("block_id")), 0), str(row.get("anchor_id")))):
        start = block_positions.get(str(anchor.get("block_id")))
        if start is None:
            continue
        # Repeated DOM units already have an explicit structural Owner.  A
        # signature span over a table/definition row would only re-bind that
        # row to a neighboring identity when a label is weakly shaped; keep
        # fallback spans for standalone text/code surfaces instead.
        if _block_kind(blocks[start]) in {"TABLE_ROW", "DEFINITION"}:
            continue
        primary = str(anchor.get("identity") or "")
        # Component-rendered reference pages commonly put the formal
        # signature after several ``data-as=p`` blocks.  When the anchor is a
        # standalone signature, lift the fallback owner to the nearest
        # identity heading and keep the complete heading-bounded section.  No
        # feature vocabulary or site selector is involved; the DOM heading is
        # the owner boundary.
        section_start = start
        section_level = int(blocks[start].get("heading_level") or 6)
        for cursor in range(start - 1, -1, -1):
            if _block_kind(blocks[cursor]) == "HEADING":
                heading_identity = {
                    str(item.get("identity") or "")
                    for item in by_block.get(str(blocks[cursor].get("block_id")), [])
                }
                if not heading_identity or primary in heading_identity:
                    section_start = cursor
                    section_level = int(blocks[cursor].get("heading_level") or 1)
                break
        end = section_start
        for index in range(section_start + 1, len(blocks)):
            next_anchors = by_block.get(str(blocks[index].get("block_id")), [])
            if next_anchors and any(str(item.get("identity")) != primary for item in next_anchors):
                break
            if _block_kind(blocks[index]) == "HEADING" and int(blocks[index].get("heading_level") or 1) <= section_level:
                break
            end = index
        members = blocks[section_start:end + 1]
        parent_id = str(anchor.get("structural_parent_id") or blocks[start].get("structural_parent_id") or "") or None
        parent_kind = str(anchor.get("structural_parent_kind") or blocks[start].get("structural_parent_kind") or "") or None
        add(owner_kind="SIGNATURE_SPAN_OWNER", template="signature", member_blocks=members,
            structural_parent_id=parent_id, structural_parent_kind=parent_kind,
            structural_signals=["STANDALONE_SIGNATURE", "SIGNATURE_FALLBACK"], origin="signature_span")

    for candidate in candidates:
        candidate["structural_block_count"] = len(candidate.get("member_block_ids") or [])
    diagnostics = {
        "page_id": str(document.get("page_id") or ""),
        "url": str(document.get("url") or ""),
        "block_count": len(blocks),
        "strong_anchor_count": len(strong),
        "identity_shaped_token_count": len(shaped),
        "strong_identities": sorted(all_identities),
        "example_token_block_ids": sorted({str(row.get("block_id")) for row in shaped if row.get("role") == "EXAMPLE_TOKEN"}),
        "candidate_count": len(candidates),
    }
    return candidates, {"anchors": strong, "identity_shaped_tokens": shaped, "diagnostics": diagnostics}


def rank_owner_candidates(candidates: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [dict(row) for row in candidates]
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[(str(row.get("page_id") or ""), str(row.get("primary_identity") or ""))].append(row)
    ranked: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for group_key, group in sorted(by_group.items()):
        group.sort(key=deterministic_candidate_key)
        accepted: list[dict[str, Any]] = []
        for rank, row in enumerate(group, start=1):
            row["rank"] = rank
            row["selection_status"] = "RANKED"
            row["alternate_candidate_ids"] = []
            row["complementary_owner"] = False
            multi_surface_owner = False
            if row.get("foreign_identity_values"):
                anchors = [item for item in row.get("anchor_evidence") or [] if isinstance(item, Mapping)]
                anchor_block_ids = {str(item.get("block_id") or "") for item in anchors if item.get("block_id")}
                anchor_kinds = {str(item.get("block_kind") or "").upper() for item in anchors}
                # A single heading can intentionally name several peer
                # operators (for example ``ALL, ANY and SOME``).  It is one
                # bounded Owner surface, not cross-feature contamination.
                # Preserve the multi-identity Owner and let the identity
                # layer bind each surface locally.  Broad/page owners and
                # multi-block mixtures remain review-only.
                multi_surface_owner = bool(
                    str(row.get("owner_kind") or "") == "HEADING_SECTION_OWNER"
                    and len(anchor_block_ids) == 1
                    and anchor_kinds <= {"HEADING", "TEXT"}
                    and not row.get("root_is_example")
                )
            if row.get("foreign_identity_values") and row.get("owner_kind") not in {
                "TABLE_ROW_OWNER", "LIST_ITEM_OWNER", "DEFINITION_LIST_OWNER",
            } and not multi_surface_owner:
                row["selection_status"] = "REVIEW_REQUIRED"
                row["status"] = "AMBIGUOUS"
                row["review_reason"] = "MULTIPLE_STRONG_PEER_ANCHORS"
                ranked.append(row)
                continue
            if multi_surface_owner:
                row["multi_identity_owner"] = True
                row.setdefault("reason_codes", []).append("BOUNDED_MULTI_IDENTITY_SURFACE")
            members = set(str(x) for x in row.get("member_block_ids") or [])
            equivalent = None
            for current in accepted:
                current_members = set(str(x) for x in current.get("member_block_ids") or [])
                union = members | current_members
                jaccard = len(members & current_members) / len(union) if union else 1.0
                if jaccard >= 0.80:
                    equivalent = current
                    break
            if equivalent is not None:
                row["selection_status"] = "ALTERNATE"
                row["status"] = "RANKED"
                row["alternate_of"] = equivalent.get("candidate_id")
                equivalent.setdefault("alternate_candidate_ids", []).append(row.get("candidate_id"))
            else:
                row["selection_status"] = "SELECTED"
                row["status"] = "RANKED"
                row["complementary_owner"] = bool(accepted)
                accepted.append(row)
                selected.append(row)
            ranked.append(row)
    for row in ranked:
        row["alternate_candidate_ids"] = sorted(str(x) for x in row.get("alternate_candidate_ids") or [] if x)
    return sorted(ranked, key=lambda row: (str(row.get("page_id")), str(row.get("primary_identity")), int(row.get("rank", 0)), str(row.get("candidate_id")))), sorted(selected, key=lambda row: (str(row.get("page_id")), str(row.get("primary_identity")), deterministic_candidate_key(row)))


def candidates_to_owner_hypotheses(documents: Iterable[Mapping[str, Any]], selected: Iterable[Mapping[str, Any]], profile: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    pages = {str(row.get("page_id")): row for row in documents}
    result: list[dict[str, Any]] = []
    for candidate in selected:
        page = pages.get(str(candidate.get("page_id")))
        if not page:
            continue
        confidence = float(candidate.get("confidence") or 0.0)
        tier = "STRONG" if confidence >= 0.78 else "PROBABLE" if confidence >= 0.55 else "WEAK"
        owner = make_owner_hypothesis(
            dbms=str(page.get("dbms") or "mysql"), page_id=str(page.get("page_id") or ""),
            source_url=str(page.get("url") or ""), page_hash=str(page.get("page_hash") or ""),
            html_path=str(page.get("html_path") or ""), start_block_id=str(candidate.get("start_block_id") or ""),
            end_block_id=str(candidate.get("end_block_id") or ""), root_block_id=str(candidate.get("root_block_id") or ""),
            member_block_ids=list(candidate.get("member_block_ids") or []),
            owner_kind=_OWNER_KIND_TO_LEGACY.get(str(candidate.get("owner_kind")), "ContainerOwner"),
            identity_signals=list(candidate.get("reason_codes") or []), confidence=confidence, tier=tier,
            template=str(candidate.get("template") or "unknown"), status=tier,
            parent_ids=[str(candidate.get("candidate_id") or ""), str(page.get("document_id") or "")],
            reason_codes=["DOM_FIRST_SHADOW", str(candidate.get("origin") or "")],
        )
        hints = profile_hints_for_page(page, profile or {}) if profile else []
        owner["profile_hint"] = dict(hints[0]) if hints else {}
        owner.update({
            "shadow_schema_version": SCHEMA_VERSION,
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "candidate_kind": str(candidate.get("owner_kind") or ""),
            "selection_status": str(candidate.get("selection_status") or "SELECTED"),
            "primary_identity": str(candidate.get("primary_identity") or ""),
            "identity_values": list(candidate.get("identity_values") or []),
            "candidate_scores": dict(candidate.get("ranking_features") or {}),
            "penalties": {
                name: (candidate.get("ranking_features") or {}).get(name, 0.0)
                for name in ("example_penalty", "contamination_penalty", "weak_anchor_penalty")
            },
            "anchor_evidence": list(candidate.get("anchor_evidence") or []),
            "profile_hints_used": list(candidate.get("profile_hints_used") or []),
            "profile_hint_overridden_by_structure": bool(candidate.get("profile_hint_overridden_by_structure")),
            "complementary_owner": bool(candidate.get("complementary_owner")),
            "alternate_candidate_ids": list(candidate.get("alternate_candidate_ids") or []),
            "foreign_identity_values": list(candidate.get("foreign_identity_values") or []),
            "multi_identity_owner": bool(candidate.get("multi_identity_owner")),
            "root_is_example": bool(candidate.get("root_is_example")),
        })
        result.append(owner)
    return sorted(result, key=lambda row: (str(row.get("provenance", {}).get("source_url") or ""), str(row.get("owner_id") or "")))


def rebuild_structural_documents(raw_documents: Iterable[Mapping[str, Any]], profile: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for raw in raw_documents:
        html_path = Path(str(raw.get("html_path") or ""))
        if not html_path.is_file():
            failures.append({"url": raw.get("url"), "html_path": str(html_path), "reason": "FROZEN_HTML_MISSING"})
            continue
        spec = {
            "url": str(raw.get("url") or ""), "html_path": str(html_path),
            "feature_context": list(raw.get("feature_context") or []),
            "crawl_depth": raw.get("crawl_depth", 0), "source": "mysql_frozen_document_ir",
        }
        try:
            document = build_structural_document(spec, dbms=str(raw.get("dbms") or "mysql"), parser_profile=PARSER_PROFILE, doc_version=str(raw.get("doc_version") or "current"))
            for key in ("discovery_provenance", "discovery_classification", "feature_context"):
                if key in raw:
                    document[key] = raw[key]
            documents.append(document)
        except Exception as exc:  # local frozen parse diagnostics are retained
            failures.append({"url": raw.get("url"), "html_path": str(html_path), "reason": "DOCUMENTIR_REBUILD_FAILURE", "detail": f"{type(exc).__name__}: {exc}"})
    return sorted(documents, key=lambda row: str(row.get("url") or "")), failures


def _content_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("identity") or row.get("normalized_identity") or "").upper(), str(row.get("feature_type") or "UNCERTAIN").upper())


def _owner_block_map(owners: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], list[str]] = defaultdict(list)
    for owner in owners:
        key = (str(owner.get("primary_identity") or "").upper(), str(owner.get("feature_type") or owner.get("type_hint") or "UNCERTAIN").upper())
        for block_id in owner.get("member_block_ids") or []:
            result[(key[0], str(block_id))].append(str(owner.get("owner_id") or ""))
    return result


def compare_content_units(content_units: list[dict[str, Any]], old_owners: list[dict[str, Any]], new_owners: list[dict[str, Any]]) -> dict[str, Any]:
    old_by_id = {str(row.get("owner_id")): row for row in old_owners}
    new_by_id = {str(row.get("owner_id")): row for row in new_owners}
    new_block_identity: dict[tuple[str, str], list[str]] = defaultdict(list)
    new_block_all: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for owner in new_owners:
        identities = {
            str(value).upper()
            for value in (owner.get("identity_values") or [owner.get("primary_identity")])
            if str(value or "").strip()
        }
        if not identities:
            identities = {str(owner.get("primary_identity") or "").upper()}
        for block_id in owner.get("member_block_ids") or []:
            owner_id = str(owner.get("owner_id") or "")
            for identity in identities:
                new_block_identity[(identity, str(block_id))].append(owner_id)
                new_block_all[str(block_id)].append((owner_id, identity))
    old_block_identity: dict[tuple[str, str], list[str]] = defaultdict(list)
    for unit in content_units:
        key = _content_key(unit)
        for owner_id in unit.get("feature_owner_ids") or []:
            owner = old_by_id.get(str(owner_id))
            if owner:
                old_block_identity[(key[0], str(unit.get("block_id") or ""))].append(str(owner_id))
    rows: list[dict[str, Any]] = []
    for unit in content_units:
        key = _content_key(unit)
        block_id = str(unit.get("block_id") or "")
        old_ids = sorted({str(x) for x in unit.get("feature_owner_ids") or [] if str(x) in old_by_id})
        new_ids = sorted(set(new_block_identity.get((key[0], block_id), [])))
        foreign_new = sorted({
            owner_id for owner_id, identity in new_block_all.get(block_id, [])
            if identity and identity != key[0] and owner_id not in new_ids
        })
        old_retained = bool(old_ids)
        new_retained = bool(new_ids)
        rows.append({
            "unit_id": str(unit.get("unit_id") or ""), "identity": key[0], "feature_type": key[1],
            "block_id": block_id, "block_kind": unit.get("block_kind"), "expected_role": unit.get("expected_role"),
            "official_url": unit.get("official_url"), "old_owner_ids": old_ids, "new_owner_ids": new_ids,
            "old_retained": old_retained, "new_retained": new_retained,
            "new_foreign_owner_ids": foreign_new, "new_subject_mismatch": bool(foreign_new),
            "recovered": bool(new_retained and not old_retained), "lost": bool(old_retained and not new_retained),
            "old_first_loss": unit.get("first_loss"),
            "new_status": "NO_LOSS" if new_retained else "OWNER_BOUNDARY_LOSS",
        })
    by_feature: dict[str, dict[str, Any]] = {}
    for row in rows:
        feature = row["identity"]
        stats = by_feature.setdefault(feature, {"identity": feature, "feature_type": row["feature_type"], "units": 0, "old_retained": 0, "new_retained": 0, "recovered": 0, "lost": 0, "unit_ids": []})
        stats["units"] += 1
        stats["old_retained"] += int(row["old_retained"])
        stats["new_retained"] += int(row["new_retained"])
        stats["recovered"] += int(row["recovered"])
        stats["lost"] += int(row["lost"])
        stats["unit_ids"].append(row["unit_id"])
    return {
        "schema_version": "mysql-owner-shadow-content-comparison/v1",
        "content_units": len(rows),
        "old_retained_units": sum(int(row["old_retained"]) for row in rows),
        "new_retained_units": sum(int(row["new_retained"]) for row in rows),
        "recovered_units": sum(int(row["recovered"]) for row in rows),
        "lost_units": sum(int(row["lost"]) for row in rows),
        "old_retention_ratio": sum(int(row["old_retained"]) for row in rows) / len(rows) if rows else 0.0,
        "new_retention_ratio": sum(int(row["new_retained"]) for row in rows) / len(rows) if rows else 0.0,
        "rows": rows,
        "by_feature": dict(sorted(by_feature.items())),
    }


def _stage_comparison(
    content_rows: list[dict[str, Any]],
    sample_features: list[dict[str, Any]],
    old_owners: Iterable[Mapping[str, Any]] = (),
    new_owners: Iterable[Mapping[str, Any]] = (),
    example_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    units_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in content_rows:
        units_by_feature[str(row.get("identity") or "")].append(row)
    old_by_id = {str(row.get("owner_id")): row for row in old_owners}
    new_by_id = {str(row.get("owner_id")): row for row in new_owners}
    example_report = example_report or {}
    old_example_ids = {str(row.get("owner_id")) for row in example_report.get("old_example_roots") or []}
    new_example_ids = {str(row.get("owner_id")) for row in example_report.get("new_example_roots") or []}
    feature_rows: list[dict[str, Any]] = []
    for feature in sample_features:
        identity = str(feature.get("normalized_identity") or feature.get("name") or "").upper()
        rows = units_by_feature.get(identity, [])
        old_loss = Counter(str(row.get("old_first_loss") or "UNRESOLVED") for row in rows)
        new_missing = [row for row in rows if not row.get("new_retained")]
        if not rows:
            first = "NO_CONTENT_UNITS"
        elif not new_missing:
            first = "NO_LOSS"
        elif any(row.get("new_subject_mismatch") for row in rows):
            first = "OWNER_SUBJECT_MISMATCH"
        else:
            first = "OWNER_BOUNDARY_LOSS"
        old_owner_ids = sorted({owner_id for row in rows for owner_id in row.get("old_owner_ids") or []})
        new_owner_ids = sorted({owner_id for row in rows for owner_id in row.get("new_owner_ids") or []})
        old_blocks = sorted({
            str(block_id)
            for owner_id in old_owner_ids
            for block_id in old_by_id.get(owner_id, {}).get("member_block_ids") or []
        })
        new_blocks = sorted({
            str(block_id)
            for owner_id in new_owner_ids
            for block_id in new_by_id.get(owner_id, {}).get("member_block_ids") or []
        })
        recovered_blocks = sorted({str(row.get("block_id")) for row in rows if row.get("recovered")})
        lost_blocks = sorted({str(row.get("block_id")) for row in rows if row.get("lost")})
        foreign_blocks_new = sorted({str(row.get("block_id")) for row in rows if row.get("new_subject_mismatch")})
        feature_rows.append({
            "identity": identity, "feature_type": str(feature.get("feature_type") or "UNCERTAIN").upper(),
            "first_loss_old": first if not rows else old_loss.most_common(1)[0][0],
            "first_loss_new": first, "content_units": len(rows),
            "old_retained": sum(bool(row.get("old_retained")) for row in rows),
            "new_retained": sum(bool(row.get("new_retained")) for row in rows),
            "recovered": sum(bool(row.get("recovered")) for row in rows),
            "lost": sum(bool(row.get("lost")) for row in rows),
            "old_owner_ids": old_owner_ids,
            "new_owner_ids": new_owner_ids,
            "old_blocks": old_blocks,
            "new_blocks": new_blocks,
            "recovered_blocks": recovered_blocks,
            "lost_blocks": lost_blocks,
            "old_content_recall": sum(bool(row.get("old_retained")) for row in rows) / len(rows) if rows else 0.0,
            "new_content_recall": sum(bool(row.get("new_retained")) for row in rows) / len(rows) if rows else 0.0,
            "foreign_blocks_old": [],
            "foreign_blocks_new": foreign_blocks_new,
            "example_root_old": bool(set(old_owner_ids) & old_example_ids),
            "example_root_new": bool(set(new_owner_ids) & new_example_ids),
        })
    return {
        "schema_version": "mysql-owner-shadow-stage-comparison/v1",
        "feature_count": len(feature_rows),
        "feature_first_loss_old": dict(Counter(row["first_loss_old"] for row in feature_rows)),
        "feature_first_loss_new": dict(Counter(row["first_loss_new"] for row in feature_rows)),
        "features": feature_rows,
    }


def build_contamination_report(content_units: list[dict[str, Any]], old_owners: list[dict[str, Any]], new_owners: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    defining_by_block: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for unit in content_units:
        if str(unit.get("expected_role") or "") in {"FORMAL_SYNTAX", "CATALOG_LABEL", "DEFINITION_IDENTITY"}:
            defining_by_block[str(unit.get("block_id") or "")].add(_content_key(unit))

    def inspect(owners: list[dict[str, Any]], shadow: bool) -> list[dict[str, Any]]:
        rows = []
        for owner in owners:
            primary = str(owner.get("primary_identity") or "").upper() if shadow else ""
            keys = set().union(*(defining_by_block.get(str(block_id), set()) for block_id in owner.get("member_block_ids") or []))
            if shadow and primary:
                foreign = sorted(key for key in keys if key[0] != primary)
            else:
                foreign = []
            if len(keys) > 1 or foreign:
                rows.append({
                    "owner_id": owner.get("owner_id"), "page_id": owner.get("page_id"),
                    "primary_identity": primary, "defining_keys": sorted([list(key) for key in keys]),
                    "foreign_keys": [list(key) for key in foreign], "member_block_ids": list(owner.get("member_block_ids") or []),
                })
        return rows

    raw_selected_rows = inspect(new_owners, True)
    new_owner_by_id = {str(row.get("owner_id")): row for row in new_owners}
    # A catalog row can legitimately carry two explicitly documented labels
    # (synonym/variant or a compound operator surface).  Keep those rows for
    # human review, but count an actual cross-feature Owner only when the
    # selected candidate itself reported a second strong anchor identity.
    selected_rows = [
        row for row in raw_selected_rows
        if list(new_owner_by_id.get(str(row.get("owner_id")), {}).get("foreign_identity_values") or [])
    ]
    old_rows = inspect(old_owners, False)
    candidate_rows = [{
        "candidate_id": row.get("candidate_id"), "page_id": row.get("page_id"),
        "primary_identity": row.get("primary_identity"), "owner_kind": row.get("owner_kind"),
        "selection_status": row.get("selection_status"), "foreign_identity_values": list(row.get("foreign_identity_values") or []),
        "contamination_flags": list(row.get("contamination_flags") or []),
    } for row in candidates if row.get("foreign_identity_values") or row.get("contamination_flags")]
    return {
        "schema_version": "mysql-owner-shadow-contamination/v1",
        "old_owner_contamination_count": len(old_rows), "new_owner_contamination_count": len(selected_rows),
        "new_content_multi_identity_review_count": len(raw_selected_rows),
        "new_selected_cross_feature_owner_count": len(selected_rows),
        "candidate_multi_anchor_count": len(candidate_rows),
        "old": old_rows, "new": selected_rows, "new_content_multi_identity": raw_selected_rows,
        "candidates": candidate_rows,
    }


def build_example_anchor_report(documents: list[dict[str, Any]], old_owners: list[dict[str, Any]], new_owners: list[dict[str, Any]]) -> dict[str, Any]:
    blocks_by_page = {str(page.get("page_id")): {str(block.get("block_id")): block for block in page.get("blocks") or []} for page in documents}

    def roots(owners: list[dict[str, Any]], shadow: bool) -> list[dict[str, Any]]:
        rows = []
        for owner in owners:
            page_blocks = blocks_by_page.get(str(owner.get("page_id")), {})
            root_id = str(owner.get("root_block_id") or owner.get("start_block_id") or "")
            root = page_blocks.get(root_id, {})
            if _is_example_block(root):
                rows.append({
                    "owner_id": owner.get("owner_id"), "page_id": owner.get("page_id"), "root_block_id": root_id,
                    "root_block_kind": root.get("block_kind"), "root_text": root.get("text"),
                    "primary_identity": owner.get("primary_identity") if shadow else None,
                    "candidate_id": owner.get("candidate_id") if shadow else None,
                })
        return rows

    old_rows = roots(old_owners, False)
    new_rows = roots(new_owners, True)
    return {
        "schema_version": "mysql-owner-shadow-example-anchor/v1",
        "old_example_root_count": len(old_rows), "new_example_root_count": len(new_rows),
        "old_example_roots": old_rows, "new_example_roots": new_rows,
        "new_example_root_rate": len(new_rows) / len(new_owners) if new_owners else 0.0,
    }


def build_structural_preference_report(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        groups[(str(row.get("page_id") or ""), str(row.get("primary_identity") or ""))].append(row)
    cases = []
    for key, rows in sorted(groups.items()):
        structural = [row for row in rows if row.get("owner_kind") in _STRUCTURAL_OWNER_KINDS]
        fallback = [row for row in rows if row.get("owner_kind") == "SIGNATURE_SPAN_OWNER"]
        if not structural or not fallback:
            continue
        selected_structural = any(row.get("selection_status") == "SELECTED" for row in structural)
        selected_fallback = any(row.get("selection_status") == "SELECTED" for row in fallback)
        cases.append({
            "page_id": key[0], "identity": key[1],
            "structural_candidate_ids": [row.get("candidate_id") for row in structural],
            "fallback_candidate_ids": [row.get("candidate_id") for row in fallback],
            "structural_selected": selected_structural, "fallback_selected": selected_fallback,
            "winner_kind": "STRUCTURAL" if selected_structural else "FALLBACK_ONLY",
        })
    return {
        "schema_version": "mysql-owner-shadow-structural-preference/v1",
        "cases": len(cases), "structural_selected": sum(bool(row["structural_selected"]) for row in cases),
        "fallback_selected": sum(bool(row["fallback_selected"]) for row in cases),
        "structural_preference_rate": sum(bool(row["structural_selected"]) for row in cases) / len(cases) if cases else 0.0,
        "rows": cases,
    }


def _snippet(text: Any, limit: int = 240) -> str:
    value = _compact(text)
    return value if len(value) <= limit else value[:limit - 3] + "..."


def build_review_pack(
    path: Path,
    *,
    documents: list[dict[str, Any]],
    old_owners: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    selected_owners: list[dict[str, Any]],
    content_comparison: dict[str, Any],
    sample_features: list[dict[str, Any]],
    stage_comparison: Mapping[str, Any] | None = None,
    contamination: Mapping[str, Any] | None = None,
    example_report: Mapping[str, Any] | None = None,
    identity_comparison: Mapping[str, Any] | None = None,
) -> None:
    docs = {str(row.get("page_id")): row for row in documents}
    old_by_id = {str(row.get("owner_id")): row for row in old_owners}
    new_by_id = {str(row.get("owner_id")): row for row in selected_owners}
    candidate_by_id = {str(row.get("candidate_id")): row for row in candidates}
    rows_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in content_comparison.get("rows") or []:
        rows_by_identity[str(row.get("identity") or "")].append(row)
    stage_comparison = stage_comparison or {}
    contamination = contamination or {}
    example_report = example_report or {}
    identity_comparison = identity_comparison or {}

    def page_for(value: Mapping[str, Any]) -> Mapping[str, Any]:
        return docs.get(str(value.get("page_id")), {})

    def block_context(page: Mapping[str, Any], member_ids: Iterable[str], radius: int = 1) -> list[str]:
        blocks = sorted(page.get("blocks") or [], key=lambda row: (int(row.get("order", 0)), str(row.get("block_id"))))
        positions = {str(row.get("block_id")): index for index, row in enumerate(blocks)}
        selected_positions = [positions[str(block_id)] for block_id in member_ids if str(block_id) in positions]
        if not selected_positions:
            return []
        lo = max(0, min(selected_positions) - radius)
        hi = min(len(blocks), max(selected_positions) + radius + 1)
        return [
            f"`{row.get('block_id')}` `{row.get('block_kind')}` {_snippet(row.get('text'))}"
            for row in blocks[lo:hi]
        ]

    def candidate_detail(candidate: Mapping[str, Any], include_context: bool = True) -> list[str]:
        page = page_for(candidate)
        lines = [
            f"- candidate `{candidate.get('candidate_id')}` `{candidate.get('owner_kind')}` "
            f"identity=`{candidate.get('primary_identity')}` rank={candidate.get('rank')} "
            f"status={candidate.get('selection_status')} confidence={candidate.get('confidence')}",
            f"  - scores: `{json.dumps(candidate.get('ranking_features') or {}, sort_keys=True)}`",
            f"  - members: `{json.dumps(candidate.get('member_block_ids') or [])}`",
            f"  - reasons: `{json.dumps(candidate.get('reason_codes') or [], sort_keys=True)}`",
        ]
        if include_context:
            lines.append("  - neighbor context:")
            lines.extend(f"    - {text}" for text in block_context(page, candidate.get("member_block_ids") or []))
        return lines

    def owner_detail(owner: Mapping[str, Any], label: str = "owner") -> list[str]:
        page = page_for(owner)
        lines = [
            f"- {label} `{owner.get('owner_id')}` `{owner.get('candidate_kind') or owner.get('owner_kind')}` "
            f"identity=`{owner.get('primary_identity')}` page=`{page.get('url')}`",
            f"  - blocks: `{json.dumps(owner.get('member_block_ids') or [])}`",
        ]
        lines.append("  - neighbor context:")
        lines.extend(f"    - {text}" for text in block_context(page, owner.get("member_block_ids") or []))
        return lines
    lines = [
        "# MySQL DOM-first Owner Shadow Review Pack", "",
        "This pack is generated from the 250-page frozen corpus. It is shadow-only; production Owner, Semantic, Canonical and Frontend artifacts were not modified.", "",
        "## Runtime boundary", "",
        "- Local frozen HTML only; no network fetch.", "- No LLM call.", "- Candidate generation and ranking are deterministic.", "",
        "## Candidate selection overview", "",
        f"- Candidate rows: **{len(candidates)}**", f"- Selected shadow owners: **{len(selected_owners)}**", f"- Content units: **{content_comparison.get('content_units', 0)}**", "",
        "## ABS golden case", "",
    ]
    abs_rows = rows_by_identity.get("ABS", [])
    if not abs_rows:
        lines.append("- ABS did not resolve in the content audit ledger.")
    else:
        for row in abs_rows:
            lines.append(f"- `{row.get('block_id')}` `{row.get('expected_role')}`: old={row.get('old_retained')} new={row.get('new_retained')} recovered={row.get('recovered')} — {_snippet(row.get('official_url'))}")
    abs_new = [owner for owner in selected_owners if str(owner.get("primary_identity") or "").upper() == "ABS"]
    lines.append("")
    lines.append("### ABS selected owners")
    for owner in abs_new[:12]:
        blocks = docs.get(str(owner.get("page_id")), {}).get("blocks") or []
        by_id = {str(row.get("block_id")): row for row in blocks}
        lines.append(f"- `{owner.get('owner_id')}` `{owner.get('candidate_kind')}` page `{docs.get(str(owner.get('page_id')), {}).get('url')}`")
        for block_id in owner.get("member_block_ids") or []:
            block = by_id.get(str(block_id))
            if block:
                lines.append(f"  - `{block_id}` `{block.get('block_kind')}` {_snippet(block.get('text'))}")
    lines.extend(["", "## Top recovered content features", ""])
    recovered = sorted((value for value in (content_comparison.get("by_feature") or {}).values() if int(value.get("recovered", 0)) > 0), key=lambda row: (-int(row.get("recovered", 0)), str(row.get("identity"))))
    for row in recovered[:5]:
        lines.append(f"- **{row.get('identity')}** ({row.get('feature_type')}): recovered `{row.get('recovered')}` of `{row.get('units')}` content units; old `{row.get('old_retained')}`, new `{row.get('new_retained')}`.")
    if not recovered:
        lines.append("- No recovered content units.")
    lines.extend(["", "## Five ambiguous candidate groups", ""])
    ambiguous = [row for row in candidates if row.get("selection_status") in {"ALTERNATE", "REVIEW_REQUIRED"}]
    for row in ambiguous[:5]:
        lines.extend(candidate_detail(row))
        related = [candidate for candidate in candidates
                   if str(candidate.get("page_id")) == str(row.get("page_id"))
                   and str(candidate.get("primary_identity")) == str(row.get("primary_identity"))]
        for candidate in related[:5]:
            if candidate.get("candidate_id") != row.get("candidate_id"):
                lines.extend(candidate_detail(candidate, include_context=False))
    if not ambiguous:
        lines.append("- No alternate/review-required candidate rows.")
    lines.extend(["", "## All subject mismatch cases", ""])
    subject_rows = [row for row in content_comparison.get("rows") or [] if row.get("new_subject_mismatch")]
    if not subject_rows:
        lines.append("- None in the content audit sample.")
    else:
        for row in subject_rows:
            lines.append(
                f"### `{row.get('identity')}` `{row.get('block_id')}` [{row.get('expected_role')}]"
            )
            lines.append(
                f"- old owners: `{json.dumps(row.get('old_owner_ids') or [])}`; "
                f"new owners: `{json.dumps(row.get('new_owner_ids') or [])}`; "
                f"foreign new owners: `{json.dumps(row.get('new_foreign_owner_ids') or [])}`"
            )
            for owner_id in row.get("old_owner_ids") or []:
                if owner_id in old_by_id:
                    lines.extend(owner_detail(old_by_id[owner_id], "old owner"))
            for owner_id in row.get("new_owner_ids") or []:
                if owner_id in new_by_id:
                    lines.extend(owner_detail(new_by_id[owner_id], "new owner"))
            for owner_id in row.get("new_foreign_owner_ids") or []:
                if owner_id in new_by_id and owner_id not in row.get("new_owner_ids"):
                    lines.extend(owner_detail(new_by_id[owner_id], "foreign new owner"))
    lines.extend(["", "## All contamination candidate cases", ""])
    contamination_candidates = list(contamination.get("candidates") or [])
    if not contamination_candidates:
        lines.append("- None.")
    else:
        for case in contamination_candidates:
            lines.append(
                f"### `{case.get('candidate_id')}` `{case.get('primary_identity')}` "
                f"[{case.get('owner_kind')}] status={case.get('selection_status')}"
            )
            lines.append(f"- foreign identity values: `{json.dumps(case.get('foreign_identity_values') or [])}`")
            lines.append(f"- flags: `{json.dumps(case.get('contamination_flags') or [])}`")
            candidate = candidate_by_id.get(str(case.get("candidate_id")))
            if candidate:
                lines.extend(candidate_detail(candidate))
    lines.extend(["", "## All example-root cases", ""])
    old_example_roots = list(example_report.get("old_example_roots") or [])
    new_example_roots = list(example_report.get("new_example_roots") or [])
    lines.append(f"- old example-root count: **{len(old_example_roots)}**")
    lines.append(f"- new example-root count: **{len(new_example_roots)}**")
    for case in old_example_roots:
        lines.append(
            f"- old `{case.get('owner_id')}` page=`{case.get('page_id')}` root=`{case.get('root_block_id')}` "
            f"`{case.get('root_block_kind')}` {_snippet(case.get('root_text'))}"
        )
    for case in new_example_roots:
        lines.append(
            f"- new `{case.get('owner_id')}` candidate=`{case.get('candidate_id')}` page=`{case.get('page_id')}` "
            f"root=`{case.get('root_block_id')}` `{case.get('root_block_kind')}` {_snippet(case.get('root_text'))}"
        )
    lines.extend(["", "## Identity dry replay changes", ""])
    lost_confirmed = [row for row in identity_comparison.get("lost") or [] if row.get("status") == "CONFIRMED_FEATURE"]
    added_confirmed = [row for row in identity_comparison.get("added") or [] if row.get("status") == "CONFIRMED_FEATURE"]
    status_changes = list(identity_comparison.get("status_changed") or [])
    lines.append(
        f"- old confirmed identities lost: **{len(lost_confirmed)}**; "
        f"new confirmed identities added: **{len(added_confirmed)}**; "
        f"status changes: **{len(status_changes)}**"
    )
    if not lost_confirmed:
        lines.append("- No old CONFIRMED_FEATURE identity disappeared.")
    else:
        lines.append("### Lost old confirmed identities")
        for row in lost_confirmed:
            lines.append(
                f"- `{row.get('identity')}` [{row.get('feature_type')}] reason=`NO_MATCHING_NEW_IDENTITY_GROUP`; "
                f"old defining variants=`{json.dumps(row.get('defining_variants') or [])}`; "
                f"sources=`{json.dumps(row.get('source_urls') or [])}`"
            )
    if added_confirmed:
        lines.append("### Added confirmed identities")
        for row in added_confirmed:
            lines.append(
                f"- `{row.get('identity')}` [{row.get('feature_type')}] "
                f"defining variants=`{json.dumps(row.get('defining_variants') or [])}`; "
                f"sources=`{json.dumps(row.get('source_urls') or [])}`"
            )
    if status_changes:
        lines.append("### Identity status changes")
        for row in status_changes:
            lines.append(
                f"- `{row.get('identity')}` [{row.get('feature_type')}] "
                f"`{row.get('old_status')}` → `{row.get('new_status')}`"
            )
    lines.extend(["", "## Review sample traces", ""])
    for feature in sample_features:
        identity = str(feature.get("normalized_identity") or feature.get("name") or "").upper()
        rows = rows_by_identity.get(identity, [])
        lines.append(f"### {identity} [{feature.get('feature_type') or 'UNCERTAIN'}]")
        if not rows:
            lines.append("- No content audit unit.")
            continue
        for row in rows[:8]:
            lines.append(f"- `{row.get('block_id')}` `{row.get('expected_role')}` old={row.get('old_retained')} new={row.get('new_retained')} old_loss={row.get('old_first_loss')}")
        matching = [owner for owner in selected_owners if str(owner.get("primary_identity") or "").upper() == identity]
        for owner in matching[:3]:
            lines.append(f"- selected owner `{owner.get('owner_id')}` `{owner.get('candidate_kind')}` blocks={len(owner.get('member_block_ids') or [])} scores={json.dumps(owner.get('candidate_scores'), sort_keys=True)}")
        lines.append("")
    lines.extend(["## Boundary interpretation", "", "- Structural repeated-unit candidates are preferred over signature fallback by the deterministic key.", "- Example code tokens are retained as non-defining evidence and are not promoted to peer anchors.", "- Any remaining loss is a review signal for the next phase, not an automatic production change.", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_identity_comparison(
    old_units: list[dict[str, Any]],
    new_units: list[dict[str, Any]],
    old_quality: Mapping[str, Any] | None = None,
    new_quality: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare the dry-replay identity groups without semantic execution."""
    def key(row: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str(row.get("canonical_identity") or row.get("normalized_identity") or "").upper(),
            str(row.get("feature_type") or row.get("type_hint") or "UNCERTAIN").upper(),
        )

    old_by_key = {key(row): dict(row) for row in old_units if key(row)[0]}
    new_by_key = {key(row): dict(row) for row in new_units if key(row)[0]}
    old_status = Counter(str(row.get("group_status") or "UNKNOWN") for row in old_units)
    new_status = Counter(str(row.get("group_status") or "UNKNOWN") for row in new_units)
    old_types = Counter(key(row)[1] for row in old_units if key(row)[0])
    new_types = Counter(key(row)[1] for row in new_units if key(row)[0])
    added = sorted(set(new_by_key) - set(old_by_key))
    lost = sorted(set(old_by_key) - set(new_by_key))
    status_changed = sorted(
        {
            identity: {
                "feature_type": new_by_key[(identity, feature_type)].get("feature_type"),
                "old_status": old_by_key[(identity, feature_type)].get("group_status"),
                "new_status": new_by_key[(identity, feature_type)].get("group_status"),
            }
            for identity, feature_type in set(old_by_key) & set(new_by_key)
            if old_by_key[(identity, feature_type)].get("group_status")
            != new_by_key[(identity, feature_type)].get("group_status")
        }.items(),
        key=lambda item: item[0],
    )

    def details(keys: Iterable[tuple[str, str]], source: Mapping[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "identity": identity,
                "feature_type": feature_type,
                "status": source[(identity, feature_type)].get("group_status"),
                "unit_id": source[(identity, feature_type)].get("identity_unit_id"),
                "defining_variants": list(source[(identity, feature_type)].get("defining_variants") or []),
                "source_urls": list(source[(identity, feature_type)].get("source_urls") or []),
            }
            for identity, feature_type in sorted(keys)
        ]

    old_quality = old_quality or {}
    new_quality = new_quality or {}
    old_by_status = Counter(str(row.get("group_status") or "UNKNOWN") for row in old_units)
    new_by_status = Counter(str(row.get("group_status") or "UNKNOWN") for row in new_units)
    return {
        "schema_version": "mysql-owner-shadow-identity-comparison/v1",
        "old_units": len(old_units),
        "new_units": len(new_units),
        "old_by_status": dict(sorted((old_quality.get("groups_by_status") or old_by_status).items())),
        "new_by_status": dict(sorted((new_quality.get("groups_by_status") or new_by_status).items())),
        "old_by_type": dict(sorted((old_quality.get("groups_by_type") or old_types).items())),
        "new_by_type": dict(sorted((new_quality.get("groups_by_type") or new_types).items())),
        "added_count": len(added),
        "lost_count": len(lost),
        "status_changed_count": len(status_changed),
        "added": details(added, new_by_key),
        "lost": details(lost, old_by_key),
        "status_changed": [{"identity": identity, **value} for identity, value in status_changed],
        "added_by_status": dict(sorted(Counter(new_by_key[item].get("group_status") or "UNKNOWN" for item in added).items())),
        "lost_by_status": dict(sorted(Counter(old_by_key[item].get("group_status") or "UNKNOWN" for item in lost).items())),
        "old_groups_by_status_source": "mysql_identity_quality_v3/evaluation/identity_quality_summary.json",
        "new_groups_by_status_source": "shadow identity/group reconciliation",
    }


def run_shadow(
    *,
    repo_root: str | Path,
    output_root: str | Path,
    documents_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    old_owners_path: str | Path | None = None,
    content_units_path: str | Path | None = None,
    sample_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    output = Path(output_root).resolve()
    identity_root = repo / "output" / "mysql_identity_quality_v3"
    documents_path = Path(documents_path or identity_root / "document_ir" / "documents.jsonl")
    profile_path = Path(profile_path or identity_root / "profile" / "generated_profile.json")
    old_owners_path = Path(old_owners_path or identity_root / "owners" / "owners.jsonl")
    old_identity_units_path = identity_root / "identity" / "identity_units.jsonl"
    old_identity_quality_path = identity_root / "evaluation" / "identity_quality_summary.json"
    content_units_path = Path(content_units_path or repo / "output" / "mysql_content_audit_v1" / "audit" / "content_units.jsonl")
    sample_manifest_path = Path(sample_manifest_path or repo / "output" / "mysql_content_audit_v1" / "audit" / "audit_sample_manifest.json")
    raw_documents = _read_jsonl(documents_path)
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else {}
    old_owners = _read_jsonl(old_owners_path)
    old_identity_units = _read_jsonl(old_identity_units_path)
    old_identity_quality = json.loads(old_identity_quality_path.read_text(encoding="utf-8")) if old_identity_quality_path.is_file() else {}
    content_units = _read_jsonl(content_units_path)
    sample_manifest = json.loads(sample_manifest_path.read_text(encoding="utf-8")) if sample_manifest_path.is_file() else {"features": []}
    sample_features = list(sample_manifest.get("features") or [])
    structural_documents, rebuild_failures = rebuild_structural_documents(raw_documents, profile)
    all_candidates: list[dict[str, Any]] = []
    document_diagnostics: list[dict[str, Any]] = []
    for document in structural_documents:
        candidates, diagnostics = generate_owner_candidates(document, profile)
        all_candidates.extend(candidates)
        document_diagnostics.append(diagnostics)
    ranked_candidates, selected_candidates = rank_owner_candidates(all_candidates)
    selected_owners = candidates_to_owner_hypotheses(structural_documents, selected_candidates, profile)
    content_comparison = compare_content_units(content_units, old_owners, selected_owners)
    contamination = build_contamination_report(content_units, old_owners, selected_owners, ranked_candidates)
    example_report = build_example_anchor_report(structural_documents, old_owners, selected_owners)
    stage_comparison = _stage_comparison(
        content_comparison.get("rows") or [], sample_features,
        old_owners=old_owners, new_owners=selected_owners, example_report=example_report,
    )
    structural_preference = build_structural_preference_report(ranked_candidates)

    identity_root_out = output / "identity"
    identity_error = None
    identity_result: dict[str, Any] = {}
    try:
        occurrences = build_identity_occurrences(structural_documents, selected_owners, profile=profile)
        candidates = build_candidate_ledger(structural_documents, selected_owners, occurrences=occurrences)
        accepted, rejected = validate_candidate_ledger(candidates, selected_owners, retain_quality_evidence=True)
        units, quality_audit = reconcile_identity_groups(accepted)
        identity_comparison = build_identity_comparison(
            old_identity_units,
            units,
            old_quality=(old_identity_quality.get("groups_by_status") and old_identity_quality) or {},
            new_quality=quality_audit,
        )
        _write_jsonl(identity_root_out / "occurrences.jsonl", occurrences)
        _write_jsonl(identity_root_out / "candidates.jsonl", candidates)
        _write_jsonl(identity_root_out / "validated_candidates.jsonl", accepted)
        _write_jsonl(identity_root_out / "rejected_candidates.jsonl", rejected)
        _write_jsonl(identity_root_out / "groups.jsonl", units)
        _write_json(identity_root_out / "comparison.json", identity_comparison)
        identity_summary = {
            "schema_version": "mysql-owner-shadow-identity-dry-replay/v1",
            "documents": len(structural_documents), "owners": len(selected_owners),
            "occurrences": len(occurrences), "candidates": len(candidates),
            "accepted": len(accepted), "rejected": len(rejected), "units": len(units),
            "groups_by_type": dict(sorted(Counter(str(row.get("feature_type") or "UNCERTAIN") for row in units).items())),
            "quality_audit": quality_audit,
            "comparison": identity_comparison,
            "network_fetch": False, "llm_called": False,
        }
        _write_json(identity_root_out / "summary.json", identity_summary)
        identity_result = identity_summary
    except Exception as exc:  # retain shadow diagnostics; production is untouched
        identity_error = {"type": type(exc).__name__, "message": str(exc)}
        _write_json(identity_root_out / "comparison.json", {"error": identity_error})
        _write_json(identity_root_out / "summary.json", {"error": identity_error, "network_fetch": False, "llm_called": False})

    # The compact per-feature comparison is easier to consume than the full
    # content-unit JSON, while content_retention_comparison.json keeps both.
    _write_jsonl(output / "documentir" / "structural_documents.jsonl", structural_documents)
    _write_jsonl(output / "candidates" / "owner_candidates.jsonl", ranked_candidates)
    _write_jsonl(output / "owners" / "ranked_owner_hypotheses.jsonl", selected_owners)
    _write_jsonl(output / "evaluation" / "old_vs_new_owner.jsonl", stage_comparison.get("features") or [])
    _write_json(output / "evaluation" / "content_retention_comparison.json", content_comparison)
    _write_json(output / "evaluation" / "contamination_report.json", contamination)
    _write_json(output / "evaluation" / "example_anchor_report.json", example_report)
    _write_json(output / "evaluation" / "structural_preference.json", structural_preference)

    old_feature_counts = Counter(str(row.get("owner_kind") or "UNKNOWN") for row in old_owners)
    new_feature_counts = Counter(str(row.get("candidate_kind") or "UNKNOWN") for row in selected_owners)
    anchor_count = sum(int((row.get("diagnostics") or {}).get("strong_anchor_count", 0)) for row in document_diagnostics)
    shaped_count = sum(int((row.get("diagnostics") or {}).get("identity_shaped_token_count", 0)) for row in document_diagnostics)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "input": {
            "frozen_documents": len(raw_documents), "structural_documents": len(structural_documents),
            "frozen_html_failures": rebuild_failures, "frozen_html_failure_count": len(rebuild_failures),
            "old_owners": len(old_owners), "content_units": len(content_units), "audit_features": len(sample_features),
        },
        "documentir": {
            "pages": len(structural_documents), "blocks": sum(len(row.get("blocks") or []) for row in structural_documents),
            "strong_anchors": anchor_count, "identity_shaped_tokens": shaped_count,
            "structural_container_kinds": dict(sorted(Counter(str(block.get("structural_parent_kind") or "UNKNOWN") for page in structural_documents for block in page.get("blocks") or []).items())),
        },
        "candidates": {
            "generated": len(all_candidates), "ranked": len(ranked_candidates), "selected": len(selected_candidates),
            "by_kind": dict(sorted(Counter(str(row.get("owner_kind") or "UNKNOWN") for row in ranked_candidates).items())),
            "selected_by_kind": dict(sorted(Counter(str(row.get("owner_kind") or "UNKNOWN") for row in selected_candidates).items())),
            "status": dict(sorted(Counter(str(row.get("selection_status") or "UNKNOWN") for row in ranked_candidates).items())),
        },
        "owners": {
            "old_count": len(old_owners), "new_selected_count": len(selected_owners),
            "old_by_kind": dict(sorted(old_feature_counts.items())), "new_by_kind": dict(sorted(new_feature_counts.items())),
        },
        "content_retention": {
            "content_units": content_comparison.get("content_units", 0),
            "old_retained": content_comparison.get("old_retained_units", 0), "new_retained": content_comparison.get("new_retained_units", 0),
            "recovered": content_comparison.get("recovered_units", 0), "lost": content_comparison.get("lost_units", 0),
            "old_ratio": content_comparison.get("old_retention_ratio", 0.0), "new_ratio": content_comparison.get("new_retention_ratio", 0.0),
            "old_boundary_loss": sum(row.get("first_loss_old") == "OWNER_BOUNDARY_LOSS" for row in stage_comparison.get("features") or []),
            "new_boundary_loss": sum(row.get("first_loss_new") == "OWNER_BOUNDARY_LOSS" for row in stage_comparison.get("features") or []),
            "old_subject_mismatch": sum(row.get("first_loss_old") == "OWNER_SUBJECT_MISMATCH" for row in stage_comparison.get("features") or []),
            "new_subject_mismatch": sum(row.get("first_loss_new") == "OWNER_SUBJECT_MISMATCH" for row in stage_comparison.get("features") or []),
        },
        "contamination": {
            "old": contamination.get("old_owner_contamination_count", 0),
            "new": contamination.get("new_owner_contamination_count", 0),
            "new_content_multi_identity_review": contamination.get("new_content_multi_identity_review_count", 0),
            "candidate_multi_anchor": contamination.get("candidate_multi_anchor_count", 0),
        },
        "examples": example_report,
        "structural_preference": structural_preference,
        "identity_dry_replay": identity_result,
        "identity_dry_replay_error": identity_error,
        "integrity": {
            "network_fetch": False, "llm_called": False, "full_discovery_expanded": False,
            "production_owner_replaced": False, "canonical_modified": False, "frontend_modified": False,
            "db_specific_rule": False,
        },
    }
    _write_json(output / "evaluation" / "owner_shadow_summary.json", summary)
    build_review_pack(output / "review" / "owner_review_pack.md", documents=structural_documents, old_owners=old_owners,
                      candidates=ranked_candidates, selected_owners=selected_owners, content_comparison=content_comparison,
                      sample_features=sample_features, stage_comparison=stage_comparison,
                      contamination=contamination, example_report=example_report,
                      identity_comparison=(identity_result.get("comparison") or {}))
    run_manifest = {
        "schema_version": "mysql-owner-shadow-run/v1", "pipeline_id": SCHEMA_VERSION,
        "run_id": f"mysql_owner_shadow_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "input_document_count": len(raw_documents), "old_owner_path": str(old_owners_path),
        "profile_path": str(profile_path), "documents_path": str(documents_path),
        "output_root": str(output), "network_fetch": False, "llm_called": False,
        "full_discovery_expanded": False, "production_owner_replaced": False,
        "canonical_modified": False, "frontend_modified": False, "db_specific_rule": False,
        "profile_hints_decisive": False, "input_mode": "FROZEN_MYSQL_HTML_AND_DOCUMENTIR",
    }
    _write_json(output / "runtime" / "run_manifest.json", run_manifest)
    report_path = repo / "reports" / "mysql_owner_shadow_v1.md"
    write_final_report(report_path, summary, stage_comparison, contamination, example_report, structural_preference, output)
    return summary


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_final_report(path: Path, summary: Mapping[str, Any], stage: Mapping[str, Any], contamination: Mapping[str, Any], examples: Mapping[str, Any], preference: Mapping[str, Any], output: Path) -> None:
    content = summary.get("content_retention") or {}
    identity = summary.get("identity_dry_replay") or {}
    lines = [
        "# MySQL DOM-first Owner Shadow Replay", "",
        "## 1. Scope and runtime", "",
        "- Input: 250 frozen MySQL documents and local HTML.", "- Discovery, network, LLM, Semantic, Canonical and Frontend were not run or modified.", "- Output is independent shadow data under `output/mysql_owner_shadow_v1/`.", "",
        "## 2. Frozen corpus", "",
        f"- Frozen documents: **{(summary.get('input') or {}).get('frozen_documents', 0)}**", f"- Rebuilt structural documents: **{(summary.get('input') or {}).get('structural_documents', 0)}**", f"- Frozen HTML/path failures: **{(summary.get('input') or {}).get('frozen_html_failure_count', 0)}**", "",
        "## 3. Structural DocumentIR", "",
        f"- Blocks: **{(summary.get('documentir') or {}).get('blocks', 0)}**", f"- Strong defining anchors: **{(summary.get('documentir') or {}).get('strong_anchors', 0)}**", f"- Example/identity-shaped tokens retained separately: **{(summary.get('documentir') or {}).get('identity_shaped_tokens', 0)}**", f"- Container kinds: `{json.dumps((summary.get('documentir') or {}).get('structural_container_kinds', {}), sort_keys=True)}`", "",
        "## 4. Candidate generation", "",
        f"- Generated: **{(summary.get('candidates') or {}).get('generated', 0)}**", f"- Ranked: **{(summary.get('candidates') or {}).get('ranked', 0)}**", f"- Selected: **{(summary.get('candidates') or {}).get('selected', 0)}**", f"- By kind: `{json.dumps((summary.get('candidates') or {}).get('by_kind', {}), sort_keys=True)}`", "",
        "## 5. Deterministic ranking", "",
        "Ranking order is structural containment → defining anchor → peer symmetry → semantic completeness → boundary confidence → profile hint bonus → penalties → stable tie-breakers. Profile hints are never decisive.",
        f"- Review-required candidates: **{((summary.get('candidates') or {}).get('status') or {}).get('REVIEW_REQUIRED', 0)}**",
        f"- Alternate candidates: **{((summary.get('candidates') or {}).get('status') or {}).get('ALTERNATE', 0)}**", "",
        "## 6. Old versus new Owner", "",
        f"- Old Owner rows: **{(summary.get('owners') or {}).get('old_count', 0)}**", f"- New selected shadow owners: **{(summary.get('owners') or {}).get('new_selected_count', 0)}**", f"- Old by kind: `{json.dumps((summary.get('owners') or {}).get('old_by_kind', {}), sort_keys=True)}`", f"- New by kind: `{json.dumps((summary.get('owners') or {}).get('new_by_kind', {}), sort_keys=True)}`", "",
        "## 7. Content retention", "",
        f"- Audited content units: **{content.get('content_units', 0)}**", f"- Old retained: **{content.get('old_retained', 0)} ({_pct(float(content.get('old_ratio', 0.0)))} )**", f"- New retained: **{content.get('new_retained', 0)} ({_pct(float(content.get('new_ratio', 0.0)))} )**", f"- Recovered: **{content.get('recovered', 0)}**", f"- New loss: **{content.get('lost', 0)}**", "",
        "## 8. Boundary and subject loss", "",
        f"- OWNER_BOUNDARY_LOSS: old **{content.get('old_boundary_loss', 0)}**, new **{content.get('new_boundary_loss', 0)}**", f"- OWNER_SUBJECT_MISMATCH: old **{content.get('old_subject_mismatch', 0)}**, new **{content.get('new_subject_mismatch', 0)}**", "",
        "## 9. Example-anchor safety", "",
        f"- Example-root Owners: old **{examples.get('old_example_root_count', 0)}**, new **{examples.get('new_example_root_count', 0)}**", f"- New example-root rate: **{_pct(float(examples.get('new_example_root_rate', 0.0)))}**", "Example calls are recorded as non-defining tokens and do not become peer feature anchors.", "",
        "## 10. Contamination", "",
        f"- Old contaminated owners: **{contamination.get('old_owner_contamination_count', 0)}**", f"- New selected cross-feature owners: **{contamination.get('new_owner_contamination_count', 0)}**", f"- New same-surface multi-label review cases: **{contamination.get('new_content_multi_identity_review_count', 0)}**", f"- Candidate multi-anchor cases: **{contamination.get('candidate_multi_anchor_count', 0)}**", "",
        "## 11. Structural preference", "",
        f"- Comparable cases: **{preference.get('cases', 0)}**", f"- Structural selected: **{preference.get('structural_selected', 0)}**", f"- Structural preference rate: **{_pct(float(preference.get('structural_preference_rate', 0.0)))}**", "",
        "## 12. Identity dry replay", "",
        f"- Occurrences: **{identity.get('occurrences', 0)}**", f"- Candidates: **{identity.get('candidates', 0)}**", f"- Accepted: **{identity.get('accepted', 0)}**", f"- Rejected: **{identity.get('rejected', 0)}**", f"- Identity units: **{identity.get('units', 0)}**", f"- Groups by type: `{json.dumps(identity.get('groups_by_type', {}), sort_keys=True)}`",
        f"- Status counts (new): `{json.dumps((identity.get('quality_audit') or {}).get('groups_by_status', {}), sort_keys=True)}`",
        f"- Status counts (old): `{json.dumps(((identity.get('comparison') or {}).get('old_by_status') or {}), sort_keys=True)}`",
        f"- Identity keys added/lost: **{(identity.get('comparison') or {}).get('added_count', 0)} / {(identity.get('comparison') or {}).get('lost_count', 0)}**",
        f"- Added by status: `{json.dumps(((identity.get('comparison') or {}).get('added_by_status') or {}), sort_keys=True)}`",
        f"- Lost by status: `{json.dumps(((identity.get('comparison') or {}).get('lost_by_status') or {}), sort_keys=True)}`",
        f"- Status changes: **{(identity.get('comparison') or {}).get('status_changed_count', 0)}**",
        "- Full added/lost/status-change identity list: `output/mysql_owner_shadow_v1/identity/comparison.json`", "",
        "## 13. Recommendation", "",
    ]
    old_ratio = float(content.get("old_ratio", 0.0))
    new_ratio = float(content.get("new_ratio", 0.0))
    precision_stable = (
        int(contamination.get("new_owner_contamination_count", 0)) <= int(contamination.get("old_owner_contamination_count", 0))
        and int(content.get("new_subject_mismatch", 0)) <= int(content.get("old_subject_mismatch", 0))
    )
    ranking_stable = int(((summary.get("candidates") or {}).get("status") or {}).get("REVIEW_REQUIRED", 0)) == 0
    identity_stable = not bool(summary.get("identity_dry_replay_error")) and int(identity.get("units", 0)) > 0
    if new_ratio > old_ratio and precision_stable and ranking_stable and identity_stable:
        lines.append("**Owner Productionization → Identity Quality Replay → Evidence Bundle Rebuild → Semantic LLM Replay**")
    else:
        lines.append("**Continue Owner shadow investigation; do not productionize yet.**")
    lines.extend(["", f"Full artifacts: `{output}`", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
