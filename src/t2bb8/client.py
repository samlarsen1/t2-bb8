"""Minimal synchronous Trading212 client for the metadata endpoints.

API reference: https://docs.trading212.com/api
Rate limits: instruments 1 request / 50s, exchanges 1 request / 30s.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any

import httpx
import structlog

from .config import LIVE_BASE_URL

log = structlog.get_logger(__name__)

INSTRUMENTS_PATH = "/api/v0/equity/metadata/instruments"
EXCHANGES_PATH = "/api/v0/equity/metadata/exchanges"

# Upper bound on any single wait, so a bad header can't stall the process indefinitely.
MAX_WAIT_SECONDS = 120.0
# Added to server-provided waits, which are rounded to whole seconds.
RATE_LIMIT_MARGIN = 1.0


class Trading212Error(Exception):
    """A non-retryable or exhausted Trading212 API failure.

    Attributes:
        status_code: HTTP status code, or ``None`` for transport failures.
    """

    def __init__(self, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        prefix = f"Trading212 API error {status_code}" if status_code else "Trading212 API error"
        super().__init__(f"{prefix}: {message}")


class _RetryableError(Exception):
    def __init__(self, status_code: int | None, reason: str, wait: float | None) -> None:
        self.status_code = status_code
        self.reason = reason
        self.wait = wait
        super().__init__(reason)


def _retry_wait(response: httpx.Response, now: float) -> float | None:
    """Seconds to wait before retrying, from ``Retry-After`` or ``x-ratelimit-reset``."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset is not None:
        try:
            return max(0.0, float(reset) - now)
        except ValueError:
            pass
    return None


class Trading212Client:
    """Synchronous Trading212 client. Use as a context manager.

    Retries timeouts, network errors, HTTP 429 and 5xx responses with backoff, honouring the
    server's rate-limit headers when present.

    Args:
        api_key: Trading212 API key.
        api_secret: Trading212 API secret.
        base_url: Trading212 live or demo base URL.
        timeout: Per-request timeout in seconds.
        max_attempts: Total attempts per request, including the first.
        backoff_base: Base for exponential backoff when no rate-limit header is given.
        sleep: Sleep function, injectable for tests.
        transport: Optional httpx transport, injectable for tests.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = LIVE_BASE_URL,
        timeout: float = 60.0,
        max_attempts: int = 4,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        encoded = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Basic {encoded}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._sleep = sleep

    def __enter__(self) -> Trading212Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _send_once(self, path: str) -> Any:
        try:
            response = self._client.get(path)
        except httpx.TimeoutException as exc:
            raise _RetryableError(None, "timeout", None) from exc
        except httpx.TransportError as exc:
            raise _RetryableError(None, type(exc).__name__, None) from exc

        if response.status_code == 429:
            raise _RetryableError(429, "rate_limited", _retry_wait(response, time.time()))
        if response.status_code >= 500:
            raise _RetryableError(response.status_code, "server_error", None)
        if not response.is_success:
            # Trading212 error bodies are short codes such as "Scope( metadata ) missing".
            raise Trading212Error(response.status_code, response.text[:200] or "no body")
        try:
            return response.json()
        except ValueError as exc:
            raise Trading212Error(response.status_code, "response was not valid JSON") from exc

    def _get(self, path: str) -> Any:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._send_once(path)
            except _RetryableError as exc:
                if attempt == self._max_attempts:
                    raise Trading212Error(
                        exc.status_code, f"{exc.reason} after {attempt} attempts"
                    ) from exc
                backoff = self._backoff_base**attempt
                # Rate-limit headers are whole seconds, so pad them; never wait less than the
                # backoff, or a "reset in 0s" header would retry immediately into another 429.
                wait = backoff if exc.wait is None else max(exc.wait + RATE_LIMIT_MARGIN, backoff)
                wait = min(wait, MAX_WAIT_SECONDS)
                log.warning(
                    "t212_request_retry",
                    path=path,
                    reason=exc.reason,
                    status=exc.status_code,
                    attempt=attempt,
                    wait_seconds=round(wait, 1),
                )
                self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def get_instruments_raw(self) -> list[dict[str, Any]]:
        """Fetch every instrument Trading212 offers, as returned by the API.

        Returns:
            The raw instrument objects.

        Raises:
            Trading212Error: On API failure or an unexpected payload shape.
        """
        return self._get_list(INSTRUMENTS_PATH)

    def get_exchanges_raw(self) -> list[dict[str, Any]]:
        """Fetch every exchange and its working schedules, as returned by the API.

        Returns:
            The raw exchange objects.

        Raises:
            Trading212Error: On API failure or an unexpected payload shape.
        """
        return self._get_list(EXCHANGES_PATH)

    def _get_list(self, path: str) -> list[dict[str, Any]]:
        data = self._get(path)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise Trading212Error(None, f"expected a JSON array of objects from {path}")
        return data
