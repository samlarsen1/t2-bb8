"""Command-line entrypoint: ``uv run t2bb8-harvest``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from .client import Trading212Client, Trading212Error
from .config import ConfigError, load_settings
from .harvest import harvest, last_run

log = structlog.get_logger(__name__)

DEFAULT_DATA_DIR = Path("data")
# The instrument list changes slowly. Skipping a harvest when the last one is this recent keeps a
# timer, a manual run and the orchestrator from spending the key's rate limit on the same data.
DEFAULT_MIN_AGE_HOURS = 20.0

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
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=(
            "Skip the harvest if the last successful one finished less than this many hours ago "
            f"(default: {DEFAULT_MIN_AGE_HOURS:g})"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Harvest even if the last successful harvest is recent",
    )
    return parser.parse_args(argv)


def recent_run(data_dir: Path, min_age_hours: float, now: datetime) -> datetime | None:
    """The last harvest's finish time if it is too recent to harvest again, else ``None``."""
    previous = last_run(data_dir)
    if previous is None or now - previous >= timedelta(hours=min_age_hours):
        return None
    return previous


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

    if not args.force:
        previous = recent_run(args.data_dir, args.min_age_hours, datetime.now(UTC))
        if previous is not None:
            # Checked before anything is deleted or fetched, so the data from that run is kept.
            log.info(
                "harvest_skipped",
                reason="recent_harvest",
                last_run=previous.isoformat(),
                min_age_hours=args.min_age_hours,
                hint="pass --force to harvest anyway",
            )
            return EXIT_OK

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
