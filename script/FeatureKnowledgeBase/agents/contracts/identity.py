"""IdentityCandidate/v2 and IdentityUnit/v1 contracts."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, stable_id, validate_provenance
from agents.contracts.status import validate_status


CANDIDATE_SCHEMA_VERSION = "IdentityCandidate/v2"
UNIT_SCHEMA_VERSION = "IdentityUnit/v1"


def make_identity_candidate(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    raw_identity: str,
    normalized_identity: str,
    owner_id: str,
    source_block_ids: Iterable[str],
    recall_tier: str = "WEAK",
    validation_status: str = "UNVALIDATED",
    identity_signals: Iterable[str] = (),
    risk_signals: Iterable[str] = (),
    parent_ids: Iterable[str] = (),
    source_candidate_id: str | None = None,
) -> dict[str, Any]:
    source_block_ids = [str(item) for item in source_block_ids if item]
    validate_status(recall_tier, "candidate")
    candidate_id = stable_id(
        "candidate",
        [page_id, owner_id, normalized_identity, sorted(source_block_ids)],
    )
    value = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "raw_identity": raw_identity,
        "normalized_identity": normalized_identity,
        "owner_id": owner_id,
        "page_id": page_id,
        "source_block_ids": sorted(set(source_block_ids)),
        "recall_tier": recall_tier,
        "validation_status": validation_status,
        "identity_signals": sorted({str(item) for item in identity_signals if item}),
        "risk_signals": sorted({str(item) for item in risk_signals if item}),
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="identity_candidate",
            producer_version="identity-contract/v2",
            parent_ids=parent_ids,
            reason_codes=["LEGACY_SOURCE_CANDIDATE_ADAPTED"] if source_candidate_id else (),
        ),
        "source_candidate_id": source_candidate_id,
    }
    validate_identity_candidate(value)
    return value


def validate_identity_candidate(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "candidate_id", "raw_identity", "normalized_identity",
         "owner_id", "page_id", "source_block_ids", "recall_tier",
         "validation_status", "identity_signals", "risk_signals", "provenance"),
        "IdentityCandidate",
    )
    if value["schema_version"] != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("IdentityCandidate schema_version must be IdentityCandidate/v2")
    validate_status(value["recall_tier"], "candidate")
    validate_provenance(value["provenance"], label="IdentityCandidate.provenance")
    if not value["normalized_identity"].strip():
        raise ValueError("IdentityCandidate normalized_identity must not be empty")


def make_identity_unit(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    canonical_identity: str,
    candidate_ids: Iterable[str],
    owner_ids: Iterable[str],
    variants: Iterable[str] = (),
    aliases: Iterable[str] = (),
    validation_status: str = "VALIDATED",
    merge_decisions: Iterable[dict[str, Any]] = (),
    rejected_merge_ids: Iterable[str] = (),
    parent_ids: Iterable[str] = (),
) -> dict[str, Any]:
    candidate_ids = sorted({str(item) for item in candidate_ids if item})
    owner_ids = sorted({str(item) for item in owner_ids if item})
    unit_id = stable_id("identity_unit", [canonical_identity, candidate_ids])
    value = {
        "schema_version": UNIT_SCHEMA_VERSION,
        "identity_unit_id": unit_id,
        "canonical_identity": canonical_identity,
        "candidate_ids": candidate_ids,
        "owner_ids": owner_ids,
        "variants": sorted({str(item) for item in variants if item}),
        "aliases": sorted({str(item) for item in aliases if item}),
        "validation_status": validation_status,
        "merge_decisions": list(merge_decisions),
        "rejected_merge_ids": sorted({str(item) for item in rejected_merge_ids if item}),
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="identity_unit",
            producer_version="identity-unit-contract/v1",
            parent_ids=parent_ids,
        ),
    }
    validate_identity_unit(value)
    return value


def validate_identity_unit(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "identity_unit_id", "canonical_identity", "candidate_ids",
         "owner_ids", "variants", "aliases", "validation_status",
         "merge_decisions", "rejected_merge_ids", "provenance"),
        "IdentityUnit",
    )
    if value["schema_version"] != UNIT_SCHEMA_VERSION:
        raise ValueError("IdentityUnit schema_version must be IdentityUnit/v1")
    validate_provenance(value["provenance"], label="IdentityUnit.provenance")
    if not value["candidate_ids"]:
        raise ValueError("IdentityUnit must retain at least one candidate_id")
