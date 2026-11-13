"""Command-line entrypoint: ``uv run t2bb8-harvest``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import structlog
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from .client import Trading212Client, Trading212Error
from .config import ConfigError, load_settings
from .harvest import harvest

log = structlog.get_logger(__name__)

DEFAULT_DATA_DIR = Path("data")

# Exit codes
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_API = 3
EXIT_DATA = 4


def setup_logging(level: str) -> None:
    """Configure structlog with a minimum level name such as ``INFO``."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="t2bb8-harvest",
        description="Harvest all Trading212 instruments and exchanges into a data folder.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Output directory (default: ./data)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a harvest and return a process exit code."""
    args = parse_args(argv)
    # Read .env from the working directory; real environment variables take precedence.
    load_dotenv(find_dotenv(usecwd=True))
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))

    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        # ConfigError messages name variables, never their values.
        log.error("config_error", error=str(exc))
        return EXIT_CONFIG

    try:
        with Trading212Client(
            settings.api_key, settings.api_secret, base_url=settings.base_url
        ) as client:
            harvest(client, args.data_dir, settings.base_url)
    except Trading212Error as exc:
        log.error("t212_api_error", status=exc.status_code, error=str(exc))
        if exc.status_code in (401, 403):
            log.error("t212_auth_hint", hint="check the API key, secret and Metadata permission")
        return EXIT_API
    except ValidationError as exc:
        log.error("t212_payload_invalid", errors=exc.error_count())
        return EXIT_DATA
    except OSError as exc:
        log.error("write_failed", path=str(exc.filename), error=exc.strerror)
        return EXIT_DATA
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
