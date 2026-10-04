# Agentic Multi-Asset Hedge Fund — Product Requirements Document (PRD)

Sep 26, 2026 · @maahin · Revised Oct 3, 2026 (gap closure — see Revision history)

## 1. Vision & problem statement

**Vision.** A single input — a stock ticker, a bond/debt identifier, a mutual fund, or an ETF — returns an institutional-grade investment evaluation produced by a coordinated team of specialist AI agents, output as a unified verdict, a full multi-desk report, and a Bloomberg-terminal-style visual dashboard, with a paper-trading module to test any resulting strategy before real capital is committed. The system is portable: it must run on any capable agent runtime, not one vendor's tooling, so its value survives platform changes.

**Problem.** Individual investors and small funds have no equivalent of an institutional research desk: no equity analyst, credit analyst, risk manager, and portfolio manager working a name together and reconciling disagreement into one accountable call. A 180-page institutional finance prompt guide (11 analyst desks, \~88 evaluation frameworks: Graham/Lynch/Buffett-style equity research, M&A valuation, macro risk, earnings intelligence, portfolio strategy, quant trading, endowment and sovereign-wealth allocation models, ESG, and fixed income/credit) supplies the individual expert frameworks, but explicitly does not specify how those experts communicate, has zero dedicated coverage of mutual funds or ETFs as their own asset class, and includes no paper-trading or execution layer. A companion audit of the open-source agentic-trading ecosystem (12 candidate repositories plus further search) confirmed that gap is real industry-wide, not just in the guide: no mature, actively-maintained open-source project does real mutual-fund or ETF analysis end to end, and only one of the investigated repos (TauricResearch/TradingAgents) implements a genuine multi-agent conflict-resolution protocol. This PRD and its companion TRD define the product that closes both gaps.

## 2. Goals, success metrics & target user

**Target user.** A single independent investor (the product owner) who currently evaluates stocks, mutual funds, ETFs, and debt instruments manually and wants an agentic research desk plus a safe environment to test strategies before committing real capital. Not built for a regulated advisory business, though its compliance conventions (see Non-goals) are written as if it were, to keep the discipline honest.

**Goals**

| Goal | Metric |
| --- | --- |
| Cover all four target asset classes | Stocks, bonds/debt, mutual funds, ETFs all return an evaluation — never a refusal. Where free data cannot support a metric, the evaluation says so (`data_coverage: partial`, with the missing inputs listed) instead of omitting the metric silently or inventing a figure |
| Honest coverage | Every evaluation states what data it ran on: a coverage label (full / partial / insufficient), the missing inputs, and an as-of timestamp per dataset. Data older than its staleness limit produces a refusal that names the dataset, not a quiet verdict on old numbers |
| Resolve specialist disagreement, not just report it | Every verdict where two specialists conflicted shows a debate transcript and a judge ruling, never a silent average, for cases flagged as needing judgment |
| Terminal-quality visual output | Every equity/ETF evaluation includes a technical chart panel, and every options-eligible instrument includes a Greeks/IV panel, without manual chart-building |
| Safe strategy testing | Any agent-derived strategy can be run against historical data (backtest) and against live-but-simulated markets (paper trade) before any real order is placed |
| Verifiable output | Every specialist passes an automated check that each number in its reasoning appears in the data it was given, and has a golden evaluation set it is scored against |
| Platform portability | The specialist prompts and schemas are usable by a different agent runtime without rewriting them, verified by at least one successful run outside the original build environment |

**Non-metric goals.** This explicitly does not target beating a market-cap-weighted benchmark, generating a specific annualized return, or replacing professional financial advice — see Non-goals.

## 3. Scope & phased rollout

