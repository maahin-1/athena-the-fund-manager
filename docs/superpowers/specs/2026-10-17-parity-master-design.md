# Terminal parity master design (Chart Lab, Fundamentals, Sentiment)

Date: 2026-10-17. Status: **design approved in section 1 by the user; section 2 and the batch list below are for review.** Inputs: [TradingView breakdown](2026-10-17-tradingview-breakdown.md), [Screener breakdown](2026-10-17-screener-breakdown.md). This file records the decisions and the architecture; each batch gets its own detailed plan before any code.

## 1. Goal

Turn the terminal into one place for the technical, the fundamental and the social view of an Indian stock, each in its own tab, with Athena's AI layer on top, using free data only and no usage caps.

Tabs: `Terminal · Chart Lab · Fundamentals · Screen · Research · Sentiment`.

- **Chart Lab:** TradingView-class charting (chart types, indicators, drawings, options and futures, alerts, replay, formula and Python indicators).
- **Fundamentals:** Screener-class company pages (statements, ratios, shareholding, documents, peers, screens, alerts, AI summaries) built from NSE/BSE filings.
- **Sentiment:** relevant public discussion per stock, filtered by Jev, analysed by an agent, displayed and tested before it influences anything.
- **Terminal, Screen, Research:** as now; Screen later folds into Fundamentals; Research gets the risk-profile form.

## 2. Decisions (made with the user, 2026-10-17)

| # | Decision |
|---|---|
| D1 | Interleave the tracks (order in section 9). |
| D2 | Fundamentals holds the **full NSE equity universe**. First run is long, resumable, ordered Nifty 50, then Nifty 500, then the rest. **Cost, measured:** a long-listed company has about 60 filings across both bases (TCS 62, HDFCBANK 58), so fetching everything costs about 2 minutes per company. The default is therefore **consolidated first** (standalone only when a company has no consolidated filing, or on request), the 7 annual filings and the latest 13 quarterly ones, newest first: about 20 fetches, 30 to 40 seconds a company, so Nifty 50 in about 30 minutes, Nifty 500 in about 5 hours and the full market in about 20 hours. Started by a manual command; **no scheduled background job** without explicit approval. A per-company "refresh" button exists. |
| D3 | Statement history (**corrected 2026-10-17 after probing real filings**): NSE XBRL carries the **income statement from FY2019** (quarterly from Oct 2018) but the **balance sheet and cash flow only from FY2023** (March 2023 filings onward; FY2019 to FY2022 results files hold income and segment data only). So: P&L and quarters FY2019+ from XBRL; balance sheet and cash flow FY2023+ from XBRL; FY2019 to FY2022 balance sheet and cash flow first from Yahoo statements, stored with `source = yahoo` and shown as "Yahoo, not an exchange filing" (it covers about four years back and has known quirks), then replaced or extended from annual-report PDFs read by a model and cross-checked against the next year's comparative column (phase 6e2, in B2). Everything older than that is blank until the PDF phase reaches it. |
| D4 | Order-flow: **volume profile from bars now**; **record our own ticks** from the next market session; measure the real tick rate; then decide whether inferred delta and footprint are good enough or another feed is worth checking. Footprint shows "needs tick data" until then. |
| D5 | Custom indicators: **own formula language plus Python indicator files the user places in a local folder.** No Pine. Pine import stays parked. |
| D6 | Commodities: **MCX contracts through Angel** with the F&O instrument work. The 10,000-product claim is dropped. |
| D7 | Sentiment sources v1: **Reddit official API and open forums (ValuePickr and the like) through Scrapling.** X is left out until the user sets a paid-API spend cap. |
| D8 | Sentiment use: **show, store, test against forward returns, then decide.** Never feeds the verdict until a test says it adds something. |
| D9 | **Same layout and same controls as the originals, with more features on top** (user, 2026-10-17: "copy the exact same charting interface and the exact same Screener fundamentals interface, free and paid, but with more features"). That means the same arrangement of toolbars, panels, sections, row labels, tables and tools, built from our own code. It does **not** mean their code, CSS, icons, fonts, wordmarks, logos, colours as a brand, data, or page text: those are ours, and the product keeps its own name. A layout and a feature set can be matched; assets and source cannot be copied. |
| D10 | F&O and index feeds are built against a **fake feed first** and verified live in a market session. (Default, unobjected.) |
| D11 | Parked, unchanged: Zerodha, paper trading, Pine import, TradingView integration, Pine export, Codex review runs, X (until a cap is set). |

