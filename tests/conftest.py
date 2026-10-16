"""Synthetic files shaped like IEX DAM snapshot exports.

Real IEX data must not be committed, so tests build look-alike files: title rows, a header,
the date on only the first row of each day, text time blocks and comma-formatted numbers.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

HEADER = [
    "Date",
    "Hour",
    "Time Block",
    "Purchase Bid (MW)",
    "Sell Bid (MW)",
    "MCV (MW)",
    "Final Scheduled Volume (MW)",
    "MCP (Rs/MWh) *",
]


def block_label(block: int) -> str:
    start = (block - 1) * 15
    end = start + 15
    return f"{start // 60:02d}:{start % 60:02d} - {end // 60:02d}:{end % 60:02d}"


def synthetic_price(block: int) -> float:
    """A day shape with a solar dip and an evening peak at the cap."""
    if 37 <= block <= 64:  # 09:00-16:00
        return 2_500.0
    if 73 <= block <= 84:  # 18:00-21:00
        return 10_000.0
    return 5_000.0 + block


def iex_rows(start: date, days: int, price: Callable[[int], float] = synthetic_price) -> list:
    rows = []
    for d in range(days):
        day = start + timedelta(days=d)
        for b in range(1, 97):
            rows.append(
                [
                    day.strftime("%d-%m-%Y") if b == 1 else "",
                    str((b - 1) // 4 + 1),
                    block_label(b),
                    "12,345.6",
                    "9,876.5",
                    "8,000.0",
                    "7,950.0",
                    f"{price(b):,.2f}",
                ]
            )
    return rows


@pytest.fixture
def write_export(tmp_path: Path):
    """Write rows to an IEX-shaped CSV and return its path."""

    def _write(rows: list, name: str = "export.csv") -> Path:
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Market Snapshot - Day Ahead Market"])
            w.writerow(["Delivery period: synthetic test data"])
            w.writerow([])
            w.writerow(HEADER)
            w.writerows(rows)
            w.writerow([])
            w.writerow(["", "", "Total", "", "", "", "", ""])
        return path

    return _write
