"""Independent deterministic Stage 1 runner and artifact writer."""
from __future__ import annotations

import json
from pathlib import Path

from agents.document_structure_v2.archetypes import classify_page, representative_pages_by_archetype
from agents.document_structure_v2.canonical import canonicalize, link_mentions
from agents.document_structure_v2.models import validate_evidence
from agents.document_structure_v2.profile import validate_profile_v2
from agents.document_structure_v2.recipes import execute_page_recipe
from agents.document_structure_v2.topology import build_page_topology, document_relations, resolve_links


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def run_stage1(profile: dict, page_specs: list[dict], output_dir: str | Path | None = None) -> dict:
    validate_profile_v2(profile)
    pages = resolve_links([build_page_topology(spec, profile) for spec in page_specs])
    classifications = [classify_page(page) for page in pages]
    classifications_by_page = {row["page_id"]: row for row in classifications}
    for page in pages:
        page["page_archetype"] = classifications_by_page[page["page_id"]]["page_archetype"]
    mentions, owners = [], []
    for page in pages:
        page_mentions, page_owners = execute_page_recipe(page)
        mentions.extend(page_mentions)
        owners.extend(page_owners)
    relations = link_mentions(mentions, pages)
    features = canonicalize(profile["dbms"], mentions, owners, relations)
    pages_by_id = {page["page_id"]: page for page in pages}
    evidence_errors = []
    for feature in features:
        for item in feature["evidence"]:
            for error in validate_evidence(item, pages_by_id):
                evidence_errors.append({"canonical_id": feature["canonical_id"], "error": error, "evidence": item})
    identities = [json.dumps(row["identity"], sort_keys=True) for row in features]
    duplicate_identities = sorted({value for value in identities if identities.count(value) > 1})
    unfetched_mention_links = sorted({
        link["link_id"] for page in pages for link in page["links"]
        if link["source_block_id"] in {mention["evidence"][0]["block_id"] for mention in mentions}
        and link["resolution_status"] in {"unresolved_fragment", "unfetched"}
    })
    result = {
        "schema_version": "document-structure-stage1/v2", "profile_id": profile["profile_id"],
        "pages": pages, "document_relations": document_relations(pages),
        "classifications": classifications,
        "representative_page_ids": representative_pages_by_archetype(classifications),
        "mentions": mentions, "owner_groups": owners, "feature_relations": relations,
        "canonical_features": features, "evidence_errors": evidence_errors,
        "diagnostics": {
            "duplicate_identities": duplicate_identities,
            "unfetched_mention_links": unfetched_mention_links,
        },
    }
    if output_dir is not None:
        root = Path(output_dir)
        _write_jsonl(root / "document_pages.jsonl", pages)
        _write_jsonl(root / "document_relations.jsonl", result["document_relations"])
        _write_jsonl(root / "page_archetypes.jsonl", classifications)
        _write_jsonl(root / "feature_mentions.jsonl", mentions)
        _write_jsonl(root / "feature_owner_groups.jsonl", owners)
        _write_jsonl(root / "feature_relations.jsonl", relations)
        _write_jsonl(root / "canonical_features.jsonl", features)
        (root / "stage1_summary.json").write_text(json.dumps({
            "schema_version": result["schema_version"], "profile_id": result["profile_id"],
            "counts": {key: len(result[key]) for key in ("pages", "mentions", "owner_groups", "feature_relations", "canonical_features", "evidence_errors")},
            "diagnostics": result["diagnostics"],
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def evaluate_gold_paths(result: dict, expectations: list[dict]) -> dict:
    """Evaluate named offline traces; expectations are run data, never Profile knowledge."""
    failures, traces = [], []
    by_link = {link["link_id"]: link for page in result["pages"] for link in page["links"]}
    for expected in expectations:
        matches = [row for row in result["canonical_features"]
                   if row["identity"]["feature_type"] == expected["feature_type"]
                   and row["identity"]["name"] == expected["name"].casefold()]
        if len(matches) != 1:
            failures.append(f"{expected['feature_type']}:{expected['name']}: expected one canonical entity, got {len(matches)}")
            continue
        feature = matches[0]
        actual = feature["identity"]["discriminator"]
        if expected.get("discriminator") != actual:
            failures.append(f"{expected['feature_type']}:{expected['name']}: discriminator {actual!r}")
        fields = {claim["field"] for claim in feature["claims"]}
        missing = set(expected.get("required_fields", [])) - fields
        if missing:
            failures.append(f"{expected['feature_type']}:{expected['name']}: missing fields {sorted(missing)}")
        trace_links = {item["link_id"] for item in feature["trace"]["mention_to_href_relation"]}
        unresolved = sorted(link_id for link_id in trace_links
                            if by_link.get(link_id, {}).get("resolution_status") not in {"resolved_page", "resolved_anchor"})
        if unresolved:
            failures.append(f"{expected['feature_type']}:{expected['name']}: unresolved relations {unresolved}")
        traces.append({"canonical_id": feature["canonical_id"], "identity": feature["identity"],
                       "trace": feature["trace"], "resolved_relation_count": len(trace_links) - len(unresolved)})
    if result["evidence_errors"]:
        failures.append(f"evidence errors: {len(result['evidence_errors'])}")
    if result["diagnostics"]["duplicate_identities"]:
        failures.append(f"duplicate identities: {len(result['diagnostics']['duplicate_identities'])}")
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "traces": traces}
