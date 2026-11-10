from __future__ import annotations

from agents.type.decision import decide_type
from agents.type.evidence import build_typed_feature_evidence


def _type(context):
    evidence = build_typed_feature_evidence(context)
    return evidence, decide_type(context, evidence)


def test_operator_table_row_uses_owner_local_operation_surface():
    evidence, decision = _type({
        "identity_unit_id": "unit:plus",
        "canonical_subject": "+",
        "owner_id": "owner:operators",
        "source_url": "https://docs.example/operators",
        "blocks": [{
            "block_id": "block:plus",
            "block_type": "table_row",
            "cells": ["x + y", "adds two values"],
            "table_headers": ["Operation", "Description"],
            "heading_ancestry": ["Mathematical operators"],
        }],
    })

    assert {row["candidate_type"] for row in evidence} == {"OPERATOR"}
    assert decision["primary_type"] == "OPERATOR"
    assert decision["type_status"] == "CONFIRMED"


def test_symbolic_operator_heading_is_not_treated_as_prose():
    evidence, decision = _type({
        "identity_unit_id": "unit:arrow",
        "canonical_subject": "->",
        "owner_id": "owner:operators",
        "source_url": "https://docs.example/operators",
        "blocks": [{
            "block_id": "heading:arrow",
            "block_type": "heading",
            "text": "-> operator",
            "heading_ancestry": ["Operators"],
        }],
    })

    assert any(row["candidate_type"] == "OPERATOR" for row in evidence)
    assert decision["primary_type"] == "OPERATOR"


def test_datatype_heading_and_mapping_surfaces_are_generic():
    for subject, block in [
        (
            "VARCHAR",
            {
                "block_id": "heading:varchar",
                "block_type": "heading",
                "text": "VARCHAR",
                "heading_ancestry": ["Data types", "VARCHAR"],
            },
        ),
        (
            "TIMESTAMP",
            {
                "block_id": "row:timestamp",
                "block_type": "table_row",
                "cells": ["timestamp", "TIMESTAMP"],
                "table_headers": ["Source type", "Target type"],
                "heading_ancestry": ["Type mapping"],
            },
        ),
    ]:
        evidence, decision = _type({
            "identity_unit_id": f"unit:{subject.lower()}",
            "canonical_subject": subject,
            "owner_id": "owner:types",
            "source_url": "https://docs.example/types",
            "blocks": [block],
        })
        assert any(row["candidate_type"] == "DATATYPE" for row in evidence)
        assert decision["primary_type"] == "DATATYPE"
        assert decision["type_status"] == "CONFIRMED"


def test_callable_signature_remains_function():
    evidence, decision = _type({
        "identity_unit_id": "unit:abs",
        "canonical_subject": "ABS",
        "owner_id": "owner:functions",
        "source_url": "https://docs.example/functions",
        "signature_variants": ["ABS(X)"],
        "blocks": [{
            "block_id": "signature:abs",
            "block_type": "signature",
            "text": "ABS(X)",
            "heading_ancestry": ["Mathematical functions"],
        }],
    })

    assert {row["candidate_type"] for row in evidence} == {"FUNCTION"}
    assert decision["primary_type"] == "FUNCTION"
    assert decision["type_status"] == "CONFIRMED"


def test_mixed_parent_heading_preserves_specific_function_table_context():
    evidence, decision = _type({
        "identity_unit_id": "unit:day-of-month",
        "canonical_subject": "DAY_OF_MONTH",
        "owner_id": "owner:datetime-functions",
        "source_url": "https://docs.example/functions/datetime",
        "blocks": [{
            "block_id": "row:day-of-month",
            "block_type": "table_row",
            "cells": ["DAY_OF_MONTH", "day()"],
            "table_headers": ["Field", "Description"],
            "heading_ancestry": [
                "Date and time functions and operators",
                "Extraction function",
            ],
        }],
    })

    assert any(row["candidate_type"] == "FUNCTION" for row in evidence)
    assert not any(row["candidate_type"] == "OPERATOR" for row in evidence)
    assert decision["primary_type"] == "FUNCTION"
    assert decision["type_status"] == "CONFIRMED"


def test_datatype_word_in_type_constructor_prose_does_not_become_operator():
    evidence, decision = _type({
        "identity_unit_id": "unit:array",
        "canonical_subject": "ARRAY",
        "owner_id": "owner:types",
        "source_url": "https://docs.example/types",
        "blocks": [{
            "block_id": "prose:array",
            "block_type": "text",
            "text": "ARRAY is a data type constructor.",
            "heading_ancestry": ["Data types"],
        }],
    })

    assert {row["candidate_type"] for row in evidence} == {"DATATYPE"}
    assert decision["primary_type"] == "DATATYPE"


def test_same_owner_function_and_datatype_surfaces_require_review():
    evidence, decision = _type({
        "identity_unit_id": "unit:json",
        "canonical_subject": "JSON",
        "owner_id": "owner:mixed",
        "source_url": "https://docs.example/mixed",
        "blocks": [
            {
                "block_id": "signature:json",
                "block_type": "signature",
                "text": "JSON(X)",
                "heading_ancestry": ["Functions"],
            },
            {
                "block_id": "heading:json",
                "block_type": "heading",
                "text": "JSON",
                "heading_ancestry": ["Data types", "JSON"],
            },
        ],
    })

    assert {row["candidate_type"] for row in evidence} == {"FUNCTION", "DATATYPE"}
    assert decision["type_status"] == "REVIEW_REQUIRED"
    assert decision["primary_type"] == "TARGET_FEATURE_UNCERTAIN"
