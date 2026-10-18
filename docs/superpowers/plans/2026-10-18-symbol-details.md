# Symbol details panel and advanced watchlist view — implementation plan

> Spec: `docs/superpowers/specs/2026-10-18-symbol-details-design.md`. Execute inline, task by task, tests first; commit after each task. Web code in `terminal/web/src`, tests in `terminal/web/test` (vitest) and `terminal/web/e2e` (Playwright, real warehouse, port 8701).

**Goal:** a Details panel (key facts, performance, technicals gauge, seasonals) and market-wide advanced columns in the watchlist.
**Architecture:** pure TypeScript modules for the maths; two small data modules (bars cache, screen store); one panel; one Rust field. No new route.
**Tech:** TypeScript, vitest, Playwright, existing indicator maths in `indicators.ts`, `chartlab/maths.ts`, `chartlab/maths2.ts`, `chartlab/resample.ts`.

## Global constraints
- A missing figure is `null` and shown `—`; never zero, never invented.
- Percent values in the screen are fractions (0.12 = 12%); the bars-based figures here are computed in percent. Convert once, at the edge.
- Drop answers for a symbol that is no longer shown.
- All overlays and the panel itself stay inside the window (the layout e2e audits it).

## Task 1 — performance maths
Files: create `details/perf.ts`, `test/perf.test.ts`.
Interface: `performance(bars: Bar[], last?: number): { key: '1W'|'1M'|'3M'|'6M'|'YTD'|'1Y'|'3Y'|'5Y'; pct: number | null }[]`.
Tests: base picks the last bar on or before the date; 1M across a 31-day month end; YTD base is the previous year's last close; a period before the first bar gives null; `last` overrides the final close; fewer than 2 bars gives all null.

## Task 2 — technicals gauge
Files: create `details/technicals.ts`, `test/technicals.test.ts`.
Interface: `technicals(bars: Bar[]): { oscillators: Vote[]; averages: Vote[]; summary: Summary } | null` with `Vote = { name: string; value: number | null; verdict: 'buy'|'sell'|'neutral' }`, `Summary = { buy; sell; neutral; score; label }` per group and overall; `labelFor(score)`; `timeframeBars(bars, '1D'|'1W'|'1M')`. Returns null under 30 bars.
Tests: one hand-built series per rule that fires buy, one sell, one neutral (RSI, Stoch, CCI, ADX, AO, MOM, MACD, StochRSI, %R, BBP, UO); an average counts only with enough history; score thresholds at ±0.1 and ±0.5; constant series is all neutral; weekly bars shrink the counts' history.

## Task 3 — seasonals
Files: create `details/seasonals.ts`, `test/seasonals.test.ts`.
Interface: `seasonals(bars): { years: { year: number; points: [number, number][] }[]; average: [number, number][]; months: { month: number; avg: number | null; up: number | null; n: number }[] } | null`; null under 2 full years.
Tests: synthetic two-year series with known month returns (avg, hit rate, n); path starts at the prior year-end close; current year is last and partial; the first partial year is skipped.

## Task 4 — data modules
Files: create `details/data.ts` (bars cache: `loadBars(symbol)` 5 min TTL, in-flight dedupe, returns `Bar[] | null`), `screenrows.ts` (`ScreenStore`: `load()`, `get(symbol)`, `subscribe`, TTL 10 min, retry 30 s), `test/screenrows.test.ts`.
Rust: `analysis.rs::known` adds `isin`; extend its test.
Tests: load once for two gets; TTL reload; failure keeps the old rows and retries after 30 s; case-insensitive lookup; subscribers called on load; `known` returns the ISIN.

## Task 5 — Details panel
Files: create `panels/details.ts`; modify `layout.ts` (PanelType `details`, presets A and M, Quote group), `panels/base.ts` (TITLES, AppContext gains `screen: ScreenStore`), `main.ts` (builders, add-panel list, context), `command.ts` (`DETAILS [SYM]`, `DET`), `style.css` (gauge, bars, seasonals, sections).
Behaviour per spec section 7; sections collapsible, state in `PanelState.open?: string[]`; gauge as SVG half dial; seasonals as SVG; stale answers dropped.
Tests: vitest for command parsing and layout validation of the new type; e2e `details.spec.ts` as in spec section 10; extend `layout.spec.ts` views loop (preset A shows Details as a tab).

## Task 6 — advanced watchlist columns
Files: modify `watchlists/model.ts` (COLUMNS, titles, `BASIC_COLUMNS`, `ADVANCED_COLUMNS`), `panels/watchlist.ts` (values from `ctx.screen`, repaint on screen load, Basic/Advanced buttons in the columns popover), `test/watchlists.test.ts`.
Tests: new keys validate and round-trip; unknown keys are dropped; sort values numeric with nulls last; e2e: Advanced preset shows P/E and Mkt cap for SBIN and sorting by P/E reorders.

## Task 7 — docs and close
TRD §2.29 (details and advanced columns, the written gauge rules), master design §18, memory file update, `graphify update .`, final run of vitest, Playwright and cargo test, commit.

## Order and commits
Tasks 1–3 are independent pure modules; 4 before 5 and 6. One commit per task, `feat(web): ...` with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## As built (2026-10-18)

All seven tasks done. Differences from the plan: the Details panel sits under the watchlist in layout A (420 px, its own group, so layout A has 6 groups) rather than as a tab of Quote, because the Quote group is too short for it; it is a tab of the watchlist group in the narrow layout M. `AppContext` gained `market` (ScreenStore) and `history` (BarsCache). The command line also takes `DETAILS NIFTY 50` and `NIFTY 50 DETAILS`. The Hull average has almost no lag, so on a wobbling rise it can vote against the trend; the tests assert the lagging averages only. Counts at the end: vitest 209, Playwright 56, cargo 109.
