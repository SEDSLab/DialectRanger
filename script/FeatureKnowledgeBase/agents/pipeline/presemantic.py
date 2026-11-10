"""Generic offline Pre-Semantic pipeline over a frozen documentation corpus.

This module is intentionally upstream of Semantic/Admission/Canonical. It
reuses the production DocumentIR adapter, Owner/Identity path, and generic
owner-local TypeDecision implementation, while making no provider call and
writing only the supplied experiment output directory.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.frozen_surface import build_text_page_topology
from agents.document_structure_v2.topology import build_page_topology
from agents.identity.review import write_identity_quality_artifacts
from agents.pipeline.generic_unified import _build_typed_layer
from agents.pipeline.production_identity import execute_production_identity_path
from agents.profile_bootstrap.cold_start import build_cold_start_profile, profile_digest


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _norm_url(value: Any) -> str:
    parsed = urlsplit(str(value or "").strip())
    path = parsed.path or "/"
    if path.casefold().endswith((".html", ".htm", ".md", ".markdown", ".txt")):
        path = path.rsplit(".", 1)[0]
    path = path.rstrip("/") or "/"
    if path.casefold().endswith("/index"):
        path = path[:-6] or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, parsed.query, ""))


def _norm_identity(value: Any) -> str:
    text = " ".join(str(value or "").replace("\xa0", " ").split()).strip(" \t\r\n\"'")
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    return text.casefold()


def _resolve_artifact_path(page_manifest_path: Path, raw_path: Any, corpus_root: Path) -> Path:
    """Resolve corpus paths without assuming a DBMS directory layout."""
    value = Path(str(raw_path or ""))
    if value.is_absolute():
        return value.resolve()
    candidates = [
        (page_manifest_path.parent / value).resolve(),
        (corpus_root.parent / value).resolve(),
        (corpus_root / value).resolve(),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def build_document_ir_from_record(
    record: Mapping[str, Any],
    *,
    page_manifest_path: str | Path,
    corpus_root: str | Path,
    dbms: str,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Parse one selected frozen page through the shared DocumentIR adapter."""
    manifest_path = Path(page_manifest_path).resolve()
    corpus = Path(corpus_root).resolve()
    artifact = _resolve_artifact_path(
        manifest_path,
        record.get("local_artifact_absolute_path") or record.get("local_artifact_path"),
        corpus,
    )
    raw = artifact.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    detected_format = str(record.get("detected_format") or "").upper()
    spec = {
        "url": str(record.get("url") or record.get("final_url") or ""),
        "html_path": str(artifact),
        "crawl_depth": record.get("crawl_depth", 0),
        "feature_context": list(record.get("feature_scopes") or []),
        "page_hash": str(record.get("content_hash") or hashlib.sha256(raw).hexdigest()),
    }
    if detected_format in {"MARKDOWN", "PLAIN_TEXT"}:
        topology = build_text_page_topology(
            spec, content=text, detected_format=detected_format,
        )
    else:
        topology = build_page_topology(spec, dict(profile))
        topology["page_hash"] = spec["page_hash"]
    topology["source_format"] = detected_format or "UNKNOWN"
    topology["document_id"] = str(record.get("document_id") or "")
    document = from_v2_page(
        topology,
        dbms=dbms,
        html_path=str(artifact),
        doc_version=str(record.get("doc_version") or "frozen"),
    )
    document.update({
        "document_id": str(record.get("document_id") or ""),
        "source_format": detected_format or "UNKNOWN",
        "document_role": str(record.get("document_role") or "UNKNOWN"),
        "selected_representation": str(
            record.get("selected_representation") or detected_format or "UNKNOWN"
        ),
        "feature_context": sorted({
            str(value) for value in record.get("feature_scopes") or [] if str(value)
        }),
        "source_scope_root": str(
            record.get("source_scope_root")
            or (record.get("provenance") or {}).get("source_scope_root")
            or ""
        ),
        "discovery_provenance": {
            "source_parent": record.get("source_parent")
            or (record.get("provenance") or {}).get("source_parent"),
            "source_anchor": record.get("source_anchor")
            or (record.get("provenance") or {}).get("source_anchor")
            or "",
            "source_scope_root": record.get("source_scope_root")
            or (record.get("provenance") or {}).get("source_scope_root"),
            "scope_roots": list((record.get("provenance") or {}).get("scope_roots") or []),
            "feature_scopes": list(record.get("feature_scopes") or []),
            "depth": record.get("crawl_depth", 0),
        },
        "frozen_corpus_provenance": {
            "document_id": str(record.get("document_id") or ""),
            "manifest_path": str(manifest_path),
            "artifact_path": str(artifact),
            "content_hash": spec["page_hash"],
            "fetch_status": str(record.get("fetch_status") or ""),
        },
    })
    return document


