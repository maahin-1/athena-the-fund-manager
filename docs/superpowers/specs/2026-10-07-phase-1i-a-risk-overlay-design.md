# Phase 1i-a — Risk overlay and investor profile: design

Date: 7 Oct 2026. Status: written for review; Plan 1i-a implements it. Builds on Phases 0d (metrics engine), 1c (orchestrator blend) and 1d (dashboard). Phase 1i also contains 1i-b (golden test sets) and 1i-c (blend calibration); each gets its own spec and plan after this one.

## Goal

After the specialists have spoken, check the stock against the person's own risk limits and, when a hard limit is breached, stop the verdict from reading as "go ahead". The check is a deterministic rule, not a language model: every number in it is computed in code, compared with a limit the person can see and change, and shown with the reason. It follows `TRD.md` §2.7 and `PRD.md` FR-10: a limit breach is always surfaced, even when every specialist says buy, and the overlay has override authority.

## Non-goals

No stored portfolio (holdings are given per request), no sector or factor limits (the NSE master lists carry no sector), no portfolio-level VaR from the holdings' own price histories (a later step: it needs one download per holding), no rebalancing advice, no order sizing beyond "this breaches your maximum position", no horizon or goals in the profile (no check would use them), and no change to what any specialist sees or says. Mutual funds and bonds are out of scope as before.

## Inputs, all plain data

**Profile** (`RiskProfile`, frozen, JSON round trip like a strategy). A name and one `Limit(warn, hard)` pair for each measure below. Three presets ship; every number can be edited:

| Measure | What it is | conservative | moderate | aggressive |
| --- | --- | --- | --- | --- |
| `volatility` | annualised volatility of daily returns, fraction | 0.25 / 0.35 | 0.35 / 0.50 | 0.50 / 0.70 |
| `drawdown` | worst peak-to-trough fall in the window, as a positive fraction | 0.20 / 0.30 | 0.30 / 0.45 | 0.45 / 0.60 |
| `var_95` | 1-day historical VaR at 95%, positive fraction of value | 0.020 / 0.030 | 0.030 / 0.045 | 0.045 / 0.065 |
| `cvar_95` | 1-day historical CVaR (expected shortfall) at 95% | 0.030 / 0.045 | 0.045 / 0.065 | 0.065 / 0.090 |
| `position` | weight of this stock in the portfolio after the intended buy | 0.05 / 0.10 | 0.10 / 0.15 | 0.15 / 0.25 |
| `concentration` | Herfindahl index of the portfolio weights after the buy | 0.15 / 0.25 | 0.20 / 0.30 | 0.30 / 0.45 |
| `liquidity` | intended amount as a fraction of the stock's typical daily traded value | 0.01 / 0.03 | 0.02 / 0.05 | 0.05 / 0.10 |

These defaults are starting points, not calibrated to any data; the report says so. A limit is breached at or above its `hard` value, warned at or above its `warn` value. Validation: every number finite and above zero, `warn` below `hard`, a measure may be switched off (`null`), unknown keys refused with the path of the wrong part (the same style as the strategy parser).

**Holdings** (`Holding(symbol, value)`): what the person owns now, in rupees, read from a CSV with the columns `symbol,value` (header required, one row per holding, duplicate symbols summed). Optional.

**Intent**: an `amount` in rupees the person is thinking of putting into the stock. Optional; without it, the checks that need an amount (`position`, `concentration`, `liquidity`) are reported as "not checked: no amount given", never silently skipped.

## What it computes

All from the instrument's own price history, which the analysis has already fetched (about 1 year of daily bars; the metrics packet's window of 252 returns):