| Phase | Adds | Rationale |
| --- | --- | --- |
| 0 | Foundation: local data store with batch loaders (AMFI expense ratios and holdings, index total-return series, NSE/BSE bond records); live and batch data adapters that declare coverage and freshness; deterministic instrument resolver; metrics package skeleton; evaluation harness skeleton | The gap review (Oct 2026) found specialists specified with no free data source for several of their inputs, and no defined way to route an ambiguous input. Everything later depends on knowing what data exists and on routing being unambiguous |
| 1 | Stocks: orchestrator, equity specialists, technical/visualization layer, stock backtest on free NSE/Yahoo data | Best-templated path in both the finance guide and the open-source research — proves the architecture end to end fastest. Equity valuation runs in a reduced ratio-only mode where free financial statements are missing |
| 2 | Debt/bonds: credit and duration specialists, starting with government securities and listed corporate bonds (price, yield, rating, issue terms) | Also well-templated by the finance guide's Fixed Income desk; bond mechanics (coupons, duration) still modeled as generic price series at this stage. Covenant analysis only where offer-document data is obtained |
| 3 | Mutual funds & ETFs: two new specialist agents built from scratch, on top of a code-computed metrics engine (alpha/beta, tracking error, style drift, overlap) | Confirmed the weakest-covered asset classes anywhere in the open-source ecosystem — original build, not assembly |
| 4 | Full debate-then-judge arbitration (2-round debate, single judge; a multi-vendor judge panel only if evaluation shows single-judge instability); options/volatility specialist and its dashboard panel | Closes the conflict-resolution gap and adds the terminal-style options view |
| 5 | Live/paper broker integration (Zerodha, Groww) | Turns agent verdicts into actual (paper, then optionally live) orders |

**In scope for v1 (end of Phase 5):** stocks, bonds/debt, mutual funds, ETFs; unified + full-report + dashboard output; paper trading; India-market data and execution (NSE/BSE, Zerodha, Groww) alongside global free sources (Yahoo Finance, FRED). **Free data sources only** — no paid data vendor in v1 (decision, Oct 3, 2026).

**Out of scope for v1:** cryptocurrency, commodities trading, real estate, private equity, structured products, live automated order placement without a human confirmation step (see Security & Compliance in the TRD for the preview/confirm-token requirement), any paid market-data subscription.

## 4. Core features & functional requirements

**FR-1: Input & classification.** User enters a ticker, fund name/code, ISIN, or bond identifier. System classifies asset type and routes to the correct specialist subset (TRD §3 routing table). *Acceptance:* all four asset-class inputs are correctly classified and routed without the user specifying the type manually; an ambiguous or low-confidence input returns a candidate list for the user to choose from, never a silent guess and never an evaluation of the wrong asset class.

**FR-2: Specialist evaluation.** Each routed specialist (see TRD §2 for the full roster) produces an independent, schema-conformant verdict (signal, confidence, reasoning, data coverage) grounded only in the data it was given. *Acceptance:* every specialist's output validates against its schema; reasoning cites specific figures, not generic language, and an automated check confirms each cited figure appears in the input data; the output states its data coverage and lists any missing inputs.

**FR-3: Conflict detection & arbitration.** When two specialists issue opposite signals and both are at or above a minimum confidence (starting default 60, tunable), the orchestrator runs a bounded debate (2 rounds) between them and a separate judge agent rules, per the debate-then-judge protocol (TRD §1/§2). The judge sees specialists under anonymous labels so ruling cannot lean on persona names or speaking order. Risk-limit breaches are enforced by the risk overlay as a rule and are not debated. *Acceptance:* a debate transcript and a judge ruling are shown whenever conflict is flagged; the judge never defaults to Hold/neutral purely because both sides argued; the ruling does not flip when the order of specialists is swapped.

**FR-4: Unified verdict.** The orchestrator emits one line: verdict, conviction score, key risks. *Acceptance:* traceable back to the specific specialist(s) and, where applicable, debate that produced it.

**FR-5: Full multi-desk report.** Every specialist's own reasoning is shown underneath the unified verdict, not only the winning side. *Acceptance:* a user can see what the losing side argued and why the judge ruled against it.

**FR-6: Visual dashboard.** Candlestick + moving averages + Bollinger Bands + volume for any equity/ETF; options Greeks/IV surface/decay curve for any options-eligible instrument; portfolio-level risk/holdings-overlap view. *Acceptance:* dashboard is interactive and re-queryable per instrument, not a static image.

