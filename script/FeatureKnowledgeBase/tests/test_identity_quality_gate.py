from __future__ import annotations

from agents.identity.production import reconcile_identity_groups
from agents.identity.quality import assess_surface, classify_occurrence_role


def _candidate(identity: str, candidate_id: str, records: list[dict], type_hint: str = "UNCERTAIN") -> dict:
    return {
        "candidate_id": candidate_id,
        "normalized_identity": identity,
        "raw_identity": " | ".join(record["raw_identity"] for record in records),
        "owner_id": f"owner:{candidate_id}",
        "page_id": f"page:{candidate_id}",
        "dbms": "testdb",
        "source_url": f"https://example.test/{candidate_id}",
        "page_hash": candidate_id,
        "html_path": f"/tmp/{candidate_id}.html",
        "type_hint": type_hint,
        "source_block_ids": [f"block:{candidate_id}"],
        "validation_status": "VALIDATED",
        "occurrence_records": records,
        "type_evidence": [item for record in records for item in record.get("type_evidence", [])],
    }


def _record(occurrence_id: str, raw: str, role: str, evidence: list[dict] | None = None) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "raw_identity": raw,
        "occurrence_role": role,
        "defining": role in {"DEFINING_IDENTITY", "FORMAL_VARIANT"},
        "source_type": "table_row" if role == "DEFINING_IDENTITY" else "inline_code",
        "source_block_id": f"block:{occurrence_id}",
        "source_url": f"https://example.test/{occurrence_id}",
        "raw_evidence": raw,
        "surface_role": "FEATURE_CATALOG" if role == "DEFINING_IDENTITY" else "SUPPORTING_CONTEXT",
        "surface_confidence": 0.9,
        "type_evidence": evidence or [],
    }


def test_surface_quality_distinguishes_catalog_from_configuration_table():
    catalog = assess_surface({
        "block_kind": "TABLE_ROW",
        "text": "ABS(X) Returns the absolute value",
        "table_metadata": {"headers": ["Function", "Description"], "cells": ["ABS(X)", "Returns the absolute value"]},
    }, page={"feature_context": ["function"]})
    config = assess_surface({
        "block_kind": "TABLE_ROW",
        "text": "max_connections 151",
        "table_metadata": {"headers": ["Variable", "Meaning", "Default"], "cells": ["max_connections", "Maximum connections", "151"]},
    }, page={"feature_context": []})
    assert catalog["surface_role"] == "FEATURE_CATALOG"
    assert catalog["surface_confidence"] >= 0.8
    assert config["surface_role"] == "SUPPORTING_CONTEXT"


def test_non_defining_roles_are_retained_and_not_promoted():
    block = {
        "block_kind": "CODE",
        "text": "SELECT ABS(2)",
        "heading_ancestry": ["Examples"],
        "code_metadata": {},
    }
    surface = {"surface_role": "SUPPORTING_CONTEXT", "surface_confidence": 0.2}
    assert classify_occurrence_role(
        raw_identity="ABS(2)", source_type="inline_code", block=block, surface=surface,
    ) == "EXAMPLE_TOKEN"
    parameter_block = {
        "block_kind": "TABLE_ROW",
        "text": "expr expression to evaluate",
        "table_metadata": {"headers": ["Parameter", "Description"], "cells": ["expr", "expression to evaluate"]},
    }
    assert classify_occurrence_role(
        raw_identity="expr", source_type="table_row", block=parameter_block, surface=surface,
    ) == "PARAMETER_OR_FIELD"


def test_uncertain_occurrence_merges_into_strong_function_group():
    units, audit = reconcile_identity_groups([
        _candidate("ABS", "def", [_record(
            "abs-def", "ABS(X)", "DEFINING_IDENTITY",
            [{"type": "FUNCTION", "strength": "STRONG", "reason": "callable_signature", "source": "raw_identity"}],
        )], "FUNCTION"),
        _candidate("ABS", "mention", [_record("abs-example", "ABS(2)", "EXAMPLE_TOKEN")], "UNCERTAIN"),
    ])
    assert len(units) == 1
    assert units[0]["group_key"] == "ABS|FUNCTION"
    assert units[0]["group_status"] == "CONFIRMED_FEATURE"
    assert units[0]["occurrence_roles"]["EXAMPLE_TOKEN"] == 1
    assert audit["same_normalized_identity_multi_group_count"] == 0


def test_true_strong_type_conflict_remains_split():
    units, audit = reconcile_identity_groups([
        _candidate("DATE", "datatype", [_record(
            "date-type", "DATE", "DEFINING_IDENTITY",
            [{"type": "DATATYPE", "strength": "STRONG", "reason": "type_declaration_context", "source": "header"}],
        )], "DATATYPE"),
        _candidate("DATE", "function", [_record(
            "date-fn", "DATE(expr)", "DEFINING_IDENTITY",
            [{"type": "FUNCTION", "strength": "STRONG", "reason": "callable_signature", "source": "raw_identity"}],
        )], "FUNCTION"),
    ])
    assert {unit["group_key"] for unit in units} == {"DATE|DATATYPE", "DATE|FUNCTION"}
    assert audit["same_normalized_identity_multi_group_count"] == 1
    assert len(audit["cross_type_conflicts"]) == 1


def test_mention_only_group_is_non_feature_only():
    units, _ = reconcile_identity_groups([
        _candidate("LONG", "mention", [_record("long-mention", "LONG", "MENTION")]),
    ])
    assert units[0]["group_status"] == "NON_FEATURE_ONLY"
    assert units[0]["semantic_ready"] is False


def test_defining_but_untyped_identity_is_retained_as_uncertain():
    units, _ = reconcile_identity_groups([
        _candidate("CUSTOM", "unknown", [_record("custom-def", "CUSTOM", "DEFINING_IDENTITY")]),
    ])
    assert units[0]["feature_type"] == "UNCERTAIN"
    assert units[0]["group_status"] == "UNCERTAIN_FEATURE"
    assert units[0]["semantic_ready"] is True
