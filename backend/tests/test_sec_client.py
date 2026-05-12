import httpx
import pytest

from app.core import Settings
from app.services import (
    SecClient,
    SecRateLimiter,
    SecRequestError,
    SecResponseError,
    get_global_sec_rate_limiter,
)


class FakeRateLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


def make_settings() -> Settings:
    return Settings(
        sec_user_agent="Equity Research Copilot test@example.com",
        sec_rate_limit_per_second=10,
    )


def make_http_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sec_client_adds_user_agent_and_builds_relative_urls() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers["User-Agent"]
        return httpx.Response(200, json={"ok": True})

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=FakeRateLimiter(),
    )

    assert client.get_json("/submissions/CIK0000320193.json") == {"ok": True}
    assert seen["url"] == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert seen["user_agent"] == "Equity Research Copilot test@example.com"


def test_sec_client_requires_user_agent() -> None:
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT must be configured"):
        SecClient(
            settings=Settings(sec_user_agent=" "),
            http_client=make_http_client(lambda request: httpx.Response(200, json={})),
            rate_limiter=FakeRateLimiter(),
        )


def test_sec_rate_limiter_sleeps_before_rapid_requests() -> None:
    sleeps: list[float] = []
    limiter = SecRateLimiter(
        requests_per_second=2,
        clock=lambda: 0.0,
        sleeper=sleeps.append,
    )

    limiter.wait()
    limiter.wait()

    assert sleeps == [0.5]


def test_global_sec_rate_limiter_is_shared_between_clients() -> None:
    settings = Settings(
        sec_user_agent="Equity Research Copilot test@example.com",
        sec_rate_limit_per_second=7,
    )

    first_client = SecClient(
        settings=settings,
        http_client=make_http_client(lambda request: httpx.Response(200, json={})),
    )
    second_client = SecClient(
        settings=settings,
        http_client=make_http_client(lambda request: httpx.Response(200, json={})),
    )

    assert first_client._rate_limiter is second_client._rate_limiter
    assert first_client._rate_limiter is get_global_sec_rate_limiter(7)


def test_sec_client_retries_500_response_then_returns_json() -> None:
    calls = 0
    sleeps: list[float] = []
    rate_limiter = FakeRateLimiter()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(200, json={"ok": True})

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=rate_limiter,
        sleeper=sleeps.append,
        backoff_seconds=0.25,
    )

    assert client.get_json("https://data.sec.gov/example.json") == {"ok": True}
    assert calls == 2
    assert rate_limiter.calls == 2
    assert sleeps == [0.25]


def test_sec_client_retries_429_response_with_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json={"ok": True})

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=FakeRateLimiter(),
        sleeper=sleeps.append,
    )

    assert client.get_json("/example.json") == {"ok": True}
    assert calls == 2
    assert sleeps == [2.0]


def test_sec_client_retries_timeout_then_returns_json() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.TimeoutException("timed out", request=request)
        return httpx.Response(200, json={"ok": True})

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=FakeRateLimiter(),
        sleeper=sleeps.append,
        backoff_seconds=0.5,
    )

    assert client.get_json("/example.json") == {"ok": True}
    assert calls == 2
    assert sleeps == [0.5]


def test_sec_client_does_not_retry_non_retryable_404() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "not found"})

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=FakeRateLimiter(),
        sleeper=sleeps.append,
    )

    with pytest.raises(SecResponseError) as exc_info:
        client.get_json("/missing.json")

    assert calls == 1
    assert sleeps == []
    assert exc_info.value.status_code == 404


def test_sec_client_raises_last_retryable_error_after_attempts_exhausted() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=FakeRateLimiter(),
        sleeper=sleeps.append,
        max_attempts=2,
        backoff_seconds=0.1,
    )

    with pytest.raises(SecResponseError) as exc_info:
        client.get_json("/temporarily-unavailable.json")

    assert calls == 2
    assert sleeps == [0.1]
    assert exc_info.value.status_code == 503


def test_sec_client_raises_request_error_after_timeout_attempts_exhausted() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("timed out", request=request)

    client = SecClient(
        settings=make_settings(),
        http_client=make_http_client(handler),
        rate_limiter=FakeRateLimiter(),
        sleeper=lambda seconds: None,
        max_attempts=2,
    )

    with pytest.raises(SecRequestError):
        client.get_json("/timeout.json")

    assert calls == 2
