# Phase 1a — Specialist Base Class and Quant/Technical Specialist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared specialist plumbing (TRD §2.2) and the first specialist on it, Quant/Technical (TRD §2.3), with every technical figure computed in code by ta-lib and handed to the model as data. No real LLM provider is wired yet: the model sits behind an `LLMClient` protocol and all tests use scripted fakes.

**Architecture:** `athena.technicals` turns daily bars into a technical packet in the same shape as the Phase 0d metrics packet (`metrics`, `missing`, `missing_reasons`). `athena.agents.base.Specialist` takes a `SpecialistSpec` (name, persona prompt, critical and optional metric names) and an `LLMClient`; it derives the coverage label from the spec rather than trusting the model, abstains without calling the model when a critical metric is missing, validates the reply against the TRD §3 contract and the Phase 0e number-grounding check, retries once with feedback, caches by prompt hash, and fails loud or quiet. A specialist is therefore only data: `QUANT_TECHNICAL` is a persona string plus two tuples.

**Tech Stack:** Python >= 3.11, numpy, TA-Lib (new; Windows cp313 wheel bundles the C library), pytest.

**Spec:** `TRD.md` §2.2 (base class), §2.3 (Quant/Technical), §3 (output contract, metrics packet), §6.1 (OHLCV is fully covered); `PRD.md` FR-1, FR-2.

**Plan series:** 0a-0e (done) -> **1a (this plan)** -> 1b (real LLM provider, orchestrator skeleton with simple blend) -> 1c (remaining equity specialists in ratio-only mode, dashboard, backtest).

**Suggested models:** Sonnet at medium effort. Prototyped end to end in a scratch copy first: 271 offline tests and the 2 live tests passed; mutation checks confirmed that disabling the grounding check or the abstention path makes tests fail.

## Verified findings (5 Oct 2026)

- `pip install TA-Lib` gives `ta_lib-0.8.1-cp313-cp313-win_amd64.whl` with the C library bundled; no manual native build is needed on this machine.
- Live packets built from ~25 months of jugaad-data daily bars are complete (no missing metrics) for both an equity and an ETF. On the day of the run: SBIN closed 954.1 with RSI 32.3, daily and weekly trends down, monthly mixed, so `not_aligned`; NIFTYBEES closed 256.5 with RSI 19.8 and all three timeframes down, so `aligned_down`. These are sanity checks, not recommendations.
- The monthly trend needs 15 monthly candles (a 10-month average plus a 5-month slope), so about 320 trading days of history. With less, `trend_monthly` and `trend_alignment` are listed as missing and the specialist reports `partial` coverage, which is the designed behaviour.
- A model is never allowed to set `data_coverage` or `missing`: a reply that includes them is rejected as having keys the contract does not allow. The class sets both from the spec.
- **Deliberately not built:** `blind` mode for backtests (belongs with the backtest plan, where tickers and dates are actually stripped), a rule forcing a `partial` reasoning to name its gaps (too brittle to check by string match; the abstention test covers `missing`), and any merge of this packet with the Phase 0d risk metrics (YAGNI until a second specialist needs both).

## Global Constraints

- No network in the default test run; the live test is opt-in via `--live`.
- The output contract is TRD §3 exactly: `signal`, `confidence` (integer 0-100), `reasoning`, `data_coverage`, `missing`. The model supplies only the first three.
- Compute in code, judge in prompts: the persona must say "do not calculate your own" and every figure the model cites must appear in the packet (`check_grounded`).
- Compliance language: forward-looking views are a base case with its main risk, never a price target.
- Timezone-aware UTC datetimes everywhere; "trading date" means the IST date (`ist_date`).
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | add the `TA-Lib>=0.6` dependency |
| `src/athena/technicals/candles.py` | `Candle`, `candles_from_bars`, `resample` (weekly / monthly) |
| `src/athena/technicals/packet.py` | `build_technical_packet` (ta-lib indicators, trend labels, alignment, reward:risk) |
| `src/athena/agents/base.py` | `LLMClient`, `SpecialistSpec`, `Specialist`, `SpecialistError`, `abstention`, `parse_model_json` |
| `src/athena/agents/quant_technical.py` | `PERSONA`, `QUANT_TECHNICAL` |
| `tests/test_technicals_candles.py`, `tests/test_technicals_packet.py`, `tests/test_specialist_base.py`, `tests/test_quant_technical.py`, `tests/live/test_live_technicals.py` | one test module per source module, plus the live check |

---

### Task 1: ta-lib dependency and candles

**Files:**
- Modify: `pyproject.toml`
- Create: `src/athena/technicals/__init__.py` (empty file), `src/athena/technicals/candles.py`
- Test: `tests/test_technicals_candles.py`

**Interfaces:**
- Produces (`athena.technicals.candles`): constants `WEEK = "week"`, `MONTH = "month"`; `@dataclass(frozen=True) Candle(day: date, open, high, low, close, volume: float)`; `candles_from_bars(bars: Sequence[Bar]) -> list[Candle]` (sorted oldest first, one per IST date, a repeated date keeps the later bar); `resample(candles, period: str) -> list[Candle]` (ISO-week or calendar-month merge; open of the first, high max, low min, close of the last, volume sum, `day` is the last day in the period; unknown period raises `ValueError`).

- [ ] **Step 1: Add the dependency and install it**

