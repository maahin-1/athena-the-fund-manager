# Symbol succession Implementation Plan

> **For agentic workers:** execute task by task, tests first. Steps use checkbox syntax.

**Goal:** every ticker's prices, search and statements follow the exchange's rename record; no ticker is named in code.

**Architecture:** a `symbol_renames` table built in Python from NSE's rename file and our bars; Python `adjusted_history`, Rust raw bars, Rust search and fundamentals history read it.

**Tech Stack:** Python 3.13 + DuckDB, Rust (axum, duckdb), pytest, cargo test, Playwright.

## Global Constraints

- No ticker symbol appears in the production code of the join, the search aliases or the fundamentals table.
- `joinable`: both have bars, old ends before new begins, `0 <= gap_days <= 14`, `|step| <= 0.25`.
- `retired`: no old bar after `change_day` + 7 days.
- At most 5 hops, never a loop.
- A failed refresh never clears the table.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task 1: the record (`athena/warehouse/renames.py`, table and Warehouse methods)

**Files:** create `src/athena/warehouse/renames.py`, `tests/test_warehouse_renames.py`; modify `src/athena/warehouse/warehouse.py` (table, `replace_renames`, `renames`, `predecessors`), `src/athena/warehouse/build.py` (step).

- [ ] Tests: `classify` gives joinable for adjacent bars with a small step; not joinable for a long gap, a big step, no bars under one name, overlapping bars; `retired` for a symbol that stopped, not for one still trading; `event` text from a demerger action near the date; `predecessors(symbol)` returns oldest first through a chain, stops at a loop and at 5 hops, ignores a non-joinable row when asked for joinable only.
- [ ] Implement `Span`, `classify`, `build_rows`, `refresh_renames(warehouse, fetch, now)`; table `symbol_renames`; Warehouse methods; CLI; `renames` build step.
- [ ] Run `pytest tests/test_warehouse_renames.py tests/test_warehouse*.py -q`.
- [ ] Commit.

### Task 2: prices in Python

**Files:** modify `src/athena/warehouse/history.py`, `src/athena/warehouse/chart_bars.py`; test `tests/test_warehouse_joined_history.py`.

- [ ] Test: a rename with a 1:5 split action after it gives joined adjusted bars with no step at the join and old volumes scaled; the actions listed under both symbols are applied once; a symbol with no predecessor is unchanged; a non-joinable predecessor is not joined and `chart_bars` says nothing was joined.
- [ ] Implement joined raw fetch and merged actions in `adjusted_history`; `chart_bars` returns `joined: [{symbol, until, note}]`.
- [ ] Run the tests. Commit.

### Task 3: prices in Rust

**Files:** modify `terminal/src/bars.rs`, `terminal/src/server.rs`.

- [ ] Replace `PREDECESSORS`, `predecessor`, `join_scale`, `Bars::with_predecessor` with `chain(conn, symbol)` reading `symbol_renames` (joinable rows; absent table means no chain) and `daily_bars` joining the chain on the raw path; `Adjusted` gains `joined`; the response note names the joined symbols.
- [ ] Tests: a raw join over a temp warehouse with the table; no table gives the symbol's own bars; a limit smaller than the symbol's own bars does not read the old ones; the note.
- [ ] `cargo test`. Commit.

### Task 4: search aliases

**Files:** modify `terminal/src/search.rs`.

- [ ] Replace `ALIASES` with the `retired` rows of the table for symbols in the universe; test on a temp warehouse that an old symbol finds the new one and that a symbol that is still listed does not become an alias.
- [ ] `cargo test`. Commit.

### Task 5: statements and filings

**Files:** modify `src/athena/fundamentals/history.py` (the view), tests `tests/test_fund_page.py` unchanged for explicit tables, new `tests/test_fund_succession.py`.

- [ ] Replace the hand-kept dict with a read-through view of the `retired` rows (generic note with company, date and event wording); empty when the warehouse or table is missing.
- [ ] Tests: the view answers `.get(new)` with the old symbol chain, no rows when the table is missing, a reused symbol is not a predecessor.
- [ ] Run fundamentals tests. Commit.

### Task 6: every ticker

**Files:** create `scripts/succession_audit.py`; docs.

- [ ] Script: refresh the table (or use it), then for every stock in the universe compare the joined and unjoined series and print: counts joined and not joined with reasons, and any joined series with a move beyond 40% in a day that the unjoined series did not have.
- [ ] Run it against the real warehouse and the live server; fix what it finds.
- [ ] e2e: replace the three-name test with one that reads the table through the API for the symbols it lists.
- [ ] TRD section, master design, memory, `graphify update .`, full suites. Commit.
