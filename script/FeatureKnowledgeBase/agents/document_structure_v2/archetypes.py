"""Deterministic page-archetype classification from topology signals."""
from __future__ import annotations

import re


def classify_page(page: dict) -> dict:
    blocks = page["blocks"]
    rows = [block for block in blocks if block["block_type"] == "table_row"]
    codes = [block for block in blocks if block["block_type"] == "code"]
    repeated = {block["boundary_id"] for block in blocks if block["boundary_kind"] == "repeated_unit"}
    headings = " ".join(block["text"] for block in blocks if block["block_type"] == "heading")
    header_text = " ".join(" ".join(row.get("table_headers", [])) for row in rows)
    context = set(page.get("feature_context", []))
    signals = {
        "table_rows": len(rows), "code_blocks": len(codes), "repeated_units": len(repeated),
        "internal_detail_links": sum(link["resolution_status"] in {"resolved_page", "resolved_anchor"} for link in page["links"]),
        "feature_context": sorted(context),
    }
    is_reference_table = rows and re.search(r"\bname\b", header_text, re.I) and re.search(r"\bdescription\b", header_text, re.I)
    if re.search(r"\bprecedence\b", headings, re.I) and rows:
        archetype, rule = "precedence_table", "precedence_heading_with_table"
    elif re.search(r"\b(?:other|vendor|mapping|compatib)\b", headings, re.I) and rows:
        archetype, rule = "mapping_table", "mapping_heading_with_table"
    elif is_reference_table and repeated:
        archetype, rule = "mixed_feature_reference", "reference_table_with_owned_detail_units"
    elif is_reference_table:
        archetype, rule = "feature_reference_index", "name_description_reference_table"
    elif context == {"datatype"} and len(repeated) >= 2 and page["links"]:
        archetype, rule = "aggregate_syntax", "datatype_repeated_linked_declarations"
    elif len(repeated) >= 2:
        archetype, rule = "multi_feature_detail", "anchored_repeated_owner_units"
    elif len(repeated) == 1:
        archetype, rule = "single_feature_detail", "single_repeated_owner_unit"
    elif len(context) == 1 and any(block["block_type"] == "paragraph" for block in blocks):
        archetype, rule = "single_feature_detail", "single_context_content_page"
    elif page["links"] and len(blocks) <= 3:
        archetype, rule = "navigation_overview", "link_dominant_thin_page"
    else:
        archetype, rule = "unknown", "insufficient_structural_signal"
    return {
        "schema_version": "page-archetype/v2", "page_id": page["page_id"],
        "feature_context": sorted(context), "page_archetype": archetype,
        "signals": signals, "rule_ids": [rule],
    }


def representative_pages_by_archetype(classifications: list[dict]) -> list[str]:
    """Choose one stable representative for every observed major archetype."""
    selected = {}
    for row in sorted(classifications, key=lambda value: value["page_id"]):
        selected.setdefault(row["page_archetype"], row["page_id"])
    return [selected[key] for key in sorted(selected) if key != "unknown"]
