# Phase 1h-c — Strategy Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a person backtest their own strategy, not only the two built-in rules: a strategy is plain, validated data (a buy condition, a sell condition, an optional stop) that can be built from a form, picked from presets, pasted as JSON or loaded from a file, and is run by the same engine with the same costs, fills, price adjustment and blinding check.

**Architecture:** A new package `athena.strategies` holds the format (`model`), the validating parser and its inverse (`parse`: `parse_strategy`, `to_dict`), a plain-English description (`describe`), a compiler that turns a strategy into entry and exit signals for every bar in one pass (`compile`), and four presets. Nothing in a strategy can run code: it is data, checked against fixed limits, and its values come from the existing indicator registry. The engine learns one new kind of rule, `SeriesRule`, whose signals are computed for the whole history up front and whose stop is its own; the packet rules (`trend`, `persona`) are untouched. The command line gains `--preset` and `--strategy`; the dashboard gains a picker (built-in rules, preset, form builder, pasted JSON) that runs a strategy only when the person presses Run.

**Tech Stack:** Python >= 3.11, pytest, numpy, ta-lib (all already dependencies), Streamlit 1.65. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-phase-1h-research-tools-design.md` part 1h-c; `PRD.md` FR-11; `TRD.md` §2.10.

**Plan series:** 0a-0e, 1a-1h-b (done) -> **1h-c (this plan)** -> 1h-d (text to strategy: ON HOLD, not part of this plan) -> risk overlay and investor profile -> later phases.

**Suggested models:** Sonnet at medium effort for the implementers (every step carries complete code or a verified script), Sonnet for reviewers, Opus for the final review. Prototyped in a scratch copy first: 698 offline tests passed (597 before), the live checks passed on real data, 37 deliberate mutations were each caught (the first pass found three that were not; the tests were strengthened), and the task-by-task steps below were re-run on a clean copy of the repository to prove they apply and pass (653, then 690, then 698 passed).

## Verified findings (7 Oct 2026)

- A strategy is data: `{"version": 1, "name", "entry", "exit", "stop_atr"}`. A condition is `all` / `any` (1 to 8 items) / `not` / a comparison `{"op": gt|ge|lt|le|crosses_above|crosses_below, "left", "right"}`. A value is `{"price": open|high|low|close|volume}`, `{"ind": key, "params", "line"}` for any of the 16 registry indicators (a multi-line indicator's line by name or number), `{"rolling": max|min|mean, "of": value, "window"}`, `{"const": number}` or `{"arith": add|sub|mul|div, "left", "right"}`; every value takes an optional `shift` (bars back). Limits: nesting 8, 60 nodes, window 2 to 1000, shift 0 to 500, stop above 0 and up to 10 ATR.
- The user's example ("buy when the price crosses the 52-week high, sell at the 52-week low") is the preset `breakout_52w`: buy when the close is above the highest close of the previous 252 bars, sell when it is below the lowest.
- Three-valued logic: a bar where a value cannot be computed yet (a 50-bar average on bar 10) is never a signal, and `not` of an unknown stays unknown instead of becoming true. A division by a zero value is unknown, not infinite.
- No lookahead: tests compile a strategy on the whole history and on truncated copies and require identical decisions on the shared bars, and corrupt every later bar and require unchanged earlier decisions. A rolling high that includes today can never be strictly exceeded by today's close, so a breakout needs `shift: 1`; a test pins this.
- Real data (SBIN, 8 years, 15 bps per side, buy and hold +256.4%): 52-week high breakout +99.6% (1 trade), golden cross +157.2% (5 trades), RSI mean reversion +56.4% (13 trades), Bollinger rebound -24.4% (35 trades). All four traded identically on the blinded replay. None beats buy and hold, which is the honest message the report already gives.
- A strategy file runs next to a built-in rule on RELIANCE with the 2024 bonus adjustment still applied.

**Honest limits:**
- Long-only, one position, all in, as in the existing engine; a strategy cannot size positions, short, or use fundamentals.
- Strategies see one instrument's daily bars (open, high, low, close, volume and the registry indicators). Market-wide or cross-instrument signals are not part of this plan.
- The form builder offers up to 4 conditions on each side, combined all-or-any; deeper logic is written as JSON.
- A strategy that depends on the price level (for example "close above 300") is flagged by the blinding check as DIFFERENT, because the blinded replay rescales prices to 100; ratio and indicator strategies are not.
- The pasted-JSON box and the strategy file are for people who write their own; language-model drafting of the same JSON (1h-d) is on hold.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and need no API key.
- A strategy is plain data validated against fixed limits; nothing in it is executed as code (no `eval`, no `exec`, no imports from a strategy).
- Every value at a bar uses only bars up to that close.
- The packet rules `trend` and `persona` and their results are unchanged.
- No global state; nothing reads keys from the environment; a strategy and its parts are frozen dataclasses with `parse_strategy` / `to_dict` as the plain-data boundary.
- Files in the repository use LF; no new dependency; match the surrounding code's comment density.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...` and end each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `git push` after each task.
- Windows, Git Bash: run Python as `.venv/Scripts/python`. Helper scripts live outside the repository (save them under `$TEMP` with the Write tool, never shell heredocs) and are run from the repository root.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/strategies/__init__.py` | new, empty |
| `src/athena/strategies/model.py` | new: the frozen dataclasses (`Strategy`, conditions, values), limits and `LINE_NAMES` |
| `src/athena/strategies/parse.py` | new: `parse_strategy(data) -> Strategy` (raises `StrategyError`, a `ValueError`, naming the path of the wrong part), `to_dict(strategy)` |
| `src/athena/strategies/describe.py` | new: `describe(strategy)`, the plain-English sentence |
| `src/athena/strategies/presets.py` | new: `PRESET_DATA`, `PRESETS` (four ready-made strategies) |
| `src/athena/strategies/compile.py` | new: `compile_strategy`, `strategy_rule(strategy) -> SeriesRule` |
| `src/athena/backtest/rules.py`, `engine.py` | modified: `Signals`, `SeriesRule`, `run_backtest` accepts a `SeriesRule` and uses its stop |
| `src/athena/backtest/cli.py` | modified: `run_rules` and `analyze` take rules or strategies, per-rule descriptions and stops, `--preset`, `--strategy`, `load_strategy_rule` |
| `src/athena/dashboard/backtest_view.py`, `service.py` | modified: a run's own description; `backtest(identifier, rules=None)` |
| `src/athena/dashboard/strategy_form.py` | new: `strategy_controls()` (mode, preset, form builder, JSON box, Run button) |
| `src/athena/dashboard/app.py` | modified: the backtest block uses `strategy_controls()` |
| `tests/test_strategies_parse.py`, `test_strategies_describe.py`, `test_strategies_compile.py`, `test_backtest_strategies.py` | new |
| `tests/test_backtest_cli.py`, `test_dashboard_service.py`, `test_dashboard_app.py`, `tests/dash_fakes.py` | modified |
| `tests/live/test_live_strategies.py` | new, opt-in |
| `TRD.md`, `PRD.md` | modified: §2.10, the data-source table, the revision history, FR-11 status |

---

### Task 1: The strategy format, its parser, description and presets

**Files:**
- Create: `src/athena/strategies/__init__.py` (empty), `model.py`, `parse.py`, `describe.py`, `presets.py`, `tests/test_strategies_parse.py`, `tests/test_strategies_describe.py` (the two test files are written by the script below)

**Interfaces:**
- Produces: `athena.strategies.model` (`Strategy(name, entry, exit, stop_atr=None)`, `Compare`, `AllOf`, `AnyOf`, `Not`, `Price`, `IndicatorRef`, `Const`, `Arith`, `Rolling`, `LINE_NAMES`, the limits `MAX_DEPTH = 8`, `MAX_NODES = 60`, `MAX_CONDITIONS = 8`, `MAX_WINDOW = 1000`, `MAX_SHIFT = 500`, `MAX_STOP_ATR = 10.0`); `parse.parse_strategy(data) -> Strategy`, `parse.to_dict(strategy) -> dict`, `parse.StrategyError`; `describe.describe(strategy) -> str`; `presets.PRESET_DATA: dict[str, dict]`, `presets.PRESETS: dict[str, Strategy]`.
- Consumes: `athena.technicals.indicators` (`REGISTRY`, `default_params`) from Plan 1h-b.

- [ ] **Step 1: Write the failing tests**

Save as `$TEMP/s1hc_tests_format.py` and run `.venv/Scripts/python $TEMP/s1hc_tests_format.py` from the repository root. It writes `tests/test_strategies_parse.py` and `tests/test_strategies_describe.py`.

```python
import pathlib

files = {}

files["tests/test_strategies_parse.py"] = '''import copy

import pytest
from bar_factory import make_bars

from athena.strategies.model import LINE_NAMES, MAX_DEPTH, MAX_NODES, AllOf, Compare, Const, IndicatorRef, Not, Price, Rolling
from athena.strategies.parse import StrategyError, parse_strategy, to_dict
from athena.strategies.presets import PRESET_DATA, PRESETS
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import REGISTRY, Selected, compute, default_params

CLOSE = {"price": "close"}
BASIC = {"name": "Basic", "entry": {"op": "gt", "left": CLOSE, "right": {"const": 100}}, "exit": {"op": "lt", "left": CLOSE, "right": {"const": 90}}}


def broken(path, value):
    """BASIC with one part replaced; `path` is a list of keys from the top."""
    data = copy.deepcopy(BASIC)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


def test_a_minimal_strategy_parses_with_a_default_name_no_stop_and_the_version_accepted():
    data = {"version": 1, "entry": BASIC["entry"], "exit": BASIC["exit"]}
    strategy = parse_strategy(data)
    assert strategy.name == "Custom strategy" and strategy.stop_atr is None
    assert strategy.entry == Compare("gt", Price("close"), Const(100.0))
    assert parse_strategy(BASIC).name == "Basic" and parse_strategy({**BASIC, "stop_atr": 2}).stop_atr == 2.0


def test_an_indicator_gets_its_missing_settings_filled_in_and_a_line_by_name_or_number():
    strategy = parse_strategy({"entry": {"op": "gt", "left": {"ind": "macd", "line": "signal", "params": {"fast": 8}}, "right": {"const": 0}}, "exit": BASIC["exit"]})
    ref = strategy.entry.left
    assert isinstance(ref, IndicatorRef) and ref.line == 1
    assert dict(ref.params) == {"fast": 8, "slow": 26, "signal": 9} and ref.params == tuple(sorted(ref.params))
    assert parse_strategy({"entry": {"op": "gt", "left": {"ind": "macd", "line": 2}, "right": {"const": 0}}, "exit": BASIC["exit"]}).entry.left.line == 2


def test_every_preset_parses_and_turns_back_into_equal_data():
    assert set(PRESETS) == set(PRESET_DATA) and len(PRESETS) == 4
    for key, strategy in PRESETS.items():
        assert parse_strategy(to_dict(strategy)) == strategy, key
    names = [strategy.name for strategy in PRESETS.values()]
    assert len(set(names)) == len(names)


def test_a_complex_strategy_round_trips_through_plain_data():
    data = {
        "name": "Complex", "stop_atr": 1.5,
        "entry": {"all": [
            {"op": "crosses_above", "left": {"ind": "sma", "params": {"length": 20}, "shift": 1}, "right": {"ind": "ema", "params": {"length": 50}}},
            {"any": [{"op": "lt", "left": {"ind": "rsi"}, "right": {"const": 40}}, {"not": {"op": "gt", "left": {"arith": "div", "left": CLOSE, "right": {"rolling": "mean", "of": CLOSE, "window": 30}}, "right": {"const": 1.2}}}]},
        ]},
        "exit": {"op": "le", "left": CLOSE, "right": {"rolling": "min", "of": {"price": "low"}, "window": 10, "shift": 2}},
    }
    strategy = parse_strategy(data)
    assert isinstance(strategy.entry, AllOf) and isinstance(strategy.entry.items[1].items[1], Not)
    assert parse_strategy(to_dict(strategy)) == strategy
    assert to_dict(parse_strategy(to_dict(strategy))) == to_dict(strategy)


@pytest.mark.parametrize(
    "data, expected",
    [
        ("not an object", "strategy: must be an object"),
        ({**BASIC, "extra": 1}, "strategy: unexpected key 'extra'"),
        ({"name": "x", "exit": BASIC["exit"]}, "strategy: missing 'entry'"),
        ({"name": "x", "entry": BASIC["entry"]}, "strategy: missing 'exit'"),
        ({**BASIC, "version": 2}, "only version 1 is supported"),
        ({**BASIC, "name": ""}, "strategy.name: must be text of 1 to 60 characters"),
        ({**BASIC, "name": "x" * 61}, "strategy.name: must be text of 1 to 60 characters"),
        ({**BASIC, "name": 5}, "strategy.name: must be text"),
        ({**BASIC, "stop_atr": 0}, "strategy.stop_atr: must be above 0 and at most 10"),
        ({**BASIC, "stop_atr": 11}, "strategy.stop_atr: must be above 0 and at most 10"),
        ({**BASIC, "stop_atr": "2"}, "strategy.stop_atr: must be a number"),
        ({**BASIC, "stop_atr": True}, "strategy.stop_atr: must be a number"),
        (broken(["entry"], {"op": "gt", "all": []}), "entry: must have exactly one of"),
        (broken(["entry"], {}), "entry: must have exactly one of"),
        (broken(["entry"], {"op": "gt", "left": CLOSE, "right": CLOSE, "zzz": 1}), "entry: unexpected key 'zzz'"),
        (broken(["entry", "op"], "equals"), "entry.op: must be one of gt, ge, lt, le, crosses_above, crosses_below"),
        (broken(["entry"], {"op": "gt", "left": CLOSE}), "entry: missing 'right'"),
        (broken(["entry"], {"all": []}), "entry.all: must be a list of 1 to 8 conditions"),
        (broken(["entry"], {"all": [BASIC["entry"]] * 9}), "entry.all: must be a list of 1 to 8 conditions"),
        (broken(["entry"], {"not": 5}), "entry.not: must be an object"),
        (broken(["entry", "left"], {"price": "vwap"}), "entry.left.price: must be one of open, high, low, close, volume"),
        (broken(["entry", "left"], {"price": "close", "shift": -1}), "entry.left.shift: must be between 0 and 500"),
        (broken(["entry", "left"], {"price": "close", "shift": 501}), "entry.left.shift: must be between 0 and 500"),
        (broken(["entry", "left"], {"price": "close", "shift": 1.5}), "entry.left.shift: must be a whole number"),
        (broken(["entry", "left"], {"price": "close", "colour": "red"}), "entry.left: unexpected key 'colour'"),
        (broken(["entry", "left"], {"price": "close", "const": 1}), "entry.left: must have exactly one of"),
        (broken(["entry", "left"], {"const": "5"}), "entry.left.const: must be a number"),
        (broken(["entry", "left"], {"const": float("inf")}), "entry.left.const: must be a number"),
        (broken(["entry", "left"], {"const": True}), "entry.left.const: must be a number"),
        (broken(["entry", "left"], {"ind": "nope"}), "entry.left.ind: unknown indicator 'nope'"),
        (broken(["entry", "left"], {"ind": "sma", "params": {"length": 1}}), "entry.left.params: Length must be between 2 and 400"),
        (broken(["entry", "left"], {"ind": "sma", "params": {"size": 5}}), "entry.left.params: Simple moving average has no setting called 'size'"),
        (broken(["entry", "left"], {"ind": "macd", "params": {"fast": 30, "slow": 20}}), "the fast length must be shorter than the slow length"),
        (broken(["entry", "left"], {"ind": "sma", "params": [1]}), "entry.left.params: must be an object"),
        (broken(["entry", "left"], {"ind": "sma", "line": 1}), "entry.left.line: must be between 0 and 0"),
        (broken(["entry", "left"], {"ind": "macd", "line": "histo"}), "entry.left.line: macd has the lines macd, signal, histogram"),
        (broken(["entry", "left"], {"rolling": "median", "of": CLOSE, "window": 5}), "entry.left.rolling: must be one of max, min, mean"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE}), "a rolling value needs 'of' and 'window'"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE, "window": 1}), "entry.left.window: must be between 2 and 1000"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE, "window": 1001}), "entry.left.window: must be between 2 and 1000"),
        (broken(["entry", "left"], {"arith": "pow", "left": CLOSE, "right": CLOSE}), "entry.left.arith: must be one of add, sub, mul, div"),
        (broken(["entry", "left"], {"arith": "add", "left": CLOSE}), "entry.left: missing 'right'"),
        (broken(["entry", "left"], {"arith": "div", "left": CLOSE, "right": {"const": 0}}), "entry.left.right: cannot divide by zero"),
    ],
)
def test_a_malformed_strategy_is_refused_with_the_path_of_the_wrong_part(data, expected):
    with pytest.raises(StrategyError) as caught:
        parse_strategy(data)
    assert expected in str(caught.value)


def test_a_strategy_error_is_a_value_error_so_callers_can_catch_either():
    assert issubclass(StrategyError, ValueError)


def test_nesting_and_size_are_bounded():
    nested = BASIC["entry"]
    for _ in range(MAX_DEPTH):
        nested = {"not": nested}
    with pytest.raises(StrategyError, match="nested more than 8 levels deep"):
        parse_strategy({**BASIC, "entry": nested})
    leaf = BASIC["entry"]
    wide = {"all": [{"any": [leaf] * 8}] * 8}  # 8 x 8 comparisons of 3 parts each: far over the limit
    with pytest.raises(StrategyError, match=f"more than {MAX_NODES} parts"):
        parse_strategy({**BASIC, "entry": wide})
    deep_expr = CLOSE
    for _ in range(MAX_DEPTH):
        deep_expr = {"arith": "add", "left": deep_expr, "right": {"const": 1}}
    with pytest.raises(StrategyError, match="nested more than 8 levels deep"):
        parse_strategy({**BASIC, "entry": {"op": "gt", "left": deep_expr, "right": {"const": 1}}})


def test_the_line_names_match_what_the_registry_computes_for_every_indicator():
    candles = candles_from_bars(make_bars([100.0 + i * 0.3 + (i % 5) for i in range(400)]))
    assert set(LINE_NAMES) == set(REGISTRY)
    for key in REGISTRY:
        lines = compute(candles, Selected("s-1", key, default_params(key)))
        assert len(lines) == len(LINE_NAMES[key]), key


def test_a_rolling_value_can_be_built_over_another_value_such_as_an_indicator():
    strategy = parse_strategy({**BASIC, "entry": {"op": "gt", "left": CLOSE, "right": {"rolling": "max", "of": {"ind": "rsi"}, "window": 5}}})
    assert isinstance(strategy.entry.right, Rolling) and isinstance(strategy.entry.right.of, IndicatorRef)
'''

