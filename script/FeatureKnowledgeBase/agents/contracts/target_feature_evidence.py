"""Shadow contract for separating document identity from target features.

The contract is intentionally independent from the production identity path.
An occurrence may be document-defining without being a target SQL feature;
this module makes that distinction explicit for offline audits.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from agents.contracts.base import require_fields, validate_provenance


SCHEMA_VERSION = "TargetFeatureEvidence/v1"
TARGET_TYPES = ("FUNCTION", "OPERATOR", "DATATYPE")
EVIDENCE_STRENGTHS = ("STRONG", "MEDIUM", "WEAK", "NONE")


def make_target_feature_evidence(
    *,
    document_defining: bool,
    target_feature_defining: bool,
    target_feature_plausible: bool,
    target_types: Iterable[str],
    evidence_by_type: Mapping[str, Iterable[Mapping[str, Any]]],
    non_target_evidence: Iterable[Mapping[str, Any]],
    strength: str,
    reason_codes: Iterable[str],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    values = {
        "schema_version": SCHEMA_VERSION,
        "document_defining": bool(document_defining),
        "target_feature_defining": bool(target_feature_defining),
        "target_feature_plausible": bool(target_feature_plausible),
        "target_types": sorted({str(value).upper() for value in target_types if str(value).upper() in TARGET_TYPES}),
        "evidence_by_type": {
            feature_type: [dict(item) for item in evidence_by_type.get(feature_type, ())]
            for feature_type in TARGET_TYPES
        },
        "non_target_evidence": [dict(item) for item in non_target_evidence],
        "strength": strength,
        "reason_codes": sorted({str(value) for value in reason_codes if value}),
        "provenance": dict(provenance),
    }
    validate_target_feature_evidence(values)
    return values


def validate_target_feature_evidence(value: Mapping[str, Any]) -> None:
    require_fields(
        value,
        (
            "schema_version", "document_defining", "target_feature_defining",
            "target_feature_plausible", "target_types", "evidence_by_type",
            "non_target_evidence", "strength", "reason_codes", "provenance",
        ),
        "TargetFeatureEvidence",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"TargetFeatureEvidence schema_version must be {SCHEMA_VERSION}")
    if value["strength"] not in EVIDENCE_STRENGTHS:
        raise ValueError(f"unknown TargetFeatureEvidence strength: {value['strength']}")
    if not isinstance(value["target_types"], list):
        raise ValueError("TargetFeatureEvidence.target_types must be a list")
    if any(item not in TARGET_TYPES for item in value["target_types"]):
        raise ValueError("TargetFeatureEvidence.target_types contains an unknown target type")
    evidence = value["evidence_by_type"]
    if not isinstance(evidence, Mapping) or set(evidence) != set(TARGET_TYPES):
        raise ValueError("TargetFeatureEvidence.evidence_by_type must contain all target types")
    if not isinstance(value["non_target_evidence"], list):
        raise ValueError("TargetFeatureEvidence.non_target_evidence must be a list")
    if not isinstance(value["reason_codes"], list):
        raise ValueError("TargetFeatureEvidence.reason_codes must be a list")
    validate_provenance(value["provenance"], label="TargetFeatureEvidence.provenance")

