"""Stable-ID funnel ledger used by the authoritative pipeline."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


FUNNEL_STAGES = (
    "discovered",
    "candidate",
    "validated",
    "identity_unit",
    "typed",
    "evidence_bound",
    "semantic",
    "admitted",
    "canonical",
    "frontend",
)


def make_funnel_row(
    *,
    identity_unit_id: str,
    name: str,
    feature_type: str,
    stage_flags: Mapping[str, bool],
    reason_by_stage: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    flags = {stage: bool(stage_flags.get(stage, False)) for stage in FUNNEL_STAGES}
    reasons = {
        stage: str(value)
        for stage, value in (reason_by_stage or {}).items()
        if value
    }
    first_loss_stage = None
    first_loss_reason = None
    for stage in FUNNEL_STAGES:
        if not flags[stage]:
            first_loss_stage = stage
            first_loss_reason = reasons.get(stage, "STAGE_OUTPUT_MISSING")
            break
    return {
        "schema_version": "pipeline-funnel-ledger/v1",
        "identity_unit_id": identity_unit_id,
        "name": name,
        "feature_type": feature_type,
        **flags,
        "first_loss_stage": first_loss_stage,
        "first_loss_reason": first_loss_reason,
    }


def validate_funnel_row(value: Mapping[str, Any]) -> None:
    required = {"schema_version", "identity_unit_id", "name", "feature_type", *FUNNEL_STAGES,
                "first_loss_stage", "first_loss_reason"}
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"pipeline ledger row missing fields: {', '.join(missing)}")
    if value["schema_version"] != "pipeline-funnel-ledger/v1":
        raise ValueError("pipeline ledger schema_version must be pipeline-funnel-ledger/v1")
    seen_false = False
    for stage in FUNNEL_STAGES:
        if not isinstance(value[stage], bool):
            raise ValueError(f"pipeline ledger stage {stage} must be boolean")
        if seen_false and value[stage]:
            raise ValueError("pipeline ledger stages must be monotonic")
        if not value[stage]:
            seen_false = True
