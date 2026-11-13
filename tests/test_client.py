"""Tests for Trading212Client against mocked Trading212 API responses."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator

import httpx
import pytest

from t2bb8.client import EXCHANGES_PATH, INSTRUMENTS_PATH, Trading212Client, Trading212Error

from .payloads import EXCHANGES, INSTRUMENTS

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(
    handler: Handler, sleeps: list[float] | None = None, max_attempts: int = 3
) -> Trading212Client:
    recorded = sleeps if sleeps is not None else []
    return Trading212Client(
        "key",
        "secret",
        max_attempts=max_attempts,
        sleep=recorded.append,
        transport=httpx.MockTransport(handler),
    )


def sequence(*responses: httpx.Response | Exception) -> Handler:
    """Return a handler that replays ``responses`` in order (exceptions are raised)."""
    items: Iterator[httpx.Response | Exception] = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = next(items)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def test_get_instruments_sends_basic_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == INSTRUMENTS_PATH
        expected = base64.b64encode(b"key:secret").decode()
        assert request.headers["Authorization"] == f"Basic {expected}"
        return httpx.Response(200, json=INSTRUMENTS)

    with make_client(handler) as client:
        assert client.get_instruments_raw() == INSTRUMENTS


def test_get_exchanges() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == EXCHANGES_PATH
        return httpx.Response(200, json=EXCHANGES)

    with make_client(handler) as client:
        assert client.get_exchanges_raw() == EXCHANGES


def test_rate_limit_honours_retry_after() -> None:
    sleeps: list[float] = []
    handler = sequence(
        httpx.Response(429, headers={"Retry-After": "50"}),
        httpx.Response(200, json=INSTRUMENTS),
    )
    with make_client(handler, sleeps) as client:
        assert client.get_instruments_raw() == INSTRUMENTS
    assert sleeps == [51.0]


def test_rate_limit_uses_ratelimit_reset_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("t2bb8.client.time.time", lambda: 1_000.0)
    sleeps: list[float] = []
    handler = sequence(
        httpx.Response(429, headers={"x-ratelimit-reset": "1030"}),
        httpx.Response(200, json=EXCHANGES),
    )
    with make_client(handler, sleeps) as client:
        client.get_exchanges_raw()
    assert sleeps == [31.0]


def test_rate_limit_zero_wait_falls_back_to_backoff() -> None:
    # Seen live: after waiting out the first 429, Trading212 sent more 429s saying "Retry-After: 0".
    sleeps: list[float] = []
    handler = sequence(
        httpx.Response(429, headers={"Retry-After": "29"}),
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=INSTRUMENTS),
    )
    with make_client(handler, sleeps) as client:
        assert client.get_instruments_raw() == INSTRUMENTS
    assert sleeps == [30.0, 4.0]


def test_rate_limit_wait_is_capped() -> None:
    sleeps: list[float] = []
    handler = sequence(
        httpx.Response(429, headers={"Retry-After": "86400"}),
        httpx.Response(200, json=INSTRUMENTS),
    )
    with make_client(handler, sleeps) as client:
        client.get_instruments_raw()
    assert sleeps == [120.0]


def test_rate_limit_exhausted_raises() -> None:
    sleeps: list[float] = []
    handler = sequence(*[httpx.Response(429) for _ in range(3)])
    with make_client(handler, sleeps) as client, pytest.raises(Trading212Error) as exc_info:
        client.get_instruments_raw()
    assert exc_info.value.status_code == 429
    # Exponential backoff without headers: 2**1, 2**2
    assert sleeps == [2.0, 4.0]


def test_server_error_is_retried() -> None:
    sleeps: list[float] = []
    handler = sequence(
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(200, json=INSTRUMENTS),
    )
    with make_client(handler, sleeps) as client:
        assert client.get_instruments_raw() == INSTRUMENTS
    assert sleeps == [2.0]


def test_timeout_is_retried_then_raises() -> None:
    handler = sequence(*[httpx.ReadTimeout("timed out") for _ in range(3)])
    with make_client(handler) as client, pytest.raises(Trading212Error) as exc_info:
        client.get_instruments_raw()
    assert exc_info.value.status_code is None
    assert "timeout after 3 attempts" in str(exc_info.value)


def test_network_error_is_retried() -> None:
    handler = sequence(httpx.ConnectError("refused"), httpx.Response(200, json=EXCHANGES))
    with make_client(handler) as client:
        assert client.get_exchanges_raw() == EXCHANGES


@pytest.mark.parametrize("status", [401, 403, 404])
def test_client_errors_are_not_retried(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"code": "Scope( metadata ) missing for API key"})

    with make_client(handler) as client, pytest.raises(Trading212Error) as exc_info:
        client.get_instruments_raw()
    assert exc_info.value.status_code == status
    assert calls == 1


def test_invalid_json_raises() -> None:
    handler = sequence(httpx.Response(200, text="<html>maintenance</html>"))
    with make_client(handler) as client, pytest.raises(Trading212Error, match="not valid JSON"):
        client.get_instruments_raw()


def test_unexpected_shape_raises() -> None:
    handler = sequence(httpx.Response(200, json={"items": INSTRUMENTS}))
    with make_client(handler) as client, pytest.raises(Trading212Error, match="JSON array"):
        client.get_instruments_raw()


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Trading212Client("key", "secret", max_attempts=0)
