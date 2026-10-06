# Phase 1g — Stock and ETF Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last open Phase 1 item (TRD §9: "a stock backtest"): replay the Quant/Technical rules day by day over eight years of a stock or ETF, with no lookahead, compare them with buy and hold, the market and the overnight rate, prove each run does not depend on the name or price level of the instrument, and show it from a command line and on the dashboard.

**Architecture:** A small in-house engine (`athena.backtest`), no new dependency. A *rule* is a pair of deterministic functions over the same technical packet the live specialist reads. For each day the engine rebuilds that packet from the bars up to that close (a 520-bar window, as the live specialist sees), asks the rule, and fills at the next open; a protective stop and costs are modelled. Every run is repeated on *blinded* bars (ticker removed, dates moved back 28 years, prices rescaled to 100) and must trade identically. `summarize` reduces a run to the figures PRD FR-11 asks for. The command line and the dashboard are two views over the same `run_rules`. No language model, no API key, no cost.

**Tech Stack:** Python >= 3.11, pytest, Plotly and Streamlit (already present). No new dependencies.

**Spec:** `PRD.md` FR-11; `TRD.md` §1 (backtest integrity), §2.10, §4, §9.

**Plan series:** 0a-0e, 1a-1f (done) -> **1g (this plan)** -> risk overlay and investor profile -> later phases (debt, ETF analyst, mutual funds, full arbitration, options, live feed, brokers).

**Suggested models:** Haiku or Sonnet for the implementers (every step carries complete code to transcribe), Sonnet for the task reviewers, Opus for the final whole-branch review. Prototyped end to end in a scratch copy first: 527 offline tests passed (466 existing + 61 new), the four live tests passed on real data, and 36 deliberate mutations were each caught (the first pass found five that were not; the tests were strengthened until they were).

## Already done before this plan

The price-series bug (commit `46ad4b4`, "fix: keep only equity-series rows from jugaad price history") is already in the repository. It is a precondition: without it eight years of SBIN carry 612 repeated days and bond prices near 10,000, and the engine would refuse them.

## Verified findings (6 Oct 2026)

Real data, eight years requested (about 6.5 years evaluated after a 330-bar warm-up), 15 bps per side, 2 x ATR stop. Illustrative, not a finding about the market:

| Instrument | Rule | Strategy | Buy and hold | Strategy Sharpe / buy-and-hold Sharpe | Time in market | Trades |
| --- | --- | --- | --- | --- | --- | --- |
| SBIN | trend | +9.2% | +206.1% | -0.16 / 0.57 | 35% | 28 (6 stopped) |
| SBIN | persona | -5.6% | +206.1% | -1.91 / 0.57 | 1.6% | 3 |
| TCS | trend | +38% | -1% | not recorded | not recorded | not recorded |
| TCS | persona | +17% | -1% | not recorded | not recorded | not recorded |
| NIFTYBEES | trend | -44.1% | +100.3% | -0.80 / 0.43 | 40% | 29 (7 stopped) |
| NIFTYBEES | persona | +1% | +98% | not recorded | not recorded | not recorded |

- The persona rule almost never trades: "all three trends up" puts price near its 60-day high, while "reward:risk of at least 2" needs room back to that high, so the two conditions fight each other.
- **Integrity evidence.** Blinded runs make identical trades on all three instruments. A deliberately price-dependent rule is flagged as different. A direct test shows a decision uses only the bars up to that close (a one-bar peek is caught). The first version of that test could not catch a one-bar peek; a second test, which corrupts every later bar and requires an identical decision, was added.
- **Test gaps found by mutation and closed.** The synthetic bars have open == close, so a fill at a close and a fill at the next open were indistinguishable; `with_opens` fixes that. The benchmark's exit cost, `same_decisions`' trade-count and exit-reason checks, and "break-even is not a win" had no test.

**Honest limits:**
- Only the deterministic technical rules are backtested. The language-model specialists are not (a model may remember how a name performed; each replayed day costs a call) and neither are the fundamentals (Yahoo statements are not point-in-time).
- Costs are an approximation (15 bps per side), not exact delivery-trade charges; prices are unadjusted for splits and bonus issues (the engine does not detect them).
- Cash earns nothing between trades, which flatters buy and hold slightly; the Sharpe ratio uses the overnight-rate index for both.
- One position, all in, long-only: this measures a rule, not a portfolio.
- The 28-year blinding shift works for 1901-2099 and assumes the exchange calendar lines up, which it does for weekdays; holidays differ, but the engine takes the bars as given and never consults a calendar.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and need no API key (prices and market series only).
- The engine never reads a bar later than the decision's close; any change that does is a defect.
- Every backtest report states its assumed costs, that cash earns nothing, and the standard disclaimer ("A stylized analytical framework, not financial advice; not a registered investment adviser.").
- Compliance and style: files in the repository use LF; no new dependency; match the surrounding code's comment density.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...` and end each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `git push` after each task.
- Windows, Git Bash: run Python as `.venv/Scripts/python`. The helper scripts in this plan live outside the repository (save them under `$TEMP`), never inside it.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/backtest/__init__.py` | empty package marker |
| `src/athena/backtest/blinding.py` | `blind_bars`: remove identity from a price history |
| `src/athena/backtest/rules.py` | `Rule`, the `trend` and `persona` rules, `RULES` |
| `src/athena/backtest/engine.py` | `Config`, `Trade`, `BacktestResult`, `decide`, `run_backtest`, `same_decisions` |
| `src/athena/backtest/summary.py` | `Summary`, `summarize`, `format_summary`, `pct`, `num` |
| `src/athena/backtest/cli.py`, `__main__.py` | `BacktestWorld`, `live_world`, `run_rules`, `analyze`, `main`; `python -m athena.backtest` |
| `src/athena/dashboard/backtest_view.py` | `BacktestPanel`, `BacktestView`, `build_backtest_view` |
| `src/athena/dashboard/charts.py` | modified: `equity_chart` |
| `src/athena/dashboard/service.py` | modified: `DashboardService(long_history=...)`, `backtest()`, eight-year market series |
| `src/athena/dashboard/app.py` | modified: backtest checkbox, `render_backtest`, per-input result memory |
| `tests/test_backtest_blinding_rules.py`, `test_backtest_engine.py`, `test_backtest_summary.py`, `test_backtest_cli.py`, `test_dashboard_backtest_view.py` | new |
| `tests/dash_fakes.py`, `test_dashboard_app.py`, `test_dashboard_charts.py`, `test_dashboard_service.py` | modified |
| `tests/live/test_live_backtest.py` | new, opt-in |
| `TRD.md`, `src/athena/evaluation/resolver_eval.py` | modified: decision recorded; two unused imports removed |

---

### Task 1: Blinding and the two rules

**Files:**
- Create: `src/athena/backtest/__init__.py` (empty), `src/athena/backtest/blinding.py`, `src/athena/backtest/rules.py`
- Test: `tests/test_backtest_blinding_rules.py`

**Interfaces:**
- Produces: `blind_bars(bars, base=100.0, years=28) -> list[Bar]`; `Rule(name, description, enter, leave)`; `TREND`, `PERSONA`, `RULES: dict[str, Rule]`.
- Consumes: `athena.contracts.Bar` (a frozen dataclass: `symbol, timestamp, open, high, low, close, volume, as_of, source`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_blinding_rules.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest
from bar_factory import make_bars

from athena.backtest.blinding import BLIND_BASE, BLIND_SYMBOL, blind_bars
from athena.backtest.rules import MIN_REWARD_RISK, PERSONA, RULES, TREND
from athena.contracts import Bar
from athena.trading_calendar import ist_date

CLOSES = [250.0 + i * 0.3 + (4 if i % 9 == 0 else 0) for i in range(120)]
REAL = make_bars(CLOSES, symbol="SBIN")


def test_blinding_removes_the_ticker_rescales_prices_and_marks_the_source():
    blind = blind_bars(REAL)
    assert {bar.symbol for bar in blind} == {BLIND_SYMBOL} and {bar.source for bar in blind} == {"blinded"}
    assert blind[0].close == pytest.approx(BLIND_BASE) and len(blind) == len(REAL)


def test_blinding_keeps_returns_and_volume_exactly_so_a_return_based_rule_cannot_tell():
    blind = blind_bars(REAL)
    real_returns = [b.close / a.close for a, b in zip(REAL, REAL[1:])]
    blind_returns = [b.close / a.close for a, b in zip(blind, blind[1:])]
    assert blind_returns == pytest.approx(real_returns, rel=1e-12)
    assert [bar.volume for bar in blind] == [bar.volume for bar in REAL]


def test_blinding_moves_every_date_28_years_back_keeping_weekday_month_and_day():
    for real, blind in zip(REAL, blind_bars(REAL)):
        a, b = ist_date(real.timestamp), ist_date(blind.timestamp)
        assert b.year == a.year - 28 and (b.month, b.day, b.weekday()) == (a.month, a.day, a.weekday())


def test_blinding_survives_a_leap_day_and_sorts_its_output():
    def bar(day, close):
        stamp = datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1)
        return Bar("X", stamp, close, close, close, close, 1.0, stamp, "t")

    blind = blind_bars([bar(date(2024, 3, 1), 12.0), bar(date(2024, 2, 29), 10.0)])  # given out of order
    assert [ist_date(b.timestamp) for b in blind] == [date(1996, 2, 29), date(1996, 3, 1)]
    assert [b.close for b in blind] == pytest.approx([100.0, 120.0]) and blind_bars([]) == []


def packet(**values):
    return {"metrics": {name: {"value": value} for name, value in values.items()}}


FULL_SETUP = dict(trend_alignment="aligned_up", volume_ratio_20_50=1.2, reward_risk=2.5)


def test_the_trend_rule_enters_only_when_all_three_trends_are_up_and_leaves_when_alignment_is_lost():
    assert TREND.enter(packet(trend_alignment="aligned_up"))
    for other in ("not_aligned", "aligned_down"):
        assert not TREND.enter(packet(trend_alignment=other)) and TREND.leave(packet(trend_alignment=other))
    assert not TREND.leave(packet(trend_alignment="aligned_up"))


def test_a_missing_figure_never_triggers_an_entry_or_an_exit():
    assert not TREND.enter(packet()) and not TREND.leave(packet())
    assert not PERSONA.enter(packet(trend_alignment="aligned_up")) and not PERSONA.leave(packet())


def test_the_persona_rule_needs_alignment_volume_above_one_and_reward_risk_of_two():
    assert PERSONA.enter(packet(**FULL_SETUP))
    assert not PERSONA.enter(packet(**{**FULL_SETUP, "volume_ratio_20_50": 1.0}))  # must be above 1
    assert not PERSONA.enter(packet(**{**FULL_SETUP, "reward_risk": MIN_REWARD_RISK - 0.01}))
    assert PERSONA.enter(packet(**{**FULL_SETUP, "reward_risk": MIN_REWARD_RISK}))
    assert not PERSONA.enter(packet(**{**FULL_SETUP, "trend_alignment": "not_aligned"}))


def test_the_persona_rule_leaves_exactly_when_the_trend_rule_does():
    for alignment in ("aligned_up", "not_aligned", "aligned_down"):
        assert PERSONA.leave(packet(trend_alignment=alignment)) == TREND.leave(packet(trend_alignment=alignment))


def test_the_registry_names_both_rules_and_each_has_a_description():
    assert set(RULES) == {"trend", "persona"} and all(rule.description for rule in RULES.values())
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_blinding_rules.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'athena.backtest'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/backtest/__init__.py` as an empty file. Create `src/athena/backtest/blinding.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from athena.contracts import Bar

BLIND_SYMBOL = "ASSET"
BLIND_BASE = 100.0
BLIND_YEARS = 28  # the Gregorian calendar repeats weekdays and month lengths every 28 years (1901-2099)


def blind_bars(bars: Sequence[Bar], base: float = BLIND_BASE, years: int = BLIND_YEARS) -> list[Bar]:
    """The same price history with its identity removed (PRD FR-11): the ticker becomes "ASSET", every date moves
    back by `years` calendar years (weekdays, months and leap years line up exactly, so weekly and monthly structure
    is unchanged), and prices are rescaled so the first close is `base`. Returns and volume are untouched, so any
    rule that uses only those must behave identically on blinded and real data."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    if not ordered:
        return []
    factor = base / ordered[0].close
    return [
        replace(
            bar,
            symbol=BLIND_SYMBOL,
            timestamp=bar.timestamp.replace(year=bar.timestamp.year - years),
            open=bar.open * factor,
            high=bar.high * factor,
            low=bar.low * factor,
            close=bar.close * factor,
            source="blinded",
        )
        for bar in ordered
    ]
```

