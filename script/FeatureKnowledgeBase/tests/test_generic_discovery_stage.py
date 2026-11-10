from __future__ import annotations

import json
from pathlib import Path

from agents.discovery import generic_stage


def test_format_detection_and_markdown_plaintext_links() -> None:
    markdown = "# Docs\n\n- [One](pages/one.html)\n- [Two](/docs/two.html)\n"
    assert generic_stage.detect_document_format(
        "text/plain", "https://example.test/docs/index.txt", markdown,
    ) == "MARKDOWN"
    links = generic_stage.extract_document_links(markdown, "MARKDOWN")
    assert [row["raw_target"] for row in links] == ["pages/one.html", "/docs/two.html"]
    assert all(row["extraction_method"] == "markdown_link" for row in links)
    assert not any(row["extraction_method"] == "plaintext_url" for row in links)

    plaintext = "Documentation pages:\nhttps://example.test/docs/one\nhttps://example.test/docs/two\n"
    assert generic_stage.detect_document_format(
        "text/plain", "https://example.test/docs/index.txt", plaintext,
    ) == "PLAIN_TEXT"
    assert len(generic_stage.extract_document_links(plaintext, "PLAIN_TEXT")) == 2
    cleaned = generic_stage.extract_document_links(
        "See https://example.test:8443/docs/index.md`.", "PLAIN_TEXT",
    )
    assert cleaned[0]["raw_target"] == "https://example.test:8443/docs/index.md"

    assert generic_stage.is_document_index(
        url="https://example.test/docs/page.md",
        root_url="https://example.test/docs/",
        detected_format="MARKDOWN",
        links=links,
        text=markdown,
    )[0] == "DOCUMENT_INDEX"
    assert generic_stage.is_document_index(
        url="https://example.test/docs/page.md",
        root_url="https://example.test/docs/",
        detected_format="MARKDOWN",
        links=[{"resolved_url": "https://example.test/docs/other"}],
        text="A paragraph with one related link.",
    )[0] == "CONTENT_PAGE"


def test_html_links_and_standard_relative_resolution() -> None:
    html = '<main><a href="../guide/one.html"> One </a><a href="/assets/site.css">css</a></main>'
    assert generic_stage.detect_document_format(
        "text/html", "https://example.test/docs/index", html,
    ) == "HTML"
    links = generic_stage.extract_document_links(html, "HTML")
    assert links[0]["anchor_text"] == "One"
    assert generic_stage._normalize_url(
        generic_stage.urljoin("https://example.test/docs/index", links[0]["raw_target"])
    ) == "https://example.test/guide/one.html"


class _FakeResponse:
    def __init__(self, url: str, body: str, content_type: str) -> None:
        self.url = url
        self.content = body.encode("utf-8")
        self.headers = {"content-type": content_type}
        self.status_code = 200
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, pages: dict[str, tuple[str, str]]) -> None:
        self.pages = pages
        self.trust_env = True

    def get(self, url: str, **_: object) -> _FakeResponse:
        body, content_type = self.pages[url]
        return _FakeResponse(url, body, content_type)


def test_stage_a_crawls_html_markdown_and_plaintext_without_feature_classification(
    tmp_path: Path, monkeypatch
) -> None:
    root = "https://example.test/docs/"
    pages = {
        "https://example.test/docs": (
            '<html><body><a href="index.txt">catalog</a>'
            '<a href="/docs/index.txt">duplicate</a>'
            '<a href="/assets/logo.png">asset</a>'
            '<a href="https://outside.test/docs/x">external</a></body></html>',
            "text/html",
        ),
        "https://example.test/docs/index.txt": (
            "# Documentation\n"
            "[One](pages/one.html)\n"
            "[Two](/docs/pages/two.md)\n"
            "[One again](pages/one.html)\n",
            "text/plain",
        ),
        "https://example.test/docs/pages/one.html": (
            "<html><body><h1>One</h1><p>description</p></body></html>",
            "text/html",
        ),
        "https://example.test/docs/pages/two.md": (
            "# Two\nplain content\n",
            "text/markdown",
        ),
    }
    monkeypatch.setattr(generic_stage.requests, "Session", lambda: _FakeSession(pages))
    output = tmp_path / "run"
    report = tmp_path / "report.md"
    result = generic_stage.run_generic_discovery(
        dbms="synthetic",
        root_url=root,
        output_root=output,
        max_depth=2,
        timeout=1,
        report_path=report,
    )

    assert result["status"] == "accepted"
    summary = result["summary"]
    assert summary["fetched_pages"] == 4
    assert summary["fetch_failures"] == 0
    assert summary["format_counts"] == {"HTML": 2, "MARKDOWN": 2}
    assert summary["scope_rejects"] >= 1
    assert summary["asset_rejects"] >= 1
    assert summary["duplicate_urls"] >= 1

    page_rows = [
        json.loads(line)
        for line in (output / "corpus" / "page_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["detected_format"] for row in page_rows} == {"HTML", "MARKDOWN"}
    assert any(row["document_role"] == "DOCUMENT_INDEX" for row in page_rows)
    assert (output / "discovery" / "discovered_urls.jsonl").is_file()
    assert (output / "discovery" / "discovery_edges.jsonl").is_file()
    assert (output / "discovery" / "discovery_summary.json").is_file()
    assert (output / "corpus" / "corpus_manifest.json").is_file()
    assert (output / "evaluation" / "discovery_run_manifest.json").is_file()
    assert report.is_file()


def test_generic_scope_rejects_binary_and_external_links() -> None:
    assert not generic_stage._is_html_url("https://example.test/docs/file.pdf")
    assert not generic_stage._url_in_scope(
        "https://outside.test/docs/page.html", "https://example.test/docs/", True,
    )
