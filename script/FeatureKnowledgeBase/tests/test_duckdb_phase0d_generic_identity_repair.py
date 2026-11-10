"""Synthetic generic structural-role tests for Phase 0D.

The fixtures intentionally use neutral feature names.  They do not encode a
database, URL selector, or production inventory.
"""
from __future__ import annotations

from agents.identity.production import run_identity_owner_path


def _block(block_id: str, order: int, kind: str, text: str, *, headers=None, cells=None, items=None, ancestry=None):
    metadata = None
    if headers is not None or cells is not None:
        metadata = {
            "headers": list(headers or []),
            "cells": list(cells or []),
            "cell_items": list(items if items is not None else [[value] for value in (cells or [])]),
        }
    return {
        "block_id": block_id,
        "block_kind": kind,
        "text": text,
        "order": order,
        "heading_ancestry": list(ancestry or []),
        "table_metadata": metadata,
        "code_metadata": None,
    }


def _document():
    blocks = [
        _block("h-name", 0, "TABLE_ROW", "Name Aliases Description", headers=["Name", "Aliases", "Description"], cells=["Name", "Aliases", "Description"]),
        _block("r-type", 1, "TABLE_ROW", "INTEGER INT, INT4 integer type", headers=["Name", "Aliases", "Description"], cells=["INTEGER", "INT, INT4", "integer type"], items=[["INTEGER"], ["INT, INT4"], ["integer type"]]),
        _block("h-signature", 2, "TABLE_ROW", "Signature Description", headers=["Signature", "Description"], cells=["Signature", "Description"]),
        _block("r-functions", 3, "TABLE_ROW", "alpha(x) beta(x, y) callable", headers=["Signature", "Description"], cells=["alpha(x), beta(x, y)", "callable"], items=[["alpha(x), beta(x, y)"], ["callable"]]),
        _block("h-main", 4, "HEADING", "main(x)"),
        _block("r-alias", 5, "TABLE_ROW", "Aliases alt_main, alt2", cells=["Aliases", "alt_main, alt2"], items=[["Aliases"], ["alt_main, alt2"]], ancestry=["main(x)"]),
        _block("r-parameter", 6, "TABLE_ROW", "Parameter Description", headers=["Parameter", "Description"], cells=["x", "input value"], items=[["x"], ["input value"]]),
        _block("r-example", 7, "TABLE_ROW", "Example Result", headers=["Example", "Result"], cells=["main(1)", "2"], items=[["main(1)"], ["2"]]),
    ]
    return {
        "schema_version": "DocumentIR/v2",
        "dbms": "synthetic",
        "page_id": "page:generic",
        "document_id": "document:generic",
        "url": "https://docs.example.test/reference",
        "canonical_url": "https://docs.example.test/reference",
        "page_hash": "page-hash",
        "html_path": "frozen/reference.html",
        "title": "Function and Data Type Reference",
        "feature_context": ["function", "datatype"],
        "blocks": blocks,
    }


def _occurrences(result):
    return result["occurrences"]


def test_table_roles_emit_canonical_and_alias_without_description_noise():
    result = run_identity_owner_path([_document()])
    rows = _occurrences(result)
    assert {row["normalized_identity"] for row in rows if row["structural_role"] == "FEATURE_NAME"} >= {"INTEGER"}
    aliases = [row for row in rows if row["structural_role"] == "ALIAS"]
    assert {row["normalized_identity"] for row in aliases} >= {"INT", "INT4", "ALT_MAIN", "ALT2"}
    assert all(row["relation_type"] == "ALIAS_OF" for row in aliases)
    assert all(row["alias_of_normalized"] for row in aliases)
    assert all(row["alias_of_owner_id"] for row in aliases)
    assert all(row["alias_of_block_id"] for row in aliases)
    assert not {row["normalized_identity"] for row in rows} & {"DESCRIPTION", "INPUT", "VALUE", "RESULT"}


def test_signature_cell_splits_multiple_callables_and_keeps_raw_variant():
    result = run_identity_owner_path([_document()])
    rows = _occurrences(result)
    signatures = [row for row in rows if row["structural_role"] == "SIGNATURE"]
    assert {row["normalized_identity"] for row in signatures} >= {"ALPHA", "BETA"}
    assert {row["raw_identity"] for row in signatures} >= {"alpha(x)", "beta(x, y)"}


def test_parameter_and_example_columns_do_not_become_identities():
    result = run_identity_owner_path([_document()])
    normalized = {row["normalized_identity"] for row in _occurrences(result)}
    assert "X" not in normalized
    assert "2" not in normalized


def test_uncertain_identity_remains_in_ledger_units():
    document = _document()
    document["feature_context"] = []
    document["title"] = "Reference"
    result = run_identity_owner_path([document])
    assert any(unit.get("canonical_identity") == "ALPHA" for unit in result["units"])