**FR-7: Mutual fund evaluation.** Expense-ratio drag, alpha/beta net of fees vs. category benchmark, style drift, holdings overlap against the user's existing positions, and manager tenure where a source is available. *Acceptance:* a fund evaluation surfaces at least one metric no other asset class's specialists produce (expense drag). Holdings overlap is monthly-granularity (disclosure cadence) and says so. Manager tenure is optional in v1 because no free source was found; when absent it is listed as missing, not guessed.

**FR-8: ETF evaluation.** Tracking error vs. stated index, liquidity/spread, creation-redemption health, index methodology quality, tax efficiency. *Acceptance:* tracking-error figure is computed, not asserted qualitatively; it is produced for ETFs whose index total-return series is available and is marked missing otherwise. Creation-redemption health uses a proxy (premium/discount to indicative NAV) if a free source is confirmed, otherwise it is marked unavailable.

**FR-9: Debt/bond evaluation.** Covenant strength, spread decomposition, distressed-recovery waterfall, duration/curve positioning, per the finance guide's Fixed Income & Credit desk. *Acceptance:* a covenant quality rating (strong/standard/weak/red flag) is always produced when indenture data is available; when it is not, the evaluation is `data_coverage: partial` with covenants listed as missing. Phase 2 covers government securities and listed corporate bonds with price, yield, rating, and issue terms.

**FR-10: Risk & compliance overlay.** Portfolio-level VaR, drawdown, and position-size/concentration checks applied uniformly across all four asset classes, capable of overriding an individual specialist's enthusiasm. *Acceptance:* a risk-limit breach is surfaced even when every specialist individually says buy.

**FR-11: Backtesting.** Any specialist-derived signal can be run against historical data with point-in-time correctness (no lookahead), "blinded" of identifying tickers/dates. *Acceptance:* Sharpe, drawdown, and benchmark-relative return are reported per backtest run.

**FR-12: Paper trading.** A simulated broker executes orders derived from agent verdicts against live or near-live prices, carrying a persistent book, with a single toggle to switch to a real (but still confirmation-gated) broker later. *Acceptance:* a paper position, once opened, persists and marks to market across sessions.

**FR-13: Guarded live execution.** Any order that would touch real money requires an explicit human confirmation step (preview, then a repeated call with a confirm token) — never a fully autonomous real-money trade. *Acceptance:* no code path exists that places a live order without that round-trip.

**FR-14: Data coverage & freshness.** Every evaluation carries per-dataset as-of timestamps and a coverage label, and records which source in a fallback chain served each dataset. The orchestrator refuses a verdict on data older than that dataset's staleness limit (TRD §5). Adapter health is checked daily so a broken source degrades visibly. *Acceptance:* a stale-data fixture produces a refusal naming the dataset and its age; a coverage-gap fixture produces a `partial` evaluation listing the missing inputs; a simulated adapter outage is caught by the daily health check and logged.

**FR-15: Evaluation harness.** Each specialist, the instrument resolver, and the orchestrator ship with automated evaluation. *Acceptance:* a golden set of 20–30 instruments per asset class with expected signal ranges (not exact verdicts); a labeled set of 100–200 instruments for the resolver; schema and number-grounding checks run on every specialist output; a judge order-swap test; a run reports pass/fail per component.

**User stories (representative).** "As the investor, I paste a ticker and get one clear verdict I can act on today, with the full reasoning underneath if I want to check it." · "As the investor, I ask about a mutual fund I hold and learn what it's actually costing me net of fees, which nothing else I use tells me." · "As the investor, I see my equity and risk agents disagree, and I get to see them argue it out rather than a shrug." · "As the investor, I test a strategy on paper for a month before I risk real capital on it." · "As the investor, I can tell at a glance which parts of an evaluation ran on full data and which ran on partial data, so I know how far to trust it."

## 5. Non-goals, assumptions & business-level risks

**Non-goals.** Not a registered investment adviser or a substitute for one — every output should carry base-case-estimate language and risk caveats, never "price target" or "guaranteed return" framing, following the finance guide's own compliance convention. Not targeting a specific benchmark-beating return. Not a fully autonomous trading bot: no path exists from an agent's verdict to a live order without an explicit human confirmation step. Not covering crypto, commodities, real estate, or private equity in v1. Not buying data: v1 runs on free sources only.