Create `src/athena/backtest/rules.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

ALIGNED_UP = "aligned_up"
MIN_REWARD_RISK = 2.0  # the persona's minimum reward:risk (TRD 2.3)


def _value(packet: Mapping[str, Any], name: str) -> Any:
    metric = packet["metrics"].get(name)
    return None if metric is None else metric["value"]


@dataclass(frozen=True)
class Rule:
    """A long-only decision rule over a technical packet (the same packet the Quant/Technical specialist reads).
    `enter` is asked while flat, `leave` while long; a missing figure never triggers either."""

    name: str
    description: str
    enter: Callable[[Mapping[str, Any]], bool]
    leave: Callable[[Mapping[str, Any]], bool]


def _trend_lost(packet: Mapping[str, Any]) -> bool:
    alignment = _value(packet, "trend_alignment")
    return alignment is not None and alignment != ALIGNED_UP


def _trend_entry(packet: Mapping[str, Any]) -> bool:
    return _value(packet, "trend_alignment") == ALIGNED_UP


def _persona_entry(packet: Mapping[str, Any]) -> bool:
    volume = _value(packet, "volume_ratio_20_50")
    reward_risk = _value(packet, "reward_risk")
    return (
        _trend_entry(packet)
        and volume is not None and volume > 1.0
        and reward_risk is not None and reward_risk >= MIN_REWARD_RISK
    )


TREND = Rule(
    "trend",
    "own it while the daily, weekly and monthly trends are all up; leave when they are no longer aligned",
    _trend_entry,
    _trend_lost,
)
PERSONA = Rule(
    "persona",
    "the Quant/Technical persona's long setup: all three trends up, volume ratio above 1 and reward:risk of at least 2 "
    "(a pullback with room back to the 60-day high); leave when the trends stop being aligned",
    _persona_entry,
    _trend_lost,
)
RULES: dict[str, Rule] = {rule.name: rule for rule in (TREND, PERSONA)}
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_blinding_rules.py -q`
Expected: `9 passed`. Then `.venv/Scripts/python -m pyflakes src/athena/backtest tests/test_backtest_blinding_rules.py` prints nothing.

- [ ] **Step 5: Mutation check**

Save the helper below as `$TEMP/mutate_core.py` (it is used again in Tasks 2-4). From the repository root run `.venv/Scripts/python $TEMP/mutate_core.py blinding rules`. Every line must start with `CAUGHT`; a `SURVIVED` or `NOT FOUND` line means a test or the transcribed code is wrong. Files are restored automatically.

```python
import glob
import pathlib
import subprocess
import sys

PY = sys.executable
ENGINE, BLIND, RULES, SUMMARY, CLI = (f"src/athena/backtest/{n}.py" for n in ("engine", "blinding", "rules", "summary", "cli"))

MUTATIONS = [
    ("engine: a one-bar peek at tomorrow", ENGINE, "bars[max(0, index + 1 - window) : index + 1]", "bars[max(0, index + 2 - window) : index + 2]"),
    ("engine: stop on a gap fills at the stop, not the open", ENGINE, "close_position(min(bar.open, stop)", "close_position(stop"),
    ("engine: no exit cost", ENGINE, "proceeds = shares * price * (1 - cost)", "proceeds = shares * price"),
    ("engine: no entry cost", ENGINE, "shares = cash / (bar.open * (1 + cost))", "shares = cash / bar.open"),
    ("engine: entry fills at the signal close, not the next open", ENGINE, "cash, entry_day, entry_price = 0.0, bar.day, bar.open", "cash, entry_day, entry_price = 0.0, bar.day, bar.close"),
    ("engine: shares are bought at the next close", ENGINE, "shares = cash / (bar.open * (1 + cost))", "shares = cash / (bar.close * (1 + cost))"),
    ("engine: a signal exit fills at the close", ENGINE, 'close_position(bar.open, bar.day, "signal")', 'close_position(bar.close, bar.day, "signal")'),
    ("engine: stop above the entry", ENGINE, "stop = bar.open - config.stop_atr_multiple", "stop = bar.open + config.stop_atr_multiple"),
    ("engine: benchmark never pays its exit cost", ENGINE, "    benchmark[-1] = bought * candles[-1].close * (1 - cost)", "    pass"),
    ("engine: an open position is not liquidated at the end", ENGINE, "        equity[-1] = shares * candles[-1].close * (1 - cost)", "        pass"),
    ("engine: repeated days are accepted", ENGINE, "if len(candles) != len(ordered):", "if False:"),
    ("engine: same_decisions ignores the trade count", ENGINE, "and len(first.trades) == len(second.trades)", "and True"),
    ("engine: same_decisions ignores exit reasons", ENGINE, "a.reason == b.reason and abs", "abs"),
    ("blinding: dates are not moved", BLIND, "year=bar.timestamp.year - years", "year=bar.timestamp.year"),
    ("blinding: prices are not rescaled", BLIND, "factor = base / ordered[0].close", "factor = 1.0"),
    ("blinding: the ticker is kept", BLIND, "symbol=BLIND_SYMBOL,", "symbol=bar.symbol,"),
    ("rules: persona ignores volume", RULES, "and volume is not None and volume > 1.0", "and True"),
    ("rules: persona ignores reward:risk", RULES, "and reward_risk is not None and reward_risk >= MIN_REWARD_RISK", "and True"),
    ("rules: a missing trend figure triggers an exit", RULES, "return alignment is not None and alignment != ALIGNED_UP", "return alignment != ALIGNED_UP"),
    ("summary: excess return has the wrong sign", SUMMARY, "excess_return=equity[-1] / initial - held[-1] / initial", "excess_return=held[-1] / initial - equity[-1] / initial"),
    ("summary: win rate counts break-even as a win", SUMMARY, "if t.net_return > 0)", "if t.net_return >= 0)"),
    ("summary: stops counted as all trades", SUMMARY, 'if trade.reason == "stop")', "if True)"),
    ("cli: blinded run is never compared", CLI, "same_decisions(real, run_backtest(blinded, RULES[name], config))", "True"),
    ("cli: an ETF is refused", CLI, 'if resolved.asset_class not in ("equity", "etf"):', 'if resolved.asset_class not in ("equity",):'),
    ("cli: ambiguity downloads anyway", CLI, "        return \"\\n\".join(lines + [\"Re-run with the exact symbol.\"]), 2", "        return \"\\n\".join(lines + [\"Re-run with the exact symbol.\"]), 0"),
]

only = sys.argv[1:]
for name, path, old, new in MUTATIONS:
    if only and not any(name.startswith(o) for o in only):
        continue
    p = pathlib.Path(path)
    original = p.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    if old not in text:
        print("NOT FOUND", name)
        continue
    p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    try:
        run = subprocess.run(
            [PY, "-m", "pytest", *sorted(glob.glob("tests/test_backtest_*.py")), "-q", "-x", "-p", "no:cacheprovider"],
            capture_output=True, text=True,
        )
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-200:]
        print(("CAUGHT  " if run.returncode == 1 else "ERROR   " if run.returncode else "SURVIVED"), name, "|", tail)
    finally:
        p.write_bytes(original)
```

Expected: 6 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/backtest tests/test_backtest_blinding_rules.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add backtest blinding and the trend and persona rules" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 2: The engine

**Files:**
- Create: `src/athena/backtest/engine.py`
- Test: `tests/test_backtest_engine.py`

**Interfaces:**
- Consumes: Task 1's `Rule`, `RULES`; `athena.technicals.packet.build_technical_packet(symbol, as_of, bars)`; `athena.technicals.candles.candles_from_bars`; `athena.contracts.InsufficientData`.
- Produces: `Config(cost_bps_per_side=15.0, stop_atr_multiple=2.0, warmup_bars=330, window_bars=520, initial_cash=100.0)`; `Trade(entry_day, exit_day, entry_price, exit_price, net_return, reason)`; `BacktestResult(rule, config, days, equity, in_market, trades, benchmark)`; `decide(bars, index, rule, holding, window) -> (action, atr)`; `run_backtest(bars, rule, config) -> BacktestResult`; `same_decisions(first, second, tolerance=1e-4) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_engine.py`:

