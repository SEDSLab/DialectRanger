from __future__ import annotations

import json

from agents.semantic_stage import pipeline
from agents.semantic_stage.target_admission import deterministic_admission


def _write_identity(root, groups):
    (root / "identity").mkdir(parents=True)
    (root / "owners").mkdir()
    (root / "document_ir").mkdir()
    (root / "identity" / "groups.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in groups), encoding="utf-8",
    )
    occurrences = []
    owners = []
    for group in groups:
        identity = group["canonical_identity"]
        occurrences.append({
            "occurrence_id": f"occ:{identity}",
            "owner_id": f"owner:{identity}",
            "page_id": f"page:{identity}",
            "source_block_id": f"block:{identity}",
            "source_url": f"https://example.test/{identity.lower()}",
            "raw_identity": identity,
            "raw_evidence": identity,
            "occurrence_role": "DEFINING_IDENTITY",
            "owner_local_binding": True,
        })
        owners.append({
            "owner_id": f"owner:{identity}",
            "page_id": f"page:{identity}",
            "owner_kind": "SectionOwner",
            "member_block_ids": [f"block:{identity}"],
            "owner_scope_block_ids": [f"block:{identity}"],
            "owner_scope_locality": "BOUNDED",
        })
    (root / "identity" / "occurrences.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in occurrences), encoding="utf-8",
    )
    (root / "owners" / "owners.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in owners), encoding="utf-8",
    )
    (root / "type").mkdir()
    typed_rows = [{
        "identity_unit_id": group["identity_unit_id"],
        "evidence_id": f"typed:{group['canonical_identity']}",
        "candidate_type": "FUNCTION",
        "strength": "STRONG",
        "subject_alignment": "EXACT_HEAD",
        "evidence_kind": "CALLABLE_SIGNATURE",
        "owner_id": f"owner:{group['canonical_identity']}",
        "owner_scope_locality": "BOUNDED",
        "owner_local_binding": True,
    } for group in groups if group.get("group_status") == "CONFIRMED_FEATURE"]
    (root / "type" / "typed_feature_evidence.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in typed_rows), encoding="utf-8",
    )
    documents = []
    for group in groups:
        identity = group["canonical_identity"]
        documents.append({
            "page_id": f"page:{identity}",
            "dbms": "synthetic",
            "url": f"https://example.test/{identity.lower()}",
            "page_hash": f"hash:{identity}",
            "html_path": f"{identity.lower()}.html",
            "blocks": [{
                "block_id": f"block:{identity}",
                "block_kind": "HEADING",
                "text": identity,
                "heading_ancestry": [identity],
            }],
        })
    (root / "document_ir" / "documents.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in documents), encoding="utf-8",
    )


def _group(identity, status):
    return {
        "identity_unit_id": f"unit:{identity}",
        "canonical_identity": identity,
        "feature_type": "FUNCTION",
        "type_hint": "FUNCTION",
        "group_status": status,
        "variants": [f"{identity}(X)"],
        "structural_roles": ["SIGNATURE", "FEATURE_NAME"],
        "occurrence_roles": {"DEFINING_IDENTITY": 1},
        "owner_ids": [f"owner:{identity}"],
        "occurrence_ids": [f"occ:{identity}"],
        "defining_occurrence_ids": [f"occ:{identity}"],
        "candidate_ids": [f"candidate:{identity}"],
        "source_urls": [f"https://example.test/{identity.lower()}"],
        "provenance": {
            "dbms": "synthetic",
            "page_id": f"page:{identity}",
            "source_url": f"https://example.test/{identity.lower()}",
        },
    }


def test_confirmed_direct_path_and_probable_gate_use_separate_calls(tmp_path, monkeypatch):
    groups = [_group("ABS", "CONFIRMED_FEATURE"), _group("SUM", "PROBABLE_FEATURE")]
    identity = tmp_path / "identity"
    _write_identity(identity, groups)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def _check_ready(self):
            return None

        def call(self, prompt, feature_id):
            self.calls.append((prompt, feature_id))
            if prompt.startswith("Classify only"):
                return {"_status": "ok", "_raw_response": '{"decision":"FUNCTION","reason":"target"}'}
            return {
                "_status": "ok",
                "_raw_response": json.dumps({"identity_group_id": feature_id}),
            }

    fake = FakeClient()
    monkeypatch.setattr(pipeline.LLMClientFactory, "create", lambda config: fake)
    result = pipeline.run_semantic_stage(
        identity,
        tmp_path / "semantic",
        run_llm=True,
        model="fake-model",
        workers=1,
        semantic_budget=10,
    )
    summary = result["summary"]
    budget = summary["budget_accounting"]
    assert summary["confirmed_admitted"] == 1
    assert summary["probable_admitted"] == 1
    assert budget["confirmed_direct_semantic"] == 1
    assert budget["probable_classification_calls"] == 1
    assert budget["classification_calls_total"] == 1
    assert budget["semantic_extraction_calls"] == 2
    assert len(fake.calls) == 3
    assert sum(prompt.startswith("Classify only") for prompt, _ in fake.calls) == 1


def test_probable_no_llm_is_review_and_never_semantic_ready():
    group = _group("SUM", "PROBABLE_FEATURE")
    admission = deterministic_admission(group)
    assert admission["admission_status"] == "PENDING_TARGET_CLASSIFICATION"
    assert admission["semantic_ready"] is False
