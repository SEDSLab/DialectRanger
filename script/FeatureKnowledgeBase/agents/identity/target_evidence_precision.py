"""Target evidence precision and CASE/IS surface recovery shadow replay.

This stage consumes only the frozen MySQL Owner shadow v1 and Identity shadow
v2 artifacts.  It refines target evidence using local table semantics and,
when required, adds a generic bounded Owner for an omitted operator-like table
row.  Production Owner, Discovery, Semantic, Canonical, and Frontend paths
are not imported as mutation targets.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from agents.contracts.base import stable_id
from agents.contracts.target_feature_evidence import TARGET_TYPES, make_target_feature_evidence
from agents.identity.production import build_identity_occurrences
from agents.identity.target_evidence_shadow import (
    _aggregate_group,
    _best_group,
    _compare_snapshots,
    _is_callable,
    _is_symbol,
    _read_json,
    _read_jsonl,
    _status_counts,
    _text,
    _type_counts,
    _write_json,
    _write_jsonl,
    classify_target_evidence,
)
from agents.owner.dom_first_shadow import (
    build_contamination_report,
    build_example_anchor_report,
    compare_content_units,
)
from agents.owner.generic_surface_recovery import (
    build_table_role_index,
    make_recovered_occurrence,
    recover_generic_surface_owners,
)


SCHEMA_VERSION = "mysql-identity-shadow-v3/v1"
TARGET_SCOPE = list(TARGET_TYPES)
_CALL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\s*\([^\n()]*\)$")
_SYMBOL_RE = re.compile(r"^[+*/<>=!|&^~%?-]{1,3}$")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
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
_TYPE_CONTEXT_RE = re.compile(
    r"\b(?:data\s+type|datatype|type\s+(?:alias|mapping|declaration|definition|syntax)|"
    r"column\s+(?:type|definition)|storage\s+class|precision|range)\b",
    re.I,
)
_FUNCTION_CONTEXT_RE = re.compile(r"\b(?:function|functions|signature|callable|routine)\b", re.I)
_NEGATIVE_TABLE_ROLES = {"INSTALLATION_OPTIONS", "CHARACTER_CLASS_TABLE", "CONFIGURATION_TABLE"}


def _words(value: Any) -> set[str]:
    return {word.upper() for word in _WORD_RE.findall(_text(value))}


def _cells(block: Mapping[str, Any]) -> list[str]:
    return [_text(value) for value in (block.get("table_metadata") or {}).get("cells") or [] if _text(value)]


def _headers(block: Mapping[str, Any]) -> list[str]:
    return [_text(value) for value in (block.get("table_metadata") or {}).get("headers") or [] if _text(value)]


def _table_key(page: Mapping[str, Any], block: Mapping[str, Any]) -> tuple[str, str]:
    parent_id = ""
    for ancestor in block.get("ancestor_chain") or []:
        if str(ancestor.get("kind") or "").upper() != "TABLE_ROW" and ancestor.get("id"):
            parent_id = str(ancestor.get("id"))
            break
    parent_id = parent_id or str(block.get("structural_parent_id") or block.get("structural_boundary_id") or "")
    return str(page.get("page_id") or ""), parent_id


def _block_context(
    occurrence: Mapping[str, Any],
    block: Mapping[str, Any],
    owner: Mapping[str, Any],
    page: Mapping[str, Any],
    table_index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    source_context = occurrence.get("source_context") or {}
    headers = _headers(block) or [_text(value) for value in source_context.get("table_headers") or [] if _text(value)]
    ancestry = [_text(value) for value in block.get("heading_ancestry") or [] if _text(value)] or [_text(value) for value in source_context.get("heading_ancestry") or [] if _text(value)]
    table = table_index.get(_table_key(page, block)) or {}
    row_info = (table.get("row_roles") or {}).get(str(block.get("block_id") or "")) or {}
    table_role = str(table.get("table_role") or "UNKNOWN_TABLE")
    row_role = str(row_info.get("row_role") or table_role)
    cell_text = " ".join(_cells(block))
    heading_text = " ".join(ancestry)
    block_text = _text(block.get("text"))
    page_text = " ".join([_text(page.get("url")), heading_text, block_text, " ".join(headers), cell_text])
    return {
        "block_kind": str(block.get("block_kind") or source_context.get("block_kind") or "").upper(),
        "headers": headers,
        "cells": _cells(block),
        "ancestry": ancestry,
        "heading_text": heading_text,
        "block_text": block_text,
        "page_text": page_text,
        "owner_kind": str(owner.get("candidate_kind") or owner.get("owner_kind") or source_context.get("owner_kind") or ""),
        "surface_role": str(occurrence.get("surface_role") or ""),
        "table_role": table_role,
        "row_role": row_role,
        "table_role_signals": list(table.get("role_signals") or []),
        "row_role_signals": list(row_info.get("signals") or []),
        "table_stats": dict(table.get("stats") or {}),
    }


def _has_explicit_callable(raw: str) -> bool:
    return bool(_CALL_RE.fullmatch(_text(raw)))


def _has_explicit_operator_context(context: Mapping[str, Any]) -> bool:
    return bool(_OPERATOR_CONTEXT_RE.search(" ".join([context.get("block_text", ""), context.get("heading_text", ""), " ".join(context.get("headers") or [])])))


def _has_type_context(context: Mapping[str, Any]) -> bool:
    return bool(_TYPE_CONTEXT_RE.search(" ".join([context.get("block_text", ""), context.get("heading_text", ""), " ".join(context.get("headers") or [])])))


def _has_function_context(context: Mapping[str, Any]) -> bool:
    return bool(_FUNCTION_CONTEXT_RE.search(" ".join([context.get("block_text", ""), context.get("heading_text", ""), " ".join(context.get("headers") or [])])))


def _is_example_occurrence(occurrence: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if str(occurrence.get("occurrence_role") or "") == "EXAMPLE_TOKEN":
        return True
    return context.get("block_kind") == "CODE" and bool(_EXAMPLE_RE.search(context.get("block_text", "")))


def _evidence_item(strength: str, reason: str, source: str) -> dict[str, str]:
    return {"strength": strength, "reason": reason, "source": source}


def _precision_classify(
    occurrence: Mapping[str, Any],
    *,
    block: Mapping[str, Any],
    owner: Mapping[str, Any],
    page: Mapping[str, Any],
    table_index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Refine target evidence without allowing page/Owner kind inheritance."""
    base = classify_target_evidence(occurrence, block=block, owner=owner, page=page)
    context = _block_context(occurrence, block, owner, page, table_index)
    identity = _text(occurrence.get("normalized_identity") or occurrence.get("raw_identity")).upper()
    raw = _text(occurrence.get("raw_identity") or identity)
    role = str(occurrence.get("occurrence_role") or "UNKNOWN")
    document_defining = bool(base.get("document_defining"))
    example_only = _is_example_occurrence(occurrence, context)
    header_row = context.get("block_kind") == "TABLE_ROW" and bool(context.get("headers")) and context.get("cells") == context.get("headers")
    evidence: dict[str, list[dict[str, Any]]] = {name: [] for name in TARGET_TYPES}
    non_target = [dict(item) for item in base.get("non_target_evidence") or []]
    reasons = set(str(value) for value in base.get("reason_codes") or [])
    is_table = context.get("block_kind") == "TABLE_ROW"
    row_role = str(context.get("row_role") or "UNKNOWN_TABLE")
    table_role = str(context.get("table_role") or "UNKNOWN_TABLE")
    callable_shape = _has_explicit_callable(raw)
    symbol_shape = bool(_SYMBOL_RE.fullmatch(raw))
    operator_context = _has_explicit_operator_context(context)
    type_context = _has_type_context(context)
    function_context = _has_function_context(context)

    if example_only or header_row or role in {"MENTION", "PARAMETER_OR_FIELD", "OUTPUT_TEXT", "CROSS_REFERENCE"}:
        document_defining = False
        if example_only:
            non_target.append({"code": "EXAMPLE_ONLY", "reason": "example invocation or code token", "source": "occurrence"})
            reasons.add("EXAMPLE_ONLY")
        if header_row:
            non_target.append({"code": "TABLE_HEADER_OR_LABEL", "reason": "table header row", "source": "table_structure"})
            reasons.add("TABLE_HEADER_OR_LABEL")
        if role in {"MENTION", "PARAMETER_OR_FIELD", "OUTPUT_TEXT", "CROSS_REFERENCE"}:
            non_target.append({"code": "NON_DEFINING_OCCURRENCE", "reason": "non-defining occurrence role", "source": "occurrence_role"})
            reasons.add("NON_DEFINING_OCCURRENCE")

    if is_table and row_role in _NEGATIVE_TABLE_ROLES:
        non_target.append({
            "code": "NON_TARGET_TABLE_ROLE",
            "reason": f"local table role is {row_role}, not a SQL feature catalog",
            "source": "table_role_inference",
        })
        reasons.add(f"NON_TARGET_{row_role}")
    elif document_defining and not example_only and not header_row:
        if is_table:
            # A table's Owner kind and a function-like page are not enough.
            # Function evidence requires a callable row, or an explicitly
            # function-labelled table with callable peer symmetry.
            if callable_shape:
                evidence["FUNCTION"].append(_evidence_item("STRONG", "standalone_callable_signature", "raw_identity"))
                reasons.add("CALLABLE_SIGNATURE")
            elif row_role == "FUNCTION_CATALOG" and function_context and context.get("table_stats", {}).get("callable_count", 0) >= 2 and context.get("table_stats", {}).get("callable_ratio", 0.0) >= 0.5:
                evidence["FUNCTION"].append(_evidence_item("MEDIUM", "explicit_function_catalog_with_callable_peers", "local_table"))
                reasons.add("FUNCTION_CATALOG_LOCAL")
            # Datatype evidence requires a local type mapping/declaration,
            # not a lone header named simply "Type".
            if row_role == "DATATYPE_CATALOG" and (type_context or context.get("table_stats", {}).get("type_mapping_context")):
                strength = "STRONG" if context.get("table_stats", {}).get("type_mapping_context") else "MEDIUM"
                evidence["DATATYPE"].append(_evidence_item(strength, "local_datatype_catalog_or_mapping", "local_table"))
                reasons.add("DATATYPE_CATALOG_LOCAL")
            # Word-like operators are admitted from local operator semantics,
            # including expression constructs without callable syntax.
            if not callable_shape and row_role == "OPERATOR_CATALOG" and (operator_context or context.get("table_role") == "OPERATOR_CATALOG"):
                strength = "STRONG" if operator_context else "MEDIUM"
                evidence["OPERATOR"].append(_evidence_item(strength, "local_operator_catalog_context", "local_table"))
                reasons.add("OPERATOR_CATALOG_LOCAL")
        else:
            # Preserve non-table strong evidence such as detailed callable
            # syntax and explicit expression syntax from the v2 audit.
            for feature_type in TARGET_TYPES:
                evidence[feature_type].extend(dict(item) for item in (base.get("evidence_by_type") or {}).get(feature_type, []))

    if not is_table:
        # The base classifier already handles expression syntax.  Remove only
        # page/profile-only signals; those are not target evidence.
        for feature_type in TARGET_TYPES:
            evidence[feature_type] = [
                dict(item) for item in evidence[feature_type]
                if str(item.get("reason") or "") not in {"function_catalog_label", "datatype_declaration_or_type_context"}
                or str(item.get("strength") or "") == "STRONG"
            ]

    if row_role in _NEGATIVE_TABLE_ROLES:
        evidence = {name: [] for name in TARGET_TYPES}

    target_types = sorted(feature_type for feature_type, values in evidence.items() if values)
    target_plausible = bool(target_types) and document_defining and not example_only and not header_row and row_role not in _NEGATIVE_TABLE_ROLES
    target_defining = bool(target_plausible)
    if target_defining:
        reasons.add("TARGET_FEATURE_EVIDENCE")
    else:
        reasons.add("NO_TARGET_FEATURE_EVIDENCE")
    if not target_types and document_defining and not non_target:
        non_target.append({"code": "STRUCTURE_ONLY", "reason": "structural Owner/table shape without target-type evidence", "source": "precision_audit"})
        reasons.add("STRUCTURE_ONLY")
    rank = {"STRONG": 3, "MEDIUM": 2, "WEAK": 1, "NONE": 0}
    strength = max((str(item.get("strength") or "NONE") for values in evidence.values() for item in values), key=lambda value: rank.get(value, 0), default="NONE")
    producer = dict(base.get("provenance") or {})
    producer.update({"producer_stage": "target_feature_evidence_precision_shadow", "producer_version": SCHEMA_VERSION})
    result = make_target_feature_evidence(
        document_defining=document_defining,
        target_feature_defining=target_defining,
        target_feature_plausible=target_plausible,
        target_types=target_types,
        evidence_by_type=evidence,
        non_target_evidence=non_target,
        strength=strength,
        reason_codes=sorted(reasons),
        provenance=producer,
    )
    result["precision_context"] = {
        "table_role": table_role,
        "local_section_role": row_role,
        "table_role_signals": context.get("table_role_signals", []),
        "row_role_signals": context.get("row_role_signals", []),
        "peer_identity_shape": {
            "peer_count": context.get("table_stats", {}).get("peer_count", 0),
            "callable_count": context.get("table_stats", {}).get("callable_count", 0),
            "callable_ratio": context.get("table_stats", {}).get("callable_ratio", 0.0),
            "symbol_count": context.get("table_stats", {}).get("symbol_count", 0),
        },
        "column_role": context.get("headers", []),
        "heading_context": context.get("ancestry", []),
        "syntax_strength": strength,
        "structural_evidence": ["TABLE_ROW" if is_table else context.get("block_kind", "UNKNOWN"), *context.get("row_role_signals", [])],
        "context_evidence": [*context.get("table_role_signals", []), *context.get("row_role_signals", [])],
    }
    return result


