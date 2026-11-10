from __future__ import annotations

from agents.semantic_sense.audit import (
    _apply_ai_decision,
    _deterministic_decision,
    _project_group,
    detect_sense_candidate,
)


def _feature(identity="ABS", feature_type="FUNCTION", *, definitions=None, description=None, examples=None, variants=None):
    refs = []
    for item in definitions or []:
        refs.append(item["evidence_ref"])
    for value in [description, *(examples or [])]:
        if value:
            refs.extend(value.get("evidence_refs") or [])
    return {
        "feature_id": f"feature:{identity.lower()}",
        "identity_group_id": f"group:{identity.lower()}",
        "name": identity,
        "normalized_identity": identity,
        "feature_type": feature_type,
        "variants": variants or [f"{identity}(X)"],
        "description": description,
        "formal_syntax": [],
        "parameters": [],
        "return_type": None,
        "notes": [],
        "official_examples": examples or [],
        "generated_examples": [],
        "owners": [],
        "sources": [],
        "evidence_refs": sorted(set(refs)),
        "semantic_status": "PARTIAL",
        "semantic_diagnostics": [],
    }


def _context(identity, feature_type, definitions, *, description=None, examples=None, variants=None):
    blocks = {}
    for item in definitions:
        ref = item["evidence_ref"]
        blocks[ref] = {
            "evidence_ref": ref,
            "block_id": ref,
            "text": item["raw_text"],
            "heading_ancestry": item.get("heading_ancestry", []),
            "source_url": item.get("source_url", f"https://example.test/{ref}"),
            "owner_ids": [f"owner:{ref}"],
        }
    for claim in [description, *(examples or [])]:
        if claim:
            for ref in claim.get("evidence_refs") or []:
                blocks.setdefault(ref, {
                    "evidence_ref": ref,
                    "block_id": ref,
                    "text": claim.get("text") or claim.get("sql") or "",
                    "heading_ancestry": [],
                    "source_url": f"https://example.test/{ref}",
                    "owner_ids": [f"owner:{ref}"],
                })
    feature = _feature(identity, feature_type, definitions=definitions, description=description, examples=examples, variants=variants)
    return {
        "key": (identity, feature_type),
        "features": [feature],
        "feature_ids": [feature["feature_id"]],
        "identity_group_ids": [feature["identity_group_id"]],
        "definitions": [dict(x, feature_id=feature["feature_id"], combined_text=x["raw_text"], block_text=x["raw_text"], heading_ancestry=x.get("heading_ancestry", [])) for x in definitions],
        "blocks": blocks,
        "source_urls": {x.get("source_url", "") for x in blocks.values()},
    }


def test_same_meaning_definitions_are_single_sense():
    context = _context("ABS", "FUNCTION", [
        {"evidence_ref": "d1", "raw_text": "ABS(X) Return the absolute value"},
        {"evidence_ref": "d2", "raw_text": "ABS(X) Return the absolute value"},
    ])
    assert detect_sense_candidate(context) is None
    decision = _deterministic_decision(context, None)
    assert decision["decision"] == "SINGLE_SENSE"
    assert decision["clusters"][0]["sense_label"] == "DEFAULT"


def test_comparison_and_assignment_definitions_split():
    context = _context("=", "OPERATOR", [
        {"evidence_ref": "cmp", "raw_text": "= Equal operator", "heading_ancestry": ["Comparison Operators"]},
        {"evidence_ref": "asg", "raw_text": "= Assign a value", "heading_ancestry": ["Assignment Operators"]},
    ], description={"text": "Equal operator", "evidence_refs": ["cmp"]}, examples=[
        {"sql": "SELECT 1 = 1", "evidence_refs": ["cmp"]},
        {"sql": "SET @x = 1", "evidence_refs": ["asg"]},
    ])
    candidate = detect_sense_candidate(context)
    assert candidate is not None
    decision = _deterministic_decision(context, candidate)
    assert decision["decision"] == "MULTI_SENSE"
    assert {x["sense_label"] for x in decision["clusters"]} == {"COMPARISON", "ASSIGNMENT"}


