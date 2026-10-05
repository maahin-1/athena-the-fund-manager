# Phase 1d — Stocks and ETF Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local, interactive dashboard (`python -m athena.dashboard`) where you type a stock or ETF and see everything Athena tracks for it: the verdict and each specialist's view, a candlestick chart with moving averages, Bollinger Bands and volume, an RSI chart, the technical-indicator table, and the risk and benchmark table, every table showing its own as-of date, source and coverage label (PRD FR-6, TRD §2.9).

**Architecture:** The page is a thin Streamlit script over a plain-data `DashboardView`. Everything with logic is a pure function and is tested offline: `indicator_series` (full ta-lib history for charting, from the same calls as the technical packet), the Plotly figure builders, `risk_packet` (the Phase 0d metrics packet wired to NIFTY 50, the overnight-rate index and the NIFTY 50 TRI), and `build_view`. `DashboardService` calls the Plan 1c orchestrator, shares one price download with the charts through a per-request cache, and returns the view. Streamlit's `AppTest` drives the real page against a fake service, so the page is tested without a browser or network.

**Tech Stack:** Python >= 3.11, Plotly 7.1, Streamlit 1.65 (both new dependencies; the versions tested), ta-lib, pytest.

**Spec:** `TRD.md` §2.9 (visualization), §2.13 (metrics engine); `PRD.md` FR-6 ("interactive and re-queryable per instrument, not a static image").

**Plan series:** 0a-0e, 1a-1c (done) -> **1d (this plan)** -> 1e (remaining equity specialists in ratio-only mode) -> 1f (stock backtest) -> later phases (debt, mutual funds, options panel, portfolio view, live feed, brokers).

**Suggested models:** Sonnet at medium effort, inline execution (six small sequential tasks). Prototyped end to end in a scratch copy first: 385 offline tests passed, three deliberate mutations were each caught, four live tests passed against real data and models, and the real Streamlit server was started, answered its health check and was stopped.

## Verified findings (5 Oct 2026)

- **It works on real data.** Live runs for `SBIN`, `NIFTYBEES` and the ambiguous `SBI`: the stock view has a verdict, both charts, a technical panel with full coverage and a risk panel with volatility, drawdown, beta, alpha and Sharpe; the ETF view adds tracking error and difference against the NIFTY 50 TRI; the ambiguous name shows candidates and no charts; the whole page renders under `AppTest` with the live service.
- **A real bug the live run exposed.** The metric tables mix numbers and text labels ("down", "aligned_up") in one column, and the browser serialisation (Arrow) rejects a mixed column; Streamlit catches it, logs a long traceback and falls back to text. The harness could not see this: `AppTest` does not serialise. The fix shows every value as text, and the test asserts the column is all text (it fails without the fix; an earlier version of the test, which passed without the fix, was discarded).
- **Real server smoke test.** `python -m athena.dashboard --server.headless true --server.port 8599` started, `/_stcore/health` returned 200, and the process was stopped with nothing left running.
- **Honest limits, all visible on the page or in this plan:**
  - Data is end of day: the as-of label reads "(last close)". There is no live feed (free sources have none; Zerodha live data is a paid plan, still undecided).
  - The beta and alpha benchmark is always the NIFTY 50, which is meaningless for a gold or sector ETF; the metric's `inputs` list says which index was used.
  - Tracking metrics exist only for ETFs in `ETF_TRACKING_INDEX`, which today holds only `NIFTYBEES` (checked against the NIFTY 50 TRI). Any other ETF gets a note saying no tracking index is mapped.
  - The market series for the risk panel are loaded once when the service is built; restart the dashboard on a new day.
  - "Interactive" means Plotly zoom, pan and hover plus a text box that re-queries; there is no watchlist, no portfolio view, no options panel and no mutual funds (on hold).
  - One run takes about 35 s the first time (loading the NSE lists and market series), then a few seconds per query, mostly the model call.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and use the keys in `.env` (read by the code at runtime; the assistant cannot read `.env`).
- Compute in code, show in the page: every number on the page comes from a packet or a series computed in code; the page never calculates or rounds metrics except text formatting.
- Every panel shows its as-of, source and coverage label (TRD §2.9). Missing figures are listed with the reason, never dropped.
- The disclaimer "A stylized analytical framework, not financial advice; not a registered investment adviser." appears on every analysis page.
- Errors that are `AthenaError` or `ValueError` are shown in the page as an error message, not as a traceback.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | add `plotly` and `streamlit` dependencies |
| `src/athena/technicals/series.py` | `IndicatorSeries`, `indicator_series` |
| `src/athena/dashboard/charts.py` | `price_chart`, `rsi_chart` (Plotly figures) |
| `src/athena/dashboard/risk.py` | `RiskWorld`, `refresh_risk_data`, `risk_world_from_store`, `risk_packet`, `ETF_TRACKING_INDEX` |
| `src/athena/dashboard/view.py` | `DashboardView`, `Panel`, `MetricRow`, `SpecialistRow`, `CandidateRow`, `build_view` |
| `src/athena/dashboard/service.py` | `RequestCache`, `DashboardService`, `live_service` |
| `src/athena/cli.py` | modified: `LiveSources`, `live_sources`, `fetch_bars` option on `build_orchestrator` |
| `src/athena/dashboard/app.py` | `render`, `run`, the Streamlit script |
| `src/athena/dashboard/__main__.py` | `python -m athena.dashboard` launcher |
| `tests/bar_factory.py`, `tests/dash_fakes.py` | shared synthetic bars; shared canned results, views and a fake service |
| `tests/test_technicals_series.py`, `test_dashboard_charts.py`, `test_dashboard_risk.py`, `test_dashboard_view.py`, `test_dashboard_service.py`, `test_dashboard_app.py`, `tests/live/test_live_dashboard.py` | one test module per source module, plus the live check |

---

### Task 1: Indicator series for charting

**Files:**
- Create: `tests/bar_factory.py`, `src/athena/technicals/series.py`
- Test: `tests/test_technicals_series.py`

