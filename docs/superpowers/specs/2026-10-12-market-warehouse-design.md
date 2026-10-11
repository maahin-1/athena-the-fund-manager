# Market warehouse: a database built each night, read by everything else

**Why:** today every request re-downloads what it needs into an in-memory store, so it is slow, NSE blocks it, and two requests a minute apart can disagree. The user asked (2026-10-12) for a database that holds the whole market (stocks and ETFs), is updated once a day after the close, and is the thing charts, screens and analysts read. The TRD and PRD are references and are updated to match.

**Principles**
- Data views need no model call. Charts, screens and tables read the database only.
- Every row carries where it came from and when it was loaded. A stale or failed night shows as stale, never as fresh.
- Prices are stored as the exchange printed them; adjustment for splits and bonuses happens when read, from recorded events (Phase 1j rule).
- A model verdict is made on demand, or for stocks whose data changed, and its confidence is dampened until it has been calibrated against outcomes (Phase 1k found 70+ confidence was less accurate than average).
- Same DuckDB file for everything (`.athena/warehouse.duckdb`); the existing `DataStore` records table is reused for statements and events.

**Evidence the design rests on (measured 2026-10-11)**
- One archive file holds every symbol for a day (about 1,850 EQ-series rows of 2,624); a day costs one 1.5 s request and is final once published.
- Archive files include ETFs in the EQ series (ITBEES, NEXT50, NETF), so stocks and ETFs are told apart by the NSE master lists.
- Yahoo statements: about 2.6 s per stock, 16 of 21 sampled stocks fully usable; failures were a renamed ticker, one unexplained, and ETFs (which have no statements).
- A model call takes about 7 s, so verdicts for the whole market (1,850 x 4 analysts) would take about 15 hours one after another: not a nightly job.

## Stages
- **2a Prices and universe (this plan).** `athena.warehouse`: the `bars`, `days` and `universe` tables, a nightly ingest of the archive files (resumable, gaps visible), a status report, a command `python -m athena.warehouse.build`.
- **2b Events and statements.** Corporate-action events (NSE's bulk list, Yahoo splits) stored per symbol; a statements refresh queue (oldest first, with a daily budget, and new-results first); adjusted bars and price flags read from the warehouse; packets precomputed with as-of stamps.
- **2c Views.** A market screen (filter and sort every stock and ETF on price, return, volume, P/E, P/B, ROE, trend) and charts read from the warehouse; the dashboard says how old the data is.
- **2d Verdicts.** On demand and for changed stocks (new results, large move); analyst confidence dampened by the measured accuracy until calibrated.

## Stage 2a design
- **Tables.**
  - `bars(symbol, day, open, high, low, close, volume, series, source, loaded_at)`, key (symbol, day).
  - `days(day, status, rows, loaded_at)`, status one of `loaded`, `no_session`, `pending`. A weekday with no row is a gap.
  - `universe(symbol, kind, name, isin, updated_at)`, kind `stock`, `etf` or `other`.
- **Which day is ready.** The exchange publishes the day's file in the evening. The current day is tried only after 19:00 IST. A missing file within the last three calendar days is recorded `pending` and not remembered as a holiday on disk, so a late file is picked up on the next run; older misses are holidays or weekends and are remembered.
- **Idempotent.** Re-running a day replaces its rows. A run stopped halfway resumes from `days`.
- **Universe.** Symbols in the NSE equity master are `stock`; in the ETF master `etf`; a symbol traded but in neither is `other` and shown as such rather than guessed.
- **Reading.** `Warehouse.bars(symbol, since, until)` returns `Bar`s (source `bhavcopy`) so existing code that takes a bars fetcher can take the warehouse. `status()` gives the last loaded day, rows, gaps and universe sizes.
- **Not in 2a.** Adjusted prices, statements, packets, screens, verdicts.
