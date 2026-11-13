"""Pydantic models for Trading212 metadata endpoints.

Models allow extra fields so attributes Trading212 adds later are kept, not dropped. The
harvester writes each instrument's raw attributes, so these models are used for validation and
joining rather than as the only record of what the API returned.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(populate_by_name=True, extra="allow", frozen=True)


class TradeableInstrument(BaseModel):
    """An instrument from ``GET /api/v0/equity/metadata/instruments``.

    Attributes:
        ticker: Trading212's unique ticker, e.g. ``AAPL_US_EQ`` or ``VODl_EQ``.
        short_name: The instrument's market ticker symbol, e.g. ``AAPL``.
        name: Full instrument name.
        isin: ISIN code, when Trading212 has one.
        currency_code: Trading currency (ISO 4217).
        type: Instrument type, e.g. ``STOCK``, ``ETF``.
        working_schedule_id: Links to a working schedule on an exchange.
        max_open_quantity: Maximum quantity that can be held.
        added_on: When the instrument was added to Trading212.
        extended_hours: Whether extended-hours trading is supported.
    """

    model_config = _CONFIG

    ticker: str = Field(min_length=1)
    short_name: str | None = Field(None, alias="shortName")
    name: str | None = None
    isin: str | None = None
    currency_code: str | None = Field(None, alias="currencyCode")
    type: str | None = None
    working_schedule_id: int | None = Field(None, alias="workingScheduleId")
    max_open_quantity: Decimal | None = Field(None, alias="maxOpenQuantity")
    added_on: datetime | None = Field(None, alias="addedOn")
    extended_hours: bool | None = Field(None, alias="extendedHours")


class TimeEvent(BaseModel):
    """A market open/close event within a working schedule."""

    model_config = _CONFIG

    date: datetime
    type: str


class WorkingSchedule(BaseModel):
    """A trading schedule; instruments reference it via ``workingScheduleId``."""

    model_config = _CONFIG

    id: int
    time_events: list[TimeEvent] = Field(default_factory=list, alias="timeEvents")


class Exchange(BaseModel):
    """An exchange from ``GET /api/v0/equity/metadata/exchanges``."""

    model_config = _CONFIG

    id: int
    name: str
    working_schedules: list[WorkingSchedule] = Field(
        default_factory=list, alias="workingSchedules"
    )