**Interfaces:**
- Consumes: `Candle`, `candles_from_bars` (Plan 1a); `BOLLINGER_PERIOD`, `RSI_PERIOD`, `build_technical_packet` (Plan 1a, tests).
- Produces (`athena.technicals.series`): `@dataclass(frozen=True) IndicatorSeries(days, sma_50, sma_200, bb_upper, bb_middle, bb_lower, rsi_14)` where `days` is a `tuple[date, ...]` and every other field is a `tuple[float | None, ...]` the same length, `None` while the indicator warms up; `indicator_series(candles) -> IndicatorSeries`.
- Produces (`tests/bar_factory.py`): `NOW`; `make_bars(closes, end=date(2026, 10, 2), volume=1000.0, symbol="X") -> list[Bar]` (one weekday bar per close, oldest first, stamped like jugaad-data's IST midnight).

- [ ] **Step 1: Create the shared test helper and write the failing tests**

`tests/bar_factory.py`:

```python
from datetime import date, datetime, timedelta, timezone

from athena.contracts import Bar

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def make_bars(closes, end=date(2026, 10, 2), volume=1000.0, symbol="X"):
    """One bar per weekday ending on `end`, one per close, oldest first; stamped the way jugaad-data stamps IST midnight."""
    days, day = [], end
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar(symbol, datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1), c, c + 1, c - 1, c, volume, NOW, "t")
        for d, c in zip(reversed(days), closes)
    ]
```

`tests/test_technicals_series.py`:

```python

import pytest
from bar_factory import NOW, make_bars

from athena.technicals.candles import candles_from_bars
from athena.technicals.packet import build_technical_packet
from athena.technicals.series import indicator_series

CLOSES = [100.0 + i * 0.5 + (3 if i % 7 == 0 else 0) for i in range(300)]
BARS = make_bars(CLOSES)
CANDLES = candles_from_bars(BARS)
SERIES = indicator_series(CANDLES)


def test_every_series_is_aligned_with_the_candles():
    assert SERIES.days == tuple(c.day for c in CANDLES)
    for name in ("sma_50", "sma_200", "bb_upper", "bb_middle", "bb_lower", "rsi_14"):
        assert len(getattr(SERIES, name)) == len(CANDLES), name


def test_warmup_values_are_none_and_the_rest_are_floats():
    assert SERIES.sma_50[:49] == (None,) * 49 and SERIES.sma_50[49] is not None
    assert SERIES.sma_200[:199] == (None,) * 199 and SERIES.sma_200[199] is not None
    assert SERIES.bb_upper[:19] == (None,) * 19 and SERIES.bb_upper[19] is not None
    assert SERIES.rsi_14[:14] == (None,) * 14 and SERIES.rsi_14[14] is not None
    assert all(isinstance(v, float) for v in SERIES.sma_50[49:])


def test_bands_straddle_the_middle_band():
    for upper, middle, lower in zip(SERIES.bb_upper, SERIES.bb_middle, SERIES.bb_lower):
        if upper is not None:
            assert lower < middle < upper


def test_the_last_values_match_what_the_technical_packet_reports():
    metrics = build_technical_packet("X", NOW, BARS)["metrics"]
    assert SERIES.sma_50[-1] == pytest.approx(metrics["sma_50"]["value"], abs=1e-3)
    assert SERIES.sma_200[-1] == pytest.approx(metrics["sma_200"]["value"], abs=1e-3)
    assert SERIES.rsi_14[-1] == pytest.approx(metrics["rsi_14"]["value"], abs=1e-3)
    pct_b = (CLOSES[-1] - SERIES.bb_lower[-1]) / (SERIES.bb_upper[-1] - SERIES.bb_lower[-1])
    assert pct_b == pytest.approx(metrics["bollinger_pct_b"]["value"], abs=1e-3)


def test_a_short_history_is_all_none_not_an_error():
    short = indicator_series(candles_from_bars(make_bars(CLOSES[:10])))
    assert len(short.days) == 10 and set(short.sma_50) == {None} and set(short.rsi_14[:14]) == {None}
    assert indicator_series([]).days == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_series.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.technicals.series'`.

- [ ] **Step 3: Write `src/athena/technicals/series.py`**

```python
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import talib

from athena.technicals.candles import Candle
from athena.technicals.packet import BOLLINGER_PERIOD, RSI_PERIOD


@dataclass(frozen=True)
class IndicatorSeries:
    """Indicator values aligned one-to-one with the candles; `None` where the indicator is still warming up."""

    days: tuple[date, ...]
    sma_50: tuple[float | None, ...]
    sma_200: tuple[float | None, ...]
    bb_upper: tuple[float | None, ...]
    bb_middle: tuple[float | None, ...]
    bb_lower: tuple[float | None, ...]
    rsi_14: tuple[float | None, ...]


def _clean(values: np.ndarray) -> tuple[float | None, ...]:
    return tuple(None if math.isnan(value) else float(value) for value in values)


def indicator_series(candles: Sequence[Candle]) -> IndicatorSeries:
    """Full indicator history for charting, from the same ta-lib calls and periods the technical packet uses."""
    close = np.array([c.close for c in candles], dtype=float)
    upper, middle, lower = talib.BBANDS(close, BOLLINGER_PERIOD, 2, 2)
    return IndicatorSeries(
        days=tuple(c.day for c in candles),
        sma_50=_clean(talib.SMA(close, 50)),
        sma_200=_clean(talib.SMA(close, 200)),
        bb_upper=_clean(upper),
        bb_middle=_clean(middle),
        bb_lower=_clean(lower),
        rsi_14=_clean(talib.RSI(close, RSI_PERIOD)),
    )
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_technicals_series.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `5 passed`, then `352 passed, 44 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add tests/bar_factory.py src/athena/technicals/series.py tests/test_technicals_series.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add indicator series for charting"
git push
```

---

### Task 2: Plotly charts and the new dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `src/athena/dashboard/__init__.py` (empty file), `src/athena/dashboard/charts.py`
- Test: `tests/test_dashboard_charts.py`

**Interfaces:**
- Consumes: `Candle`, `IndicatorSeries` (Task 1).
- Produces (`athena.dashboard.charts`): `UP`, `DOWN`, `RSI_OVERBOUGHT = 70`, `RSI_OVERSOLD = 30`; `price_chart(candles, series, title) -> plotly.graph_objects.Figure` with traces named `Price` (candlestick), `SMA 50`, `SMA 200`, `Bollinger upper`, `Bollinger lower`, `Volume` (bars coloured by candle direction, in a second row); `rsi_chart(candles, series, title="RSI 14") -> Figure` (one trace, reference lines at 30 and 70, y-axis 0-100). Both raise `ValueError` for no candles or a series not aligned with the candles.

- [ ] **Step 1: Add the dependencies and install them**

In `pyproject.toml`, change the `dependencies` line to:

```toml
dependencies = ["duckdb>=1.0", "requests>=2.31", "jugaad-data>=0.35", "yfinance>=1.0", "numpy>=1.26", "TA-Lib>=0.6", "plotly>=7.1", "streamlit>=1.65"]
```

Run: `.venv/Scripts/python -m pip install -e ".[dev]" && .venv/Scripts/python -c "import plotly, streamlit; print(plotly.__version__, streamlit.__version__)"`
Expected: prints two versions (7.1 or later, 1.65 or later). The `width="stretch"` option used in Task 6 needs this Streamlit version.

- [ ] **Step 2: Write the failing tests `tests/test_dashboard_charts.py`**

```python
import plotly.graph_objects as go
import pytest

from athena.dashboard.charts import RSI_OVERBOUGHT, RSI_OVERSOLD, price_chart, rsi_chart
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
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_charts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.dashboard'`.

- [ ] **Step 4: Create the empty `src/athena/dashboard/__init__.py` and write `src/athena/dashboard/charts.py`**

```python
from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

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
```

- [ ] **Step 5: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_charts.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `5 passed`, then `357 passed, 44 skipped`.

- [ ] **Step 6: Commit and push**

```bash
git add pyproject.toml src/athena/dashboard tests/test_dashboard_charts.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add plotly price and RSI charts; add plotly and streamlit dependencies"
git push
```

---

### Task 3: Risk metrics wiring

**Files:**
- Create: `src/athena/dashboard/risk.py`
- Test: `tests/test_dashboard_risk.py`

**Interfaces:**
- Consumes: `IndexCloseLoader`, `IndexTriLoader`, their `default_fetch` functions and dataset constants (Phase 0d); `build_packet`, `series_from_bars`, `series_from_store`, `Series` (Phase 0d); `Resolution` (Phase 0c); `DataStore`; `make_bars` (Task 1, tests).
- Produces (`athena.dashboard.risk`): `ETF_TRACKING_INDEX = {"NIFTYBEES": "NIFTY 50"}`; `BENCHMARK_NAME = "NIFTY 50"`; `@dataclass(frozen=True) RiskWorld(price_index, rate, tri)` (each a `Series`); `refresh_risk_data(store, since, price_fetch=..., rate_fetch=..., tri_fetch=..., clock=utc_now) -> None` (the three fetchers default to the real jugaad-data ones and take `(index_name, start, end)`); `risk_world_from_store(store) -> RiskWorld`; `risk_packet(resolution, bars, world, now) -> dict` (the metrics packet; tracking metrics only for an ETF in the map).

- [ ] **Step 1: Write the failing tests `tests/test_dashboard_risk.py`**

```python
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from bar_factory import make_bars

from athena.dashboard.risk import ETF_TRACKING_INDEX, refresh_risk_data, risk_packet, risk_world_from_store
from athena.resolver import Resolution
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
START = date(2025, 6, 2)


def weekdays(start, end):
    day, days = start, []
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = weekdays(START, date(2026, 10, 2))


def levels(seed, drift, vol, base=1000.0):
    rng, level, out = random.Random(seed), base, []
    for _ in DAYS:
        level *= 1 + rng.gauss(drift, vol)
        out.append(level)
    return out


NIFTY = levels(1, 0.0004, 0.008)
RATE = [1000 * (1 + 0.00014) ** i for i in range(len(DAYS))]
TRI = [v * 1.01 for v in NIFTY]


def close_rows(values):
    return [{"HistoricalDate": d.strftime("%d %b %Y"), "CLOSE": str(v)} for d, v in zip(DAYS, values)]


def tri_rows():
    return [{"Date": d.strftime("%d %b %Y"), "TotalReturnsIndex": str(v), "NTR_Value": str(v)} for d, v in zip(DAYS, TRI)]


def populated_store():
    store = DataStore()
    refresh_risk_data(
        store, START,
        price_fetch=lambda name, s, e: close_rows(NIFTY),
        rate_fetch=lambda name, s, e: close_rows(RATE),
        tri_fetch=lambda name, s, e: tri_rows(),
        clock=lambda: NOW,
    )
    return store


WORLD = risk_world_from_store(populated_store())


def resolution(symbol, asset_class):
    return Resolution(asset_class, "ticker", symbol, symbol, "", "exact", 1.0, (), ())


def asset_bars(symbol, values):
    # market-priced asset that follows NIFTY with some noise
    return make_bars([v / 10 for v in values], end=date(2026, 10, 2), symbol=symbol)


def test_refresh_then_read_gives_aligned_series_for_all_three_datasets():
    assert len(WORLD.price_index) == len(DAYS) and len(WORLD.rate) == len(DAYS) and len(WORLD.tri) == len(DAYS)
    assert WORLD.price_index[DAYS[10]] == pytest.approx(NIFTY[10])
    assert WORLD.tri[DAYS[10]] == pytest.approx(TRI[10])


def test_a_stock_gets_volatility_drawdown_beta_alpha_and_sharpe_but_no_tracking_metrics():
    rng = random.Random(7)
    asset = [n * (1 + rng.gauss(0, 0.004)) for n in NIFTY]
    packet = risk_packet(resolution("SBIN", "equity"), asset_bars("SBIN", asset), WORLD, NOW)
    assert {"volatility_annualized", "max_drawdown", "beta", "alpha_annualized", "sharpe"} <= set(packet["metrics"])
    assert {"tracking_error", "tracking_difference"} <= set(packet["missing"])
    assert packet["metrics"]["beta"]["inputs"] == ["asset", "NIFTY 50"]
    assert 0.7 < packet["metrics"]["beta"]["value"] < 1.3  # it follows the index


def test_a_mapped_etf_also_gets_tracking_error_and_difference_against_the_tri():
    assert "NIFTYBEES" in ETF_TRACKING_INDEX
    packet = risk_packet(resolution("NIFTYBEES", "etf"), asset_bars("NIFTYBEES", TRI), WORLD, NOW)
    assert {"tracking_error", "tracking_difference"} <= set(packet["metrics"])
    assert packet["metrics"]["tracking_error"]["inputs"] == ["asset", "NIFTY 50 TRI"]
    assert packet["metrics"]["tracking_error"]["value"] < 0.001  # the asset is the index


def test_an_etf_with_no_known_index_gets_no_tracking_metrics():
    packet = risk_packet(resolution("GOLDBEES", "etf"), asset_bars("GOLDBEES", NIFTY), WORLD, NOW)
    assert "tracking_error" in packet["missing"] and "tracking_difference" in packet["missing"]


def test_a_stock_with_a_symbol_in_the_etf_map_is_not_treated_as_tracking():
    packet = risk_packet(resolution("NIFTYBEES", "equity"), asset_bars("NIFTYBEES", NIFTY), WORLD, NOW)
    assert "tracking_error" in packet["missing"]


def test_an_empty_store_gives_a_packet_with_everything_missing_not_a_crash():
    empty = risk_world_from_store(DataStore())
    packet = risk_packet(resolution("SBIN", "equity"), asset_bars("SBIN", NIFTY), empty, NOW)
    assert packet["metrics"].keys() <= {"volatility_annualized", "max_drawdown"}
    assert {"beta", "alpha_annualized", "sharpe"} <= set(packet["missing"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_risk.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.dashboard.risk'`.

- [ ] **Step 3: Write `src/athena/dashboard/risk.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import Bar
from athena.loaders import index_close, index_tri
from athena.loaders.index_close import PRICE_DATASET, PRICE_INDEX, RATE_DATASET, RATE_INDEX, IndexCloseLoader
from athena.loaders.index_tri import DATASET as TRI_DATASET
from athena.loaders.index_tri import IndexTriLoader
from athena.metrics.packets import build_packet
from athena.metrics.series import Series, series_from_bars, series_from_store
from athena.resolver import Resolution
from athena.store import DataStore

# ETFs whose tracked index is known and has a total-return series loaded. Only NIFTYBEES has been checked
# against the NIFTY 50 TRI (5 Oct 2026); an ETF not listed gets no tracking metrics rather than a guess.
ETF_TRACKING_INDEX = {"NIFTYBEES": "NIFTY 50"}
BENCHMARK_NAME = PRICE_INDEX


@dataclass(frozen=True)
class RiskWorld:
    """The market series every risk metric is measured against."""

    price_index: Series  # NIFTY 50 close
    rate: Series  # Nifty 1D Rate Index level (the risk-free accrual)
    tri: Series  # NIFTY 50 total-return index


def refresh_risk_data(
    store: DataStore,
    since: date,
    price_fetch: Callable[[str, date, date], list[dict]] = index_close.default_fetch,
    rate_fetch: Callable[[str, date, date], list[dict]] = index_close.default_fetch,
    tri_fetch: Callable[[str, date, date], list[dict]] = index_tri.default_fetch,
    clock: Callable[[], datetime] = utc_now,
) -> None:
    """Pull the NIFTY 50 close, the overnight-rate index and the NIFTY 50 TRI into the store since `since`."""
    IndexCloseLoader(store, fetch=price_fetch, clock=clock).refresh(since=since)
    IndexCloseLoader(store, dataset=RATE_DATASET, default_index=RATE_INDEX, fetch=rate_fetch, clock=clock).refresh(since=since)
    IndexTriLoader(store, fetch=tri_fetch, clock=clock).refresh(since=since)


def risk_world_from_store(store: DataStore) -> RiskWorld:
    return RiskWorld(
        price_index=series_from_store(store, PRICE_DATASET, PRICE_INDEX, "close"),
        rate=series_from_store(store, RATE_DATASET, RATE_INDEX, "close"),
        tri=series_from_store(store, TRI_DATASET, "NIFTY 50", "tri"),
    )


def risk_packet(resolution: Resolution, bars: Sequence[Bar], world: RiskWorld, now: datetime) -> dict[str, Any]:
    """The Phase 0d metrics packet for one instrument: volatility, drawdown, beta, alpha and Sharpe against the
    NIFTY 50 and the overnight rate, plus tracking error and difference for an ETF with a known index."""
    tracked = resolution.asset_class == "etf" and resolution.identifier in ETF_TRACKING_INDEX
    return build_packet(
        resolution.identifier,
        now,
        series_from_bars(bars),
        benchmark=world.price_index,
        benchmark_name=BENCHMARK_NAME,
        riskfree=world.rate,
        tracking_index=world.tri if tracked else None,
        tracking_index_name=f"{ETF_TRACKING_INDEX[resolution.identifier]} TRI" if tracked else None,
    )
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_risk.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `6 passed`, then `363 passed, 44 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `risk.py`, replace the `tracked = ...` line with `tracked = resolution.asset_class == "etf"` and run `.venv/Scripts/python -m pytest tests/test_dashboard_risk.py -q`: expect `test_an_etf_with_no_known_index_gets_no_tracking_metrics` to FAIL. Undo it and confirm `git diff` is empty for `risk.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/dashboard/risk.py tests/test_dashboard_risk.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: wire risk metrics to NIFTY 50, the overnight rate and the NIFTY 50 TRI"
git push
```

---

### Task 4: View model

**Files:**
- Create: `src/athena/dashboard/view.py`, `tests/dash_fakes.py`
- Test: `tests/test_dashboard_view.py`

**Interfaces:**
- Consumes: `OrchestrationResult`, `NEEDS_CLARIFICATION` (Plan 1c); `price_chart`, `rsi_chart` (Task 2); `indicator_series` (Task 1); `candles_from_bars` (Plan 1a); a technical packet and a risk packet (metrics-packet shape: `metrics`, `missing`, `missing_reasons`).
- Produces (`athena.dashboard.view`): `UNMAPPED_ETF_NOTE`, `NO_DATA`; frozen dataclasses `MetricRow(name, value, unit, window, note)`, `Panel(title, as_of, source, coverage, rows, missing)`, `SpecialistRow(name, signal, confidence, coverage, reasoning, missing)`, `CandidateRow(identifier, name, asset_class, score)`, `DashboardView(status, query, identifier, name, asset_class, match, verdict, specialists, skipped, panels, price_figure, rsi_figure, candidates, notes)`; `build_view(result, bars=(), technical=None, risk=None) -> DashboardView`. A panel's coverage is `insufficient` with no metrics, `partial` with any missing, else `full`; its as-of reads `"<last candle date> (last close)"` or `"no data"`. An ambiguous result gives candidates and nothing else.
- Produces (`tests/dash_fakes.py`): `NOW`, `BARS`, `TECHNICAL`, `RISK`, `SPECIALIST`, `VERDICT`, `resolution(...)`, `ok_result(...)`, `ambiguous_result()`, `full_view(...)`, `FakeService(view=None, error=None)` (records `queries`), `CURRENT` (a dict holding the service the AppTest script uses), `athena_error(message)`.

- [ ] **Step 1: Create the shared fixtures and write the failing tests**

`tests/dash_fakes.py`:

```python
"""Shared fixtures for the dashboard tests: a realistic result, view and service built from synthetic data."""
from datetime import datetime, timezone

from bar_factory import make_bars

from athena.contracts import AthenaError
from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, Resolution
from athena.technicals.packet import build_technical_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
CLOSES = [100.0 + i * 0.5 for i in range(320)]
BARS = make_bars(CLOSES, symbol="SBIN")
TECHNICAL = build_technical_packet("SBIN", NOW, BARS)
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


def full_view(asset_class="equity", status=OK, risk=RISK):
    return build_view(ok_result(asset_class, status), BARS, TECHNICAL, risk)


class FakeService:
    """Stands in for DashboardService: returns canned views, raises canned errors, and records the queries."""

    def __init__(self, view=None, error=None):
        self.canned, self.error, self.queries = view, error, []

    def view(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.canned


CURRENT = {"service": FakeService(full_view())}


def athena_error(message="no LLM provider key is set"):
    return AthenaError(message)
```

`tests/test_dashboard_view.py`:

```python
from dash_fakes import BARS, RISK, TECHNICAL, ambiguous_result, full_view, ok_result

from athena.dashboard.view import UNMAPPED_ETF_NOTE, build_view
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW


def test_the_view_carries_the_instrument_verdict_and_specialist_rows():
    view = full_view()
    assert (view.identifier, view.name, view.asset_class, view.match) == ("SBIN", "State Bank of India", "equity", "exact")
    assert view.verdict["verdict"] == "Buy"
    row = view.specialists[0]
    assert (row.name, row.signal, row.confidence, row.coverage) == ("quant_technical", "bullish", 72, "full")
    assert view.skipped == {"valuation": "not built yet"} and "risk overlay missing" in view.notes


def test_each_panel_has_its_own_as_of_source_and_coverage_label():
    technical, risk = full_view().panels
    assert technical.title.startswith("Technical") and risk.title.startswith("Risk")
    assert technical.as_of.endswith("(last close)") and technical.as_of.startswith("2026-10-02")
    assert technical.source == "t" and risk.source.startswith("t, NIFTY 50")
    assert technical.coverage == "full" and risk.coverage == "partial"  # the risk packet is missing tracking_error
    assert risk.missing == {"tracking_error": "required series not provided"}


def test_panel_rows_copy_the_packet_figures_exactly():
    technical = full_view().panels[0]
    by_name = {row.name: row for row in technical.rows}
    assert by_name["last_close"].value == TECHNICAL["metrics"]["last_close"]["value"]
    assert by_name["trend_alignment"].value == "aligned_up"
    assert by_name["reward_risk"].note  # the notes travel with the numbers


def test_charts_are_built_from_the_same_bars():
    view = full_view()
    assert view.price_figure is not None and view.rsi_figure is not None
    assert len(view.price_figure.data[0].x) == len(BARS)
    assert "SBIN" in view.price_figure.layout.title.text and "2026-10-02" in view.price_figure.layout.title.text


def test_no_bars_means_no_charts_and_panels_that_say_no_data():
    view = build_view(ok_result(), (), {"metrics": {}, "missing": ["last_close"], "missing_reasons": {"last_close": "x"}}, None)
    assert view.price_figure is None and view.rsi_figure is None
    panel = view.panels[0]
    assert panel.as_of == "no data" and panel.coverage == "insufficient"


def test_an_etf_without_a_tracking_index_gets_an_explanatory_note():
    assert UNMAPPED_ETF_NOTE in full_view("etf").notes
    assert UNMAPPED_ETF_NOTE not in full_view("equity").notes
    mapped = {**RISK, "missing": [], "missing_reasons": {}}
    assert UNMAPPED_ETF_NOTE not in full_view("etf", risk=mapped).notes


def test_an_ambiguous_result_becomes_a_candidate_list_with_no_charts_or_panels():
    view = build_view(ambiguous_result())
    assert view.status == NEEDS_CLARIFICATION and view.identifier == ""
    assert [(c.identifier, c.asset_class) for c in view.candidates] == [("SBIN", "equity")]
    assert view.panels == () and view.price_figure is None and view.notes == ("several matches",)


def test_a_no_view_result_keeps_its_status():
    view = full_view(status=NO_VIEW)
    assert view.status == NO_VIEW
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.dashboard.view'`.

- [ ] **Step 3: Write `src/athena/dashboard/view.py`**

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import plotly.graph_objects as go

from athena.contracts import Bar
from athena.dashboard.charts import price_chart, rsi_chart
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OrchestrationResult
from athena.technicals.candles import candles_from_bars
from athena.technicals.series import indicator_series

NO_DATA = "no data"
UNMAPPED_ETF_NOTE = "tracking error and difference are unavailable: no tracking index is mapped for this ETF"


@dataclass(frozen=True)
class MetricRow:
    name: str
    value: str | float
    unit: str
    window: str
    note: str


@dataclass(frozen=True)
class Panel:
    """A block of figures with its own as-of date, source and coverage label (TRD 2.9)."""

    title: str
    as_of: str
    source: str
    coverage: str  # full | partial | insufficient
    rows: tuple[MetricRow, ...]
    missing: Mapping[str, str]  # metric -> why it could not be computed


@dataclass(frozen=True)
class SpecialistRow:
    name: str
    signal: str
    confidence: int
    coverage: str
    reasoning: str
    missing: tuple[str, ...]


@dataclass(frozen=True)
class CandidateRow:
    identifier: str
    name: str
    asset_class: str
    score: float


@dataclass(frozen=True)
class DashboardView:
    status: str
    query: str
    identifier: str
    name: str
    asset_class: str
    match: str  # how the input was resolved
    verdict: Mapping[str, Any] | None
    specialists: tuple[SpecialistRow, ...]
    skipped: Mapping[str, str]
    panels: tuple[Panel, ...]
    price_figure: go.Figure | None
    rsi_figure: go.Figure | None
    candidates: tuple[CandidateRow, ...]
    notes: tuple[str, ...]


def _coverage(packet: Mapping[str, Any]) -> str:
    if not packet["metrics"]:
        return "insufficient"
    return "partial" if packet["missing"] else "full"


def _panel(title: str, packet: Mapping[str, Any], as_of: str, source: str) -> Panel:
    rows = tuple(
        MetricRow(name, metric["value"], metric["unit"], metric.get("window") or "", metric.get("note", ""))
        for name, metric in packet["metrics"].items()
    )
    return Panel(title, as_of, source, _coverage(packet), rows, dict(packet.get("missing_reasons", {})))


def build_view(
    result: OrchestrationResult,
    bars: Sequence[Bar] = (),
    technical: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
) -> DashboardView:
    """Everything the page shows, as plain data: verdict, specialist views, panels with as-of and coverage, charts."""
    if result.status == NEEDS_CLARIFICATION and result.ambiguity:
        candidates = tuple(
            CandidateRow(c.identifier, c.name, c.asset_class, c.score) for c in result.ambiguity.candidates
        )
        return DashboardView(
            result.status, result.query, "", "", "", "", None, (), {}, (), None, None, candidates, (result.ambiguity.reason,)
        )

    resolution = result.resolution
    assert resolution is not None
    candles = candles_from_bars(bars)
    as_of = f"{candles[-1].day.isoformat()} (last close)" if candles else NO_DATA
    source = bars[-1].source if bars else NO_DATA

    panels: list[Panel] = []
    if technical is not None:
        panels.append(_panel("Technical indicators (ta-lib)", technical, as_of, source))
    notes = list(result.notes)
    if risk is not None:
        panels.append(_panel("Risk and benchmark metrics", risk, as_of, f"{source}, NIFTY 50, Nifty 1D Rate Index"))
        if resolution.asset_class == "etf" and "tracking_error" in risk["missing"]:
            notes.append(UNMAPPED_ETF_NOTE)

    price_figure = rsi_figure = None
    if candles:
        series = indicator_series(candles)
        label = f"{resolution.identifier}  {resolution.name}  ({as_of})"
        price_figure = price_chart(candles, series, label)
        rsi_figure = rsi_chart(candles, series)

    specialists = tuple(
        SpecialistRow(name, out["signal"], out["confidence"], out["data_coverage"], out["reasoning"], tuple(out["missing"]))
        for name, out in result.specialists.items()
    )
    return DashboardView(
        result.status, result.query, resolution.identifier, resolution.name, resolution.asset_class,
        resolution.resolution_path, result.verdict, specialists, dict(result.skipped), tuple(panels),
        price_figure, rsi_figure, (), tuple(notes),
    )
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_view.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `8 passed`, then `371 passed, 44 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `view.py`, replace `return "partial" if packet["missing"] else "full"` with `return "full"` and run `.venv/Scripts/python -m pytest tests/test_dashboard_view.py -q`: expect `test_each_panel_has_its_own_as_of_source_and_coverage_label` to FAIL. Undo it and confirm `git diff` is empty for `view.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/dashboard/view.py tests/dash_fakes.py tests/test_dashboard_view.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add dashboard view model with per-panel as-of and coverage"
git push
```

---

### Task 5: Service and shared sources

**Files:**
- Modify: `src/athena/cli.py` (replace the whole file with the version below; it adds `LiveSources`, `live_sources` and a `fetch_bars` option, and keeps `main` and `live_orchestrator` working)
- Create: `src/athena/dashboard/service.py`
- Test: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: Plan 1c's `Orchestrator`, `build_orchestrator`; `history_fetcher`, `HISTORY_DAYS`; `build_technical_packet`; `risk_packet`, `refresh_risk_data`, `risk_world_from_store`, `RiskWorld` (Task 3); `build_view`, `DashboardView` (Task 4); `ist_date`, `utc_now`.
- Produces (`athena.cli`): `LiveSources(resolver, llm_router, chain, store)`; `live_sources(env_file=DEFAULT_ENV_FILE) -> LiveSources`; `build_orchestrator(resolver, llm_router, chain, clock=utc_now, fetch_bars=None)` (a supplied `fetch_bars` replaces the default price fetcher); `live_orchestrator`, `main` unchanged in behaviour.
- Produces (`athena.dashboard.service`): `RequestCache(fetch)` (callable `symbol -> bars`, remembers each symbol until `clear()`); `DashboardService(orchestrator, bars: RequestCache, risk_world, clock=utc_now)` with `view(query) -> DashboardView` (clears the cache, runs the orchestrator, then builds the technical packet, the risk packet and the view from the same bars; an ambiguous query downloads nothing); `live_service(env_file=DEFAULT_ENV_FILE) -> DashboardService`.

- [ ] **Step 1: Write the failing tests `tests/test_dashboard_service.py`**

```python
from bar_factory import NOW, make_bars
from dash_fakes import ambiguous_result, resolution

from athena.dashboard.risk import RiskWorld
from athena.dashboard.service import DashboardService, RequestCache
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


def test_request_cache_downloads_each_symbol_once_until_cleared():
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    cache("SBIN"), cache("SBIN"), cache("TCS")
    assert fetch.symbols == ["SBIN", "TCS"]
    cache.clear()
    cache("SBIN")
    assert fetch.symbols == ["SBIN", "TCS", "SBIN"]


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_service.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.dashboard.service'`.

- [ ] **Step 3: Replace `src/athena/cli.py` and write `src/athena/dashboard/service.py`**

`src/athena/cli.py`:

```python
from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.fallback import FallbackChain
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.llm.router import Router, build_router
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.orchestrator.builders import history_fetcher, technical_packet_builder
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.orchestrator.report import format_result
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar


def build_orchestrator(
    resolver: InstrumentResolver,
    llm_router: Router,
    chain: FallbackChain,
    clock: Callable[[], datetime] = utc_now,
    fetch_bars: Callable[[str], list[Bar]] | None = None,
) -> Orchestrator:
    """Wire the specialists that exist (today only Quant/Technical) to their data and models.
    `fetch_bars` lets a caller share one price download with other consumers (the dashboard does)."""
    fetch = fetch_bars or history_fetcher(chain, clock=clock)
    specialists = {"quant_technical": Specialist(QUANT_TECHNICAL, llm_router.client_for("specialist"))}
    builders = {"quant_technical": technical_packet_builder(fetch, clock)}
    return Orchestrator(resolver, specialists, builders)


@dataclass(frozen=True)
class LiveSources:
    resolver: InstrumentResolver
    llm_router: Router
    chain: FallbackChain
    store: DataStore


def live_sources(env_file: Path | str = DEFAULT_ENV_FILE) -> LiveSources:
    """Everything wired to real sources: NSE master lists and holidays, jugaad-data then Yahoo for prices, and
    whichever LLM providers have keys. Loads into a fresh in-memory store on every call."""
    llm_router = build_router(env_file=env_file)
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    NseHolidayLoader(store).refresh()
    years = [int(record.key) for record in store.latest_records(HOLIDAY_DATASET)]
    calendar = load_calendar(store, years) if years else TradingCalendar(frozenset())
    resolver = InstrumentResolver(InstrumentIndex.from_store(store, calendar=calendar))
    chain = ohlcv_chain([JugaadPriceAdapter(), YahooPriceAdapter()], calendar)
    return LiveSources(resolver, llm_router, chain, store)


def live_orchestrator(env_file: Path | str = DEFAULT_ENV_FILE) -> Orchestrator:
    sources = live_sources(env_file)
    return build_orchestrator(sources.resolver, sources.llm_router, sources.chain)


def main(argv: list[str] | None = None, factory: Callable[..., Orchestrator] = live_orchestrator) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.cli", description="Analyze one instrument.")
    parser.add_argument("query", nargs="+", help="ticker, ISIN or name, for example SBIN")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args(argv)
    try:
        result = factory(env_file=args.env_file).analyze(" ".join(args.query))
    except (AthenaError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(format_result(result))
    return 2 if result.status == NEEDS_CLARIFICATION else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`src/athena/dashboard/service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import Bar
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.orchestrator.builders import HISTORY_DAYS, history_fetcher
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date


class RequestCache:
    """Remembers each symbol's bars for the duration of one request, so the specialists and the charts share one
    download. The service clears it at the start of every request."""

    def __init__(self, fetch: Callable[[str], list[Bar]]):
        self._fetch = fetch
        self._bars: dict[str, list[Bar]] = {}

    def __call__(self, symbol: str) -> list[Bar]:
        if symbol not in self._bars:
            self._bars[symbol] = self._fetch(symbol)
        return self._bars[symbol]

    def clear(self) -> None:
        self._bars.clear()


class DashboardService:
    """Turns a typed instrument into a `DashboardView`: the orchestrator's verdict plus the charts and metric
    panels that sit around it."""

    def __init__(
        self, orchestrator: Orchestrator, bars: RequestCache, risk_world: RiskWorld, clock: Callable[[], datetime] = utc_now
    ):
        self._orchestrator = orchestrator
        self._bars = bars
        self._risk_world = risk_world
        self._clock = clock

    def view(self, query: str) -> DashboardView:
        self._bars.clear()
        result = self._orchestrator.analyze(query)
        if result.status == NEEDS_CLARIFICATION:
            return build_view(result)
        assert result.resolution is not None
        bars = self._bars(result.resolution.identifier)
        now = self._clock()
        technical = build_technical_packet(result.resolution.identifier, now, bars)
        risk = risk_packet(result.resolution, bars, self._risk_world, now)
        return build_view(result, bars, technical, risk)


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel are loaded once, when the service
    is built, so a session left open across days should be restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=HISTORY_DAYS))
    cache = RequestCache(history_fetcher(sources.chain))
    orchestrator = build_orchestrator(sources.resolver, sources.llm_router, sources.chain, fetch_bars=cache)
    return DashboardService(orchestrator, cache, risk_world_from_store(sources.store))
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_service.py tests/test_cli.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `10 passed` (5 service + 5 existing CLI tests), then `376 passed, 44 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `service.py`, replace the first `self._bars.clear()` inside `DashboardService.view` with `pass` and run `.venv/Scripts/python -m pytest tests/test_dashboard_service.py -q`: expect `test_every_request_starts_with_a_fresh_download` to FAIL. Undo it and confirm `git diff` is empty for `service.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/cli.py src/athena/dashboard/service.py tests/test_dashboard_service.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add dashboard service with a shared per-request price download"
git push
```

---

### Task 6: The page, the launcher, live checks, docs and graph refresh

**Files:**
- Create: `src/athena/dashboard/app.py`, `src/athena/dashboard/__main__.py`, `tests/test_dashboard_app.py`, `tests/live/test_live_dashboard.py`
- Modify: `TRD.md`

**Interfaces:**
- Consumes: `DashboardView`, `Panel` (Task 4); `live_service` (Task 5); `DISCLAIMER` (Plan 1c); Streamlit and its `streamlit.testing.v1.AppTest`; `dash_fakes` (Task 4, tests).
- Produces (`athena.dashboard.app`): `ViewService` protocol (`view(query) -> DashboardView`); `render(view)`; `run(service)` (title, text box, spinner, error handling; draws the view); the script entry (`if __name__ == "__main__"` runs `run(_live_service())`, with the live service cached for the session). Produces `python -m athena.dashboard [streamlit options]`.

- [ ] **Step 1: Write the failing tests `tests/test_dashboard_app.py`**

```python
import dash_fakes
from dash_fakes import FakeService, ambiguous_result, athena_error, full_view
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.dashboard.app'`.

