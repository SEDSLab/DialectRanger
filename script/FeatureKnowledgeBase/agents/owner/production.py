"""Authoritative generic Owner construction for the frozen production replay.

The candidate generation and ranking code used here is the validated
DOM-first implementation.  This module is the stable production entry point;
evaluation/shadow runners may compare its output, but do not own a second set
of Owner rules.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping

from agents.owner.dom_first_shadow import (
    candidates_to_owner_hypotheses,
    generate_owner_candidates,
    rank_owner_candidates,
)
from agents.owner.generic_surface_recovery import recover_generic_surface_owners


_CALLABLE_HEAD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\(")


def _normalize_scope_identity(value: Any) -> str:
    """Normalize only the identity head used to bound an Owner locally.

    This is deliberately a structural normalization, not a vocabulary lookup:
    signatures lose their argument list and atomic identifiers are upper-cased.
    """
    text = " ".join(str(value or "").split()).strip()
    match = _CALLABLE_HEAD_RE.match(text)
    if match:
        return match.group(1).upper()
    return text.upper()


def _annotate_owner_scope(owner: Mapping[str, Any]) -> dict[str, Any]:
    """Attach an explicit owner-local scope contract to an Owner.

    Downstream stages must not infer locality from a valid owner id alone.  The
    scope contract records the structural block boundary and the identity
    anchors that made the owner selectable, so foreign sibling surfaces can be
    rejected without database-specific rules.
    """
    item = dict(owner)
    identity_values = [
        _normalize_scope_identity(value)
        for value in (item.get("identity_values") or [])
        if _normalize_scope_identity(value)
    ]
    primary = _normalize_scope_identity(item.get("primary_identity"))
    if primary and primary not in identity_values:
        identity_values.insert(0, primary)
    identity_values = list(dict.fromkeys(identity_values))
    member_ids = [str(value) for value in (item.get("member_block_ids") or []) if str(value)]
    anchor_block_ids = [
        str(anchor.get("block_id") or "")
        for anchor in (item.get("anchor_evidence") or [])
        if isinstance(anchor, Mapping) and str(anchor.get("block_id") or "")
    ]
    anchor_block_ids = list(dict.fromkeys(anchor_block_ids))
    # A SignatureOwner produced by the DOM-first heading-section engine has
    # an explicit, bounded member list.  Treating that owner kind as BROAD
    # makes downstream evidence fall back to a small anchor window and drops
    # the prose/parameter/return/example blocks that are already inside the
    # owner.  Page-level owners remain broad; a bounded signature/section
    # owner must remain bounded regardless of the database or profile.
    broad_kinds = {"PageOwner", "Page"}
    # A small page/container with one explicit identity is itself a bounded
    # single-feature surface.  Keeping it BROAD would discard its adjacent
    # prose in the evidence window even though there is no sibling identity
    # that could be contaminated.
    small_identity_surface = bool(identity_values) and 0 < len(member_ids) <= 32
    locality = "BROAD" if len(member_ids) > 32 or (
        str(item.get("owner_kind") or "") in broad_kinds and not small_identity_surface
    ) else "BOUNDED"
    item.update({
        "owner_scope_version": "owner-local-scope/v1",
        "owner_scope_identity_values": identity_values,
        "owner_scope_mode": "IDENTITY_ANCHORED" if identity_values else "STRUCTURAL_BLOCK",
        "owner_scope_block_ids": member_ids,
        "owner_scope_anchor_block_ids": anchor_block_ids,
        "owner_scope_locality": locality,
        "owner_scope_reason_codes": list(dict.fromkeys([
            *( ["EXPLICIT_IDENTITY_ANCHOR"] if identity_values else [] ),
            *( ["STRUCTURAL_MEMBER_BOUNDARY"] if member_ids else [] ),
            *( ["BROAD_OWNER_REQUIRES_LOCAL_FILTER"] if locality == "BROAD" else [] ),
        ])),
    })
    return item


def build_production_owner_layer(
    documents: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build selected Owners and additive generic surface recoveries.

    ``documents`` must already be frozen DocumentIR.  No discovery, fetch, or
    database inventory is consulted here.  The returned ``owners`` list is
    the only Owner collection that downstream production stages should use.
    """
    pages = [dict(row) for row in documents]
    all_candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for document in pages:
        candidates, page_diagnostics = generate_owner_candidates(document, profile)
        all_candidates.extend(candidates)
        diagnostics.append(page_diagnostics)

    ranked_candidates, selected_candidates = rank_owner_candidates(all_candidates)
    selected_owners = candidates_to_owner_hypotheses(pages, selected_candidates, profile)
    recovered_candidates, recovered_owners, table_index, table_audit = recover_generic_surface_owners(
        pages, selected_owners,
    )

    # Recovery is additive-only.  Keep the deterministic selected Owner set
    # intact and guard against accidental duplicate IDs at the production
    # boundary.
    owners_by_id: dict[str, dict[str, Any]] = {
        str(row.get("owner_id")): dict(row)
        for row in [*selected_owners, *recovered_owners]
        if row.get("owner_id")
    }
    owners = sorted(
        (_annotate_owner_scope(row) for row in owners_by_id.values()),
        key=lambda row: (
            str((row.get("provenance") or {}).get("source_url") or ""),
            str(row.get("owner_id") or ""),
        ),
    )
    production_owners = []
    for row in owners:
        item = dict(row)
        # Keep the historical candidate lineage for comparison, while making
        # the artifact's active producer explicit and non-shadow.
        item["production_schema_version"] = "OwnerHypothesis/production-v1"
        item["production_stage"] = "owner_inference_production"
        item["production_engine"] = "dom_first_generic_owner_engine"
        production_owners.append(item)
    return {
        "documents": pages,
        "candidates": ranked_candidates,
        "selected_candidates": selected_candidates,
        "owners": production_owners,
        "selected_owners": selected_owners,
        "recovered_candidates": recovered_candidates,
        "recovered_owners": recovered_owners,
        "table_index": table_index,
        "table_audit": table_audit,
        "diagnostics": diagnostics,
        "stats": {
            "documents": len(pages),
            "candidates": len(ranked_candidates),
            "selected_candidates": len(selected_candidates),
            "selected_owners": len(selected_owners),
            "recovered_owners": len(recovered_owners),
            "owners": len(owners),
            "candidate_status": dict(sorted(Counter(
                str(row.get("selection_status") or "UNKNOWN")
                for row in ranked_candidates
            ).items())),
            "owner_kinds": dict(sorted(Counter(
                str(row.get("owner_kind") or row.get("candidate_kind") or "UNKNOWN")
                for row in owners
            ).items())),
        },
    }