```python
from dataclasses import replace

import pytest
from bar_factory import NOW, make_bars

from athena.backtest.engine import Config, decide, run_backtest, same_decisions
from athena.backtest.rules import RULES, Rule
from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import candles_from_bars

ALWAYS_IN = Rule("always", "enter at the first chance and never leave", lambda p: True, lambda p: False)
NEVER = Rule("never", "never trade", lambda p: False, lambda p: False)
IN_OUT = Rule("inout", "enter when flat, leave when long", lambda p: True, lambda p: True)
SMALL = Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=0.0)


def climbing(count=60, step=1.0, base=100.0):
    return make_bars([base + i * step for i in range(count)])


def with_opens(bars, offset):
    """The same bars with every open `offset` below its close, so that an open is never mistaken for a close."""
    return [Bar(b.symbol, b.timestamp, b.close - offset, b.high, b.low, b.close, b.volume, NOW, "t") for b in bars]


def test_a_decision_at_one_close_is_filled_at_the_next_open():
    bars = climbing()
    candles = candles_from_bars(bars)
    result = run_backtest(bars, IN_OUT, SMALL)
    assert result.trades[0].entry_price == candles[SMALL.warmup_bars + 1].open  # decided at the close of bar 20, filled at 21's open
    held = run_backtest(bars, ALWAYS_IN, SMALL)
    assert held.in_market[0] is False and held.in_market[1] is True  # the evaluation window's first bar is still flat


def test_a_rule_that_never_trades_leaves_cash_untouched_and_buy_and_hold_still_runs():
    result = run_backtest(climbing(), NEVER, SMALL)
    assert set(result.equity) == {100.0} and result.trades == () and not any(result.in_market)
    assert result.benchmark[-1] > 100.0 and len(result.benchmark) == len(result.equity) == len(result.days)


def test_buy_and_hold_starts_at_the_first_open_of_the_window_with_no_cost_when_costs_are_zero():
    bars = climbing()
    candles = candles_from_bars(bars)
    result = run_backtest(bars, NEVER, SMALL)
    assert result.benchmark[-1] == pytest.approx(100.0 * candles[-1].close / candles[SMALL.warmup_bars].open)


def test_costs_are_charged_on_both_sides_of_a_round_trip():
    flat = make_bars([100.0] * 60)
    free = run_backtest(flat, IN_OUT, SMALL)
    costly = run_backtest(flat, IN_OUT, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=100.0))
    assert free.equity[-1] == pytest.approx(100.0)
    assert costly.equity[-1] < 100.0 * (1 - 0.01) ** 2 * 1.0001 and costly.trades[0].net_return == pytest.approx(0.99 / 1.01 - 1, abs=1e-9)


def test_an_open_position_is_liquidated_at_the_end_with_the_exit_cost():
    result = run_backtest(climbing(), ALWAYS_IN, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=50.0))
    candles = candles_from_bars(climbing())
    shares = 100.0 / (candles[21].open * 1.005)
    assert result.equity[-1] == pytest.approx(shares * candles[-1].close * 0.995)
    assert result.trades == ()  # still open: only closed trades are listed


def test_a_protective_stop_fills_at_the_stop_price_when_the_day_trades_through_it():
    closes = [100.0] * 40 + [100.0, 100.0, 100.0, 100.0, 100.0]
    bars = make_bars(closes)  # every bar spans 2 points, so ATR is 2 and the stop sits 2 x 2 = 4 below the entry open
    crash = list(bars)
    day = crash[30]
    crash[30] = Bar(day.symbol, day.timestamp, 100.0, 100.5, 90.0, 95.0, 1000.0, NOW, "t")  # trades down through the stop
    result = run_backtest(crash, ALWAYS_IN, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=2.0, cost_bps_per_side=0.0))
    stopped = [t for t in result.trades if t.reason == "stop"][0]
    assert stopped.exit_price == pytest.approx(stopped.entry_price - 4.0, abs=0.01) and stopped.net_return < 0


def test_a_gap_down_through_the_stop_fills_at_the_open_not_the_stop():
    bars = make_bars([100.0] * 45)
    gap = list(bars)
    day = gap[30]
    gap[30] = Bar(day.symbol, day.timestamp, 80.0, 81.0, 79.0, 80.0, 1000.0, NOW, "t")
    result = run_backtest(gap, ALWAYS_IN, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=2.0, cost_bps_per_side=0.0))
    stopped = [t for t in result.trades if t.reason == "stop"][0]
    assert stopped.exit_price == 80.0


def test_a_signal_exit_is_filled_at_the_next_open_and_recorded_as_a_signal():
    result = run_backtest(climbing(), IN_OUT, SMALL)
    candles = candles_from_bars(climbing())
    first = result.trades[0]
    assert first.reason == "signal" and first.entry_price == candles[21].open and first.exit_price == candles[22].open


def test_the_engine_cannot_see_the_future_a_prefix_run_matches_the_longer_run():
    closes = [100 + i * 0.25 + (6 if i % 17 == 0 else 0) - (8 if i % 29 == 0 else 0) for i in range(400)]
    bars = make_bars(closes)
    config = Config(warmup_bars=330, window_bars=520)
    long_run = run_backtest(bars, RULES["trend"], config)
    cut = 380
    short_run = run_backtest(bars[:cut], RULES["trend"], config)
    keep = len(short_run.equity) - 1  # the last point of a shorter run includes its forced liquidation
    assert short_run.equity[:keep] == long_run.equity[:keep]
    closed_early = [t for t in short_run.trades if t.exit_day < short_run.days[-1]]
    assert closed_early == [t for t in long_run.trades if t.exit_day < short_run.days[-1]]


def test_runs_are_deterministic():
    bars = climbing(80)
    assert run_backtest(bars, IN_OUT, SMALL) == run_backtest(bars, IN_OUT, SMALL)


def test_too_few_bars_and_repeated_days_are_rejected():
    with pytest.raises(InsufficientData, match="needs more than"):
        run_backtest(climbing(21), ALWAYS_IN, SMALL)
    doubled = climbing(40) + climbing(40)
    with pytest.raises(InsufficientData, match="repeated trading days"):
        run_backtest(doubled, ALWAYS_IN, SMALL)


def test_the_result_carries_the_rule_name_config_and_window():
    bars = climbing()
    result = run_backtest(bars, NEVER, SMALL)
    assert result.rule == "never" and result.config is SMALL
    assert result.days[0] == candles_from_bars(bars)[20].day and result.days[-1] == candles_from_bars(bars)[-1].day


def test_a_decision_depends_only_on_the_bars_up_to_that_close():
    closes = [100 + i * 0.25 + (6 if i % 17 == 0 else 0) - (8 if i % 29 == 0 else 0) for i in range(420)]
    bars = make_bars(closes)
    for index in (340, 365, 390):
        garbage = bars[: index + 1] + [
            Bar(b.symbol, b.timestamp, b.open * 7, b.high * 7, b.low * 7, b.close * 7, b.volume * 3, NOW, "t")
            for b in bars[index + 1 :]
        ]  # every later bar is wildly different
        for holding in (False, True):
            assert decide(bars, index, RULES["trend"], holding, 520) == decide(garbage, index, RULES["trend"], holding, 520)


def test_fills_happen_at_the_open_of_the_next_bar_never_at_a_close():
    bars = with_opens(climbing(), 0.5)
    candles = candles_from_bars(bars)
    assert candles[21].open != candles[21].close
    held = run_backtest(bars, ALWAYS_IN, SMALL)
    assert held.equity[-1] == pytest.approx(100.0 / candles[21].open * candles[-1].close)
    first = run_backtest(bars, IN_OUT, SMALL).trades[0]
    assert first.entry_price == candles[21].open and first.exit_price == candles[22].open


def test_buy_and_hold_pays_the_entry_cost_at_the_start_and_the_exit_cost_only_at_the_end():
    bars = with_opens(climbing(), 0.5)
    candles = candles_from_bars(bars)
    result = run_backtest(bars, NEVER, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=100.0))
    bought = 100.0 / (candles[20].open * 1.01)
    assert result.benchmark[-1] == pytest.approx(bought * candles[-1].close * 0.99)
    assert result.benchmark[-2] == pytest.approx(bought * candles[-2].close)


def test_two_runs_count_as_the_same_decisions_only_if_days_trades_reasons_and_returns_all_match():
    base = run_backtest(climbing(80), IN_OUT, SMALL)
    assert len(base.trades) >= 2 and same_decisions(base, base)
    nudged = replace(base, trades=tuple(replace(t, net_return=t.net_return + 5e-5) for t in base.trades))
    assert same_decisions(base, nudged)  # within the default tolerance of 1e-4
    off = replace(base, trades=tuple(replace(t, net_return=t.net_return + 1e-3) for t in base.trades))
    assert not same_decisions(base, off)
    assert not same_decisions(base, replace(base, trades=base.trades[:-1]))
    assert not same_decisions(base, replace(base, trades=(replace(base.trades[0], reason="stop"), *base.trades[1:])))
    assert not same_decisions(base, replace(base, in_market=tuple(not held for held in base.in_market)))
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_engine.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'athena.backtest.engine'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/backtest/engine.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.backtest.rules import Rule
from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import candles_from_bars
from athena.technicals.packet import build_technical_packet

WINDOW_BARS = 520  # about 760 calendar days: the same history the live specialist sees (orchestrator HISTORY_DAYS)
WARMUP_BARS = 330  # roughly 15 months, the least the monthly trend needs
ENTRY, EXIT = "enter", "exit"


@dataclass(frozen=True)
class Config:
    cost_bps_per_side: float = 15.0  # an approximation of delivery-trade charges; not exact
    stop_atr_multiple: float | None = 2.0  # protective stop below the entry fill, in ATR14; None switches it off
    warmup_bars: int = WARMUP_BARS
    window_bars: int = WINDOW_BARS
    initial_cash: float = 100.0


@dataclass(frozen=True)
class Trade:
    entry_day: date
    exit_day: date
    entry_price: float
    exit_price: float
    net_return: float  # after costs on both sides
    reason: str  # "signal" or "stop"


@dataclass(frozen=True)
class BacktestResult:
    rule: str
    config: Config
    days: tuple[date, ...]  # the evaluation window, one entry per bar
    equity: tuple[float, ...]  # marked at each close, cash until the first fill
    in_market: tuple[bool, ...]
    trades: tuple[Trade, ...]
    benchmark: tuple[float, ...]  # buy and hold from the same first open, same costs, same window


def decide(bars: Sequence[Bar], index: int, rule: Rule, holding: bool, window: int) -> tuple[str | None, float | None]:
    """The rule's wish after the close of bar `index`, from the bars up to and including it and nothing later."""
    visible = bars[max(0, index + 1 - window) : index + 1]
    packet = build_technical_packet(bars[index].symbol, bars[index].timestamp, visible)
    wants = rule.leave(packet) if holding else rule.enter(packet)
    atr = packet["metrics"].get("atr_14")
    return (EXIT if holding else ENTRY) if wants else None, (atr["value"] if atr else None)


def run_backtest(bars: Sequence[Bar], rule: Rule, config: Config = Config()) -> BacktestResult:
    """Long-only, one position, all in. Decisions use only information up to a bar's close and are filled at the next
    bar's open, so nothing can see the future; a protective stop fills at the stop price, or at the open if the bar
    gaps through it."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    candles = candles_from_bars(ordered)
    if len(candles) != len(ordered):
        raise InsufficientData("the bars contain repeated trading days")
    if len(ordered) <= config.warmup_bars + 1:
        raise InsufficientData(f"a backtest needs more than {config.warmup_bars + 1} daily bars, got {len(ordered)}")

    cost = config.cost_bps_per_side / 10_000.0
    cash, shares, stop = config.initial_cash, 0.0, None
    entry_day = entry_price = entry_cost_base = None
    pending: str | None = None
    pending_atr: float | None = None
    equity: list[float] = []
    in_market: list[bool] = []
    trades: list[Trade] = []

    def close_position(price: float, day: date, reason: str) -> None:
        nonlocal cash, shares, stop, entry_day, entry_price, entry_cost_base
        proceeds = shares * price * (1 - cost)
        trades.append(Trade(entry_day, day, entry_price, price, proceeds / entry_cost_base - 1, reason))  # type: ignore[arg-type]
        cash, shares, stop = proceeds, 0.0, None
        entry_day = entry_price = entry_cost_base = None

    for i in range(config.warmup_bars, len(candles)):
        bar = candles[i]
        if pending == ENTRY and shares == 0:
            entry_cost_base = cash
            shares = cash / (bar.open * (1 + cost))
            cash, entry_day, entry_price = 0.0, bar.day, bar.open
            stop = bar.open - config.stop_atr_multiple * pending_atr if (config.stop_atr_multiple and pending_atr) else None
        elif pending == EXIT and shares > 0:
            close_position(bar.open, bar.day, "signal")
        pending = None

        if shares > 0 and stop is not None and bar.low <= stop:
            close_position(min(bar.open, stop), bar.day, "stop")

        equity.append(cash + shares * bar.close)
        in_market.append(shares > 0)
        if i < len(candles) - 1:  # no point deciding after the last bar: there is no next open to fill at
            pending, pending_atr = decide(ordered, i, rule, shares > 0, config.window_bars)

    first = candles[config.warmup_bars]
    bought = config.initial_cash / (first.open * (1 + cost))
    benchmark = [bought * c.close for c in candles[config.warmup_bars :]]
    benchmark[-1] = bought * candles[-1].close * (1 - cost)  # liquidated at the end, so costs match the strategy's
    if shares > 0:
        equity[-1] = shares * candles[-1].close * (1 - cost)
    return BacktestResult(
        rule.name, config, tuple(c.day for c in candles[config.warmup_bars :]), tuple(equity), tuple(in_market),
        tuple(trades), tuple(benchmark),
    )


def same_decisions(first: BacktestResult, second: BacktestResult, tolerance: float = 1e-4) -> bool:
    """Whether two runs traded identically: the same days in the market, the same trades in the same order with the
    same exit reasons, and trade returns within `tolerance` (a rescaled price series differs only by rounding).
    Dates are not compared, so a run on blinded data can be checked against the run on the real data."""
    return (
        first.in_market == second.in_market
        and len(first.trades) == len(second.trades)
        and all(
            a.reason == b.reason and abs(a.net_return - b.net_return) <= tolerance
            for a, b in zip(first.trades, second.trades)
        )
    )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_engine.py tests/test_backtest_blinding_rules.py -q`
Expected: `25 passed`. `.venv/Scripts/python -m pyflakes src/athena/backtest tests/test_backtest_engine.py` prints nothing.

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_core.py engine`
Expected: 13 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/backtest/engine.py tests/test_backtest_engine.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add the backtest engine with next-open fills, a protective stop and costs" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: The summary

**Files:**
- Create: `src/athena/backtest/summary.py`
- Test: `tests/test_backtest_summary.py`

**Interfaces:**
- Consumes: Task 2's `BacktestResult`, `Trade`, `Config`; `athena.metrics.stats` (`sharpe`, `annualized_volatility`, `max_drawdown`); `athena.metrics.series` (`Series`, `level_asof`, `returns_from_levels`, `riskfree_returns`).
- Produces: `Summary` (frozen dataclass of the figures PRD FR-11 names plus trade statistics); `summarize(result, riskfree=None, index=None) -> Summary`; `format_summary(summary, symbol="", rule_description="") -> str`; `pct(value) -> str` and `num(value) -> str` (both give `"n/a"` for `None`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_summary.py`:

