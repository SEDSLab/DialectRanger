"""Additive contracts for DOM-first Owner candidate inference.

This module is deliberately a shadow contract.  The current production Owner
extractor does not import it and therefore keeps its existing behaviour.  The
contract makes future candidate generation and deterministic ranking explicit
without turning a profile hint into an Owner decision.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, stable_id, validate_provenance


CANDIDATE_SCHEMA_VERSION = "OwnerCandidate/v1"
RANKING_SCHEMA_VERSION = "OwnerRanking/v1"

OWNER_CANDIDATE_KINDS = (
    "PAGE_OWNER",
    "SECTION_OWNER",
    "CONTAINER_OWNER",
    "HEADING_SECTION_OWNER",
    "TABLE_ROW_OWNER",
    "LIST_ITEM_OWNER",
    "DEFINITION_LIST_OWNER",
    "SIGNATURE_SPAN_OWNER",
    "CARD_OWNER",
)
OWNER_CANDIDATE_STATUSES = ("CANDIDATE", "RANKED", "REJECTED", "AMBIGUOUS")
OWNER_TIERS = ("STRONG", "PROBABLE", "WEAK", "UNSUPPORTED", "AMBIGUOUS")

RANKING_DIMENSIONS = (
    "structural_containment_score",
    "defining_anchor_score",
    "peer_symmetry_score",
    "semantic_completeness_score",
    "boundary_confidence",
    "profile_hint_bonus",
    "example_penalty",
    "contamination_penalty",
    "weak_anchor_penalty",
)


def _normalized_ids(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if value})


def make_owner_candidate(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    owner_kind: str,
    template: str,
    start_block_id: str | None,
    end_block_id: str | None,
    root_block_id: str | None,
    member_block_ids: Iterable[str],
    structural_parent_id: str | None = None,
    structural_parent_kind: str | None = None,
    ancestor_chain: Iterable[Mapping[str, Any] | str] = (),
    sibling_index: int | None = None,
    identity_anchor_ids: Iterable[str] = (),
    identity_shaped_token_ids: Iterable[str] = (),
    structural_signals: Iterable[str] = (),
    profile_hints: Iterable[str] = (),
    ranking_features: Mapping[str, float] | None = None,
    contamination_flags: Iterable[str] = (),
    confidence: float = 0.0,
    tier: str = "WEAK",
    status: str = "CANDIDATE",
    parent_ids: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    """Create one auditable Owner candidate without selecting a winner."""
    if owner_kind not in OWNER_CANDIDATE_KINDS:
        raise ValueError(f"unknown owner candidate kind: {owner_kind}")
    if tier not in OWNER_TIERS:
        raise ValueError(f"unknown owner candidate tier: {tier}")
    if status not in OWNER_CANDIDATE_STATUSES:
        raise ValueError(f"unknown owner candidate status: {status}")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("owner candidate confidence must be between 0 and 1")
    if sibling_index is not None and int(sibling_index) < 0:
        raise ValueError("owner candidate sibling_index must be non-negative")

    member_ids = _normalized_ids(member_block_ids)
    features = {
        name: float((ranking_features or {}).get(name, 0.0))
        for name in RANKING_DIMENSIONS
    }
    if any(not 0 <= value <= 1 for value in features.values()):
        raise ValueError("owner candidate ranking features must be between 0 and 1")
    candidate_id = stable_id(
        "owner_candidate",
        [page_id, owner_kind, template, structural_parent_id, start_block_id,
         end_block_id, sorted(member_ids)],
    )
    value = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "page_id": str(page_id),
        "owner_kind": owner_kind,
        "template": str(template),
        "structural_parent_id": structural_parent_id,
        "structural_parent_kind": structural_parent_kind,
        "ancestor_chain": [dict(item) if isinstance(item, Mapping) else str(item) for item in ancestor_chain],
        "sibling_index": sibling_index,
        "start_block_id": start_block_id,
        "end_block_id": end_block_id,
        "root_block_id": root_block_id,
        "member_block_ids": member_ids,
        "identity_anchor_ids": _normalized_ids(identity_anchor_ids),
        "identity_shaped_token_ids": _normalized_ids(identity_shaped_token_ids),
        "structural_signals": _normalized_ids(structural_signals),
        "profile_hints": _normalized_ids(profile_hints),
        "ranking_features": features,
        "contamination_flags": _normalized_ids(contamination_flags),
        "confidence": float(confidence),
        "tier": tier,
        "status": status,
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="owner_candidate_inference",
            producer_version="owner-candidate-contract/v1",
            parent_ids=parent_ids, reason_codes=reason_codes,
        ),
    }
    validate_owner_candidate(value)
    return value


def validate_owner_candidate(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        (
            "schema_version", "candidate_id", "page_id", "owner_kind", "template",
            "structural_parent_id", "structural_parent_kind", "ancestor_chain",
            "sibling_index", "start_block_id", "end_block_id", "root_block_id",
            "member_block_ids", "identity_anchor_ids", "identity_shaped_token_ids",
            "structural_signals", "profile_hints", "ranking_features",
            "contamination_flags", "confidence", "tier", "status", "provenance",
        ),
        "OwnerCandidate",
    )
    if value["schema_version"] != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("OwnerCandidate schema_version must be OwnerCandidate/v1")
    if value["owner_kind"] not in OWNER_CANDIDATE_KINDS:
        raise ValueError(f"unknown owner candidate kind: {value['owner_kind']}")
    if value["tier"] not in OWNER_TIERS:
        raise ValueError(f"unknown owner candidate tier: {value['tier']}")
    if value["status"] not in OWNER_CANDIDATE_STATUSES:
        raise ValueError(f"unknown owner candidate status: {value['status']}")
    if not 0 <= float(value["confidence"]) <= 1:
        raise ValueError("OwnerCandidate.confidence must be between 0 and 1")
    if value["sibling_index"] is not None and int(value["sibling_index"]) < 0:
        raise ValueError("OwnerCandidate.sibling_index must be non-negative")
    if not isinstance(value["ancestor_chain"], list):
        raise ValueError("OwnerCandidate.ancestor_chain must be a list")
    if not isinstance(value["member_block_ids"], list):
        raise ValueError("OwnerCandidate.member_block_ids must be a list")
    features = value["ranking_features"]
    if not isinstance(features, Mapping):
        raise ValueError("OwnerCandidate.ranking_features must be an object")
    missing = set(RANKING_DIMENSIONS) - set(features)
    if missing:
        raise ValueError(f"OwnerCandidate.ranking_features missing: {sorted(missing)}")
    if any(not 0 <= float(features[name]) <= 1 for name in RANKING_DIMENSIONS):
        raise ValueError("OwnerCandidate ranking features must be between 0 and 1")
    validate_provenance(value["provenance"], label="OwnerCandidate.provenance")
    if value["provenance"]["page_id"] != value["page_id"]:
        raise ValueError("OwnerCandidate provenance page_id mismatch")


def deterministic_candidate_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the frozen shadow ranking order.

    Structural DOM evidence is deliberately compared before profile hints and
    penalties.  This function is still shadow-only; it does not select a
    production Owner.
    """
    validate_owner_candidate(value)
    features = value["ranking_features"]
    return (
        -float(features["structural_containment_score"]),
        -float(features["defining_anchor_score"]),
        -float(features["peer_symmetry_score"]),
        -float(features["semantic_completeness_score"]),
        -float(features["boundary_confidence"]),
        -float(features["profile_hint_bonus"]),
        float(features["example_penalty"]),
        float(features["contamination_penalty"]),
        float(features["weak_anchor_penalty"]),
        -float(value["confidence"]),
        str(value["owner_kind"]),
        str(value["start_block_id"] or ""),
        str(value["end_block_id"] or ""),
        str(value["candidate_id"]),
    )


def owner_candidate_schema() -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_kinds": list(OWNER_CANDIDATE_KINDS),
        "required": [
            "candidate_id", "page_id", "owner_kind", "template",
            "structural_parent_id", "structural_parent_kind", "ancestor_chain",
            "sibling_index", "start_block_id", "end_block_id", "root_block_id",
            "member_block_ids", "identity_anchor_ids", "identity_shaped_token_ids",
            "structural_signals", "profile_hints", "ranking_features",
            "contamination_flags", "confidence", "tier", "status", "provenance",
        ],
        "selection_authority": "deterministic_owner_ranking",
        "ranking_dimensions": list(RANKING_DIMENSIONS),
    }


def owner_ranking_schema() -> dict[str, Any]:
    return {
        "schema_version": RANKING_SCHEMA_VERSION,
        "dimensions": list(RANKING_DIMENSIONS),
        "profile_hints_are_decisive": False,
        "tie_breaker": ["owner_kind", "start_block_id", "end_block_id", "candidate_id"],
    }