In `pyproject.toml`, change the `dependencies` line to:

```toml
dependencies = ["duckdb>=1.0", "requests>=2.31", "jugaad-data>=0.35", "yfinance>=1.0", "numpy>=1.26", "TA-Lib>=0.6"]
```

Run: `.venv/Scripts/python -m pip install -e ".[dev]" && .venv/Scripts/python -c "import talib; print(talib.__version__)"`
Expected: prints a version (0.6 or later).

- [ ] **Step 2: Write the failing tests `tests/test_technicals_candles.py`**

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import Bar
from athena.technicals.candles import MONTH, WEEK, Candle, candles_from_bars, resample

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def bar(day: date, o, h, l, c, v=100.0):
    # jugaad-style: IST midnight stored as 18:30 UTC of the previous calendar day
    stamp = datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1)
    return Bar("X", stamp, o, h, l, c, v, NOW, "t")


def test_candles_are_sorted_and_deduplicated_by_ist_date():
    bars = [bar(date(2026, 9, 2), 1, 2, 0.5, 1.5), bar(date(2026, 9, 1), 1, 1, 1, 1), bar(date(2026, 9, 2), 1, 3, 0.4, 2.5)]
    candles = candles_from_bars(bars)
    assert [c.day for c in candles] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert candles[1].close == 2.5  # the later duplicate wins


def test_weekly_resample_merges_ohlcv():
    days = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 14)]  # Mon,Tue,Wed | next Mon
    candles = candles_from_bars(
        [bar(days[0], 10, 12, 9, 11, 100), bar(days[1], 11, 15, 10, 14, 200), bar(days[2], 14, 14, 8, 9, 300), bar(days[3], 9, 10, 9, 10, 50)]
    )
    weekly = resample(candles, WEEK)
    assert weekly == [Candle(days[2], 10, 15, 8, 9, 600), Candle(days[3], 9, 10, 9, 10, 50)]


def test_monthly_resample_and_unknown_period():
    candles = candles_from_bars([bar(date(2026, 8, 31), 1, 2, 1, 2), bar(date(2026, 9, 1), 2, 3, 2, 3)])
    assert [c.day for c in resample(candles, MONTH)] == [date(2026, 8, 31), date(2026, 9, 1)]
    with pytest.raises(ValueError):
        resample(candles, "year")
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_candles.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.technicals'`.

- [ ] **Step 4: Create the empty `src/athena/technicals/__init__.py` and write `src/athena/technicals/candles.py`**

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.contracts import Bar
from athena.trading_calendar import ist_date

WEEK = "week"
MONTH = "month"


@dataclass(frozen=True)
class Candle:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def candles_from_bars(bars: Sequence[Bar]) -> list[Candle]:
    """Daily candles keyed by IST trading date, oldest first. A repeated date keeps the last bar."""
    by_day = {
        ist_date(bar.timestamp): Candle(ist_date(bar.timestamp), bar.open, bar.high, bar.low, bar.close, bar.volume)
        for bar in sorted(bars, key=lambda b: b.timestamp)
    }
    return [by_day[day] for day in sorted(by_day)]


def _period_key(day: date, period: str) -> tuple[int, int]:
    if period == WEEK:
        iso = day.isocalendar()
        return (iso.year, iso.week)
    if period == MONTH:
        return (day.year, day.month)
    raise ValueError(f"unknown period {period!r}")


