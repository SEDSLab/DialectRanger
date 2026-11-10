from __future__ import annotations

import json
from pathlib import Path

from agents.semantic_stage.pipeline import run_semantic_stage
from agents.semantic_stage.type_aware_target_gate import derive_risk, route_for_confirmed


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _group(identity: str, feature_type: str):
    suffix = identity.replace("+", "plus").lower()
    return {
        "identity_unit_id": f"unit:{suffix}",
        "canonical_identity": identity,
        "feature_type": feature_type,
        "type_hint": feature_type,
        "group_status": "CONFIRMED_FEATURE",
        "variants": [identity],
        "structural_roles": ["SIGNATURE", "FEATURE_NAME"],
        "occurrence_roles": {"DEFINING_IDENTITY": 1},
        "owner_ids": [f"owner:{suffix}"],
        "occurrence_ids": [f"occ:{suffix}"],
        "defining_occurrence_ids": [f"occ:{suffix}"],
        "candidate_ids": [f"candidate:{suffix}"],
        "provenance": {"page_id": f"page:{suffix}", "source_url": f"https://example.test/{suffix}", "dbms": "one"},
    }


def _fixture_root(tmp_path: Path):
    root = tmp_path / "identity"
    groups = [_group("ABS", "FUNCTION"), _group("+", "OPERATOR")]
    occurrences = []
    owners = []
    documents = []
    typed = []
    for group in groups:
        suffix = group["identity_unit_id"].split(":", 1)[1]
        owner_id = f"owner:{suffix}"
        block_id = f"block:{suffix}"
        occ_id = f"occ:{suffix}"
        page_id = f"page:{suffix}"
        owners.append({
            "owner_id": owner_id, "page_id": page_id, "owner_kind": "SectionOwner",
            "template": "section", "member_block_ids": [block_id], "status": "STRONG",
            "owner_scope_locality": "BOUNDED", "identity_values": [group["canonical_identity"]],
        })
        occurrences.append({
            "occurrence_id": occ_id, "owner_id": owner_id, "page_id": page_id,
            "source_block_id": block_id, "source_url": group["provenance"]["source_url"],
            "raw_identity": group["canonical_identity"], "raw_evidence": group["canonical_identity"],
            "occurrence_role": "DEFINING_IDENTITY",
        })
        documents.append({
            "page_id": page_id, "url": group["provenance"]["source_url"], "dbms": "one",
            "blocks": [{"block_id": block_id, "block_kind": "HEADING", "text": group["canonical_identity"], "heading_ancestry": [group["canonical_identity"]]}],
        })
        typed.append({
            "identity_unit_id": group["identity_unit_id"], "evidence_id": f"typed:{suffix}",
            "candidate_type": group["feature_type"], "strength": "STRONG", "subject_alignment": "EXACT_HEAD",
            "evidence_kind": "CALLABLE_SIGNATURE" if group["feature_type"] == "FUNCTION" else "OPERATOR_SLOT",
            "owner_id": owner_id, "owner_scope_locality": "BOUNDED", "owner_local_binding": True,
        })
    _write_jsonl(root / "identity" / "groups.jsonl", groups)
    _write_jsonl(root / "identity" / "occurrences.jsonl", occurrences)
    _write_jsonl(root / "owners" / "owners.jsonl", owners)
    _write_jsonl(root / "document_ir" / "documents.jsonl", documents)
    _write_jsonl(root / "type" / "typed_feature_evidence.jsonl", typed)
    return root


def test_risk_and_route_are_deterministic_and_inventory_independent():
    group = _group("ABS", "FUNCTION")
    owner = {"owner_id": "owner:abs", "owner_scope_locality": "BOUNDED", "identity_values": ["ABS"]}
    typed = [{
        "identity_unit_id": "unit:abs", "evidence_id": "typed:abs", "candidate_type": "FUNCTION",
        "strength": "STRONG", "subject_alignment": "EXACT_HEAD", "evidence_kind": "CALLABLE_SIGNATURE",
        "owner_id": "owner:abs", "owner_scope_locality": "BOUNDED", "owner_local_binding": True,
    }]
    first = derive_risk(group, {"owner:abs": owner}, typed)
    second = derive_risk({**group, "provenance": {"dbms": "different"}}, {"owner:abs": owner}, typed)
    assert first["risk_stratum"] == second["risk_stratum"] == "STRONG_CONFIRMED"
    assert route_for_confirmed(group, first) == "DIRECT_STRONG_FUNCTION"


def test_production_no_llm_routes_operator_to_explicit_review(tmp_path):
    identity_root = _fixture_root(tmp_path)
    result = run_semantic_stage(identity_root, tmp_path / "semantic", run_llm=False)
    admissions = {row["identity_group_id"]: row for row in _read_jsonl(Path(result["output_root"]) / "target" / "admission.jsonl")}
    assert admissions["unit:abs"]["route"] == "DIRECT_STRONG_FUNCTION"
    assert admissions["unit:abs"]["admission_status"] == "ADMITTED"
    assert admissions["unit:plus"]["route"] == "CLASSIFY_CONFIRMED_OPERATOR"
    assert admissions["unit:plus"]["admission_status"] == "REVIEW_REQUIRED"
    assert result["summary"]["budget_accounting"]["provider_calls_total"] == 0


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