def _enrich_occurrences(
    occurrences: Iterable[Mapping[str, Any]],
    *,
    pages: Mapping[str, Mapping[str, Any]],
    blocks: Mapping[tuple[str, str], Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Any]],
    table_index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for source in occurrences:
        occurrence = dict(source)
        page = pages.get(str(occurrence.get("page_id") or ""), {})
        block = blocks.get((str(occurrence.get("page_id") or ""), str(occurrence.get("source_block_id") or "")), {})
        owner = owners.get(str(occurrence.get("owner_id") or ""), {})
        evidence = _precision_classify(occurrence, block=block, owner=owner, page=page, table_index=table_index)
        occurrence["document_defining"] = bool(evidence.get("document_defining"))
        occurrence["target_feature_defining"] = bool(evidence.get("target_feature_defining"))
        occurrence["target_feature_evidence"] = evidence
        occurrence["target_feature_evidence_summary"] = {
            "target_types": evidence.get("target_types", []),
            "strength": evidence.get("strength"),
            "reason_codes": evidence.get("reason_codes", []),
            "non_target_codes": sorted({item.get("code") for item in evidence.get("non_target_evidence", []) if item.get("code")}),
            "table_role": (evidence.get("precision_context") or {}).get("table_role"),
            "local_section_role": (evidence.get("precision_context") or {}).get("local_section_role"),
        }
        enriched.append(occurrence)
    return sorted(enriched, key=lambda row: (str(row.get("source_url") or ""), str(row.get("normalized_identity") or ""), str(row.get("occurrence_id") or "")))


def _occurrence_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        index[str(row.get("normalized_identity") or "").upper()].append(dict(row))
    return index


def _snapshot_by_identity(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("canonical_identity") or "").upper()].append(dict(row))
    return {identity: _best_group(values) or {} for identity, values in groups.items()}


