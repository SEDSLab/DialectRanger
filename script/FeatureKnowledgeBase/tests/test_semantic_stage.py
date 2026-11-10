from __future__ import annotations

from agents.semantic_stage.evidence_bundle import build_evidence_bundle, build_indexes
from agents.semantic_stage.grounding import validate_semantic_output
from agents.semantic_stage.pipeline import _build_summary
from agents.semantic_stage.target_admission import apply_target_decision, deterministic_admission


def _group(status="CONFIRMED_FEATURE", feature_type="FUNCTION", identity="ABS"):
    return {
        "identity_unit_id": f"identity_unit:{identity.lower()}",
        "canonical_identity": identity,
        "feature_type": feature_type,
        "type_hint": feature_type,
        "group_status": status,
        "variants": [f"{identity}(X)"],
        "defining_variants": [f"{identity}(X)"],
        "occurrence_ids": [f"occ:{identity.lower()}"],
        "defining_occurrence_ids": [f"occ:{identity.lower()}"],
        "owner_ids": [f"owner:{identity.lower()}"],
        "source_urls": [f"https://example.test/{identity.lower()}"],
        "candidate_ids": [f"candidate:{identity.lower()}"],
        "evidence": [{
            "occurrence_ids": [f"occ:{identity.lower()}"],
            "owner_id": f"owner:{identity.lower()}",
            "source_block_ids": [f"block:{identity.lower()}"],
        }],
        "provenance": {"dbms": "testdb"},
    }


def _fixture_records():
    group = _group()
    occurrence = {
        "occurrence_id": "occ:abs",
        "owner_id": "owner:abs",
        "page_id": "page:abs",
        "source_block_id": "block:abs",
        "source_url": "https://example.test/abs",
        "raw_identity": "ABS(X)",
        "raw_evidence": "ABS(X)",
        "occurrence_role": "DEFINING_IDENTITY",
    }
    owner = {
        "owner_id": "owner:abs",
        "page_id": "page:abs",
        "owner_kind": "SectionOwner",
        "template": "section",
        "member_block_ids": ["block:abs", "block:abs-example"],
        "status": "STRONG",
    }
    document = {
        "page_id": "page:abs",
        "url": "https://example.test/abs",
        "blocks": [
            {"block_id": "block:abs", "block_kind": "HEADING", "text": "ABS(X)"},
            {"block_id": "block:abs-example", "block_kind": "CODE", "text": "SELECT ABS(2);"},
        ],
    }
    return group, [occurrence], [owner], [document]


def test_non_feature_is_excluded_confirmed_is_direct_and_probable_needs_gate():
    assert deterministic_admission(_group("NON_FEATURE_ONLY", "UNCERTAIN", "ordinary"))["semantic_ready"] is False
    confirmed = deterministic_admission(_group())
    assert confirmed["semantic_ready"] is True
    assert confirmed["target_resolution"] == "DETERMINISTIC"
    assert confirmed["target_classification"] == "TARGET"
    probable = deterministic_admission(_group("PROBABLE_FEATURE", "FUNCTION", "SUM"))
    assert probable["semantic_ready"] is False
    assert probable["admission_status"] == "PENDING_TARGET_CLASSIFICATION"
    assert probable["target_resolution"] == "PENDING_AI"
    assert probable["target_classification"] == "PENDING"
    assert probable["group_status"] == "PROBABLE_FEATURE"


def test_uncertain_target_decision_is_explicit_and_does_not_change_group_status():
    record = deterministic_admission(_group("UNCERTAIN_FEATURE", "UNCERTAIN", "CUSTOM"))
    decided = apply_target_decision(
        record, "OPERATOR", "official operator table", model="test-model",
        prompt_version="test/v1", response_hash="hash",
    )
    assert decided["semantic_ready"] is True
    assert decided["feature_type"] == "OPERATOR"
    assert decided["group_status"] == "UNCERTAIN_FEATURE"
    non_target = apply_target_decision(
        record, "NON_TARGET", "ordinary prose", model="test-model",
        prompt_version="test/v1", response_hash="hash",
    )
    assert non_target["semantic_ready"] is False
    assert non_target["admission_status"] == "EXCLUDED"


def test_probable_target_decision_does_not_rewrite_type():
    record = deterministic_admission(_group("PROBABLE_FEATURE", "FUNCTION", "SUM"))
    decided = apply_target_decision(
        record, "FUNCTION", "owner-local function surface", model="test-model",
        prompt_version="test/v1", response_hash="hash",
    )
    assert decided["semantic_ready"] is True
    assert decided["admission_status"] == "ADMITTED"
    assert decided["target_resolution"] == "AI_CLASSIFIED"
    assert decided["target_classification"] == "TARGET"
    assert decided["feature_type"] == "FUNCTION"
    assert decided["group_status"] == "PROBABLE_FEATURE"


def test_probable_non_target_and_provider_failure_are_not_semantic_ready():
    record = deterministic_admission(_group("PROBABLE_FEATURE", "DATATYPE", "METHODS"))
    non_target = apply_target_decision(
        record, "NON_TARGET", "API method surface", model="test-model",
        prompt_version="test/v1", response_hash="hash",
    )
    assert non_target["admission_status"] == "EXCLUDED"
    assert non_target["target_classification"] == "NON_TARGET"
    assert non_target["semantic_ready"] is False

    review = apply_target_decision(
        record, "UNRESOLVED", "CALL_FAILED", model="test-model",
        prompt_version="test/v1", response_hash=None,
    )
    assert review["admission_status"] == "REVIEW_REQUIRED"
    assert review["target_classification"] == "REVIEW_REQUIRED"
    assert review["semantic_ready"] is False