files["tests/test_strategies_describe.py"] = '''from athena.strategies.describe import INDICATOR_NAMES, describe
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.technicals.indicators import REGISTRY

CLOSE = {"price": "close"}


def strategy(entry, exit_=None, **extra):
    return parse_strategy({"name": "t", "entry": entry, "exit": exit_ or {"op": "lt", "left": CLOSE, "right": {"const": 1}}, **extra})


def test_the_fifty_two_week_example_reads_as_the_sentence_a_person_would_say():
    assert describe(PRESETS["breakout_52w"]) == (
        "Buy when the close is at or above the highest close over the last 252 bars, as of 1 bar ago. "
        "Sell when the close is at or below the lowest close over the last 252 bars, as of 1 bar ago. No protective stop."
    )


def test_a_crossover_and_a_stop_are_described():
    text = describe(PRESETS["golden_cross"])
    assert "Buy when the simple moving average (length 50) crosses above the simple moving average (length 200)." in text
    assert "Sell when the simple moving average (length 50) crosses below" in text
    assert describe(PRESETS["rsi_reversion"]).endswith("Stop out if the price falls 2 x ATR below the entry fill.")


def test_and_or_not_and_arithmetic_are_spelled_out_with_brackets_where_they_nest():
    entry = {"all": [
        {"op": "gt", "left": CLOSE, "right": {"ind": "ema", "params": {"length": 20}, "shift": 3}},
        {"any": [{"op": "lt", "left": {"ind": "rsi"}, "right": {"const": 40}}, {"not": {"op": "ge", "left": {"arith": "div", "left": CLOSE, "right": {"const": 2}}, "right": {"price": "open"}}}]},
    ]}
    assert describe(strategy(entry)).startswith(
        "Buy when the close is above the exponential moving average (length 20) 3 bars ago and "
        "(the RSI (length 14) is below 40 or it is not true that (the close / 2) is at or above the open)."
    )


def test_a_multi_line_indicator_names_the_line_and_a_single_condition_has_no_brackets():
    text = describe(strategy({"op": "gt", "left": {"ind": "macd", "line": "signal"}, "right": {"const": 0}}))
    assert text.startswith("Buy when the MACD (fast 12, slow 26, signal 9) signal line is above 0.")
    single = describe(strategy({"all": [{"op": "gt", "left": CLOSE, "right": {"const": 1}}]}))
    assert single.startswith("Buy when the close is above 1. Sell when")


def test_every_indicator_has_a_plain_name_for_the_sentence():
    assert set(INDICATOR_NAMES) == set(REGISTRY)
'''


for path, text in files.items():
    pathlib.Path(path).write_text(text, encoding="utf-8", newline="\n")
print("format tests written")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_strategies_parse.py tests/test_strategies_describe.py -q`
Expected: collection errors `ModuleNotFoundError: No module named 'athena.strategies'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/strategies/__init__.py` as an empty file, then the four modules below (copy each verbatim).

`src/athena/strategies/model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

VERSION = 1
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
COMPARE_OPS = ("gt", "ge", "lt", "le", "crosses_above", "crosses_below")
ARITH_OPS = ("add", "sub", "mul", "div")
ROLLING_FNS = ("max", "min", "mean")
MAX_DEPTH = 8  # nesting of conditions and expressions
MAX_NODES = 60
MAX_CONDITIONS = 8  # inside one all / any
MAX_WINDOW = 1000
MAX_SHIFT = 500
MAX_STOP_ATR = 10.0

# The names of an indicator's lines, in the order the registry computes them, so a strategy can say which one it means.
LINE_NAMES: dict[str, tuple[str, ...]] = {
    "sma": ("value",), "ema": ("value",), "wma": ("value",), "bbands": ("upper", "lower"), "sar": ("value",),
    "channel": ("high", "low"), "rsi": ("value",), "macd": ("macd", "signal", "histogram"), "stoch": ("k", "d"),
    "adx": ("adx", "plus_di", "minus_di"), "atr": ("value",), "obv": ("value",), "cci": ("value",), "mfi": ("value",),
    "willr": ("value",), "roc": ("value",),
}


@dataclass(frozen=True)
class Price:
    field: str
    shift: int = 0


@dataclass(frozen=True)
class IndicatorRef:
    key: str
    params: tuple[tuple[str, float], ...]  # sorted by name, complete (defaults filled in)
    line: int = 0
    shift: int = 0


@dataclass(frozen=True)
class Const:
    value: float


@dataclass(frozen=True)
class Arith:
    op: str
    left: Expr
    right: Expr
    shift: int = 0


@dataclass(frozen=True)
class Rolling:
    fn: str
    of: Expr
    window: int
    shift: int = 0


Expr = Price | IndicatorRef | Const | Arith | Rolling


@dataclass(frozen=True)
class Compare:
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class AllOf:
    items: tuple[Cond, ...]


@dataclass(frozen=True)
class AnyOf:
    items: tuple[Cond, ...]


@dataclass(frozen=True)
class Not:
    item: Cond


Cond = Compare | AllOf | AnyOf | Not


@dataclass(frozen=True)
class Strategy:
    """A long-only strategy as data: when to buy, when to sell, and an optional protective stop. Nothing in it can run
    code; it is validated by `parse_strategy` and turned into signals by `compile_strategy`."""

    name: str
    entry: Cond
    exit: Cond
    stop_atr: float | None = None
```

`src/athena/strategies/parse.py`:

```python
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from athena.strategies.model import (
    ARITH_OPS,
    COMPARE_OPS,
    LINE_NAMES,
    MAX_CONDITIONS,
    MAX_DEPTH,
    MAX_NODES,
    MAX_SHIFT,
    MAX_STOP_ATR,
    MAX_WINDOW,
    PRICE_FIELDS,
    ROLLING_FNS,
    VERSION,
    AllOf,
    AnyOf,
    Arith,
    Compare,
    Cond,
    Const,
    Expr,
    IndicatorRef,
    Not,
    Price,
    Rolling,
    Strategy,
)
from athena.technicals.indicators import REGISTRY, Selected, default_params, problem

NAME_LIMIT = 60
CONDITION_KINDS = ("all", "any", "not", "op")
EXPR_KEYS = {
    "price": {"price", "shift"},
    "ind": {"ind", "params", "line", "shift"},
    "rolling": {"rolling", "of", "window", "shift"},
    "const": {"const"},
    "arith": {"arith", "left", "right", "shift"},
}


class StrategyError(ValueError):
    """The strategy is not valid; the message names the part that is wrong."""


