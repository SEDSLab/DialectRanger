"""Offline contract coverage for the Generic Unified production route."""
from __future__ import annotations

import json
from pathlib import Path

from agents.contracts.document_ir import from_v2_page
from agents.document_structure_v2.topology import build_page_topology
from agents.pipeline.generic_unified import run_generic_downstream
from agents.profile_bootstrap.cold_start import build_cold_start_profile


def _write_page(root: Path, name: str, title: str, body: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(
        f"<!doctype html><html><head><title>{title}</title></head><body><main>{body}</main></body></html>",
        encoding="utf-8",
    )
    return {
        "url": f"https://fixture.example/{name}",
        "html_path": str(path),
        "feature_context": [],
        "crawl_depth": 0,
    }


def _documents(tmp_path: Path) -> list[dict]:
    specs = [
        _write_page(
            tmp_path,
            "functions.html",
            "Function reference",
            """
            <h1>Functions</h1>
            <table><tr><th>Function</th><th>Aliases</th><th>Description</th></tr>
            <tr><td>ADD(X,Y)</td><td>PLUS</td><td>Returns the sum of two values.</td></tr></table>
            <h2>IDENTITY_ONLY function</h2>
            """,
        ),
        _write_page(
            tmp_path,
            "operators.html",
            "Operator reference",
            """
            <h1>Operators</h1>
            <table><tr><th>Operator</th><th>Description</th></tr>
            <tr><td>+</td><td>Adds the left and right operands.</td></tr></table>
            """,
        ),
        _write_page(
            tmp_path,
            "datatypes.html",
            "Data type reference",
            """
            <h1>Data types</h1>
            <table><tr><th>Data type</th><th>Description</th></tr>
            <tr><td>INTEGER</td><td>Stores signed integer values.</td></tr></table>
            """,
        ),
        _write_page(
            tmp_path,
            "review.html",
            "Reference concepts",
            "<h2>Ambiguous concept</h2><p>This page has no bounded feature slot.</p>",
        ),
    ]
    return [
        from_v2_page(
            build_page_topology(spec, {}),
            dbms="fixture",
            html_path=spec["html_path"],
        )
        for spec in specs
    ]


def test_generic_unified_offline_full_chain_has_no_legacy_boundary(tmp_path: Path):
    documents = _documents(tmp_path / "pages")
    profile = build_cold_start_profile(
        dbms="fixture", documents=documents, run_id="generic-contract-test",
    )
    output = tmp_path / "run"
    result = run_generic_downstream(
        dbms="fixture",
        documents=documents,
        profile=profile,
        output_root=output,
        semantic=False,
    )

    manifest = json.loads((output / "execution_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pipeline"] == "unified"
    assert manifest["profile_source"] == "generic_structural_profile"
    assert manifest["identity_source"] == "generic_identity_unit"
    assert manifest["type_source"] == "generic_owner_local_typed_evidence"
    assert manifest["admission_source"] == "generic_identity_and_evidence_contract"
    assert manifest["evidence_source"] == "generic_owner_aligned_evidence_bundle"
    assert manifest["legacy_profile_dsl_used"] is False
    assert manifest["legacy_formal_candidate_used"] is False
    assert manifest["legacy_fallback_used"] is False

    types = {
        str(row.get("feature_type") or "").upper()
        for row in result["type"]["units"]
    }
    assert {"FUNCTION", "OPERATOR", "DATATYPE"} <= types
    assert (output / "document_ir" / "documents.jsonl").is_file()
    assert (output / "evidence" / "evidence_envelopes.jsonl").is_file()
    assert (output / "canonical" / "canonical_kb.json").is_file()
    assert (output / "frontend" / "web" / "index.html").is_file()
    assert result["lineage"]["status"] == "PASS"


def test_unified_orchestrator_does_not_import_legacy_downstream():
    source = Path("agents/pipeline/orchestrator.py").read_text(encoding="utf-8")
    assert "run_onboarding_lifecycle" not in source
    assert "adapt_formal_artifacts" not in source
    assert "from agents.pipeline.adapters" not in source


def test_supplied_profile_reaches_identity_without_regeneration(tmp_path, monkeypatch):
    from agents.pipeline import presemantic
    from agents.profile_bootstrap.cold_start import profile_digest
    documents = _documents(tmp_path / 'pages')
    profile = build_cold_start_profile(dbms='fixture', documents=documents, run_id='fixed-input')
    def forbidden(*args, **kwargs):
        raise AssertionError('Provided profile was silently regenerated')
    monkeypatch.setattr(presemantic, 'build_cold_start_profile', forbidden)
    output = tmp_path / 'run'
    run_generic_downstream(dbms='fixture', documents=documents, profile=profile, output_root=output, semantic=False)
    metadata = json.loads((output/'presemantic/profile/profile_metadata.json').read_text(encoding='utf-8'))
    assert metadata['load_mode'] == 'PROVIDED_PROFILE'
    assert metadata['profile_digest'] == profile_digest(profile)
