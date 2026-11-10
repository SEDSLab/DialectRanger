from __future__ import annotations

from agents.identity.target_evidence_shadow import (
    _aggregate_group,
    classify_root_cause,
    classify_target_evidence,
)


def _occurrence(
    identity: str,
    *,
    raw: str | None = None,
    role: str = "DEFINING_IDENTITY",
    source_type: str = "table_row",
    defining: bool = True,
    block_kind: str = "TABLE_ROW",
    text: str | None = None,
    headers: list[str] | None = None,
    cells: list[str] | None = None,
    ancestry: list[str] | None = None,
    url: str = "https://docs.example.test/reference.html",
    surface_role: str = "FEATURE_CATALOG",
):
    raw = raw or identity
    return {
        "occurrence_id": f"occ:{identity}:{raw}",
        "normalized_identity": identity,
        "raw_identity": raw,
        "occurrence_role": role,
        "defining": defining,
        "source_type": source_type,
        "raw_evidence": text or raw,
        "owner_id": "owner:test",
        "page_id": "page:test",
        "source_url": url,
        "source_block_id": "block:test",
        "surface_role": surface_role,
        "source_context": {
            "block_kind": block_kind,
            "table_headers": headers or [],
            "heading_ancestry": ancestry or [],
            "owner_kind": "TableRowOwner",
            "template": "table_row",
        },
    }


def _context_block(occurrence):
    context = occurrence["source_context"]
    return {
        "block_id": occurrence["source_block_id"],
        "block_kind": context["block_kind"],
        "text": occurrence["raw_evidence"],
        "heading_ancestry": context["heading_ancestry"],
        "table_metadata": {
            "headers": context["table_headers"],
            "cells": occurrence.get("_cells", []),
        },
    }


def _classify(occurrence, *, cells=None, owner=None, page=None):
    occurrence = dict(occurrence)
    occurrence["_cells"] = cells or []
    return classify_target_evidence(
        occurrence,
        block=_context_block(occurrence),
        owner=owner or {"owner_id": "owner:test", "candidate_kind": "TABLE_ROW_OWNER"},
        page=page or {"dbms": "test", "page_id": "page:test", "url": occurrence["source_url"], "page_hash": "hash", "html_path": "cached.html"},
    )


def test_glossary_definition_is_document_defining_but_not_target_defining():
    occurrence = _occurrence(
        "ACID",
        source_type="definition_list",
        block_kind="DEFINITION",
        text="ACID Atomicity, consistency, isolation, and durability",
        ancestry=["MySQL Glossary", "A"],
        url="https://docs.example.test/glossary.html",
        surface_role="FEATURE_DEFINITION",
    )
    evidence = _classify(occurrence)
    assert evidence["document_defining"] is True
    assert evidence["target_feature_defining"] is False
    assert evidence["target_types"] == []
    assert classify_root_cause(evidence, occurrence) == "GLOSSARY_TERM"


def test_callable_signature_is_function_target_evidence():
    occurrence = _occurrence(
        "ABS",
        raw="ABS(X)",
        text="ABS(X) Returns the absolute value",
        headers=["Name", "Description"],
        cells=["ABS(X)", "Returns the absolute value"],
        ancestry=["Mathematical Functions"],
        url="https://docs.example.test/mathematical-functions.html",
    )
    evidence = _classify(occurrence, cells=occurrence["source_context"]["table_headers"][:0] + ["ABS(X)", "Returns the absolute value"])
    assert evidence["target_feature_defining"] is True
    assert evidence["target_types"] == ["FUNCTION"]
    assert evidence["strength"] == "STRONG"
    unit = _aggregate_group("ABS", [{**occurrence, "target_feature_evidence": evidence}])
    assert unit["group_status"] == "CONFIRMED_FEATURE"
    assert unit["feature_type"] == "FUNCTION"


def test_operator_expression_is_target_evidence_without_callable_syntax():
    occurrence = _occurrence(
        "IS",
        text="IS expr IS NULL, expr IS TRUE, expr IS FALSE",
        headers=["Operator", "Description"],
        cells=["IS", "expr IS NULL, expr IS TRUE, expr IS FALSE"],
        ancestry=["Comparison Operators"],
        url="https://docs.example.test/comparison-operators.html",
    )
    evidence = _classify(occurrence, cells=["IS", "expr IS NULL, expr IS TRUE, expr IS FALSE"])
    assert evidence["target_feature_defining"] is True
    assert evidence["target_types"] == ["OPERATOR"]
    assert evidence["strength"] == "STRONG"