def resample(candles: Sequence[Candle], period: str) -> list[Candle]:
    """Weekly (ISO week) or monthly candles. The latest period may be incomplete; it is kept as-is."""
    groups: dict[tuple[int, int], list[Candle]] = {}
    for candle in candles:
        groups.setdefault(_period_key(candle.day, period), []).append(candle)
    merged = []
    for key in sorted(groups):
        group = groups[key]
        merged.append(
            Candle(
                day=group[-1].day,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return merged
```

- [ ] **Step 5: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_candles.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `3 passed`, then `247 passed, 37 skipped`.

- [ ] **Step 6: Commit and push**

```bash
git add pyproject.toml src/athena/technicals tests/test_technicals_candles.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add ta-lib dependency and daily/weekly/monthly candles"
git push
```

---

### Task 2: Technical packet

**Files:**
- Create: `src/athena/technicals/packet.py`
- Test: `tests/test_technicals_packet.py`

**Interfaces:**
- Consumes: `candles_from_bars`, `resample`, `WEEK`, `MONTH`, `Candle` (Task 1); `Bar`, `InsufficientData` from `athena.contracts`.
- Produces (`athena.technicals.packet`): `build_technical_packet(instrument: str, as_of: datetime, bars: Sequence[Bar]) -> dict` returning `{"instrument", "as_of", "metrics", "missing", "missing_reasons"}`. Each metric is `{"value", "unit", "inputs", "window", "source"[, "note"]}`; trend and alignment values are strings, all others are floats rounded to 4 places. Metric names: `last_close`, `trend_daily`, `trend_weekly`, `trend_monthly`, `trend_alignment` (`aligned_up` / `aligned_down` / `not_aligned`), `sma_50`, `sma_200`, `rsi_14`, `bollinger_pct_b`, `bollinger_bandwidth`, `atr_14`, `volume_ratio_20_50`, `high_60d`, `reward_risk`. Constants `TREND_FRAMES`, `REWARD_RISK_NOTE`.

- [ ] **Step 1: Write the failing tests `tests/test_technicals_packet.py`**

```python
import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import Bar
from athena.technicals.packet import build_technical_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def weekdays(count: int, end: date = date(2026, 10, 2)) -> list[date]:
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def make_bars(closes, volume=1_000.0, spread=1.0):
    bars = []
    for day, close in zip(weekdays(len(closes)), closes):
        stamp = datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1)
        bars.append(Bar("X", stamp, close, close + spread, close - spread, close, volume, NOW, "t"))
    return bars


def packet_for(closes, **kwargs):
    return build_technical_packet("X", NOW, make_bars(closes, **kwargs))


def value(packet, name):
    return packet["metrics"][name]["value"]


UP = [100.0 + i * 0.5 for i in range(400)]  # steady climb
DOWN = [300.0 - i * 0.5 for i in range(400)]


def test_a_steady_uptrend_is_aligned_up_with_every_metric_present():
    packet = packet_for(UP)
    assert packet["missing"] == []
    for frame in ("trend_daily", "trend_weekly", "trend_monthly"):
        assert value(packet, frame) == "up"
    assert value(packet, "trend_alignment") == "aligned_up"
    assert value(packet, "last_close") == UP[-1]
    assert value(packet, "sma_50") == pytest.approx(sum(UP[-50:]) / 50, abs=1e-3)
    assert value(packet, "rsi_14") > 90  # relentless climb
    assert value(packet, "bollinger_pct_b") > 0.5
    assert value(packet, "high_60d") == UP[-1] + 1.0
    assert value(packet, "reward_risk") == pytest.approx(1.0 / (2 * value(packet, "atr_14")), abs=1e-3)


def test_a_steady_downtrend_is_aligned_down():
    packet = packet_for(DOWN)
    assert value(packet, "trend_alignment") == "aligned_down"
    assert value(packet, "rsi_14") < 10
    assert value(packet, "reward_risk") > 2  # far below the 60-day high


def test_mixed_timeframes_are_not_aligned():
    rally_then_drop = UP[:380] + [UP[379] - i * 2.0 for i in range(1, 21)]
    packet = packet_for(rally_then_drop)
    assert value(packet, "trend_daily") != "up"
    assert value(packet, "trend_monthly") != "down"  # the long view has not turned
    assert value(packet, "trend_alignment") == "not_aligned"


def test_short_history_lists_what_cannot_be_computed_with_reasons():
    packet = packet_for(UP[:60])
    assert "trend_monthly" in packet["missing"] and "trend_alignment" in packet["missing"]
    assert "sma_200" in packet["missing"]
    assert "needs" in packet["missing_reasons"]["sma_200"]
    assert value(packet, "trend_daily") == "up"  # what can be computed still is


def test_empty_input_is_all_missing_not_an_error():
    packet = build_technical_packet("X", NOW, [])
    assert packet["metrics"] == {}
    assert "last_close" in packet["missing"] and "reward_risk" in packet["missing"]


def test_zero_volume_marks_volume_ratio_missing():
    packet = packet_for(UP, volume=0.0)
    assert "volume_ratio_20_50" in packet["missing"]
    assert packet["missing_reasons"]["volume_ratio_20_50"] == "no volume data"


def test_volume_ratio_reflects_recent_participation():
    bars = make_bars(UP, volume=100.0)
    bars = bars[:-20] + [Bar(b.symbol, b.timestamp, b.open, b.high, b.low, b.close, 400.0, NOW, "t") for b in bars[-20:]]
    ratio = value(build_technical_packet("X", NOW, bars), "volume_ratio_20_50")
    assert ratio == pytest.approx(400 / ((400 * 20 + 100 * 30) / 50), abs=1e-3)


def test_flat_prices_have_no_percent_b_and_no_reward_risk():
    packet = packet_for([100.0] * 80, spread=0.0)
    assert "bollinger_pct_b" in packet["missing"]
    assert "reward_risk" in packet["missing"]


def test_packet_is_json_ready_and_every_number_is_finite():
    packet = packet_for(UP)
    json.dumps(packet)
    for metric in packet["metrics"].values():
        if not isinstance(metric["value"], str):
            assert math.isfinite(metric["value"])
    assert packet["instrument"] == "X" and packet["as_of"] == NOW.isoformat()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_packet.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.technicals.packet'`.

- [ ] **Step 3: Write `src/athena/technicals/packet.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import numpy as np
import talib

from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import MONTH, WEEK, Candle, candles_from_bars, resample

# (timeframe, moving-average length). Slope compares the average with its value SLOPE_LOOKBACK candles ago.
TREND_FRAMES = (("daily", 50), ("weekly", 20), ("monthly", 10))
SLOPE_LOOKBACK = 5
BOLLINGER_PERIOD = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_SHORT, VOLUME_LONG = 20, 50
SWING_WINDOW = 60
STOP_ATR_MULTIPLE = 2.0
REWARD_RISK_NOTE = (
    f"long side: reward is the distance up to the {SWING_WINDOW}-day high, risk is a stop "
    f"{STOP_ATR_MULTIPLE:g} x ATR{ATR_PERIOD} below the last close; 0 means price is at the high"
)
SOURCE = "computed from daily OHLCV (ta-lib)"


def _arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray]:
    return {
        "open": np.array([c.open for c in candles], dtype=float),
        "high": np.array([c.high for c in candles], dtype=float),
        "low": np.array([c.low for c in candles], dtype=float),
        "close": np.array([c.close for c in candles], dtype=float),
        "volume": np.array([c.volume for c in candles], dtype=float),
    }


def _need(count: int, minimum: int, what: str) -> None:
    if count < minimum:
        raise InsufficientData(f"{what} needs {minimum} candles, have {count}")


def _trend(candles: Sequence[Candle], length: int) -> str:
    _need(len(candles), length + SLOPE_LOOKBACK, f"{length}-candle trend")
    close = _arrays(candles)["close"]
    average = talib.SMA(close, length)
    above = close[-1] > average[-1]
    rising = average[-1] > average[-1 - SLOPE_LOOKBACK]
    if above and rising:
        return "up"
    if not above and not rising:
        return "down"
    return "mixed"


def build_technical_packet(instrument: str, as_of: datetime, bars: Sequence[Bar]) -> dict[str, Any]:
    """Technical-analysis packet in the metrics-packet shape (TRD section 3): every figure a
    Quant/Technical specialist may cite, computed in code. A figure that cannot be computed is
    listed in `missing` with a reason, never silently dropped."""
    daily = candles_from_bars(bars)
    frames = {"daily": daily, "weekly": resample(daily, WEEK), "monthly": resample(daily, MONTH)}
    data = _arrays(daily)
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    window = f"{len(daily)} daily candles {daily[0].day}..{daily[-1].day}" if daily else "0 candles"

    def attempt(name: str, unit: str, inputs: list[str], compute: Callable[[], Any], note: str | None = None) -> None:
        try:
            value = compute()
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metrics[name] = {
            "value": value if isinstance(value, str) else round(float(value), 4),
            "unit": unit,
            "inputs": inputs,
            "window": window,
            "source": SOURCE,
        }
        if note:
            metrics[name]["note"] = note

    def sma(length: int) -> float:
        _need(len(daily), length, f"{length}-day average")
        return float(talib.SMA(data["close"], length)[-1])

    def bollinger() -> tuple[float, float, float]:
        _need(len(daily), BOLLINGER_PERIOD, "Bollinger Bands")
        upper, middle, lower = talib.BBANDS(data["close"], BOLLINGER_PERIOD, 2, 2)
        return float(upper[-1]), float(middle[-1]), float(lower[-1])

    def percent_b() -> float:
        upper, _, lower = bollinger()
        if upper == lower:
            raise InsufficientData("Bollinger Bands have zero width")
        return (daily[-1].close - lower) / (upper - lower)

    def bandwidth() -> float:
        upper, middle, lower = bollinger()
        return (upper - lower) / middle

    def atr() -> float:
        _need(len(daily), ATR_PERIOD + 1, f"ATR{ATR_PERIOD}")
        return float(talib.ATR(data["high"], data["low"], data["close"], ATR_PERIOD)[-1])

    def rsi() -> float:
        _need(len(daily), RSI_PERIOD + 1, f"RSI{RSI_PERIOD}")
        return float(talib.RSI(data["close"], RSI_PERIOD)[-1])

    def volume_ratio() -> float:
        _need(len(daily), VOLUME_LONG, "volume ratio")
        long_average = data["volume"][-VOLUME_LONG:].mean()
        if long_average <= 0:
            raise InsufficientData("no volume data")
        return float(data["volume"][-VOLUME_SHORT:].mean() / long_average)

    def high_60d() -> float:
        _need(len(daily), SWING_WINDOW, f"{SWING_WINDOW}-day high")
        return float(data["high"][-SWING_WINDOW:].max())

    def reward_risk() -> float:
        risk = STOP_ATR_MULTIPLE * atr()
        if risk <= 0:
            raise InsufficientData("ATR is zero")
        return max(high_60d() - daily[-1].close, 0.0) / risk

    def last_close() -> float:
        _need(len(daily), 1, "last close")
        return daily[-1].close

    attempt("last_close", "price", ["close"], last_close)
    for name, length in TREND_FRAMES:
        attempt(
            f"trend_{name}", "label", ["close", f"sma_{length}"], lambda n=name, ln=length: _trend(frames[n], ln),
            note=f"close vs its {length}-candle average and that average's {SLOPE_LOOKBACK}-candle slope: up, down or mixed",
        )
    labels = [metrics[f"trend_{name}"]["value"] for name, _ in TREND_FRAMES if f"trend_{name}" in metrics]

    def alignment() -> str:
        if len(labels) < len(TREND_FRAMES):
            raise InsufficientData("alignment needs all three timeframes")
        if all(label == "up" for label in labels):
            return "aligned_up"
        if all(label == "down" for label in labels):
            return "aligned_down"
        return "not_aligned"

    attempt("trend_alignment", "label", [f"trend_{name}" for name, _ in TREND_FRAMES], alignment)
    attempt("sma_50", "price", ["close"], lambda: sma(50))
    attempt("sma_200", "price", ["close"], lambda: sma(200))
    attempt("rsi_14", "index", ["close"], rsi)
    attempt("bollinger_pct_b", "ratio", ["close"], percent_b, note="0 = lower band, 1 = upper band")
    attempt("bollinger_bandwidth", "fraction", ["close"], bandwidth, note="band width as a fraction of the middle band")
    attempt("atr_14", "price", ["high", "low", "close"], atr)
    attempt(
        f"volume_ratio_{VOLUME_SHORT}_{VOLUME_LONG}", "ratio", ["volume"], volume_ratio,
        note="average volume of the last 20 days over the last 50; above 1 means rising participation",
    )
    attempt("high_60d", "price", ["high"], high_60d)
    attempt("reward_risk", "ratio", ["high", "low", "close"], reward_risk, note=REWARD_RISK_NOTE)

    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
    }
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_packet.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `9 passed`, then `256 passed, 37 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/technicals/packet.py tests/test_technicals_packet.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add ta-lib technical packet with multi-timeframe trend alignment"
git push
```

---

### Task 3: Specialist base class

**Files:**
- Create: `src/athena/agents/__init__.py` (empty file), `src/athena/agents/base.py`
- Test: `tests/test_specialist_base.py`

**Interfaces:**
- Consumes: `derive_coverage(critical, optional, available) -> (Coverage, missing)` from `athena.coverage`; `check_grounded` from `athena.evaluation.grounding`; `validate_specialist_output` from `athena.evaluation.schema`; `AthenaError`, `Coverage` from `athena.contracts`.
- Produces (`athena.agents.base`): `LLMClient` protocol with `complete(system: str, user: str) -> str`; `SpecialistError(AthenaError)`; `@dataclass(frozen=True) SpecialistSpec(name: str, persona: str, critical: tuple[str, ...], optional: tuple[str, ...] = ())`; `abstention(missing, reason) -> dict`; `parse_model_json(text) -> dict` (raises `ValueError`); `Specialist(spec, llm, fail_quiet=False, max_attempts=2)` with `analyze(packet: Mapping) -> dict` (the full TRD §3 output) and a `model_calls` counter. `analyze` reads available inputs from `packet["metrics"]`.

- [ ] **Step 1: Write the failing tests `tests/test_specialist_base.py`**

```python
import json

import pytest

from athena.agents.base import Specialist, SpecialistError, SpecialistSpec, parse_model_json

SPEC = SpecialistSpec("Test", "You are a test analyst.", critical=("a",), optional=("b",))
PACKET = {"instrument": "X", "as_of": "2026-10-05T04:00:00+00:00", "metrics": {"a": {"value": 42.5}, "b": {"value": 7.25}}, "missing": []}


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


GOOD = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5 and b is 7.25."})
GOOD_A_ONLY = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5."})


def test_parse_model_json_handles_fences_and_surrounding_text():
    assert parse_model_json('```json\n{"x": 1}\n```') == {"x": 1}
    assert parse_model_json('Here you go: {"x": 1} done') == {"x": 1}
    with pytest.raises(ValueError):
        parse_model_json("no braces here")
    with pytest.raises(ValueError):
        parse_model_json("{not json}")


def test_valid_reply_becomes_a_full_contract_output():
    out = Specialist(SPEC, Scripted(GOOD)).analyze(PACKET)
    assert out == {"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5 and b is 7.25.", "data_coverage": "full", "missing": []}


def test_system_prompt_carries_persona_and_contract_and_user_prompt_carries_data():
    llm = Scripted(GOOD)
    Specialist(SPEC, llm).analyze(PACKET)
    system, user = llm.calls[0]
    assert "You are a test analyst." in system and "one JSON object" in system
    assert "42.5" in user and "Specialist: Test" in user


def test_missing_critical_input_abstains_without_calling_the_model():
    llm = Scripted(GOOD)
    out = Specialist(SPEC, llm).analyze({"metrics": {"b": {"value": 1}}})
    assert llm.calls == []
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")
    assert out["missing"] == ["a"] and "(a)" in out["reasoning"]


def test_missing_optional_input_is_partial_and_the_prompt_says_so():
    llm = Scripted(GOOD_A_ONLY)
    out = Specialist(SPEC, llm).analyze({"metrics": {"a": {"value": 42.5}}})
    assert (out["data_coverage"], out["missing"]) == ("partial", ["b"])
    assert "PARTIAL" in llm.calls[0][1] and "Missing: b" in llm.calls[0][1]


def test_model_cannot_choose_its_own_coverage_label():
    sneaky = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "a is 42.5.", "data_coverage": "full", "missing": []})
    llm = Scripted(sneaky, GOOD_A_ONLY)
    out = Specialist(SPEC, llm).analyze({"metrics": {"a": {"value": 42.5}}})
    assert len(llm.calls) == 2 and "does not allow" in llm.calls[1][1]
    assert out["data_coverage"] == "partial"