- `volatility`, `drawdown`: the metrics engine's `annualized_volatility` and `max_drawdown`, computed by the overlay on that window (drawdown sign flipped to a positive size).
- `var_95`, `cvar_95`: new pure functions in `metrics/stats.py`, historical simulation on the same window of daily returns: VaR is minus the 5th percentile of the returns (numpy's default linear interpolation), CVaR is minus the mean of the returns at or below that percentile. Tested against hand-computed values. The overlay computes them itself from the stock's bars; they are not added to the metrics packet, so the overlay needs no market series.
- `liquidity`: the median of `close x volume` over the last 20 bars; the check is `amount / median traded value`. Zero or missing volume is reported as "not checked: no volume data".
- `position`: `(existing value of this symbol + amount) / (total holdings + amount)`.
- `concentration`: sum of squared weights across all holdings with the buy applied. The finding's text names the largest holding and its share.
- Beta is not part of the overlay: the risk panel already shows it.

A figure that cannot be computed (too little history) is a finding with status `unchecked` and the reason, so the report never implies a clean bill of health from missing data.

## Findings and the override rule

`Finding(check, status, value, warn, hard, message)` with status `ok`, `warn`, `breach` or `unchecked`; the message is one plain sentence with the numbers ("Volatility 48% a year is above your hard limit of 35%").

`apply_overlay(verdict, findings) -> verdict` (pure):

1. Every `breach` and `warn` is put at the front of `key_risks` (breaches first, ahead of the specialists' own risks), even when the verdict does not change.
2. If any finding is a `breach` and the specialists' verdict is `Buy` or `Overweight`, the final verdict becomes `Hold`, the original is kept in `pre_overlay_verdict`, and `key_risks` starts with "Held back by your risk limits: ...".
3. `Hold`, `Underweight` and `Sell` never change: a risk limit may stop a purchase but never stops a sale. Warnings never change a verdict.
4. The findings go in the verdict as a plain list so the dashboard and a report can draw them; the verdict contract gains the optional keys `pre_overlay_verdict` and `risk_findings` (the schema validator learns them).

## Wiring

- `athena.risk_overlay` (new package, no I/O): `model` (profile, limit, holding, finding, presets), `parse` (profile and holdings parsing with `ProfileError`, a `ValueError`), `checks` (the findings), `apply` (the override).
- The orchestrator takes an optional `overlay` (profile, holdings, amount); after the blend it computes the findings from the instrument's bars (the same shared price download the specialists and charts use) and applies the override. With no profile given, nothing changes: today's behaviour is the default.
- CLI: `--profile NAME|FILE.json` (default none), `--holdings FILE.csv`, `--amount RUPEES`. The text report gains a "Risk overlay" block.
- Dashboard: a sidebar section "Risk profile" (preset picker, the limits as number boxes, a box to paste the holdings CSV or a file upload, an amount box); the findings and the pre-overlay verdict travel inside `view.verdict`, so `DashboardView` itself is unchanged; the verdict line shows "Hold (held back from Buy)" with the reason, and a "Risk overlay" panel lists each check with a mark, the value and the limit. Nothing is stored by the app: the profile and holdings live in the session or in the person's own files, and every function takes them as arguments, so a hosted version can pass them per request.

## Honest limits, stated in the report

- Historical VaR and CVaR use about one year of daily returns; they describe the past and say nothing about a crash outside that window.
- There are no sector, issuer-group or factor limits.
- Holdings values and the amount are what the person types; the app does not verify them.
- Default limits are uncalibrated starting points.
- A stylized analytical framework, not financial advice.

## Testing

- Unit: VaR/CVaR against hand-computed values (including a known 5% tail), HHI and weights, each check at and just below its limit, an unchecked case for each check, profile validation with the path of every wrong part, the CSV parser (bad header, negative, duplicate symbol, non-number), `apply_overlay` for every verdict and status combination (a breach never changes a Sell; a warning never changes anything; the original is preserved).
- Integration: an orchestrator run with a fake specialist set that all say bullish and a breached limit gives `Hold` with `pre_overlay_verdict "Buy"`; with no profile the output is byte-for-byte the old output.
- Dashboard: the sidebar edits flow into the view (AppTest); a bad CSV or profile shows a message, not a traceback (the lesson of 1h-c: arbitrary input is parsed with a catch-all that becomes a clear error).
- Mutation checks by hand, all must be caught; a live check on real stocks (a calm large cap passes every check on the moderate preset; a volatile small cap breaches volatility).
- `TRD.md` §2.7 and §2.1 and `PRD.md` FR-10 updated.

## Review fixes

- The stock figures are measured on clean prices adjusted for splits and bonuses (`athena.backtest.adjust`, the same overnight-break detection as the backtest); bars whose close or volume is not a finite number, or whose close is zero or below, are dropped; the adjustment sentence is added to the overlay note.
- A figure that is not a finite number is "not checked: the price history has gaps", never within the limits; historical VaR and CVaR of a series with a missing value are nan, never a zero loss.
- Fewer than 126 daily returns leave volatility, drawdown, VaR and CVaR not checked; 126 to 251 returns add "(measured over the last N daily returns, less than a year)" to those findings.
- When the price download fails, the price checks are listed as not checked with the reason and the money checks (position, concentration) still run and can hold a Buy back; the note says the limits could not be fully checked.
- Holdings and the amount are capped at 1e15 rupees; a total that cannot be added up leaves position and concentration not checked. Holdings text is capped at 1,000,000 characters and 5000 rows, holdings files at 1 MB and profile files at 100 KB.
- Dashboard: an uploaded file wins over pasted text and says so; the holdings help asks for NSE tickers; switching off every check asks for one; a sidebar problem shows even before a ticker is typed.
- The verdict contract checks each risk finding (check, status, message; status ok, warn, breach or unchecked) and that a `pre_overlay_verdict` is Buy or Overweight with the verdict Hold.
