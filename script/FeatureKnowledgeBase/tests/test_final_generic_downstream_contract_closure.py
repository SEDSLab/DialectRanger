from __future__ import annotations

from agents.contracts.identity_credibility import (
    evaluate_identity_credibility,
    make_adaptation_provenance_record,
)
from agents.semantic_stage.grounding import semantic_status


def _fixture():
    page = {
        "page_id": "page:one",
        "dbms": "fixture",
        "url": "https://example.invalid/reference",
        "html_path": "reference.html",
        "page_hash": "hash-one",
        "blocks": [{
            "block_id": "block:one",
            "block_kind": "table_row",
            "text": "Widget value",
            "heading_ancestry": ["Reference"],
        }],
    }
    owner = {
        "owner_id": "owner:one",
        "page_id": "page:one",
        "member_block_ids": ["block:one"],
    }
    bundle = {
        "identity_group_id": "identity_unit:adapted:one",
        "owners": [{"owner_id": "owner:one"}],
        "source_blocks": [{
            "block_id": "block:one",
            "owner_ids": ["owner:one"],
            "page_id": "page:one",
            "text": "Widget value",
            "structural_roles": ["DATATYPE_DECLARATION"],
        }],
    }
    return page, owner, bundle


def test_optional_semantics_do_not_promote_structural_only():
    assert semantic_status({"parameters": [{"name": "x"}]}, []) == "STRUCTURAL_ONLY"
    assert semantic_status({"return_type": {"text": "INTEGER"}}, []) == "STRUCTURAL_ONLY"
    assert semantic_status({"notes": [{"text": "note"}]}, []) == "STRUCTURAL_ONLY"
    assert semantic_status({"official_examples": [{"sql": "SELECT 1"}]}, []) == "PARTIAL"
    assert semantic_status({"formal_syntax": [{"text": "WIDGET(x)"}]}, []) == "PARTIAL"


def test_adapted_identity_uses_owner_local_provenance_without_id_prefix_rule():
    page, owner, bundle = _fixture()
    unit = {
        "identity_unit_id": "unit:adapted-but-not-prefixed",
        "canonical_identity": "WIDGET",
        "feature_type": "DATATYPE",
        "owner_ids": ["owner:one"],
        "candidate_ids": [],
        "provenance": {
            "dbms": "fixture",
            "page_id": "page:one",
            "source_url": page["url"],
            "html_path": page["html_path"],
            "page_hash": page["page_hash"],
        },
        "adaptation_provenance": [make_adaptation_provenance_record(
            adaptation_kind="DATATYPE",
            adaptation_rule_id="GENERIC_DATATYPE_OWNER_SURFACE",
            owner_id="owner:one",
            page_id="page:one",
            source_url=page["url"],
            block_id="block:one",
            raw_text="Widget value",
            page_hash=page["page_hash"],
        )],
    }
    result = evaluate_identity_credibility(
        unit, owners=[owner], documents=[page], bundle=bundle,
    )
    assert result["origin"] == "ADAPTED"
    assert result["identity_credible"] is True
    assert result["lineage_complete"] is True


def test_adapted_identity_without_real_block_is_rejected():
    page, owner, bundle = _fixture()
    unit = {
        "identity_unit_id": "unit:adapted",
        "canonical_identity": "WIDGET",
        "owner_ids": ["owner:one"],
        "provenance": {
            "dbms": "fixture", "page_id": "page:one",
            "source_url": page["url"], "html_path": page["html_path"],
            "page_hash": page["page_hash"],
        },
        "adaptation_provenance": [{
            "adaptation": "DATATYPE", "owner_id": "owner:one",
            "page_id": "page:one", "source_url": page["url"],
            "block_id": "block:missing", "raw_text": "WIDGET",
        }],
    }
    result = evaluate_identity_credibility(
        unit, owners=[owner], documents=[page], bundle=bundle,
    )
    assert result["identity_credible"] is False
    assert "ADAPTED_PROVENANCE_INVALID" in result["reasons"]