```python
import random
from datetime import date, timedelta

import pytest

from athena.backtest.engine import BacktestResult, Config, Trade
from athena.backtest.summary import format_summary, summarize

CONFIG = Config()


def weekdays(count, start=date(2025, 1, 6)):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def trade(net, reason="signal"):
    return Trade(date(2025, 1, 6), date(2025, 2, 6), 100.0, 100.0 * (1 + net), net, reason)


def result(equity, benchmark=None, in_market=None, trades=(), days=None):
    count = len(equity)
    return BacktestResult(
        "trend", CONFIG, days or weekdays(count), tuple(equity), tuple(in_market or [True] * count), tuple(trades),
        tuple(benchmark or [100.0] * count),
    )


def noisy(count, drift, vol, seed):
    rng, level, out = random.Random(seed), 100.0, []
    for _ in range(count):
        level *= 1 + rng.gauss(drift, vol)
        out.append(level)
    return out


def test_total_return_excess_over_buy_and_hold_and_drawdown_come_from_the_curves():
    s = summarize(result([105, 110, 99, 105], benchmark=[100, 102, 101, 104]))
    assert s.total_return == pytest.approx(0.05) and s.benchmark_total_return == pytest.approx(0.04)
    assert s.excess_return == pytest.approx(0.01)
    assert s.max_drawdown == pytest.approx(99 / 110 - 1)  # the fall from the 110 peak to 99
    assert s.benchmark_max_drawdown == pytest.approx(0.0, abs=1e-12) or s.benchmark_max_drawdown < 0


def test_the_drawdown_counts_from_the_starting_cash_not_just_the_first_close():
    assert summarize(result([90, 95, 99])).max_drawdown == pytest.approx(-0.10)


def test_yearly_return_uses_the_calendar_span_of_the_window():
    days = (date(2025, 1, 1), date(2026, 1, 1))
    s = summarize(result([110.0, 121.0], days=days))
    assert s.cagr == pytest.approx(1.21 ** (365.25 / 365) - 1, abs=1e-6)
    assert summarize(result([100.0, 100.0], days=(date(2025, 1, 1), date(2025, 1, 1)))).cagr is None


def test_too_short_a_series_gives_no_volatility_or_sharpe_instead_of_a_guess():
    s = summarize(result([101, 102, 103, 104]))
    assert s.volatility is None and s.sharpe is None and s.max_drawdown is not None


def test_a_long_series_gets_volatility_and_a_sharpe_that_a_positive_riskfree_rate_lowers():
    equity = noisy(120, 0.001, 0.01, seed=3)
    days = weekdays(120)
    riskfree = {day: 100.0 * (1.0004 ** i) for i, day in enumerate(days)}  # a steady overnight accrual
    plain, with_rate = summarize(result(equity, days=days)), summarize(result(equity, days=days), riskfree=riskfree)
    assert plain.volatility > 0 and plain.sharpe is not None and with_rate.sharpe < plain.sharpe


def test_a_riskfree_series_that_starts_late_drops_the_sharpe_rather_than_inventing_one():
    days = weekdays(60)
    late = {days[30]: 100.0, days[-1]: 103.0}
    assert summarize(result(noisy(60, 0.001, 0.01, 1), days=days), riskfree=late).sharpe is None


def test_trade_statistics():
    s = summarize(result([100, 101], trades=[trade(0.10), trade(-0.05, "stop"), trade(0.02)], in_market=[False, True]))
    assert (s.trades, s.stops) == (3, 1) and s.win_rate == pytest.approx(2 / 3)
    assert s.average_trade == pytest.approx((0.10 - 0.05 + 0.02) / 3) and s.worst_trade == pytest.approx(-0.05)
    assert s.time_in_market == 0.5


def test_no_trades_means_no_trade_statistics():
    s = summarize(result([100.0, 100.0]))
    assert (s.trades, s.win_rate, s.average_trade, s.worst_trade) == (0, None, None, None)


def test_the_market_return_over_the_same_window_is_reported_when_a_series_is_given():
    days = weekdays(4)
    index = {days[0]: 200.0, days[-1]: 230.0}
    assert summarize(result([100, 101, 102, 103], days=days), index=index).index_total_return == pytest.approx(0.15)
    assert summarize(result([100, 101, 102, 103], days=days)).index_total_return is None


def test_the_text_report_shows_both_columns_and_marks_missing_figures_na():
    text = format_summary(summarize(result([105, 110, 99, 105], benchmark=[100, 102, 101, 104])), "SBIN", "a rule")
    for expected in ("Backtest SBIN  rule: trend", "a rule", "strategy    buy and hold", "total return", "Sharpe", "n/a", "excess over buy and hold: 1.0%"):
        assert expected in text


def test_a_trade_that_breaks_even_is_not_counted_as_a_win():
    summary = summarize(result([100, 101, 102], trades=[trade(0.10), trade(0.0), trade(-0.05)]))
    assert summary.win_rate == pytest.approx(1 / 3)
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_summary.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'athena.backtest.summary'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/backtest/summary.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from athena.backtest.engine import BacktestResult
from athena.contracts import InsufficientData
from athena.metrics import stats
from athena.metrics.series import Series, level_asof, returns_from_levels, riskfree_returns


@dataclass(frozen=True)
class Summary:
    """What a backtest run reports (PRD FR-11): Sharpe, drawdown and benchmark-relative return, plus trade statistics.
    Everything is over the evaluation window; `None` means it could not be computed."""

    rule: str
    start: date
    end: date
    bars: int
    total_return: float
    cagr: float | None
    volatility: float | None
    max_drawdown: float | None
    sharpe: float | None
    time_in_market: float
    trades: int
    stops: int
    win_rate: float | None
    average_trade: float | None
    worst_trade: float | None
    benchmark_total_return: float
    benchmark_cagr: float | None
    benchmark_max_drawdown: float | None
    benchmark_sharpe: float | None
    excess_return: float  # strategy total return minus buy-and-hold total return
    index_total_return: float | None  # the market (NIFTY 50) over the same window, if its series was given


def _try(compute) -> float | None:
    try:
        return compute()
    except InsufficientData:
        return None


def _cagr(first: float, last: float, start: date, end: date) -> float | None:
    years = (end - start).days / 365.25
    return (last / first) ** (1 / years) - 1 if years > 0 and first > 0 and last > 0 else None


def _sharpe(levels: tuple[float, ...], days: tuple[date, ...], riskfree: Series | None) -> float | None:
    returns = returns_from_levels(levels)
    if riskfree is None:
        free = [0.0] * len(returns)
    else:
        try:
            free = riskfree_returns(riskfree, days)
        except InsufficientData:
            return None
    return _try(lambda: stats.sharpe(returns, free))


def summarize(result: BacktestResult, riskfree: Series | None = None, index: Series | None = None) -> Summary:
    """Sharpe is measured against `riskfree` (an accrual index such as the Nifty 1D Rate Index) or against zero if none."""
    initial = result.config.initial_cash
    days, equity, held = result.days, result.equity, result.benchmark
    start, end = days[0], days[-1]
    trades = result.trades
    first_level, last_level = level_asof(index, start) if index else None, level_asof(index, end) if index else None
    return Summary(
        rule=result.rule,
        start=start,
        end=end,
        bars=len(days),
        total_return=equity[-1] / initial - 1,
        cagr=_cagr(initial, equity[-1], start, end),
        volatility=_try(lambda: stats.annualized_volatility(returns_from_levels(equity))),
        max_drawdown=_try(lambda: stats.max_drawdown([initial, *equity])),
        sharpe=_sharpe(equity, days, riskfree),
        time_in_market=sum(result.in_market) / len(result.in_market),
        trades=len(trades),
        stops=sum(1 for trade in trades if trade.reason == "stop"),
        win_rate=sum(1 for t in trades if t.net_return > 0) / len(trades) if trades else None,
        average_trade=sum(t.net_return for t in trades) / len(trades) if trades else None,
        worst_trade=min((t.net_return for t in trades), default=None),
        benchmark_total_return=held[-1] / initial - 1,
        benchmark_cagr=_cagr(initial, held[-1], start, end),
        benchmark_max_drawdown=_try(lambda: stats.max_drawdown([initial, *held])),
        benchmark_sharpe=_sharpe(held, days, riskfree),
        excess_return=equity[-1] / initial - held[-1] / initial,
        index_total_return=last_level / first_level - 1 if first_level and last_level else None,
    )


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_summary(summary: Summary, symbol: str = "", rule_description: str = "") -> str:
    head = f"Backtest {symbol}  rule: {summary.rule}  {summary.start} to {summary.end} ({summary.bars} trading days)"
    lines = [head]
    if rule_description:
        lines.append(f"  {rule_description}")
    lines += [
        "                      strategy    buy and hold",
        f"  total return        {pct(summary.total_return):>9}    {pct(summary.benchmark_total_return):>9}",
        f"  yearly return       {pct(summary.cagr):>9}    {pct(summary.benchmark_cagr):>9}",
        f"  worst drawdown      {pct(summary.max_drawdown):>9}    {pct(summary.benchmark_max_drawdown):>9}",
        f"  Sharpe              {num(summary.sharpe):>9}    {num(summary.benchmark_sharpe):>9}",
        f"  excess over buy and hold: {pct(summary.excess_return)}"
        + (f"   market (NIFTY 50): {pct(summary.index_total_return)}" if summary.index_total_return is not None else ""),
        f"  in the market {pct(summary.time_in_market)} of days; {summary.trades} trades ({summary.stops} stopped out), "
        f"win rate {pct(summary.win_rate)}, average trade {pct(summary.average_trade)}, worst {pct(summary.worst_trade)}",
        "  Cash earns nothing between trades. Past results do not predict future ones.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_summary.py -q`
Expected: `11 passed`. `.venv/Scripts/python -m pyflakes src/athena/backtest tests/test_backtest_summary.py` prints nothing.

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_core.py summary`
Expected: 3 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/backtest/summary.py tests/test_backtest_summary.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: summarize a backtest run (return, drawdown, Sharpe, trades) against buy and hold" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 4: The command line

**Files:**
- Create: `src/athena/backtest/cli.py`, `src/athena/backtest/__main__.py`
- Test: `tests/test_backtest_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3; `athena.adapters.prices` (`JugaadPriceAdapter`, `YahooPriceAdapter`, `ohlcv_chain`); `athena.dashboard.risk` (`refresh_risk_data`, `risk_world_from_store`); `athena.loaders.nse_masters`, `nse_holidays`; `athena.orchestrator.builders.history_fetcher(chain, days)`; `athena.resolver` (`InstrumentResolver`, `InstrumentIndex`, `Ambiguity`).
- Produces: `BacktestWorld(resolver, fetch_bars, riskfree, index)`; `live_world(years=8)`; `RuleRun(summary, blinded_identical, result)`; `run_rules(bars, rule_names, riskfree, index, config=Config()) -> list[RuleRun]` (raises `ValueError("unknown rule 'x'; choose from [...]")`); `assumptions(config=Config()) -> str`; `format_runs(symbol, runs, config=Config()) -> str`; `analyze(world, query, rule_names) -> (text, code)`; `main(argv=None, factory=live_world) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_cli.py`:

```python
import pytest
from bar_factory import NOW, make_bars

from athena.backtest.cli import BacktestWorld, analyze, main, run_rules
from athena.backtest.engine import Config
from athena.backtest.rules import RULES, Rule
from athena.contracts import AthenaError, Record
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import ist_date

CLOSES = [100.0 + i * 0.3 + (3 if i % 13 == 0 else 0) - (4 if i % 31 == 0 else 0) for i in range(520)]
BARS = make_bars(CLOSES, symbol="SBIN")


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("SBILIFE", "SBI Life Insurance Company Limited"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def world(fetched=None):
    days = [ist_date(bar.timestamp) for bar in BARS]
    rate = {day: 100.0 * 1.0002 ** i for i, day in enumerate(days)}
    index = {day: 1000.0 + i for i, day in enumerate(days)}

    def fetch_bars(symbol):
        if fetched is not None:
            fetched.append(symbol)
        return BARS

    return BacktestWorld(make_resolver(), fetch_bars, rate, index)


def test_a_known_symbol_gets_both_rules_a_blinding_check_the_assumptions_and_the_disclaimer():
    fetched = []
    text, code = analyze(world(fetched), "sbin", list(RULES))
    assert code == 0 and fetched == ["SBIN"]
    for expected in ("Backtest SBIN  rule: trend", "Backtest SBIN  rule: persona", "buy and hold", "market (NIFTY 50)",
                     "trend: same trades, persona: same trades", "15 bps per side", "cash earns nothing", "not financial advice"):
        assert expected in text


def test_an_etf_can_be_backtested_too():
    text, code = analyze(world(), "NIFTYBEES", ["trend"])
    assert code == 0 and "Backtest NIFTYBEES" in text and "rule: persona" not in text


def test_an_ambiguous_name_lists_candidates_downloads_nothing_and_returns_two():
    fetched = []
    text, code = analyze(world(fetched), "sbi", ["trend"])
    assert code == 2 and fetched == [] and "could be more than one instrument" in text and "SBILIFE" in text


def test_an_unknown_rule_is_rejected_before_any_run():
    with pytest.raises(ValueError, match="unknown rule 'magic'"):
        analyze(world(), "sbin", ["magic"])


def test_the_blinding_check_catches_a_rule_that_depends_on_the_absolute_price(monkeypatch):
    def cheating_entry(packet):
        return packet["metrics"]["last_close"]["value"] > 250.0  # true on these real-looking prices, false once rescaled to 100

    monkeypatch.setitem(RULES, "cheat", Rule("cheat", "enters on a price level", cheating_entry, lambda p: False))
    expensive = make_bars([close + 200.0 for close in CLOSES], symbol="SBIN")  # starts at 300, so blinding really rescales it
    market = world()
    runs = run_rules(expensive, ["trend", "cheat"], market.riskfree, market.index, Config())
    assert [run.blinded_identical for run in runs] == [True, False]


def test_main_joins_the_words_applies_the_rule_choice_and_prints_the_report(capsys):
    assert main(["state", "bank", "--rule", "trend", "--years", "5"], factory=lambda years: world()) == 2  # "state bank" is ambiguous here
    capsys.readouterr()
    assert main(["SBIN", "--rule", "persona"], factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    assert "rule: persona" in out and "rule: trend" not in out


def test_main_reports_errors_without_a_traceback(capsys):
    def broken(years):
        raise AthenaError("no price data")

    assert main(["SBIN"], factory=broken) == 1 and "error: no price data" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_cli.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'athena.backtest.cli'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/backtest/cli.py`:

```python
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.backtest.blinding import blind_bars
from athena.backtest.engine import BacktestResult, Config, run_backtest, same_decisions
from athena.backtest.rules import RULES
from athena.backtest.summary import Summary, format_summary, summarize
from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.dashboard.risk import refresh_risk_data, risk_world_from_store
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.metrics.series import Series
from athena.orchestrator.builders import history_fetcher
from athena.resolver import Ambiguity, InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar, ist_date

BACKTEST_YEARS = 8
DISCLAIMER = "A stylized analytical framework, not financial advice; not a registered investment adviser."


@dataclass(frozen=True)
class BacktestWorld:
    resolver: InstrumentResolver
    fetch_bars: Callable[[str], list[Bar]]  # symbol -> a long daily history
    riskfree: Series  # Nifty 1D Rate Index levels, for the Sharpe ratio
    index: Series  # NIFTY 50 closes, for the market comparison


def live_world(years: int = BACKTEST_YEARS) -> BacktestWorld:
    """Real sources: NSE master lists and holidays, jugaad-data then Yahoo prices, NIFTY 50 and overnight-rate series.
    No language model and no API key is involved."""
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    NseHolidayLoader(store).refresh()
    holiday_years = [int(record.key) for record in store.latest_records(HOLIDAY_DATASET)]
    calendar = load_calendar(store, holiday_years) if holiday_years else TradingCalendar(frozenset())
    resolver = InstrumentResolver(InstrumentIndex.from_store(store, calendar=calendar))
    chain = ohlcv_chain([JugaadPriceAdapter(), YahooPriceAdapter()], calendar)
    refresh_risk_data(store, since=ist_date(utc_now()) - timedelta(days=365 * years))
    market = risk_world_from_store(store)
    return BacktestWorld(resolver, history_fetcher(chain, days=365 * years), market.rate, market.price_index)


@dataclass(frozen=True)
class RuleRun:
    summary: Summary
    blinded_identical: bool
    result: BacktestResult


def run_rules(
    bars: list[Bar], rule_names: list[str], riskfree: Series, index: Series, config: Config = Config()
) -> list[RuleRun]:
    """Each named rule on the real bars and again on blinded bars (ticker removed, dates shifted, prices rescaled)."""
    unknown = [name for name in rule_names if name not in RULES]
    if unknown:
        raise ValueError(f"unknown rule {unknown[0]!r}; choose from {sorted(RULES)}")
    blinded = blind_bars(bars)
    runs = []
    for name in rule_names:
        real = run_backtest(bars, RULES[name], config)
        runs.append(
            RuleRun(
                summarize(real, riskfree, index),
                same_decisions(real, run_backtest(blinded, RULES[name], config)),
                real,
            )
        )
    return runs


def assumptions(config: Config = Config()) -> str:
    return (
        f"Assumed costs {config.cost_bps_per_side:g} bps per side, stop {config.stop_atr_multiple} x ATR below the fill, "
        "decisions at the close and fills at the next open, cash earns nothing."
    )


def format_runs(symbol: str, runs: list[RuleRun], config: Config = Config()) -> str:
    blocks = [format_summary(run.summary, symbol, RULES[run.summary.rule].description) for run in runs]
    checks = ", ".join(f"{run.summary.rule}: {'same trades' if run.blinded_identical else 'DIFFERENT'}" for run in runs)
    return "\n\n".join(blocks) + (
        f"\n\nBlinding check (ticker removed, dates shifted 28 years, prices rescaled to 100): {checks}."
        f"\n{assumptions(config)}\n{DISCLAIMER}"
    )


def analyze(world: BacktestWorld, query: str, rule_names: list[str]) -> tuple[str, int]:
    """The report text and an exit code: 0 ok, 2 when the name is ambiguous."""
    resolved = world.resolver.resolve(query)
    if isinstance(resolved, Ambiguity):
        lines = [f"{query!r} could be more than one instrument ({resolved.reason}):"]
        lines += [f"  {c.identifier}  {c.name}  ({c.asset_class})" for c in resolved.candidates]
        return "\n".join(lines + ["Re-run with the exact symbol."]), 2
    if resolved.asset_class not in ("equity", "etf"):
        raise ValueError(f"backtests cover stocks and ETFs, not {resolved.asset_class}")
    runs = run_rules(world.fetch_bars(resolved.identifier), rule_names, world.riskfree, world.index)
    return format_runs(resolved.identifier, runs), 0


def main(argv: list[str] | None = None, factory: Callable[..., BacktestWorld] = live_world) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.backtest", description="Backtest the technical rules on one instrument.")
    parser.add_argument("query", nargs="+", help="ticker, ISIN or name, for example SBIN")
    parser.add_argument("--rule", choices=[*RULES, "both"], default="both")
    parser.add_argument("--years", type=int, default=BACKTEST_YEARS)
    args = parser.parse_args(argv)
    try:
        text, code = analyze(factory(years=args.years), " ".join(args.query), list(RULES) if args.rule == "both" else [args.rule])
    except (AthenaError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `src/athena/backtest/__main__.py`:

```python
from athena.backtest.cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_backtest_cli.py tests/test_backtest_engine.py tests/test_backtest_summary.py tests/test_backtest_blinding_rules.py -q`
Expected: `43 passed`. `.venv/Scripts/python -m pyflakes src/athena/backtest tests/test_backtest_cli.py` prints nothing.

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_core.py cli`
Expected: 3 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/backtest tests/test_backtest_cli.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add python -m athena.backtest with a blinding check per rule" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 5: The dashboard backtest

