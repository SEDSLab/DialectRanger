"""TypeDecision/v1 contract."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, validate_provenance


SCHEMA_VERSION = "TypeDecision/v1"
FEATURE_TYPES = ("FUNCTION", "OPERATOR", "DATATYPE", "UNCERTAIN")


def make_type_decision(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    identity_unit_id: str,
    primary_type: str = "UNCERTAIN",
    semantic_roles: Iterable[str] = (),
    type_evidence_ids: Iterable[str] = (),
    confidence: float = 0.0,
    conflict_flags: Iterable[str] = (),
    status: str = "INFERRED",
    parent_ids: Iterable[str] = (),
) -> dict[str, Any]:
    if primary_type not in FEATURE_TYPES:
        raise ValueError(f"unknown primary feature type: {primary_type}")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("type confidence must be between 0 and 1")
    value = {
        "schema_version": SCHEMA_VERSION,
        "identity_unit_id": identity_unit_id,
        "primary_type": primary_type,
        "semantic_roles": sorted({str(item) for item in semantic_roles if item}),
        "type_evidence_ids": sorted({str(item) for item in type_evidence_ids if item}),
        "confidence": float(confidence),
        "conflict_flags": sorted({str(item) for item in conflict_flags if item}),
        "status": status,
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="type_decision", producer_version="type-contract/v1",
            parent_ids=parent_ids,
        ),
    }
    validate_type_decision(value)
    return value


def validate_type_decision(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "identity_unit_id", "primary_type", "semantic_roles",
         "type_evidence_ids", "confidence", "conflict_flags", "status", "provenance"),
        "TypeDecision",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("TypeDecision schema_version must be TypeDecision/v1")
    if value["primary_type"] not in FEATURE_TYPES:
        raise ValueError(f"unknown TypeDecision primary_type: {value['primary_type']}")
    validate_provenance(value["provenance"], label="TypeDecision.provenance")