- [ ] **Step 3: Write `src/athena/dashboard/app.py` and `src/athena/dashboard/__main__.py`**

`src/athena/dashboard/app.py`:

```python
from __future__ import annotations

from typing import Protocol

import streamlit as st

from athena.contracts import AthenaError
from athena.dashboard.view import DashboardView, Panel
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW
from athena.orchestrator.report import DISCLAIMER

PLACEHOLDER = "SBIN"


class ViewService(Protocol):
    def view(self, query: str) -> DashboardView: ...


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


def run(service: ViewService) -> None:
    st.set_page_config(page_title="Athena", layout="wide")
    st.title("Athena")
    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER).strip()
    if not query:
        st.info("Type an NSE ticker, an ISIN or a name to analyze it.")
        return
    try:
        with st.spinner("Resolving, fetching data and asking the specialists..."):
            view = service.view(query)
    except (AthenaError, ValueError) as exc:
        st.error(str(exc))
        return
    render(view)


@st.cache_resource(show_spinner="Loading NSE lists, market series and models...")
def _live_service():
    from athena.dashboard.service import live_service

    return live_service()


if __name__ == "__main__":
    run(_live_service())
```

`src/athena/dashboard/__main__.py`:

```python
"""`python -m athena.dashboard` opens the dashboard in the browser; extra arguments go to Streamlit,
for example `--server.port 8600` or `--server.headless true`."""
from __future__ import annotations

import sys
from pathlib import Path

APP = Path(__file__).with_name("app.py")


def main(extra: list[str] | None = None) -> int:
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(APP), *(sys.argv[1:] if extra is None else extra)]
    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `9 passed`, then `385 passed, 44 skipped`. Lint: `.venv/Scripts/python -m pyflakes src/athena/dashboard src/athena/cli.py src/athena/technicals tests/dash_fakes.py tests/test_dashboard*.py` prints nothing.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `app.py`, replace `"value": _show(r.value)` with `"value": r.value` and run `.venv/Scripts/python -m pytest tests/test_dashboard_app.py -q`: expect `test_each_metric_table_has_one_text_value_column_so_the_browser_never_gets_mixed_types` to FAIL (with an Arrow error logged). Undo it and confirm `git diff` is empty for `app.py`.

- [ ] **Step 6: Write the live tests `tests/live/test_live_dashboard.py`**

```python
import pytest
from streamlit.testing.v1 import AppTest