**Files:**
- Create: `src/athena/dashboard/backtest_view.py`, `tests/test_dashboard_backtest_view.py`
- Modify (replace the whole file with the version below): `src/athena/dashboard/charts.py`, `src/athena/dashboard/service.py`, `src/athena/dashboard/app.py`, `tests/dash_fakes.py`, `tests/test_dashboard_app.py`, `tests/test_dashboard_charts.py`, `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: Task 4's `RuleRun`, `run_rules`, `assumptions`; Task 3's `pct`, `num`; Task 2's `BacktestResult`; `athena.orchestrator.builders.history_fetcher`.
- Produces: `equity_chart(result, title) -> go.Figure` (traces `Strategy`, `Buy and hold`, `Entry`, `Exit`; raises `ValueError("no days to chart")`); `BacktestPanel(rule, description, rows, facts, blinded_identical, figure)`; `BacktestView(identifier, panels, assumptions)`; `build_backtest_view(identifier, runs, assumptions)`; `DashboardService(..., fundamentals=None, long_history=None)` with `backtest(identifier) -> BacktestView` (raises `AthenaError("backtests are not available in this session")` without `long_history`); `render_backtest(view)`; the `ViewService` protocol gains `backtest(identifier)`. The checkbox appears for `equity` and `etf` views only, and the view and the backtest are remembered per input in `st.session_state`, so a click redraws instead of re-asking the specialists.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_backtest_view.py`:

```python
from dataclasses import replace

from dash_fakes import trend_runs

from athena.backtest.rules import RULES
from athena.backtest.summary import num, pct
from athena.dashboard.backtest_view import build_backtest_view

RUNS = list(trend_runs())


def test_there_is_one_panel_per_rule_with_the_description_the_table_and_the_curve():
    view = build_backtest_view("SBIN", RUNS, "assumed costs")
    assert view.identifier == "SBIN" and view.assumptions == "assumed costs"
    assert [panel.rule for panel in view.panels] == ["trend", "persona"]
    trend = view.panels[0]
    assert trend.description == RULES["trend"].description
    assert [row[0] for row in trend.rows] == ["Total return", "Yearly return", "Worst drawdown", "Sharpe"]
    assert trend.figure.layout.title.text == "SBIN: trend rule against buy and hold" and len(trend.figure.data) == 4


def test_the_table_shows_the_strategy_beside_buy_and_hold_in_the_right_columns():
    summary = RUNS[0].summary
    rows = {measure: (strategy, hold) for measure, strategy, hold in build_backtest_view("SBIN", RUNS, "").panels[0].rows}
    assert rows["Total return"] == (pct(summary.total_return), pct(summary.benchmark_total_return))
    assert rows["Worst drawdown"] == (pct(summary.max_drawdown), pct(summary.benchmark_max_drawdown))
    assert rows["Sharpe"] == (num(summary.sharpe), num(summary.benchmark_sharpe))
    assert rows["Total return"][0] != rows["Total return"][1]  # the two columns are not the same figure twice


def test_the_facts_give_the_window_the_market_and_the_trade_statistics():
    summary = RUNS[0].summary
    window, trades = build_backtest_view("SBIN", RUNS, "").panels[0].facts
    assert f"{summary.start} to {summary.end} ({summary.bars} trading days)" in window
    assert f"excess over buy and hold {pct(summary.excess_return)}" in window
    assert f"the market (NIFTY 50) returned {pct(summary.index_total_return)}" in window
    assert "1 trades (0 stopped out)" in trades and f"in the market {pct(summary.time_in_market)} of days" in trades


def test_a_rule_that_never_trades_shows_not_available_instead_of_failing():
    persona = build_backtest_view("SBIN", RUNS, "").panels[1]
    assert "0 trades (0 stopped out)" in persona.facts[1] and "win rate n/a" in persona.facts[1]


def test_the_market_comparison_is_left_out_when_the_market_series_is_missing():
    bare = replace(RUNS[0], summary=replace(RUNS[0].summary, index_total_return=None))
    assert "NIFTY 50" not in build_backtest_view("SBIN", [bare], "").panels[0].facts[0]


def test_the_blinding_result_is_carried_through_per_rule():
    runs = [RUNS[0], replace(RUNS[1], blinded_identical=False)]
    assert [panel.blinded_identical for panel in build_backtest_view("SBIN", runs, "").panels] == [True, False]
```

Replace `tests/dash_fakes.py` with:

```python
"""Shared fixtures for the dashboard tests: a realistic result, view and service built from synthetic data."""
from datetime import datetime, timezone
from functools import cache

from bar_factory import make_bars

from athena.backtest.cli import assumptions, run_rules
from athena.contracts import AthenaError
from athena.dashboard.backtest_view import build_backtest_view
from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, Resolution
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date
from fund_fixtures import ACME, PRICE, index_history

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
CLOSES = [100.0 + i * 0.5 for i in range(320)]
BARS = make_bars(CLOSES, symbol="SBIN")
TECHNICAL = build_technical_packet("SBIN", NOW, BARS)
FUNDAMENTALS = build_fundamentals_packet("SBIN", NOW, ACME, PRICE, NOW, index_history())
RISK = {
    "instrument": "SBIN",
    "as_of": NOW.isoformat(),
    "metrics": {"beta": {"value": 1.1, "unit": "ratio", "inputs": ["asset", "NIFTY 50"], "window": "252 returns", "source": "x"}},
    "missing": ["tracking_error"],
    "missing_reasons": {"tracking_error": "required series not provided"},
}
SPECIALIST = {"signal": "bullish", "confidence": 72, "reasoning": "Daily trend is up.", "data_coverage": "full", "missing": []}
VERDICT = {"verdict": "Buy", "conviction": 72, "key_risks": ["a data gap"], "resolution_path": "blend"}


def resolution(asset_class="equity", symbol="SBIN"):
    return Resolution(asset_class, "ticker", symbol, "State Bank of India", "INE062A01020", "exact", 1.0, (), ())


def ok_result(asset_class="equity", status=OK, specialists=None, verdict=None):
    return OrchestrationResult(
        status, "sbin", resolution(asset_class), None,
        {"quant_technical": SPECIALIST} if specialists is None else specialists,
        {"valuation": "not built yet"}, None, 1.0, verdict or VERDICT, ("risk overlay missing",),
    )


def ambiguous_result():
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.81),), "several matches")
    return OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ())


def full_view(asset_class="equity", status=OK, risk=RISK, fundamentals=None, fundamentals_note=None):
    return build_view(ok_result(asset_class, status), BARS, TECHNICAL, risk, fundamentals, fundamentals_note)


# A long climb, a slide and a recovery: the trend rule makes exactly one trade on it, the persona rule none.
TREND_CLOSES = [100.0 + i * 0.4 for i in range(400)] + [260.0 - i * 1.2 for i in range(60)] + [190.0 + i * 0.8 for i in range(100)]
TREND_BARS = make_bars(TREND_CLOSES, symbol="SBIN")
TREND_DAYS = [ist_date(bar.timestamp) for bar in TREND_BARS]
RATE = {day: 100.0 * 1.0002**i for i, day in enumerate(TREND_DAYS)}
INDEX = {day: 1000.0 + i for i, day in enumerate(TREND_DAYS)}


@cache
def trend_runs():
    return tuple(run_rules(TREND_BARS, ["trend", "persona"], RATE, INDEX))


def sample_backtest_view():
    return build_backtest_view("SBIN", trend_runs(), assumptions())


class FakeService:
    """Stands in for DashboardService: returns canned views, raises canned errors, and records the queries."""

    def __init__(self, view=None, error=None, backtest=None, backtest_error=None):
        self.canned, self.error, self.queries = view, error, []
        self.canned_backtest, self.backtest_error, self.backtests = backtest, backtest_error, []

    def backtest(self, identifier):
        self.backtests.append(identifier)
        if self.backtest_error:
            raise self.backtest_error
        return self.canned_backtest or sample_backtest_view()

    def view(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.canned


CURRENT = {"service": FakeService(full_view())}


def athena_error(message="no LLM provider key is set"):
    return AthenaError(message)
```

Replace `tests/test_dashboard_charts.py` with:

```python
import plotly.graph_objects as go
import pytest
from dash_fakes import trend_runs

from athena.backtest.engine import BacktestResult, Config
from athena.dashboard.charts import RSI_OVERBOUGHT, RSI_OVERSOLD, equity_chart, price_chart, rsi_chart
from athena.technicals.candles import candles_from_bars
from athena.technicals.series import indicator_series
from bar_factory import make_bars

CANDLES = candles_from_bars(make_bars([100.0 + i * 0.5 for i in range(260)]))
SERIES = indicator_series(CANDLES)


def traces_by_name(figure):
    return {trace.name: trace for trace in figure.data}


def test_price_chart_has_candles_averages_bands_and_volume():
    figure = price_chart(CANDLES, SERIES, "SBIN")
    traces = traces_by_name(figure)
    assert list(traces) == ["Price", "SMA 50", "SMA 200", "Bollinger upper", "Bollinger lower", "Volume"]
    assert isinstance(traces["Price"], go.Candlestick) and isinstance(traces["Volume"], go.Bar)
    assert figure.layout.title.text == "SBIN"


def test_every_trace_has_one_point_per_candle_and_values_come_from_the_data():
    traces = traces_by_name(price_chart(CANDLES, SERIES, "SBIN"))
    for name, trace in traces.items():
        assert len(trace.x) == len(CANDLES), name
    assert traces["Price"].close[-1] == CANDLES[-1].close
    assert traces["SMA 50"].y[-1] == pytest.approx(SERIES.sma_50[-1])
    assert traces["Volume"].y[0] == CANDLES[0].volume


def test_volume_bars_are_coloured_by_candle_direction():
    traces = traces_by_name(price_chart(CANDLES, SERIES, "x"))
    assert set(traces["Volume"].marker.color) == {"#2e9e6b"}  # a steady climb: every candle closes up


def test_rsi_chart_has_the_series_and_the_two_reference_lines():
    figure = rsi_chart(CANDLES, SERIES)
    assert len(figure.data) == 1 and figure.data[0].name == "RSI 14"
    levels = sorted(shape.y0 for shape in figure.layout.shapes)
    assert levels == [RSI_OVERSOLD, RSI_OVERBOUGHT]
    assert tuple(figure.layout.yaxis.range) == (0, 100)


def test_charts_refuse_empty_or_misaligned_input():
    with pytest.raises(ValueError, match="no candles"):
        price_chart([], indicator_series([]), "x")
    with pytest.raises(ValueError, match="not aligned"):
        rsi_chart(CANDLES[:-1], SERIES)


def test_equity_chart_draws_both_curves_and_marks_each_trade_on_the_strategy_curve():
    result = trend_runs()[0].result
    traces = traces_by_name(equity_chart(result, "SBIN trend"))
    assert list(traces) == ["Strategy", "Buy and hold", "Entry", "Exit"]
    assert list(traces["Strategy"].y) == list(result.equity) and list(traces["Buy and hold"].y) == list(result.benchmark)
    trade = result.trades[0]
    level = dict(zip(result.days, result.equity))
    assert list(traces["Entry"].x) == [trade.entry_day] and list(traces["Entry"].y) == [level[trade.entry_day]]
    assert list(traces["Exit"].x) == [trade.exit_day] and list(traces["Exit"].y) == [level[trade.exit_day]]


def test_equity_chart_for_a_rule_that_never_trades_has_empty_marker_traces():
    result = trend_runs()[1].result
    traces = traces_by_name(equity_chart(result, "SBIN persona"))
    assert result.trades == () and len(traces["Entry"].x) == 0 and len(traces["Exit"].x) == 0 and len(traces["Strategy"].x) == len(result.days)


def test_equity_chart_refuses_a_result_without_days():
    with pytest.raises(ValueError, match="no days"):
        equity_chart(BacktestResult("x", Config(), (), (), (), (), ()), "x")
```

