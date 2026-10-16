"""Check a loaded price table before any model sees it (spec F1).

Every check answers a question an optimiser cannot answer for itself:

* Missing blocks   - a gap silently shifts every later block of the day in a 96-step model.
* Duplicates       - overlapping 31-day exports repeat blocks; identical repeats are dropped,
                     repeats with different prices are conflicts and must be resolved by hand.
* Units            - DAM prices are Rs/MWh (typically 2,000-10,000). A median below 50 means the
                     file is almost certainly in Rs/kWh, which would make revenue 1000x too small.
* Price range      - the regulator's DAM bid range is Rs 0-10,000/MWh (since April 2023).
                     Anything outside it is a parse error or the wrong market (e.g. HP-DAM).
* Cap hits         - not an error, but counted: blocks at the cap are where the true scarcity
                     price is unobserved, which matters for the price-cap sensitivity analysis.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from battery_arb.data.iex import BLOCKS_PER_DAY, TZ, load_exports

DAM_PRICE_FLOOR = 0.0  # Rs/MWh
DAM_PRICE_CAP = 10_000.0  # Rs/MWh (Rs 10/kWh)
KWH_SUSPICION_MEDIAN = 50.0  # Rs/MWh; real DAM medians are in the thousands


@dataclass
class ValidationReport:
    n_rows: int = 0
    first_block: pd.Timestamp | None = None
    last_block: pd.Timestamp | None = None
    exact_duplicates_dropped: int = 0
    conflicting_duplicates: list[pd.Timestamp] = field(default_factory=list)
    missing_blocks: list[pd.Timestamp] = field(default_factory=list)
    missing_prices: list[pd.Timestamp] = field(default_factory=list)
    out_of_range: list[pd.Timestamp] = field(default_factory=list)
    cap_hits: int = 0
    median_price: float = float("nan")
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [
            f"rows: {self.n_rows}  ({self.first_block} -> {self.last_block})",
            f"median MCP: {self.median_price:,.0f} Rs/MWh",
            f"blocks at the {DAM_PRICE_CAP:,.0f} cap: {self.cap_hits}",
            f"exact duplicates dropped: {self.exact_duplicates_dropped}",
        ]
        lines += [f"ERROR: {e}" for e in self.errors] or ["no errors"]
        return "\n".join(lines)


class ValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        super().__init__("price data failed validation:\n" + report.summary())
        self.report = report


def check_prices(
    df: pd.DataFrame,
    cap: float = DAM_PRICE_CAP,
    floor: float = DAM_PRICE_FLOOR,
) -> tuple[pd.DataFrame, ValidationReport]:
    """Validate a table from ``iex.load_exports``; return it de-duplicated, plus a report.

    The returned frame is indexed by ``timestamp`` (block start, IST) and sorted. It is returned
    even when there are errors, so you can inspect the bad rows; use ``load_prices`` when you
    want errors to stop the program.
    """
    rep = ValidationReport()
    value_cols = [c for c in df.columns if c.endswith(("_mw", "_rs_mwh"))]

    # 1. Duplicates: identical repeats are harmless (overlapping exports); different values aren't.
    before = len(df)
    df = df.drop_duplicates(subset=["timestamp", *value_cols])
    rep.exact_duplicates_dropped = before - len(df)
    dup_mask = df["timestamp"].duplicated(keep=False)
    rep.conflicting_duplicates = sorted(df.loc[dup_mask, "timestamp"].unique())
    if rep.conflicting_duplicates:
        rep.errors.append(
            f"{len(rep.conflicting_duplicates)} blocks appear more than once with different "
            f"values, first at {rep.conflicting_duplicates[0]}"
        )
        df = df.drop_duplicates(subset="timestamp")  # keep a frame you can still index
    df = df.set_index("timestamp").sort_index()

    rep.n_rows = len(df)
    if df.empty:
        rep.errors.append("no rows")
        return df, rep
    rep.first_block, rep.last_block = df.index[0], df.index[-1]

    # 2. Completeness: every 15-minute block from the first day's 00:00 to the last day's 23:45.
    days = pd.date_range(df.index[0].normalize(), df.index[-1].normalize(), freq="D", tz=TZ)
    expected = pd.date_range(
        days[0], periods=len(days) * BLOCKS_PER_DAY, freq=f"{24 * 60 // BLOCKS_PER_DAY}min"
    )
    rep.missing_blocks = list(expected.difference(df.index))
    if rep.missing_blocks:
        rep.errors.append(
            f"{len(rep.missing_blocks)} missing blocks, first at {rep.missing_blocks[0]}"
        )

    price = df["mcp_rs_mwh"]
    rep.missing_prices = list(price.index[price.isna()])
    if rep.missing_prices:
        rep.errors.append(f"{len(rep.missing_prices)} blocks have no (numeric) MCP")

    # 3. Units, then range. Units first: a Rs/kWh file would otherwise pass the range check.
    rep.median_price = float(price.median())
    if rep.median_price < KWH_SUSPICION_MEDIAN:
        rep.errors.append(
            f"median MCP is {rep.median_price:.2f}; expected Rs/MWh (thousands). "
            "Is this export in Rs/kWh?"
        )
    bad = price[(price < floor) | (price > cap)]
    rep.out_of_range = list(bad.index)
    if rep.out_of_range:
        rep.errors.append(
            f"{len(bad)} prices outside [{floor:,.0f}, {cap:,.0f}] Rs/MWh, e.g. "
            f"{bad.iloc[0]:,.2f} at {bad.index[0]} (wrong market or parse error?)"
        )
    rep.cap_hits = int((price >= cap - 0.5).sum())
    return df, rep


def load_prices(paths: Iterable[str | Path], cap: float = DAM_PRICE_CAP) -> pd.DataFrame:
    """Load IEX exports and validate them; raise ``ValidationError`` if anything is wrong."""
    df, report = check_prices(load_exports(paths), cap=cap)
    if not report.ok:
        raise ValidationError(report)
    return df