def _copy_identity_artifacts(result: Mapping[str, Any], output_root: Path) -> None:
    owners = list(result.get("owners") or [])
    candidates = list(result.get("candidates") or [])
    accepted = list(result.get("accepted") or [])
    rejected = list(result.get("rejected") or [])
    units = list(result.get("units") or [])
    _write_jsonl(output_root / "owners" / "owners.jsonl", owners)
    _write_json(output_root / "owners" / "owner_summary.json", {
        "schema_version": "presemantic-owner-summary/v1",
        "documents": len(result.get("documents") or []),
        "owner_candidates": int(
            result.get("raw_owner_count")
            or len(result.get("owner_candidates") or [])
            or len(result.get("owner_hypotheses") or [])
        ),
        "accepted_owners": len(owners),
        "ownerless_pages": sorted({
            str(page.get("page_id") or "")
            for page in result.get("documents") or []
            if not any(
                str(owner.get("page_id") or "") == str(page.get("page_id") or "")
                for owner in owners
            )
        }),
        "owner_kinds": dict(sorted(Counter(
            str(row.get("owner_kind") or "UNKNOWN") for row in owners
        ).items())),
        "owner_status": dict(sorted(Counter(
            str(row.get("status") or "UNKNOWN") for row in owners
        ).items())),
    })
    _write_jsonl(output_root / "identity" / "identity_candidates.jsonl", candidates)
    _write_jsonl(output_root / "identity" / "validated_candidates.jsonl", accepted)
    _write_jsonl(output_root / "identity" / "rejected_candidates.jsonl", rejected)
    _write_jsonl(output_root / "identity" / "identity_groups_pre_type.jsonl", units)


