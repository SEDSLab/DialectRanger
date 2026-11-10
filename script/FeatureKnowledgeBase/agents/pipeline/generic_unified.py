"""The Generic Unified production contract.

This module is the production continuation of the already validated generic
Discovery/DocumentIR/Owner/Identity implementation.  It deliberately has no
dependency on onboarding, formal crawl, Profile DSL, or legacy candidate
adapters.  A Profile is used only as a structural hint contract; IdentityUnit
is the authoritative input to every stage after identity construction.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import re
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping

from agents.contracts.admission import decide_admission
from agents.contracts.publication_scope import definition_publication_review
from agents.contracts.publication_integrity import evaluate_publication_integrity
from agents.pipeline.publication import select_publication
from agents.contracts.canonical import make_canonical_feature
from agents.contracts.datatype_knowledge import build_datatype_extensions, response_extension_payload
from agents.semantic_stage.source_structure import SourceStructureIndex
from agents.contracts.evidence import make_evidence_envelope
from agents.contracts.identity_credibility import evaluate_identity_credibility
from agents.contracts.semantic import make_semantic_record
from agents.contracts.type_decision import make_type_decision
from agents.identity.production import (
    build_candidate_ledger,
    build_identity_occurrences,
    reconcile_identity_groups,
    validate_candidate_ledger,
    normalize_identity,
)
from agents.pipeline.lineage import validate_parent_lineage
from agents.semantic_extractor.frontend_projection_v2 import write_frontend_projection
from agents.semantic_sense.audit import run_sense_audit
from agents.semantic_stage.evidence_bundle import build_evidence_bundle, build_indexes
from agents.semantic_stage.definition_section import project_definition_owner
from agents.semantic_stage.pipeline import run_semantic_stage
from agents.type.decision import decide_type
from agents.type.evidence import (
    ALIGN_EXACT,
    ALIGN_SLOT,
    TARGET_TYPES,
    build_typed_feature_evidence,
)


TARGET_TYPE_NAMES = {"FUNCTION", "OPERATOR", "DATATYPE"}
SEMANTIC_STATUS = {"COMPLETE", "PARTIAL", "STRUCTURAL_ONLY", "REVIEW_REQUIRED", "UNAVAILABLE"}


def _write_json(path: Path, value: Any) -> None:
    from agents.contracts.artifact_io import write_json
    write_json(path,value)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    from agents.contracts.artifact_io import write_jsonl
    write_jsonl(path,rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hash(value: Any) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _first_source(group: Mapping[str, Any], documents: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    provenance = group.get("provenance") or {}
    page_id = str(provenance.get("page_id") or "")
    page = documents.get(page_id) or {}
    return {
        "page_id": page_id,
        "source_url": str(provenance.get("source_url") or page.get("url") or ""),
        "page_hash": str(provenance.get("page_hash") or page.get("page_hash") or ""),
        "html_path": str(provenance.get("html_path") or page.get("html_path") or ""),
        "dbms": str(provenance.get("dbms") or page.get("dbms") or ""),
    }


def _block_for(page: Mapping[str, Any], block_id: str) -> Mapping[str, Any] | None:
    return next((block for block in page.get("blocks") or [] if str(block.get("block_id") or "") == block_id), None)


def _typed_block(block: Mapping[str, Any]) -> dict[str, Any]:
    metadata = block.get("table_metadata") or {}
    return {
        "block_id": str(block.get("block_id") or ""),
        "block_type": str(block.get("block_kind") or block.get("block_type") or "text").casefold(),
        "text": str(block.get("text") or ""),
        "cells": list(metadata.get("cells") or []),
        "table_metadata": dict(block['table_metadata']) if block.get('table_metadata') is not None else None,
        "table_headers": list(metadata.get("headers") or []),
        "heading_ancestry": list(block.get("heading_ancestry") or []),
        "definition_context": dict(block.get("definition_context") or {}),
        "block_kind": str(block.get('block_kind') or ''),
        "dom_path": str(block.get('dom_path') or ''),
        "inline_nodes": list(block.get('inline_nodes') or []),
        "provenance": dict(block.get('provenance') or {}),
    }


def _owner_type_context(
    unit: Mapping[str, Any],
    owner: Mapping[str, Any],
    page: Mapping[str, Any],
) -> dict[str, Any]:
    blocks = [_block_for(page, str(block_id)) for block_id in owner.get("member_block_ids") or []]
    blocks = [block for block in blocks if block]
    variants = list(unit.get("variants") or [])
    scope_values = {
        normalize_identity(value)
        for value in (owner.get("owner_scope_identity_values") or owner.get("identity_values") or [])
        if normalize_identity(value)
    }
    native_term_values = {
        normalize_identity(value)
        for proof in owner.get('definition_section_proofs') or []
        if proof.get('association') == 'NATIVE_DEFINITION_TERM_BODY'
        for value in proof.get('identity_values') or []
        if normalize_identity(value)
    }
    if native_term_values:
        scope_values = native_term_values
    canonical_subject = normalize_identity(unit.get("canonical_identity") or "")
    owner_local_binding = not scope_values or canonical_subject in scope_values
    owner_heading_context = list(dict.fromkeys(
        str(value).strip()
        for block in blocks
        for value in (block.get("heading_ancestry") or [])
        if str(value).strip()
    ))
    owner_headers = list(dict.fromkeys(
        str(value).strip()
        for block in blocks
        for value in ((block.get("table_metadata") or {}).get("headers") or [])
        if str(value).strip()
    ))
    # A native term in an explicitly function-labelled section defines the
    # callable subject. References to its implementing/infix operator in DD
    # prose must not become a second definition vote for this same unit.
    definition_term_function = bool(
        not unit.get('adapted_identity_kind')
        and any(p.get('association') == 'NATIVE_DEFINITION_TERM_BODY'
                and canonical_subject in p.get('identity_values', [])
                for p in owner.get('definition_section_proofs') or [])
        and any(re.search(r'\bfunctions?\b', h, re.I) for h in owner_heading_context)
        and not any(re.search(r'\b(?:operators?|data\s*types?|datatypes?)\b', h, re.I)
                    for h in owner_heading_context))
    return {
        "identity_unit_id": str(unit.get("identity_unit_id") or ""),
        "canonical_subject": str(unit.get("canonical_identity") or ""),
        "definition_term_function": definition_term_function,
        "dbms": str(page.get("dbms") or ""),
        "owner_id": str(owner.get("owner_id") or ""),
        "owner_kind": str(owner.get("owner_kind") or ""),
        "raw_text": " ".join(str(block.get("text") or "") for block in blocks),
        "source_url": str(page.get("url") or ""),
        "html_path": str(page.get("html_path") or ""),
        "page_id": str(page.get("page_id") or ""),
        "page_title": str(page.get("title") or ""),
        # These are structural observations emitted by the generic owner
        # layer.  Type evidence may use them as context, but they never
        # override owner-local block evidence.
        "profile_hint": owner.get("profile_hint") or {},
        "owner_profile_hint": owner.get("profile_hint") or {},
        "adapted_identity_kind": str(unit.get("adapted_identity_kind") or ""),
        "operator_closure_observations": list(unit.get("operator_closure_observations") or []),
        # Heading/table context is owner-local.  The page title remains
        # available separately, but sibling sections must not type this unit.
        "page_heading_context": owner_heading_context,
        "owner_heading_context": owner_heading_context,
        "heading_ancestry": list((blocks[0] if blocks else {}).get("heading_ancestry") or []),
        "table_headers": owner_headers,
        "owner_scope_identity_values": sorted(scope_values),
        "owner_scope_mode": str(owner.get("owner_scope_mode") or ""),
        "owner_scope_locality": str(owner.get("owner_scope_locality") or ""),
        "owner_scope_block_ids": list(owner.get("owner_scope_block_ids") or owner.get("member_block_ids") or []),
        "owner_local_binding": owner_local_binding,
        "owner_binding_reason": "EXPLICIT_IDENTITY_ANCHOR" if owner_local_binding and scope_values else (
            "FOREIGN_LOCAL_UNIT" if scope_values else "STRUCTURAL_BLOCK"
        ),
        "signature_variants": variants,
        "raw_variants": variants,
        "blocks": [_typed_block(block) for block in blocks],
        "definition_section_proofs": list(owner.get('definition_section_proofs') or []),
    }


def _build_typed_layer(
    units: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    output_root: Path,
    *,
    type_adaptation: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    owner_by_id = {str(row.get("owner_id") or ""): row for row in owners}
    page_by_id = {str(row.get("page_id") or ""): row for row in documents}
    typed_rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    updated_units: list[dict[str, Any]] = []

    for original in sorted(units, key=lambda row: str(row.get("identity_unit_id") or "")):
        unit = dict(original)
        unit_id = str(unit.get("identity_unit_id") or "")
        evidence: list[dict[str, Any]] = []
        for owner_id in unit.get("owner_ids") or []:
            owner = owner_by_id.get(str(owner_id))
            page = page_by_id.get(str(owner.get("page_id") or "")) if owner else None
            if not owner or not page:
                continue
            context = _owner_type_context(unit, owner, page)
            if type_adaptation:
                # Import lazily so the frozen/default path retains exactly the
                # existing dependency graph and evidence behavior.
                from agents.type.adaptation import build_adapted_typed_feature_evidence

                evidence_rows = build_adapted_typed_feature_evidence(context)
            else:
                evidence_rows = build_typed_feature_evidence(context)
            for row in evidence_rows:
                row = dict(row)
                row["identity_unit_id"] = unit_id
                evidence.append(row)

        # Type-only interpretation of source evidence, not Identity/Owner repair.
        from agents.type.definition_roles import interpret_type_evidence
        evidence = interpret_type_evidence([unit], evidence, documents, owners)
        semantic_only=set(unit.get('semantic_reference_owner_ids') or [])
        for row in evidence:
            if row.get('owner_id') in semantic_only:
                row['evidence_role']='USAGE_REFERENCE'
                row['definition_vote_eligible']=False
                row['role_proof']={'rule':'PROVEN_IDENTITY_USAGE_NOT_A_SECOND_DEFINITION/v1',
                    'owner_id':row['owner_id'],'identity_unit_id':unit_id,
                    'reason':'Signature usage retained for Semantic, not TypeDecision'}
        # Persist the interpreted records without changing original vote IDs.
        typed_rows.extend(evidence)
        if type_adaptation:
            from agents.type.adaptation import adapted_decide_type

            type_result = adapted_decide_type(unit, evidence)
        else:
            type_result = decide_type(unit, evidence)
        source = _first_source(unit, page_by_id)
        # ``decide_type`` deliberately exposes the richer internal states
        # ``TARGET_FEATURE_UNCERTAIN`` and ``OTHER``.  The public contract is
        # intentionally smaller: an unresolved target is represented as
        # ``UNCERTAIN`` and a non-target is retained in the audit but cannot be
        # emitted as an invalid TypeDecision record.
        contract_primary = str(type_result.get("primary_type") or "UNCERTAIN").upper()
        if contract_primary not in TARGET_TYPE_NAMES and contract_primary != "UNCERTAIN":
            contract_primary = "UNCERTAIN"
        type_decision = make_type_decision(
            dbms=source["dbms"],
            page_id=source["page_id"],
            source_url=source["source_url"],
            page_hash=source["page_hash"],
            html_path=source["html_path"],
            identity_unit_id=unit_id,
            primary_type=contract_primary,
            type_evidence_ids=[],
            confidence=float(type_result.get("confidence") or 0.0),
            conflict_flags=list(type_result.get("reason_codes") or []),
            status=str(type_result.get("type_status") or "REVIEW_REQUIRED"),
            parent_ids=[unit_id],
        )
        type_decision.update({
            "type_status": type_result.get("type_status"),
            "reason_codes": list(type_result.get("reason_codes") or []),
            "alternate_types": list(type_result.get("alternate_types") or []),
            "supporting_evidence_ids": list(type_result.get("supporting_evidence_ids") or []),
            "conflicting_evidence_ids": list(type_result.get("conflicting_evidence_ids") or []),
            "typed_feature_evidence_count": len(evidence),
            "evidence_summary": dict(type_result.get("evidence_summary") or {}),
        })
        decisions.append(type_decision)

        primary = str(type_result.get("primary_type") or "UNCERTAIN").upper()
        status = str(type_result.get("type_status") or "REVIEW_REQUIRED")
        target_evidence = [
            row for row in evidence
            if str(row.get("candidate_type") or "") in TARGET_TYPE_NAMES
            and row.get("owner_local_binding", True) is not False
        ]
        if status == "NON_TARGET":
            unit["feature_type"] = "UNCERTAIN"
            unit["type_hint"] = "UNCERTAIN"
            unit["group_status"] = "NON_FEATURE_ONLY"
            unit["semantic_ready"] = False
        elif primary in TARGET_TYPE_NAMES and status == "CONFIRMED":
            unit["feature_type"] = primary
            unit["type_hint"] = primary
            unit["group_status"] = "CONFIRMED_FEATURE"
            unit["semantic_ready"] = True
        elif primary in TARGET_TYPE_NAMES and target_evidence:
            unit["feature_type"] = primary
            unit["type_hint"] = primary
            unit["group_status"] = "PROBABLE_FEATURE"
            unit["semantic_ready"] = True
        else:
            unit["feature_type"] = "UNCERTAIN"
            unit["type_hint"] = "UNCERTAIN"
            unit["group_status"] = "UNCERTAIN_FEATURE"
            unit["semantic_ready"] = False
        unit["type_decision_id"] = type_decision.get("type_decision_id")
        unit["type_evidence_ids"] = sorted({str(row.get("evidence_id") or "") for row in evidence if row.get("evidence_id")})
        updated_units.append(unit)

    _write_jsonl(output_root / "typed_feature_evidence.jsonl", typed_rows)
    _write_jsonl(output_root / "type_decisions.jsonl", decisions)
    _write_json(output_root / "type_summary.json", {
        "identity_units": len(updated_units),
        "typed_evidence": len(typed_rows),
        "by_type": dict(sorted(Counter(str(row.get("primary_type") or "UNCERTAIN") for row in decisions).items())),
        "by_status": dict(sorted(Counter(str(row.get("type_status") or "UNKNOWN") for row in decisions).items())),
    })
    return updated_units, decisions, {str(row.get("identity_unit_id") or ""): row for row in decisions}


def _build_evidence_layer(
    units: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    output_root: Path,
    *, profile=None, source_structure=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = build_indexes(units, occurrences, owners, documents,profile=profile)
    if source_structure is not None:indexes['source_structure']=source_structure
    from agents.contracts.evidence_cache import reuse_contracts,seal_bundle
    reuse=reuse_contracts(units,owners,documents,profile)
    bundles: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for unit in sorted(units, key=lambda row: str(row.get("identity_unit_id") or "")):
        # Evidence availability and safe publication are different contracts.
        # Review identities retain their source proof; Type/Admission still
        # decide whether they can be released. Missing type is not missing HTML.
        if str(unit.get("group_status") or "") not in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE", "UNCERTAIN_FEATURE"}:
            continue
        bundle = build_evidence_bundle(unit, indexes=indexes)
        from agents.contracts.source_binding import isolate_optional_sources
        bundle=isolate_optional_sources(unit,bundle,owners,documents,indexes['source_structure'])
        bundle["feature_type"] = str(unit.get("feature_type") or "UNCERTAIN").upper()
        bundle["identity_unit_id"] = str(unit.get("identity_unit_id") or "")
        by_block_owner: dict[tuple[str, str], dict[str, Any]] = {}
        owner_by_id = {str(row.get("owner_id") or ""): row for row in owners}
        page_by_id = {str(row.get("page_id") or ""): row for row in documents}
        source_by_id = {b['block_id']: b for d in documents for b in d.get('blocks', [])}
        for block in bundle.get("source_blocks") or []:
            if unit.get('profile_definition_required'):
                original = source_by_id.get(block.get('block_id'), {})
                if 'heading_level' in original:block['heading_level'] = original['heading_level']
            block_id = str(block.get("block_id") or "")
            owner_ids = list(block.get("owner_ids") or []) or list(unit.get("owner_ids") or [])
            for owner_id in owner_ids:
                owner = owner_by_id.get(str(owner_id)) or {}
                page = page_by_id.get(str(owner.get("page_id") or block.get("page_id") or "")) or {}
                if not owner or not page or not block_id:
                    continue
                roles = set(block.get("occurrence_roles") or [])
                structural_roles = set(block.get("structural_roles") or [])
                role = (
                    "FORMAL_SYNTAX" if "FORMAL_SYNTAX" in structural_roles or "FORMAL_VARIANT" in roles else
                    "EXAMPLE" if "EXAMPLE" in structural_roles else
                    "IDENTITY" if "IDENTITY" in structural_roles or "DEFINING_IDENTITY" in roles else
                    "SUPPORTING"
                )
                envelope = make_evidence_envelope(
                    dbms=str(page.get("dbms") or ""),
                    source_url=str(page.get("url") or ""),
                    page_id=str(page.get("page_id") or ""),
                    page_hash=str(page.get("page_hash") or ""),
                    html_path=str(page.get("html_path") or ""),
                    identity_unit_id=str(unit.get("identity_unit_id") or ""),
                    owner_id=str(owner_id), role=role,
                    raw_text=str(block.get("text") or ""),
                    source_block_ids=[block_id],
                    parent_ids=[str(unit.get("identity_unit_id") or ""), str(owner_id)],
                    reason_codes=["GENERIC_OWNER_ALIGNED_EVIDENCE"],
                )
                envelope.update({
                    "structural_roles": sorted(structural_roles),
                    "structural_role_reasons": dict(block.get("structural_role_reasons") or {}),
                    "owner_local_binding": bool(block.get("owner_local_binding", True)),
                    "owner_binding_reasons": list(block.get("owner_binding_reasons") or []),
                    "evidence_provenance": {
                        "definition_section_proofs": list(block.get("definition_section_proofs") or []),
                        "owner_scope_version": owner.get("owner_scope_version"),
                        "owner_scope_identity_values": list(owner.get("owner_scope_identity_values") or []),
                        "owner_scope_block_ids": list(owner.get("owner_scope_block_ids") or []),
                        "source_block_id": block_id,
                    },
                })
                key = (str(owner_id), block_id)
                by_block_owner[key] = envelope
        bundle["contract_evidence_ids"] = sorted({row["evidence_id"] for row in by_block_owner.values()})
        seal_bundle(bundle,reuse[unit['identity_unit_id']])
        bundles.append(bundle)
        envelopes.extend(by_block_owner.values())
    bundles.sort(key=lambda row: str(row.get("identity_group_id") or ""))
    envelopes.sort(key=lambda row: str(row.get("evidence_id") or ""))
    _write_jsonl(output_root / "bundles.jsonl", bundles)
    _write_jsonl(output_root / "evidence_envelopes.jsonl", envelopes)
    _write_json(output_root / "evidence_summary.json", {
        "identity_units_with_evidence": len(bundles),
        "bundles": len(bundles),
        "evidence_envelopes": len(envelopes),
        "subject_binding": "owner_aligned",
    })
    _write_json(output_root / "evidence_completeness.json", {
        "schema_version": "generic-evidence-completeness/v1",
        "bundles": len(bundles),
        "envelopes": len(envelopes),
        "source_blocks": sum(len(bundle.get("source_blocks") or []) for bundle in bundles),
        "bound_occurrences": sum(int((bundle.get("evidence_completeness") or {}).get("bound_occurrence_count") or 0) for bundle in bundles),
        "role_availability": {
            role: sum(len(bundle.get(role) or []) for bundle in bundles)
            for role in ("definition_evidence", "formal_syntax_evidence", "parameter_evidence", "return_type_evidence", "official_example_evidence")
        },
        "official_example_auto_promotion": False,
        "example_occurrence_records": sum(
            int((bundle.get("evidence_completeness") or {}).get("example_occurrence_count") or 0)
            for bundle in bundles
        ),
    })
    return bundles, envelopes


def _claim_items(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, Mapping):
            text = str(item.get("text") or item.get("value") or item.get("sql") or item.get('name') or '').strip()
            refs = [str(ref) for ref in item.get("evidence_refs") or [] if str(ref)]
        else:
            text, refs = str(item or '').strip(), []
        if text:
            result.append({**(dict(item) if isinstance(item,Mapping) else {}),"text": text, "evidence_refs": refs})
    return result


def _semantic_status(value: Any) -> str:
    text = str(value or "").upper()
    if text in {
        "NOT_RUN", "NOT_REQUESTED", "NOT_CONFIGURED", "CALL_FAILED",
        "PARSE_FAILED",
    }:
        return "UNAVAILABLE"
    return text if text in SEMANTIC_STATUS else "REVIEW_REQUIRED"


def _source_evidence(
    ref: str,
    documents: Mapping[str, Mapping[str, Any]],
    block_by_id: Mapping[str, Mapping[str, Any]],
    role: str,
) -> dict[str, Any] | None:
    block = block_by_id.get(str(ref))
    if not block:
        return None
    page_id = str(block.get("page_id") or "")
    page = documents.get(page_id) or {}
    text = str(block.get("text") or "")
    return {
        "url": str(page.get("url") or ""),
        "source_anchor": str(page.get("url") or ""),
        "page_id": page_id,
        "block_id": str(block.get("block_id") or ""),
        "block_type": str(block.get("block_kind") or block.get("block_type") or "OTHER"),
        "section_heading": " / ".join(str(x) for x in block.get("heading_ancestry") or []),
        "page_hash": str(page.get("page_hash") or ""),
        "evidence_role": role,
        "evidence_span": {"start": 0, "end": len(text), "text": text},
    }


def _bound_owner_projection(unit, bundle, owner_index):
    """Consume the exact Evidence owner range; never recompile default recipes.

    Unbound owners retain their original range. Credibility still validates
    block membership, source and applicability; this grants no permission.
    """
    from agents.contracts.source_binding import projected_owners
    return projected_owners(unit,bundle,owner_index)


def _make_contract_downstream(
    *,
    dbms: str,
    units: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
    envelopes: list[dict[str, Any]],
    semantic_root: Path,
    sense_root: Path,
    documents: list[dict[str, Any]],
    output_root: Path,
    owners: list[dict[str, Any]] | None = None,
    typed_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    document_by_id = {str(row.get("page_id") or ""): row for row in documents}
    block_by_id = {
        str(block.get("block_id") or ""): {**block, "page_id": page.get("page_id")}
        for page in documents for block in page.get("blocks") or [] if block.get("block_id")
    }
    # Project the same source-verified witness that grounded semantic fields.
    # Identity/Owner still consume the original IR; only its display witness
    # is replaced, never its membership, ID, URL or provenance.
    for bundle in bundles:
        for observed in bundle.get('source_blocks') or []:
            original=block_by_id.get(str(observed.get('block_id') or ''))
            if not original or observed.get('source_text_origin')!='HASH_VERIFIED_LITERAL_NODE':continue
            page=document_by_id.get(str(original['page_id'])) or {}
            from agents.contracts.literal_source import verified_literal_text
            literal=verified_literal_text(observed)
            if (observed.get('source_structure_status')=='RESOLVED'
                    and observed.get('source_hash')==page.get('page_hash')
                    and observed.get('source_url')==page.get('url')
                    and observed.get('text')==original.get('text')
                    and literal is not None):
                block_by_id[original['block_id']]={**original,'text':literal}
    unit_by_id = {str(row.get("identity_unit_id") or ""): row for row in units}
    decision_by_unit = {str(row.get("identity_unit_id") or ""): row for row in decisions}
    bundle_by_unit = {str(row.get("identity_group_id") or ""): row for row in bundles}
    owner_rows_for_contract = list(owners or [])
    if not owner_rows_for_contract:
        owner_rows_for_contract = [
            dict(owner)
            for candidate_bundle in bundles
            for owner in (candidate_bundle.get("owners") or [])
            if isinstance(owner, Mapping)
        ]
    owner_index = {
        str(row.get("owner_id") or ""): row
        for row in owner_rows_for_contract
        if row.get("owner_id")
    }
    page_index = {
        str(row.get("page_id") or ""): row
        for row in documents
        if row.get("page_id")
    }
    block_index = {
        str(block.get("block_id") or ""): (page, block)
        for page in documents
        for block in page.get("blocks") or []
        if block.get("block_id")
    }
    envelope_by_block: dict[tuple[str, str], dict[str, Any]] = {}
    for envelope in envelopes:
        for block_id in envelope.get("source_block_ids") or []:
            envelope_by_block[(str(envelope.get("identity_unit_id") or ""), str(block_id))] = envelope
    raw_semantic = _read_jsonl(semantic_root / "semantic" / "semantic_features.jsonl")
    extension_responses = {
        str(row.get('identity_group_id') or ''): row
        for row in _read_jsonl(semantic_root / 'semantic' / 'semantic_raw_responses.jsonl')
    }
    extension_source_index = SourceStructureIndex()
    sense_features = _read_jsonl(sense_root / "sense" / "semantic_features_sense_aware.jsonl")
    if not sense_features:
        sense_features = raw_semantic
    target_admission_path = semantic_root / "target" / "admission.jsonl"
    target_admission_rows = _read_jsonl(target_admission_path) if target_admission_path.is_file() else []
    target_admission_by_unit = {
        str(row.get("identity_group_id") or ""): row for row in target_admission_rows
    }
    target_gate_available = target_admission_path.is_file()

    def target_gate_allows(unit_id: str, unit: Mapping[str, Any]) -> bool:
        """Prevent probable/uncertain rows from bypassing the target gate.

        Confirmed Identity groups retain their direct path.  Probable and
        uncertain groups require an explicit AI_CLASSIFIED target decision;
        a missing gate artifact is never treated as an implicit TARGET.
        """

        from agents.contracts.profile_authority import verified_authority
        if verified_authority(unit, bundle_by_unit.get(unit_id) or {}):
            return True
        status = str(unit.get("group_status") or "").upper()
        if status == "CONFIRMED_FEATURE":
            if not target_gate_available:
                return True
            return str((target_admission_by_unit.get(unit_id) or {}).get("admission_status") or "") == "ADMITTED"
        if status in {"PROBABLE_FEATURE", "UNCERTAIN_FEATURE"}:
            admission = target_admission_by_unit.get(unit_id) or {}
            return (
                target_gate_available
                and str(admission.get("admission_status") or "") == "ADMITTED"
                and str(admission.get("target_resolution") or "") == "AI_CLASSIFIED"
            )
        return False
    semantic_primary: dict[str, dict[str, Any]] = {}
    for feature in raw_semantic:
        semantic_primary[str(feature.get("identity_group_id") or "")] = feature

    semantic_records: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    contract_canonical: list[dict[str, Any]] = []
    projection_canonical: list[dict[str, Any]] = []
    target_gate_decisions: list[dict[str, Any]] = []

    for unit_id, unit in sorted(unit_by_id.items()):
        if str(unit.get("group_status") or "") not in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE", "UNCERTAIN_FEATURE"}:
            continue
        gate_admission = target_admission_by_unit.get(unit_id) or {}
        gate_allowed = target_gate_allows(unit_id, unit)
        target_gate_decisions.append({
            "identity_unit_id": unit_id,
            "group_status": unit.get("group_status"),
            "target_classification": gate_admission.get("target_classification"),
            "target_resolution": gate_admission.get("target_resolution"),
            "admission_status": gate_admission.get("admission_status"),
            "allowed": gate_allowed,
            "reason": "CONFIRMED_DIRECT_PATH" if str(unit.get("group_status") or "") == "CONFIRMED_FEATURE" and gate_allowed else (
                "TARGET_CLASSIFICATION_REQUIRED" if str(unit.get("group_status") or "") in {"PROBABLE_FEATURE", "UNCERTAIN_FEATURE"} else "NOT_TARGET"
            ),
        })
        # Target failure is a review decision, not permission to discard the
        # candidate's already-grounded Semantic content from the audit dataset.
        source = _first_source(unit, document_by_id)
        bundle = bundle_by_unit.get(unit_id) or {}
        semantic = semantic_primary.get(unit_id) or {
            "identity_group_id": unit_id,
            "name": unit.get("canonical_identity"),
            "normalized_identity": unit.get("canonical_identity"),
            "feature_type": unit.get("feature_type"),
            "semantic_status": "NOT_RUN",
            "variants": unit.get("variants") or [],
            "sources": bundle.get("source_urls") or [],
            "owners": bundle.get("owners") or [],
        }
        semantic_status = _semantic_status(semantic.get("semantic_status"))
        claims: list[dict[str, Any]] = []
        field_roles = {
            "description": "DESCRIPTION",
            "formal_syntax": "FORMAL_SYNTAX",
            "official_examples": "EXAMPLE",
            "notes": "NOTE",
            "parameters": "PARAMETER",
            "return_type": "RETURN_TYPE",
        }
        for field, role in field_roles.items():
            for item in _claim_items(semantic.get(field)):
                refs = item.get("evidence_refs") or []
                for ref in refs:
                    envelope = envelope_by_block.get((unit_id, str(ref)))
                    if not envelope:
                        continue
                    claims.append({
                        "evidence_id": envelope["evidence_id"],
                        "role": role,
                        "value": item["text"],
                        "evidence_refs": refs,
                    })
        semantic_record = make_semantic_record(
            dbms=source["dbms"], page_id=source["page_id"], source_url=source["source_url"],
            page_hash=source["page_hash"], html_path=source["html_path"],
            identity_unit_id=unit_id, claims=claims, status=semantic_status,
            parent_ids=[unit_id, *[row["evidence_id"] for row in envelopes if row.get("identity_unit_id") == unit_id]],
            reason_codes=[] if claims else ["SEMANTIC_FIELDS_UNAVAILABLE"],
        )
        semantic_records.append(semantic_record)

        # Reconstruct the same proven evidence-only scopes used by the Bundle.
        # The validator still checks exact block membership; upstream Owner
        # and Type inputs remain unchanged.
        evidence_owner_index = _bound_owner_projection(unit, bundle, owner_index)
        credibility = evaluate_identity_credibility(
            unit,
            owners=owner_rows_for_contract,
            documents=documents,
            bundle=bundle,
            envelopes=[row for row in envelopes if str(row.get("identity_unit_id") or "") == unit_id],
            owner_index=evidence_owner_index,
            page_index=page_index,
            block_index=block_index,
            source_structure=extension_source_index,
        )
        owner_ok = bool(credibility.get("owner_evidence_correct"))
        publication_review = definition_publication_review(unit, bundle)
        integrity = evaluate_publication_integrity(
            unit, bundle, decision_by_unit.get(unit_id), {**gate_admission, "publication_gate_allowed": gate_allowed},
            typed_evidence=typed_evidence or [],
            source_identity_valid=all(credibility.get(k) for k in
                ('identity_credible', 'owner_evidence_correct', 'lineage_complete')),
        )
        admission = decide_admission(
            dbms=source["dbms"], page_id=source["page_id"], source_url=source["source_url"],
            page_hash=source["page_hash"], html_path=source["html_path"],
            identity_unit_id=unit_id,
            identity_credible=bool(credibility.get("identity_credible")),
            official_source=bool(source["source_url"].startswith(("http://", "https://"))),
            owner_evidence_correct=owner_ok,
            lineage_complete=bool(credibility.get("lineage_complete")),
            semantic_status=semantic_status,
            parent_ids=[unit_id, semantic_record["semantic_record_id"]],
            publication_review_reasons=publication_review['reason_codes'],
            publication_integrity=integrity,
        )
        admission["identity_credibility"] = credibility
        admission["publication_scope_review"] = publication_review
        admissions.append(admission)

    semantic_by_unit = {str(row.get("identity_unit_id") or ""): row for row in semantic_records}
    admission_by_unit = {str(row.get("identity_unit_id") or ""): row for row in admissions}
    for feature in sorted(sense_features, key=lambda row: (str(row.get("identity_group_id") or ""), str(row.get("sense_id") or ""), str(row.get("feature_id") or ""))):
        unit_id = str(feature.get("identity_group_id") or "")
        unit = unit_by_id.get(unit_id)
        admission = admission_by_unit.get(unit_id)
        semantic_record = semantic_by_unit.get(unit_id)
        bundle = bundle_by_unit.get(unit_id) or {}
        if not unit or not admission or not semantic_record:
            continue
        source = _first_source(unit, document_by_id)
        feature_type = str(unit.get("feature_type") or feature.get("feature_type") or "UNCERTAIN").upper()
        from agents.contracts.publication_integrity import publication_integrity_reasons
        if feature_type not in TARGET_TYPE_NAMES:
            integrity = admission.get('publication_integrity') or {}
            if not (feature_type == 'UNCERTAIN' and integrity.get('explicit_profile_authority')
                    and not publication_integrity_reasons(integrity, unit_id)):
                continue
        envelope_ids = [str(row.get("evidence_id") or "") for row in envelopes if str(row.get("identity_unit_id") or "") == unit_id]
        fields = {
            "description": feature.get("description"),
            "formal_syntax": feature.get("formal_syntax") or [],
            "official_examples": feature.get("official_examples") or [],
            "notes": feature.get("notes") or [],
            "parameters":feature.get('parameters') or [],
            "return_type":feature.get('return_type'),
        }
        quality_status = {
            "ADMITTED": "READY",
            "ADMITTED_PARTIAL": "PARTIAL",
        }.get(str(admission.get("decision") or ""), "REVIEW_REQUIRED")
        extension_payload, extension_diagnostics = response_extension_payload(
            semantic_primary.get(unit_id) or {}, extension_responses.get(unit_id),
        )
        datatype_extensions = build_datatype_extensions(
            unit=unit, bundle=bundle, documents=documents, owners=owner_rows_for_contract,
            envelopes=envelopes, payload=extension_payload, source_index=extension_source_index,
            initial_diagnostics=extension_diagnostics,
        )
        contract = make_canonical_feature(
            dbms=source["dbms"], page_id=source["page_id"], source_url=source["source_url"],
            page_hash=source["page_hash"], html_path=source["html_path"],
            identity_unit_id=unit_id, name=str(unit.get("canonical_identity") or feature.get("name") or ""),
            feature_type=feature_type, admission_id=str(admission["admission_id"]),
            semantic_record_id=str(semantic_record["semantic_record_id"]), evidence_ids=envelope_ids,
            fields=fields, quality_status=quality_status,
            parent_ids=[unit_id, admission["admission_id"], semantic_record["semantic_record_id"], *envelope_ids],
            semantic_extensions=datatype_extensions,
        )
        contract.update({"semantic_status": feature.get('semantic_status') or semantic_record["status"], "sense_id": feature.get("sense_id"), "semantic_feature_id": feature.get("feature_id")})
        contract_canonical.append(contract)
        sense_label = str(feature.get("semantic_sense") or "DEFAULT")
        normalized = str(unit.get("canonical_identity") or feature.get("normalized_identity") or "").strip()
        canonical_name = normalized
        from agents.contracts.identity_key import canonical_identity_key
        canonical_key = canonical_identity_key(dbms,normalized,feature_type,sense_label)
        canonical_id = "canonical:" + _hash([unit_id, feature_type, sense_label])[:24]

        def projection_claims(field: str, frontend_field: str) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for item in _claim_items(feature.get(field)):
                refs = item.get("evidence_refs") or []
                evidence = _source_evidence(refs[0], document_by_id, block_by_id, frontend_field) if refs else None
                if evidence:
                    all_evidence=[e for ref in refs for e in [_source_evidence(ref,document_by_id,block_by_id,frontend_field)] if e]
                    from agents.semantic_stage.grounding import normalize_for_match
                    matched=next((e for e in all_evidence if normalize_for_match(item['text']) in normalize_for_match(e['evidence_span']['text'])),evidence)
                    result.append({"value": item["text"], "evidence": evidence,'evidence_refs':refs,
                        **{k:item[k] for k in ('fragment_refs','source_locations','field_evidence') if k in item},
                        'additional_provenance':[{'evidence':e} for e in all_evidence if e['block_id']!=matched['block_id']],
                        **({k:item[k] for k in ('name','description') if k in item} if field=='parameters' else {})})
                    result[-1]['evidence']=matched
            return result

        all_refs = [str(x.get("block_id") or "") for x in bundle.get("source_blocks") or [] if x.get("block_id") and x.get('evidence_usage')!='DISCOVERY_ONLY']
        primary_ref = all_refs[0] if all_refs else ""
        primary_evidence = _source_evidence(primary_ref, document_by_id, block_by_id, "IDENTITY") if primary_ref else None
        supporting = [item for item in (_source_evidence(ref, document_by_id, block_by_id, "SUPPORTING") for ref in all_refs[1:]) if item]
        projection_canonical.append({
            "schema_version": "canonical-feature-kb-projection/v1",
            "feature_id": canonical_id,
            "canonical_identity": {
                "key": canonical_key,
                "dbms": dbms,
                "feature_type": feature_type.casefold(),
                "canonical_name": canonical_name,
                "discriminator": sense_label,
            },
            "dbms": dbms,
            "doc_version": "current",
            "feature_type": feature_type.casefold(),
            "name": canonical_name,
            "canonical_name": canonical_name,
            "structural": {
                "name": canonical_name,
                "canonical_name": canonical_name,
                "signatures": list(unit.get("variants") or []),
                "return_types": [i['text'] for i in _claim_items(feature.get('return_type'))],
                "aliases": list(unit.get("aliases") or []),
                "attributes": {"identity_unit_id": unit_id, "sense_id": feature.get("sense_id"),
                    "field_reviews": feature.get('field_reviews',[]),
                    'quarantined_fields':[{'field':q.get('field'),'reason':q.get('reason'),'status':'QUARANTINED'}
                        for q in feature.get('rejected_fields',[])]},
                "primary_evidence": primary_evidence or {},
                "supporting_evidence": supporting,
            },
            "semantics": {
                "syntax": projection_claims("formal_syntax", "FORMAL_SYNTAX"),
                "description": projection_claims("description", "DESCRIPTION"),
                "examples": projection_claims("official_examples", "EXAMPLE"),
                "aliases": [],
                "constraints": projection_claims("notes", "NOTE"),
                "parameters":projection_claims('parameters','PARAMETER'),
                "return_type":projection_claims('return_type','RETURN_TYPE'),
            },
            "semantic_status": str(feature.get("semantic_status") or semantic_record["status"]),
            "semantic_execution_state":feature.get('semantic_execution_state') or feature.get('execution_status'),
            "field_validation_status":feature.get('field_validation_status'),
            "promotion_eligibility": "promotion_ready" if admission.get("decision") in {"ADMITTED", "ADMITTED_PARTIAL"} else "review_required",
            "quality": {
                "fields_populated": [key for key, value in fields.items() if _claim_items(value)],
                "fields_missing": [key for key, value in fields.items() if not _claim_items(value)],
                "structural_evidence_resolved": bool(primary_evidence),
                "semantic_evidence_resolved": True,
                "ownership_ambiguity": not bool(admission.get('owner_evidence_correct')),
                "conflict_state": "clear" if (admission.get('publication_integrity') or {}).get('state')=='CONFIRMED' else 'review_required',
            },
            "provenance": {
                "identity_unit_id": unit_id,
                "admission_id": admission["admission_id"],
                "semantic_record_id": semantic_record["semantic_record_id"],
                "evidence_ids": envelope_ids,
                "producer_stage": "generic_canonical_projection",
                "producer_version": "generic-unified-contract/v1",
            },
        })
        if datatype_extensions is not None:
            # Top-level optional knowledge is deliberately outside semantics:
            # the unchanged Phase4B resolver treats semantics entries as lists.
            projection_canonical[-1]['semantic_extensions'] = datatype_extensions

    _write_jsonl(output_root / "semantic_records.jsonl", semantic_records)
    _write_jsonl(output_root / "admission_decisions.jsonl", admissions)
    _write_jsonl(output_root / "target_gate_decisions.jsonl", target_gate_decisions)
    _write_jsonl(output_root / "canonical_features.jsonl", contract_canonical)
    _write_json(output_root / "canonical_kb.json", projection_canonical)
    _write_jsonl(output_root/'canonical_identity_key_migration.jsonl',[
        {'feature_id':r['feature_id'],'old_key':f"{dbms}|{r['feature_type']}|{r['name'].casefold()}|{r['canonical_identity']['discriminator'].casefold()}",
         'new_key':r['canonical_identity']['key'],'identity_unit_id':r['provenance']['identity_unit_id']}
        for r in projection_canonical])
    _write_json(output_root / "canonical_summary.json", {
        "contract_records": len(contract_canonical),
        "projection_records": len(projection_canonical),
        "target_gate": {
            "artifact_available": target_gate_available,
            "evaluated": len(target_gate_decisions),
            "allowed": sum(bool(row.get("allowed")) for row in target_gate_decisions),
            "excluded": sum(not bool(row.get("allowed")) for row in target_gate_decisions),
        },
        "by_type": dict(sorted(Counter(str(row.get("feature_type") or "UNKNOWN") for row in contract_canonical).items())),
        "by_quality": dict(sorted(Counter(str(row.get("quality_status") or "UNKNOWN") for row in contract_canonical).items())),
    })
    return {
        "semantic_records": semantic_records,
        "admissions": admissions,
        "contract_canonical": contract_canonical,
        "projection_canonical": projection_canonical,
    }


def _run_sense(semantic_root: Path, identity_root: Path, output_root: Path) -> dict[str, Any]:
    features = _read_jsonl(semantic_root / "semantic" / "semantic_features.jsonl")
    if not features:
        output_root.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output_root / "sense" / "semantic_features_sense_aware.jsonl", [])
        _write_json(output_root / "evaluation" / "sense_summary.json", {
            "pipeline_id": "generic-semantic-sense-audit/v1",
            "input_feature_count": 0,
            "status": "NOT_RUN_NO_SEMANTIC_FEATURES",
        })
        return {"status": "not_run", "summary": {"input_feature_count": 0}}
    return run_sense_audit(
        semantic_root=semantic_root,
        output_root=output_root,
        identity_root=identity_root,
        run_llm=False,
    )


def _write_conservation_audit(
    output_root: Path,
    *,
    candidates: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    units: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
    semantic_root: Path,
    admissions: list[dict[str, Any]],
    contract_canonical: list[dict[str, Any]],
    frontend_manifest: Mapping[str, Any],
    final_records=None,
    publication_decisions=None,
) -> dict[str, Any]:
    """Write a unit-level first-loss ledger for one bounded replay."""
    semantic_rows = _read_jsonl(semantic_root / "semantic" / "semantic_features.jsonl")
    unit_by_id = {str(row.get("identity_unit_id") or ""): row for row in units}
    candidate_by_id = {str(row.get("candidate_id") or ""): row for row in candidates}
    accepted_by_id = {str(row.get("candidate_id") or ""): row for row in accepted}
    decision_by_unit = {str(row.get("identity_unit_id") or ""): row for row in decisions}
    bundle_by_unit = {str(row.get("identity_group_id") or ""): row for row in bundles}
    semantic_by_unit = {str(row.get("identity_group_id") or ""): row for row in semantic_rows}
    admission_by_unit = {str(row.get("identity_unit_id") or ""): row for row in admissions}
    canonical_by_unit = {str(row.get("identity_unit_id") or ""): row for row in contract_canonical}
    published={r['provenance']['identity_unit_id']:r['feature_id'] for r in final_records or []}
    merged={v['identity_unit_id']:r['feature_id'] for r in final_records or []
        for v in r.get('structural',{}).get('attributes',{}).get('canonical_representation_lineage',[])
        if v['identity_unit_id']!=r['provenance']['identity_unit_id']}
    pub_by_unit={}
    for p in publication_decisions or []:pub_by_unit.setdefault(p['identity_unit_id'],[]).append(p)
    rows: list[dict[str, Any]] = []
    for unit_id, unit in sorted(unit_by_id.items()):
        decision = decision_by_unit.get(unit_id) or {}
        bundle = bundle_by_unit.get(unit_id)
        semantic = semantic_by_unit.get(unit_id)
        admission = admission_by_unit.get(unit_id)
        canonical = canonical_by_unit.get(unit_id)
        unit_candidate_ids = [str(value) for value in (unit.get("candidate_ids") or []) if str(value)]
        candidate_count = sum(1 for value in unit_candidate_ids if value in candidate_by_id)
        accepted_count = sum(1 for value in unit_candidate_ids if value in accepted_by_id)
        if unit_id in published or unit_id in merged:
            first_loss='RETAINED' if unit_id in published else 'MERGED_INTO'
        elif not candidate_count and not unit.get('adaptation_provenance'):
            first_loss = "IDENTITY"
        elif str(unit.get("group_status") or "") not in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE"}:
            first_loss = "TYPE_DECISION"
        elif bundle is None:
            first_loss = "EVIDENCE"
        elif semantic is None:
            first_loss = "SEMANTIC"
        elif admission is None:
            first_loss = "ADMISSION"
        elif admission.get('decision') not in {'ADMITTED','ADMITTED_PARTIAL'}:
            first_loss='ADMISSION'
        elif canonical is None:
            first_loss = "CANONICAL"
        elif final_records is not None:
            first_loss='PUBLICATION'
        else:
            first_loss = "RETAINED"
        rows.append({
            "identity_unit_id": unit_id,
            "identity": unit.get("canonical_identity"),
            "feature_type": unit.get("feature_type"),
            "group_status": unit.get("group_status"),
            "type_status": decision.get("type_status"),
            "type_reason_codes": decision.get("reason_codes") or [],
            "candidate_count": candidate_count,
            "validated_candidate_count": accepted_count,
            "evidence": bool(bundle),
            "evidence_block_count": len((bundle or {}).get("source_blocks") or []),
            "semantic": bool(semantic),
            "semantic_execution_status": (semantic or {}).get("execution_status"),
            "admission_decision": (admission or {}).get("decision"),
            "canonical": bool(canonical),
            'published_feature_id':published.get(unit_id) or merged.get(unit_id),
            'publication_decisions':pub_by_unit.get(unit_id,[]),
            "first_loss": first_loss,
        })
    counts = Counter(str(row.get("first_loss") or "UNKNOWN") for row in rows)
    audit = {
        "schema_version": "generic-conservation-audit/v1",
        "unit_count": len(rows),
        "candidate_count": len(candidates),
        "validated_candidate_count": len(accepted),
        "type_decision_count": len(decisions),
        "evidence_bundle_count": len(bundles),
        "semantic_feature_count": len(semantic_rows),
        "admission_count": len(admissions),
        "canonical_count": len(contract_canonical),
        "frontend_count": int(frontend_manifest.get("frontend_records") or 0),
        "first_loss_counts": dict(sorted(counts.items())),
        "rows": rows,
        "conservation": {
            # ``NEEDS_REVIEW`` is still an explicit AdmissionDecision and is
            # intentionally projected as a review-required canonical row.
            # Conservation therefore compares against all emitted admission
            # decisions; promotion readiness is tracked separately by the
            # admission decision itself.
            "canonical_not_greater_than_admitted": len(contract_canonical) <= len(admissions),
            "frontend_not_greater_than_canonical": int(frontend_manifest.get("frontend_records") or 0) <= len(contract_canonical),
        },
    }
    _write_json(output_root / "evaluation" / "conservation_audit.json", audit)
    _write_jsonl(output_root/'evaluation'/'first_loss.jsonl',rows)
    return audit


def _copy_frontend_static(output_root: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "frontend_projection" / "static"
    destination = output_root / "frontend" / "web"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("index.html",):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    for directory in ("css", "js"):
        if (source / directory).is_dir():
            shutil.copytree(source / directory, destination / directory, dirs_exist_ok=True)


def _explicit_profile_mode(profile):
    from agents.owner.structure_execution import compile_structure_plan
    if ((profile.get('hint_contract') or {}).get('authority_scope') == 'CONFIGURED_PLANS_ONLY'
            and (profile.get('hint_contract') or {}).get('selection_authority') == 'OWNER_CANDIDATE_INFERENCE'):
        return False
    return ((profile.get('hint_contract') or {}).get('selection_authority') == 'EXPLICIT_PROFILE_DEFINITIONS'
            or any((p.get('execution') or {}).get('authority') == 'EXPLICIT_DEFINITION'
                   for p in compile_structure_plan(profile)))


def _effective_profile_types(units, decisions, owners, documents, *, authority_required, configured_only=True):
    """Apply verified Profile types while preserving inference inputs unchanged."""
    if not authority_required:
        return units, decisions
    from agents.contracts.profile_authority import verified_authority
    from agents.contracts.type_decision import make_type_decision
    from agents.contracts.field_validation import digest
    evidence = {'owners': owners, 'source_blocks': [
        dict(b, source_hash=d.get('page_hash'), source_url=d.get('url'))
        for d in documents for b in d.get('blocks', [])]}
    authorities = {}
    effective_sources = {}
    effective_units = []
    for original in units:
        unit = dict(original)
        if configured_only:
            unit['profile_definition_required'] = True
        authority = verified_authority(unit, evidence)
        if authority:
            defining_pages = [d for d in documents
                if d.get('url') == authority['source_url'] and d.get('page_hash') == authority['source_hash']
                and set(authority['evidence_refs']).intersection(b['block_id'] for b in d.get('blocks', []))
                and any(o.get('owner_id') in authority['owner_ids'] and o.get('owner_id') in unit.get('owner_ids', [])
                        and o.get('page_id') == d['page_id'] for o in owners)]
            if not defining_pages:
                authority = None
        if authority:
            primary = defining_pages[0]
            unit['profile_definition_required'] = True
            unit['inference_provenance'] = dict(original.get('provenance') or {})
            unit['provenance'] = dict(original.get('provenance') or {},
                page_id=primary['page_id'], source_url=primary['url'], page_hash=primary['page_hash'],
                html_path=primary['html_path'], dbms=primary.get('dbms') or (original.get('provenance') or {}).get('dbms', ''))
            effective_sources[unit['identity_unit_id']] = unit['provenance']
            kind = authority['feature_type']
            unit.update(feature_type=kind, type_hint=kind, semantic_ready=True,
                        group_status='UNCERTAIN_FEATURE' if kind == 'UNCERTAIN' else 'CONFIRMED_FEATURE')
            authorities[unit['identity_unit_id']] = authority
        effective_units.append(unit)
    effective_decisions = []
    for inference in decisions:
        uid = inference['identity_unit_id']
        authority = authorities.get(uid)
        if not authority:
            effective_decisions.append(inference)
            continue
        kind = authority['feature_type']
        # Preserve contract provenance, but attribute the effective decision to
        # the source-verified Profile rather than its independent inference.
        prov = dict(inference['provenance'], **effective_sources[uid])
        decision = make_type_decision(
            dbms=prov['dbms'], page_id=prov['page_id'], source_url=prov['source_url'],
            page_hash=prov['page_hash'], html_path=prov['html_path'], identity_unit_id=uid,
            primary_type=kind, confidence=0.0 if kind == 'UNCERTAIN' else 1.0,
            status='UNCERTAIN' if kind == 'UNCERTAIN' else 'CONFIRMED',
            parent_ids=prov.get('parent_ids') or [uid])
        decision.update(source='EXPLICIT_PROFILE', type_status=decision['status'],
                        explicit_profile_authority=authority, supporting_evidence_ids=[],
                        conflicting_evidence_ids=[], alternate_types=[],
                        source_evidence_refs=authority['evidence_refs'],
                        inference_decision_sha256=digest(inference))
        effective_decisions.append(decision)
    return effective_units, effective_decisions


def run_generic_downstream(
    *,
    dbms: str,
    documents: list[dict[str, Any]],
    profile: Mapping[str, Any],
    output_root: str | Path,
    semantic: bool = False,
    semantic_provider: str = "openai_compatible",
    semantic_model: str | None = None,
    semantic_budget: int | None = None,
    offline_evidence_fallback: bool = False,
) -> dict[str, Any]:
    """Run the authoritative Generic Profile -> Frontend chain offline."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    from agents.profile_bootstrap.cold_start import validate_cold_start_profile
    profile=validate_cold_start_profile(profile)
    authority_required = _explicit_profile_mode(profile)
    from agents.owner.structure_execution import compile_structure_plan
    authority_present = authority_required or any(
        (p.get('execution') or {}).get('authority') == 'EXPLICIT_DEFINITION'
        for p in compile_structure_plan(profile))
    # Reuse the accepted shared production presemantic orchestration.
    from agents.owner.source_relations import prepare_relation_documents
    documents=prepare_relation_documents(documents,profile)
    # No Owner/Identity/TypeDecision rule is reimplemented here.
    from agents.pipeline.presemantic import run_frozen_presemantic
    prepared = run_frozen_presemantic(dbms=dbms, documents=documents, output_root=root / 'presemantic',
                                     run_id='unified-production', type_adaptation=not authority_required,
                                     operator_closure=not authority_required, profile=profile)
    identity_result = prepared['identity']
    owners = list(identity_result['owners'])
    occurrences = list(identity_result.get('occurrences', []))
    candidates = list(identity_result['candidates'])
    accepted = list(identity_result['accepted'])
    rejected = list(identity_result['rejected'])
    units = list(identity_result['units'])
    _write_jsonl(root / 'owners' / 'owners.jsonl', owners)
    from agents.contracts.definition_unit import build_definition_units
    from agents.owner.scope_plan import compile_scope_plan
    definition_reader=SourceStructureIndex()
    definition_units=build_definition_units(documents,owners,profile,source_structure=definition_reader)
    _write_jsonl(root / 'owners' / 'definition_units.jsonl', definition_units)
    from agents.owner.structure_execution import compile_structure_plan, execute_structures
    structure_diagnostics=[]
    for document in documents:
        result=execute_structures(document,profile)
        structure_diagnostics.append({'page_id':document['page_id'],'source_url':document.get('url'),
                                      'diagnostics':result['diagnostics'],'counts':result['counts'],
                                      'rejected':result['rejected']})
    _write_jsonl(root/'owners'/'structure_execution_diagnostics.jsonl',structure_diagnostics)
    _write_json(root/'owners'/'structure_execution_summary.json',{
        'schema_version':'StructureExecutionSummary/v1',
        'plans':compile_structure_plan(profile),
        'definition_units_by_structure':dict(Counter((u.get('structure_execution') or {}).get('structure','UNCLASSIFIED')
                                                    for u in definition_units)),
        'invalid_source_units':sum((u.get('structure_execution') or {}).get('status')=='INVALID' for u in definition_units),
        'scope':'source structure only; not Feature recall, semantic completeness or publication permission'})
    _write_json(root / 'owners' / 'definition_unit_summary.json', {
        'schema_version':'DefinitionUnitSummary/v1',
        'units':len(definition_units),
        'statuses':dict(Counter(u['status'] for u in definition_units)),
        'patterns':dict(Counter(str(u['producer_pattern']) for u in definition_units)),
        'profile_digest':definition_units[0]['profile_digest'] if definition_units else None,
        'scope_plan_execution':[
            {'plan':p,'source_units':sum(u.get('execution_plan')==p for u in definition_units)}
            for p in compile_scope_plan(profile)],
    })
    _write_jsonl(root / "identity" / "candidates.jsonl", candidates)
    _write_jsonl(root / "identity" / "validated_candidates.jsonl", accepted)
    _write_jsonl(root / "identity" / "rejected_candidates.jsonl", rejected)
    _write_jsonl(root / "identity" / "occurrences.jsonl", occurrences)
    _write_jsonl(root / "identity" / "groups_pre_type.jsonl", units)

    typed_units = list(prepared['typed_units'])
    if authority_required:
        units = [dict(u, profile_definition_required=True) for u in units]
    inference_decisions = list(prepared['type_decisions'])
    typed_units, type_decisions = _effective_profile_types(
        typed_units, inference_decisions, owners, documents, authority_required=authority_present,
        configured_only=authority_required)
    shutil.copytree(root/'presemantic'/'type',root/'type',dirs_exist_ok=True)
    if authority_present:
        _write_jsonl(root/'type'/'type_inference_decisions.jsonl', inference_decisions)
        _write_jsonl(root/'type'/'type_decisions.jsonl', type_decisions)
        _write_json(root/'type'/'type_summary.json', {
            'identity_units': len(typed_units),
            'typed_evidence': len(_read_jsonl(root/'type'/'typed_feature_evidence.jsonl')),
            'by_type': dict(sorted(Counter(r['primary_type'] for r in type_decisions).items())),
            'by_status': dict(sorted(Counter(r.get('type_status') or r['status'] for r in type_decisions).items())),
            'inference_audit': 'type_inference_decisions.jsonl',
        })
    _write_jsonl(root / "identity" / "groups.jsonl", typed_units)
    identity_summary = {**identity_result["stats"], "typed_units": len(typed_units), "groups_by_status_after_type": dict(sorted(Counter(str(row.get("group_status") or "UNKNOWN") for row in typed_units).items()))}
    _write_json(root / "identity" / "summary.json", identity_summary)
    _write_jsonl(root / "document_ir" / "documents.jsonl", documents)

    bundles, envelopes = _build_evidence_layer(typed_units, occurrences, owners, documents, root / "evidence",
                                             profile=profile,source_structure=definition_reader)
    semantic_root = root / "semantic_stage"
    semantic_result = run_semantic_stage(
        identity_root=root,
        output_root=semantic_root,
        evidence_root=root,
        run_llm=bool(semantic),
        provider=semantic_provider,
        model=semantic_model,
        semantic_budget=semantic_budget,
        offline_evidence_fallback=offline_evidence_fallback,
    )
    sense_root = root / "sense"
    sense_result = _run_sense(semantic_root, root, sense_root)
    downstream = _make_contract_downstream(
        dbms=dbms, units=typed_units, decisions=type_decisions, bundles=bundles,
        envelopes=envelopes, semantic_root=semantic_root, sense_root=sense_root,
        documents=documents, output_root=root / "canonical", owners=owners,
        typed_evidence=_read_jsonl(root / "type" / "typed_feature_evidence.jsonl"),
    )
    projection_path = root / "canonical" / "canonical_kb.json"
    release_lineage = validate_parent_lineage({
        'document_ir':documents,'owner_hypothesis':owners,'identity_candidate':accepted,'identity_unit':typed_units,
        'type_decision':type_decisions,'evidence_envelope':envelopes,'semantic_record':downstream['semantic_records'],
        'admission_decision':downstream['admissions'],'canonical_feature':downstream['contract_canonical']})
    from agents.contracts.canonical_integrity import resolve_canonical_representations
    release_validations = _read_jsonl(semantic_root/'semantic'/'semantic_validation.jsonl')
    release_canonical, canonical_reconciliation = resolve_canonical_representations(
        downstream['projection_canonical'], units=typed_units, bundles=bundles,
        admissions=downstream['admissions'], validations=release_validations, lineage=release_lineage)
    _write_json(root/'canonical'/'consolidated_canonical_kb.json', release_canonical)
    _write_jsonl(root/'canonical'/'canonical_representation_decisions.jsonl', canonical_reconciliation)
    final_records, review_records, publication_decisions = select_publication(
        release_canonical, downstream['admissions'],
        release_validations,release_lineage,
        integrity_required=True)
    projection_path = root/'canonical'/'promotion_ready_kb.json'
    _write_json(projection_path,final_records)
    _write_jsonl(root/'canonical'/'review_features.jsonl',review_records)
    _write_jsonl(root/'canonical'/'publication_decisions.jsonl',publication_decisions)
    from agents.semantic_stage.content_completeness import audit_final_content
    _write_jsonl(root/'evaluation'/'final_content_completeness.jsonl',audit_final_content(
        _read_jsonl(semantic_root/'semantic'/'semantic_features.jsonl'),final_records,
        _read_jsonl(sense_root/'sense'/'semantic_features_sense_aware.jsonl')))
    from agents.semantic_stage.content_completeness import audit_profile_dispositions
    field_accounting=audit_profile_dispositions(
        _read_jsonl(semantic_root/'semantic'/'profile_field_consumption.jsonl'),typed_units,final_records)
    _write_jsonl(root/'evaluation'/'profile_field_accounting.jsonl',field_accounting)
    from agents.semantic_stage.source_field_ledger import audit_source_fields
    semantic_index={f['identity_group_id']:f for f in _read_jsonl(semantic_root/'semantic'/'semantic_features.jsonl')}
    source_ledger=[row for b in bundles for row in audit_source_fields(b,semantic_index.get(b['identity_group_id'],{}),final_records)]
    _write_jsonl(root/'evaluation'/'source_field_ledger.jsonl',source_ledger)
    from agents.semantic_stage.field_dispositions import audit_field_dispositions
    _write_jsonl(root/'evaluation'/'field_dispositions.jsonl',audit_field_dispositions(bundles,list(semantic_index.values()),final_records))
    _write_json(root/'evaluation'/'source_field_ledger_summary.json',{
        'basis':'Profile physical slots and observed labels; no semantic extractor generates expectations',
        'scope':'Known source fields only; unknown roles and official inventory not certified',
        'dispositions':dict(Counter(r['disposition'] for r in source_ledger))})
    _write_json(root/'evaluation'/'profile_field_accounting_summary.json',{
        'scope':'explicit source fields; not official inventory completeness or publication permission',
        'dispositions':dict(Counter(r['disposition']for r in field_accounting)),
        'unexplained_final_field_gaps':sum(r['disposition']=='FINAL_FIELD_GAP'for r in field_accounting)})
    _copy_frontend_static(root)
    if final_records:
        frontend_manifest = write_frontend_projection(
            {dbms: projection_path}, root / "frontend" / "data", root / "frontend" / "web" / "js" / "data.js",
        )
    else:
        frontend_manifest = {"canonical_records": 0, "frontend_records": 0, "identity_preserved": True, "dbms": [dbms]}
        (root/'frontend'/'web'/'js'/'data.js').write_text('window.FEATURE_DATA = []; window.QUALITY_DATA = {};',encoding='utf8')
        _write_json(root / "frontend" / "data" / f"{dbms}.json", {"features": [], "counts": {"canonical_total": 0}})
        _write_json(root / "frontend" / "data" / "index.json", frontend_manifest)
    conservation = _write_conservation_audit(
        root,
        candidates=candidates,
        accepted=accepted,
        units=typed_units,
        decisions=type_decisions,
        bundles=bundles,
        semantic_root=semantic_root,
        admissions=downstream["admissions"],
        contract_canonical=downstream["contract_canonical"],
        frontend_manifest=frontend_manifest,
        final_records=final_records,
        publication_decisions=publication_decisions,
    )
    lineage = validate_parent_lineage({
        "document_ir": documents,
        "owner_hypothesis": owners,
        "identity_candidate": accepted,
        "identity_unit": typed_units,
        "type_decision": type_decisions,
        "evidence_envelope": envelopes,
        "semantic_record": downstream["semantic_records"],
        "admission_decision": downstream["admissions"],
        "canonical_feature": downstream["contract_canonical"],
    })
    _write_json(root / "lineage_audit.json", lineage)
    manifest = {
        "schema_version": "generic-unified-execution-manifest/v1",
        "pipeline": "unified",
        "profile_source": "generic_structural_profile",
        "identity_source": "generic_identity_unit",
        "type_source": "generic_owner_local_typed_evidence",
        "admission_source": "generic_identity_and_evidence_contract",
        "evidence_source": "generic_owner_aligned_evidence_bundle",
        "semantic_source": "generic_evidence_grounded_semantic_stage",
        "grounding_source": "generic_semantic_validator",
        "sense_source": "generic_semantic_sense_audit",
        "canonical_source": "generic_canonical_projection",
        "frontend_source": "generic_frontend_projection_v2",
        "legacy_profile_dsl_used": False,
        "legacy_formal_candidate_used": False,
        "legacy_fallback_used": False,
        "legacy_adapter_used": False,
        "db_specific_rules_used": False,
        "feature_inventory_used": False,
        "counts": {
            "documents": len(documents), "owners": len(owners), "identity_units": len(typed_units),
            "type_decisions": len(type_decisions), "evidence_envelopes": len(envelopes),
            "semantic_records": len(downstream["semantic_records"]), "admissions": len(downstream["admissions"]),
            "canonical_features": len(downstream["contract_canonical"]), "frontend_features": frontend_manifest.get("frontend_records", 0),
            "conservation_first_loss": conservation.get("first_loss_counts", {}),
        },
        "lineage": lineage,
        "semantic": semantic_result.get("summary", {}),
        "sense": sense_result.get("summary", {}),
    }
    _write_json(root / "execution_manifest.json", manifest)
    return {
        "status": "accepted",
        "publication": {'final_count':len(final_records),'review_count':len(review_records),'decisions':publication_decisions},
        "identity": identity_summary,
        "type": {"decisions": type_decisions, "units": typed_units},
        "evidence": {"bundles": bundles, "envelopes": envelopes},
        "semantic": semantic_result,
        "sense": sense_result,
        "conservation": conservation,
        "canonical": downstream,
        "frontend": frontend_manifest,
        "lineage": lineage,
        "execution_manifest": manifest,
    }


__all__ = ["run_generic_downstream"]