def test_ungrounded_figure_is_rejected_with_feedback_then_accepted():
    invented = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 99.9."})
    llm = Scripted(invented, GOOD)
    out = Specialist(SPEC, llm).analyze(PACKET)
    assert out["reasoning"].startswith("Metric a is 42.5")
    assert "99.9" in llm.calls[1][1] and "rejected" in llm.calls[1][1]


def test_invalid_contract_values_are_rejected():
    bad = json.dumps({"signal": "buy", "confidence": 70, "reasoning": "a is 42.5."})
    with pytest.raises(SpecialistError) as caught:
        Specialist(SPEC, Scripted(bad)).analyze(PACKET)
    assert "signal must be one of" in str(caught.value)


def test_unparseable_reply_fails_loud_by_default_and_quiet_on_request():
    with pytest.raises(SpecialistError):
        Specialist(SPEC, Scripted("I refuse")).analyze(PACKET)
    out = Specialist(SPEC, Scripted("I refuse"), fail_quiet=True).analyze(PACKET)
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")
    assert out["missing"] == ["valid_model_output"]


def test_identical_requests_are_served_from_cache_and_copies_are_independent():
    llm = Scripted(GOOD)
    specialist = Specialist(SPEC, llm)
    first = specialist.analyze(PACKET)
    first["missing"].append("tampered")
    second = specialist.analyze(PACKET)
    assert specialist.model_calls == 1 and second["missing"] == []
    specialist.analyze({"metrics": {"a": {"value": 42.5}, "b": {"value": 7.25}}, "instrument": "Y"})
    assert specialist.model_calls == 2  # different data, different key
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_specialist_base.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.agents'`.

