# B1b Fundamentals Company Page Implementation Plan

> **For agentic workers:** execute task by task (superpowers:executing-plans). Steps use checkbox syntax.

**Goal:** A Fundamentals tab in the terminal that shows one company the way Screener does (header, key ratios, price chart, quarters, P&L, balance sheet, cash flow, ratios, growth tables, basis toggle) from the stored statements, with an Excel export, and the Python/Rust plumbing behind it.

**Architecture:** `athena.fundamentals.page` builds one JSON document per company from `.athena/fundamentals.duckdb` (read-only, short retry because the ingest writes in bursts) plus a price the Rust server passes in from the warehouse `screen` table. The Rust server validates the symbol, reads the price, spawns the Python module once per request (no worker: a request is a second or two and rare), and returns its JSON; the export route spawns `athena.fundamentals.export` and streams the `.xlsx`. The page module `terminal/web/src/fundamentals/` renders the JSON; its logic (tables, growth, number format) is pure functions with vitest tests.

**Tech Stack:** Python 3.12 + duckdb + openpyxl (new), Rust axum (existing), TypeScript + vite + lightweight-charts (existing), vitest, Playwright.

## Global Constraints

- Figures are stored in rupees; the page shows crores (÷10,000,000), per-share and percent as is (master design D9: same layout and controls as Screener's company page, own code, own colours, own name).
- A missing figure is `null` in JSON and `-` on screen, never zero.
- Every ratio carries its formula text (`ratios.py` docstrings) so a difference from another site is explainable.
- Read-only access to the fundamentals file from the server; Python never runs while the page waits more than 30 s.
- Symbol check on the server: same rule as `/api/bars` (`symbol_ok`); basis is `consolidated` or `standalone` only.
- No schedule, no background job; the ingest stays a hand-run command.
- Tests: no live network; the page builder is tested on fixtures (`tests/fixtures/xbrl/*.xml` through `parse_rows` into an in-memory store).

## File Structure

- Create `src/athena/fundamentals/page.py` — `build_page(store, symbol, basis, price) -> dict`, CLI `python -m athena.fundamentals.page SYMBOL [--basis] [--price]` printing JSON.
- Create `src/athena/fundamentals/export.py` — `write_workbook(page: dict, path)`, CLI `python -m athena.fundamentals.export SYMBOL --out FILE [--basis] [--price]`.
- Modify `src/athena/fundamentals/store.py` — `open_read(path, tries=5, pause=0.4)` helper that retries a locked file.
- Modify `pyproject.toml` — add `openpyxl`.
- Create `terminal/src/fundamentals.rs` — `FundamentalsJobs` (spawn page/export, price lookup, limits) + unit tests with a fake Python.
- Modify `terminal/src/server.rs`, `terminal/src/main.rs` — routes `/api/fund/:symbol`, `/api/fund/:symbol/export`, `/api/fund-status`.
- Create `terminal/web/src/fundamentals/model.ts` — types of the JSON; `format.ts` — number formats; `tables.ts` — pure row builders; `view.ts` — `FundamentalsView` (DOM); `chartbox.ts` — price chart with range buttons.
- Modify `terminal/web/src/main.ts`, `command.ts`, `style.css` — tab, `FUND SYM`, F6 cycle.
- Tests: `tests/test_fund_page.py`, `tests/test_fund_export.py`, `terminal/web/test/fund.test.ts`, `terminal/web/e2e/fund.spec.ts`.

## JSON contract (`build_page`)

```json
{
  "symbol": "TCS", "name": "Tata Consultancy Services", "layout": "indas", "basis": "consolidated", "bases": ["consolidated", "standalone"],
  "as_of": "2026-09-30", "price": 3200.5, "price_note": "warehouse close 2026-10-16",
  "key": [{"label": "Market Cap", "value": 1156000.0, "unit": "Rs Cr", "how": "price x shares"}],
  "quarters": {"ends": ["2025-12-31"], "rows": [{"key": "sales", "label": "Sales", "values": [64000.0]}]},
  "annual":  {"ends": ["2024-03-31"], "income": [...], "balance": [...], "cashflow": [...], "banking": [...]},
  "ratios":  {"ends": [...], "rows": [{"key": "roce", "label": "ROCE %", "values": [..], "how": "EBIT / (total assets - current liabilities)"}]},
  "growth":  [{"title": "Compounded Sales Growth", "rows": [{"label": "10 Years", "value": 9.1}, {"label": "5 Years", "value": 11.2}, {"label": "3 Years", "value": 7.0}, {"label": "TTM", "value": 6.1}]}],
  "coverage": {"periods": 30, "first": "2019-03-31", "last": "2026-09-30"},
  "gaps": ["balance sheet and cash flow before FY2023 are not held yet"]
}
```

Each row's `values` is parallel to its table's `ends`; `null` where missing; rupee rows are in crores already, `eps`, `opm`, `tax_pct` and ratios as is.

---

### Task 1: The page builder

**Files:** Create `src/athena/fundamentals/page.py`; Modify `src/athena/fundamentals/store.py`; Test `tests/test_fund_page.py`.

**Interfaces:**
- Consumes: `FundamentalsStore.lines/company/bases/coverage`, `ratios.annual/quarterly/ttm/compounded/year_ratios/market_ratios/crore`, rows labels from `show.INCOME_ROWS/BALANCE_ROWS/CASH_ROWS`.
- Produces: `build_page(store, symbol, basis, price: float | None) -> dict` (contract above); `open_read(path) -> FundamentalsStore`.

- [ ] **Step 1: tests** (`tests/test_fund_page.py`): load `tcs_fy2024.xml` and `tcs_fy2020.xml` through `parse_rows` + `save_statements` into `:memory:`; assert (a) `build_page(store,"TCS","consolidated",None)["annual"]["ends"]` holds FY2020 and FY2024 year ends sorted oldest first, (b) `income` row `sales` value for FY2024 equals `ratios.crore(raw)` of the stored value, (c) no price → `key` has no market cap and `price` is `None`, with price 3200 and shares known → market cap row present and `how` text mentions "shares", (d) a missing figure is `None` not 0, (e) `growth` has a "Compounded Sales Growth" table whose 3-year row is `None` when the year three earlier is not held, (f) unknown symbol → `{"symbol":..., "error": "no statements stored"}`, (g) `bases` lists both bases when both stored, (h) `gaps` mentions FY2023 when balance sheets start there, (i) `open_read` on a locked file retries then raises `AthenaError` (use a fake connect).
- [ ] **Step 2: run, see fail** `.venv/Scripts/python.exe -m pytest tests/test_fund_page.py -q` → ImportError.
- [ ] **Step 3: implement** `page.py` and `open_read`. Row order and labels reuse `show.py` tuples; quarters table uses `ratios.quarterly(income)` newest 13 ends; `key` block: market cap, current price, P/E (with `pe_period`), P/B, book value, ROE and ROCE of the latest year, debt to equity, dividend yield is left out (dividend data arrives with 6d), 52w high/low left out (price block from the warehouse comes in B1c); growth tables for Sales, Profit, Stock price CAGR is B1c; ROE averages over 10/5/3 years and last year from `year_ratios`.
- [ ] **Step 4: pass** same command. **Step 5: commit** `feat(fundamentals): the company page document`.

### Task 2: Excel export

**Files:** Create `src/athena/fundamentals/export.py`; Modify `pyproject.toml`; Test `tests/test_fund_export.py`.

**Interfaces:** Produces `write_workbook(page: dict, path: str | Path) -> None` — sheets: `Summary` (key figures + formulas), `Quarters`, `Profit & Loss`, `Balance Sheet`, `Cash Flow`, `Ratios`, one header row of period ends, one row per line, numbers as numbers (not text), a note row with the source ("NSE XBRL filings") and generated date.

- [ ] **Step 1: tests**: build a page dict from the fixture store (reuse a helper in `tests/test_fund_page.py`), write to `tmp_path`, reopen with `openpyxl.load_workbook`, assert sheet names, that `Profit & Loss` cell B1 is the first period end, that a Sales cell is a float equal to the page value, a missing value is an empty cell, and the file opens without a Python-side error for a bank page too.
- [ ] **Step 2: fail; Step 3: implement; Step 4: pass.** `pip install openpyxl` into `.venv` and add to `pyproject.toml` dependencies.
- [ ] **Step 5: CLI** `python -m athena.fundamentals.export TCS --out x.xlsx` builds the page with `open_read` and writes; test it by running the CLI against the real store for TCS after Task 1 passes.
- [ ] **Step 6: commit** `feat(fundamentals): Excel export of a company page`.

### Task 3: Server routes

**Files:** Create `terminal/src/fundamentals.rs`; Modify `terminal/src/server.rs`, `terminal/src/main.rs`, `terminal/src/config.rs` if the Python path is held there.

**Interfaces:**
- Produces `FundamentalsJobs::new(python: String, store: PathBuf, warehouse: PathBuf)`; `page(&self, symbol, basis) -> Result<Value, FundError>`; `export(&self, symbol, basis) -> Result<Vec<u8>, FundError>`; `status(&self) -> Result<Value, FundError>` (coverage count per basis from the store, read via the Python module `athena.fundamentals.show --coverage`, or directly with duckdb in Rust since Rust already links duckdb: use Rust). `FundError::{BadSymbol, BadBasis, NoData, Busy, Failed(String)}`.
- Server routes: `GET /api/fund/:symbol?basis=consolidated` → 200 JSON, 400 bad symbol/basis, 404 `{"error":"no statements stored"}`, 503 busy; `GET /api/fund/:symbol/export?basis=` → `.xlsx` with `content-disposition: attachment; filename="SYMBOL-basis.xlsx"`; `GET /api/fund-status` → `{"companies": n, "periods": n, "latest": "..."}`.

- [ ] **Step 1: tests in `fundamentals.rs`** with a stand-in Python (`-c` script printing a fixed JSON, as `analyse_worker` tests do, skipped when the venv is absent): good page passes through; `{"error":"no statements stored"}` maps to `NoData`; non-zero exit maps to `Failed`; a 30-second limit kills a hung child (use a 1 s limit in test); bad symbol/basis rejected before spawning; price is read from a temp warehouse `screen` table and passed as `--price`. Server tests in `server.rs`: routes check origin/host like the others and return 400 for `symbol=a;b`.
- [ ] **Step 2: fail; Step 3: implement** (`tokio::process::Command`, `kill_on_drop`, `timeout`, stdout JSON parse, stderr discarded; price lookup with the read-only warehouse connection as in `analysis.rs`); **Step 4: `cargo test` passes** (`cd terminal && cargo test`). **Step 5: commit** `feat(terminal): fundamentals page and export routes`.

### Task 4: Page logic in TypeScript

**Files:** Create `terminal/web/src/fundamentals/{model,format,tables}.ts`; Test `terminal/web/test/fund.test.ts`.

**Interfaces:** `fmtCrore(v: number | null): string` (Indian grouping 12,34,567), `fmtPlain(v, digits)`, `fmtPct(v)`, `growthClass(v)` (`up`/`down`/`flat`), `periodLabel(iso)` (`Mar 2024`), `tableFrom(section): {head: string[]; rows: {label; cells: string[]; how?: string; cls: string[]}[]}`, `quarterTable`, `ratioTable`, `keyBlocks(page)`.

- [ ] **Step 1: tests**: `fmtCrore(1234567.8)` → `12,34,568`, `null` → `-`; `periodLabel("2024-03-31")` → `Mar 2024`; `tableFrom` keeps the null cells as `-` and attaches `how` to ratio rows; `growthClass(-0.4)` → `down`.
- [ ] **Step 2: fail; Step 3: implement; Step 4: pass `npm test`; Step 5: commit** `feat(terminal): fundamentals table logic`.

### Task 5: The Fundamentals view and tab

**Files:** Create `terminal/web/src/fundamentals/{view,chartbox}.ts`; Modify `main.ts`, `command.ts`, `style.css`, `index.html` only if needed.

**Interfaces:** `class FundamentalsView { element; show(symbol?): void; hide(): void }` constructed with `AppContext`; `command.ts` gains `{kind:'fund', symbol: string | null}` for `FUND`, `FUND SBIN`, `SBIN FUND`; `View` gains `'fund'`; tab button `Fundamentals`; F6 cycles terminal → fund → research → screen.

Layout (same order and controls as the reference page, own styling): header (name, symbol, live price and change from the feed, basis toggle `Consolidated | Standalone`, `Export to Excel`, link buttons to Chart / Research), key-ratio grid, price chart box (`1M 6M 1Y 3Y 5Y 10Y Max` buttons, daily bars from `/api/bars`, line series), then sections Quarterly Results, Profit & Loss, Balance Sheet, Cash Flows, Ratios, Compounded growth tables, and a small "data coverage" line with the gaps text. Section nav row scrolls to each section. Sticky first column, horizontal scroll for wide tables, the formula appears as a tooltip on ratio rows.

- [ ] **Step 1: unit test** (jsdom, in `fund.test.ts`): feed a page JSON, `view.render(page)` yields a header with the symbol, a basis toggle with the active basis marked, 5 section containers, a `-` cell for a null, and the export link `href` equal to `/api/fund/TCS/export?basis=consolidated`.
- [ ] **Step 2: fail; Step 3: implement view + chart box + wire into `main.ts`/`command.ts`; Step 4: `npm test`, `npm run build` pass; Step 5: commit** `feat(terminal): the Fundamentals tab`.

### Task 6: End-to-end check and wrap-up

- [ ] **Step 1:** rebuild web (`npm run build`), `cargo build`, restart the terminal on 8700 (stop background shell first), open the Fundamentals tab on TCS and HDFCBANK in Playwright MCP, take a screenshot, read the numbers against `python -m athena.fundamentals.show TCS`.
- [ ] **Step 2: `terminal/web/e2e/fund.spec.ts`**: with the terminal on the fake feed and a seeded temporary fundamentals file, assert the page shows Sales for a seeded year, toggling basis changes the table, export download has `.xlsx` and starts with `PK`.
- [ ] **Step 3: docs**: TRD 2.21 gets the page and routes, master design §10 gets B1b results, this plan gets an "As built" note.
- [ ] **Step 4: graphify rebuild** (`graphify update .`), **commit** `docs: B1b results`.
