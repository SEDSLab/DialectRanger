"""Human-review artifacts for the generic Identity Quality Gate."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from agents.identity.quality import OCCURRENCE_ROLES, SURFACE_ROLES, aggregate_occurrence_roles


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _baseline_summary(baseline_root: str | Path | None) -> dict[str, Any]:
    if not baseline_root:
        return {}
    root = Path(baseline_root)
    summary = _read_json(root / "evaluation" / "identity_summary.json")
    groups = _read_jsonl(root / "identity" / "groups.jsonl")
    counts = Counter(str(row.get("canonical_identity") or "") for row in groups)
    return {
        "root": str(root.resolve()),
        "identity_groups": int(summary.get("identity_groups_total", len(groups))),
        "groups_by_type": dict(summary.get("groups_by_type") or {}),
        "cross_type_duplicates": sum(1 for value in counts.values() if value > 1),
    }


def _surface_summary(profile: Mapping[str, Any]) -> dict[str, Any]:
    feature_roles = {"FEATURE_DEFINITION", "FEATURE_CATALOG", "FEATURE_REFERENCE"}
    counts = Counter()
    for surface in profile.get("surfaces", []):
        role = str(surface.get("surface_role") or "UNKNOWN")
        if role in feature_roles:
            counts["feature_bearing_surfaces"] += 1
        elif role == "SUPPORTING_CONTEXT":
            counts["supporting_surfaces"] += 1
        else:
            counts["rejected_or_unknown_surfaces"] += 1
    return {
        "feature_bearing_surfaces": int(counts.get("feature_bearing_surfaces", 0)),
        "supporting_surfaces": int(counts.get("supporting_surfaces", 0)),
        "rejected_or_unknown_surfaces": int(counts.get("rejected_or_unknown_surfaces", 0)),
        "total_profile_surfaces": len(profile.get("surfaces", [])),
        "by_role": dict(sorted(Counter(str(row.get("surface_role") or "UNKNOWN") for row in profile.get("surfaces", [])).items())),
    }


def _owner_text(owner: Mapping[str, Any], documents_by_page: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    page = documents_by_page.get(str(owner.get("page_id") or ""), {})
    blocks = {str(block.get("block_id")): block for block in page.get("blocks", [])}
    values = []
    for block_id in owner.get("member_block_ids", []):
        block = blocks.get(str(block_id))
        if block and str(block.get("text") or "").strip():
            values.append({"block_id": str(block_id), "text": str(block.get("text") or "")})
    return values


def _sample_row(
    unit: Mapping[str, Any],
    occurrences_by_id: Mapping[str, Mapping[str, Any]],
    owners_by_id: Mapping[str, Mapping[str, Any]],
    documents_by_page: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    occurrence_rows = []
    owner_rows: dict[str, dict[str, Any]] = {}
    for occurrence_id in unit.get("occurrence_ids") or []:
        occurrence = occurrences_by_id.get(str(occurrence_id))
        if not occurrence:
            continue
        owner_id = str(occurrence.get("owner_id") or "")
        owner = owners_by_id.get(owner_id, {})
        owner_rows.setdefault(owner_id, {
            "owner_id": owner_id,
            "owner_kind": owner.get("owner_kind"),
            "source_url": occurrence.get("source_url"),
            "blocks": _owner_text(owner, documents_by_page),
        })
        occurrence_rows.append({
            "occurrence_id": str(occurrence.get("occurrence_id") or ""),
            "raw_identity": occurrence.get("raw_identity"),
            "normalized_identity": occurrence.get("normalized_identity"),
            "occurrence_role": occurrence.get("occurrence_role"),
            "defining": occurrence.get("defining"),
            "source_type": occurrence.get("source_type"),
            "source_url": occurrence.get("source_url"),
            "source_block_id": occurrence.get("source_block_id"),
            "raw_evidence": occurrence.get("raw_evidence"),
            "surface_role": occurrence.get("surface_role"),
            "surface_confidence": occurrence.get("surface_confidence"),
            "type_evidence": occurrence.get("type_evidence") or [],
        })
    return {
        "identity_unit_id": unit.get("identity_unit_id"),
        "normalized_identity": unit.get("canonical_identity"),
        "final_type": unit.get("feature_type") or unit.get("type_hint"),
        "group_key": unit.get("group_key"),
        "group_status": unit.get("group_status"),
        "semantic_ready": unit.get("semantic_ready"),
        "variants": unit.get("variants") or [],
        "defining_variants": unit.get("defining_variants") or [],
        "occurrence_roles": unit.get("occurrence_roles") or {},
        "type_evidence": unit.get("type_evidence") or {},
        "strong_type_evidence": unit.get("strong_type_evidence") or [],
        "source_urls": unit.get("source_urls") or [],
        "owners": list(owner_rows.values()),
        "occurrences": occurrence_rows,
        "reconciliation_reason": unit.get("reconciliation_reason"),
    }


def _select_review_rows(
    units: list[Mapping[str, Any]],
    occurrences: list[Mapping[str, Any]],
    owners: list[Mapping[str, Any]],
    documents: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    occurrences_by_id = {str(row.get("occurrence_id")): row for row in occurrences}
    owners_by_id = {str(row.get("owner_id")): row for row in owners}
    documents_by_page = {str(row.get("page_id")): row for row in documents}
    ordered = sorted(units, key=lambda row: (str(row.get("canonical_identity") or ""), str(row.get("identity_unit_id") or "")))
    limits = {
        "CONFIRMED_FEATURE": 50,
        "PROBABLE_FEATURE": 30,
        "UNCERTAIN_FEATURE": 30,
        "NON_FEATURE_ONLY": 30,
    }
    selected: list[dict[str, Any]] = []
    for status, limit in limits.items():
        selected.extend(
            _sample_row(unit, occurrences_by_id, owners_by_id, documents_by_page)
            for unit in ordered if str(unit.get("group_status") or "") == status
        )
        # Preserve deterministic review order while applying the per-status cap.
        start = sum(1 for row in selected if row.get("group_status") != status)
        status_rows = [row for row in selected if row.get("group_status") == status]
        selected = [row for row in selected if row.get("group_status") != status] + status_rows[:limit]
    selected_ids = {str(row.get("identity_unit_id")) for row in selected}
    # Every conflict gets a review slot, even when it is outside the strata.
    for unit in ordered:
        if unit.get("conflict") and str(unit.get("identity_unit_id")) not in selected_ids:
            selected.append(_sample_row(unit, occurrences_by_id, owners_by_id, documents_by_page))
    return selected


def _markdown_review(rows: list[Mapping[str, Any]]) -> str:
    lines = [
        "# Identity Quality Review Pack",
        "",
        "This pack is generated from the cold-start Identity Quality Gate. It stops before Semantic Extraction, Canonical KB, and Frontend.",
        "",
        f"- review groups: `{len(rows)}`",
        "",
    ]
    for index, row in enumerate(rows, 1):
        lines.extend([
            f"## {index}. {row.get('normalized_identity')}",
            "",
            f"- Final type: `{row.get('final_type')}`",
            f"- Group status: `{row.get('group_status')}`",
            f"- Semantic ready: `{row.get('semantic_ready')}`",
            f"- Group key: `{row.get('group_key')}`",
            f"- Variants: `{json.dumps(row.get('variants') or [], ensure_ascii=False)}`",
            f"- Defining variants: `{json.dumps(row.get('defining_variants') or [], ensure_ascii=False)}`",
            f"- Type evidence: `{json.dumps(row.get('type_evidence') or {}, ensure_ascii=False)}`",
            "",
            "### Owners",
            "",
        ])
        for owner in row.get("owners") or []:
            lines.append(f"- `{owner.get('owner_id')}` `{owner.get('owner_kind')}` `{owner.get('source_url')}`")
            for block in (owner.get("blocks") or [])[:8]:
                lines.append(f"  - `{block.get('block_id')}` {block.get('text')}")
        lines.extend(["", "### Occurrences", ""])
        for occurrence in (row.get("occurrences") or [])[:20]:
            lines.extend([
                f"- `{occurrence.get('occurrence_role')}` `{occurrence.get('raw_identity')}` from `{occurrence.get('source_url')}`",
                f"  - evidence: {occurrence.get('raw_evidence')}",
                f"  - surface: `{occurrence.get('surface_role')}`; type evidence: `{json.dumps(occurrence.get('type_evidence') or [], ensure_ascii=False)}`",
            ])
        if len(row.get("occurrences") or []) > 20:
            lines.append(f"- ... {len(row['occurrences']) - 20} more occurrences are available in the JSONL pack.")
        lines.extend(["", f"- Reconciliation: `{row.get('reconciliation_reason')}`", ""])
    return "\n".join(lines)


def write_identity_quality_artifacts(
    *,
    output_root: str | Path,
    result: Mapping[str, Any],
    profile: Mapping[str, Any],
    baseline_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write the quality summary, conflict ledger, and human review pack."""
    root = Path(output_root)
    evaluation_root = root / "evaluation"
    identity_root = root / "identity"
    occurrences = list(result.get("occurrences") or [])
    units = list(result.get("units") or [])
    owners = list(result.get("owners") or [])
    documents = list(result.get("documents") or [])
    quality_audit = dict(result.get("quality_audit") or {})
    roles = aggregate_occurrence_roles(occurrences)
    status_counts = dict(sorted(Counter(str(row.get("group_status") or "UNKNOWN") for row in units).items()))
    surface = _surface_summary(profile)
    baseline = _baseline_summary(baseline_root)
    summary = {
        "schema_version": "identity-quality-summary/v1",
        "algorithm": "occurrence-role-type-evidence-group-reconciliation/v1",
        "baseline": baseline,
        "identity_groups_before": baseline.get("identity_groups"),
        "identity_groups_after": len(units),
        "groups_by_type": dict(sorted(Counter(str(row.get("feature_type") or "UNCERTAIN") for row in units).items())),
        "groups_by_status": status_counts,
        "occurrence_roles": roles,
        "groups_with_defining_evidence": int(quality_audit.get("groups_with_defining_evidence", 0)),
        "groups_without_defining_evidence": int(quality_audit.get("groups_without_defining_evidence", 0)),
        "cross_type_duplicates_before": int(quality_audit.get("cross_type_duplicates_before", 0)),
        "cross_type_duplicates_after": int(quality_audit.get("cross_type_duplicates_after", 0)),
        "baseline_cross_type_duplicates": int(baseline.get("cross_type_duplicates", 0)) if baseline else None,
        "same_normalized_identity_multi_group_count": int(quality_audit.get("same_normalized_identity_multi_group_count", 0)),
        "surface_quality": surface,
        "candidate_counts": {
            "total": len(result.get("candidates") or []),
            "validated": sum(row.get("validation_status") == "VALIDATED" for row in result.get("accepted", [])),
            "retained_evidence": sum(row.get("validation_status") == "RETAINED_EVIDENCE" for row in result.get("accepted", [])),
            "rejected": len(result.get("rejected") or []),
        },
        "integrity": {
            "legacy_profile_used": False,
            "legacy_formal_used": False,
            "legacy_adapter_used": False,
            "db_specific_repair_used": False,
            "feature_inventory_used_for_extraction": False,
            "llm_used": False,
        },
        "review_sample_limits": {
            "CONFIRMED_FEATURE": 50,
            "PROBABLE_FEATURE": 30,
            "UNCERTAIN_FEATURE": 30,
            "NON_FEATURE_ONLY": 30,
        },
    }
    conflicts = list(quality_audit.get("cross_type_conflicts") or [])
    _write_jsonl(identity_root / "cross_type_conflicts.jsonl", conflicts)
    review_rows = _select_review_rows(units, occurrences, owners, documents)
    _write_jsonl(evaluation_root / "identity_quality_review.jsonl", review_rows)
    (evaluation_root / "identity_quality_review.md").write_text(_markdown_review(review_rows), encoding="utf-8")
    _write_json(evaluation_root / "identity_quality_summary.json", summary)

    merge_examples = [
        unit for unit in units
        if len(unit.get("variants") or []) > 1
        and str(unit.get("group_status") or "") in {"CONFIRMED_FEATURE", "PROBABLE_FEATURE"}
    ][:3]
    noise_examples = [
        unit for unit in units
        if str(unit.get("group_status") or "") == "NON_FEATURE_ONLY"
    ][:3]
    report_lines = [
        "# Identity Quality Gate Report",
        "",
        "## Baseline vs New",
        "",
        f"- baseline groups: `{baseline.get('identity_groups', 'not supplied')}`",
        f"- new groups: `{len(units)}`",
        f"- baseline groups by type: `{json.dumps(baseline.get('groups_by_type', {}), ensure_ascii=False)}`",
        f"- new groups by type: `{json.dumps(summary['groups_by_type'], ensure_ascii=False)}`",
        f"- baseline cross-type duplicates: `{summary.get('baseline_cross_type_duplicates')}`",
        f"- new provisional cross-type duplicates: `{summary['cross_type_duplicates_before']}`",
        f"- new reconciled cross-type duplicates: `{summary['cross_type_duplicates_after']}`",
        f"- groups with defining evidence: `{summary['groups_with_defining_evidence']}`",
        f"- groups without defining evidence: `{summary['groups_without_defining_evidence']}`",
        "",
        "## Surface Quality",
        "",
        f"- feature-bearing surfaces: `{surface['feature_bearing_surfaces']}`",
        f"- supporting surfaces: `{surface['supporting_surfaces']}`",
        f"- rejected/unknown surfaces: `{surface['rejected_or_unknown_surfaces']}`",
        f"- by role: `{json.dumps(surface['by_role'], ensure_ascii=False)}`",
        "",
        "The classification uses table header/identity-slot consistency, bounded signature shape, definition-list structure, and surrounding context. It does not use page-name or database-specific blacklists.",
        "",
        "## Occurrence Roles",
        "",
    ]
    report_lines.extend(f"- {role}: `{roles.get(role, 0)}`" for role in OCCURRENCE_ROLES)
    report_lines.extend([
        "",
        "## Group Reconciliation",
        "",
        f"- group status: `{json.dumps(status_counts, ensure_ascii=False)}`",
        f"- cross-type conflicts: `{len(conflicts)}`",
        "- same normalized identity is bucketed first; type is resolved from strong defining evidence. Weak/uncertain occurrences attach to a strong group instead of creating a duplicate.",
        "- multiple strong defining types remain split and are recorded in `identity/cross_type_conflicts.jsonl`.",
        "- groups with only mentions/examples/parameters are marked `NON_FEATURE_ONLY` and have `semantic_ready=false`.",
        "",
        "### Reconciliation examples",
        "",
    ])
    if merge_examples:
        report_lines.append("- ordinary merge:")
        report_lines.extend(
            f"  - `{unit.get('canonical_identity')}` `{unit.get('group_key')}` <- variants `{json.dumps(unit.get('variants') or [], ensure_ascii=False)}`"
            for unit in merge_examples
        )
    else:
        report_lines.append("- ordinary merge: no multi-variant confirmed group in this page budget.")
    if conflicts:
        report_lines.append("- true strong-type split:")
        report_lines.extend(
            f"  - `{conflict.get('normalized_identity')}` -> `{json.dumps(conflict.get('strong_type_evidence') or [], ensure_ascii=False)}`"
            for conflict in conflicts[:3]
        )
    else:
        report_lines.append("- true strong-type split: none observed in this fixed MySQL corpus; regression tests cover DATE datatype vs DATE(expr) function.")
    if noise_examples:
        report_lines.append("- noise retained but not semantic-ready:")
        report_lines.extend(
            f"  - `{unit.get('canonical_identity')}` status=`NON_FEATURE_ONLY`, roles=`{json.dumps(unit.get('occurrence_roles') or {}, ensure_ascii=False)}`"
            for unit in noise_examples
        )
    else:
        report_lines.append("- noise retained but not semantic-ready: none observed.")
    report_lines.extend([
        "",
        "## Review Pack",
        "",
        f"- sampled review groups: `{len(review_rows)}`",
        f"- JSONL: `evaluation/identity_quality_review.jsonl`",
        f"- Markdown: `evaluation/identity_quality_review.md`",
        "",
        "## Integrity",
        "",
        "```yaml",
        "legacy_profile_used: false",
        "legacy_formal_used: false",
        "legacy_adapter_used: false",
        "db_specific_repair_used: false",
        "feature_inventory_used_for_extraction: false",
        "llm_used: false",
        "semantic_not_run: true",
        "canonical_not_run: true",
        "frontend_not_run: true",
        "```",
        "",
    ])
    (evaluation_root / "identity_quality_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    return summary