- [ ] **Step 3: Create the empty `src/athena/agents/__init__.py` and write `src/athena/agents/base.py`**

```python
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from athena.contracts import AthenaError, Coverage
from athena.coverage import derive_coverage
from athena.evaluation.grounding import check_grounded
from athena.evaluation.schema import validate_specialist_output

MODEL_KEYS = ("signal", "confidence", "reasoning")
OUTPUT_INSTRUCTIONS = (
    'Reply with one JSON object and nothing else: {"signal": "bullish"|"bearish"|"neutral", '
    '"confidence": 0-100, "reasoning": "2-4 sentences citing specific figures from the data"}. '
    "Cite only figures that appear in the data."
)


class LLMClient(Protocol):
    """Anything that turns a system prompt and a user message into text. Provider choice lives behind this."""

    def complete(self, system: str, user: str) -> str: ...


class SpecialistError(AthenaError):
    """The model never produced an output that passed validation (fail-loud mode)."""


@dataclass(frozen=True)
class SpecialistSpec:
    """A specialist is only data: a name, a persona prompt, and the metrics it must / may have."""

    name: str
    persona: str
    critical: tuple[str, ...]
    optional: tuple[str, ...] = ()


def abstention(missing: Sequence[str], reason: str) -> dict[str, Any]:
    return {
        "signal": "neutral",
        "confidence": 0,
        "reasoning": reason,
        "data_coverage": Coverage.INSUFFICIENT.value,
        "missing": list(missing),
    }


def parse_model_json(text: str) -> dict[str, Any]:
    """The first JSON object in `text`, tolerating a markdown fence or a sentence around it."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1] if "{" in text else ""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no JSON object found in the reply ({exc.msg})") from exc
    if not isinstance(parsed, dict):
        raise ValueError("the reply is not a JSON object")
    return parsed


class Specialist:
    """Shared plumbing for every specialist (TRD 2.2): coverage from declared inputs, abstention
    without calling the model, JSON parsing, contract validation, number grounding, retry with
    feedback, response caching, and fail-loud / fail-quiet handling."""

    def __init__(self, spec: SpecialistSpec, llm: LLMClient, fail_quiet: bool = False, max_attempts: int = 2):
        self.spec = spec
        self.llm = llm
        self.fail_quiet = fail_quiet
        self.max_attempts = max_attempts
        self._cache: dict[str, dict[str, Any]] = {}
        self.model_calls = 0

    def analyze(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        coverage, missing = derive_coverage(self.spec.critical, self.spec.optional, packet.get("metrics", {}))
        if coverage is Coverage.INSUFFICIENT:
            critical_gaps = [name for name in missing if name in self.spec.critical]
            return abstention(missing, f"No view formed: required inputs are missing ({', '.join(critical_gaps)}).")

        system = f"{self.spec.persona}\n\n{OUTPUT_INSTRUCTIONS}"
        user = self._render(packet, coverage, missing)
        key = hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()
        if key in self._cache:
            return json.loads(json.dumps(self._cache[key]))

        feedback = ""
        errors: list[str] = []
        for _ in range(self.max_attempts):
            self.model_calls += 1
            reply = self.llm.complete(system, user + feedback)
            output, errors = self._check(reply, packet, coverage, missing)
            if not errors:
                self._cache[key] = output
                return json.loads(json.dumps(output))
            feedback = "\n\nYour previous reply was rejected: " + "; ".join(errors) + ". Reply again, fixing this."

        if self.fail_quiet:
            return abstention(["valid_model_output"], "No view formed: the model output failed validation.")
        raise SpecialistError(f"{self.spec.name}: no valid output after {self.max_attempts} attempts: {errors}")

    def _render(self, packet: Mapping[str, Any], coverage: Coverage, missing: Sequence[str]) -> str:
        lines = [f"Specialist: {self.spec.name}", "Data (metrics packet):", json.dumps(packet, indent=2, sort_keys=True)]
        if coverage is Coverage.PARTIAL:
            lines.append(
                f"Data coverage is PARTIAL. Missing: {', '.join(missing)}. "
                "Say which of your conclusions this gap affects, and qualify them."
            )
        return "\n".join(lines)

    def _check(
        self, reply: str, packet: Mapping[str, Any], coverage: Coverage, missing: Sequence[str]
    ) -> tuple[dict[str, Any], list[str]]:
        try:
            parsed = parse_model_json(reply)
        except ValueError as exc:
            return {}, [str(exc)]
        extra = sorted(parsed.keys() - set(MODEL_KEYS))
        if extra:
            return {}, [f"reply has keys the contract does not allow: {extra}"]
        output = {**parsed, "data_coverage": coverage.value, "missing": list(missing)}
        errors = validate_specialist_output(output)
        if errors:
            return {}, errors
        grounding = check_grounded(output["reasoning"], packet)
        if not grounding.ok:
            return {}, [f"figures not found in the data: {list(grounding.ungrounded)}"]
        return output, []
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_specialist_base.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `10 passed`, then `266 passed, 37 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

