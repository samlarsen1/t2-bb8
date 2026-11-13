"""Tests for settings loading."""

from __future__ import annotations

import pytest

from t2bb8.config import DEMO_BASE_URL, LIVE_BASE_URL, ConfigError, load_settings


def test_defaults_to_live() -> None:
    settings = load_settings({"T212_API_KEY": "k", "T212_API_SECRET": "s"})
    assert settings.base_url == LIVE_BASE_URL
    assert settings.log_level == "INFO"


def test_demo_url_with_trailing_slash() -> None:
    settings = load_settings(
        {"T212_API_KEY": "k", "T212_API_SECRET": "s", "T212_BASE_URL": f"{DEMO_BASE_URL}/"}
    )
    assert settings.base_url == DEMO_BASE_URL


def test_missing_credentials_names_variables_only() -> None:
    private_value = "super-private"
    with pytest.raises(ConfigError) as exc_info:
        load_settings({"T212_API_KEY": "", "T212_API_SECRET": private_value})
    message = str(exc_info.value)
    assert "T212_API_KEY" in message
    assert "T212_API_SECRET" not in message
    assert private_value not in message


def test_rejects_non_trading212_host() -> None:
    with pytest.raises(ConfigError, match="T212_BASE_URL"):
        load_settings(
            {"T212_API_KEY": "k", "T212_API_SECRET": "s", "T212_BASE_URL": "https://evil.example"}
        )


def test_repr_hides_credentials() -> None:
    settings = load_settings({"T212_API_KEY": "the-key", "T212_API_SECRET": "the-secret"})
    assert "the-key" not in repr(settings)
    assert "the-secret" not in repr(settings)
