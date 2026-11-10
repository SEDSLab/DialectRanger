"""Deterministic TypeDecision/v1 aggregation."""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any, Dict, List, Mapping, Sequence

from .evidence import ALIGN_EXACT, ALIGN_SLOT, NON_TARGET, STRONG, TARGET_TYPES, TARGET_UNCERTAIN


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _strong_target(row: Mapping[str, Any]) -> bool:
    return (
        row.get("definition_vote_eligible", True) is not False
        and
        _clean(row.get("strength")) == STRONG
        and _clean(row.get("candidate_type")) in TARGET_TYPES
        and _clean(row.get("subject_alignment")) in {ALIGN_EXACT, ALIGN_SLOT}
    )


def _strong_negative(row: Mapping[str, Any]) -> bool:
    # Example and parameter occurrences are useful diagnostics, but they are
    # not evidence that the IdentityUnit itself is a non-feature.  Only a
    # strong owner-local non-target slot may veto a defining target surface.
    return (
        _clean(row.get("strength")) == STRONG
        and _clean(row.get("candidate_type")) == NON_TARGET
        and _clean(row.get("subject_alignment")) == "FEATURE_SLOT"
        and _clean(row.get("evidence_kind")) == "NON_TARGET_OWNER_SLOT"
    )


def _target_weight(row: Mapping[str, Any]) -> int:
    """Score one owner-local observation without using feature names.

    Evidence kind is only a structural specificity bonus.  It is not a
    database vocabulary and therefore remains valid for an unseen DBMS.
    """

    weight = 3
    if _clean(row.get("subject_alignment")) == ALIGN_EXACT:
        weight += 2
    elif _clean(row.get("subject_alignment")) == ALIGN_SLOT:
        weight += 1
    if _clean(row.get("evidence_kind")) in {
        "DATATYPE_DECLARATION", "DATATYPE_SLOT", "OPERATOR_SLOT", "CALLABLE_SIGNATURE",
    }:
        weight += 2
    return weight


def _explicit_datatype_anchor(row: Mapping[str, Any]) -> bool:
    """Identify a local datatype declaration surface without a DB vocabulary.

    An exact heading in a type/declaration context is stronger than repeated
    incidental mentions from connector mappings or callable examples.  This
    helper intentionally does not inspect URLs, feature names, or inventories.
    It is only used to resolve a bounded type conflict after owner-local
    evidence has already been constructed.
    """

    if (
        _clean(row.get("candidate_type")) != "DATATYPE"
        or _clean(row.get("strength")) != STRONG
        or _clean(row.get("subject_alignment")) != "EXACT_HEAD"
    ):
        return False
    if _clean(row.get("evidence_kind")) == "DATATYPE_DECLARATION":
        return True
    heading = " | ".join(_clean(value) for value in (row.get("heading_ancestry") or []))
    return bool(re.search(
        r"\b(?:data[- ]?types?|datatype|type\s+(?:system|declaration|definition|name|constructor|mapping|support))\b",
        heading,
        re.I,
    ))


