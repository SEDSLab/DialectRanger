import json

from agents.pipeline.generic_unified import _owner_type_context
from agents.owner.dom_first_shadow import extract_feature_anchors
from agents.semantic_stage.evidence_bundle import build_evidence_bundle, build_indexes
from agents.semantic_stage.pipeline import run_semantic_stage
from agents.type.decision import decide_type
from agents.type.evidence import build_typed_feature_evidence


def test_type_decision_ignores_foreign_owner_local_evidence():
    result = decide_type(
        {"identity_unit_id": "unit:x"},
        [
            {
                "evidence_id": "foreign",
                "owner_id": "owner:foreign",
                "candidate_type": "FUNCTION",
                "strength": "STRONG",
                "subject_alignment": "EXACT_HEAD",
                "owner_local_binding": False,
            },
            {
                "evidence_id": "local",
                "owner_id": "owner:local",
                "candidate_type": "OPERATOR",
                "strength": "STRONG",
                "subject_alignment": "EXACT_HEAD",
                "evidence_kind": "OPERATOR_SLOT",
                "owner_local_binding": True,
            },
        ],
    )
    assert result["primary_type"] == "OPERATOR"
    assert "OWNER_LOCAL_EVIDENCE_REJECTED" in result["reason_codes"]
    assert result["evidence_summary"]["owner_local_binding_rejected"] == 1


def test_evidence_bundle_does_not_promote_incidental_example_occurrence():
    group = {
        "identity_unit_id": "unit:abs",
        "canonical_identity": "ABS",
        "occurrence_ids": ["occ:1"],
        "owner_ids": ["owner:1"],
        "provenance": {"dbms": "synthetic"},
    }
    occurrence = {
        "occurrence_id": "occ:1",
        "owner_id": "owner:1",
        "page_id": "page:1",
        "source_block_id": "block:1",
        "occurrence_role": "EXAMPLE_TOKEN",
        "owner_local_binding": True,
        "raw_identity": "ABS",
        "source_url": "https://example.invalid/functions",
    }
    owner = {
        "owner_id": "owner:1",
        "page_id": "page:1",
        "member_block_ids": ["block:1"],
        "owner_scope_block_ids": ["block:1"],
        "owner_scope_locality": "BOUNDED",
    }
    document = {
        "page_id": "page:1",
        "url": "https://example.invalid/functions",
        "blocks": [{
            "block_id": "block:1",
            "block_kind": "CODE",
            "text": "SELECT ABS(x)",
            "heading_ancestry": ["Reference"],
        }],
    }
    bundle = build_evidence_bundle(
        group,
        indexes=build_indexes([group], [occurrence], [owner], [document]),
    )
    assert bundle["official_example_evidence"] == []
    assert bundle["source_blocks"][0]["structural_roles"] == ["EXAMPLE_OCCURRENCE"]
    assert bundle["evidence_completeness"]["official_example_auto_promotion"] is False


def test_owner_type_context_is_page_local():
    context = _owner_type_context(
        {"identity_unit_id": "unit:x", "canonical_identity": "X", "variants": []},
        {
            "owner_id": "owner:x",
            "owner_kind": "TableRowOwner",
            "member_block_ids": ["block:x"],
            "owner_scope_identity_values": ["X"],
            "owner_scope_mode": "IDENTITY_ANCHORED",
            "owner_scope_locality": "BOUNDED",
        },
        {
            "page_id": "page:x",
            "dbms": "synthetic",
            "url": "https://example.invalid/x",
            "blocks": [
                {"block_id": "block:x", "block_kind": "TABLE_ROW", "text": "X | local", "heading_ancestry": ["X"]},
                {"block_id": "block:other", "block_kind": "HEADING", "text": "OTHER", "heading_ancestry": ["OTHER"]},
            ],
        },
    )
    assert context["owner_heading_context"] == ["X"]
    assert "OTHER" not in context["owner_heading_context"]
    assert context["owner_local_binding"] is True


