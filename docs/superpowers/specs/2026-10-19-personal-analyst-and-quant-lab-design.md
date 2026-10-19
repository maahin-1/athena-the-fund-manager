# Personal analyst and quant lab (draft roadmap)

Date 2026-10-19. Status: draft for discussion. **Not scheduled: starts after the current system is built** (Chart Lab, Fundamentals, Options, sentiment, intraday index charts). Two tracks that share one engine.

## 0. Principles (apply to both tracks)

1. **Ranges, never points.** Every forward-looking number is a distribution with the bad cases shown, and the displayed expectation is deliberately on the low side (haircuts below).
2. **No look-ahead.** Every test and every simulation uses only what was known at the time, the same rule the replay and the point-in-time harness already follow.
3. **Our own results bind us.** Phases 2e, 2h and 2i found no skill in the analysts or Kronos over a trivial baseline. So the advisor is built on diversification, cost, risk matching and valuation context. Analyst verdicts are at most a small, labelled tilt, shown with their measured track record. A model enters only if it beats a stated baseline walk-forward (section B5).
4. **Free data only.** What is not free (tick history, L2 history, Greeks) is recorded by us from now on, or left out and said so.
5. **Say what it is.** Every page says which numbers are history, which are simulated and which are assumptions, and who it is not advice for.

## A. Personal analyst

### A1. Profile
Goal (named, with an amount needed), amount to invest now and per month, horizon (years), risk appetite as a **measured** thing (a short questionnaire plus a loss test: "how much fall in a year would make you sell?"), constraints (liquid only, no leverage, single-stock cap, sector cap, exclusions, tax regime), existing holdings (optional import), income stability and an emergency-fund check. Stored locally; editable; every recommendation records the profile version it was made for.

### A2. Engines
| Engine | What | Data |
|---|---|---|
| Return model | Rolling-window returns for the same horizon from history (index, ETF, stock), block bootstrap and Monte Carlo (fat tails, volatility clustering) to get a distribution of outcomes. | Warehouse bars (stocks and ETFs from 2023, indices from 2018) |
| Haircuts | Lower the expected return for inflation, rupee decay, high starting valuation (index P/E against its history), fees and taxes, and a margin for model error. Each haircut is shown and switchable. | Index P/E (index_bars), FRED/RBI series, TER |
| Risk model | Volatility, drawdown, time to recover, probability of missing the goal, worst year. | Same |
| Portfolio builder | Core ETFs and index funds by risk budget (volatility parity, minimum variance with caps, drawdown limit), gold and debt-like ETFs as ballast, optional satellite stocks from screens (quality, value, low volatility), all inside the constraints. | ETF universe, fundamentals, screen |
| Scenario overlay | Stress tests applied to the proposed portfolio: rupee fall, oil spike, rate move, war or sanction shock, one sector down 25%. Exposure, not prediction. | Prices, sector mapping, hand-written scenario definitions |
| Explainer | Why each holding fits this profile, what could go wrong, what would make us change the advice, when to review. | All of the above |

### A3. Output
A plan page: allocation, three outcome bands for the horizon (low, middle, high; the headline is the low-middle), probability of reaching the goal and the monthly amount that would raise it, the worst year seen in history and in simulation, costs, a list of holdings with reasons, and a stress table. A change log when the plan is regenerated.

### A4. Gates
- **Regulatory:** personalised stock or ETF recommendations to other people are regulated investment advice in India (SEBI Investment Adviser and Research Analyst rules). **Owner use only** (decided 2026-10-19: mostly personal). Multi-user would be a separate decision with its own wording, consent and record-keeping, not a switch.
- **Validation:** before a plan is shown, the engine is back-tested walk-forward: would the method, run at each past date for a typical profile, have produced allocations whose realised outcome fell inside the stated bands at about the stated rate (calibration), and how did it do against a plain index-plus-gold mix.

## B. Quant lab

### B1. Data layer
- **F&O bhavcopy history** (NSE end-of-day file per trading day: price, open interest, volume per contract), a resumable rate-limited fill like the cash one. Feeds continuous futures (`NAME1!`), option strategy tests and implied-volatility history reconstructed with our Black-76.
- **Order-flow and option-chain recording** from the live stream: depth snapshots (five levels), trades as the stream gives them, option chain snapshots per minute for a chosen set (index options, a few stocks), stored compactly (Parquet or DuckDB) with the exchange time. History starts the day recording starts. Both approaches are used: end-of-day files for depth in time, recording for microstructure.
- Pipeline rules: as-of stamps, gap reports, never fill silently.

