"""Harvest Trading212 instruments and exchanges into a data folder.

Each run first deletes the previous run's files, then writes::

    instruments.json   # every API attribute + exchangeId/exchangeName, sorted by ticker
    exchanges.json     # exchange summary with instrument counts
    manifest.json      # run metadata and counts
    last-run.txt       # UTC timestamp of the run, written last (only on success)

Only files with those names are deleted, so other files in ``data_dir`` are left alone.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import ValidationError

from .models import Exchange, TradeableInstrument
from .storage import write_json, write_text

log = structlog.get_logger(__name__)

INSTRUMENTS_FILE = "instruments.json"
EXCHANGES_FILE = "exchanges.json"
MANIFEST_FILE = "manifest.json"
LAST_RUN_FILE = "last-run.txt"
OUTPUT_FILES = (INSTRUMENTS_FILE, EXCHANGES_FILE, MANIFEST_FILE, LAST_RUN_FILE)


class MetadataSource(Protocol):
    """Anything that can supply raw Trading212 metadata (the client, or a test fake)."""

    def get_instruments_raw(self) -> list[dict[str, Any]]: ...

    def get_exchanges_raw(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HarvestResult:
    """Summary of a harvest run.

    Attributes:
        harvested_at: UTC time the run started.
        instrument_count: Instruments written.
        exchange_count: Exchanges written.
        invalid_instruments: Raw instruments that failed validation and were skipped.
        unmatched_schedules: Instruments whose working schedule matched no exchange.
        duplicate_tickers: Tickers that appeared more than once in the API response.
        removed_files: Previous output files deleted before the run.
    """

    harvested_at: datetime
    instrument_count: int
    exchange_count: int
    invalid_instruments: int
    unmatched_schedules: int
    duplicate_tickers: int
    removed_files: int


def _is_output(name: str) -> bool:
    # Exact output names, plus temp files left behind by an interrupted write of one.
    return name in OUTPUT_FILES or any(
        name.startswith(f".{output}.") and name.endswith(".tmp") for output in OUTPUT_FILES
    )


def remove_previous_outputs(data_dir: Path) -> list[Path]:
    """Delete earlier harvest outputs from ``data_dir``.

    Only regular files named like the harvester's outputs (and temp files from an interrupted
    write of them) are removed; symlinks, directories and other files are left alone.

    Args:
        data_dir: Output directory; nothing happens if it doesn't exist.

    Returns:
        The deleted paths.

    Raises:
        OSError: If a matching file can't be deleted.
    """
    if not data_dir.is_dir():
        return []
    removed: list[Path] = []
    for path in sorted(data_dir.iterdir()):
        if _is_output(path.name) and path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(path)
    if removed:
        log.info("removed_previous_outputs", files=[p.name for p in removed])
    return removed


def _schedule_index(exchanges: list[Exchange]) -> dict[int, Exchange]:
    index: dict[int, Exchange] = {}
    for exchange in exchanges:
        for schedule in exchange.working_schedules:
            if schedule.id in index and index[schedule.id].id != exchange.id:
                log.warning(
                    "schedule_shared_by_exchanges",
                    schedule_id=schedule.id,
                    exchanges=[index[schedule.id].id, exchange.id],
                )
                continue
            index[schedule.id] = exchange
    return index


def _validate_instruments(
    raw: list[dict[str, Any]],
) -> tuple[list[tuple[TradeableInstrument, dict[str, Any]]], int]:
    valid: list[tuple[TradeableInstrument, dict[str, Any]]] = []
    invalid = 0
    for position, item in enumerate(raw):
        try:
            valid.append((TradeableInstrument.model_validate(item), item))
        except ValidationError as exc:
            invalid += 1
            log.warning(
                "instrument_invalid",
                index=position,
                ticker=item.get("ticker"),
                fields=sorted({".".join(map(str, err["loc"])) for err in exc.errors()}),
            )
    return valid, invalid


def build_records(
    raw_instruments: list[dict[str, Any]], raw_exchanges: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Validate and enrich raw payloads.

    Each instrument record keeps every attribute the API returned (original camelCase keys)
    plus ``exchangeId`` and ``exchangeName`` joined via ``workingScheduleId``.

    Args:
        raw_instruments: Raw ``/metadata/instruments`` payload.
        raw_exchanges: Raw ``/metadata/exchanges`` payload.

    Returns:
        Instrument records sorted by ticker, exchange summaries sorted by id, and counts of
        ``invalid_instruments``, ``unmatched_schedules`` and ``duplicate_tickers``.

    Raises:
        pydantic.ValidationError: If an exchange is malformed; without valid exchanges the
            instrument-to-exchange join can't be trusted.
    """
    exchanges = [Exchange.model_validate(item) for item in raw_exchanges]
    by_schedule = _schedule_index(exchanges)
    instruments, invalid = _validate_instruments(raw_instruments)

    records: list[dict[str, Any]] = []
    per_exchange: Counter[int] = Counter()
    unmatched = 0
    for instrument, raw in instruments:
        exchange = (
            by_schedule.get(instrument.working_schedule_id)
            if instrument.working_schedule_id is not None
            else None
        )
        if exchange is None:
            unmatched += 1
        else:
            per_exchange[exchange.id] += 1
        records.append(
            {
                **raw,
                "exchangeId": exchange.id if exchange else None,
                "exchangeName": exchange.name if exchange else None,
            }
        )
    records.sort(key=lambda record: str(record["ticker"]))

    ticker_counts = Counter(str(record["ticker"]) for record in records)
    duplicates = sum(1 for count in ticker_counts.values() if count > 1)
    if duplicates:
        log.warning("duplicate_tickers", count=duplicates)
    if unmatched:
        log.warning("instruments_without_exchange", count=unmatched)

    exchange_summaries = [
        {
            "id": exchange.id,
            "name": exchange.name,
            "workingScheduleIds": sorted(s.id for s in exchange.working_schedules),
            "instrumentCount": per_exchange[exchange.id],
        }
        for exchange in sorted(exchanges, key=lambda e: e.id)
    ]
    counts = {
        "invalid_instruments": invalid,
        "unmatched_schedules": unmatched,
        "duplicate_tickers": duplicates,
    }
    return records, exchange_summaries, counts


def harvest(
    source: MetadataSource,
    data_dir: Path,
    base_url: str,
    now: datetime | None = None,
) -> HarvestResult:
    """Delete previous outputs, fetch instruments and exchanges, and write them to ``data_dir``.

    Previous outputs are deleted before anything is fetched, so a failed run leaves no output
    files behind. ``last-run.txt`` is written last, so it only exists after a successful run.

    Args:
        source: Metadata source, normally a :class:`~t2bb8.client.Trading212Client`.
        data_dir: Output directory; created if missing.
        base_url: Trading212 base URL, recorded in the manifest.
        now: Run timestamp; defaults to the current UTC time.

    Returns:
        A summary of the run.

    Raises:
        Trading212Error: If the API calls fail.
        pydantic.ValidationError: If the exchange payload is malformed.
        OSError: If files can't be deleted or written.
    """
    harvested_at = (now or datetime.now(UTC)).astimezone(UTC)
    removed = remove_previous_outputs(data_dir)

    log.info("fetching_instruments")
    raw_instruments = source.get_instruments_raw()
    log.info("fetching_exchanges", instruments=len(raw_instruments))
    raw_exchanges = source.get_exchanges_raw()

    records, exchange_summaries, counts = build_records(raw_instruments, raw_exchanges)
    write_json(data_dir / INSTRUMENTS_FILE, records)
    write_json(data_dir / EXCHANGES_FILE, exchange_summaries)

    result = HarvestResult(
        harvested_at=harvested_at,
        instrument_count=len(records),
        exchange_count=len(exchange_summaries),
        removed_files=len(removed),
        **counts,
    )
    type_counts = Counter(str(record.get("type")) for record in records)
    write_json(
        data_dir / MANIFEST_FILE,
        {
            "harvestedAt": harvested_at.isoformat(),
            "baseUrl": base_url,
            "instrumentCount": result.instrument_count,
            "exchangeCount": result.exchange_count,
            "rawInstrumentCount": len(raw_instruments),
            "invalidInstruments": result.invalid_instruments,
            "unmatchedSchedules": result.unmatched_schedules,
            "duplicateTickers": result.duplicate_tickers,
            "instrumentTypes": dict(sorted(type_counts.items())),
        },
    )
    write_text(data_dir / LAST_RUN_FILE, f"{harvested_at.isoformat()}\n")

    log.info(
        "harvest_complete",
        instruments=result.instrument_count,
        exchanges=result.exchange_count,
        invalid=result.invalid_instruments,
        unmatched=result.unmatched_schedules,
        data_dir=str(data_dir),
    )
    return result
