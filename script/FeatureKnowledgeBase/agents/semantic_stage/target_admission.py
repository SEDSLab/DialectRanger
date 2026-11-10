"""Target admission and narrow uncertain-target classification primitives."""
from __future__ import annotations

from typing import Any, Mapping


TARGET_TYPES = ("FUNCTION", "OPERATOR", "DATATYPE")
UNCERTAIN_DECISIONS = (*TARGET_TYPES, "NON_TARGET", "UNRESOLVED")
TARGET_GATE_STATUSES = {"PROBABLE_FEATURE", "UNCERTAIN_FEATURE"}


def normalized_type(group: Mapping[str, Any]) -> str:
    return str(group.get("feature_type") or group.get("type_hint") or "UNCERTAIN").upper()


def group_status(group: Mapping[str, Any]) -> str:
    return str(group.get("group_status") or "UNKNOWN").upper()


def target_gate_required(group: Mapping[str, Any]) -> bool:
    """Return whether publication needs an explicit target decision.

    Confirmed identity groups already have a strong generic target decision.
    Probable and uncertain groups retain their original Identity/Type records,
    but cannot enter Semantic or publication without the existing narrow
    target-classification path.
    """

    return group_status(group) in TARGET_GATE_STATUSES


def evidence_refs_for_group(group: Mapping[str, Any]) -> list[str]:
    refs: set[str] = set()
    for value in group.get("occurrence_ids") or []:
        refs.add(str(value))
    for value in group.get("defining_occurrence_ids") or []:
        refs.add(str(value))
    for record in group.get("evidence") or []:
        for value in record.get("occurrence_ids") or []:
            refs.add(str(value))
        for value in record.get("source_block_ids") or []:
            refs.add(str(value))
    return sorted(refs)


