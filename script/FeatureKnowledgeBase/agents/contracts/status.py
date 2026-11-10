"""The only canonical status vocabulary for the unified pipeline."""
from __future__ import annotations

from typing import Any


STATUS_REGISTRY: dict[str, Any] = {
    "schema_version": "featurecrawler-status-registry/v1",
    "structure": ["SUPPORTED", "PARTIAL", "AMBIGUOUS", "UNSUPPORTED"],
    "candidate": ["STRONG", "PROBABLE", "WEAK", "REJECTED"],
    "semantic": ["COMPLETE", "PARTIAL", "STRUCTURAL_ONLY", "REVIEW_REQUIRED", "UNAVAILABLE"],
    "admission": ["ADMITTED", "ADMITTED_PARTIAL", "NEEDS_REVIEW", "REJECTED"],
}


def validate_status(value: str, category: str) -> str:
    allowed = STATUS_REGISTRY.get(category)
    if not allowed:
        raise ValueError(f"unknown status category: {category}")
    if value not in allowed:
        raise ValueError(
            f"unknown {category} status {value!r}; allowed={', '.join(allowed)}"
        )
    return value


def adapt_legacy_semantic_status(value: str, *, claim_count: int = 0) -> dict[str, Any]:
    """Explicitly translate historical statuses; never silently coerce them."""
    if value in STATUS_REGISTRY["semantic"]:
        return {"status": value, "source_status": value, "adapter": None}
    if value == "success":
        translated = "COMPLETE" if claim_count else "PARTIAL"
        return {
            "status": translated,
            "source_status": value,
            "adapter": "legacy_semantic_status/v1",
            "reason_codes": ["LEGACY_STATUS_TRANSLATED"],
        }
    if value in {"failed", "error"}:
        return {
            "status": "REVIEW_REQUIRED",
            "source_status": value,
            "adapter": "legacy_semantic_status/v1",
            "reason_codes": ["LEGACY_FAILURE_TRANSLATED"],
        }
    if value in {"usable", "ready"}:
        return {
            "status": "COMPLETE",
            "source_status": value,
            "adapter": "legacy_semantic_status/v1",
            "reason_codes": ["LEGACY_QUALITY_STATUS_TRANSLATED"],
        }
    if value in {"not_present", "missing", ""}:
        return {
            "status": "UNAVAILABLE",
            "source_status": value,
            "adapter": "legacy_semantic_status/v1",
            "reason_codes": ["LEGACY_MISSING_STATUS_TRANSLATED"],
        }
    raise ValueError(f"cannot adapt unknown legacy semantic status: {value!r}")
