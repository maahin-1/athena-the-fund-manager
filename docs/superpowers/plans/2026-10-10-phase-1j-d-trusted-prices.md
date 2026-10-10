# Phase 1j-d: Retire the Guess-Based Adjuster, Gate the Verdict on Price Reliability — Plan

> **For agentic workers:** execute task by task; steps use checkbox syntax.

**Goal:** No price is adjusted from a guess anywhere, and a purchase call is not made on a price history that contains a break nothing explains.

**Architecture:** The checked-price layer (`CheckedHistory`, Phase 1j-a/b) is the only place prices are adjusted, and it adjusts only against a recorded event. `backtest.adjust.adjust_for_splits` (adjusts any overnight break ≥40%/70%, snapping to a common ratio, with no record) is removed from the risk overlay and from the backtest, so both read the bars as the checked layer gives them. Breaks that nothing explains stay in the bars and are flagged. A new gate holds a Buy or Overweight back to Hold when the last year of bars holds such a break.

**Why:** with the guess adjuster still running on top of checked bars, a real crash (no record, so left raw by the checked layer) is silently "adjusted away" in the risk figures and backtests, and the user is told prices were adjusted "from an overnight break, not corporate-action data". That is the guess the user asked us never to make. And a verdict could still say Buy while volatility/drawdown/trend figures were flagged unreliable.

## Global Constraints

- No guessing: nothing is adjusted without a recorded event confirmed by the observed gap (existing rule).
- Fail open on sources: a source failing is a note, never an error (existing rule).
- The gate only holds back a purchase (Buy/Overweight → Hold), the same direction as a risk limit; other verdicts keep their call and gain a key-risk line.
- The gate applies with or without a risk profile.
- `pre_overlay_verdict` keeps meaning "the specialists' own call"; new field `held_back_by` ("risk limits" or "price data") says who held it.

## Tasks

### Task 1: Gate on unexplained breaks
- `CheckedHistory.price_flags(symbol, window=253)`: descriptions of unexplained moves inside the last `window` bars.
- `athena/orchestrator/price_gate.py`: `gate_verdict(verdict, flags)`.
- `Orchestrator(..., price_flags=None)`: gate runs after the blend, before the overlay; a note is added.
- `held_back_by` in schema, `apply_overlay`, report text, dashboard text.
- Wire `build_orchestrator(..., price_flags=...)` and `live_orchestrator`/`live_service` to `sources.history.price_flags`.

### Task 2: Retire `adjust_for_splits` from the risk overlay
- `figures_from_bars` uses the bars as given; `Figures.adjustment_note` and its orchestrator note removed.

### Task 3: Retire it from the backtest
- `run_rules` uses the bars as given; `RuleRun.adjustments` removed; `BacktestWorld.notes(symbol)` carries the checked layer's notes into the report and the dashboard view; `live_world` and the dashboard long history use `CheckedHistory`.
- `backtest/adjust.py` keeps only the break thresholds and `require_positive_prices`.

### Task 4: Tests, live check, TRD, commit.
