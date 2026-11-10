from __future__ import annotations

from agents.identity.target_evidence_precision import (
    _multi_owner_audit,
    _precision_classify,
)
from agents.owner.generic_surface_recovery import (
    build_table_role_index,
    recover_generic_surface_owners,
)


def _document(headers: list[str], rows: list[list[str]], *, heading: str) -> dict:
    page_id = "page:fixture"
    table_id = "structural_container:fixture-table"
    blocks = []
    for index, cells in enumerate(rows):
        block_id = f"block:row-{index}"
        blocks.append({
            "block_id": block_id,
            "block_kind": "TABLE_ROW",
            "text": " ".join(cells),
            "heading_ancestry": [heading],
            "ancestor_chain": [
                {"id": f"boundary:row-{index}", "kind": "TABLE_ROW"},
                {"id": table_id, "kind": "GENERIC_BLOCK_CONTAINER"},
            ],
            "structural_parent_id": f"boundary:row-{index}",
            "structural_parent_kind": "TABLE_ROW",
            "table_metadata": {"headers": headers, "cells": cells, "cell_items": [[value] for value in cells]},
        })
    return {
        "page_id": page_id,
        "document_id": "document:fixture",
        "dbms": "fixture-db",
        "url": "https://example.test/reference.html",
        "page_hash": "fixture-hash",
        "html_path": "fixture.html",
        "blocks": blocks,
    }


def _occurrence(identity: str, block: dict) -> dict:
    return {
        "occurrence_id": f"occ:{identity}:{block['block_id']}",
        "raw_identity": identity,
        "normalized_identity": identity.upper(),
        "owner_id": "owner:fixture",
        "page_id": "page:fixture",
        "source_url": "https://example.test/reference.html",
        "source_block_id": block["block_id"],
        "source_type": "table_row",
        "raw_evidence": block["text"],
        "occurrence_role": "DEFINING_IDENTITY",
        "defining": True,
        "surface_role": "FEATURE_CATALOG",
        "source_context": {"block_kind": "TABLE_ROW", "owner_kind": "TableRowOwner"},
    }


def _classify(doc: dict, index, identity: str, row_index: int, *, raw: str | None = None):
    block = doc["blocks"][row_index]
    occurrence = _occurrence(raw or identity, block)
    occurrence["normalized_identity"] = identity.upper()
    return _precision_classify(
        occurrence,
        block=block,
        owner={"owner_id": "owner:fixture", "candidate_kind": "TABLE_ROW_OWNER"},
        page=doc,
        table_index=index,
    )


def test_installation_option_table_is_not_datatype():
    doc = _document(["Method", "Action"], [["APT", "Enable the repository"], ["RPM", "Download a package"]], heading="Installation Methods")
    index, _ = build_table_role_index([doc])
    candidates, owners, _, _ = recover_generic_surface_owners([doc], [])
    assert not candidates
    assert not owners
    assert index[("page:fixture", "structural_container:fixture-table")]["table_role"] == "INSTALLATION_OPTIONS"
    evidence = _classify(doc, index, "APT", 0)
    assert evidence["target_types"] == []
    assert evidence["target_feature_defining"] is False


def test_character_class_table_is_not_function():
    doc = _document(["Character Class Name", "Meaning"], [["ALNUM", "Alphanumeric characters"], ["ALPHA", "Alphabetic characters"]], heading="Regular Expression Syntax")
    index, _ = build_table_role_index([doc])
    candidates, owners, _, _ = recover_generic_surface_owners([doc], [])
    assert not candidates
    assert not owners
    evidence = _classify(doc, index, "ALNUM", 0)
    assert evidence["target_types"] == []


def test_datatype_alias_mapping_is_datatype_not_operator():
    doc = _document(["Other Vendor Type", "MySQL Type"], [["INT8", "BIGINT"], ["FLOAT4", "FLOAT"]], heading="Other Vendor Data Types")
    index, _ = build_table_role_index([doc])
    evidence = _classify(doc, index, "INT8", 0)
    assert evidence["target_types"] == ["DATATYPE"]
    assert evidence["target_feature_defining"] is True


def test_callable_function_table_is_function_only():
    doc = _document(["Name", "Description"], [["ABS()", "Return absolute value"], ["ACOS()", "Return arc cosine"]], heading="Mathematical Functions")
    index, _ = build_table_role_index([doc])
    evidence = _classify(doc, index, "ABS", 0, raw="ABS()")
    assert evidence["target_types"] == ["FUNCTION"]
    assert evidence["strength"] == "STRONG"


def test_operator_table_recovers_word_operator_without_identity_allowlist():
    doc = _document(["Name", "Description"], [["CASE", "Case operator"], ["IS", "Test a value against a boolean"]], heading="Comparison Operators")
    index, _ = build_table_role_index([doc])
    candidates, owners, _, _ = recover_generic_surface_owners([doc], [])
    assert {row["primary_identity"] for row in candidates} == {"CASE", "IS"}
    assert len(owners) == 2
    assert _classify(doc, index, "CASE", 0)["target_types"] == ["OPERATOR"]
    assert _classify(doc, index, "IS", 1)["target_types"] == ["OPERATOR"]


def test_page_role_does_not_inherit_into_local_character_table():
    doc = _document(["Character Class Name", "Meaning"], [["DIGIT", "Digits"]], heading="Regular Expression Functions and Operators")
    index, _ = build_table_role_index([doc])
    evidence = _classify(doc, index, "DIGIT", 0)
    assert evidence["target_types"] == []
    assert evidence["precision_context"]["table_role"] == "CHARACTER_CLASS_TABLE"


def test_complementary_multi_owner_evidence_is_preserved():
    result = _multi_owner_audit(
        "ABS",
        [{"owner_id": "owner:table"}, {"owner_id": "owner:detail"}],
        [{"owner_id": "owner:table"}, {"owner_id": "owner:detail"}, {"owner_id": "owner:recovered"}],
    )
    assert result["complementary_owners_preserved"] is True
    assert result["owners_removed"] == []
    assert result["owners_added"] == ["owner:recovered"]
