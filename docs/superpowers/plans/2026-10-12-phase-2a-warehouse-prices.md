# Phase 2a: warehouse prices and universe

Spec: docs/superpowers/specs/2026-10-12-market-warehouse-design.md (stage 2a).

**Goal:** a persistent DuckDB file holding every stock and ETF's daily bars, loaded from the exchange's archive files, resumable, with gaps visible, and a command that updates it after each close.

## Files
- Create `src/athena/warehouse/__init__.py`, `warehouse.py` (tables, `Warehouse`), `ingest.py` (ready day, price ingest, universe), `build.py` (command).
- Modify `src/athena/pit/bhavcopy.py`: `BhavcopyStore.day(day, remember_missing=True)`; when False a missing file is neither cached nor an empty marker.
- Tests: `tests/test_warehouse.py`.
- Docs: TRD section 2.20, changelog; graph rebuild.

## Tasks
1. **Store tweak.** `remember_missing=False` leaves no marker; a later call asks again. Test.
2. **Warehouse tables and reads.** `Warehouse(path)`, `upsert_day`, `mark_day`, `bars`, `days`, `gaps(first, last)`, `status`. Tests with `:memory:`.
3. **Ingest.** `ready_through(now)`, `ingest_prices(warehouse, store, since, until, now)`, `ingest_universe(warehouse, equity_master, etf_master)`. Tests with a fake store.
4. **Command.** `python -m athena.warehouse.build [--since YYYY-MM-DD] [--status]`, default two years back; prints progress and ends with the status. Real run on the cached days, then the missing recent days.
5. **Check and docs.** Compare warehouse bars with the PIT logs' source files for TCS, KOTAKBANK; TRD, changelog, graph; commit.