Temporarily change `if not grounding.ok:` to `if False:` in `base.py` and run `.venv/Scripts/python -m pytest tests/test_specialist_base.py -q`: expect `test_ungrounded_figure_is_rejected_with_feedback_then_accepted` to FAIL. Undo it. Then change `if coverage is Coverage.INSUFFICIENT:` to `if False:` and run the same file: expect `test_missing_critical_input_abstains_without_calling_the_model` to FAIL. Undo it and confirm `git diff` is empty for `base.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/agents tests/test_specialist_base.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add shared specialist base class with coverage, grounding and abstention"
git push
```

---

### Task 4: Quant/Technical specialist

**Files:**
- Create: `src/athena/agents/quant_technical.py`
- Test: `tests/test_quant_technical.py`

**Interfaces:**
- Consumes: `SpecialistSpec`, `Specialist` (Task 3); `build_technical_packet` (Task 2); `check_abstention(fn, full_inputs, critical, optional) -> list[str]` from `athena.evaluation.checks` (Phase 0e).
- Produces (`athena.agents.quant_technical`): `PERSONA: str`; `QUANT_TECHNICAL: SpecialistSpec` with `critical = (last_close, trend_daily, rsi_14, bollinger_pct_b, atr_14)` and `optional = (trend_weekly, trend_monthly, trend_alignment, volume_ratio_20_50, reward_risk, sma_200)`.

