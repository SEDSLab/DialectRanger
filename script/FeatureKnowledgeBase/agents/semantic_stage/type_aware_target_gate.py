"""Generic type-aware Target Gate primitives.

This module is deliberately independent of any DBMS, official inventory, or
feature-name vocabulary.  It derives a review stratum from the frozen
owner-local Type Evidence contract and provides the one routing/normalization
implementation used by production and bounded validation.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from agents.semantic_stage.target_admission import TARGET_TYPES


STRONG_CONFIRMED = "STRONG_CONFIRMED"
RISKY_CONFIRMED = "RISKY_CONFIRMED"
AMBIGUOUS_CONFIRMED = "AMBIGUOUS_CONFIRMED"

EXTERNAL_CONTEXT_CATEGORIES = {"ENGINE_OR_INTEGRATION", "FORMAT_OR_PROTOCOL", "CONFIGURATION"}
METADATA_CONTEXT_CATEGORIES = {"PARAMETER_OR_FIELD", "OTHER"}
EXPLICIT_RISK_REASONS = {
    "TYPE_CONFLICT_RISK",
    "EXTERNAL_MAPPING_CONTEXT",
    "API_METHOD_CONTEXT",
    "METADATA_CONTEXT",
    "CROSS_SURFACE_CONFLICT",
    "IDENTITY_OVER_ACCEPT_RISK",
}
FIRST_RISK_ORDER = [
    "OWNER_SCOPE_RISK",
    "CROSS_SURFACE_CONFLICT",
    "IDENTITY_OVER_ACCEPT_RISK",
    "TYPE_CONFLICT_RISK",
    "EXTERNAL_MAPPING_CONTEXT",
    "API_METHOD_CONTEXT",
    "METADATA_CONTEXT",
    "WEAK_EVIDENCE_CONTEXT",
    "UNKNOWN",
]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _target_evidence(group: Mapping[str, Any], evidence_rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    feature_type = _upper(group.get("feature_type"))
    return [
        row for row in evidence_rows
        if _upper(row.get("candidate_type")) == feature_type
        and bool(row.get("owner_local_binding"))
        and _upper(row.get("strength")) == "STRONG"
        and _upper(row.get("subject_alignment")) in {"EXACT_HEAD", "FEATURE_SLOT"}
        and _upper(row.get("evidence_kind")) != "PAGE_CONTEXT_ONLY"
    ]


def derive_risk(group: Mapping[str, Any], owners_by_id: Mapping[str, Mapping[str, Any]],
                typed_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive a deterministic risk stratum from owner-local frozen evidence.

    The rule intentionally mirrors the prior audit contract, but is now a
    production primitive.  It never consults an inventory or identity-name
    list and never changes the upstream TypeDecision.
    """
    feature_type = _upper(group.get("feature_type"))
    owner_rows = [
        owners_by_id[owner_id]
        for owner_id in group.get("owner_ids") or []
        if owner_id in owners_by_id
    ]
    targets = _target_evidence(group, typed_rows)
    non_targets = [
        row for row in typed_rows
        if _upper(row.get("candidate_type")) == "NON_TARGET"
        and bool(row.get("owner_local_binding"))
    ]
    target_owner_ids = {_text(row.get("owner_id")) for row in targets if _text(row.get("owner_id"))}
    same_owner_non_targets = [row for row in non_targets if _text(row.get("owner_id")) in target_owner_ids]
    same_owner_conflicts = [
        row for row in same_owner_non_targets
        if _upper(row.get("non_target_category")) != "EXAMPLE_IDENTIFIER"
    ]

    reasons: list[str] = []
    reason_support: dict[str, dict[str, Any]] = {}

    def add_reason(code: str, *, evidence: Sequence[Mapping[str, Any]] = (),
                   owner_ids: Sequence[str] = (), detail: str = "") -> None:
        if code not in reasons:
            reasons.append(code)
        slot = reason_support.setdefault(code, {"evidence_ids": [], "owner_ids": [], "detail": detail})
        slot["evidence_ids"] = sorted(set(slot["evidence_ids"]) | {
            _text(row.get("evidence_id")) for row in evidence if _text(row.get("evidence_id"))
        })
        slot["owner_ids"] = sorted(set(slot["owner_ids"]) | {
            _text(value) for value in owner_ids if _text(value)
        })
        if detail and not slot.get("detail"):
            slot["detail"] = detail

    if same_owner_conflicts:
        add_reason(
            "TYPE_CONFLICT_RISK", evidence=same_owner_conflicts,
            owner_ids=sorted(target_owner_ids),
            detail="Owner-local target and non-target evidence coexist; example-only identifiers are excluded.",
        )

    external_rows = [
        row for row in non_targets
        if _upper(row.get("non_target_category")) in EXTERNAL_CONTEXT_CATEGORIES
    ]
    if external_rows:
        add_reason(
            "EXTERNAL_MAPPING_CONTEXT", evidence=external_rows,
            detail="Typed evidence carries engine/integration, format/protocol, or configuration context.",
        )

    metadata_rows = [
        row for row in non_targets
        if _upper(row.get("non_target_category")) in METADATA_CONTEXT_CATEGORIES
    ]
    if metadata_rows:
        add_reason(
            "METADATA_CONTEXT", evidence=metadata_rows,
            detail="Owner-local evidence also contains parameter/field or other non-target context.",
        )

    target_kinds = {_upper(row.get("evidence_kind")) for row in targets}
    callable_kinds = {"CALLABLE_SIGNATURE", "FUNCTION_SLOT", "OPERATOR_SLOT"}
    if feature_type == "DATATYPE" and target_kinds & callable_kinds:
        add_reason(
            "API_METHOD_CONTEXT",
            evidence=[row for row in targets if _upper(row.get("evidence_kind")) in callable_kinds],
            detail="Datatype evidence is backed by a callable/operator slot rather than only a declaration surface.",
        )
    if feature_type == "FUNCTION" and "FUNCTION_SLOT" in target_kinds and "CALLABLE_SIGNATURE" not in target_kinds:
        add_reason(
            "API_METHOD_CONTEXT",
            evidence=[row for row in targets if _upper(row.get("evidence_kind")) == "FUNCTION_SLOT"],
            detail="Function evidence has only a generic function slot and no callable signature.",
        )

    cross_surface_owners = [
        owner for owner in owner_rows
        if bool(owner.get("foreign_identity_values"))
        or bool(owner.get("multi_identity_owner"))
        or len(owner.get("identity_values") or []) > 1
    ]
    if cross_surface_owners:
        add_reason(
            "CROSS_SURFACE_CONFLICT",
            owner_ids=[_text(owner.get("owner_id")) for owner in cross_surface_owners],
            detail="Owner metadata contains foreign identity values or an explicitly multi-identity surface.",
        )

    if feature_type == "DATATYPE" and "DATATYPE_SLOT" in target_kinds and "DATATYPE_DECLARATION" not in target_kinds:
        add_reason(
            "IDENTITY_OVER_ACCEPT_RISK",
            evidence=[row for row in targets if _upper(row.get("evidence_kind")) == "DATATYPE_SLOT"],
            detail="Datatype identity has a generic slot but no declaration surface.",
        )
    if feature_type == "FUNCTION" and "FUNCTION_SLOT" in target_kinds and "CALLABLE_SIGNATURE" not in target_kinds:
        add_reason(
            "IDENTITY_OVER_ACCEPT_RISK",
            evidence=[row for row in targets if _upper(row.get("evidence_kind")) == "FUNCTION_SLOT"],
            detail="Function identity has a generic slot but no callable signature surface.",
        )

    bounded_target = [row for row in targets if _upper(row.get("owner_scope_locality")) == "BOUNDED"]
    if targets and not bounded_target:
        add_reason(
            "OWNER_SCOPE_RISK", evidence=targets,
            owner_ids=[_text(row.get("owner_id")) for row in targets],
            detail="Target evidence is attached to broad scope; no bounded target owner is available.",
        )

    occurrence_roles = group.get("occurrence_roles") or {}
    structural_roles = {_upper(value) for value in group.get("structural_roles") or []}
    if int(occurrence_roles.get("DEFINING_IDENTITY") or 0) == 0:
        add_reason("WEAK_EVIDENCE_CONTEXT", detail="No defining identity occurrence is recorded.")
    if structural_roles and structural_roles <= {"PROSE"}:
        add_reason("PAGE_CONTEXT_ONLY", detail="Structural roles contain only prose context.")

    stable_local_proof = bool(targets) and bool(bounded_target) and bool(
        int(occurrence_roles.get("DEFINING_IDENTITY") or 0) > 0
        or structural_roles & {"SIGNATURE", "FEATURE_NAME"}
    )
    if any(reason in EXPLICIT_RISK_REASONS for reason in reasons):
        stratum = RISKY_CONFIRMED
    elif reasons or not stable_local_proof:
        stratum = AMBIGUOUS_CONFIRMED
    else:
        stratum = STRONG_CONFIRMED

    return {
        "identity_unit_id": _text(group.get("identity_unit_id")),
        "feature_type": feature_type,
        "risk_stratum": stratum,
        "risk_reason_codes": sorted(set(reasons)),
        "risk_reason_support": reason_support,
        "first_risk_source": next((item for item in FIRST_RISK_ORDER if item in reasons), "UNKNOWN"),
        "stable_owner_local_proof": stable_local_proof,
        "target_evidence_ids": sorted({_text(row.get("evidence_id")) for row in targets if _text(row.get("evidence_id"))}),
        "target_evidence_count": len(targets),
        "target_evidence_kinds": sorted(target_kinds),
        "target_owner_ids": sorted(target_owner_ids),
        "bounded_target_owner_ids": sorted({_text(row.get("owner_id")) for row in bounded_target if _text(row.get("owner_id"))}),
        "same_owner_non_target_evidence_ids": sorted({_text(row.get("evidence_id")) for row in same_owner_non_targets if _text(row.get("evidence_id"))}),
        "same_owner_conflict_evidence_ids": sorted({_text(row.get("evidence_id")) for row in same_owner_conflicts if _text(row.get("evidence_id"))}),
        "non_target_evidence_ids": sorted({_text(row.get("evidence_id")) for row in non_targets if _text(row.get("evidence_id"))}),
        "non_target_categories": sorted({_text(row.get("non_target_category")) for row in non_targets if _text(row.get("non_target_category"))}),
        "owner_ids": sorted({_text(owner.get("owner_id")) for owner in owner_rows if _text(owner.get("owner_id"))}),
        "owner_locality": sorted({_text(owner.get("owner_scope_locality")) for owner in owner_rows if _text(owner.get("owner_scope_locality"))}),
        "owner_kinds": sorted({_text(owner.get("owner_kind")) for owner in owner_rows if _text(owner.get("owner_kind"))}),
        "structural_roles": sorted(structural_roles),
        "occurrence_roles": dict(occurrence_roles),
    }