class _Parser:
    def __init__(self) -> None:
        self.nodes = 0

    def node(self, path: str, depth: int) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise StrategyError(f"{path}: the strategy has more than {MAX_NODES} parts")
        if depth > MAX_DEPTH:
            raise StrategyError(f"{path}: nested more than {MAX_DEPTH} levels deep")

    def number(self, value: Any, path: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise StrategyError(f"{path}: must be a number")
        return value

    def whole(self, value: Any, path: str, low: int, high: int) -> int:
        number = self.number(value, path)
        if number != int(number):
            raise StrategyError(f"{path}: must be a whole number")
        if not low <= number <= high:
            raise StrategyError(f"{path}: must be between {low} and {high}")
        return int(number)

    def mapping(self, value: Any, path: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise StrategyError(f"{path}: must be an object")
        return value

    def condition(self, data: Any, path: str, depth: int) -> Cond:
        data = self.mapping(data, path)
        self.node(path, depth)
        kinds = [kind for kind in CONDITION_KINDS if kind in data]
        if len(kinds) != 1:
            raise StrategyError(f"{path}: must have exactly one of {', '.join(CONDITION_KINDS)}")
        kind = kinds[0]
        allowed = {"op", "left", "right"} if kind == "op" else {kind}
        extra = sorted(set(data) - allowed)
        if extra:
            raise StrategyError(f"{path}: unexpected key {extra[0]!r}")
        if kind in ("all", "any"):
            items = data[kind]
            if not isinstance(items, list) or not 1 <= len(items) <= MAX_CONDITIONS:
                raise StrategyError(f"{path}.{kind}: must be a list of 1 to {MAX_CONDITIONS} conditions")
            parsed = tuple(self.condition(item, f"{path}.{kind}[{n}]", depth + 1) for n, item in enumerate(items))
            return AllOf(parsed) if kind == "all" else AnyOf(parsed)
        if kind == "not":
            return Not(self.condition(data["not"], f"{path}.not", depth + 1))
        op = data["op"]
        if op not in COMPARE_OPS:
            raise StrategyError(f"{path}.op: must be one of {', '.join(COMPARE_OPS)}")
        for side in ("left", "right"):
            if side not in data:
                raise StrategyError(f"{path}: missing {side!r}")
        return Compare(op, self.expression(data["left"], f"{path}.left", depth + 1), self.expression(data["right"], f"{path}.right", depth + 1))

    def expression(self, data: Any, path: str, depth: int) -> Expr:
        data = self.mapping(data, path)
        self.node(path, depth)
        kinds = [kind for kind in EXPR_KEYS if kind in data]
        if len(kinds) != 1:
            raise StrategyError(f"{path}: must have exactly one of {', '.join(EXPR_KEYS)}")
        kind = kinds[0]
        extra = sorted(set(data) - EXPR_KEYS[kind])
        if extra:
            raise StrategyError(f"{path}: unexpected key {extra[0]!r}")
        shift = self.whole(data["shift"], f"{path}.shift", 0, MAX_SHIFT) if "shift" in data else 0
        if kind == "const":
            return Const(float(self.number(data["const"], f"{path}.const")))
        if kind == "price":
            if data["price"] not in PRICE_FIELDS:
                raise StrategyError(f"{path}.price: must be one of {', '.join(PRICE_FIELDS)}")
            return Price(data["price"], shift)
        if kind == "ind":
            return self.indicator(data, path, shift)
        if kind == "rolling":
            if data["rolling"] not in ROLLING_FNS:
                raise StrategyError(f"{path}.rolling: must be one of {', '.join(ROLLING_FNS)}")
            if "of" not in data or "window" not in data:
                raise StrategyError(f"{path}: a rolling value needs 'of' and 'window'")
            window = self.whole(data["window"], f"{path}.window", 2, MAX_WINDOW)
            return Rolling(data["rolling"], self.expression(data["of"], f"{path}.of", depth + 1), window, shift)
        if data["arith"] not in ARITH_OPS:
            raise StrategyError(f"{path}.arith: must be one of {', '.join(ARITH_OPS)}")
        for side in ("left", "right"):
            if side not in data:
                raise StrategyError(f"{path}: missing {side!r}")
        left = self.expression(data["left"], f"{path}.left", depth + 1)
        right = self.expression(data["right"], f"{path}.right", depth + 1)
        if data["arith"] == "div" and isinstance(right, Const) and right.value == 0:
            raise StrategyError(f"{path}.right: cannot divide by zero")
        return Arith(data["arith"], left, right, shift)

    def indicator(self, data: Mapping, path: str, shift: int) -> IndicatorRef:
        key = data["ind"]
        if key not in REGISTRY:
            raise StrategyError(f"{path}.ind: unknown indicator {key!r}; choose from {', '.join(REGISTRY)}")
        params = self.mapping(data.get("params", {}), f"{path}.params")
        full = {**default_params(key), **params}
        reason = problem(Selected("s-1", key, full))
        if reason:
            raise StrategyError(f"{path}.params: {reason}")
        names = LINE_NAMES[key]
        line = data.get("line", 0)
        if isinstance(line, str):
            if line not in names:
                raise StrategyError(f"{path}.line: {key} has the lines {', '.join(names)}")
            line = names.index(line)
        else:
            line = self.whole(line, f"{path}.line", 0, len(names) - 1)
        return IndicatorRef(key, tuple(sorted(full.items())), line, shift)


def parse_strategy(data: Any) -> Strategy:
    """A strategy from plain data (for example JSON), or a `StrategyError` that names what is wrong."""
    parser = _Parser()
    top = parser.mapping(data, "strategy")
    extra = sorted(set(top) - {"version", "name", "entry", "exit", "stop_atr"})
    if extra:
        raise StrategyError(f"strategy: unexpected key {extra[0]!r}")
    if "version" in top and top["version"] != VERSION:
        raise StrategyError(f"strategy.version: only version {VERSION} is supported")
    name = top.get("name", "Custom strategy")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_LIMIT:
        raise StrategyError(f"strategy.name: must be text of 1 to {NAME_LIMIT} characters")
    for side in ("entry", "exit"):
        if side not in top:
            raise StrategyError(f"strategy: missing {side!r}")
    stop = top.get("stop_atr")
    if stop is not None:
        stop = float(parser.number(stop, "strategy.stop_atr"))
        if not 0 < stop <= MAX_STOP_ATR:
            raise StrategyError(f"strategy.stop_atr: must be above 0 and at most {MAX_STOP_ATR:g}")
    return Strategy(name.strip(), parser.condition(top["entry"], "entry", 1), parser.condition(top["exit"], "exit", 1), stop)


def _expr_dict(expr: Expr) -> dict:
    if isinstance(expr, Const):
        return {"const": expr.value}
    if isinstance(expr, Price):
        out: dict = {"price": expr.field}
    elif isinstance(expr, IndicatorRef):
        out = {"ind": expr.key, "params": dict(expr.params), "line": LINE_NAMES[expr.key][expr.line]}
    elif isinstance(expr, Rolling):
        out = {"rolling": expr.fn, "of": _expr_dict(expr.of), "window": expr.window}
    else:
        out = {"arith": expr.op, "left": _expr_dict(expr.left), "right": _expr_dict(expr.right)}
    if expr.shift:
        out["shift"] = expr.shift
    return out


def _cond_dict(cond: Cond) -> dict:
    if isinstance(cond, Compare):
        return {"op": cond.op, "left": _expr_dict(cond.left), "right": _expr_dict(cond.right)}
    if isinstance(cond, Not):
        return {"not": _cond_dict(cond.item)}
    return {"all" if isinstance(cond, AllOf) else "any": [_cond_dict(item) for item in cond.items]}


def to_dict(strategy: Strategy) -> dict:
    """Plain data that `parse_strategy` turns back into an equal strategy."""
    out = {"version": VERSION, "name": strategy.name, "entry": _cond_dict(strategy.entry), "exit": _cond_dict(strategy.exit)}
    if strategy.stop_atr is not None:
        out["stop_atr"] = strategy.stop_atr
    return out
```

`src/athena/strategies/describe.py`:

```python
from __future__ import annotations

from athena.strategies.model import (
    LINE_NAMES,
    AllOf,
    Compare,
    Cond,
    Const,
    Expr,
    IndicatorRef,
    Not,
    Price,
    Rolling,
    Strategy,
)
from athena.technicals.indicators import REGISTRY

INDICATOR_NAMES = {
    "sma": "simple moving average", "ema": "exponential moving average", "wma": "weighted moving average",
    "bbands": "Bollinger Bands", "sar": "Parabolic SAR", "channel": "high-low channel", "rsi": "RSI", "macd": "MACD",
    "stoch": "Stochastic", "adx": "ADX", "atr": "ATR", "obv": "on-balance volume", "cci": "CCI", "mfi": "money flow index",
    "willr": "Williams %R", "roc": "rate of change",
}
OPERATORS = {
    "gt": "is above", "ge": "is at or above", "lt": "is below", "le": "is at or below",
    "crosses_above": "crosses above", "crosses_below": "crosses below",
}
ROLLING_WORDS = {"max": "highest", "min": "lowest", "mean": "average"}
ARITH_SYMBOLS = {"add": "+", "sub": "-", "mul": "x", "div": "/"}


def _bars(count: int) -> str:
    return f"{count} bar{'s' if count != 1 else ''}"


def _ago(shift: int) -> str:
    return f" {_bars(shift)} ago" if shift else ""


def _expr(expr: Expr, article: bool = True) -> str:
    the = "the " if article else ""
    if isinstance(expr, Const):
        return f"{expr.value:g}"
    if isinstance(expr, Price):
        return f"{the}{expr.field}{_ago(expr.shift)}"
    if isinstance(expr, IndicatorRef):
        values = dict(expr.params)
        settings = ", ".join(f"{param.name} {values[param.name]:g}" for param in REGISTRY[expr.key].params)
        names = LINE_NAMES[expr.key]
        line = f" {names[expr.line]} line" if len(names) > 1 else ""
        return f"{the}{INDICATOR_NAMES[expr.key]}{f' ({settings})' if settings else ''}{line}{_ago(expr.shift)}"
    if isinstance(expr, Rolling):
        tail = f", as of {_bars(expr.shift)} ago" if expr.shift else ""
        return f"{the}{ROLLING_WORDS[expr.fn]} {_expr(expr.of, False)} over the last {_bars(expr.window)}{tail}"
    return f"({_expr(expr.left)} {ARITH_SYMBOLS[expr.op]} {_expr(expr.right)}){_ago(expr.shift)}"


def _cond(cond: Cond, nested: bool = False) -> str:
    if isinstance(cond, Compare):
        return f"{_expr(cond.left)} {OPERATORS[cond.op]} {_expr(cond.right)}"
    if isinstance(cond, Not):
        return f"it is not true that {_cond(cond.item, True)}"
    joiner = " and " if isinstance(cond, AllOf) else " or "
    parts = [_cond(item, True) for item in cond.items]
    text = joiner.join(parts)
    return f"({text})" if nested and len(parts) > 1 else text


def describe(strategy: Strategy) -> str:
    """The strategy in plain English, for the person to read and confirm before it runs."""
    stop = (
        f"Stop out if the price falls {strategy.stop_atr:g} x ATR below the entry fill." if strategy.stop_atr is not None
        else "No protective stop."
    )
    return f"Buy when {_cond(strategy.entry)}. Sell when {_cond(strategy.exit)}. {stop}"
```

`src/athena/strategies/presets.py`:

```python
from __future__ import annotations

from athena.strategies.model import Strategy
from athena.strategies.parse import parse_strategy


def _close() -> dict:
    return {"price": "close"}


def _sma(length: int) -> dict:
    return {"ind": "sma", "params": {"length": length}}


PRESET_DATA: dict[str, dict] = {
    "breakout_52w": {
        "name": "52-week high breakout",
        "entry": {"op": "ge", "left": _close(), "right": {"rolling": "max", "of": _close(), "window": 252, "shift": 1}},
        "exit": {"op": "le", "left": _close(), "right": {"rolling": "min", "of": _close(), "window": 252, "shift": 1}},
    },
    "golden_cross": {
        "name": "Golden cross",
        "entry": {"op": "crosses_above", "left": _sma(50), "right": _sma(200)},
        "exit": {"op": "crosses_below", "left": _sma(50), "right": _sma(200)},
    },
    "rsi_reversion": {
        "name": "RSI mean reversion",
        "entry": {"op": "lt", "left": {"ind": "rsi", "params": {"length": 14}}, "right": {"const": 30}},
        "exit": {"op": "gt", "left": {"ind": "rsi", "params": {"length": 14}}, "right": {"const": 55}},
        "stop_atr": 2.0,
    },
    "bollinger_rebound": {
        "name": "Bollinger rebound",
        "entry": {"op": "crosses_above", "left": _close(), "right": {"ind": "bbands", "params": {"length": 20, "width": 2.0}, "line": "lower"}},
        "exit": {"op": "crosses_above", "left": _close(), "right": _sma(20)},
        "stop_atr": 2.0,
    },
}

PRESETS: dict[str, Strategy] = {key: parse_strategy(data) for key, data in PRESET_DATA.items()}
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_strategies_parse.py tests/test_strategies_describe.py -q` (expect `56 passed`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing), then the whole suite `.venv/Scripts/python -m pytest -q` (expect `653 passed, 63 skipped`).

- [ ] **Step 5: Mutation check**

Save the helper below as `$TEMP/mutate_1hc.py` (used again in Tasks 2 and 3). Run `.venv/Scripts/python $TEMP/mutate_1hc.py parse describe` from the repository root. Every line must start with `CAUGHT`; `SURVIVED`, `ERROR` or `NOT FOUND` means a test or the transcribed code is wrong. Files are restored automatically.

```python
import glob
import pathlib
import subprocess
import sys

PY = sys.executable
PARSE, COMPILE, ENGINE, CLI, FORM, DESCRIBE = (
    "src/athena/strategies/parse.py",
    "src/athena/strategies/compile.py",
    "src/athena/backtest/engine.py",
    "src/athena/backtest/cli.py",
    "src/athena/dashboard/strategy_form.py",
    "src/athena/strategies/describe.py",
)
TESTS = [
    path
    for path in (
        *sorted(glob.glob("tests/test_strategies_*.py")),
        "tests/test_backtest_strategies.py", "tests/test_backtest_cli.py", "tests/test_backtest_engine.py",
        "tests/test_dashboard_service.py", "tests/test_dashboard_app.py",
    )
    if pathlib.Path(path).exists()  # a task may run this before the later tests exist
]

MUTATIONS = [
    ("parse: nesting is not limited", PARSE, "        if depth > MAX_DEPTH:", "        if False and depth > MAX_DEPTH:"),
    ("parse: size is not limited", PARSE, "        if self.nodes > MAX_NODES:", "        if False and self.nodes > MAX_NODES:"),
    ("parse: dividing by a zero constant is allowed", PARSE, 'if data["arith"] == "div" and isinstance(right, Const) and right.value == 0:', "if False:"),
    ("parse: a window of 1 is allowed", PARSE, 'self.whole(data["window"], f"{path}.window", 2, MAX_WINDOW)', 'self.whole(data["window"], f"{path}.window", 1, MAX_WINDOW)'),
    ("parse: a negative shift is allowed", PARSE, 'self.whole(data["shift"], f"{path}.shift", 0, MAX_SHIFT)', 'self.whole(data["shift"], f"{path}.shift", -MAX_SHIFT, MAX_SHIFT)'),
    ("parse: unexpected keys in a value are ignored", PARSE, "        extra = sorted(set(data) - EXPR_KEYS[kind])\n        if extra:", "        extra = sorted(set(data) - EXPR_KEYS[kind])\n        if False and extra:"),
    ("parse: unexpected keys in a condition are ignored", PARSE, "        extra = sorted(set(data) - allowed)\n        if extra:", "        extra = sorted(set(data) - allowed)\n        if False and extra:"),
    ("parse: a line name is not looked up", PARSE, "            line = names.index(line)", "            line = 0"),
    ("parse: missing indicator settings are not filled in", PARSE, "        full = {**default_params(key), **params}", "        full = dict(params)"),
    ("parse: a stop of zero is allowed", PARSE, "if not 0 < stop <= MAX_STOP_ATR:", "if not 0 <= stop <= MAX_STOP_ATR:"),
    ("parse: the stop is lost on the way back to data", PARSE, "    if strategy.stop_atr is not None:\n        out[\"stop_atr\"]", "    if False:\n        out[\"stop_atr\"]"),
    ("parse: a shift is lost on the way back to data", PARSE, "    if expr.shift:\n        out[\"shift\"]", "    if False:\n        out[\"shift\"]"),
    ("parse: any list may be empty", PARSE, "not isinstance(items, list) or not 1 <= len(items) <= MAX_CONDITIONS", "not isinstance(items, list) or not 0 <= len(items) <= MAX_CONDITIONS"),
    ("compile: a shift reads the future", COMPILE, "        out[bars:] = values[:-bars]", "        out[:-bars] = values[bars:]"),
    ("compile: the rolling high is a low", COMPILE, '{"max": np.max, "min": np.min, "mean": np.mean}[fn]', '{"max": np.min, "min": np.min, "mean": np.mean}[fn]'),
    ("compile: a cross needs no earlier bar on the other side", COMPILE, "(left > right) & (prev_left <= prev_right) if cond.op", "(left > right) if cond.op"),
    ("compile: not of an unknown becomes true", COMPILE, "        return known & ~value, known", "        return ~value, known"),
    ("compile: all is only known when every part is", COMPILE, "        known = value | (knowns & ~values).any(axis=0)  # all true, or one known false", "        known = value"),
    ("compile: any is only known when it is true", COMPILE, "        known = value | (knowns & ~values).all(axis=0)  # one true, or all known false", "        known = value"),
    ("compile: a division by zero is infinite", COMPILE, "    result[~np.isfinite(result)] = np.nan", "    pass"),
    ("compile: the stop ATR has a different length", COMPILE, "ATR_LENGTH = 14", "ATR_LENGTH = 20"),
    ("compile: entry and exit are swapped", COMPILE, "wants = self._leave[index] if holding else self._enter[index]", "wants = self._enter[index] if holding else self._leave[index]"),
    ("compile: every indicator line is the first", COMPILE, "        return self._indicators[key][ref.line]", "        return self._indicators[key][0]"),
    ("compile: indicators are cached without their settings", COMPILE, "        key = (ref.key, ref.params)", "        key = (ref.key,)"),
    ("describe: the stop is not mentioned", DESCRIBE, '    return f"Buy when {_cond(strategy.entry)}. Sell when {_cond(strategy.exit)}. {stop}"', '    return f"Buy when {_cond(strategy.entry)}. Sell when {_cond(strategy.exit)}."'),
    ("describe: nested alternatives lose their brackets", DESCRIBE, '    return f"({text})" if nested and len(parts) > 1 else text', "    return text"),
    ("engine: a strategy keeps the run's stop", ENGINE, "        config = replace(config, stop_atr_multiple=rule.stop_atr)  # the strategy decides its own stop", "        pass"),
    ("cli: different stops are not told apart", CLI, "    if len(set(phrases.values())) == 1:", "    if True:"),
    ("cli: a run loses its description", CLI, "                rule.description,\n", '                "",\n'),
    ("cli: the built-in rules always run", CLI, "        if args.rule or not (args.preset or args.strategy):", "        if True:"),
    ("page: the run button is never switched off", FORM, 'disabled=strategy is None) and strategy is not None:', 'disabled=False) and strategy is not None:'),
    ("page: a strategy runs before the button is pressed", FORM, '    if not ran:\n        return False, None, ""', '    if not ran:\n        return True, None, "builtin"'),
    ("page: below means at or below", FORM, '"is below": "lt"', '"is below": "le"'),
    ("page: a stop of zero is sent on", FORM, "    if stop:\n        data[\"stop_atr\"]", "    if True:\n        data[\"stop_atr\"]"),
    ("page: any of them means all", FORM, 'return {"all" if combine == "all of them" else "any": conditions}', 'return {"all": conditions}'),
    ("page: a preset is not described", FORM, "        st.caption(describe(PRESETS[key]))\n", "        pass\n"),
    ("page: a pasted strategy is never run", FORM, '        st.session_state[RUN_KEY] = json.dumps(to_dict(strategy), sort_keys=True)', "        pass"),
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
        run = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True)
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-200:]
        print(("CAUGHT  " if run.returncode == 1 else "ERROR   " if run.returncode else "SURVIVED"), name, "|", tail)
    finally:
        p.write_bytes(original)
```

Expected: 15 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/strategies tests/test_strategies_parse.py tests/test_strategies_describe.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add a strategy format with a validating parser, a plain-English description and presets" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 2: Compile strategies, run them in the engine, and report them from the command line and the service

**Files:**
- Create: `src/athena/strategies/compile.py`, `tests/test_strategies_compile.py`, `tests/test_backtest_strategies.py` (the tests are written by the scripts below)
- Modify (by the scripts below): `src/athena/backtest/rules.py`, `src/athena/backtest/engine.py`, `src/athena/backtest/cli.py`, `src/athena/dashboard/backtest_view.py`, `src/athena/dashboard/service.py`, `tests/test_backtest_cli.py`, `tests/test_dashboard_service.py`

**Interfaces:**
- Produces: `compile.compile_strategy(strategy, candles) -> CompiledStrategy` (`decide(index, holding) -> (ENTRY | EXIT | None, atr | None)`), `compile.strategy_rule(strategy) -> SeriesRule`; `rules.ENTRY`, `rules.EXIT`, `rules.Signals`, `rules.SeriesRule(name, description, prepare, stop_atr)`; `engine.run_backtest(bars, rule: Rule | SeriesRule, config)` (a `SeriesRule`'s `stop_atr` replaces the run's stop setting in `result.config`); `cli.run_rules(bars, rules: Sequence[str | Rule | SeriesRule], riskfree, index, config=...)`, `RuleRun.description`, `cli.assumptions_for(runs, config=...)`, `cli.analyze(world, query, rules)`, `cli.load_strategy_rule(path) -> SeriesRule`, flags `--preset NAME` (repeatable), `--strategy FILE` (repeatable), `--rule` now defaults to None (both built-in rules run when no strategy flag is given); `DashboardService.backtest(identifier, rules=None)`.
- Consumes: Task 1's package; `athena.technicals.indicators` (`Selected`, `arrays`, `compute`).

- [ ] **Step 1: Write the failing tests**

Save these three scripts as `$TEMP/s1hc_tests_compile.py`, `$TEMP/s1hc_tests_backtest.py` and `$TEMP/s1hc_tests_service.py` and run them in that order from the repository root. They write `tests/test_strategies_compile.py` and `tests/test_backtest_strategies.py`, append to `tests/test_backtest_cli.py` and `tests/test_dashboard_service.py`.

`s1hc_tests_compile.py`:

```python
import pathlib

files = {}

files["tests/test_strategies_compile.py"] = '''import numpy as np
import pytest
import talib
from bar_factory import make_bars

from athena.backtest.rules import ENTRY, EXIT
from athena.strategies.compile import compile_strategy, strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import Selected, compute

CLOSE = {"price": "close"}
EXIT_NEVER = {"op": "gt", "left": CLOSE, "right": {"const": 1e9}}


def candles_of(closes):
    return candles_from_bars(make_bars(closes))


def entries(entry, closes, exit_=EXIT_NEVER):
    """Which bars the entry condition holds on, as a list of indexes."""
    compiled = compile_strategy(parse_strategy({"name": "t", "entry": entry, "exit": exit_}), candles_of(closes))
    return [i for i in range(len(closes)) if compiled.decide(i, False)[0] == ENTRY]


def compare(op, left, right):
    return {"op": op, "left": left, "right": right}


def test_a_comparison_with_a_number_holds_exactly_where_it_is_true():
    assert entries(compare("gt", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [3, 4]
    assert entries(compare("ge", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [2, 3, 4]
    assert entries(compare("lt", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [0, 1]
    assert entries(compare("le", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [0, 1, 2]


def test_a_value_from_some_bars_back_reads_the_past_and_nothing_is_known_before_it_exists():
    closes = [1, 2, 3, 2, 5]
    assert entries(compare("gt", CLOSE, {"price": "close", "shift": 1}), closes) == [1, 2, 4]
    assert entries(compare("gt", CLOSE, {"price": "close", "shift": 3}), closes) == [3, 4]  # nothing to compare with before bar 3


def test_a_rolling_high_includes_today_unless_it_is_shifted_so_a_breakout_needs_the_shift():
    closes = [1, 2, 3, 2, 5]
    window = {"rolling": "max", "of": CLOSE, "window": 3}
    assert entries(compare("ge", CLOSE, window), closes) == [2, 4]  # today is part of its own high
    assert entries(compare("gt", CLOSE, window), closes) == []  # so it can never be strictly above it
    assert entries(compare("gt", CLOSE, {**window, "shift": 1}), closes) == [4]  # the previous 3 bars: a real breakout


def test_a_rolling_low_and_average_are_computed_over_the_window():
    closes = [5, 4, 3, 6, 7, 2]
    assert entries(compare("le", CLOSE, {"rolling": "min", "of": CLOSE, "window": 3}), closes) == [2, 5]
    average = {"rolling": "mean", "of": CLOSE, "window": 2}
    assert entries(compare("gt", CLOSE, average), closes) == [3, 4]


def test_a_cross_happens_only_on_the_bar_it_crosses():
    closes = [1, 3, 5, 3, 5, 5]
    assert entries(compare("crosses_above", CLOSE, {"const": 4}), closes) == [2, 4]
    assert entries(compare("crosses_below", CLOSE, {"const": 4}), closes) == [3]
    assert entries(compare("crosses_above", CLOSE, {"const": 5}), closes) == []  # touching is not crossing


def test_a_moving_average_is_not_available_until_it_has_enough_bars_and_never_triggers_before():
    closes = [10.0] * 60
    assert entries(compare("ge", CLOSE, {"ind": "sma", "params": {"length": 50}}), closes) == list(range(49, 60))


def test_not_of_something_not_yet_computable_is_not_a_signal():
    closes = [10.0] * 60
    above = compare("gt", CLOSE, {"ind": "sma", "params": {"length": 50}})
    assert entries({"not": above}, closes) == list(range(49, 60))  # from the first bar the average exists, never before
    assert entries({"not": {"not": above}}, closes) == []


def test_all_and_any_follow_three_valued_logic():
    closes = [10.0] * 60
    known_true = compare("gt", CLOSE, {"const": 1})
    known_false = compare("lt", CLOSE, {"const": 1})
    unknown = compare("gt", CLOSE, {"ind": "sma", "params": {"length": 50}, "shift": 1})
    assert entries({"all": [known_true, unknown]}, closes) == []  # equal to the average: not above it
    assert entries({"any": [known_true, unknown]}, closes) == list(range(60))  # one true is enough, even if the other is unknown
    assert entries({"any": [known_false, unknown]}, closes) == []
    assert entries({"not": {"all": [known_false, unknown]}}, closes) == list(range(60))  # one known false makes the whole known false
    assert entries({"not": {"any": [known_true, unknown]}}, closes) == []
    assert entries({"not": {"any": [known_false, known_false]}}, closes) == list(range(60))  # an any is known false when every part is
    assert entries({"not": {"any": [known_false, unknown]}}, closes) == list(range(50, 60))  # not before the average exists
    assert entries({"not": {"all": [known_true, unknown]}}, closes) == list(range(50, 60))  # known only once the average exists


def test_arithmetic_works_and_a_division_by_a_zero_value_is_unknown_not_infinite():
    closes = [2, 4, 6, 8]
    assert entries(compare("gt", {"arith": "mul", "left": CLOSE, "right": {"const": 2}}, {"const": 9}), closes) == [2, 3]
    assert entries(compare("gt", {"arith": "add", "left": CLOSE, "right": {"price": "close", "shift": 1}}, {"const": 9}), closes) == [2, 3]
    assert entries(compare("lt", {"arith": "sub", "left": CLOSE, "right": {"const": 3}}, {"const": 2}), closes) == [0, 1]
    zero = {"arith": "sub", "left": CLOSE, "right": CLOSE}
    assert entries(compare("gt", {"arith": "div", "left": CLOSE, "right": zero}, {"const": -1e9}), closes) == []
    # a division by zero must not be a number that later steps can quietly step over: the lowest of a window holding it is unknown
    step = {"arith": "div", "left": CLOSE, "right": {"arith": "sub", "left": CLOSE, "right": {"price": "close", "shift": 1}}}
    assert entries(compare("gt", {"rolling": "min", "of": step, "window": 2}, {"const": 0}), [2, 2, 4, 8]) == [3]


def test_two_averages_of_different_lengths_are_not_confused_with_each_other():
    closes = [1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 5, 6]
    fast, slow = talib.SMA(np.array(closes, dtype=float), 2), talib.SMA(np.array(closes, dtype=float), 3)
    expected = [i for i in range(1, len(closes)) if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]]
    cross = compare("crosses_above", {"ind": "sma", "params": {"length": 2}}, {"ind": "sma", "params": {"length": 3}})
    assert expected and entries(cross, closes) == expected


def test_an_indicator_line_chosen_by_name_is_that_line_of_the_registry():
    closes = [100.0 + i * 0.4 + (i % 7) for i in range(120)]
    candles = candles_of(closes)
    signal = [v for v in compute(candles, Selected("s-1", "macd", {"fast": 12, "slow": 26, "signal": 9}))[1].values]
    macd = parse_strategy({"name": "t", "entry": compare("gt", {"ind": "macd", "line": "signal"}, {"const": 0}), "exit": EXIT_NEVER})
    compiled = compile_strategy(macd, candles)
    got = [i for i in range(len(closes)) if compiled.decide(i, False)[0] == ENTRY]
    assert got == [i for i, v in enumerate(signal) if v is not None and v > 0]


def test_every_line_of_a_multi_line_indicator_is_reachable_by_name():
    closes = [100.0 + (i % 4) for i in range(60)]
    closes[40] = 70.0  # a drop below the lower band
    candles = candles_of(closes)
    lower = compute(candles, Selected("s-1", "bbands", {"length": 20, "width": 2.0}))[1].values
    expected = [i for i, v in enumerate(lower) if v is not None and closes[i] < v]
    assert expected
    assert entries(compare("lt", CLOSE, {"ind": "bbands", "line": "lower"}), closes) == expected


def test_a_decision_is_entry_or_exit_with_the_atr_for_the_stop():
    closes = [100.0 + i * 0.4 + (i % 5) for i in range(60)]
    candles = candles_of(closes)
    strategy = parse_strategy({"name": "t", "entry": compare("gt", CLOSE, {"const": 110}), "exit": compare("lt", CLOSE, {"const": 110})})
    compiled = compile_strategy(strategy, candles)
    atr = talib.ATR(np.array([c.high for c in candles]), np.array([c.low for c in candles]), np.array([c.close for c in candles]), 14)
    action, value = compiled.decide(40, False)
    assert action == (ENTRY if candles[40].close > 110 else None) and value == pytest.approx(atr[40])
    assert compiled.decide(40, True)[0] == (EXIT if candles[40].close < 110 else None)
    assert compiled.decide(5, False)[1] is None  # ATR is not available on the first bars


def test_the_strategy_rule_carries_its_name_description_and_stop():
    rule = strategy_rule(PRESETS["rsi_reversion"])
    assert rule.name == "RSI mean reversion" and rule.stop_atr == 2.0 and rule.description.startswith("Buy when the RSI")
    assert strategy_rule(PRESETS["breakout_52w"]).stop_atr is None


def walk(count, seed=7):
    rng = np.random.default_rng(seed)
    return list(100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, count))))


COMPLEX = parse_strategy({
    "name": "complex",
    "entry": {"all": [
        {"op": "gt", "left": CLOSE, "right": {"rolling": "max", "of": CLOSE, "window": 40, "shift": 1}},
        {"any": [{"op": "lt", "left": {"ind": "rsi"}, "right": {"const": 70}}, {"not": {"op": "gt", "left": {"ind": "macd", "line": "histogram"}, "right": {"const": 0}}}]},
    ]},
    "exit": {"op": "crosses_below", "left": CLOSE, "right": {"ind": "ema", "params": {"length": 20}, "shift": 1}},
})


@pytest.mark.parametrize("strategy", [*PRESETS.values(), COMPLEX], ids=lambda s: s.name)
def test_a_decision_depends_only_on_the_bars_up_to_that_close(strategy):
    candles = candles_of(walk(500))
    full = compile_strategy(strategy, candles)
    for cut in (260, 333, 480):
        part = compile_strategy(strategy, candles[:cut])
        for holding in (False, True):
            assert [part.decide(i, holding) for i in range(cut)] == [full.decide(i, holding) for i in range(cut)], (cut, holding)


@pytest.mark.parametrize("strategy", [*PRESETS.values(), COMPLEX], ids=lambda s: s.name)
def test_wild_later_bars_cannot_change_an_earlier_decision(strategy):
    closes = walk(500)
    candles = candles_of(closes)
    garbage = candles_of(closes[:300] + [c * 7 + (i % 5) * 40 for i, c in enumerate(closes[300:])])
    full, other = compile_strategy(strategy, candles), compile_strategy(strategy, garbage)
    for holding in (False, True):
        assert [other.decide(i, holding) for i in range(300)] == [full.decide(i, holding) for i in range(300)]
'''


for path, text in files.items():
    pathlib.Path(path).write_text(text, encoding="utf-8", newline="\n")
print("compile tests written")
```

`s1hc_tests_backtest.py`:

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


pathlib.Path("tests/test_backtest_strategies.py").write_text(
    '''from dataclasses import replace

import numpy as np
import pytest
from bar_factory import NOW, make_bars

from athena.backtest.cli import assumptions_for, format_runs, run_rules
from athena.backtest.engine import Config, run_backtest
from athena.backtest.rules import RULES
from athena.contracts import Bar
from athena.strategies.compile import strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.technicals.candles import candles_from_bars
from athena.trading_calendar import ist_date

CLOSE = {"price": "close"}
SMALL = Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=0.0)


def strategy(entry, exit_, **extra):
    return strategy_rule(parse_strategy({"name": "t", "entry": entry, "exit": exit_, **extra}))


def above(level):
    return {"op": "gt", "left": CLOSE, "right": {"const": level}}


def below(level):
    return {"op": "lt", "left": CLOSE, "right": {"const": level}}


def with_opens(bars, offset):
    return [Bar(b.symbol, b.timestamp, b.close - offset, b.high, b.low, b.close, b.volume, NOW, "t") for b in bars]


def walk(count, seed=11, start=100.0, drift=0.0006):
    rng = np.random.default_rng(seed)
    return list(start * np.exp(np.cumsum(rng.normal(drift, 0.012, count))))


def market(bars):
    days = [ist_date(bar.timestamp) for bar in bars]
    return {day: 100.0 * 1.0002**i for i, day in enumerate(days)}, {day: 1000.0 + i for i, day in enumerate(days)}


def test_a_strategy_decides_at_a_close_and_is_filled_at_the_next_open():
    closes = [100.0 + i * 0.5 for i in range(60)] + [130.0 - i for i in range(60)]
    bars = with_opens(make_bars(closes), 0.5)
    candles = candles_from_bars(bars)
    entry_bar = next(i for i in range(20, 120) if closes[i] > 110)
    exit_bar = next(i for i in range(entry_bar + 1, 120) if closes[i] < 110)
    result = run_backtest(bars, strategy(above(110), below(110)), SMALL)
    trade = result.trades[0]
    assert trade.entry_price == candles[entry_bar + 1].open and trade.exit_price == candles[exit_bar + 1].open
    assert trade.reason == "signal" and candles[entry_bar + 1].open != candles[entry_bar + 1].close


def test_a_strategy_chooses_its_own_stop_not_the_runs_default():
    closes = [100.0] * 60
    bars = make_bars(closes)
    crash = list(bars)
    day = crash[40]
    crash[40] = Bar(day.symbol, day.timestamp, 100.0, 100.5, 80.0, 99.0, 1000.0, NOW, "t")  # trades down through a 2 x ATR stop
    with_stop = run_backtest(crash, strategy(above(50), below(50), stop_atr=2.0), replace(SMALL, stop_atr_multiple=None))
    without = run_backtest(crash, strategy(above(50), below(50)), replace(SMALL, stop_atr_multiple=2.0))
    assert with_stop.config.stop_atr_multiple == 2.0 and [t.reason for t in with_stop.trades] == ["stop"]
    assert without.config.stop_atr_multiple is None and without.trades == ()  # still holding: the run's default stop was not applied


def test_the_packet_rules_still_use_the_runs_stop_setting():
    bars = make_bars(walk(400))
    assert run_backtest(bars, RULES["trend"], Config(stop_atr_multiple=1.5)).config.stop_atr_multiple == 1.5


def test_run_rules_runs_names_and_strategies_together_each_with_its_own_description():
    bars = make_bars(walk(700), symbol="SBIN")
    rate, index = market(bars)
    runs = run_rules(bars, ["trend", strategy_rule(PRESETS["golden_cross"])], rate, index)
    assert [run.summary.rule for run in runs] == ["trend", "Golden cross"]
    assert runs[0].description == RULES["trend"].description and runs[1].description.startswith("Buy when the simple moving average")
    assert all(isinstance(run.blinded_identical, bool) for run in runs)


def test_an_unknown_rule_name_is_still_refused_before_any_run():
    bars = make_bars(walk(400))
    rate, index = market(bars)
    with pytest.raises(ValueError, match="unknown rule 'magic'"):
        run_rules(bars, [strategy_rule(PRESETS["golden_cross"]), "magic"], rate, index)


def test_the_blinding_check_flags_a_strategy_that_depends_on_the_price_level_but_not_one_that_uses_ratios():
    bars = make_bars([120.0 * 1.003**i for i in range(700)], symbol="SBIN")  # starts at 120, so blinding really rescales it
    rate, index = market(bars)
    level = strategy(above(300), below(250))
    ratio = strategy({"op": "gt", "left": CLOSE, "right": {"arith": "mul", "left": {"ind": "sma", "params": {"length": 50}}, "right": {"const": 1.02}}}, below(1))
    runs = run_rules(bars, [replace(level, name="level"), replace(ratio, name="ratio")], rate, index)
    assert [run.blinded_identical for run in runs] == [False, True]


def test_assumptions_name_each_stop_only_when_the_rules_use_different_ones():
    bars = make_bars(walk(700), symbol="SBIN")
    rate, index = market(bars)
    same = run_rules(bars, [strategy(above(100), below(100), stop_atr=2.0)], rate, index)
    assert "no protective stop" not in assumptions_for(same) and "depends on the rule" not in assumptions_for(same)
    mixed = run_rules(bars, ["trend", strategy(above(100), below(100))], rate, index)
    text = assumptions_for(mixed)
    assert "a stop that depends on the rule (trend: a stop 2 x ATR below the entry fill; t: no protective stop)" in text
    assert "Assumed costs 15 bps per side" in text and assumptions_for([]).startswith("Assumed costs")


def test_the_report_prints_each_strategy_description_and_a_blinding_verdict_per_rule():
    bars = make_bars(walk(700), symbol="SBIN")
    rate, index = market(bars)
    runs = run_rules(bars, [strategy_rule(PRESETS["rsi_reversion"]), strategy_rule(PRESETS["golden_cross"])], rate, index)
    text = format_runs("SBIN", runs)
    assert "Backtest SBIN  rule: RSI mean reversion" in text and "Backtest SBIN  rule: Golden cross" in text
    assert "Buy when the RSI (length 14) is below 30." in text and "RSI mean reversion: " in text and "Golden cross: " in text
    assert "a stop that depends on the rule" in text
''',
    encoding="utf-8",
    newline="\n",
)

edit(
    "tests/test_backtest_cli.py",
    [],
    append='''

def write_strategy(tmp_path, data):
    import json

    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


MINE = {"name": "Mine", "entry": {"op": "gt", "left": {"price": "close"}, "right": {"const": 1}}, "exit": {"op": "lt", "left": {"price": "close"}, "right": {"const": 1}}}


def test_with_no_flags_both_built_in_rules_run_and_a_preset_replaces_them(capsys):
    assert main(["SBIN"], factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    assert "rule: trend" in out and "rule: persona" in out
    assert main(["SBIN", "--preset", "rsi_reversion"], factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    assert "rule: RSI mean reversion" in out and "rule: trend" not in out and "Buy when the RSI (length 14) is below 30." in out


def test_a_built_in_rule_can_be_asked_for_alongside_a_preset_and_a_strategy_file(capsys, tmp_path):
    argv = ["SBIN", "--rule", "trend", "--preset", "golden_cross", "--strategy", write_strategy(tmp_path, MINE)]
    assert main(argv, factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    for expected in ("rule: trend", "rule: Golden cross", "rule: Mine", "Buy when the close is above 1."):
        assert expected in out
    assert "rule: persona" not in out


def test_a_bad_strategy_file_is_reported_without_a_traceback(capsys, tmp_path):
    assert main(["SBIN", "--strategy", str(tmp_path / "missing.json")], factory=lambda years: world()) == 1
    assert "error: cannot read" in capsys.readouterr().out
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main(["SBIN", "--strategy", str(broken)], factory=lambda years: world()) == 1
    assert "is not valid JSON" in capsys.readouterr().out
    bad = write_strategy(tmp_path, {**MINE, "entry": {"op": "gt", "left": {"ind": "nope"}, "right": {"const": 1}}})
    assert main(["SBIN", "--strategy", bad], factory=lambda years: world()) == 1
    assert "error: entry.left.ind: unknown indicator 'nope'" in capsys.readouterr().out


def test_an_unknown_preset_is_refused_by_the_argument_parser():
    with pytest.raises(SystemExit):
        main(["SBIN", "--preset", "magic"], factory=lambda years: world())
''',
)
print("backtest tests added")
```

`s1hc_tests_service.py`:

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the service runs any rules
edit(
    "tests/test_dashboard_service.py",
    [
        (
            "from athena.dashboard.service import DashboardService\n",
            "from athena.dashboard.service import DashboardService\nfrom athena.strategies.compile import strategy_rule\nfrom athena.strategies.presets import PRESETS\n",
        ),
    ],
    append='''

def test_a_backtest_can_run_a_strategy_instead_of_the_built_in_rules_and_describes_it():
    cache = RequestCache(CountingFetch())
    service = DashboardService(
        FakeOrchestrator(cache), cache, RiskWorld(INDEX, RATE, {}), clock=lambda: NOW, long_history=lambda symbol: TREND_BARS
    )
    view = service.backtest("SBIN", [strategy_rule(PRESETS["rsi_reversion"]), "trend"])
    assert [panel.rule for panel in view.panels] == ["RSI mean reversion", "trend"]
    assert view.panels[0].description.startswith("Buy when the RSI (length 14) is below 30.")
    assert "a stop that depends on the rule" not in view.assumptions  # both use a 2 x ATR stop
    assert [panel.rule for panel in service.backtest("SBIN").panels] == ["trend", "persona"]  # the default is unchanged
''',
)

print("service test added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_strategies_compile.py tests/test_backtest_strategies.py tests/test_backtest_cli.py tests/test_dashboard_service.py -q`
Expected: collection errors (`No module named 'athena.strategies.compile'`) and import errors for `SeriesRule`, `assumptions_for`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/strategies/compile.py`:

```python
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import talib

from athena.backtest.rules import ENTRY, EXIT, SeriesRule
from athena.strategies.describe import describe
from athena.strategies.model import (
    AllOf,
    Compare,
    Cond,
    Const,
    Expr,
    IndicatorRef,
    Not,
    Price,
    Rolling,
    Strategy,
)
from athena.technicals.candles import Candle
from athena.technicals.indicators import Selected, arrays, compute

ATR_LENGTH = 14  # the length that sizes a protective stop, as in the technical packet


class CompiledStrategy:
    """Entry and exit signals for every bar, computed once. The value at bar t uses only bars up to t."""

    def __init__(self, enter: np.ndarray, leave: np.ndarray, atr: np.ndarray) -> None:
        self._enter, self._leave, self._atr = enter, leave, atr

    def decide(self, index: int, holding: bool) -> tuple[str | None, float | None]:
        wants = self._leave[index] if holding else self._enter[index]
        atr = self._atr[index]
        return (EXIT if holding else ENTRY) if wants else None, (None if np.isnan(atr) else float(atr))


def _shift(values: np.ndarray, bars: int) -> np.ndarray:
    if bars == 0:
        return values
    out = np.full(len(values), np.nan)
    if bars < len(values):
        out[bars:] = values[:-bars]
    return out


def _rolling(values: np.ndarray, fn: str, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if window <= len(values):
        view = np.lib.stride_tricks.sliding_window_view(values, window)
        out[window - 1 :] = {"max": np.max, "min": np.min, "mean": np.mean}[fn](view, axis=1)  # NaN in a window gives NaN
    return out


class _Context:
    def __init__(self, candles: Sequence[Candle]) -> None:
        self.size = len(candles)
        self.candles = candles
        self.series = arrays(candles)
        self._indicators: dict[tuple, list[np.ndarray]] = {}

    def indicator(self, ref: IndicatorRef) -> np.ndarray:
        key = (ref.key, ref.params)
        if key not in self._indicators:
            lines = compute(self.candles, Selected("s-1", ref.key, dict(ref.params)))
            self._indicators[key] = [np.array([np.nan if v is None else v for v in line.values]) for line in lines]
        return self._indicators[key][ref.line]


def _expr(expr: Expr, ctx: _Context) -> np.ndarray:
    if isinstance(expr, Const):
        return np.full(ctx.size, expr.value)
    if isinstance(expr, Price):
        return _shift(ctx.series[expr.field], expr.shift)
    if isinstance(expr, IndicatorRef):
        return _shift(ctx.indicator(expr), expr.shift)
    if isinstance(expr, Rolling):
        return _shift(_rolling(_expr(expr.of, ctx), expr.fn, expr.window), expr.shift)
    left, right = _expr(expr.left, ctx), _expr(expr.right, ctx)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = {"add": left + right, "sub": left - right, "mul": left * right, "div": left / right}[expr.op]
    result[~np.isfinite(result)] = np.nan  # a division by a zero value is unknown, not infinite
    return _shift(result, expr.shift)


def _cond(cond: Cond, ctx: _Context) -> tuple[np.ndarray, np.ndarray]:
    """(value, known): three-valued logic, so a bar where something is not yet computable is never a signal, and
    `not` of an unknown stays unknown instead of becoming true."""
    if isinstance(cond, Compare):
        left, right = _expr(cond.left, ctx), _expr(cond.right, ctx)
        known = np.isfinite(left) & np.isfinite(right)
        with np.errstate(invalid="ignore"):
            if cond.op in ("crosses_above", "crosses_below"):
                prev_left, prev_right = _shift(left, 1), _shift(right, 1)
                known = known & np.isfinite(prev_left) & np.isfinite(prev_right)
                value = (left > right) & (prev_left <= prev_right) if cond.op == "crosses_above" else (left < right) & (prev_left >= prev_right)
            else:
                value = {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[cond.op]
        return value & known, known
    if isinstance(cond, Not):
        value, known = _cond(cond.item, ctx)
        return known & ~value, known
    parts = [_cond(item, ctx) for item in cond.items]
    values = np.array([value for value, _ in parts])
    knowns = np.array([known for _, known in parts])
    if isinstance(cond, AllOf):
        value = values.all(axis=0)
        known = value | (knowns & ~values).any(axis=0)  # all true, or one known false
    else:
        value = values.any(axis=0)
        known = value | (knowns & ~values).all(axis=0)  # one true, or all known false
    return value, known


def compile_strategy(strategy: Strategy, candles: Sequence[Candle]) -> CompiledStrategy:
    ctx = _Context(candles)
    enter, _ = _cond(strategy.entry, ctx)
    leave, _ = _cond(strategy.exit, ctx)
    atr = talib.ATR(ctx.series["high"], ctx.series["low"], ctx.series["close"], ATR_LENGTH) if ctx.size else np.array([])
    return CompiledStrategy(enter, leave, atr)


def strategy_rule(strategy: Strategy) -> SeriesRule:
    """The strategy as a rule the backtest engine can run, described in plain English."""
    return SeriesRule(strategy.name, describe(strategy), lambda candles: compile_strategy(strategy, candles), strategy.stop_atr)
```

Save the two scripts below as `$TEMP/s1hc_source_engine.py` and `$TEMP/s1hc_source_backtest.py` and run them from the repository root, in that order. `s1hc_source_engine.py` edits `src/athena/backtest/rules.py` and `engine.py`; `s1hc_source_backtest.py` edits `src/athena/backtest/cli.py`, `src/athena/dashboard/backtest_view.py` and `src/athena/dashboard/service.py`. Each prints one line when done; an `AssertionError` means the file differs from what the script expects: stop and report.

`s1hc_source_engine.py`:

```python
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t, encoding="utf-8", newline="\n")


# ---- rules: a second kind of rule, computed once over the whole history
edit(
    "src/athena/backtest/rules.py",
    [
        (
            "from collections.abc import Callable, Mapping\nfrom dataclasses import dataclass\nfrom typing import Any\n\nALIGNED_UP",
            "from collections.abc import Callable, Mapping, Sequence\nfrom dataclasses import dataclass\nfrom typing import Any, Protocol\n\n"
            "from athena.technicals.candles import Candle\n\nENTRY, EXIT = \"enter\", \"exit\"\nALIGNED_UP",
        ),
        (
            "def _trend_lost(",
            '''class Signals(Protocol):
    def decide(self, index: int, holding: bool) -> tuple[str | None, float | None]:
        """ENTRY, EXIT or None after the close of bar `index`, and the ATR to size a protective stop with (or None)."""


@dataclass(frozen=True)
class SeriesRule:
    """A long-only rule computed once over the whole history and read at each close. `prepare` must build signals in
    which the value at bar t uses only bars up to t (every indicator and rolling window the strategy format allows is
    causal), so reading index t is as safe as rebuilding the packet from the bars up to t. `stop_atr` is the
    protective stop in ATR multiples (None for no stop) and replaces the run's default."""

    name: str
    description: str
    prepare: Callable[[Sequence[Candle]], Signals]
    stop_atr: float | None = None


def _trend_lost(''',
        ),
    ],
)

# ---- engine: read either kind of rule
edit(
    "src/athena/backtest/engine.py",
    [
        ("from dataclasses import dataclass\n", "from dataclasses import dataclass, replace\n"),
        ("from athena.backtest.rules import Rule\n", "from athena.backtest.rules import ENTRY, EXIT, Rule, SeriesRule\n"),
        ('ENTRY, EXIT = "enter", "exit"\n', ""),
        (
            "def run_backtest(bars: Sequence[Bar], rule: Rule, config: Config = Config()) -> BacktestResult:",
            "def run_backtest(bars: Sequence[Bar], rule: Rule | SeriesRule, config: Config = Config()) -> BacktestResult:",
        ),
        (
            "    ordered = sorted(bars, key=lambda bar: bar.timestamp)\n    require_positive_prices(ordered)\n",
            "    if isinstance(rule, SeriesRule):\n        config = replace(config, stop_atr_multiple=rule.stop_atr)  # the strategy decides its own stop\n"
            "    ordered = sorted(bars, key=lambda bar: bar.timestamp)\n    require_positive_prices(ordered)\n",
        ),
        (
            "    cost = config.cost_bps_per_side / 10_000.0\n",
            "    if isinstance(rule, SeriesRule):\n        decider = rule.prepare(candles).decide\n    else:\n"
            "        def decider(index: int, holding: bool) -> tuple[str | None, float | None]:\n"
            "            return decide(ordered, index, rule, holding, config.window_bars)\n\n"
            "    cost = config.cost_bps_per_side / 10_000.0\n",
        ),
        ("            pending, pending_atr = decide(ordered, i, rule, shares > 0, config.window_bars)\n", "            pending, pending_atr = decider(i, shares > 0)\n"),
    ],
)
print("engine and rules edited")
```

`s1hc_source_backtest.py`:

```python
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t, encoding="utf-8", newline="\n")


# ---- the backtest command and report: rules may be strategies, each with its own description and stop
edit(
    "src/athena/backtest/cli.py",
    [
        ("import argparse\nfrom collections.abc import Callable\n", "import argparse\nimport json\nfrom collections.abc import Callable, Sequence\n"),
        ("from athena.backtest.rules import RULES\n", "from athena.backtest.rules import RULES, Rule, SeriesRule\n"),
        (
            "from athena.store import DataStore\n",
            "from athena.store import DataStore\nfrom athena.strategies.compile import strategy_rule\n"
            "from athena.strategies.parse import parse_strategy\nfrom athena.strategies.presets import PRESETS\n",
        ),
        (
            "    adjustments: tuple[Adjustment, ...] = ()  # splits and bonuses found in the bars, the same on every run\n",
            "    adjustments: tuple[Adjustment, ...] = ()  # splits and bonuses found in the bars, the same on every run\n"
            "    description: str = \"\"  # what the rule does, in words\n",
        ),
        (
            "def run_rules(\n    bars: list[Bar], rule_names: list[str], riskfree: Series, index: Series, config: Config = Config()\n) -> list[RuleRun]:\n"
            "    \"\"\"Each named rule on the real bars and again on blinded bars (ticker removed, dates shifted, prices rescaled),\n"
            "    both after adjusting the prices for the splits and bonus issues found in them.\"\"\"\n"
            "    unknown = [name for name in rule_names if name not in RULES]\n    if unknown:\n"
            "        raise ValueError(f\"unknown rule {unknown[0]!r}; choose from {sorted(RULES)}\")\n",
            "def run_rules(\n    bars: list[Bar], rules: Sequence[str | Rule | SeriesRule], riskfree: Series, index: Series, config: Config = Config()\n) -> list[RuleRun]:\n"
            "    \"\"\"Each rule (a built-in name, a packet rule or a strategy rule) on the real bars and again on blinded bars (ticker\n"
            "    removed, dates shifted, prices rescaled), both after adjusting the prices for the splits and bonus issues found in them.\"\"\"\n"
            "    unknown = [rule for rule in rules if isinstance(rule, str) and rule not in RULES]\n    if unknown:\n"
            "        raise ValueError(f\"unknown rule {unknown[0]!r}; choose from {sorted(RULES)}\")\n"
            "    resolved = [RULES[rule] if isinstance(rule, str) else rule for rule in rules]\n",
        ),
        (
            "    for name in rule_names:\n        real = run_backtest(adjusted, RULES[name], config)\n",
            "    for rule in resolved:\n        real = run_backtest(adjusted, rule, config)\n",
        ),
        (
            "                same_decisions(real, run_backtest(blinded, RULES[name], config)),\n                real,\n                adjustments,\n",
            "                same_decisions(real, run_backtest(blinded, rule, config)),\n                real,\n                adjustments,\n                rule.description,\n",
        ),
        (
            "def assumptions(config: Config = Config()) -> str:\n    stop = (\n        f\"a stop {config.stop_atr_multiple:g} x ATR below the entry fill\" if config.stop_atr_multiple is not None\n        else \"no protective stop\"\n    )\n",
            "def stop_phrase(config: Config) -> str:\n    return (\n        f\"a stop {config.stop_atr_multiple:g} x ATR below the entry fill\" if config.stop_atr_multiple is not None\n        else \"no protective stop\"\n    )\n\n\n"
            "def assumptions(config: Config = Config(), stop: str | None = None) -> str:\n    stop = stop or stop_phrase(config)\n",
        ),
        (
            "def format_runs(symbol: str, runs: list[RuleRun], config: Config = Config()) -> str:\n    blocks = [format_summary(run.summary, symbol, RULES[run.summary.rule].description) for run in runs]\n",
            "def assumptions_for(runs: Sequence[RuleRun], config: Config = Config()) -> str:\n"
            "    \"\"\"The assumptions of a set of runs. Strategies choose their own stop, so when the stops differ each one is named.\"\"\"\n"
            "    if not runs:\n        return assumptions(config)\n"
            "    phrases = {run.summary.rule: stop_phrase(run.result.config) for run in runs}\n"
            "    if len(set(phrases.values())) == 1:\n        return assumptions(runs[0].result.config)\n"
            "    each = \"; \".join(f\"{rule}: {phrase}\" for rule, phrase in phrases.items())\n"
            "    return assumptions(runs[0].result.config, f\"a stop that depends on the rule ({each})\")\n\n\n"
            "def format_runs(symbol: str, runs: list[RuleRun], config: Config = Config()) -> str:\n    blocks = [format_summary(run.summary, symbol, run.description) for run in runs]\n",
        ),
        ("        f\"\\n{assumptions(config)}\\n{DISCLAIMER}\"\n", "        f\"\\n{assumptions_for(runs, config)}\\n{DISCLAIMER}\"\n"),
        (
            "def analyze(world: BacktestWorld, query: str, rule_names: list[str]) -> tuple[str, int]:",
            "def analyze(world: BacktestWorld, query: str, rules: Sequence[str | Rule | SeriesRule]) -> tuple[str, int]:",
        ),
        ("    runs = run_rules(world.fetch_bars(resolved.identifier), rule_names, world.riskfree, world.index)\n", "    runs = run_rules(world.fetch_bars(resolved.identifier), rules, world.riskfree, world.index)\n"),
        (
            "def main(argv: list[str] | None = None, factory: Callable[..., BacktestWorld] = live_world) -> int:\n"
            "    parser = argparse.ArgumentParser(prog=\"python -m athena.backtest\", description=\"Backtest the technical rules on one instrument.\")\n"
            "    parser.add_argument(\"query\", nargs=\"+\", help=\"ticker, ISIN or name, for example SBIN\")\n"
            "    parser.add_argument(\"--rule\", choices=[*RULES, \"both\"], default=\"both\")\n"
            "    parser.add_argument(\"--years\", type=int, default=BACKTEST_YEARS)\n    args = parser.parse_args(argv)\n    try:\n"
            "        text, code = analyze(factory(years=args.years), \" \".join(args.query), list(RULES) if args.rule == \"both\" else [args.rule])\n",
            "def load_strategy_rule(path: str) -> SeriesRule:\n"
            "    \"\"\"A strategy file (JSON) as a rule, or a ValueError that says what is wrong with it.\"\"\"\n"
            "    try:\n        with open(path, encoding=\"utf-8\") as handle:\n            data = json.load(handle)\n"
            "    except OSError as exc:\n        raise ValueError(f\"cannot read {path}: {exc.strerror}\") from exc\n"
            "    except json.JSONDecodeError as exc:\n        raise ValueError(f\"{path} is not valid JSON: {exc}\") from exc\n"
            "    return strategy_rule(parse_strategy(data))\n\n\n"
            "def main(argv: list[str] | None = None, factory: Callable[..., BacktestWorld] = live_world) -> int:\n"
            "    parser = argparse.ArgumentParser(prog=\"python -m athena.backtest\", description=\"Backtest rules and strategies on one instrument.\")\n"
            "    parser.add_argument(\"query\", nargs=\"+\", help=\"ticker, ISIN or name, for example SBIN\")\n"
            "    parser.add_argument(\"--rule\", choices=[*RULES, \"both\"], default=None, help=\"the built-in rules (the default when no strategy is given)\")\n"
            "    parser.add_argument(\"--preset\", action=\"append\", default=[], choices=list(PRESETS), help=\"a ready-made strategy; may be repeated\")\n"
            "    parser.add_argument(\"--strategy\", action=\"append\", default=[], metavar=\"FILE\", help=\"a strategy in a JSON file; may be repeated\")\n"
            "    parser.add_argument(\"--years\", type=int, default=BACKTEST_YEARS)\n    args = parser.parse_args(argv)\n    try:\n"
            "        rules: list[str | SeriesRule] = []\n"
            "        if args.rule or not (args.preset or args.strategy):\n"
            "            rules += [args.rule] if args.rule in RULES else list(RULES)\n"
            "        rules += [strategy_rule(PRESETS[key]) for key in args.preset]\n"
            "        rules += [load_strategy_rule(path) for path in args.strategy]\n"
            "        text, code = analyze(factory(years=args.years), \" \".join(args.query), rules)\n",
        ),
    ],
)

# ---- the dashboard view shows each run's own description
edit(
    "src/athena/dashboard/backtest_view.py",
    [
        ("from athena.backtest.rules import RULES\n", ""),
        ("                s.rule, RULES[s.rule].description, rows,", "                s.rule, run.description, rows,"),
    ],
)

# ---- the service can run any rules, not only the built-in two
edit(
    "src/athena/dashboard/service.py",
    [
        ("from collections.abc import Callable\n", "from collections.abc import Callable, Sequence\n"),
        ("from athena.backtest.cli import assumptions, run_rules\nfrom athena.backtest.rules import RULES\n", "from athena.backtest.cli import assumptions_for, run_rules\nfrom athena.backtest.rules import RULES, Rule, SeriesRule\n"),
        (
            "    def backtest(self, identifier: str) -> BacktestView:\n        \"\"\"Both technical rules over the long history, each against buy and hold, with the blinding check.\"\"\"\n",
            "    def backtest(self, identifier: str, rules: Sequence[str | Rule | SeriesRule] | None = None) -> BacktestView:\n"
            "        \"\"\"The given rules (the two built-in technical rules by default) over the long history, each against buy and hold,\n"
            "        with the blinding check.\"\"\"\n",
        ),
        (
            "        runs = run_rules(self._long_history(identifier), list(RULES), self._risk_world.rate, self._risk_world.price_index)\n        return build_backtest_view(identifier, runs, assumptions())\n",
            "        runs = run_rules(\n            self._long_history(identifier), list(RULES) if rules is None else list(rules), self._risk_world.rate, self._risk_world.price_index\n        )\n"
            "        return build_backtest_view(identifier, runs, assumptions_for(runs))\n",
        ),
    ],
)
print("backtest, view and service edited")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest -q` (expect `690 passed, 63 skipped`) and `.venv/Scripts/python -m pyflakes src tests` (prints nothing).

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1hc.py compile engine cli` from the repository root.
Expected: 15 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: run strategies in the backtest engine, from the command line and the service" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: Choose, build or paste a strategy on the dashboard

**Files:**
- Create: `src/athena/dashboard/strategy_form.py`
- Modify (by the scripts below): `src/athena/dashboard/app.py`, `tests/dash_fakes.py`, `tests/test_dashboard_app.py`

**Interfaces:**
- Produces: `strategy_form.strategy_controls() -> (ready: bool, rules: list[SeriesRule] | None, token: str)` (`rules` is None for the built-in rules; `token` changes whenever the thing to run changes); session and widget keys `strategy_mode`, `preset_choice`, `builder_name`, `builder_stop`, `run_strategy`, `strategy_json`, `entry-count`, `entry-combine`, `exit-count`, `exit-combine`, and `<side>-<n>-<part>` keys for the form's boxes (for example `entry-0-left-kind`, `entry-0-op`, `entry-0-right-number`, `entry-0-left-rsi-length`); `strategy_to_run` holds the strategy last run, as canonical JSON.
- Consumes: Task 2's `DashboardService.backtest(identifier, rules)`, `strategy_rule`, `describe`, `PRESETS`, `parse_strategy`, `to_dict`, and the indicator registry.

- [ ] **Step 1: Update and add the tests**

Save as `$TEMP/s1hc_tests_page.py` and run it from the repository root. It makes the fake service take and remember the rules the page asks for, and adds the page tests (it prints one line when done).

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the fake service takes the rules the page asks for and remembers them
edit(
    "tests/dash_fakes.py",
    [
        (
            "        self.canned_backtest, self.backtest_error, self.backtests = backtest, backtest_error, []\n\n    def backtest(self, identifier):\n        self.backtests.append(identifier)\n",
            "        self.canned_backtest, self.backtest_error, self.backtests, self.rule_sets = backtest, backtest_error, [], []\n\n"
            "    def backtest(self, identifier, rules=None):\n        self.backtests.append(identifier)\n        self.rule_sets.append(rules)\n",
        ),
    ],
)

# ---- the page: choosing what to test
edit(
    "tests/test_dashboard_app.py",
    [],
    append='''

def start_backtest(service=None):
    service = service or FakeService(full_view())
    app = open_app(service, "sbin")
    backtest_box(app).check().run()
    return service, app


def choose(app, mode):
    app.radio(key="strategy_mode").set_value(mode).run()


def test_the_backtest_starts_on_the_built_in_rules():
    service, app = start_backtest()
    assert app.radio(key="strategy_mode").value == "Built-in rules"
    assert service.rule_sets == [None] and not app.exception


def test_choosing_a_preset_runs_it_straight_away_and_says_what_it_does():
    service, app = start_backtest()
    choose(app, "A preset strategy")
    assert [rule.name for rule in service.rule_sets[-1]] == ["52-week high breakout"]
    app.selectbox(key="preset_choice").select("golden_cross").run()
    assert [rule.name for rule in service.rule_sets[-1]] == ["Golden cross"] and not app.exception
    assert any("crosses above" in c and "simple moving average (length 50)" in c for c in texts(app.caption))


def test_my_own_strategy_waits_for_the_run_button_and_shows_its_description_first():
    service, app = start_backtest()
    choose(app, "Build my own")
    assert service.rule_sets == [None]  # nothing new has run
    assert any("Build or paste a strategy above" in c for c in texts(app.caption))
    assert any(c.startswith("Buy when the close is at or above the highest close over the last 252 bars") and "Stop out if the price falls 2 x ATR" in c for c in texts(app.caption))
    app.button(key="run_strategy").click().run()
    (rule,) = service.rule_sets[-1]
    assert rule.name == "My strategy" and rule.stop_atr == 2.0 and not app.exception
    assert len(service.rule_sets) == 2


def test_the_builder_turns_the_boxes_into_the_strategy_that_runs():
    service, app = start_backtest()
    choose(app, "Build my own")
    app.text_input(key="builder_name").set_value("Cheap and oversold").run()
    app.selectbox(key="entry-0-left-kind").select("Indicator").run()
    app.selectbox(key="entry-0-left-ind").select("rsi").run()
    app.number_input(key="entry-0-left-rsi-length").set_value(10).run()
    app.selectbox(key="entry-0-op").select("is below").run()
    app.selectbox(key="entry-0-right-kind").select("Number").run()
    app.number_input(key="entry-0-right-number").set_value(30).run()
    app.number_input(key="builder_stop").set_value(0).run()
    app.button(key="run_strategy").click().run()
    (rule,) = service.rule_sets[-1]
    assert rule.name == "Cheap and oversold" and rule.stop_atr is None
    assert rule.description.startswith("Buy when the RSI (length 10) is below 30. Sell when the close is at or below the lowest close")
    assert rule.description.endswith("No protective stop.")


def test_several_conditions_can_be_required_all_together_or_any_one_of_them():
    service, app = start_backtest()
    choose(app, "Build my own")
    app.number_input(key="entry-count").set_value(2).run()
    assert app.radio(key="entry-combine").value == "all of them"
    app.button(key="run_strategy").click().run()
    assert " and the close is above 0." in service.rule_sets[-1][0].description
    app.radio(key="entry-combine").set_value("any of them").run()
    app.button(key="run_strategy").click().run()
    assert " or the close is above 0." in service.rule_sets[-1][0].description


def test_pasted_json_runs_and_a_bad_one_is_explained_and_cannot_be_run():
    import json

    service, app = start_backtest()
    choose(app, "Paste JSON")
    assert not app.exception and app.button(key="run_strategy").disabled is False
    app.text_area(key="strategy_json").set_value("{not json").run()
    assert "This is not valid JSON" in app.error[0].value and app.button(key="run_strategy").disabled
    unknown = {"name": "x", "entry": {"op": "gt", "left": {"ind": "nope"}, "right": {"const": 1}}, "exit": {"op": "lt", "left": {"price": "close"}, "right": {"const": 1}}}
    app.text_area(key="strategy_json").set_value(json.dumps(unknown)).run()
    assert "entry.left.ind: unknown indicator 'nope'" in app.error[0].value and app.button(key="run_strategy").disabled
    assert len(service.rule_sets) == 1  # nothing was run
    good = {**unknown, "name": "From JSON", "entry": {"op": "gt", "left": {"price": "close"}, "right": {"const": 1}}}
    app.text_area(key="strategy_json").set_value(json.dumps(good)).run()
    app.button(key="run_strategy").click().run()
    assert service.rule_sets[-1][0].name == "From JSON" and not app.error


def test_a_strategy_that_was_run_is_remembered_and_not_run_again_on_other_clicks():
    service, app = start_backtest()
    choose(app, "Build my own")
    app.button(key="run_strategy").click().run()
    runs = len(service.rule_sets)
    app.checkbox(key="show_volume").uncheck().run()
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    assert len(service.rule_sets) == runs and service.queries == ["sbin"]


def test_switching_back_to_the_built_in_rules_shows_them_again():
    service, app = start_backtest()
    choose(app, "A preset strategy")
    choose(app, "Built-in rules")
    assert service.rule_sets[-1] is None and not app.exception
''',
)
print("page tests added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q`
Expected: failures (`radio ... strategy_mode` not found).

- [ ] **Step 3: Write the implementation**

Create `src/athena/dashboard/strategy_form.py`:

```python
from __future__ import annotations

import json
from collections.abc import Mapping

import streamlit as st

from athena.backtest.rules import SeriesRule
from athena.strategies.compile import strategy_rule
from athena.strategies.describe import describe
from athena.strategies.model import LINE_NAMES, MAX_SHIFT, MAX_WINDOW, PRICE_FIELDS
from athena.strategies.parse import StrategyError, parse_strategy, to_dict
from athena.strategies.presets import PRESET_DATA, PRESETS
from athena.technicals.indicators import REGISTRY

MODES = ("Built-in rules", "A preset strategy", "Build my own", "Paste JSON")
RUN_KEY = "strategy_to_run"  # the strategy last run, as canonical JSON
OPERATORS = {
    "is above": "gt", "is at or above": "ge", "is below": "lt", "is at or below": "le",
    "crosses above": "crosses_above", "crosses below": "crosses_below",
}
KINDS = ("Price", "Indicator", "Highest of", "Lowest of", "Average of", "Number")
ROLLING_KINDS = {"Highest of": "max", "Lowest of": "min", "Average of": "mean"}
MAX_BUILDER_CONDITIONS = 4
DEFAULT_STOP = 2.0  # the same protective stop the built-in rules use, so results are comparable
FALLBACK_CONDITION = {"op": "gt", "left": {"price": "close"}, "right": {"const": 0}}
ENTRY_DEFAULT = PRESET_DATA["breakout_52w"]["entry"]
EXIT_DEFAULT = PRESET_DATA["breakout_52w"]["exit"]


def _kind_of(expr: Mapping) -> str:
    if "ind" in expr:
        return "Indicator"
    if "rolling" in expr:
        return next(kind for kind, fn in ROLLING_KINDS.items() if fn == expr["rolling"])
    return "Number" if "const" in expr else "Price"


def _number(label: str, key: str, value: float, low: float, high: float, step: float, whole: bool):
    cast = int if whole else float
    return st.number_input(
        label, min_value=cast(low), max_value=cast(high), value=cast(value), step=cast(step), key=key, label_visibility="collapsed"
    )


def _indicator_operand(prefix: str, default: Mapping) -> dict:
    keys = list(REGISTRY)
    key = st.selectbox(
        "Indicator", keys, index=keys.index(default.get("ind", "sma")), format_func=lambda k: REGISTRY[k].label,
        key=f"{prefix}-ind", label_visibility="collapsed",
    )
    saved = default.get("params", {}) if default.get("ind") == key else {}
    params = {}
    for param in REGISTRY[key].params:
        params[param.name] = _number(
            param.label, f"{prefix}-{key}-{param.name}", saved.get(param.name, param.default), param.minimum, param.maximum,
            param.step, param.integer,
        )
    out: dict = {"ind": key, "params": params}
    names = LINE_NAMES[key]
    if len(names) > 1:
        default_line = default.get("line", names[0]) if default.get("ind") == key else names[0]
        out["line"] = st.selectbox("Line", names, index=names.index(default_line), key=f"{prefix}-{key}-line", label_visibility="collapsed")
    return out


def _operand(prefix: str, default: Mapping) -> dict:
    """One side of a comparison: a price, an indicator, the highest, lowest or average of a price over some bars, or a number."""
    kind = st.selectbox("Value", KINDS, index=KINDS.index(_kind_of(default)), key=f"{prefix}-kind", label_visibility="collapsed")
    if kind == "Number":
        return {"const": st.number_input("Number", value=float(default.get("const", 0.0)), key=f"{prefix}-number", label_visibility="collapsed")}
    if kind == "Price":
        field = default.get("price", "close")
        base = {"price": st.selectbox("Price", PRICE_FIELDS, index=PRICE_FIELDS.index(field), key=f"{prefix}-field", label_visibility="collapsed")}
    elif kind == "Indicator":
        base = _indicator_operand(prefix, default)
    else:
        inner = default.get("of", {}).get("price", "close") if "rolling" in default else "close"
        window = default.get("window", 20) if "rolling" in default else 20
        base = {
            "rolling": ROLLING_KINDS[kind],
            "of": {"price": st.selectbox("Of", PRICE_FIELDS, index=PRICE_FIELDS.index(inner), key=f"{prefix}-of", label_visibility="collapsed")},
            "window": _number("Bars", f"{prefix}-window", window, 2, MAX_WINDOW, 1, True),
        }
    back = _number("Bars back", f"{prefix}-back", default.get("shift", 0), 0, MAX_SHIFT, 1, True)
    return {**base, "shift": back} if back else base


def _condition(prefix: str, default: Mapping) -> dict:
    left_column, operator_column, right_column = st.columns([4, 2, 4])
    with left_column:
        left = _operand(f"{prefix}-left", default["left"])
    with operator_column:
        words = list(OPERATORS)
        word = st.selectbox("Comparison", words, index=list(OPERATORS.values()).index(default["op"]), key=f"{prefix}-op", label_visibility="collapsed")
    with right_column:
        right = _operand(f"{prefix}-right", default["right"])
    return {"op": OPERATORS[word], "left": left, "right": right}


def _side(prefix: str, title: str, default: Mapping) -> dict:
    st.markdown(f"**{title}**")
    count = int(st.number_input("How many conditions", min_value=1, max_value=MAX_BUILDER_CONDITIONS, value=1, key=f"{prefix}-count"))
    combine = "all of them"
    if count > 1:
        combine = st.radio("Needs", ("all of them", "any of them"), horizontal=True, key=f"{prefix}-combine")
    conditions = [_condition(f"{prefix}-{n}", default if n == 0 else FALLBACK_CONDITION) for n in range(count)]
    if count == 1:
        return conditions[0]
    return {"all" if combine == "all of them" else "any": conditions}


def _builder() -> dict:
    name = st.text_input("Name", value="My strategy", key="builder_name")
    entry = _side("entry", "Buy when", ENTRY_DEFAULT)
    exit_ = _side("exit", "Sell when", EXIT_DEFAULT)
    stop = st.number_input("Protective stop in ATR below the entry fill (0 for none)", 0.0, 10.0, DEFAULT_STOP, 0.5, key="builder_stop")
    data: dict = {"version": 1, "name": name, "entry": entry, "exit": exit_}
    if stop:
        data["stop_atr"] = float(stop)
    return data


def _json_box() -> dict | None:
    text = st.text_area("Strategy as JSON", value=json.dumps(to_dict(PRESETS["breakout_52w"]), indent=2), height=300, key="strategy_json")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        st.error(f"This is not valid JSON: {exc}")
        return None


def strategy_controls() -> tuple[bool, list[SeriesRule] | None, str]:
    """Ask what to test. Returns (ready, rules, token): `rules` is None for the built-in rules, and `token` names the
    choice so the page can remember its result. A strategy of the person's own waits for the Run button."""
    mode = st.radio("Rules to test", MODES, horizontal=True, key="strategy_mode")
    if mode == MODES[0]:
        return True, None, "builtin"
    if mode == MODES[1]:
        key = st.selectbox("Preset", list(PRESETS), format_func=lambda k: PRESETS[k].name, key="preset_choice")
        st.caption(describe(PRESETS[key]))
        return True, [strategy_rule(PRESETS[key])], f"preset:{key}"
    data = _builder() if mode == MODES[2] else _json_box()
    strategy = None
    if data is not None:
        try:
            strategy = parse_strategy(data)
        except StrategyError as exc:
            st.error(str(exc))
        else:
            st.caption(describe(strategy))
    if st.button("Run this strategy", key="run_strategy", disabled=strategy is None) and strategy is not None:
        st.session_state[RUN_KEY] = json.dumps(to_dict(strategy), sort_keys=True)
    ran = st.session_state.get(RUN_KEY)
    if not ran:
        return False, None, ""
    return True, [strategy_rule(parse_strategy(json.loads(ran)))], f"custom:{ran}"
```

Save the script below as `$TEMP/s1hc_source_page.py` and run it from the repository root. It edits `src/athena/dashboard/app.py` and prints `page edited for strategies`.

```python
import pathlib


def edit(path, pairs):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t, encoding="utf-8", newline="\n")


edit(
    "src/athena/dashboard/app.py",
    [
        ("from typing import Protocol\n", "from collections.abc import Sequence\nfrom typing import Protocol\n"),
        (
            "from athena.dashboard.backtest_view import BacktestView\n",
            "from athena.backtest.rules import Rule, SeriesRule\nfrom athena.dashboard.backtest_view import BacktestView\n",
        ),
        ("from athena.dashboard.view import DashboardView, Panel\n", "from athena.dashboard.strategy_form import strategy_controls\nfrom athena.dashboard.view import DashboardView, Panel\n"),
        (
            "    def backtest(self, identifier: str) -> BacktestView: ...\n",
            "    def backtest(self, identifier: str, rules: Sequence[str | Rule | SeriesRule] | None = None) -> BacktestView: ...\n",
        ),
        (
            "    try:\n        with st.spinner(\"Downloading history and replaying every trading day...\"):\n"
            "            result = _remembered(\"backtest\", identifier, lambda: service.backtest(identifier))\n",
            "    ready, rules, token = strategy_controls()\n    if not ready:\n"
            "        st.caption(\"Build or paste a strategy above, then press Run this strategy.\")\n        return\n"
            "    try:\n        with st.spinner(\"Downloading history and replaying every trading day...\"):\n"
            "            result = _remembered(\"backtest\", f\"{identifier}|{token}\", lambda: service.backtest(identifier, rules))\n",
        ),
    ],
)
print("page edited for strategies")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest -q` (expect `698 passed, 63 skipped`) and `.venv/Scripts/python -m pyflakes src tests` (prints nothing).

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1hc.py page` from the repository root.
Expected: 7 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: let the person pick, build or paste a strategy to backtest on the dashboard" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 4: Live check, TRD and PRD

**Files:**
- Create: `tests/live/test_live_strategies.py`
- Modify: `TRD.md`, `PRD.md` (by the scripts below)

- [ ] **Step 1: Add the live test**

Create `tests/live/test_live_strategies.py`:

```python
import pytest

from athena.backtest.cli import analyze, live_world
from athena.strategies.compile import strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS

pytestmark = pytest.mark.live

MINE = {
    "name": "Close above the 20-day average",
    "entry": {"op": "crosses_above", "left": {"price": "close"}, "right": {"ind": "sma", "params": {"length": 20}}},
    "exit": {"op": "crosses_below", "left": {"price": "close"}, "right": {"ind": "sma", "params": {"length": 20}}},
    "stop_atr": 2,
}


@pytest.fixture(scope="module")
def world():
    return live_world(years=8)


def test_live_every_preset_runs_on_a_real_stock_and_reads_only_the_price_pattern(world):
    rules = [strategy_rule(strategy) for strategy in PRESETS.values()]
    text, code = analyze(world, "SBIN", rules)
    print("\n" + text)
    assert code == 0
    for strategy in PRESETS.values():
        assert f"rule: {strategy.name}" in text
    assert "buy and hold" in text and "15 bps per side" in text
    assert "DIFFERENT" not in text  # the presets use ratios and indicators, never a price level


def test_live_a_person_written_strategy_runs_next_to_a_built_in_rule(world):
    text, code = analyze(world, "RELIANCE", ["trend", strategy_rule(parse_strategy(MINE))])
    print("\n" + text)
    assert code == 0 and "rule: trend" in text and "rule: Close above the 20-day average" in text
    assert "Buy when the close crosses above the simple moving average (length 20)." in text
    assert "Prices adjusted for 1 split or bonus event" in text  # the adjustment still applies to a strategy
```

- [ ] **Step 2: Run the live checks against real data**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/live/test_live_strategies.py tests/live/test_live_backtest.py --live -q -s`
Expected: `7 passed` (about 40 seconds; prices come from NSE through jugaad-data and need no API key). If NSE is unreachable the price fetch fails: that is an environment problem; report it rather than editing the tests. Read the printed reports: the four presets and the person-written strategy must each show a description sentence, a stop phrase and a blinding verdict.

- [ ] **Step 3: Record it in the TRD and PRD**

Save the two scripts below as `$TEMP/trd_1hc.py` and `$TEMP/prd_1hc.py` and run them from the repository root. They print `trd_1hc applied to TRD.md` and `prd_1hc applied to PRD.md`; an `AssertionError` means the text differs from what the script expects: stop and report.

`trd_1hc.py`:

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
    "so a run needs no language model and costs nothing.",
    "so a run needs no language model and costs nothing. *Strategy builder (Phase 1h-c):* a person's own rule is a strategy written as plain data (`athena.strategies`, JSON, version 1): a buy condition, a sell condition and an optional protective stop in ATR multiples, where a condition compares values (a price field, any registry indicator and line, a rolling high, low or average of a value, a number, or arithmetic on these, each optionally from some bars back) and conditions combine with all, any and not, within fixed limits on nesting, size, window and shift. Nothing in it can run code; it is validated with the path of any wrong part, described back in plain English, and compiled to entry and exit signals for every bar in one pass. A bar where a value is not yet computable is never a signal, and `not` of an unknown stays unknown (three-valued logic). Every value at a bar uses only bars up to that close, which tests pin by cutting and corrupting later bars. The engine treats a strategy as a rule with its own stop, so the same costs, next-open fills, adjustment, blinding check and comparisons apply. Four presets ship (52-week high breakout, golden cross, RSI mean reversion, Bollinger rebound); the command line takes `--preset NAME` and `--strategy FILE.json`, and the dashboard offers the built-in rules, a preset, a form builder (up to 4 conditions on each side) or pasted JSON, run only when the person presses Run. Text-to-strategy (a language model drafting the same JSON for the person to review) is not built yet.",
)
swap(
    "The two technical rules only; the language-model specialists and the fundamentals are not backtested",
    "The two technical rules and any strategy written in the strategy format; the language-model specialists and the fundamentals are not backtested",
)
swap(
    "- **Oct 6, 2026 (Phase 1h-b)**",
    "- **Oct 7, 2026 (Phase 1h-c)** — Strategy builder: a strategy is plain, validated data (`athena.strategies`: model, parser, plain-English description, compiler to per-bar signals, four presets) that the backtest engine runs as a rule with its own stop (`SeriesRule`), from the command line (`--preset`, `--strategy`) and from a dashboard picker (built-in rules, preset, form, pasted JSON). Decisions are checked not to depend on later bars. Text-to-strategy stays on hold (docs/superpowers/plans/2026-10-07-phase-1h-c-strategy-builder.md).\n- **Oct 6, 2026 (Phase 1h-b)**",
)
if crlf:
    t = t.replace("\n", "\r\n")
path.write_bytes(t.encode("utf-8"))
print("trd_1hc applied to", path)
```

`prd_1hc.py`:

```python
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "PRD.md")
raw = path.read_bytes().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")
old = "implemented for the deterministic technical rules only; see TRD section 2.10.*"
assert t.count(old) == 1, old
t = t.replace(old, "implemented for the deterministic technical rules and for strategies a person writes as data (Phase 1h-c); the language-model specialists are not backtested; see TRD section 2.10.*")
if crlf:
    t = t.replace("\n", "\r\n")
path.write_bytes(t.encode("utf-8"))
print("prd_1hc applied to", path)
```

- [ ] **Step 4: Final verification**

Run the whole suite `.venv/Scripts/python -m pytest -q` (expect `698 passed, 65 skipped`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing) and `git status --short` (only `TRD.md`, `PRD.md` and the new live test).

- [ ] **Step 5: Commit**

```bash
git add TRD.md PRD.md tests/live/test_live_strategies.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live strategy checks; record the strategy builder in the TRD and PRD" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```
