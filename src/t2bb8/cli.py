"""Command-line entrypoint: ``uv run t2bb8-harvest``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import structlog
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from .client import Trading212Client, Trading212Error
from .config import ConfigError, load_settings
from .harvest import HarvestResult, harvest, last_run
from .t2contract import Exit, RunReport, Skipped, run_lock, t212_lock

log = structlog.get_logger(__name__)

DEFAULT_DATA_DIR = Path("data")
# The instrument list changes slowly. Skipping a harvest when the last one is this recent keeps a
# timer, a manual run and the orchestrator from spending the key's rate limit on the same data.
DEFAULT_MIN_AGE_HOURS = 20.0

DROID = "t2-bb8"

# Exit codes, from the T2 droid contract (t2-hq CONTRACT.md)
EXIT_OK = Exit.OK
EXIT_FAILED = Exit.FAILED
EXIT_CONFIG = Exit.CONFIG
EXIT_SKIPPED = Exit.SKIPPED


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
    parser.add_argument(
        "--report",
        type=Path,
        help="Write the JSON run report here (default: $T2_RUNS_DIR or var/runs)",
    )
    return parser.parse_args(argv)


def recent_run(data_dir: Path, min_age_hours: float, now: datetime) -> datetime | None:
    """The last harvest's finish time if it is too recent to harvest again, else ``None``."""
    previous = last_run(data_dir)
    if previous is None or now - previous >= timedelta(hours=min_age_hours):
        return None
    return previous


def _version() -> str:
    try:
        return version("t2-bb8")
    except PackageNotFoundError:
        return "unknown"


def _result(result: HarvestResult, data_dir: Path) -> dict[str, object]:
    data = asdict(result)
    data["harvested_at"] = result.harvested_at.isoformat()
    data["data_dir"] = str(data_dir)
    return data


def run(args: argparse.Namespace, report: RunReport) -> Exit:
    """Run one harvest under the droid locks, recording the outcome in ``report``."""
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        # ConfigError messages name variables, never their values.
        log.error("config_error", error=str(exc))
        return report.finish(Exit.CONFIG, "configuration error", errors=[str(exc)])

    try:
        with run_lock(DROID):
            if not args.force:
                previous = recent_run(args.data_dir, args.min_age_hours, datetime.now(UTC))
                if previous is not None:
                    # Checked before anything is deleted or fetched, so that run's data is kept.
                    log.info(
                        "harvest_skipped",
                        reason="recent_harvest",
                        last_run=previous.isoformat(),
                        min_age_hours=args.min_age_hours,
                        hint="pass --force to harvest anyway",
                    )
                    return report.finish(
                        Exit.SKIPPED,
                        f"last harvest finished at {previous.isoformat()}; pass --force to repeat",
                        {"skipped": "recent_harvest", "last_run": previous.isoformat()},
                    )
            with (
                t212_lock(),
                Trading212Client(
                    settings.api_key, settings.api_secret, base_url=settings.base_url
                ) as client,
            ):
                result = harvest(client, args.data_dir, settings.base_url)
    except Skipped as exc:
        log.warning("run_skipped", reason=exc.reason)
        return report.finish(Exit.SKIPPED, exc.reason, {"skipped": "lock_busy"})
    except Trading212Error as exc:
        log.error("t212_api_error", status=exc.status_code, error=str(exc))
        if exc.status_code in (401, 403):
            log.error("t212_auth_hint", hint="check the API key, secret and Metadata permission")
        return report.finish(Exit.FAILED, "Trading212 API error", errors=[str(exc)])
    except ValidationError as exc:
        log.error("t212_payload_invalid", errors=exc.error_count())
        return report.finish(
            Exit.FAILED, "invalid exchanges payload", errors=[f"{exc.error_count()} errors"]
        )
    except OSError as exc:
        log.error("write_failed", path=str(exc.filename), error=exc.strerror)
        return report.finish(
            Exit.FAILED, "could not write the data", errors=[f"{exc.filename}: {exc.strerror}"]
        )

    return report.finish(
        Exit.OK,
        f"{result.instrument_count} instruments and {result.exchange_count} exchanges",
        _result(result, args.data_dir),
    )


def main(argv: list[str] | None = None) -> int:
    """Run a harvest, write its run report, and return the process exit code."""
    args = parse_args(argv)
    # Read .env from the working directory; real environment variables take precedence.
    load_dotenv(find_dotenv(usecwd=True))
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))

    report = RunReport(
        droid=DROID,
        version=_version(),
        command="harvest",
        argv=list(sys.argv[1:] if argv is None else argv),
    )
    code = run(args, report)
    try:
        path = report.write(args.report)
    except OSError as exc:
        log.error("run_report_failed", path=str(exc.filename), error=exc.strerror)
    else:
        log.info("run_report_written", path=str(path), status=report.to_dict()["status"])
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
