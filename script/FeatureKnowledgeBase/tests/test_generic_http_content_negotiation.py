from __future__ import annotations

from pathlib import Path

import requests

from agents.discovery.generic_stage import detect_document_format, extract_document_links
from agents.discovery.http_transport import (
    PROFILE_ANY_REPRESENTATION,
    PROFILE_BROAD_DOCUMENT,
    PROFILE_DEFAULT,
    fetch_with_negotiation,
)


class _Response:
    def __init__(self, url: str, status: int, body: str = "<html><body>docs</body></html>", content_type: str = "text/html") -> None:
        self.url = url
        self.status_code = status
        self.content = body.encode("utf-8")
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error",
                response=self,
            )


class _SequenceSession:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.trust_env = False

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def _run(statuses: list[int], *, body: str = "<html><body>docs</body></html>", content_type: str = "text/html") -> tuple[dict[str, object], _SequenceSession]:
    url = "https://example.test/docs/index"
    session = _SequenceSession([
        _Response(url, status, body=body, content_type=content_type)
        for status in statuses
    ])
    return fetch_with_negotiation(
        session,
        url,
        timeout=1,
        caller_user_agent="FeatureCrawler/GenericDiscovery",
    ), session


def test_case_a_default_works_without_fallback() -> None:
    result, session = _run([200])
    assert result["ok"] is True
    assert result["selected_profile"] == PROFILE_DEFAULT.name
    assert result["attempt_count"] == 1
    assert result["transport_events"] == []
    assert len(session.calls) == 1
    assert session.calls[0]["headers"]["User-Agent"] == "FeatureCrawler/GenericDiscovery"


def test_case_b_one_step_negotiation_fallback() -> None:
    result, session = _run([406, 200])
    assert result["ok"] is True
    assert result["selected_profile"] == PROFILE_BROAD_DOCUMENT.name
    assert result["attempt_count"] == 2
    assert result["fallback_reason"] == "HTTP_406"
    assert session.calls[1]["headers"] == {"Accept": PROFILE_BROAD_DOCUMENT.accept}
    assert result["transport_events"][0]["event"] == "CONTENT_NEGOTIATION_FALLBACK"


def test_case_c_final_generic_fallback() -> None:
    result, _ = _run([406, 406, 200])
    assert result["ok"] is True
    assert result["selected_profile"] == PROFILE_ANY_REPRESENTATION.name
    assert result["attempt_count"] == 3
    assert len(result["attempts"]) == 3


def test_case_d_404_does_not_fallback() -> None:
    result, session = _run([404, 200])
    assert result["ok"] is False
    assert result["http_status"] == 404
    assert result["attempt_count"] == 1
    assert len(session.calls) == 1
    assert result["transport_events"] == []


def test_case_e_403_is_not_bypassed() -> None:
    result, session = _run([403, 200])
    assert result["ok"] is False
    assert result["http_status"] == 403
    assert result["attempt_count"] == 1
    assert len(session.calls) == 1


def test_case_f_all_negotiation_attempts_retain_history() -> None:
    result, session = _run([406, 406, 406])
    assert result["ok"] is False
    assert result["http_status"] == 406
    assert result["attempt_count"] == 3
    assert [row["http_status"] for row in result["attempts"]] == [406, 406, 406]
    assert len(session.calls) == 3


def test_case_g_unsupported_content_type_still_goes_through_format_validation() -> None:
    result, _ = _run([406, 200], body="\x00\x01binary", content_type="application/octet-stream")
    assert result["ok"] is True
    detected = detect_document_format(
        str(result["content_type"]), str(result["final_url"]), bytes(result["raw"]).decode("utf-8"),
    )
    assert detected == "OTHER"
    assert extract_document_links(bytes(result["raw"]).decode("utf-8"), detected) == []