Replace `tests/test_dashboard_service.py` with:

```python
import pytest
from bar_factory import NOW, make_bars
from dash_fakes import FUNDAMENTALS, INDEX, RATE, TREND_BARS, ambiguous_result, resolution

from athena.contracts import AthenaError, EmptyRefreshError
from athena.dashboard.risk import RiskWorld
from athena.dashboard.service import DashboardService
from athena.orchestrator.builders import RequestCache
from athena.orchestrator.orchestrator import OK, OrchestrationResult

BARS = make_bars([100.0 + i * 0.5 for i in range(320)], symbol="SBIN")
VERDICT = {"verdict": "Hold", "conviction": 20, "key_risks": [], "resolution_path": "blend"}


class CountingFetch:
    def __init__(self):
        self.symbols = []

    def __call__(self, symbol):
        self.symbols.append(symbol)
        return BARS


class FakeOrchestrator:
    """Like the real one, it pulls the bars through the shared cache while it works."""

    def __init__(self, cache, result=None):
        self.cache, self.result, self.queries = cache, result, []

    def analyze(self, query):
        self.queries.append(query)
        result = self.result or OrchestrationResult(OK, query, resolution(), None, {}, {}, None, 0.0, VERDICT, ())
        if result.resolution:
            self.cache(result.resolution.identifier)
        return result


def make_service(result=None):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    orchestrator = FakeOrchestrator(cache, result)
    service = DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW)
    return service, fetch, orchestrator


def test_a_view_shares_one_download_between_the_specialists_and_the_charts():
    service, fetch, orchestrator = make_service()
    view = service.view("sbin")
    assert orchestrator.queries == ["sbin"] and fetch.symbols == ["SBIN"]
    assert view.identifier == "SBIN" and view.price_figure is not None
    assert [panel.title.split()[0] for panel in view.panels] == ["Technical", "Risk"]


def test_the_technical_panel_is_computed_and_the_risk_panel_reports_what_it_could_not():
    view = make_service()[0].view("sbin")
    technical, risk = view.panels
    assert technical.coverage == "full" and any(row.name == "rsi_14" for row in technical.rows)
    assert risk.coverage == "partial" and {"volatility_annualized", "max_drawdown"} <= {r.name for r in risk.rows}
    assert "beta" in risk.missing  # the (empty) market series were not available


def test_every_request_starts_with_a_fresh_download():
    service, fetch, _ = make_service()
    service.view("sbin")
    service.view("sbin")
    assert fetch.symbols == ["SBIN", "SBIN"]


def test_an_ambiguous_query_downloads_nothing_and_returns_candidates():
    service, fetch, _ = make_service(ambiguous_result())
    view = service.view("sbi")
    assert fetch.symbols == [] and view.candidates and view.price_figure is None


# ---- fundamentals source
class FakeFundamentals:
    def __init__(self, packet=None, error=None):
        self.packet, self.error, self.calls, self.clears = packet, error, [], 0

    def __call__(self, resolution):
        self.calls.append(resolution.identifier)
        if self.error:
            raise self.error
        return self.packet

    def clear(self):
        self.clears += 1


def service_with(fundamentals, asset_class="equity"):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    result = OrchestrationResult(OK, "sbin", resolution(asset_class), None, {}, {}, None, 0.0, VERDICT, ())
    orchestrator = FakeOrchestrator(cache, result)
    return DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW, fundamentals=fundamentals)


def test_a_stock_view_gets_the_fundamentals_panels_built_from_the_same_bars():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source).view("sbin")
    assert source.calls == ["SBIN"]
    assert [p.title for p in view.panels][2:] == ["Valuation", "Business quality", "Earnings"]


def test_each_request_starts_by_clearing_the_fundamentals_cache():
    source = FakeFundamentals(FUNDAMENTALS)
    service = service_with(source)
    service.view("sbin")
    service.view("sbin")
    assert source.clears == 2


def test_an_etf_never_asks_for_fundamentals():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source, "etf").view("niftybees")
    assert source.calls == [] and len(view.panels) == 2


def test_a_fundamentals_failure_becomes_a_note_and_the_rest_of_the_page_survives():
    view = service_with(FakeFundamentals(error=EmptyRefreshError("no statements for 'SBIN'"))).view("sbin")
    assert len(view.panels) == 2 and view.price_figure is not None
    assert "fundamentals unavailable: no statements for 'SBIN'" in view.notes


def test_without_a_fundamentals_source_nothing_changes():
    assert len(service_with(None).view("sbin").panels) == 2


def test_a_backtest_needs_a_long_history_source():
    service = make_service()[0]
    with pytest.raises(AthenaError, match="backtests are not available"):
        service.backtest("SBIN")


def test_a_backtest_replays_both_rules_on_the_long_history_of_the_identifier():
    asked = []

    def long_history(symbol):
        asked.append(symbol)
        return TREND_BARS

    cache = RequestCache(CountingFetch())
    service = DashboardService(
        FakeOrchestrator(cache), cache, RiskWorld(INDEX, RATE, {}), clock=lambda: NOW, long_history=long_history
    )
    view = service.backtest("SBIN")
    assert asked == ["SBIN"] and view.identifier == "SBIN"
    assert [panel.rule for panel in view.panels] == ["trend", "persona"]
    assert "15 bps per side" in view.assumptions and all(panel.blinded_identical for panel in view.panels)
```

Replace `tests/test_dashboard_app.py` with:

```python
import dash_fakes
from dash_fakes import FakeService, ambiguous_result, athena_error, full_view, sample_backtest_view
from streamlit.testing.v1 import AppTest

from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NO_VIEW
from athena.orchestrator.report import DISCLAIMER


def script():
    import dash_fakes

    from athena.dashboard.app import run

    run(dash_fakes.CURRENT["service"])


def open_app(service, query=None):
    dash_fakes.CURRENT["service"] = service
    app = AppTest.from_function(script, default_timeout=30).run()
    if query is not None:
        app.text_input[0].set_value(query).run()
    return app


def texts(elements):
    return [element.value for element in elements]


def test_an_empty_box_shows_a_prompt_and_calls_nothing():
    service = FakeService(full_view())
    app = open_app(service)
    assert not app.exception and app.title[0].value == "Athena"
    assert "Type an NSE ticker" in app.info[0].value and service.queries == []


def test_a_query_is_passed_to_the_service_trimmed():
    service = FakeService(full_view())
    open_app(service, "  sbin ")
    assert service.queries == ["sbin"]


def test_the_page_shows_instrument_verdict_conviction_and_specialists():
    app = open_app(FakeService(full_view()), "sbin")
    assert not app.exception
    assert "SBIN" in app.subheader[0].value and "State Bank of India" in app.subheader[0].value
    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {"Verdict": "Buy", "Conviction": "72"}
    assert any("quant_technical: bullish 72" in e.label for e in app.expander)
    assert any("Not run: valuation (not built yet)" in c for c in texts(app.caption))


def test_the_page_draws_both_charts_and_each_panel_with_as_of_and_coverage():
    app = open_app(FakeService(full_view()), "sbin")
    assert len(app.get("plotly_chart")) == 2
    captions = texts(app.caption)
    assert any("coverage: full" in c and "as of 2026-10-02" in c for c in captions)  # technical panel
    assert any("coverage: partial" in c for c in captions)  # risk panel
    assert len(app.dataframe) == 2
    assert any("not available" in e.label for e in app.expander)  # the risk panel's missing figure


def test_the_page_lists_key_risks_notes_and_the_disclaimer():
    app = open_app(FakeService(full_view()), "sbin")
    markdown = texts(app.markdown)
    assert any("Key risks" in m for m in markdown) and "- a data gap" in markdown
    captions = texts(app.caption)
    assert "Note: risk overlay missing" in captions and DISCLAIMER in captions


def test_an_ambiguous_name_shows_candidates_and_no_charts():
    app = open_app(FakeService(build_view(ambiguous_result())), "sbi")
    assert "could be more than one instrument" in app.warning[0].value
    assert len(app.dataframe) == 1 and app.get("plotly_chart") == []
    assert not app.metric


def test_a_result_with_no_view_warns_the_user():
    app = open_app(FakeService(full_view(status=NO_VIEW)), "sbin")
    assert any("No specialist had enough data" in w.value for w in app.warning)


def test_athena_errors_and_bad_input_are_shown_not_raised():
    app = open_app(FakeService(error=athena_error("no LLM provider key is set")), "sbin")
    assert not app.exception and app.error[0].value == "no LLM provider key is set"
    assert open_app(FakeService(error=ValueError("empty query")), "x").error[0].value == "empty query"
    assert not app.metric


def test_each_metric_table_has_one_text_value_column_so_the_browser_never_gets_mixed_types():
    app = open_app(FakeService(full_view()), "sbin")
    assert len(app.dataframe) == 2
    for frame in app.dataframe:
        assert {type(v) for v in frame.value["value"]} == {str}
    values = dict(zip(app.dataframe[0].value["metric"], app.dataframe[0].value["value"]))
    assert values["trend_alignment"] == "aligned_up" and values["last_close"] == "259.5"


def test_the_page_shows_the_three_fundamentals_panels_each_with_coverage():
    app = open_app(FakeService(full_view(fundamentals=dash_fakes.FUNDAMENTALS)), "sbin")
    assert not app.exception and len(app.dataframe) == 5
    bold = texts(app.markdown)
    assert all(any(title in m for m in bold) for title in ("Valuation", "Business quality", "Earnings"))
    captions = texts(app.caption)
    assert sum("annual to 2026-03-31, quarter to 2026-06-30" in c and "coverage: full" in c for c in captions) == 3
    for frame in app.dataframe:
        assert {type(v) for v in frame.value["value"]} == {str}


def test_the_page_offers_a_backtest_for_stocks_and_etfs_only():
    assert len(open_app(FakeService(full_view("equity")), "sbin").checkbox) == 1
    assert len(open_app(FakeService(full_view("etf")), "sbin").checkbox) == 1
    assert len(open_app(FakeService(full_view("mutual_fund")), "sbin").checkbox) == 0
    assert len(open_app(FakeService(build_view(ambiguous_result())), "sbi").checkbox) == 0
    assert len(open_app(FakeService(), "").checkbox) == 0


def test_nothing_is_replayed_until_the_box_is_ticked():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    assert service.backtests == [] and len(app.get("plotly_chart")) == 2


def test_ticking_the_box_replays_the_rules_without_asking_the_specialists_again():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    app.checkbox[0].check().run()
    assert not app.exception and service.backtests == ["SBIN"] and service.queries == ["sbin"]
    assert len(app.get("plotly_chart")) == 4  # the price and RSI charts, plus one equity curve per rule
    assert any("trend rule" in m for m in texts(app.markdown)) and any("persona rule" in m for m in texts(app.markdown))
    captions = texts(app.caption)
    assert any("same trades" in c and "Blinding check" in c for c in captions)
    assert any("15 bps per side" in c for c in captions)
    assert any("Past results do not predict future ones" in c and DISCLAIMER in c for c in captions)


def test_the_backtest_tables_compare_strategy_and_buy_and_hold_as_text():
    app = open_app(FakeService(full_view()), "sbin")
    app.checkbox[0].check().run()
    tables = [frame for frame in app.dataframe if "strategy" in frame.value.columns]
    assert len(tables) == 2
    for frame in tables:
        assert list(frame.value["measure"]) == ["Total return", "Yearly return", "Worst drawdown", "Sharpe"]
        assert {type(v) for column in ("strategy", "buy and hold") for v in frame.value[column]} == {str}


def test_redrawing_the_page_does_not_replay_the_same_backtest_twice():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    app.checkbox[0].check().run()
    app.checkbox[0].uncheck().run()
    assert len(app.get("plotly_chart")) == 2  # unticked: the curves go away
    app.checkbox[0].check().run()
    assert service.backtests == ["SBIN"] and service.queries == ["sbin"] and len(app.get("plotly_chart")) == 4


def test_a_rule_whose_blinded_run_differs_is_flagged_on_the_page():
    from dataclasses import replace

    from athena.dashboard.backtest_view import BacktestView

    view = sample_backtest_view()
    flagged = BacktestView(view.identifier, (replace(view.panels[0], blinded_identical=False), view.panels[1]), view.assumptions)
    app = open_app(FakeService(full_view(), backtest=flagged), "sbin")
    app.checkbox[0].check().run()
    blinding = [c for c in texts(app.caption) if c.startswith("Blinding check")]
    assert len(blinding) == 2 and "DIFFERENT trades" in blinding[0] and blinding[1].endswith("same trades")


def test_a_backtest_that_cannot_run_shows_the_reason_and_keeps_the_analysis():
    service = FakeService(full_view(), backtest_error=athena_error("not enough price history for SBIN"))
    app = open_app(service, "sbin")
    app.checkbox[0].check().run()
    assert not app.exception and app.error[0].value == "not enough price history for SBIN"
    assert len(app.metric) == 2 and len(app.get("plotly_chart")) == 2
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_backtest_view.py tests/test_dashboard_charts.py tests/test_dashboard_service.py tests/test_dashboard_app.py -q`
Expected: collection errors (`ModuleNotFoundError: No module named 'athena.dashboard.backtest_view'`, or `ImportError` for `equity_chart`).

