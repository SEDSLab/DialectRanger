"""Small dependency-free helpers shared by versioned pipeline contracts."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Iterable


def stable_id(kind: str, value: Any) -> str:
    """Create a deterministic ID independent of process/list allocation order."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{kind}:{hashlib.sha256(encoded).hexdigest()[:24]}"


def text_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def build_provenance(
    *,
    dbms: str,
    source_url: str,
    page_id: str,
    page_hash: str,
    html_path: str,
    producer_stage: str,
    producer_version: str,
    parent_ids: Iterable[str] = (),
    reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "dbms": str(dbms),
        "source_url": str(source_url),
        "page_id": str(page_id),
        "page_hash": str(page_hash),
        "html_path": str(html_path),
        "parent_ids": sorted({str(value) for value in parent_ids if value}),
        "producer_stage": str(producer_stage),
        "producer_version": str(producer_version),
        "reason_codes": sorted({str(value) for value in reason_codes if value}),
    }


def validate_provenance(value: Mapping[str, Any], *, label: str = "provenance") -> None:
    required = (
        "dbms", "source_url", "page_id", "page_hash", "html_path",
        "parent_ids", "producer_stage", "producer_version", "reason_codes",
    )
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")
    for key in ("dbms", "source_url", "page_id", "producer_stage", "producer_version"):
        if not str(value.get(key, "")).strip():
            raise ValueError(f"{label}.{key} must not be empty")
    if not isinstance(value.get("parent_ids"), list):
        raise ValueError(f"{label}.parent_ids must be a list")
    if not isinstance(value.get("reason_codes"), list):
        raise ValueError(f"{label}.reason_codes must be a list")


def require_fields(value: Mapping[str, Any], fields: Iterable[str], label: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")


def ensure_list(value: Any, label: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value