**Boundaries that hold for every phase.** Study of third-party products is behaviour and design only. Scraping or mirroring Screener, TradingView, X or Reddit pages is out: Screener's terms forbid copying and mirroring; X and Reddit forbid scraping and put access controls in front of it. Scrapling is used only on sources that publish openly and allow automated reads, with a robots.txt check, rate limits and caching, and **never with its anti-bot bypass or stealth modes.** Secrets stay in the settings file and are never read, printed or named in commands.

## 3. Architecture

### 3.1 Process shape (unchanged, extended)
Rust `athena-terminal` stays the one server: Angel stream, WebSocket hub, read-only DuckDB, route groups. Python stays the engine for ingest, analysis and anything with a library: the existing `athena.dashboard.worker` plus new long jobs run as child processes. The page is TypeScript, one module per tab.

```
terminal/web/src/
  chartlab/      page module + panels (chart engine, indicators, drawings, options)
  fundamentals/  page module (company page, screens, documents, alerts)
  sentiment/     page module
  (shared) feed.ts, links.ts, command.ts, panels/base.ts
terminal/src/
  chartlab_*.rs  routes: indicators calc cache, options, alerts engine
  fund_*.rs      routes /api/fund/*  (read-only DuckDB)
  sent_*.rs      routes /api/sent/*  (read-only DuckDB)
src/athena/
  fundamentals/  ingest, statement parsing (reuses pit.xbrl, pit.nse_results), ratios, documents
  sentiment/     sources, store, relevance, analyst
  indicators/    registry, formula language, python plug-ins
```

### 3.2 Stores (DuckDB files under `.athena/`)
- `fundamentals.duckdb`: `companies`, `filings` (symbol, period end, period type, consolidated or standalone, filing date, XBRL URL, fetch status), `statement_lines` (symbol, period end, basis, statement, tag, value, unit, filing id), `ratios` (statement-based, written at ingest), `shareholding`, `documents` (kind, title, date, URL, local path, text status), `ingest_runs` (resume state, last error, blocked-until).
- `sentiment.duckdb`: `posts` (immutable raw: source, id, url, fetched at, posted at, author hashed, text), `relevance` (post, symbol, verdict, model, version), `analysis` (post, stance, topics, claims, flags, model, version), `daily` (symbol, day, counts, mean stance).
- Existing warehouse is untouched. Python writes, Rust opens read-only, exactly like bars.

### 3.3 Fundamentals specifics
- **Ingest:** `python -m athena.fundamentals.ingest --universe nifty50|nifty500|all --resume [--symbol X]`. Reuses the throttled NSE client (1.5 s gap, back-off 30 s and 90 s, 403 blocks recorded in `ingest_runs.blocked_until` so a rerun does not hammer). One company costs about 30 filing fetches. Progress is a table the page reads, so a long run is visible.
- **Parser:** extend `athena.pit.xbrl` from headline lines to every row Screener shows: P&L, balance sheet (equity capital, reserves, borrowings, other liabilities, fixed assets, CWIP, investments, other assets), cash flow (operating, investing, financing, capex, free cash flow). A separate row layout for banks, NBFCs and insurers. A tag never found is absent; **a figure is never guessed**.
- **Ratios:** one Python module, each ratio's formula written in its docstring and shown on the page: OPM, ROCE, ROE, debtor/inventory/payable days, cash conversion cycle, working-capital days, free cash flow, CFO to EBITDA, compounded growth (10/5/3 years, TTM), payout, interest cover. Price-dependent ones (market cap, P/E, dividend yield, price to book, EV/EBITDA) are a few formulas applied at read time to stored statements and the live price, tested against the same fixtures. Shares outstanding come from XBRL capital and face value, with the shareholding record and Yahoo as cross-checks; the retired NSE `getSymbolData` endpoint is not relied on.
- **Documents:** annual reports, announcements, credit ratings and concall notices as metadata and links first; PDFs cached locally on first open; text extraction and AI summaries in 6j. Scanned or unreadable PDFs are reported as such, never summarised blind.
- **Failure rule:** any fetcher fails soft, names the failed source on the page and keeps the last stored data with its as-of date.

