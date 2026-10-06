# Phase 1h-b — Selectable, Tunable Indicators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's hard-wired chart lines with a registry of indicators the person can add, tune and remove: more overlays on the price chart, more sub-charts (MACD, Stochastic, ADX and others) below it, with each setting validated.

**Architecture:** A registry (`athena.technicals.indicators`) describes every indicator once: its label, whether it draws on the price chart or in a sub-chart, its settings with defaults and ranges, how to compute its lines (ta-lib or small numpy functions), and any reference lines. A *selection* is a list of plain `Selected(id, key, params)` records, so it can be stored in the session now and sent as JSON later. `build_chart(candles, selection, title, show_volume)` turns a selection into one multi-pane figure with a shared date axis. `DashboardView` stops carrying two Plotly figures and carries the plain `candles` and a `chart_title`; the page keeps the selection in the session, offers an Indicators section (settings, remove, add, reset, volume switch) and builds the figure from the selection. The specialists are untouched: chart indicators are for looking only, and the page says so.

**Tech Stack:** Python >= 3.11, pytest, ta-lib (already a dependency), numpy, Plotly and Streamlit 1.65. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-phase-1h-research-tools-design.md` part 1h-b; `PRD.md` FR-6; `TRD.md` §2.9.

**Plan series:** 0a-0e, 1a-1h-a (done) -> **1h-b (this plan)** -> 1h-c (strategy format, engine support, rule builder; reuses this registry) -> 1h-d (text to strategy) -> risk overlay and investor profile -> later phases.

**Suggested models:** Sonnet at medium effort for the implementers (every step carries complete code or a verified script), Sonnet for reviewers, Opus for the final review. Prototyped in a scratch copy first: 597 offline tests passed (567 before), the live checks passed on real SBIN data, 29 deliberate mutations were each caught (the first pass found three that were not; the tests were strengthened), and the scripts below were re-run on a clean copy of the repository to prove they apply and pass.

## Verified findings (6 Oct 2026)

- The default selection reproduces what the page always drew: SMA 50, SMA 200, Bollinger Bands 20/2 (grey, dotted), volume and RSI 14. Its last values equal the technical packet's (`sma_50`, `sma_200`, `rsi_14`, Bollinger %B) to 3 decimals, and a test pins that.
- `AppTest` can read a Plotly figure on the page: `json.loads(chart.proto.spec)["data"]` gives the traces, and `app.number_input(key=...)`, `app.button(key=...)`, `app.selectbox(key=...)` drive the controls.
- A Streamlit expander whose label changes is re-created collapsed, so the Indicators section has a fixed label; adding an indicator no longer closes it.
- After removing the old fixed charts, the live dashboard tests needed two fixes unrelated to this feature: `test_live_stock_view_has_verdict_charts_and_complete_panels` still unpacked exactly two panels (Plan 1f added three fundamentals panels), and the end-to-end page test counted two charts. Both are corrected in Task 2.
- Every indicator was computed on 760 days of real SBIN prices and volume (`tests/live/test_live_indicators.py`), each with real values and no non-finite numbers.

**Honest limits:**
- The chart reads the same 760-day history as the specialists (about 520 bars), so an indicator whose setting needs more bars than that (an SMA of 400, for example) draws nothing and the page says "Not enough history to draw: SMA 400."
- Indicators are computed from the first bar, so exponential ones (EMA, MACD, RSI) differ from the technical packet's windowed value by a hair in the early part of the chart; the last values agree to 3 decimals.
- At most 8 indicators are shown, to keep the chart readable.
- The selection lives in the session: it survives looking up another instrument but not a page reload.
- Pattern-recognition flags (candlestick patterns) are not part of this plan.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and need no API key.
- Chart indicators never change what a specialist sees or says.
- A selection and its items are plain data (`to_dict` / `from_dict`); no global state; nothing reads keys from the environment.
- Files in the repository use LF; no new dependency; match the surrounding code's comment density.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...` and end each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `git push` after each task.
- Windows, Git Bash: run Python as `.venv/Scripts/python`. Helper scripts live outside the repository (save them under `$TEMP` with the Write tool, never shell heredocs) and are run from the repository root.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/technicals/indicators.py` | new: `Param`, `Line`, `IndicatorSpec`, `Selected`, `REGISTRY`, `default_selection`, `validate`, `compute`, `short_history`, `next_id` |
| `src/athena/dashboard/charts.py` | modified: `build_chart` replaces `price_chart` and `rsi_chart`; `equity_chart` unchanged |
| `src/athena/dashboard/view.py` | modified: `candles` and `chart_title` replace `price_figure` and `rsi_figure` |
| `src/athena/dashboard/app.py` | modified: the Indicators section and the chart built from the selection |
| `src/athena/technicals/series.py`, `tests/test_technicals_series.py` | removed (superseded by the registry) |
| `tests/test_technicals_indicators.py` | new |
| `tests/test_dashboard_charts.py`, `test_dashboard_view.py`, `test_dashboard_service.py`, `test_dashboard_app.py`, `tests/live/test_live_dashboard.py` | modified |
| `tests/live/test_live_indicators.py` | new, opt-in |
| `TRD.md` | modified: §2.9 and the revision history |

---

### Task 1: The indicator registry

**Files:**
- Create: `src/athena/technicals/indicators.py`, `tests/test_technicals_indicators.py` (the test file is written by the script below)

**Interfaces:**
- Produces: `OVERLAY`, `PANE`, `MAX_INDICATORS = 8`; `Param(name, label, default, minimum, maximum, step=1.0, integer=True)`; `Line(name, values, style="solid")` (`values` aligned with the candles, `None` while warming up; style one of `solid`, `dot`, `dots`, `bars`); `IndicatorSpec(key, label, placement, params, compute, levels=(), y_range=None, check=...)`; `Selected(id, key, params)` with `to_dict()` / `from_dict()`; `REGISTRY: dict[str, IndicatorSpec]` (16 entries); `default_params(key)`, `next_id(selection, key)`, `default_selection()`, `problem(item) -> str | None`, `validate(selection) -> (valid, {id: reason})`, `arrays(candles)`, `compute(candles, item) -> list[Line]` (raises `ValueError` with the problem text), `short_history(candles, selection) -> list[str]`.
- Consumes: `athena.technicals.candles.Candle`; ta-lib.

- [ ] **Step 1: Write the failing tests**

Save as `$TEMP/s1hb_tests_registry.py` and run `.venv/Scripts/python $TEMP/s1hb_tests_registry.py` from the repository root. It writes `tests/test_technicals_indicators.py`.

```python
import pathlib


