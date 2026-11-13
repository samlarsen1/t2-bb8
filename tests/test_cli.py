"""Tests for the CLI entrypoint and its exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from t2bb8 import cli
from t2bb8.client import EXCHANGES_PATH, INSTRUMENTS_PATH, Trading212Client

from .payloads import EXCHANGES, INSTRUMENTS


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never read a real .env (it holds live credentials) during tests.
    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: False)
    for name in ("T212_API_KEY", "T212_API_SECRET", "T212_BASE_URL", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)


def use_mock_api(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def factory(api_key: str, api_secret: str, base_url: str) -> Trading212Client:
        return Trading212Client(
            api_key,
            api_secret,
            base_url=base_url,
            sleep=lambda _: None,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(cli, "Trading212Client", factory)


def set_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T212_API_KEY", "key")
    monkeypatch.setenv("T212_API_SECRET", "secret")


def test_missing_credentials_exit_code() -> None:
    assert cli.main(["--data-dir", "unused"]) == cli.EXIT_CONFIG


def test_successful_harvest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {INSTRUMENTS_PATH: INSTRUMENTS, EXCHANGES_PATH: EXCHANGES}[request.url.path]
        return httpx.Response(200, json=payload)

    use_mock_api(monkeypatch, handler)

    assert cli.main(["--data-dir", str(tmp_path)]) == cli.EXIT_OK
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["instrumentCount"] == 3
    assert manifest["baseUrl"] == "https://live.trading212.com"
    assert (tmp_path / "last-run.txt").exists()


def test_auth_failure_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_credentials(monkeypatch)
    use_mock_api(monkeypatch, lambda request: httpx.Response(401, json={"code": "Unauthorized"}))

    assert cli.main(["--data-dir", str(tmp_path)]) == cli.EXIT_API
    assert not (tmp_path / "instruments.json").exists()


def test_rate_limit_exhausted_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_credentials(monkeypatch)
    use_mock_api(monkeypatch, lambda request: httpx.Response(429))

    assert cli.main(["--data-dir", str(tmp_path)]) == cli.EXIT_API


def test_malformed_payload_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == INSTRUMENTS_PATH:
            return httpx.Response(200, json=INSTRUMENTS)
        return httpx.Response(200, json=[{"id": "bad"}])

    use_mock_api(monkeypatch, handler)

    assert cli.main(["--data-dir", str(tmp_path)]) == cli.EXIT_DATA


def test_unwritable_data_dir_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_credentials(monkeypatch)
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    use_mock_api(monkeypatch, lambda request: httpx.Response(200, json=[]))

    assert cli.main(["--data-dir", str(blocker / "data")]) == cli.EXIT_DATA