**Assumptions.** The user has or can obtain API credentials for Zerodha Kite Connect and/or Groww for live/paper execution; free data sources (NSE/BSE scraping libraries, Yahoo Finance) remain accessible and are treated as unreliable enough to need fallback chains, not a single source of truth; the user accepts that mutual-fund and ETF analysis is being built from scratch with no mature open-source precedent, so early versions of those two specialists carry more risk of rough edges than the equity/debt specialists. **Free-data consequences the user accepts (Oct 3, 2026):** equity valuation runs in a reduced ratio-only mode for many names because free financial statements are patchy; mutual-fund holdings overlap is monthly-granularity; mutual-fund manager tenure and ETF creation-redemption detail may be unavailable; bond covenant analysis depends on offer documents that are not available through any free API found; every such limit is shown in the output as partial coverage rather than hidden.

**Business-level risks.** Regulatory: outputs resembling investment advice to a wider audience than the single named user would raise registration questions — keep it single-user/personal-use until reviewed. Data-source fragility: NSE-scraping libraries break when NSE changes endpoints, which would silently degrade coverage if not monitored (addressed by FR-14's daily health check). Coverage risk: free-only data means some specialists run degraded; the product's value depends on the coverage labels being honest, so a specialist that fakes completeness is a worse failure than one that reports partial. License: a candidate backtesting dependency (backtrader) is GPLv3 and unmaintained since 2023, which is a legal decision point before it becomes load-bearing (see TRD §4). Model risk: any forecasting signal (e.g. a candlestick foundation model) is trained predominantly on crypto/global markets and needs validation before being trusted on Indian equities, bonds, or fund NAVs specifically.

## 6. Glossary

| Term | Meaning |
| --- | --- |
| Orchestrator / Portfolio Manager agent | Routes input, runs arbitration, emits the final verdict |
| Specialist agent | A single-persona analyst (e.g. Equity Valuation, Credit Analysis, Mutual Fund Analyst) producing one schema-conformant verdict |
| Debate-then-judge | Conflict-resolution pattern: disagreeing specialists argue in bounded rounds, a separate judge agent rules |
| Conviction-weighted blend | Simpler fan-in of many low-stakes numeric signals via a weighted average, used where full debate is unnecessary |
| Tracking error | How far an ETF's return deviates from its stated index |
| Backtest blinding | Stripping tickers/dates from historical inputs so an agent can't lean on memorized outcomes |
| Paper trading | Simulated order execution against real or near-real prices, no real capital at risk |
| DataAdapter / BrokerAdapter | The common interface every data source or broker is wrapped behind |
| BCE | Base Case Estimate — the finance guide's required non-promissory language for any forward-looking figure |
| Data coverage | Label on every specialist output and evaluation: `full` (all inputs present), `partial` (some missing, conclusion qualified), `insufficient` (specialist abstains) |
| Batch loader | Component that ingests periodic file-based disclosures (e.g. AMFI expense ratios and holdings) into the local data store, stamping each record with its as-of date and source |
| Metrics engine | Deterministic code that computes quantitative metrics (alpha/beta, tracking error, expense drag, overlap); specialists interpret its output and never compute figures themselves |
| Instrument resolver | Component that maps raw user input to an asset class and identifier, deterministic first, with an ask-the-user path for ambiguity |
| Golden set | Hand-labeled instruments with expected signal ranges used to score each specialist |
| Number-grounding check | Automated test that every figure cited in a specialist's reasoning appears in the data it was given |

## Revision history

- **Sep 26, 2026** — Initial PRD. (Original preserved at `docs/archive/PRD-2026-09-26.md`.)
- **Oct 3, 2026** — Gap closure after review against the TRD and a check of free data sources: added Phase 0 (foundation); free-data-only decision and its consequences; coverage and verifiability goals; FR-14 (data coverage & freshness) and FR-15 (evaluation harness); tightened FR-1, FR-2, FR-3, FR-7, FR-8, FR-9 acceptance criteria; added glossary terms.