### 3.4 Chart Lab specifics
- **Engine** built on Lightweight Charts as now, with a pane manager, an indicator framework (registry, inputs dialog, styles, legends, per-indicator timeframe override, indicator on indicator), scale modes (auto, log, percent), `ADJ` toggle, compare/overlay, data window, crosshair sync, undo/redo, snapshot, templates.
- **Edge rules written once and tested against a fake live feed**: last-bar update, right margin, session bounds, market-status badge, interval-tied range buttons. This is the cure for the live-candle bug class.
- **Indicator maths** live in TypeScript for drawing and in the Python registry as the reference; each indicator has a test comparing both on the same data.
- **Drawings:** an engine (hit-testing, handles, snapping, persistence per symbol) then tools in tiers; object tree; lock, hide, sync.
- **Options and futures (5f):** NFO, BFO and MCX instrument master; continuous futures `NAME1!` and `NAME2!` built by us from the expiry list, with our own stated roll rule; option chain with Greeks and IV (Angel's greeks endpoint if it answers, local Black-76 otherwise), OI, PCR, volatility curve, payoff builder. Verified against a fake feed first, then in a market session.
- **Alerts (5g):** engine in the Rust server (it holds the live ticks): price, indicator, drawing, watchlist and multi-condition; triggers once, once per bar, once per close, every time; delivery by browser notification and sound, webhook optional.
- **Tick recording (5b):** the server appends every stream update to a local tick table from the next session on; feeds the bar volume profile now and the delta experiment later.

### 3.5 Custom code (5h)
- **Formula language:** a small, side-effect-free expression language that extends the strategy-condition grammar in `athena.strategies`. Series with `x[1]`, built-ins (`sma`, `ema`, `rsi`, `highest`, `lowest`, `crossover`, `if`), `input` declarations, `plot`, `hline`, `fill`, `alert`. Bounded size, nesting and history reach; no loops; validated with the path of any error. The same formula drives drawing, alerts and backtests, and the strategy-builder UI (custom backtest rules) is a form over the same grammar.
- **Backtesting with custom code (decided 2026-10-17, user question).** The existing JSON strategies (`athena.strategies`, version 1) are untouched and keep running exactly as before. The formula language is only a text surface that compiles to the same condition tree, so a formula, an alert and a backtest agree by construction. Version 2 of the JSON adds one operand, `{"custom": "<indicator id>", "params": {...}, "line": "<name>"}`, for a registered Python indicator; version 1 files stay valid. The risk is look-ahead: a Python function that is handed the whole bar array can read future bars, which would void the engine's rule that every value at a bar uses only bars up to that close. So a custom indicator is **only accepted into a backtest after a causality check**: the engine runs it on the full series and on truncated prefixes at sampled bars (including the first, last and random ones) and requires the value at each sampled bar to equal the full-run value; the existing "cut and corrupt later bars" test is applied to it too. A failing indicator is refused with the first bar that differs. The blinding replay (no ticker, shifted dates, rescaled prices) feeds the indicator the blinded bars like any other rule. Every backtest result records the indicator file's hash and settings, so a result can be reproduced or shown stale when the file changes. Runs are in the child process with the same time limit; a timeout fails that run, never the worker.
- **Python indicators:** `.py` files in a local `indicators/` folder, placed by the user. Each is listed with its hash and **must be switched on once**. Run in a **separate child process** with a time limit, given the bars and nothing else, returning series as JSON. **Nothing from the page or a network response can create or edit these files**, and there is no in-page Python editor. It is not a true sandbox on Windows, so the page says so next to the switch.

### 3.6 Sentiment pipeline (7a, 7b)
```
sources (Reddit API, open forums via Scrapling)
  -> posts store (immutable raw, fetch time, URL, author hashed)
  -> dedup and cheap prefilter (company names and cashtags through the instrument resolver)
  -> Jev relevance gate (batch call: relevant to this stock or not, plus junk/promo/bot-like)
  -> analyst agent (language model through the existing provider layer; structured output only)
  -> analysis store (stance, topics, factual claims, flags, model and version)
  -> daily roll-up per stock -> Sentiment tab
```
- **Safety:** every post is untrusted text, as `NewsItem` already says. It is wrapped in delimiters in the prompt, never placed in an instruction position, the analyst has **no tools**, its output must validate against a schema or is discarded, and no link in a post is ever followed.
- **Terms:** Reddit through its official API with credentials in the settings file (never printed), within its data terms (no redistribution, honour deletions); forums fetched politely, cached, robots.txt respected. Author names are stored hashed.
- **Honesty in the UI:** each stock shows counts, source mix, the sample size and how many posts the gate dropped; a stance from a handful of posts says so.
- **Test (7c):** once enough days exist, test the daily stance against forward returns with the same discipline as the technical and Kronos tests; the finding is written up whether it helps or not. Only a positive, out-of-sample result may connect it to the verdict.

### 3.7 AI layer (6i, 6j, 5l)
Runs through the existing live-analysis worker and specialists. The model phrases numbers the code computed and never invents one; every claim shows its figure, period and source; nothing places an order. Items: pros and cons from the ratio engine, five specialists on full statements, change detector, red flags (pledges, promoter selling, auditor changes, receivable days jumping, cash flow below profit, rating downgrades), document summaries and a key-insights table with the quoted passage, natural-language screens and alerts shown as editable structured queries, chart explanations, pattern narration with measured hit-rates, options strategy suggestions (never orders), backtest-from-chart. The risk-profile form joins here so live analysis stops running with no risk limits.

## 4. Testing and verification rules (every batch)
- Tests first for logic: Rust unit tests, vitest for pure page logic, pytest for Python, Playwright against the fake feed for each page.
- Real-data checks run against NSE for a named handful (TCS, HDFCBANK, SBIN, RELIANCE, a bank, an NBFC, an insurer) before a phase is called done; figures are compared with the filing's own PDF or the NSE page for at least one company per phase.
- Market-hours checks are a standing list, run at the next open session: index quotes, F&O and MCX streams, tick rate (`scripts/terminal-market-check.py`), the live-candle bug. A phase that depends on one is marked "built, awaiting live check", never "done".
- Each batch ends with: full Rust, page, browser and Python suites green, graphify rebuild, spec result notes, commit.
- Reviews: no Codex runs until the user lifts that.

## 5. Risks and how the design meets them
| Risk | Meeting it |
|---|---|
| NSE endpoints are unofficial and change (`getSymbolData` already did) | Fail soft, show the source's failure, keep last data, probes in a script so a change is caught early |
| 35 h first run, blocks | Resumable, `blocked_until`, tiered order so Nifty 50 is useful in under an hour |
| Banks and NBFCs differ | Separate row layout and tests with a real bank, NBFC and insurer |
| Ratio definitions differ from Screener's | Formulas written on the page and in tests; differences are explainable |
| Python plug-ins run code | Files the user places, hash, switch on once, child process, time limit, no page-writable path |
| Social text injection and manipulation | Untrusted wrapper, no-tools analyst, schema validation, tests before use, display-only first |
| Scope | Batches in section 9, each independently shippable and each with its own plan |

## 6. What is not in scope
Broker trading, paper trading, Pine, social features of our own, mobile apps, tick history we cannot get, 3.5 million-instrument licences, X until a cap is set.

## 7. Open items carried in
Risk-profile form (6i), strategy-builder UI (5h), live index quotes (5e), tick-rate measurement (market session), confirming the candle fix and layout fit on the user's screen (market session).

## 8. Naming
Working names: Chart Lab, Fundamentals, Sentiment. Product look: own colours and icons, to be settled in the 5a spec (the current light and dark themes are the base).

## 9. Batches
A batch is a group of phases that ends in something usable. Each gets a detailed plan (`writing-plans`) when it starts.

| Batch | Phases | Usable result |
|---|---|---|
| B1 | 5a chart engine, 6a statement store (Nifty 50 proof), 6b ratio engine, 6c company page and Excel export | A real Chart Lab page and a real company page for the Nifty 50 |
| B2 | 6a full-market run, 6d shareholding, 6e documents, 6e2 Yahoo gap-fill then annual-report PDF extraction for FY2019 to FY2022 balance sheets and cash flows | Every listed company has statements, holders and filings, with the pre-FY2023 balance-sheet gap closed and labelled by source |
| B3 | 5e watchlists and live indices, 5b chart types and intervals with bar volume profile and tick recording, 5c indicator library | Full chart toolkit and watchlists |
| B4 | 5d drawing tools, 6f peers and comparison | Annotated charts, peer tables |
| B5 | 5f F&O and MCX, 6g screens and query language | Derivatives view, custom screens |
| B6 | 5g alerts, 6h follow, watchlists, notebook | Alerts across both tracks |
| B7 | 5h formula language, Python indicators, strategy-builder UI | Custom indicators and rules |
| B8 | 7a sentiment sources and store, 7b relevance gate, analyst, Sentiment tab | The Sentiment tab |
| B9 | 6i AI layer with risk-profile form, 6j documents and insights, 5l chart AI, 5i screener, heatmaps and calendars | AI insights everywhere |
| B10 | 5k chart grid, 5j replay, 6k PDF history and shareholder search, 7c sentiment test | Completeness and the sentiment finding |

Each batch's detailed plan lists exact files, tests-first steps and the commit messages, and ends with the market-hours items it queued.

## 10. B1a results (2026-10-17, statement store and ratio engine)

Built and tested: `athena.fundamentals` (mapping, parser, DuckDB store, universe, resumable ingest, ratio engine, text tables), 46 new tests, plus a trimmed-fixture generator and a Yahoo cross-check. Proven on five real companies (TCS, HDFC Bank, Bajaj Finance, Reliance, SBI): 14 to 16 filings each in 5 to 23 seconds (the filings were mostly cached from earlier work; a cold fetch is about 1.5 s per filing).

**Cross-check against Yahoo, annual revenue and net profit, FY2023 and FY2024:** 14 of 20 figures equal to the rupee. The six flagged are definition or restatement differences, not mapping errors: Bajaj Finance revenue (Yahoo's revenue for a lender is net of interest expense, so the script now skips lender and bank revenue), HDFC Bank net profit (Yahoo's FY2023 figure is restated for the merger; ours is as first filed, which is the point-in-time rule, and the FY2024 gap is the minority share), Reliance revenue (ours is revenue from operations as filed, Yahoo's is about 1.5% lower, consistent with a net-of-duty construct; not yet verified against the filing's own note).

**Defects found by reading real numbers, not by the tests, and fixed:** a bank's operating profit came out negative until it used the filed pre-provision profit; free cash flow equalled operating cash flow when a bank filed capex as 0.00; a bank's unreported ratios were stored as zero; the profit reconciliation ignored provisions.

**Open finding, blocks "current" fundamentals: NSE's results endpoints are stale.** Probed 2026-10-17: `corporates-financial-results` (quarterly and annual, with or without a date range) and `results-comparision` return nothing after the **December 2024** quarter for TCS, and every stored company ends at 2024-12-31, while the same site's announcements feed is live through 2026. So the pipeline is correct on what NSE serves, but the latest ten months of filings (FY2025 annual onward) are not reachable through these endpoints. Next step needs a decision: find the endpoint NSE's own results page uses now (observe it with REA on the public results page), or use BSE's results API, or fetch result attachments from the announcements feed.
