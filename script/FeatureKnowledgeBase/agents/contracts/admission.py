"""AdmissionDecision/v1 contract and semantic-independent admission policy."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import build_provenance, require_fields, stable_id, validate_provenance
from agents.contracts.status import validate_status
from agents.contracts.publication_integrity import publication_integrity_reasons


SCHEMA_VERSION = "AdmissionDecision/v1"


def decide_admission(
    *,
    dbms: str,
    page_id: str,
    source_url: str,
    page_hash: str,
    html_path: str,
    identity_unit_id: str,
    identity_credible: bool,
    official_source: bool,
    owner_evidence_correct: bool,
    lineage_complete: bool,
    semantic_status: str,
    reason_codes: Iterable[str] = (),
    parent_ids: Iterable[str] = (),
    publication_review_reasons: Iterable[str] = (),
    publication_integrity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_status(semantic_status, "semantic")
    reasons = set(reason_codes)
    integrity_reasons = publication_integrity_reasons(publication_integrity, identity_unit_id) if publication_integrity is not None else []
    if not identity_credible:
        decision = "REJECTED"
        reasons.add("IDENTITY_NOT_CREDIBLE")
    elif not official_source:
        decision = "REJECTED"
        reasons.add("NO_OFFICIAL_SOURCE")
    elif not owner_evidence_correct:
        decision = "REJECTED"
        reasons.add("OWNER_EVIDENCE_INVALID")
    elif not lineage_complete:
        decision = "REJECTED"
        reasons.add("LINEAGE_INCOMPLETE")
    elif integrity_reasons:
        decision = "NEEDS_REVIEW"
        reasons.add("PUBLICATION_INTEGRITY_UNRESOLVED")
        reasons.update(integrity_reasons)
        reasons.update(publication_review_reasons)
    elif publication_review_reasons:
        decision = "NEEDS_REVIEW"
        reasons.update(publication_review_reasons)
    elif semantic_status == "COMPLETE":
        decision = "ADMITTED"
    elif semantic_status == "PARTIAL":
        decision = "ADMITTED_PARTIAL"
        reasons.add("SEMANTIC_PARTIAL")
    elif (publication_integrity is not None
          and not integrity_reasons and publication_integrity.get('state')=='CONFIRMED'):
        decision='ADMITTED_PARTIAL'
        reasons.add('VERIFIED_IDENTITY_SEMANTIC_'+semantic_status)
    else:
        decision = "NEEDS_REVIEW"
        reasons.add("SEMANTIC_REVIEW_OR_UNAVAILABLE")
    value = {
        "schema_version": SCHEMA_VERSION,
        "admission_id": stable_id("admission", [identity_unit_id, decision, sorted(reasons)]),
        "identity_unit_id": identity_unit_id,
        "decision": decision,
        "identity_credible": bool(identity_credible),
        "official_source": bool(official_source),
        "owner_evidence_correct": bool(owner_evidence_correct),
        "lineage_complete": bool(lineage_complete),
        "semantic_status": semantic_status,
        "reason_codes": sorted(reasons),
        "provenance": build_provenance(
            dbms=dbms, source_url=source_url, page_id=page_id,
            page_hash=page_hash, html_path=html_path,
            producer_stage="admission",
            producer_version="admission-contract/v1",
            parent_ids=parent_ids,
        ),
    }
    if publication_integrity is not None:
        value["publication_integrity"] = dict(publication_integrity)
    validate_admission_decision(value)
    return value


def validate_admission_decision(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        ("schema_version", "admission_id", "identity_unit_id", "decision",
         "identity_credible", "official_source", "owner_evidence_correct",
         "lineage_complete", "semantic_status", "reason_codes", "provenance"),
        "AdmissionDecision",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("AdmissionDecision schema_version must be AdmissionDecision/v1")
    validate_status(value["decision"], "admission")
    validate_status(value["semantic_status"], "semantic")
    validate_provenance(value["provenance"], label="AdmissionDecision.provenance")
