"""Generic IdentityUnit provenance and credibility contract.

The contract is intentionally origin-agnostic.  An IdentityUnit created by
the ordinary identity path and one created by a bounded structural adaptation
must both prove the same things: a real official source, a real owner, a real
owner-local block, and a deterministic lineage to that block.  Adaptation is
not a bypass; it is only another provenance origin.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agents.contracts.base import stable_id


_ADAPTATION_KIND_ALIASES = {
    "DATATYPE": "PARAMETERIZED_TYPE_STRUCTURAL_IDENTITY",
    "OPERATOR": "OPERATOR_RECONCILIATION",
    "OPERATOR_CLOSURE": "KEYWORD_OPERATOR_STRUCTURAL_IDENTITY",
    "PARAMETERIZED_TYPE_STRUCTURAL_IDENTITY": "PARAMETERIZED_TYPE_STRUCTURAL_IDENTITY",
    "KEYWORD_OPERATOR_STRUCTURAL_IDENTITY": "KEYWORD_OPERATOR_STRUCTURAL_IDENTITY",
    "MULTI_TOKEN_OPERATOR_STRUCTURAL_IDENTITY": "MULTI_TOKEN_OPERATOR_STRUCTURAL_IDENTITY",
    "OPERATOR_RECONCILIATION": "OPERATOR_RECONCILIATION",
}


def canonical_adaptation_kind(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return _ADAPTATION_KIND_ALIASES.get(raw, raw or "GENERIC_STRUCTURAL_ADAPTATION")


def make_adaptation_provenance_record(
    *,
    adaptation_kind: Any,
    adaptation_rule_id: Any,
    owner_id: Any,
    page_id: Any,
    source_url: Any,
    block_id: Any,
    raw_text: Any = "",
    page_hash: Any = "",
    source_evidence_ids: Sequence[Any] = (),
    structural_observation_ids: Sequence[Any] = (),
    parent_identity_unit_id: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """Create the shared provenance shape used by all structural adapters."""

    owner = str(owner_id or "")
    page = str(page_id or "")
    block = str(block_id or "")
    url = str(source_url or "")
    rule = str(adaptation_rule_id or "").strip() or "GENERIC_STRUCTURAL_ADAPTATION"
    observation_ids = [str(value) for value in structural_observation_ids if str(value)]
    if not observation_ids:
        observation_ids = [stable_id("structural_observation", {
            "adaptation_kind": canonical_adaptation_kind(adaptation_kind),
            "adaptation_rule_id": rule,
            "owner_id": owner,
            "page_id": page,
            "block_id": block,
            "source_url": url,
            "raw_text": str(raw_text or ""),
        })]
    result: dict[str, Any] = {
        # New generic contract fields.
        "adaptation_kind": canonical_adaptation_kind(adaptation_kind),
        "adaptation_rule_id": rule,
        "source_owner_id": owner,
        "source_document_id": page,
        "source_block_ids": [block] if block else [],
        "source_evidence_ids": sorted({str(value) for value in source_evidence_ids if str(value)}),
        "structural_observation_ids": sorted(set(observation_ids)),
        "corpus_hash": str(page_hash or ""),
        "deterministic": True,
        # Legacy aliases remain for frozen readers and audit compatibility.
        "adaptation": str(adaptation_kind or "") or canonical_adaptation_kind(adaptation_kind),
        "owner_id": owner,
        "page_id": page,
        "block_id": block,
        "source_url": url,
        "raw_text": str(raw_text or ""),
    }
    if parent_identity_unit_id:
        result["parent_identity_unit_id"] = str(parent_identity_unit_id)
    result.update({key: value for key, value in extra.items() if value is not None})
    return result


def normalized_adaptation_provenance(unit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read both the current and the earlier generic adaptation shapes."""

    rows: list[dict[str, Any]] = []
    for raw in unit.get("adaptation_provenance") or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        source_blocks = row.get("source_block_ids") or []
        if not source_blocks and row.get("block_id"):
            source_blocks = [row.get("block_id")]
        source_evidence = row.get("source_evidence_ids") or []
        observation_ids = row.get("structural_observation_ids") or []
        normalized = make_adaptation_provenance_record(
            adaptation_kind=row.get("adaptation_kind") or row.get("adaptation") or unit.get("adapted_identity_kind"),
            adaptation_rule_id=row.get("adaptation_rule_id") or row.get("adaptation_rule") or row.get("adaptation"),
            owner_id=row.get("source_owner_id") or row.get("owner_id"),
            page_id=row.get("source_document_id") or row.get("page_id"),
            source_url=row.get("source_url"),
            block_id=(source_blocks[0] if source_blocks else row.get("block_id")),
            raw_text=row.get("raw_text"),
            page_hash=row.get("corpus_hash") or (unit.get("provenance") or {}).get("page_hash"),
            source_evidence_ids=source_evidence,
            structural_observation_ids=observation_ids,
            parent_identity_unit_id=row.get("parent_identity_unit_id"),
        )
        for key in ("category", "matched_text", "surface"):
            if key in row:
                normalized[key] = row[key]
        rows.append(normalized)
    return rows