def test_evidence_bundle_aggregates_owner_blocks_and_preserves_roles():
    group, occurrences, owners, documents = _fixture_records()
    second_owner = {
        "owner_id": "owner:abs-2", "page_id": "page:abs-2", "owner_kind": "TableRowOwner",
        "template": "table_row", "member_block_ids": ["block:abs-2"], "status": "STRONG",
    }
    group["owner_ids"].append("owner:abs-2")
    group["occurrence_ids"].append("occ:abs-2")
    occurrences.append({
        "occurrence_id": "occ:abs-2", "owner_id": "owner:abs-2", "page_id": "page:abs-2",
        "source_block_id": "block:abs-2", "source_url": "https://example.test/abs-2",
        "raw_identity": "ABS(X)", "raw_evidence": "ABS(X) Return absolute value",
        "occurrence_role": "DEFINING_IDENTITY",
    })
    owners.append(second_owner)
    documents.append({
        "page_id": "page:abs-2", "url": "https://example.test/abs-2",
        "blocks": [{"block_id": "block:abs-2", "block_kind": "TABLE_ROW", "text": "ABS(X) Return absolute value"}],
    })
    bundle = build_evidence_bundle(group, indexes=build_indexes([group], occurrences, owners, documents))
    assert len(bundle["owners"]) == 2
    assert {x["evidence_ref"] for x in bundle["source_blocks"]} == {"block:abs", "block:abs-example", "block:abs-2"}
    assert len(bundle["defining_evidence"]) == 2


def test_grounding_accepts_current_group_evidence_and_rejects_invalid_or_ungrounded_claims():
    group, occurrences, owners, documents = _fixture_records()
    bundle = build_evidence_bundle(group, indexes=build_indexes([group], occurrences, owners, documents))
    valid = validate_semantic_output({
        "description": {"text": "ABS(X)", "evidence_refs": ["block:abs"]},
        "formal_syntax": [{"text": "ABS(X)", "evidence_refs": ["block:abs"]}],
        "official_examples": [{"sql": "SELECT ABS(2);", "evidence_refs": ["block:abs-example"]}],
        "generated_examples": [{
            "sql": "SELECT ABS(-5);", "origin": "AI_GENERATED",
            "generation_basis": ["formal_syntax"], "verification_status": "UNVERIFIED",
        }],
    }, bundle)
    # ABS(X) is an identity/signature witness, not a description. Its literal
    # presence must not authorize a role change under the unified contract.
    assert valid["accepted_claims"] == 3
    assert valid["rejected_claims"] == 1
    assert valid['fields']['description'] is None
    assert valid["fields"]["official_examples"][0]["sql"] == "SELECT ABS(2);"
    assert valid["fields"]["generated_examples"][0]["origin"] == "AI_GENERATED"

    invalid = validate_semantic_output({
        "description": {"text": "invented description", "evidence_refs": ["block:abs"]},
        "formal_syntax": [{"text": "ABS(X)", "evidence_refs": ["block:not-this-group"]}],
        "official_examples": [{"sql": "SELECT ABS(9);", "evidence_refs": ["block:abs-example"]}],
        "generated_examples": [{"sql": "SELECT ABS(9);", "origin": "OFFICIAL", "verification_status": "UNVERIFIED"}],
    }, bundle)
    assert invalid["fields"]["description"] is None
    assert invalid["fields"]["formal_syntax"] == []
    assert invalid["fields"]["official_examples"] == []
    assert invalid["fields"]["generated_examples"] == []
    assert "DESCRIPTION_FIELD_ROLE_MISMATCH" in invalid["diagnostics"]
    assert "FORMAL_SYNTAX_INVALID_EVIDENCE_REF" in invalid["diagnostics"]


def test_missing_semantic_fields_do_not_reject_the_feature():
    group, occurrences, owners, documents = _fixture_records()
    bundle = build_evidence_bundle(group, indexes=build_indexes([group], occurrences, owners, documents))
    result = validate_semantic_output({
        "description": None,
        "formal_syntax": [],
        "parameters": [],
        "return_type": None,
        "official_examples": [],
        "generated_examples": [],
    }, bundle)
    assert result["fields"]["description"] is None
    assert result["rejected_claims"] == 0


def test_summary_separates_classification_and_semantic_provider_budget():
    summary = _build_summary(
        groups=[],
        admission_rows=[],
        uncertain_rows=[],
        bundles=[],
        features=[],
        validation_rows=[],
        llm_state={"requested": True, "ready": True, "reason": None},
        model="test-model",
        identity_root="identity-root",
        evidence_reuse={},
        semantic_budget=1,
        budget_accounting={
            "provider_call_budget_total": 1,
            "classification_calls": 1,
            "semantic_extraction_calls": 0,
            "provider_calls_total": 1,
            "features_semantic_attempted": 0,
            "features_semantic_completed": 0,
        },
    )
    assert summary["budget_accounting"]["classification_calls"] == 1
    assert summary["budget_accounting"]["semantic_extraction_calls"] == 0
    assert summary["semantic_provider_calls"] == 0
