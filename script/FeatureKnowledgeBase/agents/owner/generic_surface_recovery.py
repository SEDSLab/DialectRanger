"""Generic, shadow-only recovery of omitted structured feature surfaces.

The production Owner path is intentionally untouched.  This module only
inspects frozen DocumentIR table rows and recovers a bounded row when its
local table/section semantics describe an operator-like surface.  It does
not use a feature inventory or an identity allowlist; in particular, the
word label is never the reason for recovery by itself.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any, Iterable, Mapping

from agents.contracts.base import stable_id
from agents.contracts.owner import make_owner_hypothesis
from agents.contracts.owner_inference import make_owner_candidate


_CALL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n()]*\)$")
_SYMBOL_RE = re.compile(r"^[+*/<>=!|&^~%?-]{1,3}$")
_WORD_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_HEADER_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_FIELD_LABEL_RE = re.compile(
    r"^(?:type|name|value|description|default|scope|range|unit|format|"
    r"command[- ]line(?:\s+format)?|system\s+variable|option|options)$",
    re.I,
)
_EXAMPLE_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|EXPLAIN|SHOW|SET)\b|"
    r"(?:mysql>|psql>|duckdb>|sqlite>|clickhouse>|postgres(?:ql)?>)",
    re.I,
)
_OPERATOR_CONTEXT_RE = re.compile(
    r"\b(?:operator|operators|predicate|predicates|boolean|expression|expressions|"
    r"comparison|comparisons|precedence|logical|non[- ]typed)\b",
    re.I,
)
_INSTALLATION_CONTEXT_RE = re.compile(
    r"\b(?:install|installation|repository|package|packages|registry|download|"
    r"compile|distribution|distribution method|setup method|container|source build)\b",
    re.I,
)
_CHARACTER_CLASS_HEADER_RE = re.compile(
    r"\b(?:character\s+class|class\s+name|character\s+class\s+name)\b",
    re.I,
)
_TYPE_HEADER_RE = re.compile(
    r"\b(?:data\s+type|datatype|type\s+alias|type\s+mapping|vendor\s+type|"
    r"mysql\s+type|column\s+type|storage\s+class|precision|range)\b",
    re.I,
)
_FUNCTION_HEADER_RE = re.compile(r"\b(?:function|functions|signature|callable|routine)\b", re.I)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact_upper(value: Any) -> str:
    return _text(value).upper()


def _is_callable(value: Any) -> bool:
    return bool(_CALL_RE.fullmatch(_text(value)))


def _is_symbol(value: Any) -> bool:
    return bool(_SYMBOL_RE.fullmatch(_text(value)))


def _is_word_label(value: Any) -> bool:
    text = _text(value)
    return bool(_WORD_LABEL_RE.fullmatch(text)) and len(text) <= 96


def _is_header_row(block: Mapping[str, Any]) -> bool:
    headers = [value.casefold() for value in _headers(block)]
    cells = [value.casefold() for value in _cells(block)]
    return bool(headers and len(cells) >= len(headers) and cells[:len(headers)] == headers)


def _headers(block: Mapping[str, Any]) -> list[str]:
    return [_text(value) for value in (block.get("table_metadata") or {}).get("headers") or [] if _text(value)]


def _cells(block: Mapping[str, Any]) -> list[str]:
    return [_text(value) for value in (block.get("table_metadata") or {}).get("cells") or [] if _text(value)]


def _heading(block: Mapping[str, Any]) -> str:
    return " ".join(_text(value) for value in block.get("heading_ancestry") or [] if _text(value))


def _header_words(values: Iterable[str]) -> set[str]:
    return {word.upper() for value in values for word in _HEADER_WORD_RE.findall(value)}


def _table_key(page: Mapping[str, Any], block: Mapping[str, Any]) -> tuple[str, str]:
    page_id = str(page.get("page_id") or "")
    # DocumentIR gives every HTML table row its own TABLE_ROW boundary.  The
    # first non-row ancestor is the stable local table container and is the
    # only safe grouping key for peer/column inference.  Falling back to the
    # row parent keeps hand-built fixtures and older DocumentIR compatible.
    parent_id = ""
    for ancestor in block.get("ancestor_chain") or []:
        if str(ancestor.get("kind") or "").upper() != "TABLE_ROW" and ancestor.get("id"):
            parent_id = str(ancestor.get("id"))
            break
    parent_id = parent_id or str(block.get("structural_parent_id") or block.get("structural_boundary_id") or "")
    return page_id, parent_id


def _row_text(block: Mapping[str, Any]) -> str:
    return " ".join([_text(block.get("text")), *_cells(block), *_headers(block), _heading(block)])


def _row_operator_signal(block: Mapping[str, Any], headers: list[str], peers: Mapping[str, Any]) -> tuple[bool, list[str]]:
    text = _row_text(block)
    signals: list[str] = []
    if _OPERATOR_CONTEXT_RE.search(text):
        signals.append("LOCAL_OPERATOR_LANGUAGE")
    if any(re.search(r"\b(?:operator|predicate|expression|precedence)\b", value, re.I) for value in headers):
        signals.append("OPERATOR_HEADER")
    if peers.get("heading_operator_context"):
        signals.append("OPERATOR_SECTION_CONTEXT")
    return bool(signals), signals


def _infer_group_role(headers: list[str], heading: str, rows: list[Mapping[str, Any]]) -> tuple[str, list[str], dict[str, Any]]:
    header_text = " ".join(headers)
    row_text = " ".join(_row_text(row) for row in rows)
    labels = [_cells(row)[0] for row in rows if _cells(row)]
    callable_count = sum(_is_callable(value) for value in labels)
    symbol_count = sum(_is_symbol(value) for value in labels)
    label_count = len(labels)
    callable_ratio = callable_count / label_count if label_count else 0.0
    header_words = _header_words(headers)
    # Do not let one incidental word in a large reference table (for example
    # a function description containing "download") turn the whole table into
    # an installation table.  Installation is a table/section role, so use
    # headers/heading first and require a strong row ratio for row-only cases.
    installation_header_or_heading = bool(_INSTALLATION_CONTEXT_RE.search(f"{header_text} {heading}"))
    installation_rows = sum(bool(_INSTALLATION_CONTEXT_RE.search(_row_text(row))) for row in rows)
    installation_ratio = installation_rows / label_count if label_count else 0.0
    installation = installation_header_or_heading or installation_ratio >= 0.5
    character_class = bool(_CHARACTER_CLASS_HEADER_RE.search(header_text))
    explicit_type = bool(_TYPE_HEADER_RE.search(header_text))
    type_mapping = len(headers) >= 2 and any(re.search(r"\btype\b", value, re.I) for value in headers)
    operator_section = bool(_OPERATOR_CONTEXT_RE.search(f"{header_text} {heading}"))
    function_section = bool(_FUNCTION_HEADER_RE.search(f"{header_text} {heading}"))
    operator_rows = sum(bool(_OPERATOR_CONTEXT_RE.search(_row_text(row))) for row in rows)
    operator_row_ratio = operator_rows / label_count if label_count else 0.0

    if installation and not explicit_type:
        role = "INSTALLATION_OPTIONS"
        signals = ["INSTALLATION_LANGUAGE", "NON_SQL_SETUP_COLUMNS"]
    elif character_class:
        role = "CHARACTER_CLASS_TABLE"
        signals = ["CHARACTER_CLASS_HEADERS", "NON_CALLABLE_LABEL_PEERS"]
    elif explicit_type or type_mapping:
        role = "DATATYPE_CATALOG"
        signals = ["TYPE_HEADERS", "TYPE_MAPPING_SURFACE"]
    elif operator_section or operator_row_ratio >= 0.25 or symbol_count >= max(2, label_count // 3 if label_count else 2):
        role = "OPERATOR_CATALOG"
        signals = ["OPERATOR_SECTION_OR_COLUMNS"]
    elif function_section and callable_count:
        role = "FUNCTION_CATALOG"
        signals = ["FUNCTION_CONTEXT", "CALLABLE_PEER_SYMMETRY"]
    elif callable_ratio >= 0.5 and callable_count >= 2:
        role = "FUNCTION_CATALOG"
        signals = ["CALLABLE_PEER_SYMMETRY"]
    elif headers:
        role = "SUMMARY_INDEX"
        signals = ["GENERIC_TABULAR_SUMMARY"]
    else:
        role = "UNKNOWN_TABLE"
        signals = ["INSUFFICIENT_LOCAL_TABLE_ROLE"]

    return role, signals, {
        "headers": headers,
        "heading": heading,
        "peer_count": len(rows),
        "callable_count": callable_count,
        "callable_ratio": round(callable_ratio, 6),
        "symbol_count": symbol_count,
        "operator_row_count": operator_rows,
        "operator_row_ratio": round(operator_row_ratio, 6),
        "header_words": sorted(header_words),
        "installation_context": installation,
        "installation_header_or_heading": installation_header_or_heading,
        "installation_row_count": installation_rows,
        "installation_row_ratio": round(installation_ratio, 6),
        "character_class_context": character_class,
        "explicit_type_context": explicit_type,
        "type_mapping_context": type_mapping,
        "operator_section_context": operator_section,
        "function_section_context": function_section,
    }


def build_table_role_index(documents: Iterable[Mapping[str, Any]]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Infer table roles from local headers, peers and ancestry only."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pages = {str(page.get("page_id")): page for page in documents}
    for page in pages.values():
        for block in page.get("blocks") or []:
            if str(block.get("block_kind") or "").upper() != "TABLE_ROW":
                continue
            groups[_table_key(page, block)].append(dict(block))

    index: dict[tuple[str, str], dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items()):
        headers = _headers(rows[0]) if rows else []
        if not headers:
            for row in rows:
                if _headers(row):
                    headers = _headers(row)
                    break
        heading = _heading(rows[0]) if rows else ""
        role, signals, stats = _infer_group_role(headers, heading, rows)
        group = {
            "table_key": list(key),
            "table_role": role,
            "role_signals": signals,
            "stats": stats,
            "row_roles": {},
        }
        for row in rows:
            cells = _cells(row)
            row_text = _row_text(row)
            row_signals: list[str] = []
            if _INSTALLATION_CONTEXT_RE.search(row_text) and not _TYPE_HEADER_RE.search(" ".join(headers)):
                row_role = "INSTALLATION_OPTIONS"
                row_signals.append("LOCAL_INSTALLATION_LANGUAGE")
            elif _CHARACTER_CLASS_HEADER_RE.search(" ".join(headers)):
                row_role = "CHARACTER_CLASS_TABLE"
                row_signals.append("CHARACTER_CLASS_HEADERS")
            elif role == "DATATYPE_CATALOG":
                # A datatype declaration row can mention Boolean/Expression
                # words in its value.  The table's local type-mapping role is
                # stronger than that incidental vocabulary.
                row_role = role
                row_signals.append("TABLE_TYPE_MAPPING_OR_DECLARATION")
            elif cells and _is_callable(cells[0]):
                # A mixed reference table may sit under an operator heading,
                # but a callable row keeps its function surface locally.
                row_role = "FUNCTION_CATALOG"
                row_signals.append("CALLABLE_ROW_SHAPE")
            elif _OPERATOR_CONTEXT_RE.search(row_text):
                row_role = "OPERATOR_CATALOG"
                row_signals.append("LOCAL_OPERATOR_LANGUAGE")
            elif role == "FUNCTION_CATALOG" and cells and (_is_callable(cells[0]) or stats["callable_ratio"] >= 0.5):
                row_role = role
                row_signals.append("CALLABLE_PEER_OR_FUNCTION_CONTEXT")
            else:
                row_role = role
                row_signals.append("INHERITED_LOCAL_TABLE_ROLE")
            block_id = str(row.get("block_id") or "")
            group["row_roles"][block_id] = {"row_role": row_role, "signals": row_signals}
            page = pages.get(key[0], {})
            audit.append({
                "page_id": key[0],
                "source_url": page.get("url"),
                "table_parent_id": key[1],
                "block_id": block_id,
                "block_text": row.get("text"),
                "headers": headers,
                "heading_ancestry": row.get("heading_ancestry") or [],
                "table_role": role,
                "row_role": row_role,
                "role_signals": sorted(set(signals + row_signals)),
                "role_stats": stats,
            })
        index[key] = group
    return index, audit


def _candidate_owner(
    page: Mapping[str, Any],
    block: Mapping[str, Any],
    *,
    identity: str,
    table_role: str,
    row_role: str,
    role_signals: Iterable[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    page_id = str(page.get("page_id") or "")
    block_id = str(block.get("block_id") or "")
    parent_id = str(block.get("structural_parent_id") or block.get("structural_boundary_id") or "") or None
    anchor_id = stable_id("recovered_feature_anchor", [page_id, block_id, identity])
    reasons = sorted({"GENERIC_SURFACE_RECOVERY", "BOUNDED_TABLE_ROW", "LOCAL_TARGET_CONTEXT", *role_signals})
    candidate = make_owner_candidate(
        dbms=str(page.get("dbms") or "mysql"), page_id=page_id,
        source_url=str(page.get("url") or ""), page_hash=str(page.get("page_hash") or ""),
        html_path=str(page.get("html_path") or ""), owner_kind="TABLE_ROW_OWNER", template="table_row",
        start_block_id=block_id, end_block_id=block_id, root_block_id=block_id,
        member_block_ids=[block_id], structural_parent_id=parent_id,
        structural_parent_kind=str(block.get("structural_parent_kind") or "TABLE_ROW"),
        ancestor_chain=list(block.get("ancestor_chain") or []), sibling_index=block.get("sibling_index"),
        identity_anchor_ids=[anchor_id], identity_shaped_token_ids=[],
        structural_signals=["EXPLICIT_STRUCTURAL_PARENT", "BOUNDED_WORD_LABEL", "LOCAL_TABLE_ROLE"],
        profile_hints=[],
        ranking_features={
            "structural_containment_score": 0.98,
            "defining_anchor_score": 0.88,
            "peer_symmetry_score": 0.92,
            "semantic_completeness_score": 0.76,
            "boundary_confidence": 0.99,
            "profile_hint_bonus": 0.0,
            "example_penalty": 0.0,
            "contamination_penalty": 0.0,
            "weak_anchor_penalty": 0.0,
        },
        contamination_flags=[], confidence=0.88, tier="STRONG", status="CANDIDATE",
        parent_ids=[str(page.get("document_id") or ""), block_id], reason_codes=reasons,
    )
    candidate.update({
        "primary_identity": identity,
        "identity_values": [identity],
        "subject_identities": [identity],
        "subject_evidence": [],
        "anchor_evidence": [{"anchor_id": anchor_id, "identity": identity, "raw": _cells(block)[0] if _cells(block) else identity, "source": "recovered_table_row"}],
        "identity_shaped_evidence": [],
        "origin": "generic_surface_recovery",
        "primary_anchor_source": "recovered_table_row",
        "primary_anchor_block_id": block_id,
        "root_is_example": False,
        "foreign_identity_values": [],
        "profile_hints_used": [],
        "profile_hint_overridden_by_structure": False,
        "member_block_kinds": [str(block.get("block_kind") or "TABLE_ROW")],
        "table_role": table_role,
        "row_role": row_role,
        "recovery_reason": reasons,
        "selection_status": "SELECTED",
        "rank": 1,
    })
    owner = make_owner_hypothesis(
        dbms=str(page.get("dbms") or "mysql"), page_id=page_id,
        source_url=str(page.get("url") or ""), page_hash=str(page.get("page_hash") or ""),
        html_path=str(page.get("html_path") or ""), start_block_id=block_id,
        end_block_id=block_id, root_block_id=block_id, member_block_ids=[block_id],
        owner_kind="TableRowOwner", identity_signals=reasons, confidence=0.88,
        tier="STRONG", template="table_row", status="STRONG",
        parent_ids=[str(candidate.get("candidate_id") or ""), str(page.get("document_id") or "")],
        reason_codes=reasons,
    )
    owner.update({
        "shadow_schema_version": "mysql-generic-surface-owner-shadow/v2",
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "candidate_kind": "TABLE_ROW_OWNER",
        "selection_status": "SELECTED",
        "primary_identity": identity,
        "identity_values": [identity],
        "candidate_scores": dict(candidate.get("ranking_features") or {}),
        "penalties": {"example": 0.0, "contamination": 0.0, "weak_anchor": 0.0},
        "profile_hint": {},
        "table_role": table_role,
        "row_role": row_role,
        "recovery_reason": reasons,
        "foreign_identity_values": [],
        "complementary_owner": True,
    })
    return candidate, owner


def recover_generic_surface_owners(
    documents: Iterable[Mapping[str, Any]],
    existing_owners: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Return additive Owner candidates/owners for omitted local operator rows."""
    documents = [dict(row) for row in documents]
    existing = [dict(row) for row in existing_owners]
    table_index, audit = build_table_role_index(documents)
    covered = {
        (str(owner.get("page_id") or ""), str(block_id))
        for owner in existing
        for block_id in owner.get("member_block_ids") or []
    }
    candidates: list[dict[str, Any]] = []
    owners: list[dict[str, Any]] = []
    for page in documents:
        for block in page.get("blocks") or []:
            if str(block.get("block_kind") or "").upper() != "TABLE_ROW":
                continue
            key = _table_key(page, block)
            group = table_index.get(key) or {}
            block_id = str(block.get("block_id") or "")
            if (str(page.get("page_id") or ""), block_id) in covered:
                continue
            cells = _cells(block)
            if (
                not cells
                or _is_header_row(block)
                or _FIELD_LABEL_RE.fullmatch(_text(cells[0]))
                or not _is_word_label(cells[0])
                or _is_callable(cells[0])
                or _is_symbol(cells[0])
            ):
                continue
            row_info = (group.get("row_roles") or {}).get(block_id) or {}
            row_role = str(row_info.get("row_role") or group.get("table_role") or "UNKNOWN_TABLE")
            row_text = _row_text(block)
            operator_signal, operator_signals = _row_operator_signal(block, _headers(block), group.get("stats") or {})
            if row_role != "OPERATOR_CATALOG" or not operator_signal or _EXAMPLE_RE.search(row_text):
                continue
            identity = _compact_upper(cells[0])
            candidate, owner = _candidate_owner(
                page, block, identity=identity, table_role=str(group.get("table_role") or row_role),
                row_role=row_role, role_signals=[*operator_signals, *list(row_info.get("signals") or [])],
            )
            candidates.append(candidate)
            owners.append(owner)
    return candidates, owners, table_index, audit


