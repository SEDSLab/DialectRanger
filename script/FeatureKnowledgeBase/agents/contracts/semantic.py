"""SemanticRecord/v2 contract."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, stable_id, validate_provenance
from agents.contracts.status import validate_status


SCHEMA_VERSION = "SemanticRecord/v2"


def make_semantic_record(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    identity_unit_id: str,
    claims: Iterable[Mapping[str, Any]] = (),
    status: str = "UNAVAILABLE",
    parent_ids: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    validate_status(status, "semantic")
    normalized_claims = []
    for claim in claims:
        claim = dict(claim)
        claim.setdefault("claim_id", stable_id(
            "claim", [identity_unit_id, claim.get("evidence_id"), claim.get("role"), claim.get("value")],
        ))
        claim.setdefault("status", "OBSERVED")
        claim.setdefault("provenance", {
            "evidence_id": claim.get("evidence_id"),
            "role": claim.get("role"),
        })
        normalized_claims.append(claim)
    value = {
        "schema_version": SCHEMA_VERSION,
        "semantic_record_id": stable_id("semantic_record", [identity_unit_id, normalized_claims]),
        "identity_unit_id": identity_unit_id,
        "claims": normalized_claims,
        "status": status,
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="semantic_extraction",
            producer_version="semantic-contract/v2",
            parent_ids=parent_ids, reason_codes=reason_codes,
        ),
    }
    validate_semantic_record(value)
    return value


def validate_semantic_record(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "semantic_record_id", "identity_unit_id",
         "claims", "status", "provenance"),
        "SemanticRecord",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("SemanticRecord schema_version must be SemanticRecord/v2")
    validate_status(value["status"], "semantic")
    validate_provenance(value["provenance"], label="SemanticRecord.provenance")
    for claim in value["claims"]:
        require_fields(claim, ("claim_id", "evidence_id", "role", "value", "status", "provenance"), "SemanticRecord.claim")