def test_case_expression_can_override_generic_function_heading():
    occurrence = _occurrence(
        "CASE",
        text="CASE WHEN condition THEN result ELSE other END",
        headers=["Name", "Description"],
        cells=["CASE", "CASE WHEN condition THEN result ELSE other END"],
        ancestry=["Flow Control Functions"],
        url="https://docs.example.test/flow-control-functions.html",
    )
    evidence = _classify(occurrence, cells=["CASE", "CASE WHEN condition THEN result ELSE other END"])
    assert evidence["target_feature_defining"] is True
    assert evidence["target_types"] == ["OPERATOR"]
    assert evidence["strength"] == "STRONG"


def test_datatype_declaration_is_datatype_target_evidence():
    occurrence = _occurrence(
        "BIGINT",
        block_kind="DEFINITION",
        source_type="definition_list",
        text="BIGINT Integer data type with a signed range",
        headers=["Data Type", "Description"],
        ancestry=["Numeric Data Types"],
        surface_role="FEATURE_DEFINITION",
    )
    evidence = _classify(occurrence)
    assert evidence["target_feature_defining"] is True
    assert evidence["target_types"] == ["DATATYPE"]


def test_mentioned_datatype_is_not_a_defining_target():
    occurrence = _occurrence(
        "BIGINT",
        role="OUTPUT_TEXT",
        source_type="text",
        defining=False,
        block_kind="TEXT",
        text="The result cannot be stored in a signed BIGINT value.",
        headers=[],
        ancestry=["Mathematical Functions"],
        surface_role="SUPPORTING_CONTEXT",
    )
    evidence = _classify(occurrence)
    assert evidence["document_defining"] is False
    assert evidence["target_feature_defining"] is False
    assert evidence["target_types"] == []


def test_example_invocation_does_not_become_defining_target():
    occurrence = _occurrence(
        "ABS",
        raw="ABS(2)",
        role="EXAMPLE_TOKEN",
        source_type="inline_code",
        defining=False,
        block_kind="CODE",
        text="mysql> SELECT ABS(2);",
        ancestry=["Examples"],
        surface_role="SUPPORTING_CONTEXT",
    )
    evidence = _classify(occurrence)
    assert evidence["target_feature_defining"] is False
    assert evidence["target_types"] == []
    assert "EXAMPLE_ONLY" in evidence["reason_codes"]


def test_table_header_name_does_not_become_function():
    occurrence = _occurrence(
        "NAME",
        raw="Name",
        text="Name Description",
        headers=["Name", "Description"],
        cells=["Name", "Description"],
        ancestry=["Function Reference"],
    )
    evidence = _classify(occurrence, cells=["Name", "Description"])
    assert evidence["document_defining"] is False
    assert evidence["target_types"] == []
    assert "TABLE_HEADER_OR_LABEL" in evidence["reason_codes"]


def test_definition_owner_alone_does_not_imply_feature():
    occurrence = _occurrence(
        "BUFFER",
        source_type="definition_list",
        block_kind="DEFINITION",
        text="BUFFER A memory area used by the server",
        ancestry=["Glossary"],
        url="https://docs.example.test/glossary.html",
        surface_role="FEATURE_DEFINITION",
    )
    evidence = _classify(occurrence)
    assert evidence["target_types"] == []
    assert evidence["target_feature_defining"] is False


def test_profile_hint_alone_does_not_imply_feature():
    occurrence = _occurrence(
        "TERM",
        source_type="heading",
        block_kind="HEADING",
        text="TERM",
        headers=[],
        ancestry=["Narrative Concepts"],
        surface_role="FEATURE_REFERENCE",
    )
    evidence = _classify(occurrence, owner={"owner_id": "owner:test", "candidate_kind": "HEADING_SECTION_OWNER", "profile_hint": {"type_hint": "FUNCTION", "feature_bearing": True}})
    assert evidence["target_types"] == []
    assert evidence["target_feature_defining"] is False

