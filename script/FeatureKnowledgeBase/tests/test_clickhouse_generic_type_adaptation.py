from __future__ import annotations

from agents.type.adaptation import build_adapted_typed_feature_evidence


def _context(subject: str, text: str, *, kind: str = "", block_type: str = "text", headers=None, headings=None):
    return {
        "identity_unit_id": "identity_unit:test",
        "canonical_subject": subject,
        "owner_id": "owner:test",
        "owner_kind": "STRUCTURAL_OWNER",
        "dbms": "synthetic",
        "source_url": "https://example.invalid/reference",
        "html_path": "synthetic.html",
        "page_id": "page:test",
        "raw_text": text,
        "heading_ancestry": list(headings or []),
        "table_headers": list(headers or []),
        "adapted_identity_kind": kind,
        "owner_local_binding": True,
        "blocks": [{
            "block_id": "block:test",
            "block_type": block_type,
            "text": text,
            "cells": [text],
            "table_headers": list(headers or []),
            "heading_ancestry": list(headings or []),
        }],
    }


def _adapted(rows, candidate_type):
    return [
        row for row in rows
        if row.get("type_adaptation") and row.get("candidate_type") == candidate_type
    ]


def test_keyword_infix_operator_surface():
    rows = build_adapted_typed_feature_evidence(
        _context("MATCHES", "expr MATCHES pattern", kind="OPERATOR", headings=["Predicates"])
    )
    assert _adapted(rows, "OPERATOR")
    assert any(row.get("structural_role") == "KEYWORD_INFIX" for row in _adapted(rows, "OPERATOR"))


def test_multi_token_infix_operator_surface():
    rows = build_adapted_typed_feature_evidence(
        _context("NOT MATCHES", "expr NOT MATCHES pattern", kind="OPERATOR", headings=["Predicates"])
    )
    assert any(row.get("structural_role") == "MULTI_TOKEN_INFIX" for row in _adapted(rows, "OPERATOR"))


def test_postfix_predicate_operator_surface():
    rows = build_adapted_typed_feature_evidence(
        _context("IS EMPTY", "expr IS EMPTY returns true", kind="OPERATOR", headings=["Predicates"])
    )
    assert any(row.get("structural_role") == "POSTFIX_PREDICATE" for row in _adapted(rows, "OPERATOR"))


def test_ternary_predicate_operator_surface():
    rows = build_adapted_typed_feature_evidence(
        _context("BETWEEN", "expr BETWEEN low AND high", kind="OPERATOR", headings=["Predicates"])
    )
    assert any(row.get("structural_role") == "TERNARY_PREDICATE" for row in _adapted(rows, "OPERATOR"))


def test_parameterized_and_nested_datatype_positions():
    parameterized = build_adapted_typed_feature_evidence(
        _context("TypeName", "column x TypeName(10, 2)", kind="DATATYPE", headers=["Type", "Description"], headings=["Data types"])
    )
    nested = build_adapted_typed_feature_evidence(
        _context("OuterType", "column x OuterType(InnerType)", kind="DATATYPE", headers=["Type"], headings=["Declarations"])
    )
    assert _adapted(parameterized, "DATATYPE")
    assert _adapted(nested, "DATATYPE")


def test_expression_call_is_not_parameterized_datatype():
    rows = build_adapted_typed_feature_evidence(
        _context("TypeName", "SELECT TypeName(x)", headings=["Functions"])
    )
    assert not _adapted(rows, "DATATYPE")
    assert any(row.get("candidate_type") == "FUNCTION" for row in rows)


def test_same_surface_keeps_owner_local_type_contexts_separate():
    datatype_rows = build_adapted_typed_feature_evidence(
        _context("Surface", "column x Surface(10)", kind="DATATYPE", headers=["Type"], headings=["Declarations"])
    )
    function_rows = build_adapted_typed_feature_evidence(
        _context("Surface", "SELECT Surface(x)", headings=["Functions"])
    )
    assert _adapted(datatype_rows, "DATATYPE")
    assert not _adapted(function_rows, "DATATYPE")


def test_statement_and_prose_tokens_are_not_operator_surfaces():
    statement_rows = build_adapted_typed_feature_evidence(
        _context("SELECT", "SELECT x FROM table WHERE condition", kind="OPERATOR", headings=["Operators"])
    )
    prose_rows = build_adapted_typed_feature_evidence(
        _context("JSON", "Comparison between values of the JSON type", kind="OPERATOR", headings=["Comparison"])
    )
    assert not _adapted(statement_rows, "OPERATOR")
    assert not _adapted(prose_rows, "OPERATOR")


def test_unparameterized_prose_declaration_is_not_a_datatype():
    rows = build_adapted_typed_feature_evidence(
        _context("BCP", "name or BCP", kind="DATATYPE", headings=["Configuration"])
    )
    assert not _adapted(rows, "DATATYPE")