from athena.contracts import AthenaError
from athena.dashboard.service import live_service
from athena.orchestrator.orchestrator import OK

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def service():
    try:
        return live_service()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


def rows(panel):
    return {row.name: row.value for row in panel.rows}


def test_live_stock_view_has_verdict_charts_and_complete_panels(service):
    view = service.view("SBIN")
    technical, risk = view.panels
    print("\nSBIN", view.verdict, {k: rows(technical)[k] for k in ("last_close", "rsi_14", "trend_alignment")}, rows(risk))
    assert view.status == OK and view.price_figure is not None and view.rsi_figure is not None
    assert technical.coverage == "full" and not technical.missing
    assert {"volatility_annualized", "max_drawdown", "beta", "alpha_annualized", "sharpe"} <= set(rows(risk))
    assert "tracking_error" in risk.missing  # a stock has no tracking index


def test_live_etf_view_adds_tracking_error_against_the_index(service):
    view = service.view("NIFTYBEES")
    risk = view.panels[1]
    print("\nNIFTYBEES", view.verdict, rows(risk))
    assert view.asset_class == "etf" and {"tracking_error", "tracking_difference"} <= set(rows(risk))
    assert all(row.note for row in risk.rows if row.name.startswith("tracking"))  # the market-price caveat travels along


