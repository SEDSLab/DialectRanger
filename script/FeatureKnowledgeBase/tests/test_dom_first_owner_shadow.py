from __future__ import annotations

from pathlib import Path

from agents.document_structure_v2.containment import build_structural_document
from agents.owner.dom_first_shadow import (
    extract_feature_anchors,
    generate_owner_candidates,
    rank_owner_candidates,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "owner_architecture"
PROFILE = {"main_selectors": ["main", "body"], "noise_selectors": []}


def _document(name: str) -> dict:
    path = FIXTURES / name
    return build_structural_document(
        {"url": f"https://example.test/{name}", "html_path": str(path), "feature_context": [], "crawl_depth": 0},
        dbms="fixture-db", parser_profile=PROFILE,
    )


def test_table_header_is_not_a_feature_anchor_and_rows_are_independent():
    document = _document("table_row.html")
    anchors, shaped = extract_feature_anchors(document)
    identities = {row["identity"] for row in anchors}
    assert "FUNCTION" not in identities
    assert {"ABS", "ACOS"}.issubset(identities)
    candidates, _ = generate_owner_candidates(document, {})
    ranked, selected = rank_owner_candidates(candidates)
    row_candidates = [row for row in selected if row["owner_kind"] == "TABLE_ROW_OWNER"]
    assert {row["primary_identity"] for row in row_candidates} == {"ABS", "ACOS"}
    assert all(len(row["member_block_ids"]) == 1 for row in row_candidates)


def test_example_invocation_is_shaped_token_not_peer_anchor():
    document = _document("example_conflict.html")
    anchors, shaped = extract_feature_anchors(document)
    example_blocks = {
        row["block_id"] for row in document["blocks"] if "SELECT ABS(-32)" in row["text"]
    }
    assert example_blocks
    assert not any(row["block_id"] in example_blocks and row["identity"] == "ABS" for row in anchors)
    assert any(row["block_id"] in example_blocks and row["identity"] == "ABS" and row["role"] == "EXAMPLE_TOKEN" for row in shaped)


def test_list_owner_keeps_description_and_example_inside_one_structural_unit():
    document = _document("single_feature.html")
    # The single-feature fixture is intentionally a page-level fallback.
    candidates, _ = generate_owner_candidates(document, {})
    _, selected = rank_owner_candidates(candidates)
    assert selected
    page = next(row for row in selected if row["owner_kind"] == "PAGE_OWNER")
    texts = {block["text"] for block in document["blocks"] if block["block_id"] in page["member_block_ids"]}
    assert any("Returns" in text for text in texts)


def test_definition_entries_have_explicit_structural_parents():
    document = _document("definition_list.html")
    kinds = {str(block.get("structural_parent_kind")) for block in document["blocks"]}
    assert "DEFINITION_ENTRY" in kinds
    candidates, _ = generate_owner_candidates(document, {})
    _, selected = rank_owner_candidates(candidates)
    definitions = [row for row in selected if row["owner_kind"] == "DEFINITION_LIST_OWNER"]
    assert {row["primary_identity"] for row in definitions} == {"INTEGER", "TEXT", "NUMERIC"}
    assert all(len(row["member_block_ids"]) == 2 for row in definitions)


def test_table_mapping_labels_are_subject_evidence_without_becoming_peer_anchors():
    document = _document("table_row.html")
    row = next(block for block in document["blocks"] if block["block_kind"] == "TABLE_ROW")
    row["table_metadata"] = {
        "cells": ["INT8", "BIGINT"],
        "headers": ["Other Vendor Type", "MySQL Type"],
        "cell_items": [["INT8"], ["BIGINT"]],
    }
    row["text"] = "INT8 BIGINT"
    candidates, _ = generate_owner_candidates(document, {})
    _, selected = rank_owner_candidates(candidates)
    owner = next(item for item in selected if item["primary_identity"] == "INT8")
    assert owner["identity_values"] == ["BIGINT", "INT8"]
    assert owner["foreign_identity_values"] == []
    assert len(owner["identity_anchor_ids"]) == 1


def test_bounded_multi_signature_label_emits_multiple_anchors_in_one_region():
    document = _document("table_row.html")
    row = next(block for block in document["blocks"] if block["block_kind"] == "TABLE_ROW")
    row["table_metadata"]["cells"] = ["ATAN2() , ATAN()", "Returns an arc tangent"]
    row["table_metadata"]["headers"] = ["Name", "Description"]
    row["text"] = "ATAN2() , ATAN() Returns an arc tangent"
    candidates, _ = generate_owner_candidates(document, {})
    _, selected = rank_owner_candidates(candidates)
    owner = next(item for item in selected if {"ATAN", "ATAN2"}.issubset(set(item["identity_values"])))
    assert {"ATAN", "ATAN2"}.issubset(set(owner["identity_values"]))
    assert len(owner["identity_anchor_ids"]) == 2
