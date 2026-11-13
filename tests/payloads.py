"""Realistic Trading212 metadata payloads shared by the tests."""

from __future__ import annotations

from typing import Any

AAPL: dict[str, Any] = {
    "ticker": "AAPL_US_EQ",
    "type": "STOCK",
    "workingScheduleId": 59,
    "isin": "US0378331005",
    "currencyCode": "USD",
    "name": "Apple",
    "shortName": "AAPL",
    "maxOpenQuantity": 2714,
    "extendedHours": True,
    "addedOn": "2018-07-13T07:15:44.000+03:00",
}

VOD: dict[str, Any] = {
    "ticker": "VODl_EQ",
    "type": "STOCK",
    "workingScheduleId": 73,
    "isin": "GB00BH4HKS39",
    "currencyCode": "GBX",
    "name": "Vodafone Group",
    "shortName": "VOD",
    "maxOpenQuantity": 1500000,
    "addedOn": "2018-06-11T12:30:00.000+03:00",
}

VUSA: dict[str, Any] = {
    "ticker": "VUSAl_EQ",
    "type": "ETF",
    "workingScheduleId": 73,
    "isin": "IE00B3XXRP09",
    "currencyCode": "GBP",
    "name": "Vanguard S&P 500 (Dist)",
    "shortName": "VUSA",
    "maxOpenQuantity": 44000,
    "addedOn": "2019-02-04T09:00:00.000+02:00",
    # An attribute the models don't know about must still be kept.
    "dividendFrequency": "QUARTERLY",
}

INSTRUMENTS: list[dict[str, Any]] = [VUSA, AAPL, VOD]

EXCHANGES: list[dict[str, Any]] = [
    {
        "id": 68,
        "name": "NASDAQ",
        "workingSchedules": [
            {
                "id": 59,
                "timeEvents": [
                    {"date": "2026-11-13T09:00:00.000+00:00", "type": "PRE_MARKET_OPEN"},
                    {"date": "2026-11-13T14:30:00.000+00:00", "type": "OPEN"},
                    {"date": "2026-11-13T21:00:00.000+00:00", "type": "CLOSE"},
                ],
            }
        ],
    },
    {
        "id": 3,
        "name": "London Stock Exchange",
        "workingSchedules": [
            {
                "id": 73,
                "timeEvents": [
                    {"date": "2026-11-13T08:00:00.000+00:00", "type": "OPEN"},
                    {"date": "2026-11-13T16:30:00.000+00:00", "type": "CLOSE"},
                ],
            },
            {"id": 74, "timeEvents": []},
        ],
    },
]
