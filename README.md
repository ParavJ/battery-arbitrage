# battery-arbitrage

What is a battery worth in India's day-ahead power market? This project computes the
revenue-maximising charge/discharge schedule for a grid battery on IEX day-ahead market (DAM)
prices, compares it with a simple rule-based strategy, and measures how the answer changes with
duration, efficiency and degradation cost.

> Work in progress (October–December 2026). Results and charts will appear here once the
> baseline and LP optimiser are complete.

## Getting the price data

IEX's terms of use allow personal, non-commercial use and do not permit redistributing its
data, so **this repository contains no price data and no scraper**. To reproduce the results,
export the data yourself:

1. Open the IEX **Day Ahead Market → Market Snapshot** page.
2. Choose 15-minute time-block granularity and a date range of at most 31 days.
3. Export to Excel or CSV and save the file in `data/raw/` (git ignores this folder).
4. Repeat for each month you need. Overlapping date ranges are fine; identical repeated blocks
   are de-duplicated.
5. Validate and cache:

   ```bash
   uv run python -m battery_arb.data data/raw/*.xlsx
   ```

   This prints a validation report (missing or duplicate blocks, units, price range, blocks at
   the ₹10,000/MWh cap) and writes `data/processed/iex_dam.parquet` if everything passes.

Published charts and tables are derived results. Source: IEX.

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                    # create .venv with locked dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pre-commit install  # run lint/format and the no-data guard on every commit
```

## Limitations (so far)

Revenues are upper bounds: perfect foresight of prices, the battery is a price-taker (its bids
do not move the clearing price), and exchange fees, transmission charges and taxes are ignored.
