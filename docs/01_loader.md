# How the price loader works (spec F1)

Code: `src/battery_arb/data/iex.py` (parsing) and `src/battery_arb/data/validate.py` (checks).
Tests: `tests/test_data.py`. Run: `uv run python -m battery_arb.data data/raw/*.xlsx`.

## The shape of the problem

An IEX market snapshot export is a *report*, not a dataset: title rows, a header row somewhere
below them, the date only on each day's first row, time blocks as text (`00:00 - 00:15`), numbers
with thousands separators (`10,000.00`), and maybe a footer. The optimiser needs a *table*: one
row per 15-minute block, a real timestamp, numeric prices in ₹/MWh, nothing missing.

```
file(s) ─read_raw─▶ grid of strings ─normalise─▶ tidy rows ─load_exports─▶ stacked & sorted
                                                                      │
                                         check_prices ◀───────────────┘
                                              │
                         clean frame indexed by timestamp + ValidationReport
```

## Parsing, step by step (`iex.py`)

1. **Read everything as text.** `dtype=str`, so pandas can't half-convert a column (it would turn
   `3512` into a number but leave `10,000.00` as a string). We convert deliberately later.
   CSVs go through Python's `csv` module, not `pd.read_csv`: pandas fixes the column count from the
   first line, so a one-cell title row above an 8-column table crashes it. (The tests caught this.)
2. **Find the header row** (`_find_header_row`): the first row with a cell starting "Date" and a
   cell containing "MCP". No hard-coded "skip 4 rows", which would break silently if IEX adds a
   line to the title block.
3. **Map columns by keyword** (`COLUMN_PATTERNS`): `MCP (Rs/MWh) *` → `mcp_rs_mwh`, and so on.
   Units live in the column name so nobody has to remember them. Two columns matching one pattern
   is an error, not a guess.
4. **Forward-fill the date.** In Excel the date cell is merged across the day's 96 rows; exported,
   only the first row has it. `ffill` copies it down.
5. **Parse the block** (`_parse_block`): take the *start* time, minutes ÷ 15 + 1, so
   `00:00 - 00:15` → 1 and `23:45 - 24:00` → 96. Anything that isn't a valid block (blank rows,
   "Total", a start time that isn't on a 15-minute boundary) becomes `None` and the row is dropped.
6. **Parse the date with explicit formats, day first.** `03-09-2026` is 3 September. Letting pandas
   guess can silently read it as 9 March for days ≤ 12, which is the worst kind of bug: no error,
   plausible numbers.
7. **Numbers**: strip commas, `to_numeric(errors="coerce")`: anything unparseable becomes NaN, which
   validation then reports (never silently filled).
8. **Timestamp** = date + (block − 1) × 15 min, localised to `Asia/Kolkata`. IST has no daylight
   saving, so every day has exactly 96 blocks; with a European dataset you'd get 92 or 100 on clock
   change days, and the "96 per day" check would need to know that.

## Validation (`validate.py`)

| Check | Why it matters for the optimiser |
|---|---|
| Exact duplicates dropped | Overlapping 31-day exports repeat days; harmless once removed |
| Conflicting duplicates → error | Same block, different price: which is right? Never pick silently |
| Missing blocks → error | A gap shifts every later block in a 96-step model, and the SoC balance links them all |
| Non-numeric MCP → error | NaN in an LP objective is a crash at best, garbage at worst |
| Median < ₹50 → error | Real DAM medians are in the thousands; a ₹/kWh file would make revenue 1,000× too small, yet pass a range check |
| Outside ₹0–10,000/MWh → error | The regulator's DAM bid range since April 2023; outside it means a parse error or the wrong market (HP-DAM goes to ₹20,000) |
| Blocks at the cap → counted | Not an error, but at the cap the true scarcity price is hidden. This number feeds the price-cap discussion |

`check_prices` returns the frame *and* a report even on errors, so you can look at the bad rows.
`load_prices` is the strict version: it raises `ValidationError` with the summary.

Order matters: units are checked before range, because a ₹/kWh series (all values 2–10) sits
comfortably inside ₹0–10,000 and would otherwise pass.

## Testing without real data

IEX data can't be committed, so `tests/conftest.py` writes look-alike files: title rows, blank
dates, text blocks, comma numbers, a "Total" footer. Each test breaks one thing (delete a block,
duplicate a day with a different price, write prices in ₹/kWh) and asserts the loader notices.
When the real September export arrives we compare it against these assumptions and fix whatever
differs. The synthetic format is a guess until then.

## Check yourself

1. Why read as strings first instead of letting pandas infer types?
2. What goes wrong if dates are parsed month-first? Why wouldn't you notice?
3. Why is "missing block" an error rather than something to interpolate here?
   (Hint: where *should* a gap-filling decision live, and who should make it?)
4. A ₹/kWh file passes the ₹0–10,000 range check. Which check catches it, and why does order matter?
5. Why keep `source_file` on every row?
