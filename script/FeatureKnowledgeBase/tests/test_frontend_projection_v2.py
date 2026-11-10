from __future__ import annotations

import json

from agents.semantic_extractor.frontend_projection_v2 import (
    project_database,
    write_frontend_projection,
)


def _evidence(text="ABS(number)"):
    return {
        "url": "https://docs.example/ref/abs.html",
        "source_anchor": "https://docs.example/ref/abs.html#abs",
        "section_heading": "ABS",
        "page_id": "p1", "section_id": "s1", "block_id": "b1",
        "block_type": "code", "evidence_role": "definition",
        "evidence_span": {"start": 0, "end": len(text), "text": text},
        "page_hash": "abc",
    }


def _record(discriminator="abs(arg)", *, description="Returns an absolute value.", example=""):
    suffix = "1" if discriminator == "abs(arg)" else "2"
    claim = lambda value, role: ([{
        "value": value, "evidence": {**_evidence(value), "evidence_role": role}
    }] if value else [])
    return {
        "schema_version": "canonical-feature-kb-projection/v1",
        "feature_id": "canonical:" + suffix * 24,
        "canonical_identity": {
            "key": f"test|function|abs|{discriminator}",
            "canonical_name": "abs", "discriminator": discriminator,
        },
        "dbms": "test", "doc_version": "1", "feature_type": "function",
        "structural": {
            "name": "ABS", "canonical_name": "abs",
            "signatures": ["ABS(number)" if suffix == "1" else "ABS()"],
            "aliases": [], "attributes": {}, "return_types": [],
            "primary_evidence": _evidence(), "supporting_evidence": [],
        },
        "semantics": {
            "description": claim(description, "description"), "syntax": claim("ABS(number)", "FORMAL_SYNTAX"),
            "examples": claim(example, "example"), "constraints": [],
            "parameters": [], "return_type": [], "notes": [],
        },
        "quality": {"structural_evidence_resolved": True},
        "semantic_status": "enriched", "promotion_eligibility": "promotion_ready",
    }


def test_projection_preserves_each_canonical_identity_and_links_overloads():
    projection = project_database([_record(), _record("abs()", description="")], "test")

    assert projection["counts"]["canonical_total"] == 2
    assert len(projection["features"]) == 2
    assert {row["canonical_id"] for row in projection["features"]} == {
        "canonical:" + "1" * 24, "canonical:" + "2" * 24,
    }
    assert all(row["overload_count"] == 2 for row in projection["features"])
    assert projection["counts"]["complete"] == 1
    assert projection["counts"]["partial"] == 1


def test_missing_optional_fields_are_explicit_without_invalidating_complete():
    feature = project_database([_record()], "test")["features"][0]

    assert feature["quality_status"] == "Complete"
    assert {"examples", "aliases", "constraints"}.issubset(feature["missing_fields"])
    assert feature["description"] == "Returns an absolute value."
    assert feature["evidence_valid"] is True
    assert feature["evidence"][0]["technical"]["block_id"] == "b1"


def test_static_build_writes_per_db_json_and_bootstrap(tmp_path):
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps([_record()]), encoding="utf-8")
    manifest = write_frontend_projection(
        {"test": canonical}, tmp_path / "data", tmp_path / "web" / "js" / "data.js"
    )

    assert manifest["canonical_records"] == manifest["frontend_records"] == 1
    assert manifest["identity_preserved"] is True
    assert (tmp_path / "data" / "test.json").is_file()
    assert "window.QUALITY_DATA" in (tmp_path / "web" / "js" / "data.js").read_text()
