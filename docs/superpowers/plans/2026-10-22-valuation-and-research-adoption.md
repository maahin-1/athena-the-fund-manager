# Valuation and research adoption: master plan (2026-10-22)

> **For agentic workers:** this is a program-level plan. Before starting a phase, expand that phase into a step-by-step test-first plan with `superpowers:writing-plans` (one plan per phase, saved beside this file), then execute it with `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Nothing in this file has been built yet.

**Goal:** add intrinsic-value and cost-of-capital analysis for Indian stocks (from Damodaran's published data and method), and take the useful research and evaluation machinery from five open-source repositories, without weakening Athena's rules (point-in-time honesty, the LLM never does arithmetic or trades, ranges not points).

**Architecture:** two new Python packages, `athena.reference` (versioned reference data: Damodaran tables, company-to-industry map, dated inputs) and `athena.valuation` (pure functions: industry context, cost of capital, range DCF, reverse DCF, bank excess-return model, later distress and dilution). Their output is a numbers-only *valuation packet* that the Valuation specialist and the Fundamentals page read, so the number-grounding validator keeps working. A second track adds evaluation, debate and cost-model pieces taken from the repositories, each reimplemented in-house against our own contracts.

**Tech stack:** Python (DuckDB warehouse, existing `athena.fundamentals` and `athena.metrics`), Rust axum route for the page, TypeScript Fundamentals view. New dependency to approve: `xlrd` (reads Damodaran's `.xls` files; BSD licence). `openpyxl` is already present.

## Global constraints

- Personal use only. Damodaran's files are free to use; do not republish them (he does not redistribute his data providers' company-level data).
- Every number is computed by code and carries its inputs, its data date and its source. The LLM may narrate a packet, never compute from it.
- A value is a range (low, base, high) with the assumptions that produced it. No single fair value.
- Reference data is dated. A valuation for a past date must not use a dataset published after that date (flag it, do not silently use it).
- Generic rules over the whole universe, audited across all symbols; no per-ticker fixes (standing owner instruction).
- Tax, risk-free rate, equity risk premium and cost assumptions live in an editable, dated inputs table, never in code.
- Each phase ends with: tests, a whole-universe audit script, a TRD addendum, `graphify update .`, and a Codex review when the phase touches 3 or more files (`jev-codex-review` rules).

## Part A. Damodaran items: decision and scope

Source files are in `.athena/damodaran/` (see `INDEX.md` there).

| # | Item | Decision | What it means here | Depends on | Size |
|---|---|---|---|---|---|
| D1 | India industry tables (beta, margins, multiples, growth, ROE, tax, working capital, capex) | **Adopt now** | Load the 29 `*India.xls` workbooks into `ref_industry_stat`. Show company vs industry in the Valuation packet, Screen, Peers, Fundamentals. Flag industries with fewer than 10 firms and outliers (e.g. P/E over 100). | Phase 0 | M |
| D2 | Company-to-industry map | **Adopt now** | `indname.xlsx` joins on NSE ticker (about 1,330 of our 2,729 screen symbols directly). The rest get an industry through a data-built crosswalk from NSE industry (majority vote of matched companies), marked lower confidence. No hand lists. | D1 | M |
| D3 | Cost-of-capital engine | **Adopt now** | Bottom-up beta (industry unlevered beta, cash-corrected, relevered at the company's market D/E), cost of equity = risk-free + beta × (mature ERP + country premium), synthetic-rating cost of debt from interest cover, market-value weights. Output a range. | D1, D2, rf spike | M |
| D4 | Range DCF and reverse DCF | **Adopt now** | Ginzu-style 10-year FCFF: growth fade, margin convergence, tax convergence, reinvestment by sales-to-capital, loss carry-forward, cost of capital fade. Bear/base/bull from rules over history and industry tables. Reverse DCF solves the growth the price implies. | D3 | L |
| D5 | Excess-return model for banks and NBFCs | **Adopt now** | Value = book value + present value of (ROE − cost of equity) × book value, fading; cost of equity only. Insurers: unsupported (flag), not forced into a wrong model. | D3 | M |
| D6 | Implied ERP and country-premium history as a market gauge | **Adopt later** | First load his published series. Then compute an India implied ERP ourselves from Nifty earnings yield, dividend yield and a stated growth assumption, with error bars. Shown as a gauge beside the FinRL-style turbulence gauge (Part B, F1). | D3, rf series | M |
| D7 | Archived yearly datasets | **Fetch later, only if valuation is backtested** | Pull India, emerging and global archives; choose the latest version published on or before the valuation date. Not needed if D8 produces history. | D1 | S |
| D8 | Rebuild the tables ourselves from NSE filings | **Adopt as the long-term source** | Compute industry betas (own regressions), margins, ROE, ROIC, sales-to-capital, growth and multiples from `statement_lines` using only filings filed by each date. Gives point-in-time history. Damodaran's files remain the seed and the cross-check. | D1, D2, D3 | L |
| D9 | Failure probability, distress, young-company valuation, option overhang | **Adopt last** | Failure probability from synthetic rating and his default-probability table, applied to the DCF as a distress-proceeds blend. Negative-earnings and young-company path using the margin-convergence setup (his `fcffneg.xls`). Option overhang from his `employeeoption.xls` method, with ESOP counts as an input that defaults to zero and is flagged, since XBRL results carry no ESOP data. | D4 | M |

Skipped Damodaran items (unchanged from the report): R&D capitalisation and lease converter (India R&D is small, Ind AS 116 capitalises leases), control, liquidity, synergy, brand-name, ESOP-tax, hedging, commodity, dividend-tax tools, US-only datasets, classes, webcasts and the blog.

## Part B. The five repositories: decision and scope

| # | Source | Item | Decision | What it means here | Size |
|---|---|---|---|---|---|
| R1 | TradingAgents | Bull/bear debate, then a research-manager judge | **Adopt** | `athena.orchestrator.debate`: runs only when TRD 2.15 detects a conflict (opposite signals, both confidence at least 60), two rounds, at most 3 debates per request, anonymised analysts, judge on the strongest tier. Needs the Valuation packet (Phase 3) so debates argue over numbers. | L |
| R2 | TradingAgents | 3-way risk debate (aggressive, conservative, neutral) | **Skip** | Our risk overlay is deterministic by design; an LLM risk panel adds cost and breaks "the LLM never touches the trade". | - |
| R3 | TradingAgents | Typed decisions with a `REVIEW` state; unreadable output is never a silent Hold; numeric fields sanitised | **Adopt** | Extend the verdict and specialist validators in `athena.evaluation.schema`. | S |
| R4 | TradingAgents | Decision log settled against a benchmark after N trading days, with a short lesson | **Adopt (log and settlement now, lessons later)** | `athena.verdicts.settle`: settle stored verdicts against the Nifty over 5/20/60 days from the first close after the decision; store alpha. LLM lessons wait until enough settled rows exist. | M |
| R5 | TradingAgents | Resumable ticker × date backtest grid scored by rating | **Adopt (merge)** | Add a by-rating summary and resume to `athena.evaluation.pit_run`. | S |
| R6 | TradingAgents | Position-aware context (what is held, cash) | **Adopt later** | Part of the personal analyst. | S |
| R7 | TradingAgents | Point-in-time helpers: clamp tool dates, drop undated items, report "unavailable" not "empty" | **Adopt** | `athena.news.window` for the news and Sentiment work. | S |
| R8 | TradingAgents | Social-post screening with typed questions (about the stock? stance?) and a stance count | **Adopt as design reference** | Use their two-question gate thresholds (drop below 0.3 on-topic; count stance only above 0.5) for Sentiment 7b. | S |
| R9 | TradingAgents | Structured Sentiment report (band, score, confidence) | **Adopt** | In 7b. | S |
| R10 | TradingAgents | LangGraph, per-tier providers, checkpoint resume, HTML report, US vendors (EDGAR, FRED, Polymarket, StockTwits) | **Skip** | We have our own router and data; EDGAR is US-only. FRED may be reconsidered only as a US 10-year yield source for the risk-free spike. | - |
| H1 | ai-hedge-fund | Analyst scorecard: hit rate, slugging, per-date rank IC, t-stat | **Adopt** | `athena.evaluation.scorecard`, used by PIT scoring and by the Sentiment test (7c). Same maths serves qlib's IC/ICIR. | M |
| H2 | ai-hedge-fund | Prompt cache keyed by data-snapshot hash | **Adopt** | Cache LLM calls by (specialist, packet hash, model) so an unchanged packet costs nothing. | S |
| H3 | ai-hedge-fund | Hash-chained ledger, idempotent tick, kill switch | **Adopt later (parked with paper trading)** | Keep the design for when paper trading is un-parked. | - |
| H4 | ai-hedge-fund | PEAD model | **Adopt later** | A standardised-unexpected-earnings variant from our quarterly XBRL (no free analyst estimates in India), as a quant-lab signal. | M |
| H5 | ai-hedge-fund | Blind prompts, persona agents, clamp audit trail, strategy YAML | **Skip** | Blinding and the overlay audit trail already exist; personas are done in TRD form. | - |
| F1 | FinRL | Turbulence index (Mahalanobis distance of today's returns against a one-year covariance) | **Adopt** | `athena.metrics.turbulence` over the Nifty 500 (or Nifty 50) constituents from the warehouse. Feeds the risk overlay and the market gauge. | S |
| F2 | FinRL | Reinforcement-learning agents and environments, ensemble selection, data processors, plots | **Skip** | Fragile and overfit-prone; clashes with the validation rules. | - |
| G1 | FinGPT | Labelled sentiment sets (FPB, FiQA) as a check on the relevance and stance gate | **Adopt** | A small evaluation fixture for 7b. | S |
| G2 | FinGPT | Tree-structured retrieval (RAPTOR) over long documents | **Adopt later** | For 6j document summaries. | M |
| G3 | FinGPT | LoRA sentiment models, Forecaster, forex framework, hallucination-debate RAG | **Skip** | GPU-bound, English US news, out of scope; the grounding validator covers hallucination. | - |
| Q1 | qlib | Cost-aware backtest exchange: open/close cost, minimum fee, impact, price-limit blocks, volume cap, lot size | **Adopt** | `athena.backtest.costs` with India charges and circuit bands read from the dated inputs table (rates entered from a contract note, not assumed in code). | M |
| Q2 | qlib | IC, ICIR, rank IC, risk analysis report | **Adopt (merged with H1)** | One scorecard module. | - |
| Q3 | qlib | Expression engine and Alpha158-style factor recipes | **Adopt later** | Extend our formula language with the missing rolling operators (rank, slope, r-square, correlation, quantile) and write the factor set as formulas. | M |
| Q4 | qlib | Model zoo | **Adopt only a linear and a gradient-boosted baseline, later** | Skip the deep-learning models. | M |
| Q5 | qlib | Rolling retraining; top-k with dropout; portfolio optimisers | **Adopt later** | Top-k dropout as a simple turnover-aware rule; optimisers from PyPortfolioOpt or Riskfolio-Lib (already in the TRD), not qlib code. | M |
| Q6 | qlib | RL execution, high-frequency, binary store, collectors, PIT operator, recorder | **Skip** | Not needed; we have our own point-in-time layer and warehouse. | - |

Items with no source to adopt: validation gate (CPCV and probability of backtest overfitting) is planned but unbuilt in all five repos, so it is original work in the quant lab, not in this plan.

## File structure

New:
- `src/athena/reference/__init__.py`, `damodaran.py` (workbook parsers and registry), `tables.py` (DuckDB tables: `ref_industry_stat`, `ref_company_industry`, `ref_country_premium`, `ref_rating_spread`, `ref_default_prob`, `ref_input`), `cli.py` (`python -m athena.reference load|audit`).
- `src/athena/valuation/__init__.py`, `industry.py`, `capital.py`, `dcf.py`, `excess_return.py`, `inputs.py` (company inputs from the stores), `packet.py`, `page.py` (JSON for the terminal), `industry_build.py`, `gauge.py`, later `distress.py`, `young.py`, `dilution.py`.
- `src/athena/evaluation/scorecard.py`, `src/athena/verdicts/settle.py`, `src/athena/orchestrator/debate.py`, `src/athena/backtest/costs.py`, `src/athena/news/window.py`, `src/athena/metrics/turbulence.py`.
- Tests: `tests/test_ref_*.py`, `tests/test_val_*.py`, plus golden fixtures from Damodaran's workbooks (cached cell values) under `tests/golden/damodaran/`.
- Terminal: `terminal/src/server.rs` route `/api/valuation/:symbol` (same spawn pattern as `/api/fund/:symbol`); `terminal/web/src/fundamentals/valuation.ts`.

Modified: `src/athena/agents/valuation.py` (persona and shows list), `src/athena/orchestrator/builders.py`, `src/athena/warehouse/screen.py` (industry-relative columns), `src/athena/fundamentals/page.py` (industry comparison rows), `pyproject.toml` (xlrd), `TRD.md`.

## Phases

### Phase 0. Foundations and spikes (do first)

- [ ] **0.1 Dependency and fixtures.** Approve and add `xlrd`. Copy the golden cells needed (not the whole mirror) into `tests/golden/damodaran/` as small JSON files extracted from the workbooks: India Total Market beta 0.766, cost of equity 0.094, cost of capital 0.083 (USD) and 0.109 (INR); India rating Baa3, default spread 0.0187, total ERP 0.0708, country premium 0.0285; the Ginzu sample valuation output and the synthetic-rating example (coverage 6.20 gives A2/A, company spread 0.00775, cost of debt 0.05865).
- [ ] **0.2 Risk-free spike.** The warehouse holds the Nifty 10-year benchmark G-Sec *price* index but not a yield. Test free yield sources one by one (FBIL benchmark yields, RBI, CCIL, NSE debt pages, Yahoo for the US 10-year as the dollar fallback). Decide, with evidence, which one is stable; fallback is a dated manual input. Write the result into the TRD (this answers the open decision on the India risk-free rate in section 9).
- [ ] **0.3 Inputs table.** `ref_input(key, value, as_of, source, note)` with a getter that returns the latest value on or before a date. Seed: mature ERP 4.23% (his Jan 2026), India country premium 2.85%, tax rate (default 25.17%, the concessional-regime statutory rate: to confirm with the owner; his file uses 30%), risk-free (from 0.2).
- **Gate:** the golden fixtures load; the risk-free decision is written down.

### Phase 1. Industry tables and the company map (D1, D2)

- [ ] **1.1 Parsers.** `damodaran.load_dataset(path)` returns rows (industry, metrics, n_firms) from the standard layout (header row starting "Industry Name"); a registry names the 29 India datasets and their columns. Test against the golden cells.
- [ ] **1.2 Tables and load command.** `python -m athena.reference load --mirror .athena/damodaran` is idempotent, stores `as_of` from each file's "Date updated" cell, and refuses a file it cannot read (fail loud).
- [ ] **1.3 Company map.** Load `indname.xlsx`; exact match on `NSEI:<symbol>`; build the NSE-industry crosswalk by majority vote from the matched set; store `method` and `confidence`; exclude ETFs and funds by `kind`.
- [ ] **1.4 Industry context.** `valuation.industry.lookup(symbol, as_of)` returns the stats, `n_firms`, flags (small sample, outlier, low-confidence mapping, dataset newer than `as_of`).
- [ ] **1.5 Surfaces.** Add `pe_vs_industry`, `pb_vs_industry`, `ev_ebitda_vs_industry`, `margin_vs_industry`, `roe_vs_industry` to the metrics packet; add industry columns to the screen table; add a "vs industry" block to the Fundamentals page.
- [ ] **1.6 Audit.** `python -m athena.reference audit`: coverage over all listed companies with statements (target at least 95% mapped), low-confidence list by industry, share with small-sample industries, and the dataset age.
- **Gate:** tests green; audit numbers recorded in the TRD; Valuation specialist can cite industry-relative figures with grounding intact.

### Phase 2. Cost of capital (D3)

- [ ] **2.1 Loaders** for `ctryprem*.xlsx` (ratings, default spreads, country premiums), `ratings.xls` (interest coverage to synthetic rating, large and small firm tables), the default-probability table.
- [ ] **2.2 `capital.cost_of_capital(inputs)`**: relevered bottom-up beta, cost of equity, synthetic-rating cost of debt, market weights, after-tax; returns base plus a range (mature ERP ± 0.5 point, beta ± one cross-industry standard deviation, risk-free ± 0.5 point; band widths are inputs).
- [ ] **2.3 Company inputs** `valuation.inputs`: market cap (screen), debt including lease liabilities, cash and investments, EBIT, interest, tax, from `statement_lines`; coverage audit across the universe (which companies have every field; which are partial).
- [ ] **2.4 Golden checks.** Reproduce his Ginzu cost-of-capital worksheet for its sample inputs, and the synthetic-rating example, within 0.01 point.
- **Gate:** golden checks pass; coverage audit published; cost of capital shown on the Fundamentals page with its range and every input dated.

### Phase 3. Range DCF, reverse DCF, bank model, page (D4, D5)

- [ ] **3.1 `dcf.run_fcff`.** Pure function, same structure as `fcffsimpleginzu.xlsx` (growth years 1, 2 to 5, linear fade to terminal; margin convergence; tax convergence; sales-to-capital reinvestment; loss carry-forward; cost-of-capital fade; terminal growth capped at the risk-free rate; terminal return on capital default equal to cost of capital). Golden: the workbook's cached output for its sample inputs, value per share within 0.1%.
- [ ] **3.2 Scenarios.** Rules, not hand tuning: base growth blends the company's own compound growth with the industry fundamental growth; target margin from the industry pre-tax operating margin; sales-to-capital from the industry table; bear and bull move growth, margin and cost of capital by stated amounts. Output low, base and high plus a growth × margin sensitivity grid.
- [ ] **3.3 `dcf.reverse_dcf`.** Bisection on 10-year growth (and separately on margin) to match the market price; report "price implies X% against history Y% and industry Z%".
- [ ] **3.4 `excess_return.run`** for layouts `bank` and `finance`: book value per share, normalised ROE, cost of equity from the bank industry beta, fade of excess return; justified P/B. Golden: his financial-firm worked example from the finfirm paper.
- [ ] **3.5 Routing.** Operating companies use the DCF; bank and finance layouts use 3.4; insurers return "unsupported"; loss-making firms return a flag and wait for Phase 5.
- [ ] **3.6 Packet and specialist.** `valuation.packet` builds the numbers-only packet; update the Valuation persona to allow citing a computed range and the implied growth, keeping "do not calculate your own".
- [ ] **3.7 Terminal.** `athena.valuation.page` JSON, route `/api/valuation/:symbol`, a Valuation section on the Fundamentals page (scenario table, sensitivity grid, reverse-DCF sentence, cost of capital, assumptions list), vitest and one Playwright check.
- [ ] **3.8 Audit.** Run all supported companies; report the share valued, the share flagged, the distribution of value-to-price, and a spot check that results are stable when inputs are nudged.
- **Gate:** golden tests, audit, Codex review, live look in the terminal.

### Phase 4. Own industry tables, archives, market gauge (D8, D7, D6, F1)

- [ ] **4.1 `industry_build`** computing, per industry and date, from filings filed by that date: own regression betas (weekly, two to five years, versus the Nifty 500), market D/E, cash share, unlevered betas, margins, ROE, ROIC, sales-to-capital, growth, multiples. Store with history.
- [ ] **4.2 Divergence report** against Damodaran's India values; cutover rule (use ours when an industry has at least 10 firms and full coverage, else his).
- [ ] **4.3 D7 archives** only if 4.1 cannot give enough history: fetch the archived India, emerging and global files; add the "latest version published on or before the date, plus a publication lag" selector.
- [ ] **4.4 `metrics.turbulence`** (F1) and the **implied-ERP gauge** (D6): India implied ERP from index earnings yield, dividend yield and a stated growth assumption, with error bars; a small market-risk panel in the terminal showing both.
- **Gate:** divergence report reviewed by the owner before cutover.

### Phase 5. Distress, failure probability, young companies, dilution (D9)

- [ ] **5.1 `distress`**: failure probability from synthetic rating and the default-probability table; distress proceeds as a share of value; blended value. Golden: his `distress.xls` and the Ginzu failure-rate sheet.
- [ ] **5.2 `young`**: negative-earnings path (start margin, convergence, survival probability, higher cost of capital early); golden: `fcffneg.xls`. Triggered by rule (net loss with revenue growth above a stated threshold), not by name.
- [ ] **5.3 `dilution`**: option overhang by his `employeeoption.xls` method; ESOP counts are an input that defaults to zero and is flagged "not in filings" until the document-extraction work (6e2, 6j) can read them.
- [ ] **5.4** Wire into the DCF routing and the packet; extend the audit.

### Track R. Repository items (can interleave with the phases above)

| Step | Items | Needs | Notes |
|---|---|---|---|
| R-1 Evaluation | H1/Q2 scorecard, R5 by-rating summary, R3 typed decisions and `REVIEW` | nothing | Start any time; small and independent. Golden: hand-computed IC on a fixed table. |
| R-2 Settlement | R4 decision log settled against the Nifty | verdict store | Settle 5, 20 and 60 trading days from the first close after the decision. |
| R-3 Costs | Q1 India cost model and circuit bands | owner-entered rates | Rates and fees are editable dated inputs; defaults left blank until the owner supplies them from a contract note. |
| R-4 Cache | H2 prompt cache | nothing | Key = specialist, model, packet hash. |
| R-5 Debate | R1 debate and judge | Phase 3 | Conflict-triggered only; transcripts stored; judge-stability check from TRD 2.14. |
| R-6 Sentiment | R7 date window, R8 gate design, R9 report, G1 labelled test set | Sentiment 7a | Folded into the existing 7a to 7c work. |
| R-7 Later | H4 PEAD variant, Q3 factor recipes, Q4 baselines, Q5 top-k, G2 tree retrieval, R6 position context, H3 ledger | quant lab / 6j / paper trading | Not scheduled here. |

## Order and dependencies

0 → 1 → 2 → 3 is the critical path (each needs the one before). Track R-1, R-2, R-4 can run beside Phase 1 or 2 because they touch different files. R-5 waits for Phase 3. Phase 4 and 5 follow Phase 3; Phase 4.4 can move earlier if the market gauge is wanted sooner. Sizes: S one module with tests; M a module plus integration and an audit; L a module, user interface and audit.

## Risks

- Damodaran's India sample is large but includes many micro-caps and some thin industries (e.g. 6 regional banks), so the data can be noisy. Mitigation: flags in D1, the divergence report and cutover rule in D8.
- His data is an annual snapshot (Jan 2026). Using it for earlier dates would leak the future. Mitigation: dated lookups, flag, and D8/D7.
- The risk-free source is unresolved (Phase 0.2). Mitigation: dated manual input as the fallback.
- Input coverage: some companies lack debt or lease fields; the DCF returns a partial-coverage flag rather than a guess.
- Taxonomy mismatch: Reliance and other conglomerates sit in one of his industries; the engine should flag diversified companies and later weight betas by business mix.

## Decisions needed from the owner

1. Approve adding `xlrd`.
2. Confirm the tax-rate default (25.17% concessional-regime statutory rate, against his 30% marginal).
3. Allow the Valuation specialist to cite a computed range (changes its current "never state a fair value" rule).
4. Use Damodaran's industry groups for benchmarks (NSE sectors still shown in the interface).
5. Supply brokerage and statutory charge rates from a contract note for the cost model (R-3).
