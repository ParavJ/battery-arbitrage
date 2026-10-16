"""Load IEX day-ahead market (DAM) exports into one clean, block-indexed price table.

The IEX market snapshot page exports at most 31 days per file, as CSV or Excel. Each file is
"report-shaped" rather than "data-shaped": title rows above the header, the date written only on
the first row of each day, time blocks as text ("00:00 - 00:15"), numbers with thousands
separators. This module turns one or more such files into a tidy table:

    timestamp (block start, Asia/Kolkata) | date | block (1-96) | mcp_rs_mwh | volumes ...

Parsing lives here; checking the result lives in ``validate.py``. Keeping them apart means a
validation failure always tells you the data is wrong, never that the parser crashed.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

TZ = "Asia/Kolkata"  # IST, UTC+5:30, no daylight saving, so every day has exactly 96 blocks
BLOCK_MINUTES = 15
BLOCKS_PER_DAY = 24 * 60 // BLOCK_MINUTES  # 96

# Canonical column name -> regex matched against the export's header text (case-insensitive).
# Matching on keywords rather than exact strings survives small header changes such as
# "MCP (Rs/MWh) *" vs "MCP (Rs/MWh)".
COLUMN_PATTERNS: dict[str, str] = {
    "date": r"^date",
    "hour": r"^hour",
    "time_block": r"time\s*block|^block",
    "purchase_bid_mw": r"purchase\s*bid",
    "sell_bid_mw": r"sell\s*bid",
    "mcv_mw": r"\bmcv\b|cleared\s*volume",
    "scheduled_volume_mw": r"schedul",
    "mcp_rs_mwh": r"\bmcp\b|clearing\s*price",
}
REQUIRED = ("date", "time_block", "mcp_rs_mwh")
NUMERIC = ("purchase_bid_mw", "sell_bid_mw", "mcv_mw", "scheduled_volume_mw", "mcp_rs_mwh")

_BLOCK_TEXT = re.compile(r"^\s*(\d{1,2}):(\d{2})")  # start time of "HH:MM - HH:MM"
_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y")


class IEXFormatError(ValueError):
    """The file does not look like an IEX DAM market snapshot export."""


def read_raw(path: str | Path) -> pd.DataFrame:
    """Read an export exactly as IEX wrote it, with the header row located automatically.

    Everything is read as text (``dtype=str``) so nothing is silently converted before we
    have looked at it; e.g. "10,000.00" would otherwise stay a string while "3,512" might not.
    """
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        grid = pd.read_excel(path, header=None, dtype=str)
    elif path.suffix.lower() == ".csv":
        # Not pd.read_csv: it fixes the column count from the first line, and a one-cell title
        # row above an 8-column table makes it fail. The csv module accepts ragged rows.
        with path.open(newline="", encoding="utf-8-sig") as f:
            grid = pd.DataFrame(list(csv.reader(f)), dtype=str)
    else:
        raise IEXFormatError(f"{path.name}: expected .csv or .xlsx, got {path.suffix!r}")

    header_row = _find_header_row(grid)
    if header_row is None:
        raise IEXFormatError(f"{path.name}: no header row containing an MCP column found")
    header = grid.iloc[header_row].fillna("").str.strip()
    body = grid.iloc[header_row + 1 :].reset_index(drop=True)
    body.columns = header
    return body


def _find_header_row(grid: pd.DataFrame, max_rows: int = 30) -> int | None:
    """Index of the first row that has both a date-like and an MCP-like cell."""
    for i in range(min(max_rows, len(grid))):
        cells = grid.iloc[i].dropna().astype(str).str.strip().str.lower()
        if cells.str.contains(r"^date").any() and cells.str.contains(r"\bmcp\b").any():
            return i
    return None


def normalise(raw: pd.DataFrame, source: str = "") -> pd.DataFrame:
    """Turn a raw export into the canonical tidy table (one row per delivery block).

    Rows that are not delivery blocks (blank spacer rows, "Total"/"Average" footers) are dropped.
    """
    df = raw.rename(columns=_map_columns(list(raw.columns), source))
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise IEXFormatError(f"{source}: missing columns {missing}; found {list(raw.columns)}")
    df = df[[c for c in COLUMN_PATTERNS if c in df.columns]].copy()

    # The date appears only on each day's first row (merged cells in the Excel view).
    df["date"] = df["date"].replace(r"^\s*$", pd.NA, regex=True).ffill()
    df["block"] = df["time_block"].map(_parse_block)
    df = df[df["block"].notna()].copy()  # drops footers and blank rows
    df["block"] = df["block"].astype(int)
    df["date"] = df["date"].map(_parse_date)

    for col in NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
            )

    start = pd.to_datetime(df["date"]) + pd.to_timedelta((df["block"] - 1) * BLOCK_MINUTES, "min")
    df.insert(0, "timestamp", start.dt.tz_localize(TZ))
    df["source_file"] = source
    return df.drop(columns=["time_block", "hour"], errors="ignore").reset_index(drop=True)


def _map_columns(columns: list[str], source: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for canonical, pattern in COLUMN_PATTERNS.items():
        hits = [c for c in columns if re.search(pattern, str(c), flags=re.IGNORECASE)]
        if len(hits) > 1:
            raise IEXFormatError(f"{source}: {canonical!r} matches several columns: {hits}")
        if hits:
            mapping[hits[0]] = canonical
    return mapping


def _parse_block(value: object) -> int | None:
    """'00:00 - 00:15' -> 1, '23:45 - 24:00' -> 96; a plain integer 1-96 is accepted as is."""
    text = str(value).strip()
    if text.isdigit() and 1 <= int(text) <= BLOCKS_PER_DAY:
        return int(text)
    m = _BLOCK_TEXT.match(text)
    if not m:
        return None
    minutes = int(m.group(1)) * 60 + int(m.group(2))
    if minutes % BLOCK_MINUTES or minutes >= 24 * 60:
        return None
    return minutes // BLOCK_MINUTES + 1


def _parse_date(value: object) -> pd.Timestamp:
    """Parse a day-first Indian date. Explicit formats, never guessing: 03-09 is 3 September."""
    text = str(value).strip()
    text = re.sub(r"\s+00:00:00$", "", text)  # Excel dates read as text
    for fmt in _DATE_FORMATS:
        try:
            return pd.to_datetime(text, format=fmt)
        except ValueError:
            continue
    raise IEXFormatError(f"unrecognised date {value!r}")


def load_exports(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load and stack several exports (e.g. one per month), sorted by time.

    Overlapping exports are fine: identical repeated blocks are de-duplicated by
    ``validate.check_prices``, while blocks that repeat with *different* prices are reported as
    conflicts there, because silently picking one would hide a real data problem.
    """
    frames = [normalise(read_raw(p), source=Path(p).name) for p in paths]
    if not frames:
        raise IEXFormatError("no files given")
    return pd.concat(frames, ignore_index=True).sort_values(["timestamp", "source_file"])