def _four_way_comparison(old: list[dict[str, Any]], v1: list[dict[str, Any]], v2: list[dict[str, Any]], v3: list[dict[str, Any]]) -> dict[str, Any]:
    snapshots = {
        "old_production": _snapshot_by_identity(old),
        "owner_shadow_v1": _snapshot_by_identity(v1),
        "identity_shadow_v2": _snapshot_by_identity(v2),
        "identity_shadow_v3": _snapshot_by_identity(v3),
    }
    identities = sorted(set().union(*(set(value) for value in snapshots.values())))
    records = []
    for identity in identities:
        row = {"identity": identity}
        for label, values in snapshots.items():
            current = values.get(identity) or {}
            row[label] = {
                "status": current.get("group_status"),
                "type": current.get("feature_type"),
                "defining_variants": list(current.get("defining_variants") or []),
                "source_urls": list(current.get("source_urls") or []),
            }
        row["v2_to_v3_status_change"] = row["identity_shadow_v2"]["status"] != row["identity_shadow_v3"]["status"]
        row["v2_to_v3_type_change"] = row["identity_shadow_v2"]["type"] != row["identity_shadow_v3"]["type"]
        records.append(row)
    return {
        "schema_version": "mysql-identity-shadow-comparison/v3",
        "snapshots": {
            label: {
                "units": len(values),
                "status": dict(sorted(Counter(str(row.get("group_status") or "UNKNOWN") for row in values.values()).items())),
                "types": dict(sorted(Counter(str(row.get("feature_type") or "UNCERTAIN") for row in values.values()).items())),
            }
            for label, values in snapshots.items()
        },
        "status_changes_v2_to_v3": [row for row in records if row["v2_to_v3_status_change"]],
        "type_changes_v2_to_v3": [row for row in records if row["v2_to_v3_type_change"]],
        "identity_records": records,
    }


def _now_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _group_by_identity(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        identity = _text(row.get("canonical_identity") or row.get("normalized_identity")).upper()
        if identity:
            grouped[identity].append(dict(row))
    return grouped


def _identity_occurrence_evidence(rows: Iterable[Mapping[str, Any]], limit: int = 16) -> list[dict[str, Any]]:
    result = []
    for row in list(rows)[:limit]:
        result.append({
            "occurrence_id": row.get("occurrence_id"),
            "owner_id": row.get("owner_id"),
            "source_url": row.get("source_url"),
            "source_block_id": row.get("source_block_id"),
            "raw_identity": row.get("raw_identity"),
            "raw_evidence": row.get("raw_evidence"),
            "occurrence_role": row.get("occurrence_role"),
            "target_feature_evidence": row.get("target_feature_evidence", {}),
        })
    return result


def _group_record(group: Mapping[str, Any] | None) -> dict[str, Any]:
    group = group or {}
    return {
        "status": group.get("group_status"),
        "feature_type": group.get("feature_type"),
        "target_feature_plausible": bool(group.get("target_feature_plausible")),
        "target_evidence_strength": group.get("target_evidence_strength"),
        "target_type_scores": group.get("target_type_scores", {}),
        "defining_variants": list(group.get("defining_variants") or []),
        "variants": list(group.get("variants") or []),
        "owner_ids": list(group.get("owner_ids") or []),
        "source_urls": list(group.get("source_urls") or []),
        "reason_codes": list(group.get("reason_codes") or []),
    }


def _content_units_from_comparison_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    owner_field: str = "new_owner_ids",
) -> list[dict[str, Any]]:
    """Restore the input shape expected by the generic Owner evaluator.

    The frozen v1 retention artifact is already a comparison report, so its
    owner list is named ``old_owner_ids`` rather than ``feature_owner_ids``.
    Keeping this conversion local avoids changing the shared evaluator or the
    frozen artifact.
    """
    result = []
    for row in rows:
        item = dict(row)
        item["feature_owner_ids"] = list(row.get(owner_field) or row.get("feature_owner_ids") or [])
        result.append(item)
    return result