def test_live_ambiguous_name_asks_instead_of_guessing(service):
    view = service.view("SBI")
    assert view.candidates and view.price_figure is None


def test_live_page_renders_end_to_end():
    def script():
        from athena.dashboard.app import _live_service, run

        run(_live_service())

    app = AppTest.from_function(script, default_timeout=180).run()
    app.text_input[0].set_value("SBIN").run()
    assert not app.exception, app.exception
    assert len(app.get("plotly_chart")) == 2 and {m.label for m in app.metric} == {"Verdict", "Conviction"}
```

- [ ] **Step 7: Run the live tests and look at the real page**

```bash
.venv/Scripts/python -m pytest --live tests/live/test_live_dashboard.py -q -s
.venv/Scripts/python -m athena.dashboard
```

Expected: `4 passed` with the stock and ETF figures printed; then a browser tab opens at `http://localhost:8501`. Type `SBIN`, then `NIFTYBEES`, then `SBI` and check: verdict and conviction, specialist expanders, candlestick chart with averages, bands and volume, RSI chart, two tables each with an as-of, source and coverage line, key risks, notes, the disclaimer. Stop the server with Ctrl+C. Numbers and verdicts change daily; a model may be rate-limited on a given run (the chain falls through to the next provider). If a run reports that no LLM provider key is set, ask the user to check `.env`: the assistant cannot read it. To check the server without a browser: `python -m athena.dashboard --server.headless true --server.port 8599`, request `http://localhost:8599/_stcore/health` (expect `200`), then stop it.

