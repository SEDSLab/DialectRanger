"""Non-invasive page coverage controller.

The controller only inventories observed structure and downstream counts.  It
does not decide whether an unrecognized surface is a feature.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping


STATES = ("COVERED", "PARTIALLY_COVERED", "UNKNOWN_STRUCTURE", "UNSUPPORTED", "EXCLUDED_WITH_REASON")


def build_coverage(
    documents: Iterable[Mapping[str, Any]], owners: Iterable[Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]], accepted: Iterable[Mapping[str, Any]],
    units: Iterable[Mapping[str, Any]], exclusions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    documents = list(documents); owners = list(owners); candidates = list(candidates)
    accepted = list(accepted); units = list(units); exclusions = list(exclusions)
    owners_by_page = defaultdict(list); candidates_by_page = defaultdict(list); accepted_by_page = defaultdict(list)
    for row in owners: owners_by_page[str(row.get("page_id"))].append(row)
    for row in candidates: candidates_by_page[str(row.get("page_id"))].append(row)
    for row in accepted: accepted_by_page[str(row.get("page_id"))].append(row)
    accepted_page_by_candidate = {str(row.get("candidate_id")): str(row.get("page_id")) for row in accepted if row.get("candidate_id")}
    units_by_page = defaultdict(set)
    for unit in units:
        for candidate_id in unit.get("candidate_ids", []):
            page_id = accepted_page_by_candidate.get(str(candidate_id))
            if page_id:
                units_by_page[page_id].add(str(unit.get("identity_unit_id")))
    excluded_by_url = defaultdict(list)
    for row in exclusions:
        # Candidate rejection is an object-level exclusion, not a page-level
        # fetch/document exclusion.  A page may legitimately contain both
        # accepted and rejected identity surfaces and must remain auditable.
        if row.get("source_url") and str(row.get("stage")) not in {"IdentityCandidate", "CandidateValidation"}:
            excluded_by_url[str(row["source_url"])].append(row)
    rows: list[dict[str, Any]] = []; unknown: list[dict[str, Any]] = []; unsupported: list[dict[str, Any]] = []
    for document in documents:
        page_id = str(document.get("page_id")); url = str(document.get("url"))
        blocks = list(document.get("blocks") or [])
        surfaces = Counter(str(block.get("block_kind") or "OTHER") for block in blocks)
        heading_structures = sorted({tuple(block.get("heading_ancestry") or []) for block in blocks if block.get("heading_ancestry")})
        page_owners = owners_by_page[page_id]; page_candidates = candidates_by_page[page_id]; page_accepted = accepted_by_page[page_id]
        reasons: list[str] = []
        if excluded_by_url.get(url):
            state = "EXCLUDED_WITH_REASON"; reasons.extend(str(row.get("reason_code")) for row in excluded_by_url[url])
        elif not blocks:
            state = "UNKNOWN_STRUCTURE"; reasons.append("NO_DOCUMENT_BLOCKS")
        elif not page_owners:
            state = "UNKNOWN_STRUCTURE"; reasons.append("NO_OWNER_HYPOTHESIS")
        elif not page_candidates:
            state = "PARTIALLY_COVERED" if surfaces else "UNSUPPORTED"; reasons.append("NO_IDENTITY_CANDIDATE")
        elif not page_accepted:
            state = "PARTIALLY_COVERED"; reasons.append("ALL_CANDIDATES_REJECTED")
        else:
            state = "COVERED"
        row = {
            "schema_version": "PageCoverage/v1", "page_id": page_id, "url": url,
            "document_ir_status": "AVAILABLE" if blocks else "EMPTY",
            "template": "|".join(sorted({str(owner.get("template")) for owner in page_owners})) or "unknown",
            "surface_inventory": {
                "block_kinds": dict(sorted(surfaces.items())),
                "heading_structures": [list(item) for item in heading_structures],
                "tables": surfaces.get("TABLE", 0) + surfaces.get("TABLE_ROW", 0),
                "definitions": surfaces.get("DEFINITION", 0), "lists": surfaces.get("LIST", 0) + surfaces.get("LIST_ITEM", 0),
                "code_or_signature_groups": surfaces.get("CODE", 0), "repeated_sections": len({owner.get("template") for owner in page_owners if owner.get("template") == "section"}),
            },
            "owner_count": len(page_owners), "candidate_count": len(page_candidates),
            "validated_identity_count": len(page_accepted), "identity_unit_count": len(units_by_page.get(page_id, set())),
            "coverage_status": state, "reason_codes": sorted(set(reasons)),
        }
        rows.append(row)
        if state == "UNKNOWN_STRUCTURE": unknown.append({"page_id": page_id, "url": url, "reason_codes": row["reason_codes"], "surface_inventory": row["surface_inventory"]})
        if state == "UNSUPPORTED": unsupported.append({"page_id": page_id, "url": url, "reason_codes": row["reason_codes"], "surface_inventory": row["surface_inventory"]})
    counts = Counter(row["coverage_status"] for row in rows)
    return {
        "page_coverage": sorted(rows, key=lambda row: row["url"]),
        "unknown_structures": sorted(unknown, key=lambda row: row["url"]),
        "unsupported_surfaces": sorted(unsupported, key=lambda row: row["url"]),
        "summary": {"schema_version": "coverage-summary/v1", "pages": len(rows), "by_status": {state: counts.get(state, 0) for state in STATES}, "owners": len(owners), "candidates": len(candidates), "accepted": len(accepted), "identity_units": len(units)},
    }
