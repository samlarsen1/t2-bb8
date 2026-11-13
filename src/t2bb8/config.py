"""Settings loaded from environment variables (populated from ``.env`` by the CLI)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

LIVE_BASE_URL = "https://live.trading212.com"
DEMO_BASE_URL = "https://demo.trading212.com"
ALLOWED_BASE_URLS = frozenset({LIVE_BASE_URL, DEMO_BASE_URL})


class ConfigError(Exception):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings.

    Attributes:
        api_key: Trading212 API key.
        api_secret: Trading212 API secret.
        base_url: Trading212 live or demo base URL.
        log_level: Logging level name.
    """

    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    base_url: str = LIVE_BASE_URL
    log_level: str = "INFO"


def load_settings(env: Mapping[str, str]) -> Settings:
    """Build settings from an environment mapping.

    Args:
        env: Environment variables, usually ``os.environ``.

    Returns:
        Validated settings.

    Raises:
        ConfigError: If credentials are missing or the base URL isn't a Trading212 host.
    """
    missing = [name for name in ("T212_API_KEY", "T212_API_SECRET") if not env.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    base_url = env.get("T212_BASE_URL", LIVE_BASE_URL).rstrip("/")
    # Credentials are sent to this host, so only allow Trading212's own endpoints.
    if base_url not in ALLOWED_BASE_URLS:
        allowed = ", ".join(sorted(ALLOWED_BASE_URLS))
        raise ConfigError(f"T212_BASE_URL must be one of: {allowed}")

    return Settings(
        api_key=env["T212_API_KEY"],
        api_secret=env["T212_API_SECRET"],
        base_url=base_url,
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )
