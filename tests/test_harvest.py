"""Tests for the harvest pipeline and the files it writes."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from t2bb8.client import Trading212Error
from t2bb8.harvest import build_records, harvest, remove_previous_outputs

from .payloads import EXCHANGES, INSTRUMENTS

NOW = datetime(2026, 11, 13, 7, 30, tzinfo=UTC)
BASE_URL = "https://live.trading212.com"
OUTPUTS = ["exchanges.json", "instruments.json", "last-run.txt", "manifest.json"]


class FakeSource:
    def __init__(
        self,
        instruments: list[dict[str, Any]],
        exchanges: list[dict[str, Any]],
        exchanges_error: Exception | None = None,
    ) -> None:
        self.instruments = instruments
        self.exchanges = exchanges
        self.exchanges_error = exchanges_error

    def get_instruments_raw(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.instruments)

    def get_exchanges_raw(self) -> list[dict[str, Any]]:
        if self.exchanges_error:
            raise self.exchanges_error
        return copy.deepcopy(self.exchanges)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_harvest_writes_all_outputs(tmp_path: Path) -> None:
    result = harvest(FakeSource(INSTRUMENTS, EXCHANGES), tmp_path, BASE_URL, now=NOW)

    assert names(tmp_path) == OUTPUTS

    instruments = read_json(tmp_path / "instruments.json")
    assert [i["ticker"] for i in instruments] == ["AAPL_US_EQ", "VODl_EQ", "VUSAl_EQ"]
    aapl = instruments[0]
    assert aapl["shortName"] == "AAPL"
    assert aapl["exchangeName"] == "NASDAQ"
    assert aapl["exchangeId"] == 68
    # Every original attribute is kept, including ones the models don't declare.
    vusa = instruments[2]
    assert vusa["dividendFrequency"] == "QUARTERLY"
    assert vusa["exchangeName"] == "London Stock Exchange"

    assert read_json(tmp_path / "exchanges.json") == [
        {
            "id": 3,
            "name": "London Stock Exchange",
            "workingScheduleIds": [73, 74],
            "instrumentCount": 2,
        },
        {"id": 68, "name": "NASDAQ", "workingScheduleIds": [59], "instrumentCount": 1},
    ]

    manifest = read_json(tmp_path / "manifest.json")
    assert manifest["harvestedAt"] == "2026-11-13T07:30:00+00:00"
    assert manifest["baseUrl"] == BASE_URL
    assert manifest["instrumentCount"] == 3
    assert manifest["exchangeCount"] == 2
    assert manifest["instrumentTypes"] == {"ETF": 1, "STOCK": 2}

    assert (tmp_path / "last-run.txt").read_text() == "2026-11-13T07:30:00+00:00\n"
    assert result.invalid_instruments == 0
    assert result.unmatched_schedules == 0


def test_last_run_is_recorded_in_utc(tmp_path: Path) -> None:
    local = datetime(2026, 11, 14, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    harvest(FakeSource(INSTRUMENTS, EXCHANGES), tmp_path, BASE_URL, now=local)

    assert (tmp_path / "last-run.txt").read_text() == "2026-11-13T22:30:00+00:00\n"
    assert read_json(tmp_path / "manifest.json")["harvestedAt"] == "2026-11-13T22:30:00+00:00"


def test_rerun_replaces_previous_outputs(tmp_path: Path) -> None:
    harvest(FakeSource(INSTRUMENTS, EXCHANGES), tmp_path, BASE_URL, now=NOW)
    later = NOW + timedelta(days=1)
    result = harvest(FakeSource(INSTRUMENTS[:1], EXCHANGES), tmp_path, BASE_URL, now=later)

    assert names(tmp_path) == OUTPUTS
    assert result.removed_files == 4
    assert len(read_json(tmp_path / "instruments.json")) == 1
    assert (tmp_path / "last-run.txt").read_text() == "2026-11-14T07:30:00+00:00\n"


def test_previous_outputs_deleted_even_when_run_fails(tmp_path: Path) -> None:
    harvest(FakeSource(INSTRUMENTS, EXCHANGES), tmp_path, BASE_URL, now=NOW)

    failing = FakeSource(INSTRUMENTS, EXCHANGES, exchanges_error=Trading212Error(503, "x"))
    with pytest.raises(Trading212Error):
        harvest(failing, tmp_path, BASE_URL, now=NOW + timedelta(days=1))

    # No last-run.txt either: it only exists after a successful run.
    assert names(tmp_path) == []


def test_only_harvester_files_are_removed(tmp_path: Path) -> None:
    keep = [".gitkeep", "notes.json", "instruments.json.bak", "my-manifest.json", "last-run"]
    for name in keep:
        (tmp_path / name).write_text("x")
    (tmp_path / "exchanges.json").mkdir()  # a directory, not a file
    target = tmp_path / "elsewhere.json"
    target.write_text("x")
    (tmp_path / "manifest.json").symlink_to(target)
    remove = ["instruments.json", "last-run.txt", ".instruments.json.abc123.tmp"]
    for name in remove:
        (tmp_path / name).write_text("x")

    removed = remove_previous_outputs(tmp_path)

    assert sorted(p.name for p in removed) == sorted(remove)
    assert names(tmp_path) == sorted([*keep, "exchanges.json", "manifest.json", "elsewhere.json"])


def test_remove_previous_outputs_missing_dir(tmp_path: Path) -> None:
    assert remove_previous_outputs(tmp_path / "missing") == []


def test_invalid_instrument_is_skipped_and_counted(tmp_path: Path) -> None:
    instruments = [*INSTRUMENTS, {"name": "No ticker"}, {"ticker": ""}]
    result = harvest(FakeSource(instruments, EXCHANGES), tmp_path, BASE_URL, now=NOW)

    assert result.instrument_count == 3
    assert result.invalid_instruments == 2
    manifest = read_json(tmp_path / "manifest.json")
    assert manifest["invalidInstruments"] == 2
    assert manifest["rawInstrumentCount"] == 5


def test_unknown_schedule_leaves_exchange_empty() -> None:
    orphan = {"ticker": "XYZ_US_EQ", "shortName": "XYZ", "workingScheduleId": 9999}
    no_schedule = {"ticker": "ABC_US_EQ", "shortName": "ABC"}
    records, _, counts = build_records([orphan, no_schedule], EXCHANGES)

    assert all(r["exchangeId"] is None and r["exchangeName"] is None for r in records)
    assert counts["unmatched_schedules"] == 2


def test_duplicate_tickers_are_counted() -> None:
    _, _, counts = build_records([INSTRUMENTS[0], INSTRUMENTS[0]], EXCHANGES)
    assert counts["duplicate_tickers"] == 1


def test_malformed_exchange_raises_without_writing(tmp_path: Path) -> None:
    bad_exchanges = [{"id": "not-a-number", "workingSchedules": []}]
    with pytest.raises(ValidationError):
        harvest(FakeSource(INSTRUMENTS, bad_exchanges), tmp_path, BASE_URL, now=NOW)

    assert names(tmp_path) == []
