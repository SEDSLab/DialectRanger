"""Versioned data contracts shared by the document-structure-aware path."""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any


ARCHETYPES = {
    "navigation_overview", "feature_reference_index", "category_page",
    "aggregate_syntax", "single_feature_detail", "multi_feature_detail",
    "mixed_feature_reference", "mapping_table", "precedence_table",
    "usage_guide", "unknown",
}
FEATURE_TYPES = {"function", "operator", "datatype"}


def stable_id(kind: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return f"{kind}:{hashlib.sha256(encoded).hexdigest()[:24]}"


def clean_text(value: Any) -> str:
    text = str(value or "")
    for _ in range(2):
        text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def evidence(page: dict, block: dict, value: str | None = None, role: str = "definition") -> dict:
    text = block["text"]
    needle = clean_text(value) if value is not None else text
    start = text.casefold().find(needle.casefold()) if needle else 0
    if start < 0:
        raise ValueError(f"Evidence value is not in block {block['block_id']}: {needle!r}")
    exact = text[start:start + len(needle)]
    return {
        "page_id": page["page_id"], "url": page["url"], "page_hash": page["page_hash"],
        "section_id": block["section_id"], "block_id": block["block_id"],
        "block_type": block["block_type"], "dom_path": block["dom_path"],
        "evidence_role": role,
        "evidence_span": {"start": start, "end": start + len(exact), "text": exact},
    }


def validate_evidence(value: dict, pages: dict[str, dict]) -> list[str]:
    page = pages.get(value.get("page_id"))
    if not page:
        return ["unknown_page"]
    block = next((row for row in page["blocks"] if row["block_id"] == value.get("block_id")), None)
    if not block:
        return ["unknown_block"]
    span = value.get("evidence_span", {})
    start, end = span.get("start"), span.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end > len(block["text"]):
        return ["invalid_span"]
    return [] if block["text"][start:end] == span.get("text") else ["span_mismatch"]
