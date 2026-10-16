"""Validate IEX exports and cache them as Parquet.

uv run python -m battery_arb.data data/raw/*.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from battery_arb.data.iex import load_exports
from battery_arb.data.validate import check_prices

DEFAULT_OUT = Path("data/processed/iex_dam.parquet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="IEX DAM export files (.csv/.xlsx)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Parquet output path")
    args = parser.parse_args(argv)

    df, report = check_prices(load_exports(args.files))
    print(report.summary())
    if not report.ok:
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out)
    print(f"wrote {len(df)} blocks to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