- [ ] **Step 3: Write the implementation**

Create `src/athena/dashboard/backtest_view.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import plotly.graph_objects as go

from athena.backtest.cli import RuleRun
from athena.backtest.rules import RULES
from athena.backtest.summary import num, pct
from athena.dashboard.charts import equity_chart


@dataclass(frozen=True)
class BacktestPanel:
    rule: str
    description: str
    rows: tuple[tuple[str, str, str], ...]  # (measure, strategy, buy and hold)
    facts: tuple[str, ...]
    blinded_identical: bool
    figure: go.Figure


@dataclass(frozen=True)
class BacktestView:
    identifier: str
    panels: tuple[BacktestPanel, ...]
    assumptions: str


def build_backtest_view(identifier: str, runs: Sequence[RuleRun], assumptions: str) -> BacktestView:
    """Everything the page shows for a backtest: per rule a comparison table, the facts that do not fit it, and the curve."""
    panels = []
    for run in runs:
        s = run.summary
        rows = (
            ("Total return", pct(s.total_return), pct(s.benchmark_total_return)),
            ("Yearly return", pct(s.cagr), pct(s.benchmark_cagr)),
            ("Worst drawdown", pct(s.max_drawdown), pct(s.benchmark_max_drawdown)),
            ("Sharpe", num(s.sharpe), num(s.benchmark_sharpe)),
        )
        facts = [
            f"{s.start} to {s.end} ({s.bars} trading days); excess over buy and hold {pct(s.excess_return)}"
            + (f"; the market (NIFTY 50) returned {pct(s.index_total_return)}" if s.index_total_return is not None else ""),
            f"in the market {pct(s.time_in_market)} of days; {s.trades} trades ({s.stops} stopped out), win rate "
            f"{pct(s.win_rate)}, average trade {pct(s.average_trade)}, worst trade {pct(s.worst_trade)}",
        ]
        panels.append(
            BacktestPanel(
                s.rule, RULES[s.rule].description, rows, tuple(facts), run.blinded_identical,
                equity_chart(run.result, f"{identifier}: {s.rule} rule against buy and hold"),
            )
        )
    return BacktestView(identifier, tuple(panels), assumptions)
```

Replace `src/athena/dashboard/charts.py` with:

```python
from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from athena.backtest.engine import BacktestResult
from athena.technicals.candles import Candle
from athena.technicals.series import IndicatorSeries

UP, DOWN = "#2e9e6b", "#d8574b"
RSI_OVERBOUGHT, RSI_OVERSOLD = 70, 30


def _require(candles: Sequence[Candle], series: IndicatorSeries) -> None:
    if not candles:
        raise ValueError("no candles to chart")
    if len(series.days) != len(candles):
        raise ValueError("indicator series and candles are not aligned")


def price_chart(candles: Sequence[Candle], series: IndicatorSeries, title: str) -> go.Figure:
    """Candles with the 50- and 200-day averages and Bollinger Bands, volume underneath (PRD FR-6)."""
    _require(candles, series)
    days = [c.day for c in candles]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    figure.add_trace(
        go.Candlestick(
            x=days, open=[c.open for c in candles], high=[c.high for c in candles],
            low=[c.low for c in candles], close=[c.close for c in candles], name="Price",
            increasing_line_color=UP, decreasing_line_color=DOWN,
        ),
        row=1, col=1,
    )
    lines = (
        ("SMA 50", series.sma_50, "#e0a030", "solid"),
        ("SMA 200", series.sma_200, "#7a5ccf", "solid"),
        ("Bollinger upper", series.bb_upper, "#8a8f98", "dot"),
        ("Bollinger lower", series.bb_lower, "#8a8f98", "dot"),
    )
    for name, values, color, dash in lines:
        figure.add_trace(go.Scatter(x=days, y=list(values), name=name, mode="lines", line=dict(color=color, width=1, dash=dash)), row=1, col=1)
    figure.add_trace(
        go.Bar(x=days, y=[c.volume for c in candles], name="Volume", marker_color=[UP if c.close >= c.open else DOWN for c in candles]),
        row=2, col=1,
    )
    figure.update_layout(title=title, xaxis_rangeslider_visible=False, height=620, margin=dict(l=40, r=20, t=60, b=30), legend=dict(orientation="h"))
    return figure


def rsi_chart(candles: Sequence[Candle], series: IndicatorSeries, title: str = "RSI 14") -> go.Figure:
    """RSI with the conventional 30 and 70 reference lines."""
    _require(candles, series)
    figure = go.Figure(go.Scatter(x=list(series.days), y=list(series.rsi_14), name="RSI 14", mode="lines", line=dict(color="#3b7dd8", width=1.5)))
    figure.add_hline(y=RSI_OVERBOUGHT, line=dict(color=DOWN, width=1, dash="dot"))
    figure.add_hline(y=RSI_OVERSOLD, line=dict(color=UP, width=1, dash="dot"))
    figure.update_layout(title=title, yaxis=dict(range=[0, 100]), height=240, margin=dict(l=40, r=20, t=50, b=30), showlegend=False)
    return figure


def equity_chart(result: BacktestResult, title: str) -> go.Figure:
    """The strategy's equity curve against buy and hold (both start at the same cash), with each entry and exit marked."""
    if not result.days:
        raise ValueError("no days to chart")
    days = list(result.days)
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=days, y=list(result.equity), name="Strategy", mode="lines", line=dict(color="#3b7dd8", width=2)))
    figure.add_trace(go.Scatter(x=days, y=list(result.benchmark), name="Buy and hold", mode="lines", line=dict(color="#8a8f98", width=1.5)))
    level = dict(zip(days, result.equity))
    entries = [(t.entry_day, level.get(t.entry_day)) for t in result.trades if t.entry_day in level]
    exits = [(t.exit_day, level.get(t.exit_day)) for t in result.trades if t.exit_day in level]
    figure.add_trace(go.Scatter(x=[d for d, _ in entries], y=[v for _, v in entries], name="Entry", mode="markers", marker=dict(symbol="triangle-up", color=UP, size=8)))
    figure.add_trace(go.Scatter(x=[d for d, _ in exits], y=[v for _, v in exits], name="Exit", mode="markers", marker=dict(symbol="triangle-down", color=DOWN, size=8)))
    figure.update_layout(title=title, height=320, margin=dict(l=40, r=20, t=50, b=30), legend=dict(orientation="h"), yaxis_title="value of 100 invested")
    return figure
```

Replace `src/athena/dashboard/service.py` with:

```python
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path

from athena.backtest.cli import assumptions, run_rules
from athena.backtest.rules import RULES
from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import AthenaError, Bar
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.backtest_view import BacktestView, build_backtest_view
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.orchestrator.builders import RequestCache, history_fetcher
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.resolver import Resolution
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

BACKTEST_YEARS = 8


FundamentalsSource = Callable[[Resolution], dict]


class DashboardService:
    """Turns a typed instrument into a `DashboardView`: the orchestrator's verdict plus the charts and metric
    panels that sit around it."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        bars: RequestCache,
        risk_world: RiskWorld,
        clock: Callable[[], datetime] = utc_now,
        fundamentals: FundamentalsSource | None = None,
        long_history: Callable[[str], list[Bar]] | None = None,
    ):
        self._orchestrator = orchestrator
        self._bars = bars
        self._risk_world = risk_world
        self._clock = clock
        self._fundamentals = fundamentals
        self._long_history = long_history

    def view(self, query: str) -> DashboardView:
        self._bars.clear()
        if hasattr(self._fundamentals, "clear"):
            self._fundamentals.clear()
        result = self._orchestrator.analyze(query)
        if result.status == NEEDS_CLARIFICATION:
            return build_view(result)
        assert result.resolution is not None
        bars = self._bars(result.resolution.identifier)
        now = self._clock()
        technical = build_technical_packet(result.resolution.identifier, now, bars)
        risk = risk_packet(result.resolution, bars, self._risk_world, now)
        fundamentals, note = None, None
        if self._fundamentals is not None and result.resolution.asset_class == "equity":
            try:
                fundamentals = self._fundamentals(result.resolution)
            except AthenaError as exc:
                note = f"fundamentals unavailable: {exc}"
        return build_view(result, bars, technical, risk, fundamentals, note)


    def backtest(self, identifier: str) -> BacktestView:
        """Both technical rules over the long history, each against buy and hold, with the blinding check."""
        if self._long_history is None:
            raise AthenaError("backtests are not available in this session")
        runs = run_rules(self._long_history(identifier), list(RULES), self._risk_world.rate, self._risk_world.price_index)
        return build_backtest_view(identifier, runs, assumptions())


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel and the backtest, and the NIFTY 50
    valuation history, are loaded once when the service is built, so a session left open across days should be
    restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=365 * BACKTEST_YEARS))
    orchestrator = build_orchestrator(
        sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals
    )
    long_history = lru_cache(maxsize=8)(history_fetcher(sources.chain, days=365 * BACKTEST_YEARS))
    return DashboardService(
        orchestrator, sources.bars, risk_world_from_store(sources.store), fundamentals=sources.fundamentals,
        long_history=long_history,
    )
```

Replace `src/athena/dashboard/app.py` with:

```python
from __future__ import annotations

from typing import Protocol

import streamlit as st

from athena.contracts import AthenaError
from athena.dashboard.backtest_view import BacktestView
from athena.dashboard.view import DashboardView, Panel
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW
from athena.orchestrator.report import DISCLAIMER

PLACEHOLDER = "SBIN"
BACKTESTABLE = ("equity", "etf")
BACKTEST_NOTE = "Past results do not predict future ones."


class ViewService(Protocol):
    def view(self, query: str) -> DashboardView: ...

    def backtest(self, identifier: str) -> BacktestView: ...


def _show(value: str | float) -> str:
    """Numbers and labels share one column, so show every value as text (a mixed column cannot be sent to the browser)."""
    return value if isinstance(value, str) else f"{value:.4f}".rstrip("0").rstrip(".")


def _panel(panel: Panel) -> None:
    st.markdown(f"**{panel.title}**")
    st.caption(f"as of {panel.as_of}  |  source: {panel.source}  |  coverage: {panel.coverage}")
    if panel.rows:
        st.dataframe(
            [{"metric": r.name, "value": _show(r.value), "unit": r.unit, "window": r.window, "note": r.note} for r in panel.rows],
            hide_index=True, width="stretch",
        )
    if panel.missing:
        with st.expander(f"{len(panel.missing)} figure(s) not available"):
            for name, why in panel.missing.items():
                st.write(f"{name}: {why}")


def render(view: DashboardView) -> None:
    """Draw one `DashboardView`: verdict, specialist views, charts, then each metric panel with its as-of and coverage."""
    if view.status == NEEDS_CLARIFICATION:
        st.warning(f"{view.query!r} could be more than one instrument ({' '.join(view.notes)}). Type the exact symbol.")
        st.dataframe(
            [{"symbol": c.identifier, "name": c.name, "class": c.asset_class, "match": round(c.score, 2)} for c in view.candidates],
            hide_index=True, width="stretch",
        )
        return

    st.subheader(f"{view.identifier}  {view.name}")
    st.caption(f"{view.asset_class}  |  matched by {view.match}")
    verdict = view.verdict or {}
    left, right = st.columns(2)
    left.metric("Verdict", verdict.get("verdict", "-"))
    right.metric("Conviction", verdict.get("conviction", 0))
    if view.status == NO_VIEW:
        st.warning("No specialist had enough data to form a view.")

    st.markdown("**Specialists**")
    for row in view.specialists:
        with st.expander(f"{row.name}: {row.signal} {row.confidence}  (coverage {row.coverage})"):
            st.write(row.reasoning)
            if row.missing:
                st.caption("Missing: " + ", ".join(row.missing))
    if view.skipped:
        st.caption("Not run: " + "; ".join(f"{name} ({why})" for name, why in view.skipped.items()))

    if view.price_figure is not None:
        st.plotly_chart(view.price_figure, width="stretch")
        st.plotly_chart(view.rsi_figure, width="stretch")
    for panel in view.panels:
        _panel(panel)

    risks = (view.verdict or {}).get("key_risks", [])
    if risks:
        st.markdown("**Key risks**")
        for risk in risks:
            st.write(f"- {risk}")
    for note in view.notes:
        st.caption(f"Note: {note}")
    st.caption(DISCLAIMER)


def render_backtest(view: BacktestView) -> None:
    """Draw a `BacktestView`: per rule a comparison table, the facts that do not fit it, the blinding check and the curve."""
    st.markdown(f"**Backtest {view.identifier}**")
    for panel in view.panels:
        st.markdown(f"**{panel.rule} rule**: {panel.description}")
        st.dataframe(
            [{"measure": measure, "strategy": strategy, "buy and hold": hold} for measure, strategy, hold in panel.rows],
            hide_index=True, width="stretch",
        )
        for fact in panel.facts:
            st.caption(fact)
        st.caption(
            "Blinding check (ticker removed, dates shifted 28 years, prices rescaled to 100): "
            + ("same trades" if panel.blinded_identical else "DIFFERENT trades, so the rule may depend on the name or the price level")
        )
        st.plotly_chart(panel.figure, width="stretch")
    st.caption(view.assumptions)
    st.caption(f"{BACKTEST_NOTE} {DISCLAIMER}")


def _remembered(key: str, token: str, compute):
    """Streamlit reruns the whole page on every click. Keep the last result per input, so a click redraws it instead of
    asking the specialists or replaying eight years again."""
    saved = st.session_state.get(key)
    if saved is not None and saved[0] == token:
        return saved[1]
    value = compute()
    st.session_state[key] = (token, value)
    return value


def _backtest_section(service: ViewService, identifier: str) -> None:
    if not st.checkbox(
        "Backtest the technical rules over 8 years",
        key=f"backtest-{identifier}",
        help="Replays each rule day by day on this instrument's price history and compares it with buy and hold.",
    ):
        return
    try:
        with st.spinner("Downloading history and replaying every trading day..."):
            result = _remembered("backtest", identifier, lambda: service.backtest(identifier))
    except (AthenaError, ValueError) as exc:
        st.error(str(exc))
        return
    render_backtest(result)


def run(service: ViewService) -> None:
    st.set_page_config(page_title="Athena", layout="wide")
    st.title("Athena")
    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER).strip()
    if not query:
        st.info("Type an NSE ticker, an ISIN or a name to analyze it.")
        return
    try:
        with st.spinner("Resolving, fetching data and asking the specialists..."):
            view = _remembered("view", query, lambda: service.view(query))
    except (AthenaError, ValueError) as exc:
        st.error(str(exc))
        return
    render(view)
    if view.status != NEEDS_CLARIFICATION and view.asset_class in BACKTESTABLE:
        _backtest_section(service, view.identifier)


@st.cache_resource(show_spinner="Loading NSE lists, market series and models...")
def _live_service():
    from athena.dashboard.service import live_service

    return live_service()


if __name__ == "__main__":
    run(_live_service())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_backtest_view.py tests/test_dashboard_charts.py tests/test_dashboard_service.py tests/test_dashboard_app.py -q`