### B2. Simulation engine (shared)
Monte Carlo paths (GBM for reference, bootstrap and GARCH-style for realism, jumps), option payoff distributions, portfolio paths. One module used by the advisor (goal odds), the Options tab (payoff odds), and the strategy lab. Calibration tests: simulated volatility, drawdown and tail frequency against history.

### B3. Options strategy lab
Multi-leg tests on bhavcopy history: short straddle/strangle, iron condor, covered future, calendar, ratio spreads; entry rules (IV rank, days to expiry, event calendar), management rules (stop, target, roll), real costs (brokerage, STT on exercise, slippage by liquidity), margin approximated and labelled. Output: equity curve, drawdown, distribution of trade results, tail loss, comparison with buy-and-hold and with simply selling nothing. Daily bars only; intraday strategies wait for recorded chains.

### B4. Order-flow research
From recorded depth and trades: imbalance, spread, absorption, large-order detection, cumulative delta (needs trade side inference), and their relation to the next minutes' moves. A research notebook-style tool first; a signal enters a strategy only through B5.

### B5. Model research harness
One rule for every predictive idea (ML, forecasting, order-flow or option signals): walk-forward, no look-ahead, costs included, measured against stated baselines (buy-and-hold, the trend rule, always-the-most-common-outcome), multiple-testing awareness (number of variants tried is recorded and counted against the result), a minimum sample, and the result stored whether good or bad. A model that fails stays out of the advisor.

### B6. Backtest framework changes
The existing engine (single-instrument, price rules, formula language) stays. Needed: a **portfolio layer** (rebalance rules, weights, cash, costs, taxes), a **multi-leg derivatives layer** (positions with expiry, assignment, margin approximation) and the shared simulator. Same no-look-ahead rules; results in one format so the page can compare any two.

## C. Order and dependencies

1. B1 bhavcopy fill and continuous futures (also finishes Task H of the derivatives plan).
2. B2 simulation engine, calibrated.
3. A engines on B2 and warehouse data (return model, haircuts, risk model), advisor for the owner: ETF and index-fund core first; stock satellites later and clearly labelled.
4. B3 options lab; B1 recording starts as soon as the live-market checks pass, so history accumulates while other work goes on.
5. B4 and B5 as data allows; A scenario overlay and explainer.
6. Multi-user only after the regulatory decision.

Each step has its own spec and plan, and each ends with tests, a stated limit, and an honest list of what is not known.

## D. Decisions (2026-10-19, owner)
- **Regulatory route: mostly personal use.** Built for the owner. No multi-user, accounts or sharing features are built; if that ever changes it is a new decision with its own spec (registration, consent, record-keeping). Pages still say "not advice" and show their assumptions.
- **Tax and brokerage assumptions: included**, as editable inputs with dated defaults, applied everywhere a return or a backtest is shown: brokerage per order and per segment (equity delivery, intraday, F&O), STT, exchange transaction charges, SEBI fee, stamp duty, GST on charges, DP charges, slippage by liquidity; capital gains tax by holding period and instrument (short and long term, the exemption limit, F&O as business income at the user's slab), surcharge and cess, loss set-off and carry-forward. Each rate carries its effective date and source so a change is a data edit, not a code change, and a backtest says which rate set it used. Results always show gross and net.
- **Recording length: decided by testing, not fixed.** Record from the first session and test different time frames against each other: the same research question (for example "does order-book imbalance predict the next 1, 5 and 15 minutes") run on the first 2, 4, 8 and 12 weeks of recording and on rolling windows, the learning curve of the result (effect size and its confidence interval against weeks recorded) showing when it settles, and an out-of-sample check on later weeks. The rule: an order-flow finding is trusted only when it is stable across windows and holds on data recorded after it was found. The same A/B design compares bar sizes and prediction horizons (1, 5, 15 minute, hourly, daily) so the time frame itself is a tested choice. The number of windows and variants tried is recorded and counted against the result (B5).
- Macro scenarios stay hand-written first (a feed later, only if it proves useful).