def _source_block_rows(bundle: Mapping[str, Any] | None, envelopes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in (bundle or {}).get("source_blocks") or [] if isinstance(row, Mapping)]
    if rows:
        return rows
    fallback: dict[str, dict[str, Any]] = {}
    for envelope in envelopes:
        for block_id in envelope.get("source_block_ids") or []:
            key = str(block_id)
            row = fallback.setdefault(key, {
                "block_id": key,
                "evidence_ref": key,
                "owner_ids": [],
                "page_id": envelope.get("page_id"),
                "source_url": envelope.get("provenance", {}).get("source_url"),
            })
            owner_id = str(envelope.get("owner_id") or "")
            if owner_id and owner_id not in row["owner_ids"]:
                row["owner_ids"].append(owner_id)
    return list(fallback.values())


def evaluate_identity_credibility(
    unit: Mapping[str, Any],
    *,
    owners: Sequence[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any] | None = None,
    envelopes: Sequence[Mapping[str, Any]] = (),
    owner_index: Mapping[str, Mapping[str, Any]] | None = None,
    page_index: Mapping[str, Mapping[str, Any]] | None = None,
    block_index: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]] | None = None,
    source_structure=None,
) -> dict[str, Any]:
    """Evaluate the shared identity/source/owner contract without DB rules."""

    unit_id = str(unit.get("identity_unit_id") or unit.get("identity_group_id") or "")
    identity = str(unit.get("canonical_identity") or unit.get("normalized_identity") or "").strip()
    owner_by_id = dict(owner_index) if owner_index is not None else {
        str(row.get("owner_id") or ""): row for row in owners
    }
    page_by_id = dict(page_index) if page_index is not None else {
        str(row.get("page_id") or ""): row for row in documents
    }
    block_by_id = dict(block_index) if block_index is not None else {
        str(block.get("block_id") or ""): (page, block)
        for page in documents
        for block in page.get("blocks") or []
        if block.get("block_id")
    }
    owner_ids = sorted({str(value) for value in unit.get("owner_ids") or [] if str(value)})
    original_owners={str(o.get('owner_id')):o for o in owners}
    provenance = unit.get("provenance") or {}
    adaptation_rows = normalized_adaptation_provenance(unit)
    origin = "ADAPTED" if adaptation_rows else (
        "ORIGINAL" if unit.get("candidate_ids") or unit.get("defining_occurrence_ids") else "UNKNOWN"
    )
    reasons: list[str] = []

    if not identity:
        reasons.append("IDENTITY_EMPTY")
    if not owner_ids:
        reasons.append("OWNER_MISSING")
    missing_owners = sorted(owner_id for owner_id in owner_ids if owner_id not in owner_by_id)
    if missing_owners:
        reasons.append("OWNER_NOT_FOUND")

    page_id = str(provenance.get("page_id") or "")
    source_url = str(provenance.get("source_url") or "")
    if not page_id or page_id not in page_by_id:
        reasons.append("SOURCE_DOCUMENT_NOT_FOUND")
    if not source_url:
        reasons.append("SOURCE_URL_MISSING")
    if not str(provenance.get("page_hash") or ""):
        reasons.append("SOURCE_HASH_MISSING")

    source_blocks = _source_block_rows(bundle, envelopes)
    bundle_owner_ids = {
        str(owner.get("owner_id") or "")
        for owner in (bundle or {}).get("owners") or []
        if isinstance(owner, Mapping) and owner.get("owner_id")
    }
    source_block_ids = {
        str(row.get("block_id") or row.get("evidence_ref") or "")
        for row in source_blocks
        if row.get("block_id") or row.get("evidence_ref")
    }
    valid_block_owner_pairs = 0
    invalid_block_owner_pairs = 0
    verified_content_associations = 0
    from agents.semantic_stage.source_structure import SourceStructureIndex
    content_reader=source_structure if source_structure is not None else SourceStructureIndex()
    for block in source_blocks:
        block_id = str(block.get("block_id") or block.get("evidence_ref") or "")
        page_and_block = block_by_id.get(block_id)
        block_owner_ids = {
            str(value) for value in block.get("owner_ids") or [] if str(value)
        } or bundle_owner_ids
        from agents.contracts.source_binding import block_binding
        local,associations=block_binding(block,unit,owner_by_id,page_by_id,block_by_id,
                                         original_owners,content_reader,bundle_owner_ids)
        verified_content_associations+=associations
        if local:
            valid_block_owner_pairs+=1
        else:
            invalid_block_owner_pairs+=1

    owner_evidence_correct = bool(source_blocks and (bundle_owner_ids or any(row.get("owner_ids") for row in source_blocks))) and not invalid_block_owner_pairs and valid_block_owner_pairs > 0
    if not owner_evidence_correct:
        reasons.append("OWNER_EVIDENCE_INVALID")

    primary_source_consistent = True
    if unit.get('profile_definition_required'):
        from pathlib import Path
        from agents.contracts.profile_authority import verified_authority
        page = page_by_id.get(page_id) or {}
        primary_source_consistent = bool(page)
        for key, field in (('source_url', 'url'), ('page_hash', 'page_hash'), ('html_path', 'html_path')):
            claimed, actual = provenance.get(key), page.get(field)
            equal = bool(claimed and actual and claimed == actual)
            if key == 'html_path' and claimed and actual:
                equal = Path(claimed).resolve() == Path(actual).resolve()
            if not equal:
                primary_source_consistent = False
                reasons.append('PRIMARY_SOURCE_' + key.upper() + '_MISMATCH')
        primary_owners = [oid for oid in owner_ids if owner_by_id.get(oid, {}).get('page_id') == page_id]
        authority_bundle = dict(bundle or {}, source_blocks=source_blocks)
        authority_bundle['owners'] = list(owners)
        authority = verified_authority(dict(unit, owner_ids=primary_owners), authority_bundle) if primary_owners else None
        if (not authority or authority.get('source_url') != page.get('url')
                or authority.get('source_hash') != page.get('page_hash')):
            primary_source_consistent = False
            reasons.append('PRIMARY_SOURCE_DEFINING_OWNER_MISSING')

    adaptation_valid = True
    adaptation_structural_ids: list[str] = []
    if adaptation_rows:
        for row in adaptation_rows:
            owner_id = str(row.get("source_owner_id") or "")
            document_id = str(row.get("source_document_id") or "")
            block_ids = [str(value) for value in row.get("source_block_ids") or [] if str(value)]
            if not owner_id or owner_id not in owner_ids or owner_id not in owner_by_id:
                adaptation_valid = False
                continue
            owner = owner_by_id[owner_id]
            if not document_id or document_id not in page_by_id or str(owner.get("page_id") or "") != document_id:
                adaptation_valid = False
            if not block_ids:
                adaptation_valid = False
            for block_id in block_ids:
                page_and_block = block_by_id.get(block_id)
                citation_owner=owner
                if any(b.get('block_id')==block_id and b.get('evidence_usage')=='DISCOVERY_ONLY' for b in source_blocks):
                    citation_owner=original_owners.get(owner_id,owner)
                if not page_and_block or block_id not in {str(value) for value in citation_owner.get("member_block_ids") or []}:
                    adaptation_valid = False
                if block_id not in source_block_ids:
                    adaptation_valid = False
            adaptation_structural_ids.extend(str(value) for value in row.get("structural_observation_ids") or [] if str(value))
        if not adaptation_valid:
            reasons.append("ADAPTED_PROVENANCE_INVALID")
        if not adaptation_structural_ids:
            reasons.append("ADAPTED_STRUCTURAL_EVIDENCE_MISSING")
    else:
        if not unit.get("candidate_ids") or not owner_ids:
            reasons.append("ORIGINAL_LINEAGE_IDS_MISSING")

    lineage_complete = bool(identity and owner_ids and owner_evidence_correct and page_id in page_by_id and source_url and source_blocks and primary_source_consistent)
    if adaptation_rows:
        lineage_complete = lineage_complete and adaptation_valid and bool(adaptation_structural_ids)
    else:
        lineage_complete = lineage_complete and bool(unit.get("candidate_ids") or unit.get("defining_occurrence_ids"))
    if not lineage_complete:
        reasons.append("LINEAGE_INCOMPLETE")

    identity_credible = bool(identity and owner_evidence_correct and lineage_complete and not missing_owners)
    if not identity_credible:
        reasons.append("IDENTITY_NOT_CREDIBLE")
    return {
        "identity_unit_id": unit_id,
        "identity": identity,
        "origin": origin,
        "identity_credible": identity_credible,
        "owner_evidence_correct": owner_evidence_correct,
        "verified_content_associations": verified_content_associations,
        "lineage_complete": lineage_complete,
        "source_document_id": page_id,
        "source_url": source_url,
        "owner_ids": owner_ids,
        "source_block_ids": sorted(source_block_ids),
        "adaptation_provenance": adaptation_rows,
        "adaptation_structural_observation_ids": sorted(set(adaptation_structural_ids)),
        "reasons": sorted(set(reasons)),
    }


__all__ = [
    "canonical_adaptation_kind",
    "evaluate_identity_credibility",
    "make_adaptation_provenance_record",
    "normalized_adaptation_provenance",
]