Expected: `all passed` (42 tests). Then `.venv/Scripts/python -m pyflakes src/athena/dashboard tests/dash_fakes.py tests/test_dashboard_*.py` prints nothing. Then the whole suite: `.venv/Scripts/python -m pytest -q` shows `527 passed, 55 skipped`.

- [ ] **Step 5: Mutation check**

Save the helper below as `$TEMP/mutate_dash.py` and run `.venv/Scripts/python $TEMP/mutate_dash.py`.

```python
import pathlib
import subprocess
import sys

PY = sys.executable

MUTATIONS = [
    ("app: view not remembered", "src/athena/dashboard/app.py",
     'view = _remembered("view", query, lambda: service.view(query))', "view = service.view(query)"),
    ("app: backtest not remembered", "src/athena/dashboard/app.py",
     'result = _remembered("backtest", identifier, lambda: service.backtest(identifier))', "result = service.backtest(identifier)"),
    ("app: funds get a backtest", "src/athena/dashboard/app.py",
     'BACKTESTABLE = ("equity", "etf")', 'BACKTESTABLE = ("equity", "etf", "mutual_fund")'),
    ("app: blinding flag ignored", "src/athena/dashboard/app.py",
     "+ (\"same trades\" if panel.blinded_identical", "+ (\"same trades\" if True or panel.blinded_identical"),
    ("app: errors not caught", "src/athena/dashboard/app.py",
     "    except (AthenaError, ValueError) as exc:\n        st.error(str(exc))\n        return\n    render_backtest(result)",
     "    except KeyError as exc:\n        st.error(str(exc))\n        return\n    render_backtest(result)"),
    ("charts: entry and exit swapped", "src/athena/dashboard/charts.py",
     'name="Entry", mode="markers"', 'name="Exit", mode="markers"'),
    ("charts: benchmark plotted as strategy", "src/athena/dashboard/charts.py",
     "y=list(result.equity), name=\"Strategy\"", "y=list(result.benchmark), name=\"Strategy\""),
    ("view: columns swapped", "src/athena/dashboard/backtest_view.py",
     '("Total return", pct(s.total_return), pct(s.benchmark_total_return))', '("Total return", pct(s.benchmark_total_return), pct(s.total_return))'),
    ("view: market fact always shown", "src/athena/dashboard/backtest_view.py",
     "if s.index_total_return is not None else \"\"", "if True else \"\""),
    ("service: only one rule", "src/athena/dashboard/service.py",
     "list(RULES), self._risk_world.rate", "list(RULES)[:1], self._risk_world.rate"),
    ("service: no guard", "src/athena/dashboard/service.py",
     "if self._long_history is None:", "if False:"),
]

for name, path, old, new in MUTATIONS:
    p = pathlib.Path(path)
    original = p.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    if old not in text:
        print("NOT FOUND", name)
        continue
    p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    try:
        run = subprocess.run(
            [PY, "-m", "pytest", "tests/test_dashboard_app.py", "tests/test_dashboard_charts.py", "tests/test_dashboard_service.py",
             "tests/test_dashboard_backtest_view.py", "-q", "-x", "-p", "no:cacheprovider"],
            capture_output=True, text=True,
        )
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-200:]
        print(("CAUGHT  " if run.returncode == 1 else "ERROR   " if run.returncode else "SURVIVED"), name, "|", tail)
    finally:
        p.write_bytes(original)
```

Expected: 11 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/dashboard tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add a backtest section to the dashboard for stocks and ETFs" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 6: Live check, TRD and cleanup

**Files:**
- Create: `tests/live/test_live_backtest.py`
- Modify: `TRD.md` (by the script below), `src/athena/evaluation/resolver_eval.py` (remove two unused imports)

- [ ] **Step 1: Add the live test**

Create `tests/live/test_live_backtest.py`:

```python
import pytest

from athena.backtest.cli import analyze, live_world

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def world():
    return live_world(years=8)


def test_live_stock_history_has_one_equity_price_per_day_with_no_bond_rows(world):
    bars = world.fetch_bars("SBIN")
    days = [bar.timestamp for bar in bars]
    assert len(days) == len(set(days)) and len(bars) > 1500  # eight years is about 1,950 sessions
    assert max(bar.high for bar in bars) < 2000  # the bond series trade near 10,000


def test_live_backtest_of_a_stock_reports_both_rules_against_buy_and_hold(world):
    text, code = analyze(world, "SBIN", ["trend", "persona"])
    print("\n" + text)
    assert code == 0
    for expected in ("rule: trend", "rule: persona", "buy and hold", "market (NIFTY 50)", "same trades", "15 bps per side"):
        assert expected in text
    assert "DIFFERENT" not in text  # blinding must not change a rule that reads only the price pattern


def test_live_backtest_of_an_etf_runs(world):
    text, code = analyze(world, "NIFTYBEES", ["trend"])
    print("\n" + text)
    assert code == 0 and "Backtest NIFTYBEES" in text


def test_live_market_series_cover_the_backtest_window(world):
    assert len(world.index) > 1500 and len(world.riskfree) > 1500
```

- [ ] **Step 2: Run it against real data**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/live/test_live_backtest.py --live -q -s`
Expected: `4 passed` (about 30 seconds; the report for SBIN and NIFTYBEES is printed). No API key is needed. If NSE is unreachable the test fails with the fetch error; that is an environment problem, not a code defect — report it instead of editing the test.

- [ ] **Step 3: Remove the two unused imports**

In `src/athena/evaluation/resolver_eval.py` delete the line `import string` and change `from athena.resolver import Ambiguity, InstrumentIndex, InstrumentResolver, Resolution` to `from athena.resolver import InstrumentIndex, InstrumentResolver, Resolution`. Run `.venv/Scripts/python -m pyflakes src tests`; it must print nothing.

- [ ] **Step 4: Record the decision in the TRD**

Save the script below as `$TEMP/trd_1g.py` and run `.venv/Scripts/python $TEMP/trd_1g.py` from the repository root. It prints `trd_1g applied to ...`; any `AssertionError` means the TRD text differs from what the script expects: stop and report it.

```python
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "TRD.md")
raw = path.read_bytes().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def swap(old, new):
    global t
    assert t.count(old) == 1, old[:70]
    t = t.replace(old, new)


swap(
    "**2.10 Paper-trading / backtest engine.** Backtest simulator on backtrader's `Cerebro`/`BackBroker`/pandas-feed/`SignalStrategy` pattern (see §4 for the licensing caveat and the FinRL alternative) or FinRL directly. ",
    "**2.10 Paper-trading / backtest engine.** *Decided Oct 6, 2026 (Phase 1g): a small in-house engine in `athena.backtest`, in the shape of backtrader's broker/strategy split but with no dependency* (backtrader is GPLv3 and dormant, FinRL is a heavy reinforcement-learning framework this needs none of). Long-only, one position, all in; a decision is made at a close from the bars up to that close and filled at the next open; a protective stop (2 x ATR below the entry fill) fills at the stop, or at the open when the bar gaps through it; costs are 15 bps per side (an approximation of delivery charges); cash earns nothing. Rules are deterministic functions of the technical packet (`trend`, and `persona`, the Quant/Technical setup), so a run needs no language model and costs nothing. Every run is also replayed on blinded bars (ticker `ASSET`, dates shifted back 28 years so weekdays and month lengths line up, prices rescaled to 100) and must trade identically; results are compared with buy and hold (same first open, same costs), the market (NIFTY 50) and the overnight rate (Sharpe). **Not backtested:** the language-model specialists (a model may remember how a name performed, and each replayed day costs a call) and the fundamentals (Yahoo statements are not point-in-time). Run it with `python -m athena.backtest SBIN`, or tick the backtest box on the dashboard for a stock or ETF. Live/paper execution stays a separate layer: ",
)
swap(
    "| Backtesting | backtrader **or** FinRL — open decision, see §9 | backtrader: GPLv3 (dormant since 2023); FinRL: actively maintained | If backtrader, treat as a maintained fork with legal sign-off, not a tracked upstream |",
    "| Backtesting | In-house engine (`athena.backtest`), decided Oct 6, 2026 | No dependency; backtrader (GPLv3, dormant since 2023) and FinRL (heavy) were the alternatives | Reversible: the engine is one module behind `run_backtest` |",
)
swap(
    "+ a stock backtest on backtrader or FinRL.",
    "+ a stock backtest (in-house engine, Phase 1g).",
)
swap(
    "1. **backtrader vs. FinRL** for the backtest/paper-trading core — GPLv3-and-dormant vs. actively-maintained-but-differently-shaped. Flagged directly in the architecture spec doc as a standing question; resolve before Phase 1 is load-bearing.",
    "1. ~~**backtrader vs. FinRL** for the backtest/paper-trading core~~ — *resolved Oct 6, 2026:* an in-house engine for the stock backtest (§2.10). Revisit only if the paper-trading layer needs a fuller broker simulator.",
)
swap(
    "| Equity Quant/Technical | OHLCV | jugaad-data, nsepython, Yahoo | Covered | Full |",
    "| Equity Quant/Technical | OHLCV | jugaad-data, nsepython, Yahoo | Covered | Full |\n"
    "| Stock and ETF backtest | OHLCV (8 years), NIFTY 50 close, Nifty 1D Rate Index | jugaad-data (equity series only), Yahoo, NSE index history | Covered (verified Oct 6, 2026 on SBIN and NIFTYBEES) | The two technical rules only; the language-model specialists and the fundamentals are not backtested |",
)
swap(
    "- **Oct 6, 2026 (price series fix)**",
    "- **Oct 6, 2026 (Phase 1g)** — Stock and ETF backtest implemented as an in-house engine; backtrader vs. FinRL resolved (see docs/superpowers/plans/2026-10-06-phase-1g-stock-backtest.md). First results on 8 years of SBIN: the trend rule returned +9% against +206% for buy and hold (Sharpe -0.16 against 0.57), and the persona rule was in the market on 1.6% of days with three trades, because \"all trends up\" puts price near its 60-day high while \"reward:risk of at least 2\" needs room back to that high. Neither rule beats buy and hold on SBIN or NIFTYBEES; the numbers are illustrative, not a finding about the market.\n- **Oct 6, 2026 (price series fix)**",
)
if crlf:
    t = t.replace("\n", "\r\n")
path.write_bytes(t.encode("utf-8"))
print("trd_1g applied to", path)
```

- [ ] **Step 5: Final verification**

Run `.venv/Scripts/python -m pytest -q` (expect `527 passed, 59 skipped`: the four new live tests are skipped without `--live`) and `git status --short` (expect only the files named in this task).

- [ ] **Step 6: Commit**

```bash
git add tests/live/test_live_backtest.py TRD.md src/athena/evaluation/resolver_eval.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live backtest checks; record the in-house engine decision in the TRD" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```