def test_abs_overloads_and_datatype_variants_do_not_split():
    overload = _context("ST_X", "FUNCTION", [
        {"evidence_ref": "d1", "raw_text": "ST_X(point) Return the X coordinate"},
        {"evidence_ref": "d2", "raw_text": "ST_X(point, srid) Return the X coordinate"},
    ], variants=["ST_X(point)", "ST_X(point, srid)"])
    datatype = _context("VARCHAR", "DATATYPE", [
        {"evidence_ref": "d1", "raw_text": "VARCHAR(M) Variable-length character string"},
        {"evidence_ref": "d2", "raw_text": "VARCHAR(M) CHARACTER SET charset Variable-length character string"},
    ], variants=["VARCHAR", "VARCHAR(M)", "VARCHAR(M) CHARACTER SET charset"])
    assert _deterministic_decision(overload, detect_sense_candidate(overload))["decision"] == "SINGLE_SENSE"
    assert _deterministic_decision(datatype, detect_sense_candidate(datatype))["decision"] == "SINGLE_SENSE"


def test_missing_defining_evidence_requires_review():
    context = _context("CUSTOM", "FUNCTION", [])
    candidate = detect_sense_candidate(context)
    assert candidate is not None
    assert _deterministic_decision(context, candidate)["decision"] == "REVIEW_REQUIRED"


def test_multi_sense_projection_does_not_leak_claims_between_clusters():
    context = _context("=", "OPERATOR", [
        {"evidence_ref": "cmp", "raw_text": "= Equal operator", "heading_ancestry": ["Comparison Operators"]},
        {"evidence_ref": "asg", "raw_text": "= Assign a value", "heading_ancestry": ["Assignment Operators"]},
    ], description={"text": "Equal operator", "evidence_refs": ["cmp"]}, examples=[
        {"sql": "SELECT 1 = 1", "evidence_refs": ["cmp"]},
        {"sql": "SET @x = 1", "evidence_refs": ["asg"]},
    ])
    decision = _deterministic_decision(context, detect_sense_candidate(context))
    features, _ = _project_group(context, decision)
    by_sense = {x["semantic_sense"]: x for x in features}
    assert by_sense["COMPARISON"]["description"]["evidence_refs"] == ["cmp"]
    assert by_sense["COMPARISON"]["official_examples"] == [{"sql": "SELECT 1 = 1", "evidence_refs": ["cmp"]}]
    assert by_sense["ASSIGNMENT"]["description"] is None
    assert by_sense["ASSIGNMENT"]["official_examples"] == [{"sql": "SET @x = 1", "evidence_refs": ["asg"]}]
    assert "asg" not in by_sense["COMPARISON"]["evidence_refs"]
    assert "cmp" not in by_sense["ASSIGNMENT"]["evidence_refs"]


def test_ai_cannot_change_identity_or_type_and_invalid_refs_require_review():
    context = _context("ABS", "FUNCTION", [
        {"evidence_ref": "d1", "raw_text": "ABS(X) Return the absolute value"},
        {"evidence_ref": "d2", "raw_text": "ABS(X) Return the absolute value"},
    ])
    candidate = {
        "candidate_id": "candidate:abs",
        "candidate_reason": ["test"],
    }
    response = {
        "_status": "ok",
        "_raw_response": '{"decision":"MULTI_SENSE","identity":"EVIL","feature_type":"DATATYPE","clusters":[{"sense_label":"A","defining_evidence_refs":["not-allowed"]},{"sense_label":"B","defining_evidence_refs":["d1"]}]}',
    }
    decision = _apply_ai_decision(candidate, context, response, "test-model")
    assert decision["decision"] == "REVIEW_REQUIRED"
    assert decision["normalized_identity"] == "ABS"
    assert decision["feature_type"] == "FUNCTION"