# ---- new: tests/test_technicals_indicators.py
pathlib.Path("tests/test_technicals_indicators.py").write_text(
    '''import math
import re
from dataclasses import replace

import numpy as np
import pytest
import talib
from bar_factory import make_bars

from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import (
    MAX_INDICATORS,
    OVERLAY,
    PANE,
    REGISTRY,
    Selected,
    compute,
    default_params,
    default_selection,
    next_id,
    problem,
    short_history,
    validate,
)
from athena.technicals.packet import build_technical_packet

CLOSES = [100.0 + i * 0.5 + (3 if i % 7 == 0 else 0) - (2 if i % 11 == 0 else 0) for i in range(400)]
BARS = [
    replace(bar, open=bar.close - ((i % 3) - 1) * 0.4, volume=1000.0 + (i % 5) * 100)
    for i, bar in enumerate(make_bars(CLOSES, symbol="SBIN"))
]  # an open that differs from the close, and a volume that moves
CANDLES = candles_from_bars(BARS)
CLOSE = np.array([c.close for c in CANDLES])
HIGH = np.array([c.high for c in CANDLES])
LOW = np.array([c.low for c in CANDLES])


def chosen(key, **params):
    return Selected(f"{key}-1", key, {**default_params(key), **params})


def values(key, line=0, **params):
    return compute(CANDLES, chosen(key, **params))[line].values


def test_every_registry_entry_is_well_formed_and_its_defaults_are_valid():
    assert len(REGISTRY) >= 16
    for key, spec in REGISTRY.items():
        assert spec.key == key and spec.label and spec.placement in (OVERLAY, PANE)
        for param in spec.params:
            assert param.minimum <= param.default <= param.maximum, (key, param.name)
        assert problem(chosen(key)) is None, key


def test_every_indicator_gives_lines_aligned_with_the_candles_with_a_warm_up_of_none():
    for key in REGISTRY:
        lines = compute(CANDLES, chosen(key))
        assert lines and all(len(line.values) == len(CANDLES) for line in lines), key
        for line in lines:
            assert all(v is None or (isinstance(v, float) and math.isfinite(v)) for v in line.values), (key, line.name)
            assert any(v is not None for v in line.values), (key, line.name)
        if key not in ("obv", "sar"):
            assert lines[0].values[0] is None, key  # a moving window has no value on the first bar


def test_moving_averages_match_ta_lib_and_use_the_chosen_length():
    assert values("sma", length=30)[-1] == pytest.approx(talib.SMA(CLOSE, 30)[-1])
    assert values("ema", length=21)[-1] == pytest.approx(talib.EMA(CLOSE, 21)[-1])
    assert values("wma", length=10)[-1] == pytest.approx(talib.WMA(CLOSE, 10)[-1])
    assert compute(CANDLES, chosen("sma", length=30))[0].name == "SMA 30"
    assert values("sma", length=30)[28] is None and values("sma", length=30)[29] is not None


def test_bollinger_bands_use_both_settings_and_name_them():
    upper, lower = compute(CANDLES, chosen("bbands", length=15, width=2.5))
    ta_upper, _, ta_lower = talib.BBANDS(CLOSE, 15, 2.5, 2.5)
    assert upper.values[-1] == pytest.approx(ta_upper[-1]) and lower.values[-1] == pytest.approx(ta_lower[-1])
    assert upper.name == "Bollinger upper (15, 2.5)" and upper.style == "dot"


def test_the_high_low_channel_is_the_highest_high_and_lowest_low_of_the_last_n_bars_including_today():
    high, low = compute(CANDLES, chosen("channel", length=252))
    assert high.values[250] is None and high.values[251] == max(HIGH[:252])
    assert high.values[-1] == max(HIGH[-252:]) and low.values[-1] == min(LOW[-252:])
    assert high.name == "High 252" and low.name == "Low 252"


def test_momentum_indicators_match_ta_lib_and_carry_their_reference_levels():
    assert values("rsi", length=10)[-1] == pytest.approx(talib.RSI(CLOSE, 10)[-1])
    macd, signal, histogram = compute(CANDLES, chosen("macd", fast=8, slow=21, signal=5))
    ta_macd, ta_signal, ta_hist = talib.MACD(CLOSE, 8, 21, 5)
    assert macd.values[-1] == pytest.approx(ta_macd[-1]) and signal.values[-1] == pytest.approx(ta_signal[-1])
    assert histogram.values[-1] == pytest.approx(ta_hist[-1]) and histogram.style == "bars"
    slow_k, slow_d = compute(CANDLES, chosen("stoch", k=10, smooth=3, d=3))
    ta_k, ta_d = talib.STOCH(HIGH, LOW, CLOSE, fastk_period=10, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    assert slow_k.values[-1] == pytest.approx(ta_k[-1]) and slow_d.values[-1] == pytest.approx(ta_d[-1])
    assert REGISTRY["rsi"].levels == (30.0, 70.0) and REGISTRY["rsi"].y_range == (0.0, 100.0)


def test_the_default_selection_is_what_the_dashboard_always_drew_and_agrees_with_the_technical_packet():
    selection = default_selection()
    assert [(item.key, dict(item.params)) for item in selection] == [
        ("sma", {"length": 50}), ("sma", {"length": 200}), ("bbands", {"length": 20, "width": 2.0}), ("rsi", {"length": 14}),
    ]
    assert [item.id for item in selection] == ["sma-1", "sma-2", "bbands-1", "rsi-1"]
    metrics = build_technical_packet("SBIN", BARS[-1].as_of, BARS)["metrics"]
    sma_50, sma_200, bands, rsi = (compute(CANDLES, item) for item in selection)
    assert sma_50[0].values[-1] == pytest.approx(metrics["sma_50"]["value"], abs=1e-3)
    assert sma_200[0].values[-1] == pytest.approx(metrics["sma_200"]["value"], abs=1e-3)
    assert rsi[0].values[-1] == pytest.approx(metrics["rsi_14"]["value"], abs=1e-3)
    pct_b = (CLOSE[-1] - bands[1].values[-1]) / (bands[0].values[-1] - bands[1].values[-1])
    assert pct_b == pytest.approx(metrics["bollinger_pct_b"]["value"], abs=1e-3)


@pytest.mark.parametrize(
    "item, expected",
    [
        (Selected("x-1", "nope", {}), "unknown indicator 'nope'"),
        (Selected("x-1", "sma", {"size": 5}), "has no setting called 'size'"),
        (Selected("x-1", "sma", {"length": 1}), "Length must be between 2 and 400"),
        (Selected("x-1", "sma", {"length": 401}), "Length must be between 2 and 400"),
        (Selected("x-1", "sma", {"length": 50.5}), "Length must be a whole number"),
        (Selected("x-1", "sma", {"length": float("nan")}), "Length must be a number"),
        (Selected("x-1", "sma", {"length": True}), "Length must be a number"),
        (Selected("x-1", "sma", {"length": "50"}), "Length must be a number"),
        (Selected("x-1", "bbands", {"width": 9.0}), "Width (std dev) must be between 0.5 and 4"),
        (Selected("x-1", "macd", {"fast": 30, "slow": 20}), "the fast length must be shorter than the slow length"),
        (Selected("x-1", "macd", {"fast": 26, "slow": 26}), "the fast length must be shorter than the slow length"),
    ],
)
def test_a_choice_that_cannot_be_drawn_says_why(item, expected):
    assert expected in problem(item)
    with pytest.raises(ValueError, match=re.escape(expected)):
        compute(CANDLES, item)


def test_a_setting_left_out_falls_back_to_its_default_and_boundaries_are_allowed():
    assert problem(Selected("x-1", "sma", {})) is None
    assert problem(Selected("x-1", "sma", {"length": 2})) is None and problem(Selected("x-1", "sma", {"length": 400})) is None
    assert values("sma", length=50)[-1] == compute(CANDLES, Selected("x-1", "sma", {}))[0].values[-1]


def test_validate_keeps_the_drawable_choices_and_gives_a_reason_for_each_other_one():
    selection = [chosen("sma"), Selected("sma-2", "sma", {"length": 1}), Selected("zzz-1", "zzz", {}), chosen("rsi")]
    valid, problems = validate(selection)
    assert [item.id for item in valid] == ["sma-1", "rsi-1"]
    assert set(problems) == {"sma-2", "zzz-1"} and "between 2 and 400" in problems["sma-2"]


def test_validate_limits_the_number_of_indicators_and_repeated_ids():
    many = [Selected(f"sma-{n}", "sma", {"length": 5 + n}) for n in range(MAX_INDICATORS + 2)]
    valid, problems = validate(many)
    assert len(valid) == MAX_INDICATORS == 8 and set(problems) == {"sma-8", "sma-9"}
    assert "at most 8 indicators" in problems["sma-8"]
    valid, problems = validate([chosen("sma"), chosen("sma")])
    assert len(valid) == 1 and "already used" in problems["sma-1"]


def test_ids_are_the_smallest_unused_and_a_selection_round_trips_as_plain_data():
    selection = default_selection()
    assert next_id(selection, "sma") == "sma-3" and next_id(selection, "macd") == "macd-1"
    assert next_id([item for item in selection if item.id != "sma-1"], "sma") == "sma-1"
    item = chosen("macd", fast=10)
    assert Selected.from_dict(item.to_dict()) == item


def test_a_history_too_short_for_the_settings_is_named_not_hidden():
    short = candles_from_bars(make_bars(CLOSES[:150]))
    assert short_history(short, [chosen("sma", length=50), chosen("sma", length=200)]) == ["SMA 200"]
    assert short_history(short, [chosen("sma", length=50)]) == []
''',
    encoding="utf-8",
    newline="\n",
)
print("indicator registry tests added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_indicators.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'athena.technicals.indicators'`.

- [ ] **Step 3: Write the implementation**

Create `src/athena/technicals/indicators.py`:

```python
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import talib

from athena.technicals.candles import Candle

OVERLAY, PANE = "overlay", "pane"  # drawn on the price chart, or in a sub-chart of its own
MAX_INDICATORS = 8


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float = 1.0
    integer: bool = True


@dataclass(frozen=True)
class Line:
    """One drawn series, aligned one-to-one with the candles; `None` where the indicator is still warming up."""

    name: str
    values: tuple[float | None, ...]
    style: str = "solid"  # solid | dot | dots (markers only) | bars


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    label: str
    placement: str
    params: tuple[Param, ...]
    compute: Callable[[Mapping[str, np.ndarray], Mapping[str, float]], list[Line]]
    levels: tuple[float, ...] = ()  # horizontal reference lines in a pane
    y_range: tuple[float, float] | None = None
    check: Callable[[Mapping[str, float]], str | None] = lambda params: None  # a rule across parameters


@dataclass(frozen=True)
class Selected:
    """One indicator the person chose: plain data, so a selection can be stored or sent as JSON."""

    id: str
    key: str
    params: Mapping[str, float]

    def to_dict(self) -> dict:
        return {"id": self.id, "key": self.key, "params": dict(self.params)}

    @staticmethod
    def from_dict(data: Mapping) -> Selected:
        return Selected(str(data["id"]), str(data["key"]), dict(data["params"]))


def _clean(values: np.ndarray) -> tuple[float | None, ...]:
    return tuple(None if math.isnan(value) else float(value) for value in values)


def _int(p: Mapping[str, float], name: str) -> int:
    return int(p[name])


def _rolling(values: np.ndarray, length: int, pick: Callable[[np.ndarray], float]) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(length - 1, len(values)):
        out[i] = pick(values[i + 1 - length : i + 1])
    return out


def _sma(a, p):
    return [Line(f"SMA {_int(p, 'length')}", _clean(talib.SMA(a["close"], _int(p, "length"))))]


def _ema(a, p):
    return [Line(f"EMA {_int(p, 'length')}", _clean(talib.EMA(a["close"], _int(p, "length"))))]


def _wma(a, p):
    return [Line(f"WMA {_int(p, 'length')}", _clean(talib.WMA(a["close"], _int(p, "length"))))]


def _bbands(a, p):
    length, width = _int(p, "length"), float(p["width"])
    upper, _, lower = talib.BBANDS(a["close"], length, width, width)
    tag = f"({length}, {width:g})"
    return [Line(f"Bollinger upper {tag}", _clean(upper), "dot"), Line(f"Bollinger lower {tag}", _clean(lower), "dot")]


def _sar(a, p):
    return [Line("Parabolic SAR", _clean(talib.SAR(a["high"], a["low"], float(p["acceleration"]), float(p["maximum"]))), "dots")]


def _channel(a, p):
    length = _int(p, "length")
    return [
        Line(f"High {length}", _clean(_rolling(a["high"], length, np.max)), "dot"),
        Line(f"Low {length}", _clean(_rolling(a["low"], length, np.min)), "dot"),
    ]


def _rsi(a, p):
    return [Line(f"RSI {_int(p, 'length')}", _clean(talib.RSI(a["close"], _int(p, "length"))))]


def _macd(a, p):
    macd, signal, histogram = talib.MACD(a["close"], _int(p, "fast"), _int(p, "slow"), _int(p, "signal"))
    return [Line("MACD", _clean(macd)), Line("Signal", _clean(signal)), Line("Histogram", _clean(histogram), "bars")]


def _stoch(a, p):
    slow_k, slow_d = talib.STOCH(
        a["high"], a["low"], a["close"], fastk_period=_int(p, "k"), slowk_period=_int(p, "smooth"), slowk_matype=0,
        slowd_period=_int(p, "d"), slowd_matype=0,
    )
    return [Line("%K", _clean(slow_k)), Line("%D", _clean(slow_d))]


def _adx(a, p):
    length = _int(p, "length")
    return [
        Line(f"ADX {length}", _clean(talib.ADX(a["high"], a["low"], a["close"], length))),
        Line("+DI", _clean(talib.PLUS_DI(a["high"], a["low"], a["close"], length)), "dot"),
        Line("-DI", _clean(talib.MINUS_DI(a["high"], a["low"], a["close"], length)), "dot"),
    ]


def _atr(a, p):
    return [Line(f"ATR {_int(p, 'length')}", _clean(talib.ATR(a["high"], a["low"], a["close"], _int(p, "length"))))]


def _obv(a, p):
    return [Line("OBV", _clean(talib.OBV(a["close"], a["volume"])))]


def _cci(a, p):
    return [Line(f"CCI {_int(p, 'length')}", _clean(talib.CCI(a["high"], a["low"], a["close"], _int(p, "length"))))]


def _mfi(a, p):
    return [Line(f"MFI {_int(p, 'length')}", _clean(talib.MFI(a["high"], a["low"], a["close"], a["volume"], _int(p, "length"))))]


def _willr(a, p):
    return [Line(f"Williams %R {_int(p, 'length')}", _clean(talib.WILLR(a["high"], a["low"], a["close"], _int(p, "length"))))]


def _roc(a, p):
    return [Line(f"ROC {_int(p, 'length')}", _clean(talib.ROC(a["close"], _int(p, "length"))))]


def _length(default: int, minimum: int = 2, maximum: int = 400) -> Param:
    return Param("length", "Length", default, minimum, maximum)


def _macd_check(p: Mapping[str, float]) -> str | None:
    return "the fast length must be shorter than the slow length" if p["fast"] >= p["slow"] else None


REGISTRY: dict[str, IndicatorSpec] = {
    spec.key: spec
    for spec in (
        IndicatorSpec("sma", "Simple moving average", OVERLAY, (_length(50),), _sma),
        IndicatorSpec("ema", "Exponential moving average", OVERLAY, (_length(20),), _ema),
        IndicatorSpec("wma", "Weighted moving average", OVERLAY, (_length(20),), _wma),
        IndicatorSpec(
            "bbands", "Bollinger Bands", OVERLAY,
            (Param("length", "Length", 20, 5, 200), Param("width", "Width (std dev)", 2.0, 0.5, 4.0, 0.5, integer=False)), _bbands,
        ),
        IndicatorSpec(
            "sar", "Parabolic SAR", OVERLAY,
            (Param("acceleration", "Acceleration", 0.02, 0.01, 0.2, 0.01, integer=False), Param("maximum", "Maximum", 0.2, 0.1, 0.5, 0.05, integer=False)),
            _sar,
        ),
        IndicatorSpec("channel", "High-low channel (252 = 52 weeks)", OVERLAY, (_length(252, 5, 400),), _channel),
        IndicatorSpec("rsi", "RSI", PANE, (_length(14, 2, 100),), _rsi, levels=(30.0, 70.0), y_range=(0.0, 100.0)),
        IndicatorSpec(
            "macd", "MACD", PANE,
            (Param("fast", "Fast", 12, 2, 100), Param("slow", "Slow", 26, 3, 200), Param("signal", "Signal", 9, 2, 100)),
            _macd, levels=(0.0,), check=_macd_check,
        ),
        IndicatorSpec(
            "stoch", "Stochastic", PANE,
            (Param("k", "%K length", 14, 2, 100), Param("smooth", "%K smoothing", 3, 1, 20), Param("d", "%D length", 3, 1, 20)),
            _stoch, levels=(20.0, 80.0), y_range=(0.0, 100.0),
        ),
        IndicatorSpec("adx", "ADX with directional lines", PANE, (_length(14, 2, 100),), _adx, levels=(25.0,)),
        IndicatorSpec("atr", "ATR", PANE, (_length(14, 2, 100),), _atr),
        IndicatorSpec("obv", "On-balance volume", PANE, (), _obv),
        IndicatorSpec("cci", "CCI", PANE, (_length(20, 5, 100),), _cci, levels=(-100.0, 100.0)),
        IndicatorSpec("mfi", "Money flow index", PANE, (_length(14, 2, 100),), _mfi, levels=(20.0, 80.0), y_range=(0.0, 100.0)),
        IndicatorSpec("willr", "Williams %R", PANE, (_length(14, 2, 100),), _willr, levels=(-80.0, -20.0), y_range=(-100.0, 0.0)),
        IndicatorSpec("roc", "Rate of change (%)", PANE, (_length(10, 1, 100),), _roc, levels=(0.0,)),
    )
}


def default_params(key: str) -> dict[str, float]:
    return {param.name: param.default for param in REGISTRY[key].params}


def next_id(selection: Sequence[Selected], key: str) -> str:
    taken = {item.id for item in selection}
    n = 1
    while f"{key}-{n}" in taken:
        n += 1
    return f"{key}-{n}"


def default_selection() -> list[Selected]:
    """What the dashboard has always drawn: SMA 50 and 200, Bollinger Bands (20, 2) and RSI 14."""
    selection: list[Selected] = []
    for key, params in (("sma", {"length": 50}), ("sma", {"length": 200}), ("bbands", {}), ("rsi", {})):
        selection.append(Selected(next_id(selection, key), key, {**default_params(key), **params}))
    return selection


def problem(item: Selected) -> str | None:
    """Why this choice cannot be drawn, or None when it can."""
    spec = REGISTRY.get(item.key)
    if spec is None:
        return f"unknown indicator {item.key!r}"
    unexpected = set(item.params) - {param.name for param in spec.params}
    if unexpected:
        return f"{spec.label} has no setting called {sorted(unexpected)[0]!r}"
    for param in spec.params:
        value = item.params.get(param.name, param.default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return f"{param.label} must be a number"
        if param.integer and value != int(value):
            return f"{param.label} must be a whole number"
        if not param.minimum <= value <= param.maximum:
            return f"{param.label} must be between {param.minimum:g} and {param.maximum:g}"
    return spec.check({param.name: item.params.get(param.name, param.default) for param in spec.params})


def validate(selection: Sequence[Selected]) -> tuple[list[Selected], dict[str, str]]:
    """The choices that can be drawn, and a reason for each that cannot (keyed by id). More than `MAX_INDICATORS`
    or a repeated id counts against the later ones."""
    valid: list[Selected] = []
    problems: dict[str, str] = {}
    seen: set[str] = set()
    for item in selection:
        reason = problem(item)
        if reason is None and item.id in seen:
            reason = "this id is already used"
        if reason is None and len(valid) >= MAX_INDICATORS:
            reason = f"at most {MAX_INDICATORS} indicators can be shown"
        seen.add(item.id)
        if reason:
            problems[item.id] = reason
        else:
            valid.append(item)
    return valid, problems


def arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray]:
    return {
        "open": np.array([c.open for c in candles], dtype=float),
        "high": np.array([c.high for c in candles], dtype=float),
        "low": np.array([c.low for c in candles], dtype=float),
        "close": np.array([c.close for c in candles], dtype=float),
        "volume": np.array([c.volume for c in candles], dtype=float),
    }


def compute(candles: Sequence[Candle], item: Selected) -> list[Line]:
    """The lines for one valid choice. Raises ValueError for one that `problem` rejects."""
    reason = problem(item)
    if reason:
        raise ValueError(reason)
    spec = REGISTRY[item.key]
    params = {param.name: item.params.get(param.name, param.default) for param in spec.params}
    return spec.compute(arrays(candles), params)


def short_history(candles: Sequence[Candle], selection: Sequence[Selected]) -> list[str]:
    """Names of the lines that have no value at all because the history is too short for their settings."""
    empty = []
    for item in selection:
        for line in compute(candles, item):
            if all(value is None for value in line.values):
                empty.append(line.name)
    return empty
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_indicators.py -q` (expect `23 passed`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing), then the whole suite `.venv/Scripts/python -m pytest -q` (expect `590 passed, 61 skipped`).

- [ ] **Step 5: Mutation check**

Save the helper below as `$TEMP/mutate_1hb.py` (used again in Task 2). Run `.venv/Scripts/python $TEMP/mutate_1hb.py indicators` from the repository root. Every line must start with `CAUGHT`; `SURVIVED`, `ERROR` or `NOT FOUND` means a test or the transcribed code is wrong. Files are restored automatically.

```python
import pathlib
import subprocess
import sys

PY = sys.executable
IND, CHART, APP, VIEW = (
    "src/athena/technicals/indicators.py",
    "src/athena/dashboard/charts.py",
    "src/athena/dashboard/app.py",
    "src/athena/dashboard/view.py",
)
TESTS = ["tests/test_technicals_indicators.py", "tests/test_dashboard_charts.py", "tests/test_dashboard_view.py",
         "tests/test_dashboard_service.py", "tests/test_dashboard_app.py"]

MUTATIONS = [
    ("indicators: SMA ignores its length", IND, 'talib.SMA(a["close"], _int(p, "length"))', 'talib.SMA(a["close"], 50)'),
    ("indicators: the channel reads the wrong series", IND, '_rolling(a["high"], length, np.max)', '_rolling(a["low"], length, np.max)'),
    ("indicators: the rolling window is off by one", IND, "values[i + 1 - length : i + 1]", "values[i - length : i]"),
    ("indicators: Bollinger ignores its width", IND, 'talib.BBANDS(a["close"], length, width, width)', 'talib.BBANDS(a["close"], length, 2.0, 2.0)'),
    ("indicators: the lower bound is excluded", IND, "if not param.minimum <= value <= param.maximum:", "if not param.minimum < value <= param.maximum:"),
    ("indicators: the upper bound is excluded", IND, "if not param.minimum <= value <= param.maximum:", "if not param.minimum <= value < param.maximum:"),
    ("indicators: fractions pass for whole-number settings", IND, "if param.integer and value != int(value):", "if False and value != int(value):"),
    ("indicators: True counts as a number", IND, "or isinstance(value, bool) or not math.isfinite(value)", "or not math.isfinite(value)"),
    ("indicators: MACD allows fast equal to slow", IND, 'if p["fast"] >= p["slow"]', 'if p["fast"] > p["slow"]'),
    ("indicators: one indicator too many is allowed", IND, "len(valid) >= MAX_INDICATORS", "len(valid) > MAX_INDICATORS"),
    ("indicators: ids start at 2", IND, "    n = 1\n    while", "    n = 2\n    while"),
    ("indicators: short history reports partly empty lines", IND, "if all(value is None for value in line.values):", "if any(value is None for value in line.values):"),
    ("indicators: a repeated id is accepted", IND, 'if reason is None and item.id in seen:', 'if False and item.id in seen:'),
    ("charts: the volume switch is ignored", CHART, "([VOLUME_WEIGHT] if show_volume else [])", "[VOLUME_WEIGHT]"),
    ("charts: reference lines are not drawn", CHART, "for level in spec.levels:", "for level in ():"),
    ("charts: pane ranges are not applied", CHART, "if spec.y_range:", "if False:"),
    ("charts: overlays all get the same colour", CHART, "overlay_index += 0 if item.key == \"bbands\" else 1", "overlay_index += 0"),
    ("charts: Bollinger is not grey", CHART, 'GREY if item.key == "bbands"', 'OVERLAY_COLORS[0] if item.key == "bbands"'),
    ("charts: SAR is drawn as a line", CHART, '"markers" if line.style == "dots" else "lines"', '"lines"'),
    ("view: the candles are dropped", VIEW, "tuple(candles), chart_title, (), tuple(notes)", "(), chart_title, (), tuple(notes)"),
    ("app: reset leaves old settings in the boxes", APP, '    st.session_state[INDICATORS_KEY] = [item.to_dict() for item in default_selection()]\n    _forget_widgets("ind-")', '    st.session_state[INDICATORS_KEY] = [item.to_dict() for item in default_selection()]'),
    ("app: remove removes nothing", APP, 'if d["id"] != item_id]', "if True]"),
    ("app: the add button is never switched off", APP, "disabled=len(chosen) >= MAX_INDICATORS)", "disabled=False)"),
    ("app: a problem is not reported", APP, '        st.warning(f"{item_id}: {reason}")', "        pass"),
    ("app: the looking-only note is dropped", APP, "    st.caption(CHART_NOTE)\n", "    pass\n"),
    ("app: volume starts switched off", APP, 'volume.checkbox("Volume", value=True, key=VOLUME_KEY)', 'volume.checkbox("Volume", value=False, key=VOLUME_KEY)'),
    ("app: invalid choices reach the chart", APP, "build_chart(view.candles, valid, view.chart_title", "build_chart(view.candles, _chosen_all(), view.chart_title"),
    ("app: short history is not mentioned", APP, '        st.caption("Not enough history to draw: " + ", ".join(empty) + ".")', "        pass"),
    ("app: the picker's choice is ignored", APP, "key = st.session_state[ADD_KEY]", 'key = "sma"'),
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
    mutated = text.replace(old, new, 1)
    if "_chosen_all()" in mutated:
        mutated += "\n\ndef _chosen_all():\n    return [Selected.from_dict(d) for d in _chosen()]\n"
    p.write_bytes(mutated.encode("utf-8"))
    try:
        run = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True)
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-200:]
        print(("CAUGHT  " if run.returncode == 1 else "ERROR   " if run.returncode else "SURVIVED"), name, "|", tail)
    finally:
        p.write_bytes(original)
```

Expected: 13 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add src/athena/technicals/indicators.py tests/test_technicals_indicators.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add an indicator registry with validated, adjustable settings" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 2: Build the chart from a selection and let the page edit it

**Files:**
- Modify (by the scripts below): `src/athena/dashboard/charts.py`, `src/athena/dashboard/view.py`, `src/athena/dashboard/app.py`, `tests/test_dashboard_charts.py`, `tests/test_dashboard_view.py`, `tests/test_dashboard_service.py`, `tests/test_dashboard_app.py`, `tests/live/test_live_dashboard.py`
- Delete: `src/athena/technicals/series.py`, `tests/test_technicals_series.py`

**Interfaces:**
- Produces: `charts.build_chart(candles, selection, title, show_volume=True) -> go.Figure` (traces: `Price` candles, the overlay lines, `Volume`, then the pane lines, one row per pane indicator; every item must be valid); `DashboardView.candles: tuple[Candle, ...]` and `DashboardView.chart_title: str` (the fields `price_figure` and `rsi_figure` are gone); in the page, session keys `indicators` (list of plain dicts), `show_volume`, `add_indicator`; widget keys `ind-<id>-<setting>`, `remove-<id>`, `add_button`, `reset_button`; `app.CHART_NOTE`.
- Consumes: Task 1's registry; `athena.technicals.candles.Candle`.

- [ ] **Step 1: Update and add the tests**

Save as `$TEMP/s1hb_tests_page.py` and run it from the repository root. It rewrites the chart tests, adapts the view, service, live and page tests to the new view fields, and adds the page tests for the Indicators section (it prints two lines when done).

```python
import pathlib


def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) >= 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- tests/test_dashboard_charts.py: the chart is built from a selection
edit(
    "tests/test_dashboard_charts.py",
    [
        (
            "from athena.dashboard.charts import RSI_OVERBOUGHT, RSI_OVERSOLD, equity_chart, price_chart, rsi_chart\n",
            "from athena.dashboard.charts import build_chart, equity_chart\n",
        ),
        ("from athena.technicals.series import indicator_series\n", "from athena.technicals.indicators import Selected, compute, default_params, default_selection\n"),
        ("SERIES = indicator_series(CANDLES)\n", "\n\ndef chosen(key, **params):\n    return Selected(f\"{key}-1\", key, {**default_params(key), **params})\n"),
    ],
)
p = pathlib.Path("tests/test_dashboard_charts.py")
t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
start = t.index("def test_price_chart_has_candles_averages_bands_and_volume")
end = t.index("def test_equity_chart_draws_both_curves")
new_tests = '''def test_the_default_chart_has_candles_averages_bands_volume_and_rsi_on_one_figure():
    figure = build_chart(CANDLES, default_selection(), "SBIN")
    traces = traces_by_name(figure)
    assert list(traces) == ["Price", "SMA 50", "SMA 200", "Bollinger upper (20, 2)", "Bollinger lower (20, 2)", "Volume", "RSI 14"]
    assert isinstance(traces["Price"], go.Candlestick) and isinstance(traces["Volume"], go.Bar)
    assert figure.layout.title.text == "SBIN"
    assert [traces[n].yaxis for n in ("Price", "SMA 50", "Volume", "RSI 14")] == ["y", "y", "y2", "y3"]  # price, volume, RSI rows
    bands = [traces["Bollinger upper (20, 2)"], traces["Bollinger lower (20, 2)"]]
    assert {b.line.color for b in bands} == {"#8a8f98"} and {b.line.dash for b in bands} == {"dot"}  # grey and dotted, as ever


def test_every_trace_has_one_point_per_candle_and_values_come_from_the_data():
    traces = traces_by_name(build_chart(CANDLES, default_selection(), "SBIN"))
    for name, trace in traces.items():
        assert len(trace.x) == len(CANDLES), name
    assert traces["Price"].close[-1] == CANDLES[-1].close
    assert traces["SMA 50"].y[-1] == pytest.approx(compute(CANDLES, default_selection()[0])[0].values[-1])
    assert traces["Volume"].y[0] == CANDLES[0].volume


def test_volume_bars_are_coloured_by_candle_direction():
    traces = traces_by_name(build_chart(CANDLES, [], "x"))
    assert set(traces["Volume"].marker.color) == {"#2e9e6b"}  # a steady climb: every candle closes up


def test_the_volume_row_can_be_left_out_and_the_panes_move_up():
    traces = traces_by_name(build_chart(CANDLES, [chosen("rsi")], "x", show_volume=False))
    assert list(traces) == ["Price", "RSI 14"] and traces["RSI 14"].yaxis == "y2"
    assert build_chart(CANDLES, [], "x", show_volume=False).layout.height < build_chart(CANDLES, [], "x").layout.height  # no empty row left


def test_each_pane_indicator_gets_its_own_row_with_its_reference_lines_and_range():
    selection = [chosen("rsi"), chosen("macd"), chosen("stoch")]
    figure = build_chart(CANDLES, selection, "x")
    traces = traces_by_name(figure)
    assert [traces[n].yaxis for n in ("Volume", "RSI 14", "MACD", "%K")] == ["y2", "y3", "y4", "y5"]
    assert isinstance(traces["Histogram"], go.Bar) and traces["Histogram"].yaxis == "y4"
    assert len(figure.layout.shapes) == 2 + 1 + 2  # RSI 30 and 70, the MACD zero line, stochastic 20 and 80
    assert tuple(figure.layout.yaxis3.range) == (0, 100) and tuple(figure.layout.yaxis5.range) == (0, 100)
    assert figure.layout.height > build_chart(CANDLES, [], "x").layout.height  # more rows, taller figure


def test_overlays_share_the_price_axis_and_are_told_apart_by_colour_and_style():
    selection = [chosen("sma", length=20), chosen("ema", length=20), chosen("sar"), chosen("channel", length=60)]
    selection = [Selected(f"{item.key}-{n}", item.key, item.params) for n, item in enumerate(selection)]
    traces = traces_by_name(build_chart(CANDLES, selection, "x"))
    for name in ("SMA 20", "EMA 20", "Parabolic SAR", "High 60", "Low 60"):
        assert traces[name].yaxis == "y", name
    assert traces["Parabolic SAR"].mode == "markers" and traces["High 60"].line.dash == "dot"
    colors = [traces[n].line.color for n in ("SMA 20", "EMA 20")]
    assert len(set(colors)) == 2  # two overlays are never drawn in the same colour


def test_the_chart_refuses_no_candles_and_a_choice_that_cannot_be_drawn():
    with pytest.raises(ValueError, match="no candles"):
        build_chart([], default_selection(), "x")
    with pytest.raises(ValueError, match="Length must be between"):
        build_chart(CANDLES, [Selected("sma-1", "sma", {"length": 1})], "x")


'''
t = t[:start] + new_tests + t[end:]
p.write_text(t, encoding="utf-8", newline="\n")

# ---- tests/test_dashboard_view.py: the view carries plain candles, not a figure
edit(
    "tests/test_dashboard_view.py",
    [
        (
            "def test_charts_are_built_from_the_same_bars():\n    view = full_view()\n"
            "    assert view.price_figure is not None and view.rsi_figure is not None\n"
            "    assert len(view.price_figure.data[0].x) == len(BARS)\n"
            "    assert \"SBIN\" in view.price_figure.layout.title.text and \"2026-10-02\" in view.price_figure.layout.title.text\n",
            "def test_the_view_carries_the_candles_the_chart_is_drawn_from_and_a_title():\n    view = full_view()\n"
            "    assert len(view.candles) == len(BARS) and view.candles[-1].close == BARS[-1].close\n"
            "    assert \"SBIN\" in view.chart_title and \"2026-10-02\" in view.chart_title\n",
        ),
        ("    assert view.price_figure is None and view.rsi_figure is None\n", "    assert view.candles == () and view.chart_title == \"\"\n"),
        ("view.panels == () and view.price_figure is None and view.notes", "view.panels == () and view.candles == () and view.notes"),
    ],
)
edit(
    "tests/test_dashboard_service.py",
    [
        ("view.identifier == \"SBIN\" and view.price_figure is not None", "view.identifier == \"SBIN\" and view.candles"),
        ("fetch.symbols == [] and view.candidates and view.price_figure is None", "fetch.symbols == [] and view.candidates and view.candles == ()"),
        ("len(view.panels) == 2 and view.price_figure is not None", "len(view.panels) == 2 and view.candles"),
    ],
)
edit(
    "tests/live/test_live_dashboard.py",
    [
        ("view.status == OK and view.price_figure is not None and view.rsi_figure is not None", "view.status == OK and view.candles and view.chart_title"),
        ("view.candidates and view.price_figure is None", "view.candidates and view.candles == ()"),
        ("    technical, risk = view.panels\n", "    technical, risk = view.panels[:2]  # the fundamentals panels follow\n"),
        ("len(app.get(\"plotly_chart\")) == 2 and", "len(app.get(\"plotly_chart\")) == 1 and"),
    ],
)

# ---- tests/test_dashboard_app.py
edit(
    "tests/test_dashboard_app.py",
    [
        ("import dash_fakes\nfrom dash_fakes import", "import json\n\nimport dash_fakes\nfrom dash_fakes import"),
        (
            "def texts(elements):\n    return [element.value for element in elements]\n",
            "def texts(elements):\n    return [element.value for element in elements]\n\n\n"
            "def backtest_box(app):\n    return app.checkbox(key=\"backtest-SBIN\")\n\n\n"
            "def backtest_boxes(app):\n    return [box for box in app.checkbox if str(box.key).startswith(\"backtest-\")]\n\n\n"
            "def chart_names(app):\n    \"\"\"The names of the traces on the first chart on the page (the price chart).\"\"\"\n"
            "    return [trace.get(\"name\") for trace in json.loads(app.get(\"plotly_chart\")[0].proto.spec)[\"data\"]]\n",
        ),
        ("app.checkbox[0]", "backtest_box(app)"),
        ("test_the_page_draws_both_charts_and_each_panel_with_as_of_and_coverage", "test_the_page_draws_the_chart_and_each_panel_with_as_of_and_coverage"),
        ("len(open_app(FakeService(full_view(\"equity\")), \"sbin\").checkbox) == 1", "len(backtest_boxes(open_app(FakeService(full_view(\"equity\")), \"sbin\"))) == 1"),
        ("len(open_app(FakeService(full_view(\"etf\")), \"sbin\").checkbox) == 1", "len(backtest_boxes(open_app(FakeService(full_view(\"etf\")), \"sbin\"))) == 1"),
        ("len(open_app(FakeService(full_view(\"mutual_fund\")), \"sbin\").checkbox) == 0", "len(backtest_boxes(open_app(FakeService(full_view(\"mutual_fund\")), \"sbin\"))) == 0"),
        ("len(open_app(FakeService(build_view(ambiguous_result())), \"sbi\").checkbox) == 0", "len(backtest_boxes(open_app(FakeService(build_view(ambiguous_result())), \"sbi\"))) == 0"),
        ("len(open_app(FakeService(), \"\").checkbox) == 0", "len(backtest_boxes(open_app(FakeService(), \"\"))) == 0"),
        ("len(app.get(\"plotly_chart\")) == 4  # the price and RSI charts, plus one equity curve per rule", "len(app.get(\"plotly_chart\")) == 3  # the price chart, plus one equity curve per rule"),
        ("len(app.get(\"plotly_chart\")) == 4", "len(app.get(\"plotly_chart\")) == 3"),
        ("len(app.get(\"plotly_chart\")) == 2", "len(app.get(\"plotly_chart\")) == 1"),
    ],
)
edit(
    "tests/test_dashboard_app.py",
    [],
    append='''

DEFAULT_TRACES = ["Price", "SMA 50", "SMA 200", "Bollinger upper (20, 2)", "Bollinger lower (20, 2)", "Volume", "RSI 14"]


def add_indicator(app, key):
    app.selectbox(key="add_indicator").select(key)
    app.button(key="add_button").click().run()


def test_the_chart_starts_with_the_familiar_indicators_on_one_figure_and_says_they_are_for_looking():
    app = open_app(FakeService(full_view()), "sbin")
    assert not app.exception and chart_names(app) == DEFAULT_TRACES
    assert "Indicators" in [e.label for e in app.expander]
    assert any("Chart indicators are for looking only" in c for c in texts(app.caption))


def test_adding_an_indicator_draws_it_in_its_own_row():
    app = open_app(FakeService(full_view()), "sbin")
    add_indicator(app, "macd")
    assert not app.exception and chart_names(app) == DEFAULT_TRACES + ["MACD", "Signal", "Histogram"]


def test_changing_a_setting_redraws_the_line_with_the_new_value():
    app = open_app(FakeService(full_view()), "sbin")
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    names = chart_names(app)
    assert not app.exception and "SMA 30" in names and "SMA 50" not in names


def test_removing_an_indicator_takes_its_lines_off_the_chart():
    app = open_app(FakeService(full_view()), "sbin")
    app.button(key="remove-rsi-1").click().run()
    assert chart_names(app) == DEFAULT_TRACES[:-1]


def test_reset_brings_back_the_default_indicators_and_their_settings():
    app = open_app(FakeService(full_view()), "sbin")
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    add_indicator(app, "macd")
    app.button(key="remove-rsi-1").click().run()
    app.button(key="reset_button").click().run()
    assert not app.exception and chart_names(app) == DEFAULT_TRACES
    assert app.number_input(key="ind-sma-1-length").value == 50


def test_the_volume_row_can_be_switched_off():
    app = open_app(FakeService(full_view()), "sbin")
    app.checkbox(key="show_volume").uncheck().run()
    assert "Volume" not in chart_names(app)


def test_the_add_button_is_switched_off_at_the_limit_of_eight_indicators():
    app = open_app(FakeService(full_view()), "sbin")
    for key in ("ema", "wma", "sar", "macd"):
        add_indicator(app, key)
    assert {"EMA 20", "WMA 20", "Parabolic SAR", "MACD"} <= set(chart_names(app)) and app.button(key="add_button").disabled


def test_a_choice_that_cannot_be_drawn_is_reported_and_the_others_are_still_drawn():
    app = open_app(FakeService(full_view()), "sbin")
    add_indicator(app, "macd")
    app.number_input(key="ind-macd-1-fast").set_value(40).run()
    assert not app.exception and any("macd-1: the fast length must be shorter than the slow length" in w for w in texts(app.warning))
    names = chart_names(app)
    assert "MACD" not in names and "SMA 50" in names


def test_a_history_too_short_for_a_setting_is_said_on_the_page():
    app = open_app(FakeService(full_view()), "sbin")
    app.number_input(key="ind-sma-2-length").set_value(400).run()  # only 320 bars in this history
    assert any("Not enough history to draw: SMA 400." in c for c in texts(app.caption))


def test_the_chosen_indicators_stay_when_another_instrument_is_looked_up():
    service = RoutingService({"sbin": full_view(), "tcs": full_view()})
    app = open_app(service, "sbin")
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    app.text_input[0].set_value("tcs").run()
    assert service.queries == ["sbin", "tcs"] and "SMA 30" in chart_names(app)
''',
)
print("1h-b tests applied")
print("chart, view, service, live and page tests updated and added")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_charts.py tests/test_dashboard_view.py tests/test_dashboard_service.py tests/test_dashboard_app.py -q`
Expected: failures and errors (`ImportError: cannot import name 'build_chart'`, missing `candles` on the view).

- [ ] **Step 3: Apply the source edits**

Save the two scripts below as `$TEMP/s1hb_source_charts.py` and `$TEMP/s1hb_source_page.py` and run them from the repository root, in that order; then delete the superseded module:

```bash
git rm -q src/athena/technicals/series.py tests/test_technicals_series.py
```

`s1hb_source_charts.py` rewrites the top of `src/athena/dashboard/charts.py` (keeping `equity_chart`):

```python
import pathlib


p = pathlib.Path("src/athena/dashboard/charts.py")
t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
start = t.index("def _require(")
end = t.index("def equity_chart(")
new_head = '''from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from athena.backtest.engine import BacktestResult
from athena.technicals.candles import Candle
from athena.technicals.indicators import OVERLAY, PANE, REGISTRY, Line, Selected, compute

UP, DOWN = "#2e9e6b", "#d8574b"
OVERLAY_COLORS = ("#e0a030", "#7a5ccf", "#2aa7a1", "#d16ba5", "#5b8def", "#b5651d")
PANE_COLORS = ("#3b7dd8", "#e0a030", "#7a5ccf")
GREY = "#8a8f98"
PRICE_WEIGHT, VOLUME_WEIGHT, PANE_WEIGHT = 3.0, 1.0, 1.4
ROW_HEIGHT = 120


def _trace(line: Line, color: str, days: Sequence) -> go.BaseTraceType:
    if line.style == "bars":
        return go.Bar(x=days, y=list(line.values), name=line.name, marker_color=color, opacity=0.5)
    return go.Scatter(
        x=days, y=list(line.values), name=line.name, mode="markers" if line.style == "dots" else "lines",
        line=dict(color=color, width=1, dash="dot" if line.style == "dot" else "solid"), marker=dict(color=color, size=3),
    )


def build_chart(candles: Sequence[Candle], selection: Sequence[Selected], title: str, show_volume: bool = True) -> go.Figure:
    """One figure with a shared date axis: candles and the chosen overlays on top, volume under them, then one
    sub-chart per chosen pane indicator. Every item of `selection` must be valid (see `indicators.validate`)."""
    if not candles:
        raise ValueError("no candles to chart")
    days = [c.day for c in candles]
    computed = [(item, REGISTRY[item.key], compute(candles, item)) for item in selection]
    panes = [entry for entry in computed if entry[1].placement == PANE]
    weights = [PRICE_WEIGHT] + ([VOLUME_WEIGHT] if show_volume else []) + [PANE_WEIGHT] * len(panes)
    rows = len(weights)
    figure = make_subplots(rows=rows, cols=1, shared_xaxes=True, row_heights=[w / sum(weights) for w in weights], vertical_spacing=0.03)
    figure.add_trace(
        go.Candlestick(
            x=days, open=[c.open for c in candles], high=[c.high for c in candles],
            low=[c.low for c in candles], close=[c.close for c in candles], name="Price",
            increasing_line_color=UP, decreasing_line_color=DOWN,
        ),
        row=1, col=1,
    )

    overlay_index = 0
    for item, spec, lines in computed:
        if spec.placement != OVERLAY:
            continue
        color = GREY if item.key == "bbands" else OVERLAY_COLORS[overlay_index % len(OVERLAY_COLORS)]
        overlay_index += 0 if item.key == "bbands" else 1
        for line in lines:
            figure.add_trace(_trace(line, color, days), row=1, col=1)
    next_row = 2
    if show_volume:
        figure.add_trace(
            go.Bar(x=days, y=[c.volume for c in candles], name="Volume", marker_color=[UP if c.close >= c.open else DOWN for c in candles]),
            row=next_row, col=1,
        )
        next_row += 1
    for item, spec, lines in panes:
        for position, line in enumerate(lines):
            figure.add_trace(_trace(line, PANE_COLORS[position % len(PANE_COLORS)], days), row=next_row, col=1)
        for level in spec.levels:
            figure.add_hline(y=level, row=next_row, col=1, line=dict(color=GREY, width=1, dash="dot"))
        if spec.y_range:
            figure.update_yaxes(range=list(spec.y_range), row=next_row, col=1)
        figure.update_yaxes(title_text=spec.label, title_font=dict(size=10), row=next_row, col=1)
        next_row += 1
    figure.update_layout(
        title=title, xaxis_rangeslider_visible=False, height=260 + ROW_HEIGHT * rows, margin=dict(l=40, r=20, t=60, b=30),
        legend=dict(orientation="h"),
    )
    return figure


'''
p.write_text(new_head + t[end:], encoding="utf-8", newline="\n")
print("chart builder written")
```

`s1hb_source_page.py` edits `src/athena/dashboard/view.py` and `src/athena/dashboard/app.py`:

```python
import pathlib



def edit(path, pairs, append=""):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    for old, new in pairs:
        assert t.count(old) == 1, (path, old[:70])
        t = t.replace(old, new)
    p.write_text(t + append, encoding="utf-8", newline="\n")


# ---- the view carries the candles (plain data); the page draws whatever chart the person asks for
edit(
    "src/athena/dashboard/view.py",
    [
        ("from athena.dashboard.charts import price_chart, rsi_chart\n", ""),
        ("import plotly.graph_objects as go\n\n", ""),
        ("from athena.technicals.candles import candles_from_bars\n", "from athena.technicals.candles import Candle, candles_from_bars\n"),
        ("from athena.technicals.series import indicator_series\n", ""),
        ("    price_figure: go.Figure | None\n    rsi_figure: go.Figure | None\n", "    candles: tuple[Candle, ...]  # the daily history the chart is drawn from\n    chart_title: str\n"),
        (
            "    price_figure = rsi_figure = None\n    if candles:\n        series = indicator_series(candles)\n"
            "        label = f\"{resolution.identifier}  {resolution.name}  ({as_of})\"\n"
            "        price_figure = price_chart(candles, series, label)\n        rsi_figure = rsi_chart(candles, series)\n",
            "    chart_title = f\"{resolution.identifier}  {resolution.name}  ({as_of})\" if candles else \"\"\n",
        ),
        ("        price_figure, rsi_figure, (), tuple(notes),\n", "        tuple(candles), chart_title, (), tuple(notes),\n"),
        ("            result.status, result.query, \"\", \"\", \"\", \"\", None, (), {}, (), None, None, candidates, (result.ambiguity.reason,)\n",
         "            result.status, result.query, \"\", \"\", \"\", \"\", None, (), {}, (), (), \"\", candidates, (result.ambiguity.reason,)\n"),
    ],
)

# ---- the page: an Indicators section that edits a selection kept in the session
edit(
    "src/athena/dashboard/app.py",
    [
        (
            "from athena.orchestrator.report import DISCLAIMER\n",
            "from athena.orchestrator.report import DISCLAIMER\n"
            "from athena.dashboard.charts import build_chart\n"
            "from athena.technicals.indicators import (\n"
            "    MAX_INDICATORS, REGISTRY, Selected, default_params, default_selection, next_id, short_history, validate,\n"
            ")\n",
        ),
        (
            'PICK_KEY = "picked_instrument"  # a table click waiting to be moved into the search box\n',
            'PICK_KEY = "picked_instrument"  # a table click waiting to be moved into the search box\n'
            'INDICATORS_KEY = "indicators"  # the chosen indicators, as a list of plain dicts\n'
            'VOLUME_KEY = "show_volume"\n'
            'ADD_KEY = "add_indicator"\n'
            'CHART_NOTE = (\n'
            '    "Chart indicators are for looking only. The specialists read their own fixed windows, "\n'
            '    "so changing them here never changes a verdict."\n'
            ')\n',
        ),
        (
            "    if view.price_figure is not None:\n"
            "        st.plotly_chart(view.price_figure, width=\"stretch\")\n"
            "        st.plotly_chart(view.rsi_figure, width=\"stretch\")\n",
            "    if view.candles:\n        _chart_section(view)\n",
        ),
        (
            "def render(view: DashboardView) -> None:",
            '''def _chosen() -> list[dict]:
    if INDICATORS_KEY not in st.session_state:
        st.session_state[INDICATORS_KEY] = [item.to_dict() for item in default_selection()]
    return st.session_state[INDICATORS_KEY]


def _forget_widgets(prefix: str) -> None:
    for key in [key for key in st.session_state if key.startswith(prefix)]:
        del st.session_state[key]


def _add_indicator() -> None:
    chosen = _chosen()
    if len(chosen) < MAX_INDICATORS:
        key = st.session_state[ADD_KEY]
        chosen.append(Selected(next_id([Selected.from_dict(d) for d in chosen], key), key, default_params(key)).to_dict())


def _remove_indicator(item_id: str) -> None:
    st.session_state[INDICATORS_KEY] = [d for d in _chosen() if d["id"] != item_id]
    _forget_widgets(f"ind-{item_id}-")


def _reset_indicators() -> None:
    st.session_state[INDICATORS_KEY] = [item.to_dict() for item in default_selection()]
    _forget_widgets("ind-")


def _indicator_controls() -> list[Selected]:
    """The Indicators section: each chosen indicator with its settings and a remove button, an add picker, a reset."""
    chosen = _chosen()
    with st.expander("Indicators"):  # a fixed label: a changing one would collapse the section after every click
        for item in chosen:
            spec = REGISTRY[item["key"]]
            columns = st.columns([2, *([1] * len(spec.params)), 1])
            columns[0].markdown(f"**{spec.label}**")
            for column, param in zip(columns[1:], spec.params):
                number = float if not param.integer else int
                item["params"][param.name] = column.number_input(
                    param.label, min_value=number(param.minimum), max_value=number(param.maximum),
                    value=number(item["params"].get(param.name, param.default)), step=number(param.step),
                    key=f"ind-{item['id']}-{param.name}",
                )
            columns[-1].button("Remove", key=f"remove-{item['id']}", on_click=_remove_indicator, args=(item["id"],))
        picker, add, reset, volume = st.columns([3, 1, 1, 1])
        picker.selectbox("Add indicator", list(REGISTRY), format_func=lambda key: REGISTRY[key].label, key=ADD_KEY)
        add.button("Add", key="add_button", on_click=_add_indicator, disabled=len(chosen) >= MAX_INDICATORS)
        reset.button("Reset", key="reset_button", on_click=_reset_indicators)
        volume.checkbox("Volume", value=True, key=VOLUME_KEY)
    return [Selected.from_dict(d) for d in chosen]


def _chart_section(view: DashboardView) -> None:
    valid, problems = validate(_indicator_controls())
    for item_id, reason in problems.items():
        st.warning(f"{item_id}: {reason}")
    empty = short_history(view.candles, valid)
    if empty:
        st.caption("Not enough history to draw: " + ", ".join(empty) + ".")
    st.plotly_chart(build_chart(view.candles, valid, view.chart_title, st.session_state.get(VOLUME_KEY, True)), width="stretch")
    st.caption(CHART_NOTE)


def render(view: DashboardView) -> None:''',
        ),
    ],
)
print("view and page edited")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest -q` (expect `597 passed, 61 skipped`) and `.venv/Scripts/python -m pyflakes src tests` (prints nothing).

- [ ] **Step 5: Mutation check**

Run: `.venv/Scripts/python $TEMP/mutate_1hb.py charts view app` from the repository root.
Expected: 16 lines, all `CAUGHT`.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: draw the chart from a selection of indicators the person can add, tune and remove" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```

---

### Task 3: Live check and TRD

**Files:**
- Create: `tests/live/test_live_indicators.py`
- Modify: `TRD.md` (by the script below)

- [ ] **Step 1: Add the live test**

Create `tests/live/test_live_indicators.py`:

```python
import math
from datetime import date, timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.dashboard.charts import build_chart
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import REGISTRY, Selected, compute, default_params, validate

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def candles():
    end = date.today()
    bars = JugaadPriceAdapter().fetch_ohlcv("SBIN", "1d", since=end - timedelta(days=760), until=end)
    return candles_from_bars(bars)


def test_live_every_indicator_computes_on_real_prices_and_volume(candles):
    assert len(candles) > 400
    for key in REGISTRY:
        for line in compute(candles, Selected(f"{key}-1", key, default_params(key))):
            real = [v for v in line.values if v is not None]
            assert real and all(math.isfinite(v) for v in real), (key, line.name)


def test_live_a_chart_with_many_overlays_and_panes_can_be_built(candles):
    selection = [Selected(f"{key}-1", key, default_params(key)) for key in REGISTRY]
    valid, problems = validate(selection[:8])
    assert not problems
    assert len(build_chart(candles, valid, "SBIN").data) > 8
    panes = [item for item in selection if REGISTRY[item.key].placement == "pane"][:6]
    assert len(build_chart(candles, panes, "SBIN").data) > 6
```

- [ ] **Step 2: Run the live checks against real data**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest tests/live/test_live_indicators.py tests/live/test_live_dashboard.py --live -q`
Expected: `6 passed` (about 90 seconds; the dashboard tests load real market series and run the specialists, so they need the model keys in the environment file; if a model provider is unreachable, report it rather than editing the tests). If NSE is unreachable the price fetch fails: that is an environment problem; report it.

- [ ] **Step 3: Record it in the TRD**

Save the script below as `$TEMP/trd_1hb.py` and run it from the repository root. It prints `trd_1hb applied to ...`; an `AssertionError` means the TRD text differs from what the script expects: stop and report.

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
    "renders: candles + moving averages + Bollinger Bands + volume and momentum/pattern-recognition flags via ta-lib-python;",
    "renders: candles + moving averages + Bollinger Bands + volume and momentum/pattern-recognition flags via ta-lib-python (*implemented in Plans 1d and 1h-b:* the chart is built from a plain, JSON-friendly selection of up to 8 indicators from a registry of 16, each with validated, adjustable settings; the default is SMA 50, SMA 200, Bollinger Bands 20/2 and RSI 14 with volume, and the person can add, tune and remove indicators on the page; chart indicators are for looking only, so the specialists keep reading their own fixed windows);",
)
swap(
    "- **Oct 6, 2026 (Phase 1h-a)**",
    "- **Oct 6, 2026 (Phase 1h-b)** — The dashboard chart is built from a selection of indicators (`athena.technicals.indicators`): 6 overlays (SMA, EMA, WMA, Bollinger Bands, Parabolic SAR, a high-low channel where 252 bars is the 52-week high and low) and 10 sub-chart indicators (RSI, MACD, Stochastic, ADX with directional lines, ATR, OBV, CCI, MFI, Williams %R, ROC), one shared date axis, settings validated against their ranges. `DashboardView` now carries plain `candles` and a `chart_title` instead of two Plotly figures, and the page builds the figure from the person's selection (docs/superpowers/plans/2026-10-06-phase-1h-b-indicators.md). The old fixed-indicator series module is removed.\n- **Oct 6, 2026 (Phase 1h-a)**",
)
if crlf:
    t = t.replace("\n", "\r\n")
path.write_bytes(t.encode("utf-8"))
print("trd_1hb applied to", path)
```

- [ ] **Step 4: Final verification**

Run the whole suite `.venv/Scripts/python -m pytest -q` (expect `597 passed, 63 skipped`), `.venv/Scripts/python -m pyflakes src tests` (prints nothing) and `git status --short` (only `TRD.md` and the new live test).

- [ ] **Step 5: Commit**

```bash
git add TRD.md tests/live/test_live_indicators.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live indicator checks; record the selectable indicators in the TRD" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
git push
```
