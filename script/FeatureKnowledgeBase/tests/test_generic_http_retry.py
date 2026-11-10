import pytest
import requests

from agents.discovery import http_transport as transport


class Response:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.url = 'https://example.test/docs/'
        self.content = b'<html>official</html>'
        self.encoding = 'utf-8'
        self.headers = {'Content-Type': 'text/html'}
        if retry_after is not None:
            self.headers['Retry-After'] = retry_after
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


class Session:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.trust_env = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def delays(monkeypatch):
    values = []
    monkeypatch.setattr(transport.time, 'sleep', values.append)
    return values


def fetch(session, **kwargs):
    return transport.fetch_with_negotiation(session, 'https://example.test/docs/',
        timeout=30, caller_user_agent='FeatureCrawler/Test', **kwargs)


@pytest.mark.parametrize('error', [requests.ConnectionError('RemoteDisconnected original'),
    requests.ConnectTimeout('connect timeout'), requests.ReadTimeout('read timeout'),
    requests.exceptions.ChunkedEncodingError('broken response')])
def test_transient_failure_recovers_without_changing_request(error, delays):
    session = Session([error, Response(200)])
    result = fetch(session)
    assert result['ok'] and result['attempt_count'] == 2
    assert result['attempts'][0]['error_type'] == type(error).__name__
    assert result['attempts'][0]['error_message'] == str(error)
    assert session.calls[0] == session.calls[1]
    assert session.trust_env is False
    assert delays == [1]


def test_exhaustion_preserves_last_original_error_and_history(delays):
    session = Session([requests.ConnectionError('first'), requests.ReadTimeout('second'),
                       requests.ConnectionError('last')])
    result = fetch(session)
    assert not result['ok'] and result['attempt_count'] == 3
    assert result['error_type'] == 'ConnectionError' and result['error_message'] == 'last'
    assert [a['error_message'] for a in result['attempts']] == ['first', 'second', 'last']
    assert delays == [1, 2]


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_retryable_http_status_recovers_and_closes_response(status, delays):
    response = Response(status)
    result = fetch(Session([response, Response(200)]))
    assert result['ok'] and response.closed
    assert result['attempts'][0]['http_status'] == status
    assert delays == [1]


@pytest.mark.parametrize('status', [400, 401, 403, 404, 410])
def test_permanent_http_status_is_not_retried(status, delays):
    session = Session([Response(status)])
    result = fetch(session)
    assert not result['ok'] and len(session.calls) == 1 and delays == []


@pytest.mark.parametrize('error', [requests.exceptions.SSLError('certificate error'),
    requests.exceptions.ProxyError('proxy configuration'), ValueError('invalid'),
    AssertionError('offline guard')])
def test_configuration_and_programming_errors_are_not_retried(error, delays):
    result = fetch(Session([error]))
    assert not result['ok'] and result['attempt_count'] == 1 and delays == []
    assert result['error_type'] == type(error).__name__


def test_retry_budget_is_shared_across_406_profiles(delays):
    session = Session([Response(503), Response(406), requests.ConnectionError('reset'),
                       Response(406), Response(503)])
    result = fetch(session)
    assert not result['ok'] and result['http_status'] == 503
    assert result['attempt_count'] == len(session.calls) == 5
    assert [a['request_profile'] for a in result['attempts']] == [
        'PROFILE_DEFAULT', 'PROFILE_DEFAULT', 'PROFILE_BROAD_DOCUMENT',
        'PROFILE_BROAD_DOCUMENT', 'PROFILE_ANY_REPRESENTATION']
    assert [a['attempt_index'] for a in result['attempts']] == [1, 2, 3, 4, 5]
    assert delays == [1, 2]


@pytest.mark.parametrize('header,expected', [('5', [5]), ('invalid', [1])])
def test_retry_after_delay(header, expected, delays):
    result = fetch(Session([Response(429, header), Response(200)]))
    assert result['ok'] and delays == expected


def test_long_retry_after_returns_real_failure_without_retrying_early(delays):
    result = fetch(Session([Response(429, '120')]))
    assert not result['ok'] and result['http_status'] == 429 and delays == []


def test_retry_after_http_date(delays, monkeypatch):
    from datetime import datetime, timezone
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 4, 0, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(transport, 'datetime', Clock)
    result = fetch(Session([Response(503, 'Wed, 04 Nov 2026 00:00:07 GMT'), Response(200)]))
    assert result['ok'] and delays == [7]


def test_retries_can_be_disabled(delays):
    result = fetch(Session([requests.ConnectionError('once')]), max_retries=0)
    assert result['attempt_count'] == 1 and delays == []


def test_zero_delay_and_profile_limit_remain_bounded(delays):
    result = fetch(Session([Response(503), Response(406)]), max_attempts=1, retry_backoff=0)
    assert result['attempt_count'] == 2 and result['http_status'] == 406
    assert delays == [0]
