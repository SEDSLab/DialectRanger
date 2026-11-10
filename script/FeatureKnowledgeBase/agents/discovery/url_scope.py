"""URL helpers shared by the current generic Discovery (unchanged rules)."""
from __future__ import annotations
import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port
    netloc = hostname
    if port and not ((parsed.scheme.lower() == "https" and port == 443)
                     or (parsed.scheme.lower() == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    path = posixpath.normpath(parsed.path or "/")
    if parsed.path.endswith("/") and path != "/":
        path += "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source"}
    ))
    cleaned = parsed._replace(fragment="", path=path, query=query)
    cleaned = cleaned._replace(scheme=cleaned.scheme.lower(), netloc=netloc)
    return urlunparse(cleaned)



def _is_html_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    path = parsed.path.lower()
    for ext in (".pdf", ".zip", ".gz", ".tgz", ".tar", ".bz2", ".7z", ".rar",
                ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
                ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
                ".exe", ".msi", ".dmg", ".iso", ".deb", ".rpm",
                ".mp3", ".mp4", ".wav", ".avi", ".mov"):
        if path.endswith(ext):
            return False
    return True



def _url_in_scope(url: str, docs_root: str, same_host_only: bool = True) -> bool:
    candidate = urlparse(_normalize_url(url))
    root = urlparse(_normalize_url(docs_root))
    if candidate.scheme not in ("http", "https"):
        return False
    if same_host_only and candidate.netloc != root.netloc:
        return False
    root_path = root.path.rstrip("/")
    candidate_path = candidate.path.rstrip("/")
    return not root_path or candidate_path == root_path or candidate_path.startswith(root_path + "/")



def _documentation_scope_root(entry_url: str) -> str:
    """Infer a directory scope without changing the documentation entry URL."""
    directory_like = urlparse(entry_url).path.endswith("/")
    parsed = urlparse(_normalize_url(entry_url))
    path = parsed.path or "/"
    if directory_like:
        scope_path = path.rstrip("/") + "/" if path != "/" else "/"
    elif re.search(r"\.(?:html?|xhtml)$", posixpath.basename(path), re.I):
        parent = posixpath.dirname(path)
        scope_path = (parent.rstrip("/") + "/") if parent else "/"
    else:
        scope_path = path
    return urlunparse(parsed._replace(path=scope_path, query="", fragment=""))