def _scope_values(
    unit: Mapping[str, Any],
    owner_by_id: Mapping[str, Mapping[str, Any]],
    page_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    values: set[str] = set()
    for owner_id in unit.get("owner_ids") or []:
        owner = owner_by_id.get(str(owner_id)) or {}
        page = page_by_id.get(str(owner.get("page_id") or "")) or {}
        values.update(
            str(value) for value in page.get("feature_context") or [] if str(value)
        )
    return sorted(values)


def _risk_row(
    unit: Mapping[str, Any],
    decision: Mapping[str, Any],
    owner_by_id: Mapping[str, Mapping[str, Any]],
    page_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    group_status = str(unit.get("group_status") or "UNCERTAIN_FEATURE")
    type_status = str(
        decision.get("type_status") or decision.get("status") or "REVIEW_REQUIRED"
    )
    confidence = float(decision.get("confidence") or 0.0)
    reasons = list(decision.get("reason_codes") or decision.get("conflict_flags") or [])
    if group_status == "NON_FEATURE_ONLY":
        risk_status = "NON_FEATURE_ONLY"
    elif group_status == "UNCERTAIN_FEATURE":
        risk_status = "AMBIGUOUS_CONFIRMED" if reasons else "UNCERTAIN"
    elif type_status == "REVIEW_REQUIRED" or len(decision.get("alternate_types") or []) > 0:
        risk_status = "AMBIGUOUS_CONFIRMED"
    elif group_status == "PROBABLE_FEATURE" or confidence < 0.9:
        risk_status = "RISKY_CONFIRMED"
    else:
        risk_status = "STRONG_CONFIRMED"
    scopes = _scope_values(unit, owner_by_id, page_by_id)
    page_urls = set()
    for owner_id in unit.get("owner_ids") or []:
        owner = owner_by_id.get(str(owner_id)) or {}
        page = page_by_id.get(str(owner.get("page_id") or "")) or {}
        if page.get("url"):
            page_urls.add(str(page["url"]))
    return {
        "identity_unit_id": str(unit.get("identity_unit_id") or ""),
        "identity": str(unit.get("canonical_identity") or ""),
        "feature_type": str(
            unit.get("feature_type") or decision.get("primary_type") or "UNCERTAIN"
        ).upper(),
        "group_status": group_status,
        "type_status": type_status,
        "type_confidence": confidence,
        "risk_status": risk_status,
        "reason_codes": reasons,
        "owner_ids": list(unit.get("owner_ids") or []),
        "candidate_ids": list(unit.get("candidate_ids") or []),
        "source_scopes": scopes,
        "source_urls": sorted(page_urls),
        "owner_count": len(unit.get("owner_ids") or []),
        "variant_count": len(unit.get("variants") or []),
        "type_evidence_count": int(decision.get("typed_feature_evidence_count") or 0),
        "provenance": dict(unit.get("provenance") or {}),
    }


def run_frozen_presemantic(
    *,
    dbms: str,
    documents: list[dict[str, Any]],
    output_root: str | Path,
    run_id: str,
    type_adaptation: bool = False,
    operator_closure: bool = False,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run DocumentIR consumers through Type, and stop before Semantic."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    supplied_profile = profile is not None
    if supplied_profile:
        from agents.profile_bootstrap.cold_start import validate_cold_start_profile
        profile = validate_cold_start_profile(profile)
        if str(profile['dbms']).casefold() != str(dbms).casefold():
            raise ValueError('External Profile DBMS mismatch')
    else:
        profile = build_cold_start_profile(dbms=dbms, documents=documents, run_id=run_id)
    from agents.owner.source_relations import prepare_relation_documents
    documents=prepare_relation_documents(documents,profile)
    _write_json(root / "profile" / "generated_profile.json", profile)
    _write_json(root / "profile" / "profile_metadata.json", {
        "generation_mode": profile['generation_mode'],
        "manual_edit": profile['manual_edit'],
        "load_mode": 'PROVIDED_PROFILE' if supplied_profile else 'GENERATED_THIS_RUN',
        "profile_id": profile.get("profile_id"),
        "profile_digest": profile_digest(profile),
        "source_pages": profile.get("source_pages", []),
        "legacy_profile_dsl_used": False,
    })
    _write_jsonl(root / "document_ir" / "documents.jsonl", documents)

    identity = execute_production_identity_path(
        documents, output_dir=root / "identity", profile=profile,
    )
    identity_adaptation_audit: list[dict[str, Any]] = []
    operator_closure_audit: list[dict[str, Any]] = []
    if type_adaptation:
        # This bounded reconciliation is still generic and owner-local.  It
        # only materializes a surface when the frozen DocumentIR contains an
        # operator/type declaration shape; it does not consult an inventory or
        # a DBMS vocabulary.
        from agents.type.adaptation import augment_identity_units_for_type_adaptation

        identity, identity_adaptation_audit = augment_identity_units_for_type_adaptation(
            identity, documents,
        )
        # The production identity stage may already have emitted
        # identity_units.jsonl before this optional pass. Keep this new output
        # root self-contained by replacing that snapshot with adapted units.
        _write_jsonl(root / "identity" / "identity_units.jsonl", identity.get("units") or [])
        _write_jsonl(
            root / "audit" / "identity_adaptation.jsonl",
            identity_adaptation_audit,
        )
        if operator_closure:
            from agents.type.operator_closure import augment_identity_units_for_operator_closure

            identity, operator_closure_audit = augment_identity_units_for_operator_closure(
                identity, documents,
            )
            _write_jsonl(root / "audit" / "operator_closure.jsonl", operator_closure_audit)
    _copy_identity_artifacts({
        **identity,
        "owner_candidates": identity.get("owner_candidates")
        or identity.get("owner_hypotheses")
        or [],
    }, root)
    quality = write_identity_quality_artifacts(
        output_root=root, result=identity, profile=profile, baseline_root=None,
    )

    pretype_units = list(identity.get("units") or [])
    typed_units, decisions, _ = _build_typed_layer(
        pretype_units,
        list(identity.get("owners") or []),
        documents,
        root / "type",
        type_adaptation=type_adaptation,
    )
    owner_by_id = {
        str(row.get("owner_id") or ""): row
        for row in identity.get("owners") or []
    }
    page_by_id = {
        str(row.get("page_id") or ""): row for row in documents
    }
    typed_by_id = {
        str(row.get("identity_unit_id") or ""): row for row in typed_units
    }
    decision_by_id = {
        str(row.get("identity_unit_id") or ""): row for row in decisions
    }
    type_rows: list[dict[str, Any]] = []
    for unit_id in sorted(typed_by_id):
        unit = typed_by_id[unit_id]
        decision = decision_by_id.get(unit_id) or {}
        type_rows.append({
            **dict(decision),
            "identity": str(unit.get("canonical_identity") or ""),
            "variants": list(unit.get("variants") or []),
            "feature_type": str(unit.get("feature_type") or "UNCERTAIN").upper(),
            "group_status": str(unit.get("group_status") or "UNCERTAIN_FEATURE"),
            "adapted_identity_kind": str(unit.get("adapted_identity_kind") or ""),
            "adaptation_provenance": list(unit.get("adaptation_provenance") or []),
            "source_scopes": _scope_values(unit, owner_by_id, page_by_id),
            "owner_ids": list(unit.get("owner_ids") or []),
            "candidate_ids": list(unit.get("candidate_ids") or []),
        })
    _write_jsonl(root / "type" / "type_results.jsonl", type_rows)
    _write_json(root / "type" / "type_summary.json", {
        "schema_version": "presemantic-type-summary/v1",
        "identity_units": len(typed_units),
        "by_feature_type": dict(sorted(Counter(
            row["feature_type"] for row in type_rows
        ).items())),
        "by_type_status": dict(sorted(Counter(
            str(row.get("type_status") or row.get("status") or "UNKNOWN")
            for row in type_rows
        ).items())),
        "scope_hints_used_as_type_decision": False,
        "official_inventory_used_for_type_decision": False,
    })
    _write_jsonl(root / "identity" / "identity_groups.jsonl", typed_units)
    _write_json(root / "identity" / "identity_summary.json", {
        **dict(identity.get("stats") or {}),
        "typed_identity_units": len(typed_units),
        "groups_by_status_after_type": dict(sorted(Counter(
            str(row.get("group_status") or "UNKNOWN") for row in typed_units
        ).items())),
        "quality_summary": quality,
        "scope_hints_used_as_type_decision": False,
    })

    risk_rows = [
        _risk_row(
            typed_by_id[unit_id],
            decision_by_id.get(unit_id) or {},
            owner_by_id,
            page_by_id,
        )
        for unit_id in sorted(typed_by_id)
    ]
    _write_jsonl(root / "audit" / "risk_stratification.jsonl", risk_rows)
    mixed_rows = []
    for page in documents:
        page_units = [
            row for row in risk_rows
            if any(
                str((owner_by_id.get(str(owner_id)) or {}).get("page_id") or "")
                == str(page.get("page_id") or "")
                for owner_id in row.get("owner_ids") or []
            )
        ]
        types = sorted({
            str(row.get("feature_type") or "UNCERTAIN") for row in page_units
        })
        scopes = sorted({str(value) for value in page.get("feature_context") or []})
        if len(types) > 1 or len(scopes) > 1:
            mixed_rows.append({
                "page_id": page.get("page_id"),
                "url": page.get("url"),
                "source_scopes": scopes,
                "identity_types": types,
                "identities": sorted({
                    str(row.get("identity") or "") for row in page_units
                }),
            })
    _write_jsonl(root / "audit" / "mixed_page_diagnostics.jsonl", mixed_rows)

    routing_counts: Counter[str] = Counter()
    routing_rows: list[dict[str, Any]] = []
    for row in risk_rows:
        risk = str(row.get("risk_status") or "")
        feature_type = str(row.get("feature_type") or "UNCERTAIN")
        if risk == "NON_FEATURE_ONLY":
            route = "would_stop"
        elif risk == "STRONG_CONFIRMED" and feature_type == "FUNCTION":
            route = "would_direct_semantic"
        else:
            route = "would_require_target_classification"
        routing_counts[route] += 1
        routing_rows.append({
            "identity_unit_id": row.get("identity_unit_id"),
            "identity": row.get("identity"),
            "feature_type": feature_type,
            "risk_status": risk,
            "route": route,
            "provider_call": False,
        })
    routing = {
        "schema_version": "presemantic-routing-dry-run/v1",
        "counts": dict(sorted(routing_counts.items())),
        "rows": routing_rows,
        "provider_calls": 0,
        "target_llm_classifier_called": False,
        "semantic_llm_called": False,
    }
    _write_json(root / "audit" / "routing_dry_run.json", routing)

    return {
        "profile": profile,
        "identity": identity,
        "typed_units": typed_units,
        "type_decisions": decisions,
        "risk_rows": risk_rows,
        "routing": routing,
        "quality": quality,
        "mixed_rows": mixed_rows,
        "type_adaptation": type_adaptation,
        "operator_closure": operator_closure,
        "identity_adaptation_audit": identity_adaptation_audit,
        "operator_closure_audit": operator_closure_audit,
    }


__all__ = ["build_document_ir_from_record", "run_frozen_presemantic"]