def _write_owner_shadow_v2(
    *,
    repo: Path,
    output_root: Path,
    documents: list[dict[str, Any]],
    existing_owners: list[dict[str, Any]],
    recovered_candidates: list[dict[str, Any]],
    recovered_owners: list[dict[str, Any]],
    table_audit: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write an additive Owner shadow without replaying the frozen v1 ranker."""
    output_root.mkdir(parents=True, exist_ok=True)
    v1_root = repo / "output" / "mysql_owner_shadow_v1"
    old_candidates = _read_jsonl(v1_root / "candidates" / "owner_candidates.jsonl")
    all_owners = existing_owners + recovered_owners
    all_candidates = old_candidates + recovered_candidates
    content_input = _read_json(v1_root / "evaluation" / "content_retention_comparison.json", {}) or {}
    content_rows = _content_units_from_comparison_rows(content_input.get("rows") or [])
    content = compare_content_units(content_rows, existing_owners, all_owners)
    contamination = build_contamination_report(content_rows, existing_owners, all_owners, recovered_candidates)
    examples = build_example_anchor_report(documents, existing_owners, all_owners)
    removed = sorted({str(row.get("owner_id") or "") for row in existing_owners} - {str(row.get("owner_id") or "") for row in all_owners})
    recovered_identity_counts = Counter(str(row.get("primary_identity") or "") for row in recovered_owners)

    _write_jsonl(output_root / "candidates" / "owner_candidates.jsonl", all_candidates)
    _write_jsonl(output_root / "candidates" / "recovered_candidates.jsonl", recovered_candidates)
    _write_jsonl(output_root / "owners" / "ranked_owner_hypotheses.jsonl", all_owners)
    _write_jsonl(output_root / "owners" / "recovered_owners.jsonl", recovered_owners)
    _write_jsonl(output_root / "evaluation" / "table_role_audit.jsonl", table_audit)
    _write_json(output_root / "evaluation" / "content_retention_comparison.json", content)
    _write_json(output_root / "evaluation" / "contamination_report.json", contamination)
    _write_json(output_root / "evaluation" / "example_anchor_report.json", examples)
    _write_json(output_root / "summary.json", {
        "schema_version": "mysql-generic-surface-owner-shadow/v2",
        "input": {
            "documents": len(documents),
            "frozen_owner_shadow_v1": len(existing_owners),
            "recovered_candidates": len(recovered_candidates),
            "recovered_owners": len(recovered_owners),
        },
        "owners": {
            "selected_v1": len(existing_owners),
            "selected_v2": len(all_owners),
            "removed_v1_owner_ids": removed,
            "recovered_by_identity": dict(sorted(recovered_identity_counts.items())),
        },
        "table_roles": dict(sorted(Counter(str(row.get("table_role") or "UNKNOWN_TABLE") for row in table_audit).items())),
        "content_retention": {
            "content_units": content.get("content_units", 0),
            "new_retained": content.get("new_retained_units", 0),
            "new_ratio": content.get("new_retention_ratio", 0.0),
            "lost": content.get("lost_units", 0),
        },
        "contamination": {
            "new_owner_contamination_count": contamination.get("new_owner_contamination_count", 0),
            "new_selected_cross_feature_owner_count": contamination.get("new_selected_cross_feature_owner_count", 0),
        },
        "examples": {
            "new_example_root_count": examples.get("new_example_root_count", 0),
        },
        "integrity": {
            "network_fetch": False,
            "llm_called": False,
            "v1_owners_removed": bool(removed),
            "production_owner_replaced": False,
            "db_specific_identity_rule": False,
        },
    })
    _write_json(output_root / "runtime" / "run_manifest.json", {
        "schema_version": "mysql-generic-surface-owner-shadow-run/v2",
        "pipeline_id": "mysql-generic-surface-owner-shadow/v2",
        "run_id": _now_id("mysql_owner_shadow_v2"),
        "input_document_path": str(v1_root / "documentir" / "structural_documents.jsonl"),
        "input_owner_path": str(v1_root / "owners" / "ranked_owner_hypotheses.jsonl"),
        "input_mode": "FROZEN_MYSQL_OWNER_SHADOW_V1_ADDITIVE_RECOVERY",
        "replayed_ranker": False,
        "recovered_only": True,
        "integrity": {
            "network_fetch": False,
            "llm_called": False,
            "production_owner_replaced": False,
            "db_specific_rule": False,
        },
    })
    return {
        "all_owners": all_owners,
        "all_candidates": all_candidates,
        "content": content,
        "contamination": contamination,
        "examples": examples,
        "removed": removed,
    }


def _append_recovered_occurrences(
    existing: Iterable[Mapping[str, Any]],
    documents: Iterable[Mapping[str, Any]],
    recovered_owners: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages = {str(row.get("page_id") or ""): row for row in documents}
    blocks = {
        (str(page.get("page_id") or ""), str(block.get("block_id") or "")): block
        for page in documents for block in page.get("blocks") or []
    }
    result = [dict(row) for row in existing]
    existing_ids = {str(row.get("occurrence_id") or "") for row in result}
    existing_keys = {
        (str(row.get("owner_id") or ""), str(row.get("source_block_id") or ""), _text(row.get("normalized_identity")).upper())
        for row in result
    }
    added: list[dict[str, Any]] = []
    for owner in recovered_owners:
        page_id = str(owner.get("page_id") or "")
        block_ids = list(owner.get("member_block_ids") or [])
        page = pages.get(page_id)
        if not page:
            continue
        for block_id in block_ids:
            block = blocks.get((page_id, str(block_id)))
            if not block:
                continue
            occurrence = make_recovered_occurrence(page, block, owner)
            if not occurrence:
                continue
            key = (str(occurrence.get("owner_id") or ""), str(occurrence.get("source_block_id") or ""), _text(occurrence.get("normalized_identity")).upper())
            if str(occurrence.get("occurrence_id") or "") in existing_ids or key in existing_keys:
                continue
            result.append(occurrence)
            added.append(occurrence)
            existing_ids.add(str(occurrence.get("occurrence_id") or ""))
            existing_keys.add(key)
    return result, added


def _probable_precision_audit(
    v2_groups: list[dict[str, Any]],
    v3_by_identity: Mapping[str, list[dict[str, Any]]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for old in sorted((row for row in v2_groups if row.get("group_status") == "PROBABLE_FEATURE"), key=lambda row: str(row.get("canonical_identity") or "")):
        identity = _text(old.get("canonical_identity")).upper()
        current = _best_group(v3_by_identity.get(identity, []))
        occurrences = occurrences_by_identity.get(identity, [])
        v2_type = old.get("feature_type") or old.get("type_hint")
        v3_type = (current or {}).get("feature_type")
        v3_status = (current or {}).get("group_status")
        v3_target = bool(current and current.get("target_feature_plausible"))
        table_roles = sorted({
            str(((row.get("target_feature_evidence") or {}).get("precision_context") or {}).get("table_role") or "UNKNOWN_TABLE")
            for row in occurrences
        })
        if not v3_target or v3_status == "NON_FEATURE_ONLY":
            classification = "FALSE_TARGET_CONTEXT_MISCLASSIFIED" if any(role in _NEGATIVE_TABLE_ROLES for role in table_roles) else "FALSE_TARGET_STRUCTURE_ONLY"
            reason = "v2 probable evidence was removed by local non-target table semantics"
        elif v2_type and v3_type and v2_type != v3_type:
            classification = "TYPE_WRONG"
            reason = "local syntax/context selects a different target type"
        elif v3_status == "PROBABLE_FEATURE":
            classification = "TYPE_CORRECT_BUT_WEAK"
            reason = "target evidence remains credible but is medium-strength or incomplete"
        else:
            classification = "VALID_TARGET"
            reason = "target evidence is retained with stronger or consistent local support"
        rows.append({
            "identity": identity,
            "v2_status": old.get("group_status"),
            "v2_type": v2_type,
            "v3_status": v3_status,
            "v3_type": v3_type,
            "owner_kinds": sorted({str((row.get("source_context") or {}).get("owner_kind") or "UNKNOWN") for row in occurrences}),
            "source_urls": sorted({str(row.get("source_url") or "") for row in occurrences if row.get("source_url")}),
            "raw_identity_evidence": _identity_occurrence_evidence(occurrences),
            "function_evidence": (current or {}).get("target_type_evidence", {}).get("FUNCTION", []),
            "operator_evidence": (current or {}).get("target_type_evidence", {}).get("OPERATOR", []),
            "datatype_evidence": (current or {}).get("target_type_evidence", {}).get("DATATYPE", []),
            "non_target_evidence": (current or {}).get("non_target_evidence", []),
            "table_roles": table_roles,
            "classification": classification,
            "reason": reason,
        })
    return rows


def _type_correction_audit(
    v2_comparison: Mapping[str, Any],
    comparison: Mapping[str, Any],
    v3_by_identity: Mapping[str, list[dict[str, Any]]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    identities = {
        _text(row.get("identity")).upper()
        for row in v2_comparison.get("type_corrections") or []
        if _text(row.get("identity"))
    }
    identities.update(_text(row.get("identity")).upper() for row in comparison.get("type_changes_v2_to_v3") or [] if _text(row.get("identity")))
    v2_rows = {_text(row.get("identity")).upper(): row for row in v2_comparison.get("type_corrections") or []}
    changed_rows = {_text(row.get("identity")).upper(): row for row in comparison.get("type_changes_v2_to_v3") or []}
    rows = []
    for identity in sorted(identities):
        current = _best_group(v3_by_identity.get(identity, []))
        occurrences = occurrences_by_identity.get(identity, [])
        prior = v2_rows.get(identity) or {}
        changed = changed_rows.get(identity) or {}
        v2_snapshot = prior.get("identity_refined_shadow") or changed.get("identity_shadow_v2") or {}
        v3_snapshot = changed.get("identity_shadow_v3") or _group_record(current)
        old_snapshot = prior.get("old") or {}
        v2_type = v2_snapshot.get("type")
        v3_type = v3_snapshot.get("type") or (current or {}).get("feature_type")
        if v3_type == v2_type:
            classification = "TYPE_PRESERVED_WITH_LOCAL_EVIDENCE"
        elif v3_type == "UNCERTAIN" or not v3_type:
            classification = "TYPE_REMAINS_UNCERTAIN"
        else:
            classification = "TYPE_CHANGED_BY_LOCAL_ROLE"
        rows.append({
            "identity": identity,
            "audit_scope": "V2_ORIGINAL" if identity in v2_rows else "V2_TO_V3_EXTENDED",
            "old_type": old_snapshot.get("type"),
            "v2_type": v2_type,
            "v3_type": v3_type,
            "old_status": old_snapshot.get("status"),
            "v2_status": v2_snapshot.get("status"),
            "v3_status": v3_snapshot.get("status") or (current or {}).get("group_status"),
            "v2_reason_codes": prior.get("reason_codes") or changed.get("reason_codes") or [],
            "v3_target_type_evidence": (current or {}).get("target_type_evidence", {}),
            "v3_occurrence_evidence": _identity_occurrence_evidence(occurrences),
            "classification": classification,
            "reason": "; ".join(sorted({str(code) for row in occurrences for code in (row.get("target_feature_evidence") or {}).get("reason_codes", [])})) or "no additional local reason code",
        })
    return rows


def _old_confirmed_risk_v3_audit(
    old_risk_rows: list[dict[str, Any]],
    v3_by_identity: Mapping[str, list[dict[str, Any]]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for old in sorted(old_risk_rows, key=lambda row: str(row.get("identity") or "")):
        identity = _text(old.get("identity")).upper()
        current = _best_group(v3_by_identity.get(identity, []))
        occurrences = occurrences_by_identity.get(identity, [])
        plausible = bool(current and current.get("target_feature_plausible"))
        old_type = old.get("old_type")
        v3_type = (current or {}).get("feature_type")
        if not plausible or (current and current.get("group_status") == "NON_FEATURE_ONLY"):
            classification = "OLD_FALSE_POSITIVE"
        elif old_type and v3_type and old_type != v3_type:
            classification = "TRUE_TARGET_WRONG_TYPE"
        elif plausible:
            classification = "TRUE_TARGET_CORRECT_TYPE"
        else:
            classification = "INSUFFICIENT_EVIDENCE"
        rows.append({
            **old,
            "v3_type": v3_type,
            "v3_status": (current or {}).get("group_status"),
            "v3_target_evidence": (current or {}).get("target_type_evidence", {}),
            "v3_occurrence_evidence": _identity_occurrence_evidence(occurrences),
            "classification": classification,
        })
    return rows


def _new_datatype_review(
    v3_groups: list[dict[str, Any]],
    old_by_identity: Mapping[str, Mapping[str, Any]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for group in v3_groups:
        if group.get("feature_type") != "DATATYPE":
            continue
        identity = _text(group.get("canonical_identity")).upper()
        old = old_by_identity.get(identity) or {}
        if old.get("feature_type") == "DATATYPE":
            continue
        rows.append({
            "identity": identity,
            "v3_status": group.get("group_status"),
            "v3_type": group.get("feature_type"),
            "old_type": old.get("feature_type"),
            "old_status": old.get("group_status"),
            "source_urls": list(group.get("source_urls") or []),
            "variants": list(group.get("variants") or []),
            "datatype_evidence": group.get("target_type_evidence", {}).get("DATATYPE", []),
            "occurrences": _identity_occurrence_evidence(occurrences_by_identity.get(identity, [])),
            "review_reason": "new datatype classification must be supported by local declaration/mapping semantics",
        })
    return rows


def _non_callable_function_review(
    v3_groups: list[dict[str, Any]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for group in v3_groups:
        if group.get("feature_type") != "FUNCTION":
            continue
        variants = list(group.get("variants") or [])
        if any(_is_callable(value) for value in variants):
            continue
        identity = _text(group.get("canonical_identity")).upper()
        rows.append({
            "identity": identity,
            "status": group.get("group_status"),
            "variants": variants,
            "source_urls": list(group.get("source_urls") or []),
            "function_evidence": group.get("target_type_evidence", {}).get("FUNCTION", []),
            "occurrences": _identity_occurrence_evidence(occurrences_by_identity.get(identity, [])),
            "review_reason": "FUNCTION without callable variant requires explicit local catalog evidence",
        })
    return rows


def _word_operator_review(
    v3_groups: list[dict[str, Any]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for group in v3_groups:
        if group.get("feature_type") != "OPERATOR":
            continue
        identity = _text(group.get("canonical_identity")).upper()
        if not identity or _is_symbol(identity):
            continue
        rows.append({
            "identity": identity,
            "status": group.get("group_status"),
            "variants": list(group.get("variants") or []),
            "source_urls": list(group.get("source_urls") or []),
            "operator_evidence": group.get("target_type_evidence", {}).get("OPERATOR", []),
            "occurrences": _identity_occurrence_evidence(occurrences_by_identity.get(identity, [])),
        })
    return rows


def _remaining_uncertain(
    v3_groups: list[dict[str, Any]],
    occurrences_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        {
            "identity": _text(group.get("canonical_identity")).upper(),
            "status": group.get("group_status"),
            "feature_type": group.get("feature_type"),
            "target_evidence_strength": group.get("target_evidence_strength"),
            "reason_codes": list(group.get("reason_codes") or []),
            "occurrences": _identity_occurrence_evidence(occurrences_by_identity.get(_text(group.get("canonical_identity")).upper(), [])),
        }
        for group in v3_groups
        if group.get("group_status") == "UNCERTAIN_FEATURE"
    ]


def _case_is_surface_trace(
    documents: list[dict[str, Any]],
    v1_candidates: list[dict[str, Any]],
    v1_owners: list[dict[str, Any]],
    recovered_owners: list[dict[str, Any]],
    v3_occurrences: Mapping[str, list[dict[str, Any]]],
    v3_by_identity: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    owner_blocks_v1 = defaultdict(list)
    for owner in v1_owners:
        for block_id in owner.get("member_block_ids") or []:
            owner_blocks_v1[(str(owner.get("page_id") or ""), str(block_id))].append(str(owner.get("owner_id") or ""))
    candidate_blocks_v1 = defaultdict(list)
    for candidate in v1_candidates:
        for block_id in candidate.get("member_block_ids") or []:
            candidate_blocks_v1[(str(candidate.get("page_id") or ""), str(block_id))].append({
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "selection_status": str(candidate.get("selection_status") or "UNKNOWN"),
            })
    owner_blocks_recovered = defaultdict(list)
    for owner in recovered_owners:
        for block_id in owner.get("member_block_ids") or []:
            owner_blocks_recovered[(str(owner.get("page_id") or ""), str(block_id))].append(str(owner.get("owner_id") or ""))
    pages = {str(page.get("page_id") or ""): page for page in documents}
    blocks = {
        (str(page.get("page_id") or ""), str(block.get("block_id") or "")): block
        for page in documents for block in page.get("blocks") or []
    }
    rows = []
    seen = set()
    # Trace every row recovered by the generic local operator-surface rule.
    # The current frozen corpus contains the CASE/IS rows, but the trace itself
    # does not decide eligibility from an identity-name allowlist.
    for owner in recovered_owners:
        page_id = str(owner.get("page_id") or "")
        page = pages.get(page_id)
        if not page:
            continue
        for block_id_value in owner.get("member_block_ids") or []:
            block_id = str(block_id_value)
            if (page_id, block_id) in seen:
                continue
            seen.add((page_id, block_id))
            block = blocks.get((page_id, block_id)) or {}
            cells = _cells(block)
            if not cells:
                continue
            identity = _text(cells[0]).upper()
            all_occ = [row for row in v3_occurrences.get(identity, []) if str(row.get("page_id") or "") == page_id and str(row.get("source_block_id") or "") == block_id]
            group = _best_group(v3_by_identity.get(identity, []))
            v1_ids = sorted(set(owner_blocks_v1.get((page_id, block_id), [])))
            v1_candidates_for_block = candidate_blocks_v1.get((page_id, block_id), [])
            recovered_ids = sorted(set(owner_blocks_recovered.get((page_id, block_id), [])))
            html_path = str(page.get("html_path") or "")
            if v1_ids:
                first_loss = "OWNER_SELECTED_IN_V1"
            elif recovered_ids:
                first_loss = "RECOVERED_BY_GENERIC_LOCAL_TABLE_SURFACE"
            else:
                first_loss = "OWNER_SURFACE_STILL_MISSING"
            rows.append({
                "identity": identity,
                "page_id": page_id,
                "source_url": page.get("url"),
                "html_path": html_path,
                "html_path_exists": bool(html_path and Path(html_path).is_file()),
                "documentir_block_present": True,
                "block_id": block_id,
                "block_kind": block.get("block_kind"),
                "table_headers": _headers(block),
                "cells": cells,
                "heading_ancestry": list(block.get("heading_ancestry") or []),
                "v1_candidate_count": len(v1_candidates_for_block),
                "v1_ranked_candidate_ids": sorted({row["candidate_id"] for row in v1_candidates_for_block if row.get("candidate_id")}),
                "v1_ranked_candidate_count": len(v1_candidates_for_block),
                "v1_selected_owner_ids": v1_ids,
                "v1_selected_owner_count": len(v1_ids),
                "generic_recovery_owner_ids": recovered_ids,
                "v3_occurrence_ids": [row.get("occurrence_id") for row in all_occ],
                "v3_target_feature_evidence": [row.get("target_feature_evidence") for row in all_occ],
                "v3_final_type": (group or {}).get("feature_type"),
                "v3_final_status": (group or {}).get("group_status"),
                "first_surface_stage": first_loss,
            })
    return sorted(rows, key=lambda row: (row["identity"], str(row.get("source_url") or ""), row["block_id"]))


def _multi_owner_audit(
    identity: str,
    v2_occurrences: Iterable[Mapping[str, Any]],
    v3_occurrences: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    v2 = sorted({str(row.get("owner_id") or "") for row in v2_occurrences if row.get("owner_id")})
    v3 = sorted({str(row.get("owner_id") or "") for row in v3_occurrences if row.get("owner_id")})
    return {
        "identity": identity,
        "v2_owner_ids": v2,
        "v3_owner_ids": v3,
        "owners_added": sorted(set(v3) - set(v2)),
        "owners_removed": sorted(set(v2) - set(v3)),
        "complementary_owners_preserved": set(v2).issubset(set(v3)),
        "v2_occurrence_count": sum(1 for _ in v2_occurrences),
        "v3_occurrence_count": sum(1 for _ in v3_occurrences),
    }


def _owner_regression(
    *,
    repo: Path,
    documents: list[dict[str, Any]],
    v1_owners: list[dict[str, Any]],
    all_owners: list[dict[str, Any]],
    recovered_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    v1_root = repo / "output" / "mysql_owner_shadow_v1"
    baseline = _read_json(v1_root / "evaluation" / "content_retention_comparison.json", {}) or {}
    rows = _content_units_from_comparison_rows(baseline.get("rows") or [], owner_field="new_owner_ids")
    content = compare_content_units(rows, v1_owners, all_owners)
    contamination = build_contamination_report(rows, v1_owners, all_owners, recovered_candidates)
    examples = build_example_anchor_report(documents, v1_owners, all_owners)
    v1_ids = {str(row.get("owner_id") or "") for row in v1_owners}
    all_ids = {str(row.get("owner_id") or "") for row in all_owners}
    return {
        "v1_selected_owner_count": len(v1_owners),
        "v3_selected_owner_count": len(all_owners),
        "recovered_owner_count": len(all_owners) - len(v1_owners),
        "selected_owner_removed_count": len(v1_ids - all_ids),
        "content_retention": {
            "content_units": content.get("content_units", 0),
            "v1_retained": content.get("old_retained_units", 0),
            "v3_retained": content.get("new_retained_units", 0),
            "v1_ratio": content.get("old_retention_ratio", 0.0),
            "v3_ratio": content.get("new_retention_ratio", 0.0),
            "lost": content.get("lost_units", 0),
        },
        "contamination": {
            "new_owner_contamination_count": contamination.get("new_owner_contamination_count", 0),
            "new_selected_cross_feature_owner_count": contamination.get("new_selected_cross_feature_owner_count", 0),
            "candidate_multi_anchor_count": contamination.get("candidate_multi_anchor_count", 0),
        },
        "examples": {
            "new_example_root_count": examples.get("new_example_root_count", 0),
        },
        "baseline": {
            "v1_retained": baseline.get("new_retained_units", 0),
            "v1_ratio": baseline.get("new_retention_ratio", 0.0),
            "recovered_units": baseline.get("recovered_units", 0),
        },
    }


def _render_json(value: Any, limit: int = 4500) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    if len(text) > limit:
        return text[:limit] + "\n... truncated ..."
    return text


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    comparison = summary.get("comparison") or {}
    v3 = (comparison.get("snapshots") or {}).get("identity_shadow_v3") or {}
    precision = summary.get("precision_audit") or {}
    type_audit = summary.get("type_correction_audit") or {}
    risk = summary.get("old_confirmed_risk_audit") or {}
    case_is = summary.get("case_is") or {}
    regression = summary.get("owner_regression") or {}
    lines = [
        "# MySQL Identity Shadow v3 — Target Evidence Precision", "",
        "## 1. Scope and frozen inputs", "",
        "- Offline replay over the 250 frozen MySQL DocumentIR pages.",
        "- No network, LLM, Semantic, Evidence Bundle, Canonical, Frontend, or production artifact mutation.",
        f"- v1 selected Owners: **{summary.get('input', {}).get('v1_selected_owners', 0)}**; additive recovered Owners: **{summary.get('input', {}).get('recovered_owners', 0)}**.",
        "",
        "## 2. Generic table-role inference", "",
        f"- Roles: `{json.dumps(summary.get('table_roles', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Recovered table-row Owners: **{summary.get('input', {}).get('recovered_owners', 0)}**.",
        "- Role inference uses local headers, row text, heading ancestry, peer shape, and syntax; it does not use a feature inventory or URL blacklist.",
        "",
        "## 3. PROBABLE_FEATURE precision audit", "",
        f"- Audited v2 probable groups: **{precision.get('total', 0)}**.",
        f"- Classifications: `{json.dumps(precision.get('by_classification', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 4. Type correction audit", "",
        f"- Original v2 type corrections: **{type_audit.get('v2_original_count', 0)}**; extended v2→v3 type changes: **{type_audit.get('v2_to_v3_extended_count', 0)}**; total reviewed: **{type_audit.get('total', 0)}**.",
        f"- Classifications: `{json.dumps(type_audit.get('by_classification', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 5. Old confirmed high-risk audit", "",
        f"- Audited rows: **{risk.get('total', 0)}**.",
        f"- Classifications: `{json.dumps(risk.get('by_classification', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 6. CASE / IS surface lineage", "",
        f"- CASE/IS structural rows: **{case_is.get('surface_rows', 0)}**; recovered Owners: **{case_is.get('recovered_owners', 0)}**; v3 target rows: **{case_is.get('target_rows', 0)}**.",
        f"- First-stage counts: `{json.dumps(case_is.get('first_surface_stage', {}), ensure_ascii=False, sort_keys=True)}`",
        "- The recovery is generic: a locally operator-semantic table row is recovered without identity-name allowlisting.",
        "",
        "## 7. Four-way identity comparison", "",
        f"- Old production: `{json.dumps((comparison.get('snapshots') or {}).get('old_production', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Owner shadow v1: `{json.dumps((comparison.get('snapshots') or {}).get('owner_shadow_v1', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Identity shadow v2: `{json.dumps((comparison.get('snapshots') or {}).get('identity_shadow_v2', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Identity shadow v3: `{json.dumps(v3, ensure_ascii=False, sort_keys=True)}`",
        f"- v2→v3 status changes: **{len(comparison.get('status_changes_v2_to_v3') or [])}**; type changes: **{len(comparison.get('type_changes_v2_to_v3') or [])}**.",
        "",
        "## 8. Required high-risk identities", "",
        f"- APT/DEB/DOCKER/RPM/SOURCE v3 statuses: `{json.dumps(summary.get('installation_checks', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- ALNUM/ALPHA/CNTRL v3 statuses: `{json.dumps(summary.get('character_class_checks', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- ABS/BIGINT/INT8/FLOAT4/LIKE/CASE/IS/NAME: `{json.dumps(summary.get('key_feature_checks', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 9. Multi-Owner preservation", "",
        f"- ABS audit: `{json.dumps(summary.get('multi_owner_abs', {}), ensure_ascii=False, sort_keys=True)}`",
        "- Existing v1 Owners are additive-only inputs; no v1 Owner is removed by this shadow replay.",
        "",
        "## 10. Owner regression", "",
        f"- `{json.dumps(regression, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 11. Integrity", "",
        f"- `{json.dumps(summary.get('integrity', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## 12. Decision", "",
        f"**{summary.get('decision', 'CONTINUE_IDENTITY_SHADOW')}**",
        "",
        "Detailed artifacts:",
        "- `output/mysql_identity_shadow_v3/identity/identity_occurrences.jsonl`",
        "- `output/mysql_identity_shadow_v3/identity/identity_groups.jsonl`",
        "- `output/mysql_identity_shadow_v3/audit/`",
        "- `output/mysql_identity_shadow_v3/review/identity_precision_review_pack.md`",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_review_pack(
    path: Path,
    *,
    summary: Mapping[str, Any],
    probable: list[dict[str, Any]],
    type_audit: list[dict[str, Any]],
    old_risk: list[dict[str, Any]],
    case_trace: list[dict[str, Any]],
    datatype_review: list[dict[str, Any]],
    function_review: list[dict[str, Any]],
    operator_review: list[dict[str, Any]],
    uncertain: list[dict[str, Any]],
) -> None:
    lines = [
        "# MySQL Identity Shadow v3 Precision Review Pack", "",
        "This is an offline shadow review. It does not modify production Owner, Identity, Semantic, Canonical, or Frontend data.", "",
        "## 1. All v2 PROBABLE_FEATURE audits", "",
    ]
    for row in probable:
        lines.extend([f"### `{row.get('identity')}` — {row.get('classification')}", "", f"- Reason: {row.get('reason')}", f"- v2: `{row.get('v2_type')}/{row.get('v2_status')}`; v3: `{row.get('v3_type')}/{row.get('v3_status')}`", "", "```json", _render_json(row), "```", ""])
    lines.extend(["## 2. All original v2 type corrections plus extended v2→v3 changes", ""])
    for row in type_audit:
        lines.extend([f"### `{row.get('identity')}` — {row.get('classification')}", "", "```json", _render_json(row), "```", ""])
    lines.extend(["## 3. Old confirmed high-risk", ""])
    for row in old_risk:
        lines.extend([f"### `{row.get('identity')}` — {row.get('classification')}", "", "```json", _render_json(row), "```", ""])
    lines.extend(["## 4. CASE / IS complete lineage", ""])
    for row in case_trace:
        lines.extend([f"### `{row.get('identity')}` — `{row.get('source_url')}`", "", "```json", _render_json(row, limit=7000), "```", ""])
    lines.extend(["## 5. New DATATYPE review", ""])
    for row in datatype_review:
        lines.extend([f"### `{row.get('identity')}`", "", "```json", _render_json(row, limit=6500), "```", ""])
    lines.extend(["## 6. FUNCTION without callable variant", ""])
    for row in function_review:
        lines.extend([f"### `{row.get('identity')}`", "", "```json", _render_json(row, limit=6000), "```", ""])
    lines.extend(["## 7. Word-like OPERATOR review", ""])
    for row in operator_review:
        lines.extend([f"### `{row.get('identity')}`", "", "```json", _render_json(row, limit=6000), "```", ""])
    lines.extend(["## 8. Remaining UNCERTAIN_FEATURE", ""])
    for row in uncertain:
        lines.extend([f"### `{row.get('identity')}`", "", "```json", _render_json(row, limit=6000), "```", ""])
    lines.extend([
        "## 9. Required feature matrix", "",
        "| Identity | v3 status | v3 type |", "|---|---|---|",
    ])
    for identity in ("ABS", "BIGINT", "INT8", "FLOAT4", "LIKE", "CASE", "IS", "APT", "DEB", "DOCKER", "RPM", "SOURCE", "ALNUM", "ALPHA", "CNTRL", "NAME"):
        check = (summary.get("feature_matrix") or {}).get(identity) or {}
        lines.append(f"| `{identity}` | {check.get('status', 'Not present')} | {check.get('type', 'Not present')} |")
    lines.extend([
        "", "## 10. ABS multi-Owner check", "", "```json", _render_json(summary.get("multi_owner_abs", {}), limit=8000), "```", "",
        "## 11. Decision", "", f"**{summary.get('decision', 'CONTINUE_IDENTITY_SHADOW')}**", "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_identity_shadow_v3(
    *,
    repo_root: str | Path,
    output_root: str | Path,
    documents_path: str | Path | None = None,
    selected_owners_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the additive generic precision/recovery replay over frozen MySQL artifacts."""
    repo = Path(repo_root).resolve()
    out = Path(output_root).resolve()
    v1_root = repo / "output" / "mysql_owner_shadow_v1"
    v2_root = repo / "output" / "mysql_identity_shadow_v2"
    prod_root = repo / "output" / "mysql_identity_quality_v3"
    documents_path = Path(documents_path or v1_root / "documentir" / "structural_documents.jsonl")
    selected_owners_path = Path(selected_owners_path or v1_root / "owners" / "ranked_owner_hypotheses.jsonl")
    documents = _read_jsonl(documents_path)
    v1_owners = [row for row in _read_jsonl(selected_owners_path) if str(row.get("selection_status") or "SELECTED") == "SELECTED"]
    v1_candidates = _read_jsonl(v1_root / "candidates" / "owner_candidates.jsonl")
    recovered_candidates, recovered_owners, table_index, table_audit = recover_generic_surface_owners(documents, v1_owners)
    owner_result = _write_owner_shadow_v2(
        repo=repo, output_root=repo / "output" / "mysql_owner_shadow_v2", documents=documents,
        existing_owners=v1_owners, recovered_candidates=recovered_candidates,
        recovered_owners=recovered_owners, table_audit=table_audit,
    )
    all_owners = owner_result["all_owners"]
    pages = {str(row.get("page_id") or ""): row for row in documents}
    blocks = {(str(page.get("page_id") or ""), str(block.get("block_id") or "")): block for page in documents for block in page.get("blocks") or []}
    owner_by_id = {str(row.get("owner_id") or ""): row for row in all_owners}
    cached_occurrence_path = v1_root / "identity" / "occurrences.jsonl"
    occurrences = _read_jsonl(cached_occurrence_path)
    occurrence_source = "FROZEN_OWNER_SHADOW_V1_OCCURRENCES"
    if not occurrences:
        occurrences = build_identity_occurrences(documents, v1_owners)
        occurrence_source = "REBUILT_FROM_SELECTED_SHADOW_OWNERS"
    occurrences, recovered_occurrences = _append_recovered_occurrences(occurrences, documents, recovered_owners)
    enriched = _enrich_occurrences(occurrences, pages=pages, blocks=blocks, owners=owner_by_id, table_index=table_index)
    occurrences_by_identity = _occurrence_index(enriched)
    v3_groups = sorted(
        (_aggregate_group(identity, rows) for identity, rows in occurrences_by_identity.items()),
        key=lambda row: (str(row.get("canonical_identity") or ""), str(row.get("feature_type") or "")),
    )
    v3_by_identity = _group_by_identity(v3_groups)
    v2_groups = _read_jsonl(v2_root / "identity" / "identity_groups.jsonl")
    v2_occurrences = _read_jsonl(v2_root / "identity" / "identity_occurrences.jsonl")
    old_groups = _read_jsonl(prod_root / "identity" / "identity_units.jsonl")
    v1_groups = _read_jsonl(v1_root / "identity" / "groups.jsonl")
    comparison = _four_way_comparison(old_groups, v1_groups, v2_groups, v3_groups)
    v2_comparison = _read_json(v2_root / "identity" / "comparison.json", {}) or {}
    probable = _probable_precision_audit(v2_groups, v3_by_identity, occurrences_by_identity)
    type_audit = _type_correction_audit(v2_comparison, comparison, v3_by_identity, occurrences_by_identity)
    v2_risk = _read_jsonl(v2_root / "audit" / "old_confirmed_risk_audit.jsonl")
    old_risk = _old_confirmed_risk_v3_audit(v2_risk, v3_by_identity, occurrences_by_identity)
    datatype_review = _new_datatype_review(v3_groups, {str(row.get("canonical_identity") or "").upper(): row for row in old_groups}, occurrences_by_identity)
    function_review = _non_callable_function_review(v3_groups, occurrences_by_identity)
    operator_review = _word_operator_review(v3_groups, occurrences_by_identity)
    uncertain = _remaining_uncertain(v3_groups, occurrences_by_identity)
    case_trace = _case_is_surface_trace(documents, v1_candidates, v1_owners, recovered_owners, occurrences_by_identity, v3_by_identity)
    abs_audit = _multi_owner_audit(
        "ABS",
        [row for row in v2_occurrences if _text(row.get("normalized_identity")).upper() == "ABS"],
        occurrences_by_identity.get("ABS", []),
    )
    owner_regression = _owner_regression(repo=repo, documents=documents, v1_owners=v1_owners, all_owners=all_owners, recovered_candidates=recovered_candidates)

    feature_matrix = {}
    for identity in ("ABS", "BIGINT", "INT8", "FLOAT4", "LIKE", "CASE", "IS", "APT", "DEB", "DOCKER", "RPM", "SOURCE", "ALNUM", "ALPHA", "CNTRL", "NAME"):
        group = _best_group(v3_by_identity.get(identity, []))
        feature_matrix[identity] = _group_record(group) if group else {"status": None, "feature_type": None}
    installation_checks = {identity: feature_matrix[identity] for identity in ("APT", "DEB", "DOCKER", "RPM", "SOURCE")}
    character_class_checks = {identity: feature_matrix[identity] for identity in ("ALNUM", "ALPHA", "CNTRL")}
    key_checks = {identity: feature_matrix[identity] for identity in ("ABS", "BIGINT", "INT8", "FLOAT4", "LIKE", "CASE", "IS", "NAME")}
    case_target_rows = sum(bool(row.get("v3_occurrence_ids")) and any(bool((item or {}).get("target_feature_defining")) for item in row.get("v3_target_feature_evidence") or []) for row in case_trace)
    high_risk_false_target_remaining = [
        identity for identity in (*installation_checks, *character_class_checks)
        if (feature_matrix.get(identity) or {}).get("status") not in {None, "NON_FEATURE_ONLY"}
    ]
    case_is_complete = bool(case_trace) and all(row.get("v3_occurrence_ids") and any((item or {}).get("target_feature_defining") for item in row.get("v3_target_feature_evidence") or []) for row in case_trace)
    integrity = {
        "network_fetch": False,
        "llm_called": False,
        "semantic_run": False,
        "canonical_modified": False,
        "frontend_modified": False,
        "production_owner_replaced": False,
        "production_identity_replaced": False,
        "feature_inventory_used": False,
        "db_specific_identity_rule": False,
        "v1_owner_removed": bool(owner_result.get("removed")),
        "abs_complementary_owners_preserved": bool(abs_audit.get("complementary_owners_preserved")),
    }
    decision = "IDENTITY_REFINEMENT_READY" if case_is_complete and not high_risk_false_target_remaining and not integrity["v1_owner_removed"] else "CONTINUE_IDENTITY_SHADOW"
    summary = {
        "schema_version": SCHEMA_VERSION,
        "input": {
            "documents": len(documents),
            "v1_selected_owners": len(v1_owners),
            "recovered_owners": len(recovered_owners),
            "occurrences_before_recovery": len(occurrences) - len(recovered_occurrences),
            "recovered_occurrences": len(recovered_occurrences),
            "occurrences": len(enriched),
            "occurrence_source": occurrence_source,
            "documents_path": str(documents_path),
            "v1_owner_path": str(selected_owners_path),
        },
        "table_roles": dict(sorted(Counter(str(row.get("table_role") or "UNKNOWN_TABLE") for row in table_audit).items())),
        "precision_audit": {"total": len(probable), "by_classification": dict(sorted(Counter(row.get("classification") for row in probable).items()))},
        "type_correction_audit": {
            "total": len(type_audit),
            "v2_original_count": sum(row.get("audit_scope") == "V2_ORIGINAL" for row in type_audit),
            "v2_to_v3_extended_count": sum(row.get("audit_scope") == "V2_TO_V3_EXTENDED" for row in type_audit),
            "by_classification": dict(sorted(Counter(row.get("classification") for row in type_audit).items())),
        },
        "old_confirmed_risk_audit": {"total": len(old_risk), "by_classification": dict(sorted(Counter(row.get("classification") for row in old_risk).items()))},
        "case_is": {
            "surface_rows": len(case_trace),
            "recovered_owners": sum(1 for row in case_trace if row.get("generic_recovery_owner_ids")),
            "target_rows": case_target_rows,
            "first_surface_stage": dict(sorted(Counter(row.get("first_surface_stage") for row in case_trace).items())),
            "trace": case_trace,
        },
        "installation_checks": installation_checks,
        "character_class_checks": character_class_checks,
        "key_feature_checks": key_checks,
        "feature_matrix": feature_matrix,
        "multi_owner_abs": abs_audit,
        "owner_regression": owner_regression,
        "remaining_uncertain": {"count": len(uncertain)},
        "comparison": comparison,
        "integrity": integrity,
        "decision": decision,
    }
    audit_root = out / "audit"
    identity_root = out / "identity"
    review_root = out / "review"
    runtime_root = out / "runtime"
    _write_jsonl(identity_root / "identity_occurrences.jsonl", enriched)
    _write_jsonl(identity_root / "identity_groups.jsonl", v3_groups)
    _write_jsonl(identity_root / "candidates.jsonl", enriched)
    _write_jsonl(identity_root / "validated_candidates.jsonl", [row for row in enriched if row.get("target_feature_evidence", {}).get("target_feature_defining")])
    _write_jsonl(identity_root / "rejected_candidates.jsonl", [row for row in enriched if not row.get("target_feature_evidence", {}).get("target_feature_defining")])
    _write_json(identity_root / "comparison.json", comparison)
    _write_jsonl(audit_root / "probable_precision_audit.jsonl", probable)
    _write_jsonl(audit_root / "type_correction_audit.jsonl", type_audit)
    _write_jsonl(audit_root / "old_confirmed_risk_audit.jsonl", old_risk)
    _write_jsonl(audit_root / "table_role_audit.jsonl", table_audit)
    _write_jsonl(audit_root / "case_is_surface_trace.jsonl", case_trace)
    _write_jsonl(audit_root / "new_datatype_review.jsonl", datatype_review)
    _write_jsonl(audit_root / "non_callable_function_review.jsonl", function_review)
    _write_jsonl(audit_root / "word_operator_review.jsonl", operator_review)
    _write_jsonl(audit_root / "remaining_uncertain.jsonl", uncertain)
    _write_jsonl(audit_root / "multi_owner_audit.jsonl", [abs_audit])
    _write_json(audit_root / "owner_regression.json", owner_regression)
    _write_json(audit_root / "target_evidence_summary.json", {
        "occurrences": len(enriched),
        "target_feature_defining_occurrences": sum(bool(row.get("target_feature_defining")) for row in enriched),
        "by_target_type": dict(sorted(Counter(value for row in enriched for value in row.get("target_feature_evidence", {}).get("target_types", [])).items())),
        "by_strength": dict(sorted(Counter(row.get("target_feature_evidence", {}).get("strength", "NONE") for row in enriched).items())),
    })
    _write_json(runtime_root / "run_manifest.json", {
        "schema_version": "mysql-identity-shadow-run/v3",
        "pipeline_id": SCHEMA_VERSION,
        "run_id": _now_id("mysql_identity_shadow_v3"),
        "input_documents": str(documents_path),
        "input_owner_shadow_v1": str(selected_owners_path),
        "input_identity_shadow_v2": str(v2_root),
        "occurrence_source": occurrence_source,
        "recovered_owner_count": len(recovered_owners),
        "recovered_occurrence_count": len(recovered_occurrences),
        "scope": "TARGET_EVIDENCE_PRECISION_AND_GENERIC_OPERATOR_SURFACE_RECOVERY",
        "integrity": integrity,
    })
    _write_json(out / "summary.json", summary)
    _write_report(repo / "reports" / "mysql_identity_shadow_v3.md", summary)
    _write_review_pack(
        review_root / "identity_precision_review_pack.md", summary=summary, probable=probable,
        type_audit=type_audit, old_risk=old_risk, case_trace=case_trace,
        datatype_review=datatype_review, function_review=function_review,
        operator_review=operator_review, uncertain=uncertain,
    )
    return summary