- [ ] **Step 1: Write the failing tests `tests/test_quant_technical.py`**

```python
import json
from datetime import datetime, timedelta, timezone

from athena.agents.base import Specialist
from athena.agents.quant_technical import PERSONA, QUANT_TECHNICAL
from athena.contracts import Bar
from athena.evaluation.checks import check_abstention
from athena.technicals.packet import build_technical_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def bars_for(closes):
    days, day = [], datetime(2026, 10, 2).date()
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar("X", datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1), c, c + 1, c - 1, c, 1000.0, NOW, "t")
        for d, c in zip(reversed(days), closes)
    ]


UP_PACKET = build_technical_packet("X", NOW, bars_for([100.0 + i * 0.5 for i in range(400)]))


class Narrator:
    """A fake model that reads the packet it is given and cites real figures from it."""

    def complete(self, system, user):
        data = json.loads(user[user.index("{") : user.rindex("}") + 1])
        metrics = data["metrics"]
        parts = [f"Daily trend {metrics['trend_daily']['value']}", f"RSI {metrics['rsi_14']['value']}"]
        if "trend_alignment" in metrics:
            parts.append(f"alignment {metrics['trend_alignment']['value']}")
        else:
            parts.append("timeframe alignment is unavailable so confidence is limited")
        return json.dumps({"signal": "bullish", "confidence": 65, "reasoning": ". ".join(parts) + "."})


def test_persona_encodes_the_trd_rules():
    for rule in ("aligned", "Bollinger", "Volume confirmation", "at least 2", "never as a price target"):
        assert rule in PERSONA


def test_full_data_gives_full_coverage_and_a_grounded_view():
    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(UP_PACKET)
    assert out["data_coverage"] == "full" and out["missing"] == []
    assert "alignment aligned_up" in out["reasoning"]


def test_short_history_gives_partial_coverage_naming_the_gaps():
    packet = build_technical_packet("X", NOW, bars_for([100.0 + i * 0.5 for i in range(100)]))
    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(packet)
    assert out["data_coverage"] == "partial"
    assert {"trend_monthly", "trend_alignment", "sma_200"} <= set(out["missing"])


def test_too_little_history_abstains():
    packet = build_technical_packet("X", NOW, bars_for([100.0 + i for i in range(10)]))
    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(packet)
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")


def test_abstention_harness_passes_for_the_real_specialist():
    specialist = Specialist(QUANT_TECHNICAL, Narrator(), fail_quiet=True)

    def run(metrics):
        return specialist.analyze({**UP_PACKET, "metrics": metrics})

    failures = check_abstention(
        run, UP_PACKET["metrics"], critical=list(QUANT_TECHNICAL.critical), optional=list(QUANT_TECHNICAL.optional)
    )
    assert failures == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_quant_technical.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.agents.quant_technical'`.

- [ ] **Step 3: Write `src/athena/agents/quant_technical.py`**

