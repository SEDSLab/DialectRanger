"""Generic official Feature documentation scope resolution.

This module deliberately resolves *documentation roots*, not Feature names.
It uses DBMS name only as a search query and as an official-domain verification
signal.  The resulting manifest is the boundary between network discovery and
the offline, format-aware scoped crawl.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, unquote

import requests

from .generic_stage import (
    _HTMLLinks,
    _clean_anchor,
    _normalize_url,
    detect_document_format,
    extract_document_links,
)
from .http_transport import fetch_with_negotiation


SCOPE_TYPES = ("FUNCTION", "OPERATOR", "DATATYPE")
SCHEMA_VERSION = "feature-scope-manifest/v1"
TRACE_SCHEMA_VERSION = "feature-scope-discovery-trace/v1"
SUMMARY_SCHEMA_VERSION = "feature-scope-summary/v1"

_FALSE_ENV_VALUES = {"0", "false", "no", "off"}
_THIRD_PARTY_HOST_MARKERS = {
    "google.", "bing.", "duckduckgo.", "yahoo.", "github.",
    "gitlab.", "stackoverflow.", "stackexchange.", "medium.",
    "wikipedia.", "w3schools.", "tutorialspoint.", "readthedocs.",
    "fossies.", "reddit.", "quora.", "dev.to", "substack.",
}

_FAMILY_TERMS: dict[str, tuple[str, ...]] = {
    "FUNCTION": (
        "function", "functions", "aggregate-function", "aggregate-functions",
        "scalar-function", "scalar-functions", "window-function", "window-functions",
        "table-function", "table-functions", "routine", "routines",
    ),
    "OPERATOR": ("operator", "operators"),
    "DATATYPE": (
        "data-type", "data-types", "datatype", "datatypes", "type-reference",
        "type-system", "types",
    ),
}

_FAMILY_ROOT_PATH_TERMS: dict[str, tuple[str, ...]] = {
    "FUNCTION": ("functions", "function"),
    "OPERATOR": ("operators", "operator"),
    "DATATYPE": ("data-types", "datatypes", "types"),
}


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", (value or "").casefold()) if token]


def _compact(value: str) -> str:
    return "".join(_tokens(value))


def _family_query_terms(family: str) -> tuple[str, ...]:
    return _FAMILY_TERMS[family]


def _family_signals(family: str, url: str, title: str, anchor: str = "") -> list[str]:
    haystack = " ".join((url, title, anchor)).casefold()
    signals: list[str] = []
    for term in _family_query_terms(family):
        variants = {term, term.replace("-", " ")}
        if any(variant in haystack for variant in variants):
            signals.append(f"family_term:{term}")
    return sorted(set(signals))


def _path_segments(url: str) -> list[str]:
    return [segment.casefold() for segment in urlparse(url).path.split("/") if segment]


def _path_segment_stem(segment: str) -> str:
    """Return a comparable path segment without a document extension."""

    value = str(segment or "").casefold().strip()
    return re.sub(r"\.(?:html?|md|txt|xml)$", "", value)


def _category_path_signal(family: str, url: str) -> bool:
    segments = [_path_segment_stem(segment) for segment in _path_segments(url)]
    terms = set(_family_query_terms(family))
    return any(segment in terms for segment in segments)


def _candidate_priority(url: str, anchor: str = "") -> int:
    """Prioritize documentation indexes without assuming a DBMS URL layout."""

    haystack = " ".join((url, anchor)).casefold()
    path = urlparse(url).path.casefold()
    if re.search(r"/_llms/en(?:/|\.|$)", path):
        return 0
    if re.search(r"^/docs/[a-z]{2}(?:-[a-z]{2})?(?:/|$)", path):
        return 7
    if "/_llms/" in path:
        return 6
    if "sitemap" in haystack:
        return 1
    if "llms" in haystack:
        return 0
    if re.search(
        r"/reference/[^/]+(?:/(?:index|home)(?:\.(?:html?|md|txt))?)?$",
        path,
    ):
        return 0
    if "/reference" in path or "reference" in anchor.casefold():
        return 1
    if path.rstrip("/").endswith(("/index", "/index.html", "/index.md", "/home", "/home.html", "/home.md")):
        return 2
    if "/docs" in path or "/documentation" in path or "/manual" in path:
        return 3
    # Localized documentation is still eligible, but should not outrank the
    # primary-language/index surfaces when a site exposes both.
    if re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?)(?:/|$)", path):
        return 6
    return 4


def _sitemap_navigation_links(text: str) -> list[dict[str, str]]:
    """Extract generic sitemap locations without treating them as identities."""

    links: list[dict[str, str]] = []
    for match in re.finditer(r"<loc\b[^>]*>\s*(.*?)\s*</loc\s*>", text or "", re.I | re.S):
        target = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        if target.startswith(("http://", "https://")):
            links.append({
                "raw_target": target,
                "anchor_text": "",
                "extraction_method": "sitemap_loc",
            })
    return links


def _conventional_documentation_urls(host: str) -> tuple[str, ...]:
    """Probe a small generic set of documentation entry conventions."""

    base = f"https://{host}".rstrip("/")
    return (
        f"{base}/docs",
        f"{base}/documentation",
        f"{base}/doc",
        f"{base}/manual",
    )


def _conventional_scope_urls(documentation_url: str, family: str) -> tuple[str, ...]:
    """Build generic reference-category probes below a discovered docs root."""

    base = documentation_url.rstrip("/") + "/"
    urls: list[str] = []
    for term in _FAMILY_ROOT_PATH_TERMS[family]:
        category = f"reference/{term}"
        for suffix in ("", "/index", ".md", "/index.md", ".txt"):
            urls.append(urljoin(base, category + suffix))
    return tuple(dict.fromkeys(urls))


def _documentation_navigation_signal(url: str, anchor: str) -> bool:
    haystack = " ".join((url, anchor)).casefold()
    return any(term in haystack for term in (
        "/docs", "/doc/", "documentation", "reference", "manual", "sql reference", "llms", "sitemap",
    ))


def _unwrap_search_href(href: str) -> str:
    href = html.unescape((href or "").strip())
    if href.startswith("/"):
        # Relative links on a search result page are usually navigation,
        # image, or pagination controls.  Only unwrap them when the search
        # engine embeds an actual outbound URL query parameter.
        parsed_relative = urlparse(href)
        relative_query = parse_qs(parsed_relative.query)
        for key in ("uddg", "url", "q"):
            values = relative_query.get(key)
            if values and values[0].startswith(("http://", "https://")):
                return unquote(values[0])
        return ""
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    for key in ("uddg", "url", "q"):
        values = query.get(key)
        if values:
            candidate = unquote(values[0])
            if candidate.startswith(("http://", "https://")):
                return candidate
    return href


class _SearchResultParser(_HTMLLinks):
    """HTML anchor parser with title extraction for search result pages."""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "title":
            self._in_title = True
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False
        super().handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        super().handle_data(data)


def parse_search_results(text: str, provider: str = "unknown") -> list[dict[str, str]]:
    parser = _SearchResultParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        url = _unwrap_search_href(link.get("raw_target", ""))
        if not url.startswith(("http://", "https://")):
            continue
        normalized = _normalize_url(url)
        if not normalized or normalized in seen:
            continue
        host = (urlparse(normalized).hostname or "").casefold()
        if any(marker in host for marker in _THIRD_PARTY_HOST_MARKERS):
            # Search result pages themselves and known third-party sources are
            # useful diagnostics but never become production scope seeds.
            continue
        seen.add(normalized)
        results.append({
            "url": normalized,
            "title": _clean_anchor(link.get("anchor_text", "")),
            "provider": provider,
        })
    return results


def _official_host_match(dbms: str, url: str, title: str) -> tuple[bool, list[str]]:
    host = (urlparse(url).hostname or "").casefold()
    compact_dbms = _compact(dbms)
    host_compact = _compact(host)
    title_compact = _compact(title)
    reasons: list[str] = []
    if not host or any(marker in host for marker in _THIRD_PARTY_HOST_MARKERS):
        return False, ["known_non_official_host"]
    if compact_dbms and compact_dbms in host_compact:
        reasons.append("dbms_token_in_official_hostname")
    if compact_dbms and compact_dbms in title_compact:
        reasons.append("dbms_token_in_page_title")
    if not reasons:
        return False, ["no_official_domain_signal"]
    return True, reasons


def _provided_project_source(dbms: str, url: str, docs_url: str) -> bool:
    """An explicit input is a trust boundary, not a global host allowlist.

    Shared hosting is accepted only inside the caller's exact project namespace;
    a DBMS token in an arbitrary URL alone is not official-source verification.
    """
    seed=urlparse(docs_url or '');target=urlparse(url)
    host=(seed.hostname or '').casefold()
    if not host or any(marker in host for marker in _THIRD_PARTY_HOST_MARKERS):return False
    if seed.scheme!='https' or target.scheme!='https' or seed.netloc.casefold()!=target.netloc.casefold():return False
    parts=[unquote(p).casefold() for p in seed.path.split('/') if p]
    token=_compact(dbms)
    indexes=[i for i,p in enumerate(parts) if _compact(p)==token]
    if not token or not indexes:return False
    prefix=parts[:indexes[0]+1]
    actual=[unquote(p).casefold() for p in target.path.split('/') if p]
    if any(p in {'.','..'} for p in actual+parts):return False
    return actual[:len(prefix)]==prefix


def _official_host_score(dbms: str, host: str) -> int:
    """Rank branded hosts so search mirrors do not consume the resolver queue."""

    labels = [label for label in (host or "").casefold().split(".") if label]
    compact_dbms = _compact(dbms)
    score = 0
    for index, label in enumerate(labels):
        if compact_dbms and compact_dbms == _compact(label):
            score += 100 if index == 0 else 80
        elif compact_dbms and compact_dbms in _compact(label):
            score += 45
    if len(labels) == 2:
        score += 25
    if len(labels) >= 3 and labels[-2] in {"ac", "co", "com", "org", "net"} and len(labels[-1]) == 2:
        score -= 25
    if labels and labels[0] == "www":
        score -= 5
    return score


def _preferred_official_hosts(dbms: str, hosts: Iterable[str]) -> list[str]:
    unique = sorted({str(host).casefold() for host in hosts if host})
    ranked = sorted(unique, key=lambda host: (-_official_host_score(dbms, host), host))
    if not ranked:
        return []
    best_score = _official_host_score(dbms, ranked[0])
    # Keep same-score hosts (for example www + apex) but drop lower-ranked
    # mirrors and search-result domains.
    return [host for host in ranked if _official_host_score(dbms, host) >= best_score - 5]


def _page_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if match:
        return _clean_anchor(re.sub(r"<[^>]+>", " ", html.unescape(match.group(1))))
    heading = re.search(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", text)
    return _clean_anchor(heading.group(1)) if heading else ""


def _reference_chapter_evidence(text: str, family: str, title: str) -> int:
    """Count bounded reference structures, independent of URL naming layout.

    This establishes a crawl scope, not individual Feature identity/type truth.
    """
    if re.search(r'\b(?:examples?|tutorial|usage|programming|API|SDK|migration|configuration)\b',title,re.I):return 0
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(text or '', 'lxml')
    for chrome in soup.find_all(['nav','aside','footer','header']):chrome.decompose()
    headings=[h for h in soup.find_all(['h1','h2','h3']) if _family_signals(family,h.get_text(' ',strip=True),'')]
    if not _family_signals(family,title,'') and not headings:return 0
    regions=[soup.body or soup] if _family_signals(family,title,'') else [next(
        (p for p in h.parents if getattr(p,'name',None) in {'section','article','div','body'}
         and len(p.find_all(['code','dt','table']))>=3),h.parent) for h in headings]
    seen=set()
    for region in regions:
        # Reference indexes can be link + description list rows with no code
        # signature at all. They establish crawl scope, never Feature truth.
        suffix={'FUNCTION':'function','DATATYPE':'(?:data type|datatype)','OPERATOR':'operator'}[family]
        for item in region.find_all(['li','dt']):
            link=item.find('a',href=True)
            if not link:continue
            label=link.get_text(' ',strip=True)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*(?:\([^)]*\))?(?:\s+(?:or|[A-Z][A-Z0-9_]*))*\s+'+suffix,label):continue
            description=item.get_text(' ',strip=True)[len(label):].strip()
            if re.search(r'[a-z]{3}',description):seen.add(('linked_reference',str(item)))
        for dt in region.find_all('dt'):
            dd=dt.find_next_sibling('dd')
            label=dt.get_text(' ',strip=True)
            if dd and re.fullmatch(r'[A-Z][A-Z0-9_ ]*(?:\([^;]*\))?',label) and dd.get_text(' ',strip=True):
                seen.add(('term',str(dt)))
        if family=='FUNCTION':
            for code in region.find_all('code'):
                if code.find_parent('code'):continue
                value=code.get_text(' ',strip=True)
                if not re.fullmatch(r'[A-Z][A-Z0-9_]*\s*\([^;]*\)(?:\s*\[[^;]*\])*',value,re.S):continue
                container=code.find_parent(['p','pre'])
                following=container.find_next_sibling() if container else None
                if following is not None and following.name=='p' and re.search(r'[A-Za-z]{3}',following.get_text(' ',strip=True)):
                    seen.add(('declaration',value))
        for table in region.find_all('table'):
            trs=table.find_all('tr')
            if not trs:continue
            headers=' / '.join(c.get_text(' ',strip=True) for c in trs[0].find_all(['td','th']))
            if re.search(r'\b(?:Name|Function|Operator|Type)\b',headers,re.I) and re.search(r'\b(?:Description|Meaning|Storage)\b',headers,re.I):
                for tr in trs[1:]:
                    cells=tr.find_all(['td','th'],recursive=False)
                    if len(cells)>=2 and cells[0].get_text(' ',strip=True):seen.add(('row',str(tr)))
    return len(seen)


def _reference_index_members(text: str, family: str, url: str) -> list[str]:
    """Observed index membership, not a directory-wide URL permission."""
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(text or '', 'lxml')
    for node in soup.find_all(['nav','aside','footer','header']):node.decompose()
    suffix={'FUNCTION':'function','DATATYPE':'(?:data type|datatype)','OPERATOR':'operator'}[family]
    members=set();root=urlparse(url)
    parent=root.path.rsplit('/',1)[0]+'/'
    for item in soup.find_all(['li','dt']):
        link=item.find('a',href=True)
        if not link:continue
        label=link.get_text(' ',strip=True)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*(?:\([^)]*\))?(?:\s+(?:or|[A-Z][A-Z0-9_]*))*\s+'+suffix,label):continue
        if not re.search(r'[a-z]{3}',item.get_text(' ',strip=True)[len(label):]):continue
        target=_normalize_url(urljoin(url,link['href']));parsed=urlparse(target)
        if parsed.scheme==root.scheme and parsed.netloc==root.netloc and parsed.path.startswith(parent):members.add(target)
    return sorted(members)


def _metadata_navigation_links(text: str) -> list[dict[str, str]]:
    """Read canonical/alternate documentation links from HTML head metadata."""

    links: list[dict[str, str]] = []
    tag_re = re.compile(r"<link\b([^>]+)>", re.I | re.S)
    attr_re = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", re.S)
    for match in tag_re.finditer(text):
        attrs = {name.casefold(): html.unescape(value).strip() for name, _, value in attr_re.findall(match.group(1))}
        rel = attrs.get("rel", "").casefold()
        href = attrs.get("href", "")
        if href and any(token in rel for token in ("canonical", "alternate", "documentation")):
            links.append({
                "raw_target": href,
                "anchor_text": rel,
                "extraction_method": "html_metadata_link",
            })
    return links


class ScopeResolver:
    """Resolve official documentation roots for a DBMS without feature names."""

    def __init__(
        self,
        *,
        dbms: str,
        output_root: str | Path,
        timeout: int = 30,
        trust_env: bool | None = None,
        max_search_results: int = 12,
        max_verifications: int = 80,
        search_provider: Callable[[str], list[Mapping[str, str]]] | None = None,
        docs_url: str | None = None,
        fetcher: Any = None,
        cached_urls: tuple = (),
    ) -> None:
        if not str(dbms).strip():
            raise ValueError("dbms must be non-empty")
        self.dbms = str(dbms).strip()
        self.output_root = Path(output_root).resolve()
        self.project_root = Path(__file__).resolve().parents[2]
        self.timeout = int(timeout)
        self.max_search_results = max(1, int(max_search_results))
        self.max_verifications = max(1, int(max_verifications))
        if trust_env is None:
            trust_env = os.environ.get("FEATURE_CRAWLER_HTTP_TRUST_ENV", "true").strip().casefold() not in _FALSE_ENV_VALUES
        self.trust_env = bool(trust_env)
        self._injected_search_provider = search_provider
        self.docs_url = docs_url
        self.fetcher = fetcher or fetch_with_negotiation
        self.cached_urls = cached_urls

    def _search(self, session: requests.Session, query: str) -> list[dict[str, str]]:
        if self.docs_url:
            return [{'url': u, 'title': 'Provided official documentation / frozen corpus', 'provider': 'official_root'} for u in dict.fromkeys([self.docs_url, *self.cached_urls])]
        if self._injected_search_provider is not None:
            return [dict(row) for row in self._injected_search_provider(query)]
        endpoints = (
            ("bing", "https://www.bing.com/search"),
            ("duckduckgo", "https://html.duckduckgo.com/html/"),
            ("google", "https://www.google.com/search"),
        )
        for provider, endpoint in endpoints:
            try:
                response = session.get(
                    endpoint,
                    params={"q": query, "count": self.max_search_results},
                    headers={"User-Agent": "FeatureCrawler/GenericScopeResolver"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                results = parse_search_results(response.text, provider)
                if results:
                    return results[: self.max_search_results]
            except Exception:
                continue
        return []

    def _fetch_candidate(self, session: requests.Session, url: str) -> dict[str, Any]:
        try:
            result = self.fetcher(
                session,
                url,
                timeout=self.timeout,
                caller_user_agent="FeatureCrawler/GenericScopeResolver",
                allow_redirects=True,
            )
            if not result.get("ok"):
                result.update({
                    "url": url,
                    "format": "OTHER",
                    "text": "",
                    "title": "",
                    "status_code": result.get("http_status"),
                })
                return result
            final_url = _normalize_url(str(result.get("final_url") or url))
            raw = bytes(result.get("raw") or b"")
            encoding = result.get("encoding") or "utf-8"
            text = raw.decode(encoding, errors="replace")
            detected_format = detect_document_format(
                result.get("content_type", ""), final_url, text,
            )
            title = _page_title(text)
            result.update({
                "url": final_url,
                "content_type": result.get("content_type", ""),
                "text": text,
                "format": detected_format,
                "title": title,
                "status_code": result.get("http_status"),
            })
            return result
        except Exception as exc:
            return {
                "ok": False,
                "url": url,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "status_code": getattr(getattr(exc, "response", None), "status_code", None),
            }

    def _candidate_is_scope_root(
        self, family: str, url: str, title: str, links: list[Mapping[str, str]], anchor: str,
        page_text: str = '',
    ) -> tuple[bool, list[str], str]:
        signals = _family_signals(family, url, title, anchor)
        title_has_family = bool(_family_signals(family, title, title, ""))
        path_segments = [_path_segment_stem(segment) for segment in _path_segments(url)]
        path_has_family = _category_path_signal(family, url)
        terminal = path_segments[-1] if path_segments else ""
        root_terminal = terminal in set(_family_query_terms(family)) | {"index", "home", "reference"}
        excluded_namespace = any(segment in {
            "system-tables", "table-engines", "settings", "formats", "statements", "syntax",
        } for segment in path_segments)
        same_origin_links = sum(
            bool(urlparse(str(link.get("resolved_url") or "")).netloc)
            and (urlparse(str(link.get("resolved_url") or "")).hostname or "").casefold()
            == (urlparse(url).hostname or "").casefold()
            for link in links
        )
        index_like = len(links) >= 2 and same_origin_links >= 2
        if excluded_namespace and not ("_llms" in urlparse(url).path.casefold() and root_terminal):
            return False, signals + ["excluded_documentation_namespace"], "NOT_A_SCOPE_ROOT"
        if title_has_family and (path_has_family and root_terminal) and (index_like or path_has_family):
            return True, signals + ["title_family", "index_like" if index_like else "category_path"], "REFERENCE_ROOT"
        if path_has_family and root_terminal:
            return True, signals + ["category_path_terminal"], "CATEGORY_ROOT"
        members=_reference_chapter_evidence(page_text,family,title) if page_text else 0
        if members>=3:
            return True,signals+['observed_reference_chapter','bounded_reference_structures:'+str(members)],'REFERENCE_CHAPTER'
        return False, signals, "NOT_A_SCOPE_ROOT"

    def _scope_queries(self) -> list[tuple[str, str]]:
        queries: list[tuple[str, str]] = []
        for family, display in (("FUNCTION", "functions"), ("OPERATOR", "operators"), ("DATATYPE", "data types")):
            queries.append((family, f"{self.dbms} official documentation {display} reference"))
            queries.append((family, f"{self.dbms} official docs {display}"))
        return queries

    def _load_cached_manifest(self) -> dict[str, Any] | None:
        path = self.output_root / "scope" / "feature_scope_manifest.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if value.get("schema_version") != SCHEMA_VERSION or value.get("dbms") != self.dbms:
            return None
        return value

    def resolve(self, *, reuse_cache: bool = True, report_path: str | Path | None = None) -> dict[str, Any]:
        cached = self._load_cached_manifest() if reuse_cache else None
        if cached is not None:
            summary_path = self.output_root / "scope" / "scope_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
            return {"status": "cached", "manifest": cached, "summary": summary, "output_root": str(self.output_root)}

        self.output_root.mkdir(parents=True, exist_ok=True)
        scope_dir = self.output_root / "scope"
        scope_dir.mkdir(parents=True, exist_ok=True)
        session = requests.Session()
        session.trust_env = self.trust_env
        traces: list[dict[str, Any]] = []
        search_records: list[dict[str, Any]] = []
        candidates: dict[tuple[str, str], dict[str, Any]] = {}
        fetch_failures: list[dict[str, Any]] = []
        verified_domains: dict[str, dict[str, Any]] = {}

        initial_search_records: list[tuple[str, str, list[dict[str, str]]]] = []
        for family, query in self._scope_queries():
            results = self._search(session, query)
            initial_search_records.append((family, query, results))
            search_records.append({"family": family, "query": query, "result_count": len(results)})
            traces.append({
                "trace_type": "SEARCH",
                "schema_version": TRACE_SCHEMA_VERSION,
                "family": family,
                "query": query,
                "results": results,
            })
            for rank, result in enumerate(results[: self.max_search_results], 1):
                url = _normalize_url(str(result.get("url") or ""))
                if url:
                    candidates.setdefault((family, url), {
                        "family": family,
                        "url": url,
                        "anchor": str(result.get("title") or ""),
                        "discovered_by": [{"query": query, "rank": rank, "provider": result.get("provider")}],
                    })

        # Once an official-looking domain is found, search within that domain
        # for each documentation family.  This is still DBMS-agnostic: the
        # domain is discovered from the search results, never stored in code.
        discovered_hosts: list[str] = []
        dbms_token = _compact(self.dbms)
        for _, _, results in initial_search_records:
            for result in results:
                host = (urlparse(str(result.get("url") or "")).hostname or "").casefold()
                if not host or dbms_token not in _compact(host):
                    continue
                if host not in discovered_hosts:
                    discovered_hosts.append(host)
        preferred_hosts = _preferred_official_hosts(self.dbms, discovered_hosts)
        if self.docs_url:
            preferred_hosts = [(urlparse(self.docs_url).hostname or '').casefold()]
        candidates = {
            key: value
            for key, value in candidates.items()
            if (urlparse(str(value.get("url") or "")).hostname or "").casefold() in preferred_hosts
        }
        for host in preferred_hosts[:2]:
            for family, display in (("FUNCTION", "functions"), ("OPERATOR", "operators"), ("DATATYPE", "data types")):
                query = f"site:{host} {self.dbms} {display} reference"
                results = self._search(session, query)
                search_records.append({"family": family, "query": query, "result_count": len(results)})
                traces.append({
                    "trace_type": "SEARCH",
                    "schema_version": TRACE_SCHEMA_VERSION,
                    "family": family,
                    "query": query,
                    "results": results,
                })
                for rank, result in enumerate(results[: self.max_search_results], 1):
                    url = _normalize_url(str(result.get("url") or ""))
                    result_host = (urlparse(url).hostname or "").casefold()
                    if url and result_host == host:
                        candidates.setdefault((family, url), {
                            "family": family,
                            "url": url,
                            "anchor": str(result.get("title") or ""),
                            "discovered_by": [{"query": query, "rank": rank, "provider": result.get("provider")}],
                        })
            # Search engines often return a branded home page instead of the
            # documentation entry point.  These are generic, bounded path
            # conventions; they are not DBMS-specific selectors.
            for family in SCOPE_TYPES:
                for url in _conventional_documentation_urls(host):
                    candidates.setdefault((family, url), {
                        "family": family,
                        "url": url,
                        "anchor": "documentation entry point",
                        "discovered_by": [{
                            "source_host": host,
                            "extraction_method": "conventional_documentation_path_probe",
                        }],
                    })

        scope_rows: dict[str, list[dict[str, Any]]] = {family: [] for family in SCOPE_TYPES}
        seen_scope_urls: dict[str, set[str]] = {family: set() for family in SCOPE_TYPES}
        processed: set[tuple[str, str]] = set()
        fetched_by_url: dict[str, dict[str, Any]] = {}
        family_verifications: Counter[str] = Counter()
        verification_count = 0
        candidate_queue = list(candidates.values())

        while candidate_queue and verification_count < self.max_verifications:
            # Keep one broad family from consuming the bounded verification
            # budget before the other two families get a chance to resolve.
            candidate_index = min(
                range(len(candidate_queue)),
                key=lambda index: (
                    family_verifications[str(candidate_queue[index].get("family") or "")],
                    _candidate_priority(
                        str(candidate_queue[index].get("url") or ""),
                        str(candidate_queue[index].get("anchor") or ""),
                    ),
                    index,
                ),
            )
            candidate = candidate_queue.pop(candidate_index)
            url = candidate["url"]
            family = str(candidate["family"])
            process_key = (family, url)
            if process_key in processed:
                continue
            processed.add(process_key)
            verification_count += 1
            family_verifications[family] += 1
            fetched = fetched_by_url.get(url)
            if fetched is None:
                fetched = self._fetch_candidate(session, url)
                fetched_by_url[url] = fetched
            if not fetched.get("ok"):
                optional_probe = any(
                    str(row.get("extraction_method") or "") in {
                        "conventional_documentation_path_probe",
                        "conventional_scope_path_probe",
                    }
                    for row in (candidate.get("discovered_by") or [])
                    if isinstance(row, Mapping)
                )
                if not optional_probe:
                    fetch_failures.append({"url": url, **{key: fetched.get(key) for key in ("error_type", "error_message", "status_code")}})
                traces.append({
                    "trace_type": "VERIFY",
                    "schema_version": TRACE_SCHEMA_VERSION,
                    "url": url,
                    "verified_official": False,
                    "reason_codes": ["OPTIONAL_CONVENTIONAL_PATH_MISS" if optional_probe else "FETCH_FAILED"],
                    "error_type": fetched.get("error_type"),
                    "request_profile": fetched.get("request_profile"),
                    "selected_profile": fetched.get("selected_profile"),
                    "attempt_index": fetched.get("attempt_index"),
                    "attempt_count": fetched.get("attempt_count", 0),
                    "fallback_reason": fetched.get("fallback_reason"),
                    "transport_events": fetched.get("transport_events", []),
                })
                continue
            final_url = str(fetched["url"])
            title = str(fetched.get("title") or candidate.get("anchor") or "")
            official, verification_reasons = _official_host_match(self.dbms, final_url, title)
            if not official and _provided_project_source(self.dbms,final_url,self.docs_url):
                official=True
                verification_reasons=['caller_provided_https_project_namespace']
            host = (urlparse(final_url).hostname or "").casefold()
            if official:
                verified_domains.setdefault(host, {
                    "domain": host,
                    "verified_official": True,
                    "verification_method": "source_identity_and_official_document_fetch",
                    "source_urls": [],
                })["source_urls"].append(final_url)
            detected_format = str(fetched.get("format") or "OTHER")
            page_text = str(fetched.get("text") or "")
            links = extract_document_links(page_text, detected_format)
            if detected_format == "HTML":
                links.extend(_metadata_navigation_links(page_text))
                parsed_page = urlparse(final_url)
                if parsed_page.path.rstrip("/").casefold().endswith("/docs"):
                    docs_base = final_url.rstrip("/") + "/"
                    for suffix in ("llms.txt", "sitemap.xml"):
                        links.append({
                            "raw_target": urljoin(docs_base, suffix),
                            "anchor_text": suffix,
                            "extraction_method": "generic_documentation_index_convention",
                        })
                    for target in _conventional_scope_urls(final_url, family):
                        links.append({
                            "raw_target": target,
                            "anchor_text": "reference category convention",
                            "extraction_method": "conventional_scope_path_probe",
                        })
            if urlparse(final_url).path.casefold().endswith("sitemap.xml"):
                links.extend(_sitemap_navigation_links(page_text))
            for link in links:
                try:
                    link["resolved_url"] = _normalize_url(urljoin(final_url, link.get("raw_target", "")))
                except (TypeError, ValueError):
                    link["resolved_url"] = ""
            accepted, signals, scope_kind = self._candidate_is_scope_root(
                family, final_url, title, links, str(candidate.get("anchor") or ""),page_text,
            )
            accepted = bool(accepted and official)
            reason_codes = list(verification_reasons)
            if not official:
                reason_codes.append("OFFICIAL_DOMAIN_NOT_VERIFIED")
            if not accepted:
                reason_codes.append("NOT_DIRECTORY_SCOPE_ROOT" if official else "UNVERIFIED_SCOPE_CANDIDATE")
            traces.append({
                "trace_type": "VERIFY",
                "schema_version": TRACE_SCHEMA_VERSION,
                "family": family,
                "url": url,
                "final_url": final_url,
                "page_title": title,
                "detected_format": detected_format,
                "verified_official": official,
                "verification_reasons": verification_reasons,
                "scope_candidate": accepted,
                "scope_kind": scope_kind,
                "signals": signals,
                "discovered_by": candidate.get("discovered_by", []),
                "request_profile": fetched.get("request_profile"),
                "selected_profile": fetched.get("selected_profile"),
                "attempt_index": fetched.get("attempt_index"),
                "attempt_count": fetched.get("attempt_count", 0),
                "fallback_reason": fetched.get("fallback_reason"),
                "transport_events": fetched.get("transport_events", []),
            })
            if accepted and final_url not in seen_scope_urls[family]:
                seen_scope_urls[family].add(final_url)
                scope_rows[family].append({
                    "url": final_url,
                    "title": title,
                    "scope_kind": scope_kind,
                    "official": True,
                    "verified_member_urls": _reference_index_members(page_text,family,final_url)
                        if scope_kind=='REFERENCE_CHAPTER' else [],
                    "provenance": {
                        "discovered_by": candidate.get("discovered_by", []),
                        "source_url": url,
                        "verification": verification_reasons,
                        "signals": signals,
                        "request_profile": fetched.get("request_profile"),
                        "selected_profile": fetched.get("selected_profile"),
                        "attempt_index": fetched.get("attempt_index"),
                        "attempt_count": fetched.get("attempt_count", 0),
                        "fallback_reason": fetched.get("fallback_reason"),
                        "transport_events": fetched.get("transport_events", []),
                    },
                    "selected_representation": detected_format,
                    "alternate_representations": [],
                })
            # A verified official home/docs page is a navigation seed even if
            # it is not itself a Feature scope root.  Only documentation
            # navigation and family-bearing category links enter the bounded
            # queue; individual Feature pages do not become manifest roots.
            if official:
                for link in links:
                    target = str(link.get("resolved_url") or "")
                    if not target or (family, target) in processed:
                        continue
                    target_host = (urlparse(target).hostname or "").casefold()
                    if target_host != host:
                        continue
                    anchor = str(link.get("anchor_text") or "")
                    target_signals = _family_signals(family, target, anchor, anchor)
                    family_category = bool(target_signals and _category_path_signal(family, target))
                    documentation_navigation = _documentation_navigation_signal(target, anchor)
                    if not family_category and not documentation_navigation:
                        continue
                    target_path_segments = _path_segments(target)
                    target_terminal = _path_segment_stem(target_path_segments[-1]) if target_path_segments else ""
                    if (
                        link.get("extraction_method") == "sitemap_loc"
                        and not family_category
                        and not ("/reference" in target.casefold() and target_terminal in {"index", "home"})
                    ):
                        continue
                    queued_candidate = {
                        "family": family,
                        "url": target,
                        "anchor": anchor,
                        "discovered_by": [{
                            "source_url": final_url,
                            "extraction_method": link.get("extraction_method"),
                            "anchor_text": anchor,
                            "navigation_signal": "FAMILY_CATEGORY" if family_category else "DOCUMENTATION_NAVIGATION",
                        }],
                    }
                    # Category/reference targets are more informative than
                    # generic docs navigation and must be verified before a
                    # page-budgeted queue fills with unrelated documentation.
                    if family_category or "/reference" in target.casefold() or any(
                        marker in target.casefold() for marker in ("llms", "sitemap")
                    ):
                        candidate_queue.insert(0, queued_candidate)
                    else:
                        candidate_queue.append(queued_candidate)

        reasons: list[str] = []
        missing = [family for family in SCOPE_TYPES if not scope_rows[family]]
        if missing:
            reasons.append("UNRESOLVED_REFERENCE_CATEGORY")
        if len(scope_rows["FUNCTION"]) > 1 or len(scope_rows["OPERATOR"]) > 1 or len(scope_rows["DATATYPE"]) > 1:
            reasons.append("MULTIPLE_REFERENCE_ROOTS_FOUND")
        if not missing and not fetch_failures:
            status = "COMPLETE"
            reasons.append("OFFICIAL_NAVIGATION_EXHAUSTED")
        elif not missing:
            status = "PARTIAL"
            reasons.append("SCOPE_CANDIDATE_FETCH_FAILURE")
        else:
            status = "UNCERTAIN"
        if not reasons:
            reasons.append("NO_VERIFIED_FEATURE_SCOPE")

        # The manifest contains roots only.  A title is provenance for the
        # root page; no extracted Feature identity is ever written here.
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "dbms": self.dbms,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "official_sources": sorted(verified_domains.values(), key=lambda row: row["domain"]),
            "scopes": {family: sorted(scope_rows[family], key=lambda row: row["url"]) for family in SCOPE_TYPES},
            "scope_completeness_status": status,
            "reason_codes": sorted(set(reasons)),
            "feature_identity_count_in_scope_manifest": 0,
            "resolver": {
                "method": "generic_official_search_and_navigation_verification",
                "search_queries": [record["query"] for record in search_records],
                "search_records": search_records,
                "trust_env": self.trust_env,
            },
        }
        summary = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "dbms": self.dbms,
            "scope_completeness_status": status,
            "reason_codes": sorted(set(reasons)),
            "roots_by_scope": {family: len(scope_rows[family]) for family in SCOPE_TYPES},
            "official_domains": sorted(verified_domains),
            "verified_candidates": sum(1 for row in traces if row.get("trace_type") == "VERIFY" and row.get("verified_official")),
            "rejected_candidates": sum(1 for row in traces if row.get("trace_type") == "VERIFY" and not row.get("scope_candidate")),
            "fetch_failures": len(fetch_failures),
            "feature_identity_count_in_scope_manifest": 0,
            "network_fetch": not getattr(self.fetcher,'network_free',False),
            "trust_env": self.trust_env,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _json(scope_dir / "feature_scope_manifest.json", manifest)
        _jsonl(scope_dir / "scope_discovery_trace.jsonl", traces)
        _json(scope_dir / "scope_summary.json", summary)
        report = self._write_report(manifest, summary, report_path)
        return {
            "status": "accepted" if status in {"COMPLETE", "PARTIAL"} else "blocked",
            "manifest": manifest,
            "summary": summary,
            "report_path": str(report) if report else None,
            "output_root": str(self.output_root),
        }

    def _write_report(
        self, manifest: Mapping[str, Any], summary: Mapping[str, Any], report_path: str | Path | None,
    ) -> Path:
        path = Path(report_path).resolve() if report_path else self.project_root / "reports" / "generic_feature_scope_discovery.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Generic Feature Scope Discovery",
            "",
            f"- DBMS: `{self.dbms}`",
            f"- Resolver status: **{manifest.get('scope_completeness_status')}**",
            "- Method: generic official search + official page fetch + navigation verification",
            "- Feature identity allowlist in manifest: **0**",
            "",
            "## Scope roots",
            "",
        ]
        for family in SCOPE_TYPES:
            lines.append(f"### {family}")
            rows = (manifest.get("scopes") or {}).get(family, [])
            if not rows:
                lines.append("- None verified")
            for row in rows:
                lines.append(f"- `{row.get('url')}` — {row.get('title') or 'untitled'} ({row.get('scope_kind')})")
        lines.extend([
            "",
            "## Official verification",
            "",
            "Each accepted root was fetched and its hostname/title was checked against the DBMS identity. Search results and third-party pages are never production seeds.",
            "",
            "## Completeness",
            "",
            f"- Status: **{manifest.get('scope_completeness_status')}**",
            f"- Reason codes: `{', '.join(manifest.get('reason_codes') or [])}`",
            f"- Official domains: `{', '.join(row.get('domain', '') for row in (manifest.get('official_sources') or []))}`",
            "",
            "Stage 0 resolves documentation scope only. It does not generate Feature identities and does not run Feature extraction.",
        ])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def resolve_feature_scope(
    *,
    dbms: str,
    output_root: str | Path,
    timeout: int = 30,
    trust_env: bool | None = None,
    max_verifications: int = 80,
    reuse_cache: bool = True,
    report_path: str | Path | None = None,
    docs_url: str | None = None,
    fetcher: Any = None,
    cached_urls: tuple = (),
) -> dict[str, Any]:
    return ScopeResolver(
        dbms=dbms,
        output_root=output_root,
        timeout=timeout,
        trust_env=trust_env,
        max_verifications=max_verifications,
        docs_url=docs_url, fetcher=fetcher, cached_urls=cached_urls,
    ).resolve(reuse_cache=reuse_cache, report_path=report_path)


__all__ = [
    "SCOPE_TYPES",
    "SCHEMA_VERSION",
    "ScopeResolver",
    "parse_search_results",
    "resolve_feature_scope",
]