def admission_base(group: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(group.get("canonical_identity") or group.get("normalized_identity") or "")
    return {
        "identity_group_id": str(group.get("identity_unit_id") or ""),
        "normalized_identity": identity,
        "identity": identity,
        "previous_type": normalized_type(group),
        "feature_type": normalized_type(group),
        "group_status": group_status(group),
        "variants": sorted({str(x) for x in (group.get("variants") or []) if str(x).strip()}),
        "input_evidence_refs": evidence_refs_for_group(group),
        "source_urls": sorted({str(x) for x in (group.get("source_urls") or []) if str(x).strip()}),
        "semantic_ready": False,
        "admission_status": "EXCLUDED",
        "target_resolution": "NONE",
        "target_classification": "NOT_REQUIRED",
        "target_classification_type": None,
        "reason": "",
    }


def deterministic_admission(group: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only the Identity Quality status/type contract.

    Semantic completeness is deliberately not consulted here.
    """
    result = admission_base(group)
    status = result["group_status"]
    feature_type = result["feature_type"]
    if status == "CONFIRMED_FEATURE" and feature_type in TARGET_TYPES:
        result.update({
            "semantic_ready": True,
            "admission_status": "ADMITTED",
            "target_resolution": "DETERMINISTIC",
            "target_classification": "TARGET",
            "target_classification_type": feature_type,
            "reason": f"{status} with supported feature type",
        })
        return result
    if status == "PROBABLE_FEATURE" and feature_type in TARGET_TYPES:
        result.update({
            "admission_status": "PENDING_TARGET_CLASSIFICATION",
            "target_resolution": "PENDING_AI",
            "target_classification": "PENDING",
            "reason": "probable identity group requires publication target classification",
        })
        return result
    if status == "NON_FEATURE_ONLY":
        result["reason"] = "identity group contains no defining feature evidence"
        return result
    if status == "UNCERTAIN_FEATURE" or feature_type == "UNCERTAIN":
        result.update({
            "admission_status": "PENDING_TARGET_CLASSIFICATION",
            "target_resolution": "PENDING_AI",
            "target_classification": "PENDING",
            "reason": "uncertain identity group requires narrow target classification",
        })
        return result
    result["reason"] = "identity group status/type is not a semantic target"
    return result


def target_classification_prompt(group: Mapping[str, Any], bundle: Mapping[str, Any] | None = None,
                                 *, type_decision: Mapping[str, Any] | None = None) -> str:
    """Build a strict, non-semantic target classification prompt."""
    evidence = []
    for item in (bundle or {}).get("main_evidence", [])[:40]:
        evidence.append({
            "evidence_ref": item.get("evidence_ref"),
            "block_kind": item.get("block_kind"),
            "heading_ancestry": item.get("heading_ancestry", []),
            "text": item.get("text", ""),
            "source_url": item.get("source_url", ""),
            "table_context": item.get("table_context"),
            "definition_section_proofs": item.get("definition_section_proofs", []),
            "block_id": item.get('block_id') or item.get('evidence_ref'),
            "dom_path": item.get('dom_path'),
            "inline_nodes": item.get('inline_nodes', []),
        })
    payload = {
        "identity_group_id": group.get("identity_unit_id"),
        "identity": group.get("canonical_identity"),
        "variants": group.get("variants", []),
        "previous_type": normalized_type(group),
        "group_status": group_status(group),
        "input_evidence_refs": evidence_refs_for_group(group),
        "evidence": evidence,
        "upstream_type_decision": dict(type_decision or {}),
    }
    return (
        "Classify only whether this official documentation identity is a target "
        "feature. Do not extract semantics and do not rename the identity. "
        "Return ONLY JSON with decision one of FUNCTION, OPERATOR, DATATYPE, "
        "NON_TARGET, UNRESOLVED and a short reason. A FUNCTION, OPERATOR, or "
        "DATATYPE decision means TARGET under the existing classifier contract; "
        "it must not rewrite the upstream TypeDecision. Use only the supplied "
        "owner-local evidence. Repeating previous_type does not resolve Type REVIEW. "
        "If new independent defining evidence resolves it, additionally return "
        "resolution_evidence with feature_type, evidence_refs (source block IDs), "
        "and resolved_conflict_refs. These references will be independently validated; "
        "a reason or confidence is not proof.\n"
        + _json(payload)
        + '\n{"decision":"FUNCTION|OPERATOR|DATATYPE|NON_TARGET|UNRESOLVED",'
        '"reason":"..."}'
    )


def apply_target_decision(record: dict[str, Any], decision: str, reason: str, *, model: str,
                          prompt_version: str, response_hash: str | None,
                          resolution_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    decision = str(decision or "UNRESOLVED").upper()
    previous_type = normalized_type(record)
    status = group_status(record)
    if decision == "TARGET":
        decision = previous_type if previous_type in TARGET_TYPES else "UNRESOLVED"
    elif decision == "REVIEW_REQUIRED":
        decision = "UNRESOLVED"
    if decision not in UNCERTAIN_DECISIONS:
        decision = "UNRESOLVED"
        reason = reason or "invalid target classification decision"
    record = dict(record)
    # Untrusted resolution proposal, not a verified state transition.
    record.pop("resolution_evidence", None)
    if isinstance(resolution_evidence, Mapping):
        record["resolution_evidence"] = dict(resolution_evidence)
    record["ai_decision"] = decision
    record["reason"] = reason or "no classification reason supplied"
    record["model"] = model or "NOT_CONFIGURED"
    record["prompt_version"] = prompt_version
    record["raw_response_hash"] = response_hash
    if decision in TARGET_TYPES:
        if status == "PROBABLE_FEATURE" and previous_type in TARGET_TYPES and decision != previous_type:
            # The target gate is not a second TypeDecision.  A contradiction
            # is isolated for review instead of silently changing the Type.
            record.update({
                "target_classification": "REVIEW_REQUIRED",
                "target_classification_type": decision,
                "target_classification_conflict": True,
                "semantic_ready": False,
                "admission_status": "REVIEW_REQUIRED",
                "target_resolution": "UNRESOLVED",
                "reason": reason or "target classification conflicts with existing TypeDecision",
            })
            return record
        record.update({
            "feature_type": previous_type if status == "PROBABLE_FEATURE" and previous_type in TARGET_TYPES else decision,
            "semantic_ready": True,
            "admission_status": "ADMITTED",
            "target_resolution": "AI_CLASSIFIED",
            "target_classification": "TARGET",
            "target_classification_type": decision,
            "target_classification_conflict": False,
        })
    elif decision == "NON_TARGET":
        record.update({
            "semantic_ready": False,
            "admission_status": "EXCLUDED",
            "target_resolution": "AI_CLASSIFIED_NON_TARGET",
            "target_classification": "NON_TARGET",
            "target_classification_type": None,
            "target_classification_conflict": False,
        })
    else:
        record.update({
            "semantic_ready": False,
            "admission_status": "REVIEW_REQUIRED",
            "target_resolution": "UNRESOLVED",
            "target_classification": "REVIEW_REQUIRED",
            "target_classification_type": None,
            "target_classification_conflict": False,
        })
    return record


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