```python
from __future__ import annotations

from athena.agents.base import SpecialistSpec

PERSONA = """You are a systematic technical analyst. You judge only what price and volume show, never the business.
Interpret the figures in the metrics packet; do not calculate your own. Work the checklist:
1. Multi-timeframe trend: a bullish or bearish call requires the daily, weekly and monthly trends to be
   aligned (trend_alignment is aligned_up or aligned_down). If they disagree, or a timeframe is missing,
   the signal is neutral or low confidence.
2. Bollinger Bands: bollinger_pct_b near or above 1 means stretched high, near or below 0 stretched low;
   bollinger_bandwidth shows squeeze versus expansion.
3. Volume confirmation: a move is confirmed only if the volume ratio is above 1; otherwise say it is unconfirmed.
4. Risk and reward: do not call bullish unless reward_risk is at least 2.
5. RSI extremes are a caution on their own, not a trigger.
Reason ONLY from the data provided and treat as_of as the present day. If a figure is listed as missing,
say so and qualify your conclusion. Express forward-looking views as a base case with its main risk,
never as a price target."""

QUANT_TECHNICAL = SpecialistSpec(
    name="Quant/Technical",
    persona=PERSONA,
    critical=("last_close", "trend_daily", "rsi_14", "bollinger_pct_b", "atr_14"),
    optional=("trend_weekly", "trend_monthly", "trend_alignment", "volume_ratio_20_50", "reward_risk", "sma_200"),
)
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_quant_technical.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `5 passed`, then `271 passed, 37 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/agents/quant_technical.py tests/test_quant_technical.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add Quant/Technical specialist persona and spec"
git push
```

---

### Task 5: Live check, TRD update and graph refresh

**Files:**
- Create: `tests/live/test_live_technicals.py`
- Modify: `TRD.md`

- [ ] **Step 1: Write `tests/live/test_live_technicals.py`**

```python
import json
from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live

LOOKBACK_DAYS = 760  # about 25 months, enough for the monthly trend


class Narrator:
    """A scripted model that cites real figures from whatever packet it receives."""

    def complete(self, system, user):
        data = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        text = f"Last close {data['last_close']['value']} with RSI {data['rsi_14']['value']} and daily trend {data['trend_daily']['value']}."
        return json.dumps({"signal": "neutral", "confidence": 50, "reasoning": text})


@pytest.mark.parametrize("symbol", ["SBIN", "NIFTYBEES"])
def test_live_technical_packet_is_complete_and_the_specialist_accepts_it(symbol):
    since = ist_date(utc_now()) - timedelta(days=LOOKBACK_DAYS)
    bars = JugaadPriceAdapter().fetch_ohlcv(symbol, "1d", since=since)
    packet = build_technical_packet(symbol, utc_now(), bars)
    print("\n" + json.dumps({k: v["value"] for k, v in packet["metrics"].items()}, indent=1), packet["missing_reasons"])

    assert packet["missing"] == [], packet["missing_reasons"]
    assert 0 <= packet["metrics"]["rsi_14"]["value"] <= 100
    assert packet["metrics"]["trend_alignment"]["value"] in ("aligned_up", "aligned_down", "not_aligned")
    assert packet["metrics"]["volume_ratio_20_50"]["value"] > 0

    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(packet)
    assert out["data_coverage"] == "full" and out["missing"] == []
```

- [ ] **Step 2: Run the default and live suites**

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest --live tests/live/test_live_technicals.py -q -s
```

Expected: `271 passed, 39 skipped`, then `2 passed` with each packet printed. Figures differ by day; the assertions are on completeness and ranges only. If `missing` is not empty, read `missing_reasons` before changing anything (the usual cause is fewer than ~320 trading days returned).

- [ ] **Step 3: Update `TRD.md`**

1. At the end of the §2.2 paragraph, append: ` *Implemented in Plan 1a:* coverage derived from the declared critical and optional metric names, abstention without a model call, JSON parsing, contract validation, number grounding, one retry with feedback, response caching, and fail-loud (default) or fail-quiet handling, behind an \`LLMClient\` protocol. Not yet implemented: \`blind\` mode (arrives with the backtest) and a real provider (Plan 1b).`
2. At the end of the §2.3 paragraph, append: ` *Quant/Technical implemented in Plan 1a:* ta-lib indicators and multi-timeframe trend alignment are computed in \`technicals/packet.py\`; the specialist is a persona plus a critical/optional metric list in \`agents/quant_technical.py\`. Valuation, Moat & Quality and Earnings Intelligence are not yet built.`
3. In the revision history, add after the Phase 0e line: `- **Oct 5, 2026 (Phase 1a)** — Specialist base class and Quant/Technical specialist implemented (see docs/superpowers/plans/2026-10-05-phase-1a-specialist-base-and-quant-technical.md).`

- [ ] **Step 4: Commit, push, refresh graph**

```bash
git add tests/live/test_live_technicals.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live technical check; document specialist base class in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.2 -> task):** persona-as-prompt with shared plumbing (Tasks 3-4); JSON parsing against the schema (Task 3); abstention path (Task 3, exercised by the Phase 0e harness in Task 4); coverage derived from the declared required-inputs list, never from the model (Task 3); number-grounding before acceptance (Task 3); fail-loud vs fail-quiet (Task 3); response caching (Task 3). **§2.3 Quant/Technical:** multi-timeframe alignment, Bollinger Bands, volume confirmation, 2:1 reward:risk (Tasks 2 and 4, persona rules asserted in a test). Not in this plan: `blind` mode, a real LLM provider, the orchestrator, the other equity specialists, the dashboard and the backtest, all stated above or in the series line.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `Candle`, `candles_from_bars`, `resample`, `build_technical_packet`, `SpecialistSpec`, `Specialist.analyze`, `abstention`, `parse_model_json` and the metric names match across Tasks 1-5. Test totals: 244 + 3 + 9 + 10 + 5 = 271 passed; skipped 37 + 2 live = 39.
