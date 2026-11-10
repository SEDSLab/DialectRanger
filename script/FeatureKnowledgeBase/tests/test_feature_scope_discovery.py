from __future__ import annotations

import json
from pathlib import Path

from agents.discovery import generic_stage
from agents.discovery.scope_resolver import ScopeResolver


class _Response:
    def __init__(self, url: str, body: str, content_type: str = "text/html") -> None:
        self.url = url
        self.content = body.encode("utf-8")
        self.headers = {"content-type": content_type}
        self.status_code = 200
        self.encoding = "utf-8"
        self.text = body

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self, pages: dict[str, tuple[str, str]]) -> None:
        self.pages = pages
        self.trust_env = True

    def get(self, url: str, **_: object) -> _Response:
        body, content_type = self.pages[url]
        return _Response(url, body, content_type)


def test_scope_resolver_records_roots_not_feature_identities(tmp_path: Path, monkeypatch) -> None:
    pages = {
        "https://newdb.test/docs/functions": (
            "<html><title>NewDB Functions</title><body>"
            "<a href='/docs/functions/aggregate'>Aggregate Functions</a>"
            "<a href='/docs/functions/scalar'>Scalar Functions</a></body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/functions/aggregate": (
            "<html><title>Aggregate Functions</title><body>Aggregate reference.</body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/functions/scalar": (
            "<html><title>Scalar Functions</title><body>Scalar reference.</body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/operators": (
            "<html><title>NewDB Operators</title><body>"
            "<a href='/docs/operators/arithmetic'>Arithmetic Operators</a>"
            "<a href='/docs/operators/comparison'>Comparison Operators</a></body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/operators/arithmetic": (
            "<html><title>Arithmetic Operators</title><body>Operator reference.</body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/operators/comparison": (
            "<html><title>Comparison Operators</title><body>Operator reference.</body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/data-types": (
            "<html><title>NewDB Data Types</title><body>"
            "<a href='/docs/data-types/numeric'>Numeric Types</a>"
            "<a href='/docs/data-types/string'>String Types</a></body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/data-types/numeric": (
            "<html><title>Numeric Types</title><body>Type reference.</body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/data-types/string": (
            "<html><title>String Types</title><body>Type reference.</body></html>",
            "text/html",
        ),
    }
    monkeypatch.setattr("agents.discovery.scope_resolver.requests.Session", lambda: _Session(pages))

    def search(query: str):
        family = "FUNCTION" if "function" in query else "OPERATOR" if "operator" in query else "DATATYPE"
        return [{
            "url": {
                "FUNCTION": "https://newdb.test/docs/functions",
                "OPERATOR": "https://newdb.test/docs/operators",
                "DATATYPE": "https://newdb.test/docs/data-types",
            }[family],
            "title": family.title(),
            "provider": "fixture",
        }]

    result = ScopeResolver(
        dbms="newdb",
        output_root=tmp_path / "scope-corpus",
        search_provider=search,
    ).resolve()

    assert result["status"] == "accepted"
    manifest = result["manifest"]
    assert manifest["scope_completeness_status"] == "COMPLETE"
    assert manifest["feature_identity_count_in_scope_manifest"] == 0
    assert all(len(manifest["scopes"][family]) >= 1 for family in ("FUNCTION", "OPERATOR", "DATATYPE"))
    assert (tmp_path / "scope-corpus" / "scope" / "scope_discovery_trace.jsonl").is_file()


def test_scoped_crawl_preserves_feature_scope_and_stops_at_scoped_frontier(
    tmp_path: Path, monkeypatch
) -> None:
    root = "https://newdb.test/docs/functions/index.md"
    manifest = {
        "schema_version": "feature-scope-manifest/v1",
        "dbms": "newdb",
        "official_sources": [{"domain": "newdb.test", "verified_official": True}],
        "scopes": {
            "FUNCTION": [{"url": root, "title": "NewDB Functions", "scope_kind": "REFERENCE_ROOT"}],
            "OPERATOR": [{"url": "https://newdb.test/docs/operators", "title": "Operators", "scope_kind": "REFERENCE_ROOT"}],
            "DATATYPE": [{"url": "https://newdb.test/docs/data-types", "title": "Data Types", "scope_kind": "REFERENCE_ROOT"}],
        },
        "feature_identity_count_in_scope_manifest": 0,
    }
    pages = {
        root: (
            "# Functions\n[Aggregate](aggregate.md)\n"
            "[Operators](/docs/operators)\n[Unrelated](../../install)\n",
            "text/markdown",
        ),
        "https://newdb.test/docs/functions/aggregate.md": (
            "# Aggregate Functions\n\nA reference page.\n",
            "text/markdown",
        ),
        "https://newdb.test/docs/operators": (
            "<html><title>Operators</title><body><a href='/docs/operators/arithmetic'>Arithmetic</a></body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/operators/arithmetic": (
            "<html><title>Arithmetic Operators</title><body>+</body></html>",
            "text/html",
        ),
        "https://newdb.test/install": (
            "<html><title>Install</title><body>not in a scope</body></html>",
            "text/html",
        ),
        "https://newdb.test/docs/data-types": (
            "<html><title>Data Types</title><body>types</body></html>",
            "text/html",
        ),
    }
    monkeypatch.setattr(generic_stage.requests, "Session", lambda: _Session(pages))
    output = tmp_path / "scoped"
    result = generic_stage.run_scoped_discovery(
        dbms="newdb",
        scope_manifest=manifest,
        output_root=output,
        max_depth=4,
        timeout=1,
    )

    assert result["status"] == "accepted"
    assert result["summary"]["frontier_stop_reason"] == "SCOPED_FRONTIER_EXHAUSTED"
    assert result["summary"]["scope_mode"] is True
    assert result["summary"]["feature_scope_counts"]["FUNCTION"] >= 1
    assert result["summary"]["cross_scope_edges"] >= 1
    rows = [
        json.loads(line)
        for line in (output / "corpus" / "page_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(row["feature_scopes"] for row in rows if row["fetch_status"] == "FETCHED")
    assert not any("install" in row["url"] for row in rows)
    assert (output / "evaluation" / "corpus_summary.json").is_file()
    assert (output / "evaluation" / "scope_surface_audit.json").is_file()
    assert (output / "evaluation" / "run_manifest.json").is_file()
