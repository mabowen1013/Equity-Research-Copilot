from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core import Settings, get_required_sec_user_agent, get_settings

SEC_BASE_URL = "https://data.sec.gov"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5
SEC_FILING_ACCEPT_HEADER = "text/html,application/xhtml+xml,text/plain,*/*"

_GLOBAL_RATE_LIMITER: SecRateLimiter | None = None
_GLOBAL_RATE_LIMITER_RATE: int | None = None
_GLOBAL_RATE_LIMITER_LOCK = Lock()


class SecClientError(RuntimeError):
    """Base error for SEC client failures."""


class SecRequestError(SecClientError):
    """Raised when a request fails before a usable SEC response is returned."""


class SecResponseError(SecClientError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SecRateLimiter:
    def __init__(
        self,
        requests_per_second: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second < 1:
            raise ValueError("requests_per_second must be at least 1.")

        self._min_interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._next_request_at = 0.0
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_request_at - now)
            scheduled_request_at = now + delay
            self._next_request_at = scheduled_request_at + self._min_interval

        if delay > 0:
            self._sleeper(delay)


@dataclass(frozen=True)
class SecContentResponse:
    content: bytes
    content_type: str | None
    url: str


def get_global_sec_rate_limiter(requests_per_second: int) -> SecRateLimiter:
    global _GLOBAL_RATE_LIMITER, _GLOBAL_RATE_LIMITER_RATE

    with _GLOBAL_RATE_LIMITER_LOCK:
        if (
            _GLOBAL_RATE_LIMITER is None
            or _GLOBAL_RATE_LIMITER_RATE != requests_per_second
        ):
            _GLOBAL_RATE_LIMITER = SecRateLimiter(requests_per_second)
            _GLOBAL_RATE_LIMITER_RATE = requests_per_second

        return _GLOBAL_RATE_LIMITER


class SecClient:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
        rate_limiter: SecRateLimiter | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        base_url: str = SEC_BASE_URL,
    ) -> None:
        active_settings = settings or get_settings()

        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

        self._headers = {
            "User-Agent": get_required_sec_user_agent(active_settings),
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._rate_limiter = rate_limiter or get_global_sec_rate_limiter(
            active_settings.sec_rate_limit_per_second,
        )
        self._sleeper = sleeper
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url.rstrip("/") + "/"

    def get_json(self, url: str) -> dict[str, Any]:
        response = self._request(url)
        return self._parse_json_object(response, str(response.url))

    def get_content(
        self,
        url: str,
        *,
        accept: str = SEC_FILING_ACCEPT_HEADER,
    ) -> SecContentResponse:
        response = self._request(url, headers={"Accept": accept})
        return SecContentResponse(
            content=response.content,
            content_type=response.headers.get("Content-Type"),
            url=str(response.url),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SecClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _request(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request_url = self._build_url(url)
        last_error: SecClientError | None = None
        request_headers = {**self._headers, **(headers or {})}

        for attempt in range(1, self._max_attempts + 1):
            self._rate_limiter.wait()

            try:
                response = self._client.get(
                    request_url,
                    headers=request_headers,
                    timeout=self._timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = SecRequestError(f"SEC request failed for {request_url}: {exc}")
                self._sleep_before_retry(attempt)
                continue

            if self._is_retryable_response(response):
                last_error = SecResponseError(
                    f"SEC returned retryable status {response.status_code} for {request_url}.",
                    status_code=response.status_code,
                )
                self._sleep_before_retry(attempt, response=response)
                continue

            if response.status_code >= 400:
                raise SecResponseError(
                    f"SEC returned status {response.status_code} for {request_url}.",
                    status_code=response.status_code,
                )

            return response

        if last_error is not None:
            raise last_error

        raise SecRequestError(f"SEC request failed for {request_url}.")

    def _build_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url

        return urljoin(self._base_url, url.lstrip("/"))

    def _sleep_before_retry(
        self,
        attempt: int,
        *,
        response: httpx.Response | None = None,
    ) -> None:
        if attempt >= self._max_attempts:
            return

        retry_after = self._parse_retry_after(response) if response is not None else None
        delay = retry_after if retry_after is not None else self._backoff_seconds * attempt
        self._sleeper(delay)

    def _parse_retry_after(self, response: httpx.Response | None) -> float | None:
        if response is None:
            return None

        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return None

        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return None

    def _is_retryable_response(self, response: httpx.Response) -> bool:
        return response.status_code == 429 or response.status_code >= 500

    def _parse_json_object(self, response: httpx.Response, request_url: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SecResponseError(f"SEC returned invalid JSON for {request_url}.") from exc

        if not isinstance(payload, dict):
            raise SecResponseError(f"SEC returned non-object JSON for {request_url}.")

        return payload
