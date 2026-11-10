"""User-facing projection from the production Canonical Feature KB.

This module deliberately understands only the final
``canonical-feature-kb-projection/v1`` contract.  It does not read discovery,
profile DSL, holdout, candidate, draft, or canonicalization intermediates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlparse
from agents.contracts.identity_key import canonical_name_key


FEATURE_TYPES = ("function", "operator", "datatype", "uncertain")
DISPLAY_FIELDS = ("syntax", "description", "examples", "aliases", "constraints")


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text and text not in result:
            result.append(text)
    return result


def _claims(record: dict, field: str) -> list[dict]:
    claims = record.get("semantics", {}).get(field, [])
    return [claim for claim in claims if isinstance(claim, dict)]


def _claim_values(record: dict, field: str) -> list[str]:
    result = []
    for claim in _claims(record, field):
        text = str(claim.get('value') or '').strip()
        if text and text not in result:
            result.append(text)
    return result


def _source_label(dbms: str, doc_version: str, url: str) -> str:
    names = {"mysql": "MySQL", "postgresql": "PostgreSQL", "trino": "Trino"}
    name = names.get(dbms, dbms)
    version = "" if doc_version in ("", "unknown", None) else str(doc_version)
    if not version and dbms == "mysql":
        match = re.search(r"/refman/([^/]+)/", url)
        version = match.group(1) if match else ""
    return f"{name} {version} official documentation" if version else f"{name} official documentation"


def _page_label(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


def _evidence_projection(source: dict, *, dbms: str, doc_version: str) -> dict | None:
    if not isinstance(source, dict):
        return None
    span = source.get("evidence_span") or {}
    url = str(source.get("source_anchor") or source.get("url") or "").strip()
    canonical_url = str(source.get("url") or url).strip()
    snippet = str(span.get("text") or "").strip()
    if not any((url, canonical_url, snippet, source.get("block_id"))):
        return None
    return {
        "source": _source_label(dbms, doc_version, canonical_url or url),
        "url": url or canonical_url,
        "page": _page_label(canonical_url or url),
        "section": str(source.get("section_heading") or "").strip(),
        "snippet": snippet,
        "role": str(source.get("evidence_role") or "").strip(),
        "technical": {
            "page_id": source.get("page_id"),
            "section_id": source.get("section_id"),
            "block_id": source.get("block_id"),
            "block_type": source.get("block_type"),
            "span_start": span.get("start"),
            "span_end": span.get("end"),
            "page_hash": source.get("page_hash"),
        },
    }


def _all_evidence(record: dict) -> list[dict]:
    structural = record.get("structural", {})
    sources: list[dict] = []
    primary = structural.get("primary_evidence")
    if isinstance(primary, dict):
        sources.append(primary)
    sources.extend(
        item for item in structural.get("supporting_evidence", []) if isinstance(item, dict)
    )
    for field in record.get("semantics", {}).values():
        if not isinstance(field, list):
            continue
        for claim in field:
            if not isinstance(claim, dict):
                continue
            if isinstance(claim.get("evidence"), dict):
                sources.append(claim["evidence"])
            for provenance in claim.get("additional_provenance", []):
                if isinstance(provenance, dict) and isinstance(provenance.get("evidence"), dict):
                    sources.append(provenance["evidence"])

    result, seen = [], set()
    for source in sources:
        projected = _evidence_projection(
            source,
            dbms=record["dbms"],
            doc_version=str(record.get("doc_version") or "unknown"),
        )
        if projected is None:
            continue
        technical = projected["technical"]
        key = (
            projected["url"], technical.get("block_id"),
            technical.get("span_start"), technical.get("span_end"), projected["snippet"],
        )
        if key not in seen:
            seen.add(key)
            result.append(projected)
    return result


def _evidence_valid(record: dict, evidence: list[dict]) -> bool:
    quality = record.get("quality", {})
    if quality.get("structural_evidence_resolved") is not True or not evidence:
        return False
    primary = evidence[0]
    return bool(
        primary.get("url")
        and primary.get("snippet")
        and primary.get("technical", {}).get("block_id")
    )


def project_feature(record: dict) -> dict:
    if record.get("schema_version") != "canonical-feature-kb-projection/v1":
        raise ValueError("Frontend v2 projection requires final canonical-feature-kb-projection/v1 records")
    structural = record.get("structural", {})
    identity = record.get("canonical_identity", {})
    canonical_id = str(record.get("feature_id") or "").strip()
    if not canonical_id or not identity.get("key"):
        raise ValueError("Canonical record is missing a stable identity")

    syntax_claims = [claim for claim in _claims(record, "syntax")
                     if str((claim.get('evidence') or {}).get('evidence_role') or '').upper() in {'FORMAL_SYNTAX','SYNTAX','SIGNATURE'}]
    semantic_syntax = list(dict.fromkeys(str(claim.get('value') or '').strip() for claim in syntax_claims if claim.get('value')))
    # Identity spellings/signatures are not automatically official formal syntax.
    syntax = semantic_syntax
    variants = _unique(structural.get("signatures", []))
    descriptions = _claim_values(record, "description")
    examples = _claim_values(record, "examples")
    constraints = _claim_values(record, "constraints")
    aliases = _unique(structural.get("aliases", []))
    evidence = _all_evidence(record)
    evidence_valid = _evidence_valid(record, evidence)
    name = str(structural.get("name") or identity.get("canonical_name") or "").strip()

    missing = []
    values = {
        "syntax": syntax,
        "description": descriptions,
        "examples": examples,
        "aliases": aliases,
        "constraints": constraints,
        "evidence": evidence if evidence_valid else [],
    }
    if not name:
        missing.append("identity")
    missing.extend(field for field in DISPLAY_FIELDS if not values[field])
    if not evidence_valid:
        missing.append("evidence")
    complete = bool(name and syntax and descriptions and evidence_valid)

    return {
        "schema_version": "frontend-feature-projection/v2",
        "canonical_id": canonical_id,
        "feature_id": canonical_id,
        "canonical_identity": deepcopy(identity),
        "canonical_identity_key": identity["key"],
        "discriminator": identity.get("discriminator"),
        "dbms": record["dbms"],
        "doc_version": record.get("doc_version", "unknown"),
        "feature_type": record["feature_type"],
        "representation_status": record.get('representation_status'),
        "name": name,
        "signature": syntax[0] if syntax else "",
        "syntax": syntax,
        "description": descriptions[0] if descriptions else None,
        "descriptions": descriptions,
        "identity_variants": variants,
        "official_example_claims": deepcopy(_claims(record, "examples")),
        "parameters":deepcopy(_claims(record,'parameters')),
        "return_type":deepcopy(_claims(record,'return_type')),
        "semantic_execution_state":record.get('semantic_execution_state'),
        "field_validation_status":record.get('field_validation_status'),
        "field_provenance":{k:deepcopy(v) for k,v in record.get('semantics',{}).items() if isinstance(v,list)},
        "examples": examples,
        "aliases": aliases,
        "constraints": constraints,
        "evidence": evidence,
        "evidence_valid": evidence_valid,
        "quality_status": "Complete" if complete else "Partial",
        "missing_fields": missing,
        "source_anchors": _unique(item["url"] for item in evidence),
        "source_sections_display": _unique(item["section"] for item in evidence),
        "status": record.get("semantic_status"),
        "promotion_eligibility": record.get("promotion_eligibility"),
        "overload_count": 1,
        "overloads": [],
        "is_review": record.get('promotion_eligibility') == 'review_required',
    }


def _attach_overload_families(features: list[dict]) -> None:
    families: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for feature in features:
        if feature.get('representation_status')=='UNRESOLVED_SEPARATE_AUTHORITATIVE_RECORDS':continue
        canonical_name = str(feature["canonical_identity"].get("canonical_name") or feature["name"])
        families[(feature["dbms"], feature["feature_type"], canonical_name_key(canonical_name))].append(feature)
    for members in families.values():
        members.sort(key=lambda row: (row["canonical_identity_key"], row["canonical_id"]))
        overloads = [
            {
                "canonical_id": member["canonical_id"],
                "discriminator": member.get("discriminator"),
                "syntax": member["syntax"],
            }
            for member in members
        ]
        for index, member in enumerate(members, start=1):
            member["overload_count"] = len(members)
            member["overload_index"] = index
            member["overloads"] = overloads if len(members) > 1 else []


def project_database(records: list[dict], expected_dbms: str | None = None) -> dict:
    if not records:
        raise ValueError("Canonical KB is empty")
    dbms_values = {str(record.get("dbms") or "") for record in records}
    if len(dbms_values) != 1:
        raise ValueError("A database projection must contain exactly one DBMS")
    dbms = next(iter(dbms_values))
    if expected_dbms and dbms != expected_dbms:
        raise ValueError(f"Expected {expected_dbms!r}, got {dbms!r}")
    canonical_ids = [record.get("feature_id") for record in records]
    identity_keys = [record.get("canonical_identity", {}).get("key") for record in records]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise ValueError("Canonical KB contains duplicate IDs or identity keys")
    identity_groups=defaultdict(list)
    for record in records:identity_groups[record.get('canonical_identity',{}).get('key')].append(record)
    for duplicates in identity_groups.values():
        if len(duplicates)>1 and (any(r.get('representation_status')!='UNRESOLVED_SEPARATE_AUTHORITATIVE_RECORDS'
                                    for r in duplicates)
                or len({r.get('provenance',{}).get('identity_unit_id') for r in duplicates})!=len(duplicates)):
            raise ValueError('Canonical KB contains unqualified duplicate identity keys')

    features = [project_feature(record) for record in records]
    _attach_overload_families(features)
    features.sort(key=lambda row: (
        FEATURE_TYPES.index(row["feature_type"]),
        str(row["name"]).casefold(), row["canonical_identity_key"],
    ))
    by_type = Counter(feature["feature_type"] for feature in features)
    by_quality = Counter(feature["quality_status"] for feature in features)
    return {
        "schema_version": "frontend-kb-projection/v2",
        "dbms": dbms,
        "source_schema": "canonical-feature-kb-projection/v1",
        "counts": {
            "canonical_total": len(features),
            "by_feature_type": {feature_type: by_type[feature_type] for feature_type in FEATURE_TYPES},
            "complete": by_quality["Complete"],
            "partial": by_quality["Partial"],
        },
        "features": features,
    }


def load_and_project(path: str | Path, expected_dbms: str | None = None) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Canonical KB artifact must be a JSON array")
    return project_database(value, expected_dbms)


def frontend_quality(projections: list[dict]) -> dict:
    result = {"total": 0, "dbms": {}}
    for projection in projections:
        dbms = projection["dbms"]
        result["dbms"][dbms] = {}
        for feature_type in FEATURE_TYPES:
            rows = [row for row in projection["features"] if row["feature_type"] == feature_type]
            item = {
                "total": len(rows),
                "complete": sum(row["quality_status"] == "Complete" for row in rows),
                "partial": sum(row["quality_status"] == "Partial" for row in rows),
                "with_description": sum(bool(row["description"]) for row in rows),
                "with_syntax": sum(bool(row["syntax"]) for row in rows),
                "with_examples": sum(bool(row["examples"]) for row in rows),
                "with_valid_evidence": sum(row["evidence_valid"] for row in rows),
            }
            result["dbms"][dbms][feature_type] = item
            result["total"] += len(rows)
    return result


def write_frontend_projection(
    sources: dict[str, str | Path], data_dir: str | Path, data_js_path: str | Path,
) -> dict:
    data_dir, data_js_path = Path(data_dir), Path(data_js_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    projections = []
    source_manifest = {}
    for dbms, path in sources.items():
        projection = load_and_project(path, dbms)
        projections.append(projection)
        source_manifest[dbms] = str(Path(path))
        (data_dir / f"{dbms}.json").write_text(
            json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    projections.sort(key=lambda row: row["dbms"])
    features = [feature for projection in projections for feature in projection["features"]]
    stats = {
        "total": len(features),
        "dbms": {
            projection["dbms"]: projection["counts"]["by_feature_type"]
            for projection in projections
        },
    }
    quality = frontend_quality(projections)
    data_js_path.parent.mkdir(parents=True, exist_ok=True)
    data_js_path.write_text("\n".join([
        "// Auto-generated by scripts/build_frontend_projection_v2.py",
        "window.DBMS_LIST = " + json.dumps([row["dbms"] for row in projections], ensure_ascii=False) + ";",
        "window.FEATURE_DATA = " + json.dumps(features, ensure_ascii=False) + ";",
        "window.REVIEW_DATA = [];",
        "window.STATS = " + json.dumps(stats, ensure_ascii=False) + ";",
        "window.QUALITY_DATA = " + json.dumps(quality, ensure_ascii=False) + ";",
        "",
    ]), encoding="utf-8")
    manifest = {
        "schema_version": "frontend-projection-build/v2",
        "sources": source_manifest,
        "canonical_records": len(features),
        "frontend_records": len(features),
        "dbms": [row["dbms"] for row in projections],
        "stats": stats,
        "quality": quality,
        "identity_preserved": len({row["canonical_id"] for row in features}) == len(features),
    }
    (data_dir / "index.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
