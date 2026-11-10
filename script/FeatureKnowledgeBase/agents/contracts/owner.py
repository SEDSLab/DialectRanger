"""OwnerHypothesis/v1 contract."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, stable_id, validate_provenance
from agents.contracts.status import STATUS_REGISTRY, validate_status


SCHEMA_VERSION = "OwnerHypothesis/v1"
OWNER_STATUSES = ("STRONG", "PROBABLE", "WEAK", "UNSUPPORTED", "AMBIGUOUS")


def make_owner_hypothesis(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    start_block_id: str | None,
    end_block_id: str | None,
    root_block_id: str | None,
    member_block_ids: Iterable[str],
    owner_kind: str,
    identity_signals: Iterable[str] = (),
    confidence: float = 0.0,
    tier: str = "WEAK",
    template: str = "unknown",
    status: str = "WEAK",
    parent_ids: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    member_block_ids = [str(item) for item in member_block_ids if item]
    if tier not in OWNER_STATUSES:
        raise ValueError(f"unknown owner tier: {tier}")
    if status not in OWNER_STATUSES:
        raise ValueError(f"unknown owner status: {status}")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("owner confidence must be between 0 and 1")
    owner_id = stable_id(
        "owner", [page_id, start_block_id, end_block_id, owner_kind, sorted(member_block_ids)],
    )
    value = {
        "schema_version": SCHEMA_VERSION,
        "owner_id": owner_id,
        "page_id": page_id,
        "start_block_id": start_block_id,
        "end_block_id": end_block_id,
        "root_block_id": root_block_id,
        "member_block_ids": sorted(set(member_block_ids)),
        "owner_kind": owner_kind,
        "identity_signals": sorted({str(item) for item in identity_signals if item}),
        "confidence": float(confidence),
        "tier": tier,
        "template": template,
        "status": status,
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="owner_hypothesis", producer_version="owner-contract/v1",
            parent_ids=parent_ids, reason_codes=reason_codes,
        ),
    }
    validate_owner_hypothesis(value)
    return value


def validate_owner_hypothesis(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "owner_id", "page_id", "start_block_id", "end_block_id",
         "root_block_id", "member_block_ids", "owner_kind", "identity_signals",
         "confidence", "tier", "template", "status", "provenance"),
        "OwnerHypothesis",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("OwnerHypothesis schema_version must be OwnerHypothesis/v1")
    validate_provenance(value["provenance"], label="OwnerHypothesis.provenance")
    if value["tier"] not in OWNER_STATUSES:
        raise ValueError(f"unknown owner tier: {value['tier']}")
    if value["status"] not in OWNER_STATUSES:
        raise ValueError(f"unknown owner status: {value['status']}")
