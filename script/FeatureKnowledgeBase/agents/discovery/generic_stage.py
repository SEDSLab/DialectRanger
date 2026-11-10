"""Format-aware, feature-agnostic documentation discovery.

Stage A deliberately stops at a frozen documentation corpus.  It does not
classify Feature Types and does not call any downstream extraction stage.
HTML, Markdown, and plain-text documentation indexes are treated as the same
kind of crawl surface; only their structural link syntax differs.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import time
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

import requests

from .http_transport import fetch_with_negotiation
from .url_scope import (
    _documentation_scope_root,
    _is_html_url,
    _normalize_url,
    _url_in_scope,
)


PIPELINE_VERSION = "generic-discovery-corpus/v1"
SUPPORTED_FORMATS = {"HTML", "MARKDOWN", "PLAIN_TEXT", "OTHER"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}
_LOGIN_SEGMENTS = {"login", "signin", "sign-in", "authenticate", "auth"}

_MARKDOWN_LINK_RE = re.compile(
    r"(?<![!\\])\[([^\]]{1,500})\]\(\s*(?:<([^>]+)>|([^\s)]+))\s*(?:\"[^\"]*\"|'[^']*')?\s*\)",
    re.MULTILINE,
)
_MARKDOWN_REFERENCE_RE = re.compile(
    r"^\s{0,3}\[([^\]]{1,500})\]:\s*(?:<([^>]+)>|(\S+))\s*$",
    re.MULTILINE,
)
_ABSOLUTE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-=*]{3,}\s*$)")


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    """Write bounded records and return the digest of the exact UTF-8 bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open('wb') as stream:
        for row in rows:
            data = (json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
            stream.write(data)
            digest.update(data)
    return digest.hexdigest()


class _EdgeJournal:
    """Append-only audit; repeated scans never retain the frontier's edge graph."""
    def __init__(self, path):
        self.path = path
        self.count = 0
        path.write_bytes(b'')

    def append(self, edge):
        with self.path.open('ab') as stream:
            stream.write((json.dumps(edge, ensure_ascii=False, sort_keys=True)+'\n').encode('utf-8'))
        self.count += 1

    def __len__(self):
        return self.count

    def __iter__(self):
        with self.path.open(encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_anchor(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _strip_link_punctuation(value: str) -> str:
    return value.rstrip(".,;:!?)]}>`\"'")


def _scope_path_for_root(url: str) -> str:
    """Map a manifest root URL to its logical documentation scope path.

    A catalog root is often represented by ``index.md``/``index.html`` (or
    ``home``).  Descendant pages live beside that file, so matching against
    the literal file path would reject the entire catalog.  URL provenance
    still retains the original root URL; this helper only defines the
    boundary used for descendant admission.
    """

    path = urlparse(url).path or "/"
    trimmed = path.rstrip("/") or "/"
    terminal = trimmed.rsplit("/", 1)[-1].casefold()
    stem = re.sub(r"\.(?:html?|md|txt)$", "", terminal)
    if stem in {"index", "home"} or terminal in {"index", "home"}:
        parent = trimmed.rsplit("/", 1)[0]
        return parent.rstrip("/") or "/"
    return trimmed


class _HTMLLinks(HTMLParser):
    """Small HTML anchor parser which does not depend on page semantics."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "template", "noscript"}:
            self._skip_depth += 1
            return
        if lowered != "a" or self._skip_depth:
            return
        href = next((value for name, value in attrs if name.casefold() == "href" and value), None)
        self._href = href.strip() if href else None
        self._text = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"script", "style", "template", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if lowered == "a" and self._href is not None and not self._skip_depth:
            self.links.append({
                "raw_target": self._href,
                "anchor_text": _clean_anchor("".join(self._text)),
                "extraction_method": "html_anchor",
            })
            self._href = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None and not self._skip_depth:
            self._text.append(data)


def detect_document_format(content_type: str | None, url: str, text: str) -> str:
    """Detect a transport/content format without naming a DBMS or filename."""

    media_type = (content_type or "").split(";", 1)[0].strip().casefold()
    sample = text.lstrip("\ufeff \t\r\n")[:20000]
    path = urlparse(url).path.casefold()
    if media_type in {"text/html", "application/xhtml+xml"}:
        return "HTML"
    if re.match(r"<!doctype\s+html|<html(?:\s|>)|<head(?:\s|>)|<body(?:\s|>)", sample, re.I):
        return "HTML"

    markdown_links = len(_MARKDOWN_LINK_RE.findall(text)) + len(_MARKDOWN_REFERENCE_RE.findall(text))
    has_markdown_structure = bool(_MARKDOWN_HEADING_RE.search(text))
    if media_type in {"text/markdown", "text/x-markdown", "text/commonmark"}:
        return "MARKDOWN"
    if path.endswith((".md", ".markdown", ".mdown", ".mkdn")):
        return "MARKDOWN"
    if markdown_links >= 2 or (markdown_links >= 1 and has_markdown_structure):
        return "MARKDOWN"
    if media_type.startswith("text/") or path.endswith((".txt", ".text", ".rst", ".adoc")):
        return "PLAIN_TEXT"
    if text.strip() and (markdown_links or has_markdown_structure or _ABSOLUTE_URL_RE.search(text)):
        return "PLAIN_TEXT"
    return "OTHER"


def extract_document_links(text: str, detected_format: str) -> list[dict[str, str]]:
    """Extract links with one normalized provenance shape for all formats."""

    if detected_format == "HTML":
        parser = _HTMLLinks()
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            # A malformed HTML page may still contain useful textual links.
            parser.links = []
        return parser.links

    if detected_format not in {"MARKDOWN", "PLAIN_TEXT"}:
        return []

    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    occupied_ranges: list[tuple[int, int]] = []
    for match in _MARKDOWN_LINK_RE.finditer(text):
        label = _clean_anchor(match.group(1))
        target = (match.group(2) or match.group(3) or "").strip()
        if not target:
            continue
        item = (target, label)
        if item in seen:
            continue
        seen.add(item)
        occupied_ranges.append(match.span())
        links.append({
            "raw_target": target,
            "anchor_text": label,
            "extraction_method": "markdown_link",
        })

    for match in _MARKDOWN_REFERENCE_RE.finditer(text):
        label = _clean_anchor(match.group(1))
        target = (match.group(2) or match.group(3) or "").strip()
        item = (target, label)
        if not target or item in seen:
            continue
        seen.add(item)
        occupied_ranges.append(match.span())
        links.append({
            "raw_target": target,
            "anchor_text": label,
            "extraction_method": "markdown_reference",
        })

    for match in _ABSOLUTE_URL_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied_ranges):
            continue
        target = _strip_link_punctuation(match.group(0))
        item = (target, target)
        if item in seen:
            continue
        seen.add(item)
        links.append({
            "raw_target": target,
            "anchor_text": target,
            "extraction_method": "plaintext_url",
        })
    return links


def _login_like(url: str) -> bool:
    segments = {segment.casefold() for segment in urlparse(url).path.split("/") if segment}
    return bool(segments & _LOGIN_SEGMENTS)


def _same_origin(url: str, root_url: str) -> bool:
    return (urlparse(url).netloc or "").casefold() == (urlparse(root_url).netloc or "").casefold()


def is_document_index(
    *,
    url: str,
    root_url: str,
    detected_format: str,
    links: list[Mapping[str, str]],
    text: str = "",
) -> tuple[str, list[str]]:
    """Classify only the crawl surface role, never a Feature Type."""

    signals: list[str] = []
    if _normalize_url(url) == _normalize_url(root_url):
        signals.append("configured_root")
    same_origin_links = [
        item for item in links
        if _same_origin(str(item.get("resolved_url") or ""), root_url)
    ]
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    link_lines = sum(
        bool(_MARKDOWN_LINK_RE.search(line) or _MARKDOWN_REFERENCE_RE.search(line) or _ABSOLUTE_URL_RE.search(line))
        for line in nonempty_lines
    )
    link_line_ratio = link_lines / max(len(nonempty_lines), 1)
    if detected_format == "HTML" and len(links) >= 3:
        signals.append("multiple_html_links")
    if detected_format in {"MARKDOWN", "PLAIN_TEXT"} and len(links) >= 2 and link_line_ratio >= 0.20:
        signals.append("text_index_link_surface")
    if len(same_origin_links) >= 2 and link_line_ratio >= 0.20:
        signals.append("same_origin_link_cluster")
    if links and len(same_origin_links) / max(len(links), 1) >= 0.6 and link_line_ratio >= 0.20:
        signals.append("same_origin_link_density")
    if signals:
        return "DOCUMENT_INDEX", signals
    return "CONTENT_PAGE", signals


def _extension_for_format(detected_format: str) -> str:
    return {
        "HTML": ".html",
        "MARKDOWN": ".md",
        "PLAIN_TEXT": ".txt",
        "OTHER": ".bin",
    }.get(detected_format, ".bin")


def _git_snapshot(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"commit": None, "working_tree_clean": None, "modified_or_untracked_count": None}
    try:
        result["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), check=False,
            capture_output=True, text=True,
        ).stdout.strip() or None
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(root), check=False,
            capture_output=True, text=True,
        ).stdout.splitlines()
        result["modified_or_untracked_count"] = len(status)
        result["working_tree_clean"] = not status
    except Exception as exc:  # pragma: no cover - git is available in normal runs
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


class GenericDiscovery:
    """Run the generic Stage A discovery and corpus freeze."""

    def __init__(
        self,
        *,
        dbms: str,
        root_url: str | None = None,
        output_root: str | Path,
        scope_manifest: str | Path | Mapping[str, Any] | None = None,
        fetcher: Any = None,
        max_depth: int = 3,
        max_pages: int | None = None,
        timeout: int = 30,
        polite_delay: float = 0.0,
        trust_env: bool | None = None,
        report_path: str | Path | None = None,
    ) -> None:
        if not str(dbms).strip():
            raise ValueError("dbms must be non-empty")
        self.dbms = str(dbms).strip()
        self.fetcher = fetcher or fetch_with_negotiation
        self.output_root = Path(output_root).resolve()
        self.project_root = Path(__file__).resolve().parents[2]
        self.scope_mode = scope_manifest is not None
        self.scope_manifest_path: str | None = None
        self.scope_manifest: dict[str, Any] | None = None
        self.scope_records: list[dict[str, Any]] = []
        if scope_manifest is not None:
            if isinstance(scope_manifest, Mapping):
                manifest = dict(scope_manifest)
            else:
                manifest_path = Path(scope_manifest).resolve()
                self.scope_manifest_path = str(manifest_path)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("dbms") != self.dbms:
                raise ValueError("scope manifest DBMS does not match the crawl DBMS")
            if not manifest.get("scopes"):
                raise ValueError("scope manifest has no verified scopes")
            self.scope_manifest = manifest
            for family in ("FUNCTION", "OPERATOR", "DATATYPE"):
                for row in manifest.get("scopes", {}).get(family, []) or []:
                    url = _normalize_url(str(row.get("url") or ""))
                    parsed = urlparse(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        continue
                    self.scope_records.append({
                        "url": url,
                        "scope_path": _scope_path_for_root(url),
                        "title": row.get("title", ""),
                        "feature_scopes": [family],
                        "scope_kind": row.get("scope_kind", "REFERENCE_ROOT"),
                        "verified_member_urls": list(row.get("verified_member_urls") or []),
                        "selected_representation": row.get("selected_representation"),
                        "alternate_representations": list(row.get("alternate_representations") or []),
                    })
            unique_roots = {}
            for record in self.scope_records:
                existing = unique_roots.get(record['url'])
                if existing is None:
                    unique_roots[record['url']] = record
                else:
                    for key in ('feature_scopes', 'verified_member_urls', 'alternate_representations'):
                        for value in record[key]:
                            if value not in existing[key]:existing[key].append(value)
            self.scope_records = list(unique_roots.values())
            if not self.scope_records:
                raise ValueError("scope manifest has no usable HTTP(S) scope roots")
            # The primary root is retained for backwards-compatible fields;
            # scoped artifacts additionally carry the complete root list.
            self.root_url = self.scope_records[0]["url"]
        else:
            if not root_url:
                raise ValueError("root_url is required for legacy/manual discovery mode")
            normalized_root = _normalize_url(str(root_url).strip())
            parsed = urlparse(normalized_root)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("root_url must be an HTTP(S) URL")
            self.root_url = normalized_root
            self.scope_records = [{
                "url": normalized_root,
                "scope_path": _scope_path_for_root(normalized_root),
                "title": "",
                "feature_scopes": [],
                "scope_kind": "CONFIGURED_ROOT",
                "selected_representation": None,
                "alternate_representations": [],
            }]
        self.report_path = (
            Path(report_path).resolve()
            if report_path is not None
            else self.project_root / "reports" / (
                "generic_feature_scope_discovery.md"
                if self.scope_mode else f"{self.dbms}_generic_discovery_adaptation.md"
            )
        )
        self.scope_root = _documentation_scope_root(self.root_url) if not self.scope_mode else None
        self.scope_roots = [row["url"] for row in self.scope_records]
        self.scope_hosts = {(urlparse(url).hostname or "").casefold() for url in self.scope_roots}
        self.max_depth = max(0, int(max_depth))
        self.max_pages = int(max_pages) if max_pages is not None else None
        self.timeout = int(timeout)
        self.polite_delay = max(0.0, float(polite_delay))
        if trust_env is None:
            trust_env = os.environ.get("FEATURE_CRAWLER_HTTP_TRUST_ENV", "true").strip().casefold() not in _FALSE_ENV_VALUES
        self.trust_env = bool(trust_env)

    def _scope_decision(
        self, target: str, source_item: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str, list[str], str | None]:
        """Return (accepted, reason, feature_scopes, matched_root)."""

        normalized = _normalize_url(target)
        if not self.scope_mode:
            accepted = _url_in_scope(normalized, self.scope_root, True)
            return (
                accepted,
                "DOCUMENTATION_SCOPE" if accepted else "OUTSIDE_DOCUMENTATION_SCOPE",
                [],
                self.root_url if accepted else None,
            )
        target_host = (urlparse(normalized).hostname or "").casefold()
        if target_host not in self.scope_hosts:
            return False, "EXTERNAL_SCOPE", [], None
        matched: list[dict[str, Any]] = []
        target_path = urlparse(normalized).path.rstrip("/") or "/"
        for record in self.scope_records:
            root = record["url"]
            parsed_root = urlparse(root)
            root_path = str(record.get("scope_path") or _scope_path_for_root(root))
            if target_host != (parsed_root.hostname or "").casefold():
                continue
            if (target_path == root_path or target_path.startswith(root_path + "/")
                    or normalized in record.get("verified_member_urls", [])):
                matched.append(record)
        if matched:
            scopes = sorted({scope for row in matched for scope in row["feature_scopes"]})
            reason = "ROOT_ENTRY" if normalized in self.scope_roots else "ROOT_DESCENDANT"
            if source_item and source_item.get("source_scope_root") and source_item.get("source_scope_root") not in {
                row["url"] for row in matched
            }:
                reason = "CROSS_SCOPE_REFERENCE"
            return True, reason, scopes, matched[0]["url"]
        return False, "OUTSIDE_SCOPED_DOCUMENTATION", [], None

    def _feature_scopes_for_url(self, url: str) -> list[str]:
        return self._scope_decision(url)[2]

    @staticmethod
    def _content_fingerprint(text: str, detected_format: str) -> str | None:
        """Approximate cross-representation identity without feature semantics."""

        normalized = text
        if detected_format == "HTML":
            normalized = re.sub(r"<script\b.*?</script\s*>", " ", normalized, flags=re.I | re.S)
            normalized = re.sub(r"<style\b.*?</style\s*>", " ", normalized, flags=re.I | re.S)
            normalized = re.sub(r"<[^>]+>", " ", normalized)
            normalized = html.unescape(normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if len(normalized) < 80:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _fetch(
        self, session: requests.Session, url: str, source_item: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result = self.fetcher(
                session,
                url,
                timeout=self.timeout,
                caller_user_agent="FeatureCrawler/GenericDiscovery",
                allow_redirects=True,
            )
            raw_final_url = str(result.get("raw_final_url") or url)
            final_url = _normalize_url(str(result.get("final_url") or url))
            accepted, reason, _, _ = self._scope_decision(final_url, source_item)
            if not accepted:
                return {
                    "ok": False,
                    "status": "REDIRECT_OUT_OF_SCOPE",
                    "http_status": result.get("http_status"),
                    "final_url": final_url,
                    "raw_final_url": raw_final_url,
                    "content_type": result.get("content_type", ""),
                    "raw": b"",
                    "error_type": "RedirectOutsideScope",
                    "error_message": f"final URL outside documentation scope ({reason}): {final_url}",
                    "request_profile": result.get("request_profile"),
                    "selected_profile": result.get("selected_profile"),
                    "attempt_index": result.get("attempt_index"),
                    "attempt_count": result.get("attempt_count"),
                    "fallback_reason": result.get("fallback_reason"),
                    "attempts": result.get("attempts", []),
                    "transport_events": result.get("transport_events", []),
                }
            result["final_url"] = final_url
            result["raw_final_url"] = raw_final_url
            return result
        except Exception as exc:
            return {
                "ok": False,
                "status": "FETCH_FAILED",
                "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                "final_url": url,
                "raw_final_url": url,
                "content_type": "",
                "raw": b"",
                "encoding": "utf-8",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "request_profile": None,
                "selected_profile": None,
                "attempt_index": None,
                "attempt_count": 0,
                "fallback_reason": None,
                "attempts": [],
                "transport_events": [],
            }

    def _edge_base(self, *, source_url: str, target_url: str, item: Mapping[str, str], depth: int) -> dict[str, Any]:
        return {
            "edge_id": _sha256_text(
                f"{source_url}\n{target_url}\n{item.get('raw_target', '')}\n"
                f"{item.get('anchor_text', '')}\n{item.get('extraction_method', '')}"
            )[:20],
            "source_url": source_url,
            "target_url": target_url,
            "raw_target": item.get("raw_target", ""),
            "anchor_text": item.get("anchor_text", ""),
            "extraction_method": item.get("extraction_method", "unknown"),
            "source_format": item.get("source_format", "OTHER"),
            "source_document_role": item.get("source_document_role", "CONTENT_PAGE"),
            "feature_scopes": list(item.get("feature_scopes") or []),
            "source_scope_root": item.get("source_scope_root"),
            "depth": depth,
            "provenance": item.get("provenance", {}),
        }

    def run(self) -> dict[str, Any]:
        if self.output_root.exists() and any(self.output_root.iterdir()):
            if not self.scope_mode:
                raise ValueError(
                    f"generic discovery output must be new and empty; refusing to reuse: {self.output_root}"
                )
            allowed = {"scope"}
            existing = {path.name for path in self.output_root.iterdir()}
            if not existing.issubset(allowed):
                raise ValueError(
                    "scoped crawl output may contain only the cached scope directory; "
                    f"refusing to reuse: {self.output_root}"
                )
        self.output_root.mkdir(parents=True, exist_ok=True)
        discovery_dir = self.output_root / "discovery"
        pages_dir = self.output_root / "corpus" / "pages"
        discovery_dir.mkdir(parents=True, exist_ok=True)
        pages_dir.mkdir(parents=True, exist_ok=True)

        session = requests.Session()
        session.trust_env = self.trust_env
        queue: deque[dict[str, Any]] = deque()
        for root_record in self.scope_records:
            queue.append({
                "url": root_record["url"],
                "depth": 0,
                "source_parent": None,
                "source_anchor": "",
                "discovery_method": "scope_manifest_root" if self.scope_mode else "configured_root",
                "source_format": None,
                "feature_scopes": list(root_record.get("feature_scopes") or []),
                "source_scope_root": root_record["url"],
                "provenance_chain": [root_record["url"]],
            })
        scheduled = {str(item["url"]) for item in queue}
        visited: set[str] = set()
        pages: list[dict[str, Any]] = []
        discovered: list[dict[str, Any]] = []
        edges = _EdgeJournal(discovery_dir / 'discovery_edges.jsonl')
        format_counts: Counter[str] = Counter()
        role_counts: Counter[str] = Counter()
        link_method_counts: Counter[str] = Counter()
        rejection_counts: Counter[str] = Counter()
        duplicate_count = 0
        scope_reject_count = 0
        asset_reject_count = 0
        fetch_failures: list[dict[str, Any]] = []
        depth_blocked_count = 0
        non_index_expansion_count = 0
        cross_scope_edge_count = 0
        duplicate_representation_count = 0
        representation_by_fingerprint: dict[str, dict[str, Any]] = {}
        max_depth_reached = 0
        started_at = datetime.now(timezone.utc).isoformat()

        while queue and (self.max_pages is None or len(visited) < self.max_pages):
            item = queue.popleft()
            url = _normalize_url(str(item["url"]))
            if url in visited:
                continue
            visited.add(url)
            depth = int(item.get("depth", 0))
            max_depth_reached = max(max_depth_reached, depth)
            result = self._fetch(session, url, item)
            item_feature_scopes = list(item.get("feature_scopes") or self._feature_scopes_for_url(url))
            source_scope_root = item.get("source_scope_root")
            page_entry: dict[str, Any] = {
                "document_id": f"doc_{_sha256_text(url)[:16]}",
                "url": url,
                "final_url": result.get("final_url") or url,
                "content_type": result.get("content_type", ""),
                "detected_format": "OTHER",
                "document_role": "UNKNOWN",
                "fetch_status": result.get("status", "FETCH_FAILED"),
                "http_status": result.get("http_status"),
                "content_hash": None,
                "local_artifact_path": None,
                "source_parent": item.get("source_parent"),
                "source_anchor": item.get("source_anchor", ""),
                "discovery_method": item.get("discovery_method", "configured_root"),
                "crawl_depth": depth,
                "provenance": {
                    "root_url": self.root_url,
                    "scope_manifest_ref": "corpus_manifest.json",
                    "feature_scopes": item_feature_scopes,
                    "source_scope_root": source_scope_root,
                    "source_parent": item.get("source_parent"),
                    "source_anchor": item.get("source_anchor", ""),
                    "chain": list(item.get("provenance_chain") or [self.root_url]),
                },
                "error_type": result.get("error_type"),
                "error_message": result.get("error_message"),
                "feature_scopes": item_feature_scopes,
                "source_scope_root": source_scope_root,
                "selected_representation": None,
                "alternate_representations": [],
                "request_profile": result.get("request_profile"),
                "selected_profile": result.get("selected_profile"),
                "attempt_index": result.get("attempt_index"),
                "attempt_count": result.get("attempt_count", 0),
                "fallback_reason": result.get("fallback_reason"),
                "attempts": result.get("attempts", []),
                "transport_events": result.get("transport_events", []),
            }
            page_entry["provenance"]["http_transport"] = {
                "request_profile": result.get("request_profile"),
                "attempt_index": result.get("attempt_index"),
                "fallback_reason": result.get("fallback_reason"),
                "http_status": result.get("http_status"),
                "content_type": result.get("content_type", ""),
                "selected_profile": result.get("selected_profile"),
                "attempt_count": result.get("attempt_count", 0),
                "transport_events": result.get("transport_events", []),
            }
            if not result.get("ok"):
                fetch_failures.append({
                    "url": url,
                    "http_status": result.get("http_status"),
                    "error_type": result.get("error_type"),
                    "error_message": result.get("error_message"),
                })
                pages.append(page_entry)
                discovered.append({
                    "url": url,
                    "final_url": result.get("final_url") or url,
                    "source_parent": item.get("source_parent"),
                    "source_anchor": item.get("source_anchor", ""),
                    "depth": depth,
                    "discovery_method": item.get("discovery_method", "configured_root"),
                    "source_format": item.get("source_format"),
                    "feature_scopes": item_feature_scopes,
                    "source_scope_root": source_scope_root,
                    "fetch_status": result.get("status", "FETCH_FAILED"),
                    "document_role": "UNKNOWN",
                    "provenance": page_entry["provenance"],
                })
                continue

            raw = result.get("raw") or b""
            content_hash = _sha256_bytes(raw)
            encoding = str(result.get("encoding") or "utf-8")
            text = raw.decode(encoding, errors="replace")
            detected_format = detect_document_format(
                result.get("content_type", ""), result.get("final_url") or url, text,
            )
            links = extract_document_links(text, detected_format)
            for link in links:
                link["source_format"] = detected_format
                link["source_document_role"] = "UNKNOWN"
                link["provenance"] = {
                    "root_url": self.root_url,
                    "scope_manifest_ref": "../corpus/corpus_manifest.json",
                    "source_scope_root": source_scope_root,
                    "feature_scopes": item_feature_scopes,
                    "source_parent": url,
                    "source_anchor": link.get("anchor_text", ""),
                    "chain": list(item.get("provenance_chain") or [self.root_url]) + [url],
                }
                try:
                    link["resolved_url"] = _normalize_url(
                        urljoin(result.get("raw_final_url") or result.get("final_url") or url, link["raw_target"])
                    )
                    link["resolution_error"] = None
                except (TypeError, ValueError) as exc:
                    link["resolved_url"] = ""
                    link["resolution_error"] = f"{type(exc).__name__}: {exc}"
            document_role, role_signals = is_document_index(
                url=url,
                root_url=str(source_scope_root or self.root_url),
                detected_format=detected_format,
                links=links,
                text=text,
            )
            for link in links:
                link["source_document_role"] = document_role
                link["feature_scopes"] = item_feature_scopes
                link["source_scope_root"] = source_scope_root
            format_counts[detected_format] += 1
            role_counts[document_role] += 1
            for link in links:
                link_method_counts[link.get("extraction_method", "unknown")] += 1

            extension = _extension_for_format(detected_format)
            artifact_name = f"{page_entry['document_id']}{extension}"
            artifact_path = pages_dir / artifact_name
            representation_fingerprint = self._content_fingerprint(text, detected_format)
            duplicate_of = representation_by_fingerprint.get(representation_fingerprint) if representation_fingerprint else None
            if duplicate_of:
                duplicate_representation_count += 1
                page_entry.update({
                    "final_url": result.get("final_url") or url,
                    "detected_format": detected_format,
                    "document_role": document_role,
                    "role_signals": role_signals,
                    "content_hash": content_hash,
                    "representation_hash": representation_fingerprint,
                    "local_artifact_path": duplicate_of.get("local_artifact_path"),
                    "local_artifact_absolute_path": duplicate_of.get("local_artifact_absolute_path"),
                    "selected_representation": duplicate_of.get("selected_representation"),
                    "alternate_representations": [result.get("final_url") or url],
                    "canonical_document_id": duplicate_of.get("document_id"),
                    "fetch_status": "DUPLICATE_REPRESENTATION",
                    "error_type": None,
                    "error_message": None,
                    "link_count": len(links),
                    "same_origin_link_count": sum(
                        _same_origin(str(link.get("resolved_url") or ""), str(source_scope_root or self.root_url))
                        for link in links
                    ),
                })
                pages.append(page_entry)
                discovered.append({
                    "url": url,
                    "final_url": result.get("final_url") or url,
                    "source_parent": item.get("source_parent"),
                    "source_anchor": item.get("source_anchor", ""),
                    "depth": depth,
                    "discovery_method": item.get("discovery_method", "configured_root"),
                    "source_format": detected_format,
                    "feature_scopes": item_feature_scopes,
                    "source_scope_root": source_scope_root,
                    "fetch_status": "DUPLICATE_REPRESENTATION",
                    "document_role": document_role,
                    "canonical_document_id": duplicate_of.get("document_id"),
                    "provenance": page_entry["provenance"],
                })
                if self.polite_delay > 0:
                    time.sleep(self.polite_delay)
                continue
            artifact_path.write_bytes(raw)
            relative_artifact = str(artifact_path.relative_to(self.output_root)).replace("\\", "/")
            if representation_fingerprint:
                representation_by_fingerprint[representation_fingerprint] = {
                    "document_id": page_entry["document_id"],
                    "local_artifact_path": relative_artifact,
                    "local_artifact_absolute_path": str(artifact_path),
                    "selected_representation": detected_format,
                }
            page_entry.update({
                "final_url": result.get("final_url") or url,
                "detected_format": detected_format,
                "document_role": document_role,
                "role_signals": role_signals,
                "content_hash": content_hash,
                "representation_hash": representation_fingerprint,
                "local_artifact_path": relative_artifact,
                "local_artifact_absolute_path": str(artifact_path),
                "link_count": len(links),
                "same_origin_link_count": sum(
                    _same_origin(str(link.get("resolved_url") or ""), str(source_scope_root or self.root_url))
                    for link in links
                ),
                "fetch_status": "FETCHED",
                "selected_representation": detected_format,
                "error_type": None,
                "error_message": None,
            })
            pages.append(page_entry)
            discovered.append({
                "url": url,
                "final_url": result.get("final_url") or url,
                "source_parent": item.get("source_parent"),
                "source_anchor": item.get("source_anchor", ""),
                "depth": depth,
                "discovery_method": item.get("discovery_method", "configured_root"),
                "source_format": detected_format,
                "feature_scopes": item_feature_scopes,
                "source_scope_root": source_scope_root,
                "fetch_status": "FETCHED",
                "document_role": document_role,
                "provenance": page_entry["provenance"],
            })

            expand_allowed = depth < self.max_depth
            if not expand_allowed:
                depth_blocked_count += len(links)
            if expand_allowed:
                for link in links:
                    raw_target = str(link.get("raw_target") or "").strip()
                    target = str(link.get("resolved_url") or "")
                    edge = self._edge_base(
                        source_url=url, target_url=target, item=link, depth=depth + 1,
                    )
                    if not raw_target or raw_target.startswith(("#", "javascript:", "mailto:", "data:")):
                        edge.update({"accepted": False, "rejection_reason": "unsupported_link_scheme"})
                        rejection_counts["unsupported_link_scheme"] += 1
                        edges.append(edge)
                        continue
                    if not target or not _is_html_url(target):
                        edge.update({"accepted": False, "rejection_reason": "unsupported_asset"})
                        rejection_counts["unsupported_asset"] += 1
                        asset_reject_count += 1
                        edges.append(edge)
                        continue
                    if _login_like(target):
                        edge.update({"accepted": False, "rejection_reason": "login_or_auth_surface"})
                        rejection_counts["login_or_auth_surface"] += 1
                        edges.append(edge)
                        continue
                    allowed, reason_code, target_scopes, matched_scope_root = self._scope_decision(target, item)
                    if not allowed:
                        reason = reason_code.casefold()
                        edge.update({"accepted": False, "rejection_reason": reason})
                        rejection_counts[reason] += 1
                        scope_reject_count += 1
                        edges.append(edge)
                        continue
                    if reason_code == "CROSS_SCOPE_REFERENCE":
                        cross_scope_edge_count += 1
                        edge["scope_transition"] = reason_code
                    edge["target_feature_scopes"] = target_scopes
                    edge["target_scope_root"] = matched_scope_root
                    if target in scheduled:
                        edge.update({"accepted": False, "rejection_reason": "duplicate_url"})
                        rejection_counts["duplicate_url"] += 1
                        duplicate_count += 1
                        edges.append(edge)
                        continue
                    scheduled.add(target)
                    child_chain = list(item.get("provenance_chain") or [self.root_url]) + [url]
                    queue.append({
                        "url": target,
                        "depth": depth + 1,
                        "source_parent": url,
                        "source_anchor": link.get("anchor_text", ""),
                        "discovery_method": link.get("extraction_method", "unknown"),
                        "source_format": detected_format,
                        "feature_scopes": target_scopes,
                        "source_scope_root": matched_scope_root or source_scope_root,
                        "provenance_chain": child_chain,
                    })
                    edge.update({"accepted": True, "rejection_reason": None})
                    edges.append(edge)
            else:
                non_index_expansion_count += len(links)
                # Preserve the same edge/provenance contract at the depth
                # boundary.  These links are observed but intentionally not
                # scheduled, so the frozen audit can distinguish a complete
                # frontier from a crawl stopped by max_depth.
                for link in links:
                    raw_target = str(link.get("raw_target") or "").strip()
                    target = str(link.get("resolved_url") or "")
                    edge = self._edge_base(
                        source_url=url, target_url=target, item=link, depth=depth + 1,
                    )
                    if not raw_target or raw_target.startswith(("#", "javascript:", "mailto:", "data:")):
                        edge.update({"accepted": False, "rejection_reason": "unsupported_link_scheme"})
                        rejection_counts["unsupported_link_scheme"] += 1
                    elif not target or not _is_html_url(target):
                        edge.update({"accepted": False, "rejection_reason": "unsupported_asset"})
                        rejection_counts["unsupported_asset"] += 1
                        asset_reject_count += 1
                    elif _login_like(target):
                        edge.update({"accepted": False, "rejection_reason": "login_or_auth_surface"})
                        rejection_counts["login_or_auth_surface"] += 1
                    else:
                        allowed, reason_code, target_scopes, matched_scope_root = self._scope_decision(target, item)
                        if not allowed:
                            reason = reason_code.casefold()
                            edge.update({"accepted": False, "rejection_reason": reason})
                            rejection_counts[reason] += 1
                            scope_reject_count += 1
                        elif target in scheduled:
                            edge.update({"accepted": False, "rejection_reason": "duplicate_url"})
                            rejection_counts["duplicate_url"] += 1
                            duplicate_count += 1
                        else:
                            if reason_code == "CROSS_SCOPE_REFERENCE":
                                cross_scope_edge_count += 1
                                edge["scope_transition"] = reason_code
                            edge.update({"accepted": False, "rejection_reason": "max_depth"})
                            edge["target_feature_scopes"] = target_scopes
                            edge["target_scope_root"] = matched_scope_root
                            rejection_counts["max_depth"] += 1
                    edges.append(edge)
            if self.polite_delay > 0:
                time.sleep(self.polite_delay)

        budget_exhausted = bool(self.max_pages is not None and len(visited) >= self.max_pages and queue)
        stop_reason = "page_budget" if budget_exhausted else (
            "SCOPED_FRONTIER_EXHAUSTED" if self.scope_mode else "frontier_exhausted"
        )
        if not queue and not pages:
            stop_reason = "root_fetch_failed"

        # ``discovered_urls.jsonl`` is the complete frontier ledger, not only
        # the subset that happened to be fetched before a page budget stopped
        # the run.  Pending rows keep their source edge and provenance so a
        # future frozen-corpus stage can explain exactly where they came from.
        discovered_by_url = {
            str(row.get("url")): dict(row)
            for row in discovered
            if row.get("url")
        }
        for edge in edges:
            if not edge.get("accepted") or not edge.get("target_url"):
                continue
            target = str(edge["target_url"])
            if target in discovered_by_url:
                continue
            discovered_by_url[target] = {
                "url": target,
                "final_url": None,
                "source_parent": edge.get("source_url"),
                "source_anchor": edge.get("anchor_text", ""),
                "depth": edge.get("depth"),
                "discovery_method": edge.get("extraction_method", "unknown"),
                "source_format": edge.get("source_format"),
                "feature_scopes": list(edge.get("target_feature_scopes") or edge.get("feature_scopes") or []),
                "source_scope_root": edge.get("target_scope_root") or edge.get("source_scope_root"),
                "fetch_status": "PENDING_FRONTIER",
                "document_role": "UNKNOWN",
                "provenance": edge.get("provenance") or {},
            }
        discovered = sorted(discovered_by_url.values(), key=lambda row: str(row.get("url") or ""))

        page_manifest_hash = _jsonl(self.output_root / "corpus" / "page_manifest.jsonl", pages)
        _jsonl(discovery_dir / "discovered_urls.jsonl", discovered)

        corpus_manifest = {
            "schema_version": "FrozenDocumentationCorpus/v1",
            "pipeline_version": PIPELINE_VERSION,
            "dbms": self.dbms,
            "root_url": self.root_url,
            "scope_root": self.scope_root,
            "scope_mode": self.scope_mode,
            "scope_manifest_path": self.scope_manifest_path,
            "scope_roots": list(self.scope_roots),
            "feature_scope_counts": dict(sorted(Counter(
                scope
                for row in pages
                if row.get("fetch_status") == "FETCHED"
                for scope in row.get("feature_scopes") or []
            ).items())),
            "page_count": sum(row.get("fetch_status") == "FETCHED" for row in pages),
            "attempted_url_count": len(pages),
            "content_hashes": [row.get("content_hash") for row in pages if row.get("content_hash")],
            "page_manifest_path": "corpus/page_manifest.jsonl",
            "page_manifest_hash": page_manifest_hash,
            "raw_page_directory": "corpus/pages",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _json(self.output_root / "corpus" / "corpus_manifest.json", corpus_manifest)

        summary = {
            "schema_version": "GenericDiscoverySummary/v1",
            "pipeline_version": PIPELINE_VERSION,
            "dbms": self.dbms,
            "root_url": self.root_url,
            "scope_root": self.scope_root,
            "scope_mode": self.scope_mode,
            "scope_manifest_path": self.scope_manifest_path,
            "scope_roots": list(self.scope_roots),
            "visited_urls": len(visited),
            "fetched_pages": sum(row.get("fetch_status") == "FETCHED" for row in pages),
            "attempted_pages": len(pages),
            "fetch_failures": len(fetch_failures),
            "fetch_failure_records": fetch_failures,
            "unique_discovered_urls": len(discovered),
            "fetched_unique_urls": sum(
                row.get("fetch_status") == "FETCHED" for row in discovered
            ),
            "pending_discovered_urls": sum(
                row.get("fetch_status") == "PENDING_FRONTIER" for row in discovered
            ),
            "accepted_edges": sum(bool(row.get("accepted")) for row in edges),
            "extracted_links": len(edges),
            "duplicate_urls": duplicate_count,
            "duplicate_representations_removed": duplicate_representation_count,
            "scope_rejects": scope_reject_count,
            "cross_scope_edges": cross_scope_edge_count,
            "asset_rejects": asset_reject_count,
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "format_counts": dict(sorted(format_counts.items())),
            "document_role_counts": dict(sorted(role_counts.items())),
            "link_extraction_method_counts": dict(sorted(link_method_counts.items())),
            "max_depth": self.max_depth,
            "max_depth_reached": max_depth_reached,
            "max_pages": self.max_pages,
            "budget_exhausted": budget_exhausted,
            "frontier_stop_reason": stop_reason,
            "pending_frontier_size": len(queue),
            "depth_blocked_links": depth_blocked_count,
            "non_index_expansion_links_observed": non_index_expansion_count,
            "trust_env": self.trust_env,
            "network_fetch": not getattr(self.fetcher,'network_free',False),
            "stage_b_started": False,
            "feature_extraction_started": False,
            "feature_scope_counts": dict(sorted(Counter(
                scope
                for row in pages
                if row.get("fetch_status") == "FETCHED"
                for scope in row.get("feature_scopes") or []
            ).items())),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _json(discovery_dir / "discovery_summary.json", summary)

        formats = dict(sorted(format_counts.items()))
        surface_hints: dict[str, dict[str, Any]] = {}
        for label, pattern in {
            "function": r"(?i)function",
            "operator": r"(?i)operator",
            "datatype": r"(?i)data[-_]?types?|datatype",
        }.items():
            surface_hints[label] = {
                "fetched_pages": sorted(
                    row["url"] for row in pages
                    if re.search(pattern, str(row.get("url") or ""))
                ),
                "accepted_frontier_targets": sorted({
                    str(edge.get("target_url")) for edge in edges
                    if edge.get("accepted") and re.search(pattern, str(edge.get("target_url") or ""))
                }),
                "method": "posthoc URL-shape sanity only; not a production Feature classifier",
            }
        surface_audit = {
            "schema_version": "GenericDiscoverySurfaceAudit/v1",
            "stage": "A_SCOPED" if self.scope_mode else "A",
            "feature_extraction_run": False,
            "document_ir_run": False,
            "surface": {
                "pages_by_format": formats,
                "pages_by_document_role": dict(sorted(role_counts.items())),
                "pages_by_feature_scope": summary.get("feature_scope_counts", {}),
                "index_pages": [row["url"] for row in pages if row.get("document_role") == "DOCUMENT_INDEX"],
                "markdown_or_plaintext_index_present": any(
                    row.get("document_role") == "DOCUMENT_INDEX"
                    and row.get("detected_format") in {"MARKDOWN", "PLAIN_TEXT"}
                    for row in pages
                ),
                "link_methods": dict(sorted(link_method_counts.items())),
                "reference_surface_hints": surface_hints,
            },
            "scope": {
                "root_url": self.root_url,
                "scope_root": self.scope_root,
                "scope_mode": self.scope_mode,
                "scope_roots": list(self.scope_roots),
                "feature_scope_counts": summary.get("feature_scope_counts", {}),
                "cross_scope_edges": cross_scope_edge_count,
                "external_or_out_of_scope_rejects": scope_reject_count,
                "asset_rejects": asset_reject_count,
            },
            "corpus": {
                "page_count": corpus_manifest["page_count"],
                "manifest_hash": corpus_manifest["page_manifest_hash"],
                "ready_for_stage_b": bool(corpus_manifest["page_count"] and not fetch_failures),
                "coverage_complete": not budget_exhausted,
                "coverage_status": "PARTIAL_PAGE_BUDGET" if budget_exhausted else "FRONTIER_EXHAUSTED",
                "pending_discovered_urls": summary.get("pending_discovered_urls", 0),
            },
        }
        _json(self.output_root / "evaluation" / "discovery_surface_audit.json", surface_audit)
        if self.scope_mode:
            _json(self.output_root / "evaluation" / "corpus_summary.json", summary)
            _json(self.output_root / "evaluation" / "scope_surface_audit.json", surface_audit)

        run_manifest = {
            "schema_version": "GenericDiscoveryRunManifest/v1",
            "pipeline_version": PIPELINE_VERSION,
            "stage": "A_SCOPED_FEATURE_DOCUMENTATION_CRAWL" if self.scope_mode else "A_GENERIC_DISCOVERY_AND_CORPUS",
            "dbms": self.dbms,
            "root_url": self.root_url,
            "scope_mode": self.scope_mode,
            "scope_manifest_path": self.scope_manifest_path,
            "scope_roots": list(self.scope_roots),
            "output_root": str(self.output_root),
            "network_fetch": True,
            "trust_env": self.trust_env,
            "feature_extraction_run": False,
            "stages_not_run": [
                "DocumentIR", "Profile", "Owner", "Identity", "Type", "Evidence",
                "Semantic", "Grounding", "Sense", "Admission", "Canonical", "Frontend",
            ],
            "git": _git_snapshot(Path(__file__).resolve().parents[2]),
            "summary_path": "discovery/discovery_summary.json",
            "corpus_manifest_path": "corpus/corpus_manifest.json",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _json(self.output_root / "evaluation" / "discovery_run_manifest.json", run_manifest)
        if self.scope_mode:
            _json(self.output_root / "evaluation" / "run_manifest.json", run_manifest)
        report_path = self._write_report(summary, corpus_manifest, surface_audit, run_manifest)
        return {
            "status": "accepted" if corpus_manifest["page_count"] else "blocked",
            "summary": summary,
            "corpus": corpus_manifest,
            "surface_audit": surface_audit,
            "run_manifest": run_manifest,
            "report_path": str(report_path),
        }

    def _write_report(
        self,
        summary: Mapping[str, Any],
        corpus: Mapping[str, Any],
        surface: Mapping[str, Any],
        run_manifest: Mapping[str, Any],
    ) -> Path:
        report_path = self.report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_title = "# Generic Feature Scope Crawl — Stage A" if self.scope_mode else "# Generic Discovery Adaptation — Stage A"
        boundary = (
            "This run performs only `FeatureScopeManifest → scoped Generic Crawl → frozen feature documentation corpus`."
            if self.scope_mode else
            "This run performs only `DBMS + root URL → Generic Discovery → frozen corpus`."
        )
        decision = (
            "The frozen scoped corpus is ready for the offline Stage B contract when the recorded status is complete or partial."
        )
        lines = [
            report_title,
            "",
            f"- DBMS: `{self.dbms}`",
            f"- Root URL: `{self.root_url}`" if not self.scope_mode else "- Root input: `FeatureScopeManifest`",
            f"- Pipeline: `{PIPELINE_VERSION}`",
            f"- Git commit: `{(run_manifest.get('git') or {}).get('commit')}`",
            f"- Working tree clean: `{(run_manifest.get('git') or {}).get('working_tree_clean')}`",
            f"- Modified/untracked count at start: `{(run_manifest.get('git') or {}).get('modified_or_untracked_count')}`",
            "",
            "## Stage boundary",
            "",
            boundary,
            "Feature extraction and every downstream semantic/KB stage were not started.",
            "",
            "## Discovery result",
            "",
            f"- visited/attempted URLs: **{summary.get('visited_urls', 0)}**",
            f"- fetched corpus pages: **{summary.get('fetched_pages', 0)}**",
            f"- fetch failures: **{summary.get('fetch_failures', 0)}**",
            f"- unique discovered URLs: **{summary.get('unique_discovered_urls', 0)}**",
            f"- fetched unique URLs: **{summary.get('fetched_unique_urls', 0)}**",
            f"- pending frontier URLs retained in ledger: **{summary.get('pending_discovered_urls', 0)}**",
            f"- extracted links: **{summary.get('extracted_links', 0)}**",
            f"- accepted frontier edges: **{summary.get('accepted_edges', 0)}**",
            f"- duplicate URLs: **{summary.get('duplicate_urls', 0)}**",
            f"- duplicate representations removed: **{summary.get('duplicate_representations_removed', 0)}**",
            f"- cross-scope references: **{summary.get('cross_scope_edges', 0)}**",
            f"- scope rejects: **{summary.get('scope_rejects', 0)}**",
            f"- asset rejects: **{summary.get('asset_rejects', 0)}**",
            f"- frontier stop reason: **{summary.get('frontier_stop_reason')}**",
            "",
            "## Format and surface coverage",
            "",
            f"- formats: `{json.dumps(summary.get('format_counts', {}), ensure_ascii=False, sort_keys=True)}`",
            f"- document roles: `{json.dumps(summary.get('document_role_counts', {}), ensure_ascii=False, sort_keys=True)}`",
            f"- link extraction methods: `{json.dumps(summary.get('link_extraction_method_counts', {}), ensure_ascii=False, sort_keys=True)}`",
            f"- documentation index pages: **{len((surface.get('surface') or {}).get('index_pages', []))}**",
            f"- Markdown/plaintext index detected: **{(surface.get('surface') or {}).get('markdown_or_plaintext_index_present', False)}**",
            f"- posthoc reference-surface URL hints: `{json.dumps({key: {'fetched_pages': len(value.get('fetched_pages', [])), 'accepted_frontier_targets': len(value.get('accepted_frontier_targets', []))} for key, value in ((surface.get('surface') or {}).get('reference_surface_hints') or {}).items()}, ensure_ascii=False, sort_keys=True)}`",
            f"- feature scope page counts: `{json.dumps(summary.get('feature_scope_counts', {}), ensure_ascii=False, sort_keys=True)}`",
            "",
            "## Scope boundary" if self.scope_mode else "## Scope boundary",
            "",
            *([
                "The crawl started only from verified scope roots:",
                *[f"- `{root}`" for root in self.scope_roots],
                "Cross-scope links are retained with provenance only when the target belongs to another verified scope root.",
            ] if self.scope_mode else [
                f"Documentation scope root: `{self.scope_root}`",
            ]),
            "",
            "## Frozen corpus",
            "",
            f"- page manifest: `output/{self.output_root.name}/corpus/page_manifest.jsonl`",
            f"- raw pages: `output/{self.output_root.name}/corpus/pages/`",
            f"- page count: **{corpus.get('page_count', 0)}**",
            f"- manifest hash: `{corpus.get('page_manifest_hash')}`",
            "",
            "## Generic policy",
            "",
            "- URL resolution uses standard `urljoin` for absolute, root-relative, and relative links.",
            "- Existing normalized URL, same-origin/documentation-scope, fragment/query, duplicate, and binary-asset policy is preserved.",
            "- A `DOCUMENT_INDEX` is a crawl surface role only; it is not a Feature page and no Feature Type is assigned in Stage A.",
            "- No DBMS-specific selector, page allowlist, feature-name rule, inventory lookup, or LLM was used.",
            "",
            "## Stage B contract",
            "",
            "Stage B may consume `corpus/page_manifest.jsonl`, `corpus/corpus_manifest.json`, and the raw files under `corpus/pages/`.",
            "It must start from the frozen corpus and must not repeat network Discovery unless explicitly requested as a new Stage A run.",
            "",
            "## Decision",
            "",
            f"- corpus ready for Stage B: **{(surface.get('corpus') or {}).get('ready_for_stage_b', False)}**",
            f"- corpus coverage status: **{(surface.get('corpus') or {}).get('coverage_status', 'UNKNOWN')}**",
            f"- decision: {decision}",
            "- next action: `FEATURE_CRAWL_FROM_SCOPED_FROZEN_CORPUS`; no downstream stage was run.",
            "- no Feature Crawl was executed.",
        ]
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path


def run_generic_discovery(
    *,
    dbms: str,
    root_url: str,
    output_root: str | Path,
    max_depth: int = 3,
    max_pages: int | None = None,
    timeout: int = 30,
    polite_delay: float = 0.0,
    trust_env: bool | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    return GenericDiscovery(
        dbms=dbms,
        root_url=root_url,
        output_root=output_root,
        max_depth=max_depth,
        max_pages=max_pages,
        timeout=timeout,
        polite_delay=polite_delay,
        trust_env=trust_env,
        report_path=report_path,
    ).run()


def run_scoped_discovery(
    *,
    dbms: str,
    scope_manifest: str | Path | Mapping[str, Any],
    output_root: str | Path,
    max_depth: int = 3,
    max_pages: int | None = None,
    timeout: int = 30,
    polite_delay: float = 0.0,
    trust_env: bool | None = None,
    report_path: str | Path | None = None,
    fetcher: Any = None,
) -> dict[str, Any]:
    """Crawl only the verified roots in a FeatureScopeManifest."""

    return GenericDiscovery(
        dbms=dbms,
        root_url=None,
        scope_manifest=scope_manifest,
        fetcher=fetcher,
        output_root=output_root,
        max_depth=max_depth,
        max_pages=max_pages,
        timeout=timeout,
        polite_delay=polite_delay,
        trust_env=trust_env,
        report_path=report_path,
    ).run()