- [ ] **Step 8: Update `TRD.md`**

1. At the end of the §2.9 paragraph (`**2.9 Visualization/Dashboard agent.**`), append: ` *Implemented in Plan 1d (stocks and ETFs only):* a Streamlit page (\`python -m athena.dashboard\`) with a Plotly candlestick chart (50- and 200-day averages, Bollinger Bands, volume), an RSI chart, the technical-indicator table and the risk and benchmark table, each with as-of, source and coverage; data is end of day. Not built: the options panel, the portfolio risk and overlap view, mutual funds, and any live feed.`
2. In the revision history, add after the Phase 1c line: `- **Oct 5, 2026 (Phase 1d)** — Stocks and ETF dashboard implemented (see docs/superpowers/plans/2026-10-05-phase-1d-dashboard.md).`

- [ ] **Step 9: Commit, push, refresh graph**

```bash
git add src/athena/dashboard/app.py src/athena/dashboard/__main__.py tests/test_dashboard_app.py tests/live/test_live_dashboard.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add Streamlit dashboard page and launcher; document dashboard in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.9, PRD FR-6 -> task):** candles + moving averages + Bollinger Bands + volume (Task 2 chart, Task 6 page); momentum flag via RSI (Tasks 1-2); every panel with as-of time and coverage label (Task 4 `Panel`, Task 6 captions); consumes the specialists' structured output (Task 4 `SpecialistRow`, verdict); interactive and re-queryable per instrument, not a static image (Task 6: Plotly interaction and the text box, tested with `AppTest`, and a real server start); "any equity/ETF" (Tasks 3-5; unmapped ETFs are handled with a stated note). Not in this plan: the options Greeks/IV panel, the portfolio risk/overlap view, mutual funds, pattern-recognition flags, and a live feed.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `IndicatorSeries`/`indicator_series`, `price_chart`/`rsi_chart`, `RiskWorld`/`risk_packet`, `DashboardView`/`Panel`/`build_view`, `RequestCache`/`DashboardService`/`live_service`, `LiveSources`/`live_sources`/`build_orchestrator(fetch_bars=...)`, `render`/`run`/`ViewService` and the fakes (`FakeService`, `CURRENT`, `full_view`) match across Tasks 1-6. Test totals: 347 + 5 + 5 + 6 + 8 + 5 + 9 = 385 passed; skipped 44 + 4 live = 48.
