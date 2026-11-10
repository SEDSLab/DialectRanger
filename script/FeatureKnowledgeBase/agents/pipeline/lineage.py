"""Lineage and exclusion helpers for the authoritative pipeline."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from agents.contracts.base import validate_provenance
from agents.pipeline.ledger import FUNNEL_STAGES


def validate_parent_lineage(
    records_by_stage: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Validate explicit parent IDs without inventing missing links.

    The old audit only checked that a provenance object existed.  P0/P1 also
    requires the IDs at the contract boundaries to resolve to the preceding
    object, so a malformed parent reference is a contract error rather than a
    normal extraction gap.
    """
    materialized = {
        stage: list(rows) for stage, rows in records_by_stage.items()
    }
    id_fields = {
        "document_ir": "document_id",
        "owner_hypothesis": "owner_id",
        "identity_candidate": "candidate_id",
        "identity_unit": "identity_unit_id",
        "type_decision": "identity_unit_id",
        "evidence_envelope": "evidence_id",
        "semantic_record": "semantic_record_id",
        "admission_decision": "admission_id",
        "canonical_feature": "canonical_feature_id",
    }
    indexes = {
        stage: {
            str(row[field]): row
            for row in rows
            if (field := id_fields.get(stage)) and row.get(field)
        }
        for stage, rows in materialized.items()
    }
    document_by_page = {
        str(row.get("page_id")): row
        for row in materialized.get("document_ir", [])
        if row.get("page_id")
    }
    block_by_id: dict[str, Mapping[str, Any]] = {}
    for document in materialized.get("document_ir", []):
        for block in document.get("blocks", []):
            if block.get("block_id"):
                block_by_id[str(block["block_id"])] = block
    errors = []
    object_counts = Counter()
    for stage, rows in materialized.items():
        for row in rows:
            provenance = row.get("provenance")
            if not isinstance(provenance, Mapping):
                errors.append({
                    "stage": stage,
                    "object_id": _object_id(row),
                    "reason": "MISSING_PROVENANCE",
                })
                continue
            try:
                validate_provenance(provenance, label=f"{stage}.provenance")
            except ValueError as exc:
                errors.append({
                    "stage": stage,
                    "object_id": _object_id(row),
                    "reason": "INVALID_PROVENANCE",
                    "detail": str(exc),
                })
            object_counts[stage] += 1
            if not isinstance(provenance, Mapping):
                continue
            parent_ids = set(provenance.get("parent_ids", []))
            object_id = _object_id(row)

            def require_parent(parent_stage: str, parent_id: str | None) -> None:
                if parent_id and str(parent_id) not in indexes.get(parent_stage, {}):
                    errors.append({
                        "stage": stage,
                        "object_id": object_id,
                        "reason": "BROKEN_PARENT_REFERENCE",
                        "parent_stage": parent_stage,
                        "parent_id": str(parent_id),
                    })

            if stage == "owner_hypothesis":
                document = document_by_page.get(str(row.get("page_id")))
                if document:
                    require_parent("document_ir", document.get("document_id"))
                else:
                    errors.append({
                        "stage": stage, "object_id": object_id,
                        "reason": "OWNER_DOCUMENT_NOT_FOUND",
                        "page_id": str(row.get("page_id", "")),
                    })
                for block_id in row.get("member_block_ids", []):
                    if str(block_id) not in block_by_id:
                        errors.append({
                            "stage": stage, "object_id": object_id,
                            "reason": "OWNER_BLOCK_NOT_FOUND",
                            "block_id": str(block_id),
                        })
            elif stage == "identity_candidate":
                require_parent("owner_hypothesis", row.get("owner_id"))
                for block_id in row.get("source_block_ids", []):
                    if str(block_id) not in block_by_id:
                        errors.append({
                            "stage": stage, "object_id": object_id,
                            "reason": "CANDIDATE_BLOCK_NOT_FOUND",
                            "block_id": str(block_id),
                        })
            elif stage == "identity_unit":
                for candidate_id in row.get("candidate_ids", []):
                    require_parent("identity_candidate", candidate_id)
                for owner_id in row.get("owner_ids", []):
                    require_parent("owner_hypothesis", owner_id)
            elif stage == "type_decision":
                require_parent("identity_unit", row.get("identity_unit_id"))
                for evidence_id in row.get("type_evidence_ids", []):
                    require_parent("evidence_envelope", evidence_id)
            elif stage == "evidence_envelope":
                require_parent("identity_unit", row.get("identity_unit_id"))
                require_parent("owner_hypothesis", row.get("owner_id"))
                for block_id in row.get("source_block_ids", []):
                    if str(block_id) not in block_by_id:
                        errors.append({
                            "stage": stage, "object_id": object_id,
                            "reason": "EVIDENCE_BLOCK_NOT_FOUND",
                            "block_id": str(block_id),
                        })
            elif stage == "semantic_record":
                require_parent("identity_unit", row.get("identity_unit_id"))
                for claim in row.get("claims", []):
                    require_parent("evidence_envelope", claim.get("evidence_id"))
            elif stage == "admission_decision":
                require_parent("identity_unit", row.get("identity_unit_id"))
                semantic_id = next(
                    (
                        value for value in parent_ids
                        if value in indexes.get("semantic_record", {})
                    ),
                    None,
                )
                if semantic_id is None:
                    errors.append({
                        "stage": stage, "object_id": object_id,
                        "reason": "ADMISSION_SEMANTIC_PARENT_MISSING",
                    })
            elif stage == "canonical_feature":
                require_parent("identity_unit", row.get("identity_unit_id"))
                require_parent("admission_decision", row.get("admission_id"))
                require_parent("semantic_record", row.get("semantic_record_id"))
                for evidence_id in row.get("evidence_ids", []):
                    require_parent("evidence_envelope", evidence_id)
    return {
        "schema_version": "lineage-contract-audit/v1",
        "status": "PASS" if not errors else "FAIL",
        "object_counts": dict(object_counts),
        "errors": errors,
        "broken_lineage": len(errors),
    }


def make_exclusion(
    *,
    object_id: str,
    stage: str,
    reason_code: str,
    source_url: str | None = None,
    parent_ids: Iterable[str] = (),
    detail: str | None = None,
) -> dict[str, Any]:
    if not object_id or not stage or not reason_code:
        raise ValueError("exclusion requires object_id, stage and reason_code")
    return {
        "schema_version": "pipeline-exclusion-ledger/v1",
        "object_id": object_id,
        "stage": stage,
        "reason_code": reason_code,
        "source_url": source_url,
        "parent_ids": sorted({str(value) for value in parent_ids if value}),
        "detail": detail,
    }


def _object_id(row: Mapping[str, Any]) -> str:
    for key in (
        "document_id", "owner_id", "candidate_id", "identity_unit_id",
        "evidence_id", "semantic_record_id", "admission_id",
        "canonical_feature_id",
    ):
        if row.get(key):
            return str(row[key])
    return "<unknown>"