def route_for_confirmed(group: Mapping[str, Any], risk_row: Mapping[str, Any]) -> str:
    """Return the one deterministic route for a confirmed target group."""
    feature_type = _upper(group.get("feature_type") or risk_row.get("feature_type"))
    stratum = _upper(risk_row.get("risk_stratum"))
    if feature_type == "FUNCTION" and stratum == STRONG_CONFIRMED:
        return "DIRECT_STRONG_FUNCTION"
    if feature_type == "FUNCTION":
        return "CLASSIFY_RISKY_OR_AMBIGUOUS_FUNCTION"
    if feature_type == "OPERATOR":
        return "CLASSIFY_CONFIRMED_OPERATOR"
    if feature_type == "DATATYPE":
        return "CLASSIFY_CONFIRMED_DATATYPE"
    return "REVIEW_UNSUPPORTED_CONFIRMED_TYPE"


def normalize_confirmed_classifier_result(
    group: Mapping[str, Any], base_admission: Mapping[str, Any],
    applied_admission: Mapping[str, Any], parsed_decision: str, reason: str,
) -> dict[str, Any]:
    """Apply target classification without rewriting the frozen TypeDecision."""
    original_type = _upper(group.get("feature_type")) or "UNCERTAIN"
    decision = _upper(parsed_decision) or "UNRESOLVED"
    if decision == "NON_TARGET":
        state, admission_status, target_resolution = "NON_TARGET", "EXCLUDED", "AI_CLASSIFIED_NON_TARGET"
    elif decision == original_type and decision in TARGET_TYPES and applied_admission.get("admission_status") == "ADMITTED":
        state, admission_status, target_resolution = "TARGET", "ADMITTED", "AI_CLASSIFIED"
    elif decision in TARGET_TYPES:
        state, admission_status, target_resolution = "REVIEW_REQUIRED", "REVIEW_REQUIRED", "UNRESOLVED_TYPE_CONFLICT"
    else:
        state, admission_status, target_resolution = "REVIEW_REQUIRED", "REVIEW_REQUIRED", "UNRESOLVED"
    result = dict(base_admission)
    result.update({
        "group_status": group.get("group_status"),
        "previous_type": original_type,
        "feature_type": original_type,
        "admission_status": admission_status,
        "semantic_ready": state == "TARGET",
        "target_resolution": target_resolution,
        "target_classification": "TARGET" if state == "TARGET" else state,
        "target_classification_type": decision if decision in TARGET_TYPES else None,
        "ai_decision": decision,
        "target_classification_conflict": decision in TARGET_TYPES and decision != original_type,
        "reason": reason or str(applied_admission.get("reason") or ""),
        "gate_state": state,
        # Kept as a compatibility alias for bounded audit artifacts.
        "evaluation_gate_state": state,
    })
    return result


__all__ = [
    "AMBIGUOUS_CONFIRMED", "RISKY_CONFIRMED", "STRONG_CONFIRMED",
    "derive_risk", "normalize_confirmed_classifier_result", "route_for_confirmed",
]