def test_semantic_execution_state_is_explicit_when_not_requested(tmp_path):
    identity = tmp_path / "identity"
    (identity / "identity").mkdir(parents=True)
    (identity / "owners").mkdir()
    (identity / "document_ir").mkdir()
    (identity / "identity" / "groups.jsonl").write_text(json.dumps({
        "identity_unit_id": "unit:x", "canonical_identity": "X",
        "group_status": "PROBABLE_FEATURE", "feature_type": "FUNCTION",
        "owner_ids": ["owner:x"], "occurrence_ids": ["occ:x"],
        "candidate_ids": ["candidate:x"], "defining_occurrence_ids": ["occ:x"],
        "provenance": {"dbms": "synthetic", "page_id": "page:x", "source_url": "https://example.invalid/x"},
    }) + "\n", encoding="utf-8")
    (identity / "identity" / "occurrences.jsonl").write_text(json.dumps({
        "occurrence_id": "occ:x", "owner_id": "owner:x", "page_id": "page:x",
        "source_block_id": "block:x", "owner_local_binding": True,
        "occurrence_role": "DEFINING_IDENTITY", "raw_identity": "X",
        "source_url": "https://example.invalid/x",
    }) + "\n", encoding="utf-8")
    (identity / "owners" / "owners.jsonl").write_text(json.dumps({
        "owner_id": "owner:x", "page_id": "page:x", "member_block_ids": ["block:x"],
        "owner_scope_block_ids": ["block:x"], "owner_scope_locality": "BOUNDED",
    }) + "\n", encoding="utf-8")
    (identity / "document_ir" / "documents.jsonl").write_text(json.dumps({
        "page_id": "page:x", "dbms": "synthetic", "url": "https://example.invalid/x",
        "page_hash": "hash", "html_path": "x.html", "blocks": [{
            "block_id": "block:x", "block_kind": "HEADING", "text": "X", "heading_ancestry": ["X"],
        }],
    }) + "\n", encoding="utf-8")
    result = run_semantic_stage(identity, tmp_path / "semantic", run_llm=False)
    assert result["summary"]["target_review_required"] >= 1
    assert result["summary"]["semantic_features"] == 0
    assert result["summary"]["budget_accounting"]["probable_classification_calls"] == 0
    feature_path = tmp_path / "semantic" / "semantic" / "semantic_features.jsonl"
    assert feature_path.read_text(encoding="utf-8") == ""


def test_generic_compound_operator_surface_stays_owner_local():
    strong, _ = extract_feature_anchors({
        "page_id": "page:operator",
        "blocks": [{
            "block_id": "block:operator",
            "block_kind": "HEADING",
            "text": "IS NULL and IS NOT NULL #",
            "heading_ancestry": ["Comparison operators #"],
        }],
    })
    assert {row["identity"] for row in strong} == {"IS NULL", "IS NOT NULL"}


def test_generic_datatype_heading_surface_strips_anchor_and_preserves_aliases():
    strong, _ = extract_feature_anchors({
        "page_id": "page:type",
        "blocks": [{
            "block_id": "block:type",
            "block_kind": "HEADING",
            "text": "INTEGER or INT #",
            "heading_ancestry": ["Data types #"],
        }, {
            "block_id": "block:bigint",
            "block_kind": "HEADING",
            "text": "BIGINT #",
            "heading_ancestry": ["Data types #"],
        }],
    })
    assert {row["identity"] for row in strong} == {"INTEGER", "INT", "BIGINT"}


def test_non_sql_api_surface_is_not_typed_as_a_feature():
    evidence = build_typed_feature_evidence({
        "identity_unit_id": "unit:client",
        "canonical_subject": "CLIENT",
        "owner_id": "owner:api",
        "owner_kind": "PageOwner",
        "page_title": "Client API reference",
        "page_heading_context": ["Client API"],
        "owner_local_binding": True,
        "blocks": [{
            "block_id": "block:api",
            "block_type": "text",
            "text": "Client API methods and request objects",
            "heading_ancestry": ["Client API"],
        }],
    })
    assert not [row for row in evidence if row["candidate_type"] in {"FUNCTION", "OPERATOR", "DATATYPE"}]
