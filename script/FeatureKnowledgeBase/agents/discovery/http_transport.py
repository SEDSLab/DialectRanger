"""Generic HTTP transport profiles for official documentation discovery.

The transport layer deliberately knows nothing about a DBMS, host, or feature.
The first request preserves the historical Generic request contract.  A small,
bounded representation-negotiation ladder is used only when the server returns
HTTP 406 (Not Acceptable). Transient GET failures have a separate,
bounded retry budget shared across negotiation profiles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import time
from typing import Any

import requests


DEFAULT_ACCEPT = "text/html,text/markdown,text/plain,application/xhtml+xml,*/*;q=0.2"
BROAD_DOCUMENT_ACCEPT = (
    "text/html, application/xhtml+xml, text/markdown, text/plain, "
    "application/xml;q=0.9, */*;q=0.8"
)


@dataclass(frozen=True)
class GenericFetchProfile:
    """A caller-independent request profile used by the Generic fetcher."""

    name: str
    accept: str
    use_caller_user_agent: bool

    def headers(self, caller_user_agent: str | None) -> dict[str, str]:
        headers = {"Accept": self.accept}
        if self.use_caller_user_agent and caller_user_agent:
            headers["User-Agent"] = caller_user_agent
        return headers


PROFILE_DEFAULT = GenericFetchProfile(
    name="PROFILE_DEFAULT",
    accept=DEFAULT_ACCEPT,
    use_caller_user_agent=True,
)
PROFILE_BROAD_DOCUMENT = GenericFetchProfile(
    name="PROFILE_BROAD_DOCUMENT",
    accept=BROAD_DOCUMENT_ACCEPT,
    # Some documentation servers reject application-specific User-Agent values.
    # Omitting it lets requests use its ordinary transport default without
    # pretending to be a browser or naming a particular documentation site.
    use_caller_user_agent=False,
)
PROFILE_ANY_REPRESENTATION = GenericFetchProfile(
    name="PROFILE_ANY_REPRESENTATION",
    accept="*/*",
    use_caller_user_agent=False,
)

GENERIC_FETCH_PROFILES = (
    PROFILE_DEFAULT,
    PROFILE_BROAD_DOCUMENT,
    PROFILE_ANY_REPRESENTATION,
)
NEGOTIATION_FALLBACK_STATUS_CODES = frozenset({406})


def _response_content_type(response: Any) -> str:
    return str(getattr(response, "headers", {}).get("content-type", "") or "")


def _response_url(response: Any, requested_url: str) -> str:
    return str(getattr(response, "url", None) or requested_url)


def _attempt_record(
    *,
    index: int,
    profile: GenericFetchProfile,
    headers: dict[str, str],
    response: Any | None = None,
    exception: BaseException | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    status = getattr(response, "status_code", None) if response is not None else None
    record: dict[str, Any] = {
        "attempt_index": index,
        "request_profile": profile.name,
        "request_headers": dict(headers),
        "fallback_reason": fallback_reason,
        "http_status": status,
        "content_type": _response_content_type(response) if response is not None else "",
        "final_url": _response_url(response, "") if response is not None else None,
        "body_length": len(getattr(response, "content", b"") or b"") if response is not None else 0,
    }
    if exception is not None:
        record.update({
            "error_type": type(exception).__name__,
            "error_message": str(exception),
        })
    return record


def _failure_result(
    *,
    requested_url: str,
    profile: GenericFetchProfile,
    attempt_index: int,
    attempts: list[dict[str, Any]],
    transport_events: list[dict[str, Any]],
    response: Any | None = None,
    exception: BaseException | None = None,
) -> dict[str, Any]:
    http_status = getattr(response, "status_code", None) if response is not None else None
    if http_status is None and exception is not None:
        http_status = getattr(getattr(exception, "response", None), "status_code", None)
    error = exception
    if error is None and response is not None:
        try:
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - requests supplies this path
            error = exc
    return {
        "ok": False,
        "status": "FETCH_FAILED",
        "http_status": http_status,
        "final_url": _response_url(response, requested_url) if response is not None else requested_url,
        "raw_final_url": _response_url(response, requested_url) if response is not None else requested_url,
        "content_type": _response_content_type(response) if response is not None else "",
        "raw": b"",
        "encoding": getattr(response, "encoding", None) or "utf-8",
        "error_type": type(error).__name__ if error is not None else None,
        "error_message": str(error) if error is not None else None,
        "request_profile": profile.name,
        "selected_profile": profile.name,
        "attempt_index": attempt_index,
        "attempt_count": len(attempts),
        "fallback_reason": attempts[-1].get("fallback_reason") if attempts else None,
        "attempts": attempts,
        "transport_events": transport_events,
    }


RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def _transient_exception(exc):
    # Invalid TLS/proxy configuration is not repaired by repeating a request.
    if isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ProxyError)):
        return False
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError))


def _retry_delay(response, retries_used, backoff, limit):
    delay = min(limit, backoff * (2 ** retries_used))
    headers = getattr(response, 'headers', {}) or {}
    value = headers.get('Retry-After')
    if value is None:
        return delay
    try:
        seconds = float(value) if str(value).strip().isdigit() else (
            parsedate_to_datetime(str(value)) - datetime.now(timezone.utc)).total_seconds()
        if not math.isfinite(seconds):
            return delay
    except (ValueError, TypeError, OverflowError):
        return delay
    # Do not retry earlier than a server's long Retry-After. Return the real
    # failure instead of blocking the crawler beyond its bounded wait policy.
    return None if seconds > limit else max(delay, seconds, 0)


def _close_response(response):
    close = getattr(response, 'close', None)
    if close is not None:
        close()


def fetch_with_negotiation(
    session: requests.Session,
    url: str,
    *,
    timeout: int | float,
    caller_user_agent: str | None,
    allow_redirects: bool = True,
    max_attempts: int = 3,
    max_retries: int = 2,
    retry_backoff: float = 1.0,
    max_retry_delay: float = 30.0,
) -> dict[str, Any]:
    """GET with up to three 406 profiles and two shared transient retries.

    max_attempts retains its existing meaning: negotiation profile count.
    Total session.get calls cannot exceed profile_count + retry_budget.
    Retries keep the same URL, headers, timeout, TLS and proxy configuration.
    """
    profile_count = max(1, min(int(max_attempts), len(GENERIC_FETCH_PROFILES)))
    retry_budget = max(0, min(int(max_retries), 5))
    backoff, delay_limit = float(retry_backoff), float(max_retry_delay)
    if not all(math.isfinite(v) and v >= 0 for v in (backoff, delay_limit)) or delay_limit > 60:
        raise ValueError('Retry delays must be finite, nonnegative and limited to 60 seconds')
    attempts: list[dict[str, Any]] = []
    transport_events: list[dict[str, Any]] = []
    retries_used = 0
    profile_index = 0
    fallback_reason = None
    while profile_index < profile_count:
        profile = GENERIC_FETCH_PROFILES[profile_index]
        headers = profile.headers(caller_user_agent)
        response = None
        error = None
        index = len(attempts) + 1
        try:
            response = session.get(url, headers=headers, timeout=timeout,
                                   allow_redirects=allow_redirects)
        except Exception as exc:
            error = exc
        attempts.append(_attempt_record(index=index, profile=profile, headers=headers,
            response=response, exception=error, fallback_reason=fallback_reason))
        if response is not None and _response_url(response, url) != url:
            transport_events.append({'event': 'REDIRECT', 'attempt_index': index,
                'request_profile': profile.name, 'requested_url': url,
                'final_url': _response_url(response, url)})
        status = getattr(response, 'status_code', None)
        if error is None and status in NEGOTIATION_FALLBACK_STATUS_CODES and profile_index + 1 < profile_count:
            transport_events.append({'event': 'CONTENT_NEGOTIATION_FALLBACK', 'attempt_index': index,
                'from_profile': profile.name, 'to_profile': GENERIC_FETCH_PROFILES[profile_index + 1].name,
                'reason': 'HTTP_406', 'http_status': status})
            _close_response(response)
            profile_index += 1
            fallback_reason = 'HTTP_406'
            continue
        retryable = _transient_exception(error) if error is not None else status in RETRYABLE_STATUS_CODES
        if retryable and retries_used < retry_budget:
            delay = _retry_delay(response, retries_used, backoff, delay_limit)
            if delay is not None:
                reason = type(error).__name__ if error is not None else 'HTTP_' + str(status)
                transport_events.append({'event': 'TRANSIENT_RETRY', 'attempt_index': index,
                    'retry_index': retries_used + 1, 'request_profile': profile.name,
                    'reason': reason, 'http_status': status, 'delay_seconds': delay,
                    'error_type': type(error).__name__ if error is not None else None,
                    'error_message': str(error) if error is not None else None})
                _close_response(response)
                retries_used += 1
                fallback_reason = reason
                time.sleep(delay)
                continue
        if error is None:
            try:
                response.raise_for_status()
            except Exception as exc:
                error = exc
        if error is not None:
            return _failure_result(requested_url=url, profile=profile, attempt_index=index,
                attempts=attempts, transport_events=transport_events, response=response, exception=error)
        return {
            'ok': True, 'status': 'FETCHED', 'http_status': status,
            'final_url': _response_url(response, url), 'raw_final_url': _response_url(response, url),
            'content_type': _response_content_type(response), 'raw': bytes(response.content or b''),
            'encoding': getattr(response, 'encoding', None) or 'utf-8',
            'error_type': None, 'error_message': None, 'request_profile': profile.name,
            'selected_profile': profile.name, 'attempt_index': index, 'attempt_count': len(attempts),
            'fallback_reason': fallback_reason, 'attempts': attempts, 'transport_events': transport_events,
        }


__all__ = [
    "BROAD_DOCUMENT_ACCEPT",
    "DEFAULT_ACCEPT",
    "GENERIC_FETCH_PROFILES",
    "GenericFetchProfile",
    "NEGOTIATION_FALLBACK_STATUS_CODES",
    "PROFILE_ANY_REPRESENTATION",
    "PROFILE_BROAD_DOCUMENT",
    "PROFILE_DEFAULT",
    "fetch_with_negotiation",
]