def decide_type(
    identity: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Aggregate owner-local typed evidence without using P2.8 ScopeDecision."""

    unit_id = _clean(identity.get("identity_unit_id"))
    rejected_binding = [row for row in evidence if row.get("owner_local_binding") is False]
    bound_evidence = [row for row in evidence if row.get("owner_local_binding", True) is not False]
    # An owner/evidence id is lineage, not subject proof.  Scope-rejected
    # observations remain available for audit but cannot vote on TypeDecision.
    target = [row for row in bound_evidence if _strong_target(row)]
    uncertain = [
        row for row in bound_evidence
        if _clean(row.get("candidate_type")) == TARGET_UNCERTAIN
        and _clean(row.get("strength")) in {"STRONG", "MEDIUM"}
    ]
    negative = [row for row in bound_evidence if _strong_negative(row)]
    by_type: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in target:
        by_type[_clean(row.get("candidate_type"))].append(row)
    target_types = sorted(by_type)
    owner_type_scores: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in target:
        owner_id = _clean(row.get("owner_id"))
        candidate_type = _clean(row.get("candidate_type"))
        if owner_id and candidate_type:
            owner_type_scores[owner_id][candidate_type] += _target_weight(row)

    type_scores = {
        candidate_type: sum(
            owner_type_scores[owner_id].get(candidate_type, 0)
            for owner_id in owner_type_scores
        )
        for candidate_type in target_types
    }
    conflict_same_owner = False
    conflict_owners: List[str] = []
    for owner_id in sorted(owner_type_scores):
        roles = {
            candidate_type for candidate_type, score in owner_type_scores[owner_id].items()
            if score > 0
        }
        if len(roles) > 1:
            conflict_same_owner = True
            conflict_owners.append(owner_id)
    negative_owners = {_clean(row.get("owner_id")) for row in negative}
    positive_owners = {_clean(row.get("owner_id")) for row in target}
    same_owner_negative = sorted(positive_owners & negative_owners)
    datatype_anchor_rows = [row for row in target if _explicit_datatype_anchor(row)]
    datatype_anchor_owners = sorted({
        _clean(row.get("owner_id")) for row in datatype_anchor_rows
        if _clean(row.get("owner_id"))
    })
    # A same-owner callable signature is a genuine ambiguity (for example a
    # page that documents both a type and a function with the same surface).
    # Operator-only observations in an exact type owner are weaker than the
    # declaration surface and may be retained as an alternate diagnostic.
    datatype_anchor_callable_conflicts = sorted({
        _clean(row.get("owner_id"))
        for row in target
        if _clean(row.get("owner_id")) in datatype_anchor_owners
        and _clean(row.get("candidate_type")) == "FUNCTION"
    })
    datatype_anchor_can_resolve = bool(datatype_anchor_rows) and not datatype_anchor_callable_conflicts
    reasons: List[str] = []
    alternate: List[str] = []
    if target_types:
        primary = sorted(target_types, key=lambda key: (-type_scores[key], key))[0]
        alternate = [value for value in target_types if value != primary]
        if datatype_anchor_can_resolve and not (
            set(datatype_anchor_owners) & set(same_owner_negative)
        ):
            # Prefer a precise, owner-local declaration over a cross-owner
            # type collision.  Keep the result reviewable whenever another
            # target type is present; the evidence and alternate types remain
            # in the decision for downstream audit.
            primary_type = "DATATYPE"
            if alternate or conflict_same_owner:
                status = "REVIEW_REQUIRED"
                reasons.extend([
                    "EXACT_DATATYPE_OWNER_EVIDENCE",
                    "DATATYPE_DECLARATION_PRIORITY",
                    "TYPE_REVIEW_REQUIRED",
                ])
            else:
                status = "CONFIRMED"
                reasons.append("EXACT_DATATYPE_OWNER_EVIDENCE")
        elif conflict_same_owner:
            status = "REVIEW_REQUIRED"
            primary_type = TARGET_UNCERTAIN
            reasons.extend(["SAME_OWNER_TYPE_CONFLICT", "TYPE_REVIEW_REQUIRED"])
        elif same_owner_negative:
            status = "REVIEW_REQUIRED"
            primary_type = primary
            reasons.extend(["SAME_OWNER_TARGET_NON_TARGET_CONFLICT", "TYPE_REVIEW_REQUIRED"])
        elif alternate:
            # A unit with strong evidence for two types is not made correct by
            # counting rows.  Keep the strongest type as a review hypothesis,
            # but make the ambiguity explicit and preserve both evidence sets.
            status = "REVIEW_REQUIRED"
            primary_type = primary
            reasons.extend(["CROSS_OWNER_TYPE_CONFLICT", "TYPE_REVIEW_REQUIRED"])
        else:
            status = "CONFIRMED"
            primary_type = primary
            reasons.append("STRONG_OWNER_LOCAL_TYPED_EVIDENCE")
            if negative:
                reasons.append("UNRELATED_NON_TARGET_OWNER_IGNORED")
    elif uncertain:
        status = "REVIEW_REQUIRED"
        primary_type = TARGET_UNCERTAIN
        reasons.extend(["CREDIBLE_SQL_FEATURE_WITHOUT_SAFE_TYPE", "TYPE_REVIEW_REQUIRED"])
    elif negative:
        status = "NON_TARGET"
        primary_type = "OTHER"
        category_counts = Counter(_clean(row.get("non_target_category")) or "OTHER" for row in negative)
        reasons.extend(["STRONG_OWNER_LOCAL_NON_TARGET_EVIDENCE", category_counts.most_common(1)[0][0]])
    else:
        status = "REVIEW_REQUIRED"
        primary_type = TARGET_UNCERTAIN
        reasons.extend(["INSUFFICIENT_OWNER_LOCAL_TYPE_EVIDENCE", "TYPE_REVIEW_REQUIRED"])
    confidence = 0.96 if status == "CONFIRMED" else 0.78 if status == "NON_TARGET" else 0.58
    defining_types = {_clean(row.get("candidate_type")) for row in target if row.get("evidence_role") == "DEFINITION_SUPPORT"}
    if len(defining_types) > 1:
        status = "REVIEW_REQUIRED"
        reasons.extend(["SUBJECT_TYPE_CONFLICT", "TYPE_REVIEW_REQUIRED"])
        confidence = 0.58
    if any(row.get("definition_vote_eligible") is False and _clean(row.get("candidate_type")) in TARGET_TYPES for row in evidence):
        reasons.append("USAGE_TYPE_VOTES_EXCLUDED")
    if status == "REVIEW_REQUIRED" and alternate:
        confidence = 0.62
    if status == "REVIEW_REQUIRED" and not target and not uncertain:
        confidence = 0.42
    supporting = [str(row.get("evidence_id")) for row in target or uncertain if row.get("evidence_id")]
    conflicting = [str(row.get("evidence_id")) for row in negative if row.get("evidence_id")]
    return {
        "schema_version": "TypeDecision/v1",
        "type_decision_id": "type_decision:" + unit_id.split(":", 1)[-1],
        "identity_unit_id": unit_id,
        "type_status": status,
        "primary_type": primary_type,
        "alternate_types": alternate,
        "supporting_evidence_ids": sorted(set(supporting)),
        "conflicting_evidence_ids": sorted(set(conflicting)),
        "reason_codes": list(dict.fromkeys(
            reasons + (["OWNER_LOCAL_EVIDENCE_REJECTED"] if rejected_binding else [])
        )),
        "confidence": confidence,
        "provenance": {
            "identity_unit_id": unit_id,
            "owner_ids": sorted({_clean(row.get("owner_id")) for row in evidence if _clean(row.get("owner_id"))}),
            "supporting_owner_ids": sorted(positive_owners),
            "conflicting_owner_ids": sorted(negative_owners),
            "same_owner_conflict_ids": same_owner_negative + conflict_owners,
        },
        "evidence_summary": {
            "definition_vote_excluded_ids": sorted(str(row.get("evidence_id")) for row in evidence if row.get("definition_vote_eligible") is False),
            "evidence_role_counts": dict(Counter(str(row.get("evidence_role") or "LEGACY_UNINTERPRETED") for row in evidence)),
            "type_scores": dict(sorted(type_scores.items())),
            "owner_type_scores": {
                owner_id: dict(sorted(scores.items()))
                for owner_id, scores in sorted(owner_type_scores.items())
            },
            "owner_local_binding_rejected": len(rejected_binding),
        },
    }


__all__ = ["decide_type"]
