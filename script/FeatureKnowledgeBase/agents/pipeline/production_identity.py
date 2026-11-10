"""Authoritative generic Owner -> Candidate -> IdentityUnit execution.

The function is intentionally offline and side-effect limited to the supplied
artifact directory.  It is shared by the public orchestrator and the P2 audit
runner; evaluation modules do not own a second implementation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from agents.coverage.controller import build_coverage
from agents.identity.production import run_identity_owner_path
from agents.pipeline.lineage import make_exclusion, validate_parent_lineage


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _exclusions(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = []
    for row in result.get("rejected", []):
        output.append(make_exclusion(
            object_id=str(row["candidate_id"]), stage="IdentityCandidate",
            reason_code=str(row.get("validation_reason") or "IDENTITY_VALIDATION_REJECTED"),
            source_url=str(row.get("provenance", {}).get("source_url") or ""),
            parent_ids=[str(row.get("owner_id") or "")],
            detail="Candidate retained in rejected_candidates.jsonl; no silent deletion.",
        ))
    return output


def execute_production_identity_path(
    documents: Iterable[Mapping[str, Any]],
    output_dir: str | Path | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = run_identity_owner_path(list(documents), profile=profile)
    exclusions = _exclusions(result)
    coverage = build_coverage(
        result["documents"], result["owners"], result["candidates"],
        result["accepted"], result["units"], exclusions,
    )
    lineage = validate_parent_lineage({
        "document_ir": result["documents"],
        "owner_hypothesis": result["owners"],
        "identity_candidate": result["candidates"],
        "identity_unit": result["units"],
    })
    result = {**result, "exclusions": exclusions, "coverage": coverage, "lineage": lineage}
    if output_dir is not None:
        root = Path(output_dir)
        _write_jsonl(root / "owner_hypotheses.jsonl", result["owners"])
        _write_jsonl(root / "identity_occurrences.jsonl", result.get("occurrences", []))
        _write_jsonl(root / "candidate_ledger.jsonl", result["candidates"])
        _write_jsonl(root / "validated_candidates.jsonl", result["accepted"])
        _write_jsonl(root / "rejected_candidates.jsonl", result["rejected"])
        _write_jsonl(root / "identity_units.jsonl", result["units"])
        _write_jsonl(root / "exclusion_ledger.jsonl", exclusions)
        coverage_root = root.parent / "coverage"
        _write_jsonl(coverage_root / "page_coverage.jsonl", coverage["page_coverage"])
        _write_jsonl(coverage_root / "unknown_structures.jsonl", coverage["unknown_structures"])
        _write_jsonl(coverage_root / "unsupported_surfaces.jsonl", coverage["unsupported_surfaces"])
        _write_json(coverage_root / "coverage_summary.json", coverage["summary"])
        _write_json(root / "lineage_audit.json", lineage)
        _write_json(root / "summary.json", {
            "schema_version": "production-identity-owner-run/v1",
            "algorithm": "document-ir-owner-candidate-identity/v1",
            "authoritative_candidate_source": True,
            "network_fetch": False, "llm_calls": False,
            "profile_guided": profile is not None,
            "stats": result["stats"], "coverage": coverage["summary"],
            "lineage": {"status": lineage["status"], "broken_lineage": lineage["broken_lineage"]},
        })
    return result


def production_identity_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authoritative_candidate_source": True,
        "generic_owner_induction": True,
        "identity_validation": True,
        "identity_unit_construction": True,
        "identity_occurrences": True,
        "profile_guided_owner_extraction": True,
        "coverage_controller": True,
        "stats": dict(result.get("stats", {})),
        "lineage_status": result.get("lineage", {}).get("status"),
    }