def make_recovered_occurrence(page: Mapping[str, Any], block: Mapping[str, Any], owner: Mapping[str, Any]) -> dict[str, Any] | None:
    cells = _cells(block)
    if not cells or not _is_word_label(cells[0]):
        return None
    raw = cells[0]
    identity = _compact_upper(raw)
    owner_id = str(owner.get("owner_id") or "")
    block_id = str(block.get("block_id") or "")
    occurrence_id = stable_id("identity_occurrence", [owner_id, block_id, raw, "table_row_recovery"])
    return {
        "schema_version": "IdentityOccurrence/v1",
        "occurrence_id": occurrence_id,
        "raw_identity": raw,
        "normalized_identity": identity,
        "owner_id": owner_id,
        "page_id": str(page.get("page_id") or ""),
        "source_url": str(page.get("url") or ""),
        "source_block_id": block_id,
        "source_type": "table_row",
        "raw_evidence": str(block.get("text") or ""),
        "identity_signals": ["GENERIC_SURFACE_RECOVERY", "LOCAL_OPERATOR_CONTEXT", "table_row"],
        "type_hint": "UNCERTAIN",
        "occurrence_role": "DEFINING_IDENTITY",
        "defining": True,
        "surface_role": "FEATURE_CATALOG",
        "surface_confidence": 0.88,
        "surface_confidence_label": "HIGH",
        "surface_evidence": ["generic_surface_recovery", "local_table_role"],
        "feature_evidence": [],
        "type_evidence": [],
        "source_context": {
            "owner_kind": str(owner.get("owner_kind") or "TableRowOwner"),
            "template": "table_row",
            "profile_hint": {},
            "table_headers": _headers(block),
            "heading_ancestry": list(block.get("heading_ancestry") or []),
            "block_kind": str(block.get("block_kind") or "TABLE_ROW"),
            "surface_recovery": True,
        },
        "provenance": {
            "dbms": str(page.get("dbms") or "mysql"),
            "source_url": str(page.get("url") or ""),
            "page_id": str(page.get("page_id") or ""),
            "page_hash": str(page.get("page_hash") or ""),
            "html_path": str(page.get("html_path") or ""),
            "owner_id": owner_id,
            "source_block_id": block_id,
            "producer_stage": "generic_surface_recovery",
            "producer_version": "mysql-generic-surface-owner-shadow/v2",
            "parent_ids": [owner_id, block_id],
            "reason_codes": ["GENERIC_SURFACE_RECOVERY", "LOCAL_OPERATOR_CONTEXT"],
        },
    }
