"""EvidenceEnvelope/v2 contract."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, text_hash, validate_provenance


SCHEMA_VERSION = "EvidenceEnvelope/v2"


def make_evidence_envelope(
    *,
    dbms: str,
    source_url: str,
    page_id: str,
    page_hash: str,
    html_path: str,
    identity_unit_id: str,
    owner_id: str,
    role: str,
    raw_text: str,
    source_block_ids: Iterable[str],
    source_span: dict[str, Any] | None = None,
    normalized_text: str | None = None,
    signature_applicability: str = "UNKNOWN",
    binding_status: str = "BOUND",
    parent_ids: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    source_block_ids = sorted({str(item) for item in source_block_ids if item})
    value = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": f"evidence:{text_hash([identity_unit_id, owner_id, source_block_ids, raw_text])[:24]}",
        "identity_unit_id": identity_unit_id,
        "owner_id": owner_id,
        "page_id": page_id,
        "role": role,
        "raw_text": raw_text,
        "normalized_text": normalized_text if normalized_text is not None else raw_text,
        "source_block_ids": source_block_ids,
        "source_span": source_span,
        "signature_applicability": signature_applicability,
        "binding_status": binding_status,
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="evidence_binding",
            producer_version="evidence-contract/v2",
            parent_ids=parent_ids, reason_codes=reason_codes,
        ),
    }
    validate_evidence_envelope(value)
    return value


def validate_evidence_envelope(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "evidence_id", "identity_unit_id", "owner_id", "page_id",
         "role", "raw_text", "normalized_text", "source_block_ids", "source_span",
         "signature_applicability", "binding_status", "provenance"),
        "EvidenceEnvelope",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("EvidenceEnvelope schema_version must be EvidenceEnvelope/v2")
    validate_provenance(value["provenance"], label="EvidenceEnvelope.provenance")
    if not value["source_block_ids"]:
        raise ValueError("EvidenceEnvelope must retain source_block_ids")
