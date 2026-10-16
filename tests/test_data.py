from datetime import date, datetime

import pandas as pd
import pytest
from conftest import HEADER, iex_rows
from openpyxl import Workbook

from battery_arb.data import (
    IEXFormatError,
    ValidationError,
    check_prices,
    load_exports,
    load_prices,
)
from battery_arb.data.iex import _parse_block

SEP1 = date(2026, 9, 1)


@pytest.mark.parametrize(
    ("text", "block"),
    [("00:00 - 00:15", 1), ("00:15 - 00:30", 2), ("12:00 - 12:15", 49), ("23:45 - 24:00", 96)],
)
def test_block_labels(text, block):
    assert _parse_block(text) == block


@pytest.mark.parametrize("text", ["Total", "", "nan", "24:00 - 24:15", "10:07 - 10:22"])
def test_non_blocks_are_rejected(text):
    assert _parse_block(text) is None


def test_clean_two_days(write_export):
    df = load_prices([write_export(iex_rows(SEP1, 2))])
    assert len(df) == 192
    assert str(df.index.tz) == "Asia/Kolkata"
    assert df.index[0] == pd.Timestamp("2026-09-01 00:00", tz="Asia/Kolkata")
    assert df.index[-1] == pd.Timestamp("2026-09-02 23:45", tz="Asia/Kolkata")
    # Second day's date was blank in the file and must have been forward-filled.
    assert (df["block"].groupby(df["date"]).count() == 96).all()
    assert df.loc["2026-09-01 18:00+05:30", "mcp_rs_mwh"] == 10_000.0  # "10,000.00" parsed


def test_cap_hits_counted(write_export):
    _, report = check_prices(load_exports([write_export(iex_rows(SEP1, 1))]))
    assert report.ok
    assert report.cap_hits == 12  # blocks 73-84


def test_missing_block_is_reported(write_export):
    rows = iex_rows(SEP1, 1)
    del rows[40]  # block 41, 10:00
    _, report = check_prices(load_exports([write_export(rows)]))
    assert report.missing_blocks == [pd.Timestamp("2026-09-01 10:00", tz="Asia/Kolkata")]
    assert not report.ok


def test_overlapping_identical_exports_are_merged(write_export):
    a = write_export(iex_rows(SEP1, 2), "a.csv")
    b = write_export(iex_rows(date(2026, 9, 2), 2), "b.csv")  # 2 Sep in both files
    df, report = check_prices(load_exports([a, b]))
    assert report.ok
    assert report.exact_duplicates_dropped == 96
    assert len(df) == 3 * 96


def test_conflicting_duplicates_are_errors(write_export):
    a = write_export(iex_rows(SEP1, 1), "a.csv")
    b = write_export(iex_rows(SEP1, 1, price=lambda b: 4_000.0), "b.csv")
    with pytest.raises(ValidationError, match="different values"):
        load_prices([a, b])


def test_rs_per_kwh_file_is_rejected(write_export):
    rows = iex_rows(SEP1, 1, price=lambda b: 4.25)
    with pytest.raises(ValidationError, match="Rs/kWh"):
        load_prices([write_export(rows)])


def test_price_above_cap_is_rejected(write_export):
    rows = iex_rows(SEP1, 1, price=lambda b: 15_000.0 if b == 80 else 5_000.0)
    with pytest.raises(ValidationError, match="outside"):
        load_prices([write_export(rows)])


def test_non_numeric_price_is_reported(write_export):
    rows = iex_rows(SEP1, 1)
    rows[5][-1] = "-"
    _, report = check_prices(load_exports([write_export(rows)]))
    assert len(report.missing_prices) == 1


def test_wrong_file_is_rejected(tmp_path):
    path = tmp_path / "not_iex.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(IEXFormatError, match="header"):
        load_exports([path])


def test_excel_export_with_real_date_cells(tmp_path):
    """Excel exports may store dates and numbers as typed cells rather than text."""
    wb = Workbook()
    ws = wb.active
    ws.append(["Market Snapshot - Day Ahead Market"])
    ws.append(HEADER)
    for row in iex_rows(SEP1, 1):
        day = datetime(2026, 9, 1) if row[0] else None
        ws.append(
            [
                day,
                int(row[1]),
                row[2],
                12345.6,
                9876.5,
                8000.0,
                7950.0,
                float(row[7].replace(",", "")),
            ]
        )
    path = tmp_path / "export.xlsx"
    wb.save(path)

    df = load_prices([path])
    assert len(df) == 96
    assert df.index[0] == pd.Timestamp("2026-09-01 00:00", tz="Asia/Kolkata")
    assert df["mcp_rs_mwh"].max() == 10_000.0
