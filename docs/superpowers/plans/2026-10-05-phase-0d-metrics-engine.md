# Phase 0d — Metrics Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute, in code, every quantitative figure a stock or ETF specialist will cite: annualised volatility, maximum drawdown, beta, Jensen's alpha, Sharpe ratio, and (for ETFs) tracking error and tracking difference, packaged as the metrics packet defined in TRD §3. Specialists interpret these numbers; they never calculate them.

**Architecture:** `athena.metrics` is pure functions over level series keyed by IST trading date (`stats.py` math, `series.py` alignment and risk-free returns, `packets.py` packet builder). Data arrives through the existing `DataStore`; one new loader (`IndexCloseLoader`) supplies the Nifty 50 price index and the Nifty 1D Rate Index. A metric that cannot be computed is listed under `missing` with a reason; it is never dropped silently.

**Tech Stack:** Python >= 3.11, numpy, pytest.

**Spec:** `TRD.md` §2.13 (metrics engine), §3 (metrics packet), §6.1 (coverage matrix); resolves TRD §9 open decision 6 (India risk-free rate).

**Plan series:** 0a, 0b, 0c (done) -> **0d (this plan)** -> 0e evaluation harness (number-grounding check, schema validator, resolver evaluation and candidate-ranking fix). Mutual-fund metrics (expense drag, overlap, alpha vs category benchmark) stay on hold.

**Suggested models:** Sonnet at medium effort. This plan was prototyped in a scratch copy first: all 194 offline tests passed and the 3 live tests passed against real data, so each task should go green on the first run.

## Verified findings (5 Oct 2026) — read before coding

- **Risk-free rate (decision 6, resolved).** FRED's India short-rate series are stale (policy rate stops in 2023, discount rate in 2022; only the 10-year yield is recent, with a ~3-month lag) and FRED resets plain Python `requests` connections (curl works). The usable free source is niftyindices' **Nifty 1D Rate Index** (an overnight-rate accrual index, `NSEIndexHistory().index_raw("NIFTY 1D RATE INDEX", from, to)`, `CLOSE` column). The risk-free return between two dates is the ratio of the index at those dates, so no annualising or day-count convention is needed. Live check: annualised over the last year it is 5.25%, a plausible overnight rate. It is an *overnight* rate, a slightly low proxy for a 91-day T-bill; no free T-bill series was found.
- **ETF tracking error is market-price based.** Measured from NSE closing prices against the Nifty 50 TRI, NIFTYBEES shows tracking error ~2.5% annualised and tracking difference ~0.2% over a year. Shifting the series by a day makes tracking error ~17%, so the alignment is right, and jugaad-data and Yahoo agree. The 2.5% is premium/discount noise in the exchange close, not the fund's NAV tracking error (which would be far smaller); free NAV is unavailable. The packet therefore attaches a `note` to the tracking metrics saying so.
- **Live sanity values:** SBIN vs Nifty 50: volatility 23.7%, max drawdown -23.5%, beta 1.10, Sharpe 0.31 over ~1 year.
- Stock benchmark = Nifty 50 **price** index (stock prices exclude dividends); ETF tracking index = Nifty 50 **TRI**.

## Global Constraints

- Free data only; no network in the default test run (live tests are opt-in via `--live`).
- Series are `dict[date, float]` keyed by IST trading date. A metric uses the last `window` (default 253 levels = 252 returns) dates its own inputs share, and its `window` text reports the span actually used.
- Annualisation uses 252 periods; sample (ddof=1) standard deviations; at least 20 observations unless stated.
- `InsufficientData` is raised by `stats` and caught by the packet builder; callers outside the builder see it.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` (modify) | Declare `numpy` |
| `src/athena/contracts.py` (modify) | Add `InsufficientData` |
| `src/athena/store.py` (modify) | Add `history(dataset, key)` |
| `src/athena/freshness.py` (modify) | Limits for `index.price`, `rate.overnight` |
| `src/athena/loaders/index_close.py` | `IndexCloseLoader` (price index and rate index) |
| `src/athena/metrics/__init__.py` | Package marker |
| `src/athena/metrics/stats.py` | Volatility, drawdown, beta, alpha, Sharpe, tracking error/difference |
| `src/athena/metrics/series.py` | Series helpers, risk-free returns |
| `src/athena/metrics/packets.py` | `build_packet` |
| `tests/test_*.py`, `tests/live/test_live_metrics.py` | Offline and live tests |

All commands run from the project root in Git Bash.

---

### Task 1: `InsufficientData`, `DataStore.history`, new limits, numpy

**Files:**
- Modify: `pyproject.toml`, `src/athena/contracts.py`, `src/athena/store.py`, `src/athena/freshness.py`, `tests/test_contracts.py`, `tests/test_store.py`, `tests/test_freshness.py`

**Interfaces:**
- Produces: `athena.contracts.InsufficientData(AthenaError)`; `DataStore.history(dataset: str, key: str) -> list[Record]` (every record for the key, oldest first, ties by insertion order); `DEFAULT_LIMITS["index.price"]` and `["rate.overnight"]` = `Limit(1, "business_days")`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_contracts.py`, add `InsufficientData,` to the import list (before `StaleDataError,`) and extend the parametrize list so it reads:

```python
@pytest.mark.parametrize(
    "exc",
    [StaleDataError, EmptyRefreshError, SchemaChangedError, AllSourcesFailed, UnsupportedOperation, UnknownInstrument, InsufficientData],
)
```

Append to `tests/test_store.py`:

```python
def test_history_returns_every_record_for_a_key_oldest_first():
    store = DataStore()
    store.put_many([rec(5, 3.0), rec(2, 1.0), rec(3, 2.0), rec(4, 9.0, key="OTHER")])
    assert [r.payload["nav"] for r in store.history("mf.nav", "119551")] == [1.0, 2.0, 3.0]
    assert store.history("mf.nav", "missing") == []
```

Append to `tests/test_freshness.py`:

```python
def test_index_and_rate_limits():
    assert DEFAULT_LIMITS["index.price"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["rate.overnight"] == Limit(1, "business_days")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_contracts.py tests/test_store.py tests/test_freshness.py -q`
Expected: FAIL (`ImportError: cannot import name 'InsufficientData'`).

- [ ] **Step 3: Implement**

`src/athena/contracts.py`: immediately before `class UnsupportedOperation`, add:

```python
class InsufficientData(AthenaError):
    """Too little (or degenerate) data to compute a metric."""


```

`src/athena/store.py`: immediately before `def latest_records`, add:

```python
    def history(self, dataset: str, key: str) -> list[Record]:
        """Every record for a key, oldest first."""
        rows = self._con.execute(
            "SELECT dataset, key, as_of, source, payload FROM records "
            "WHERE dataset = ? AND key = ? ORDER BY as_of, rowid",
            [dataset, key],
        ).fetchall()
        return [Record(r[0], r[1], _from_db(r[2]), r[3], json.loads(r[4])) for r in rows]

```

`src/athena/freshness.py`: in `DEFAULT_LIMITS`, after the `"master.nse_etf"` line, add:

```python
    "index.price": Limit(1, "business_days"),
    "rate.overnight": Limit(1, "business_days"),
```

`pyproject.toml`: change the `dependencies` line to:

```toml
dependencies = ["duckdb>=1.0", "requests>=2.31", "jugaad-data>=0.35", "yfinance>=1.0", "numpy>=1.26"]
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pip install -q -e ".[dev]" && .venv/Scripts/python -m pytest -q`
Expected: `155 passed, 33 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add pyproject.toml src/athena/contracts.py src/athena/store.py src/athena/freshness.py tests/test_contracts.py tests/test_store.py tests/test_freshness.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add InsufficientData, DataStore.history, and index/rate freshness limits"
git push
```

---

### Task 2: Index close loader

**Files:**
- Create: `src/athena/loaders/index_close.py`
- Test: `tests/test_index_close.py`

**Interfaces:**
- Produces (`athena.loaders.index_close`): `PRICE_DATASET="index.price"`, `RATE_DATASET="rate.overnight"`, `PRICE_INDEX="NIFTY 50"`, `RATE_INDEX="NIFTY 1D RATE INDEX"`; `parse_close_rows(rows) -> list[tuple[date, float]]`; `IndexCloseLoader(store, dataset=PRICE_DATASET, default_index=PRICE_INDEX, fetch=default_fetch, clock=utc_now)` implementing `BatchLoader`. One `Record` per trading date: `key=index_name`, `as_of` = that date's IST midnight in UTC, `payload={"close": float}`. Same incremental rules as `IndexTriLoader` (window = `since`, else latest stored day, else 30 days; only newer dates written; empty fetch raises `EmptyRefreshError`).

- [ ] **Step 1: Write the failing tests `tests/test_index_close.py`**

```python
from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.index_close import (
    PRICE_DATASET,
    RATE_DATASET,
    RATE_INDEX,
    IndexCloseLoader,
    parse_close_rows,
)
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # Mon 09:30 IST


def crow(day, close, name="Nifty 50"):
    return {"INDEX_NAME": name, "HistoricalDate": day, "OPEN": "-", "HIGH": "-", "LOW": "-", "CLOSE": close}


ROWS = [crow("01 Oct 2026", "22421.95"), crow("30 Sep 2026", "22620.45")]


def make_loader(store, rows=ROWS, calls=None, dataset=PRICE_DATASET, default_index="NIFTY 50"):
    def fetch(index_name, start, end):
        if calls is not None:
            calls.append((index_name, start, end))
        return rows

    return IndexCloseLoader(store, dataset=dataset, default_index=default_index, fetch=fetch, clock=lambda: NOW)


def test_parse_sorts_ascending_and_ignores_dash_columns():
    assert parse_close_rows(ROWS) == [(date(2026, 9, 30), 22620.45), (date(2026, 10, 1), 22421.95)]


@pytest.mark.parametrize("bad", [{"HistoricalDate": "2026-10-01", "CLOSE": "1"}, {"HistoricalDate": "01 Oct 2026"}, {"HistoricalDate": "01 Oct 2026", "CLOSE": "-"}])
def test_malformed_row_is_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_close_rows([bad])


def test_refresh_stores_close_at_ist_midnight():
    store = DataStore()
    assert make_loader(store).refresh().rows_written == 2
    latest = store.latest(PRICE_DATASET, "NIFTY 50")
    assert latest.as_of == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert latest.payload == {"close": 22421.95}


def test_rate_instance_uses_its_own_dataset_and_index():
    store = DataStore()
    calls = []
    loader = make_loader(store, rows=[crow("01 Oct 2026", "2608.02", RATE_INDEX)], calls=calls, dataset=RATE_DATASET, default_index=RATE_INDEX)
    loader.refresh()
    assert calls[0][0] == RATE_INDEX
    assert store.latest(RATE_DATASET, RATE_INDEX).payload == {"close": 2608.02}
    assert store.latest(PRICE_DATASET, "NIFTY 50") is None


def test_default_window_since_and_incremental_rerun():
    store = DataStore()
    calls = []
    loader = make_loader(store, calls=calls)
    loader.refresh()
    assert loader.refresh().rows_written == 0
    make_loader(DataStore(), calls=calls).refresh(since=date(2026, 9, 25))
    assert calls == [
        ("NIFTY 50", date(2026, 9, 5), date(2026, 10, 5)),
        ("NIFTY 50", date(2026, 10, 1), date(2026, 10, 5)),
        ("NIFTY 50", date(2026, 9, 25), date(2026, 10, 5)),
    ]
    newer = ROWS + [crow("05 Oct 2026", "22500.00")]
    assert make_loader(store, rows=newer).refresh().rows_written == 1


def test_empty_fetch_raises():
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), rows=[]).refresh()


def test_wrong_dataset_and_protocol():
    loader = make_loader(DataStore())
    with pytest.raises(ValueError, match="index.price"):
        loader.refresh("other")
    assert isinstance(loader, BatchLoader)
    with pytest.raises(EmptyRefreshError):
        loader.read(PRICE_DATASET, "NIFTY 50")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_index_close.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.loaders.index_close'`.

- [ ] **Step 3: Write `src/athena/loaders/index_close.py`**

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import IST, ist_date

PRICE_DATASET = "index.price"
RATE_DATASET = "rate.overnight"
PRICE_INDEX = "NIFTY 50"
RATE_INDEX = "NIFTY 1D RATE INDEX"
SOURCE = "jugaad.index_raw"
_DEFAULT_WINDOW_DAYS = 30


def default_fetch(index_name: str, start: date, end: date) -> list[dict]:
    from jugaad_data.nse import NSEIndexHistory

    return NSEIndexHistory().index_raw(index_name, start, end)


def parse_close_rows(rows: list[dict]) -> list[tuple[date, float]]:
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(row["HistoricalDate"], "%d %b %Y").date()
            parsed.append((day, float(row["CLOSE"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise SchemaChangedError(f"unexpected index row shape: {exc!r}") from exc
    return sorted(parsed)


def _ist_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)


class IndexCloseLoader:
    """Daily closing levels of an NSE index. Two instances are used: the Nifty 50 price index
    (`index.price`) and the Nifty 1D Rate Index (`rate.overnight`), whose level ratio between two
    dates is the overnight risk-free return over that period."""

    def __init__(
        self,
        store: DataStore,
        dataset: str = PRICE_DATASET,
        default_index: str = PRICE_INDEX,
        fetch: Callable[[str, date, date], list[dict]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._dataset = dataset
        self._default_index = default_index
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {self._dataset: {"cadence": "daily", "source": SOURCE, "series": ["close"]}}

    def refresh(self, dataset: str | None = None, since: Any = None, **params: Any) -> RefreshResult:
        dataset = dataset or self._dataset
        if dataset != self._dataset:
            raise ValueError(f"this loader only provides {self._dataset!r}, got {dataset!r}")
        index_name = params.get("index_name", self._default_index)
        now = self._clock()
        today = ist_date(now)
        last = self._store.latest(self._dataset, index_name)
        last_day = ist_date(last.as_of) if last else None
        start = since or last_day or today - timedelta(days=_DEFAULT_WINDOW_DAYS)

        raw = self._fetch(index_name, start, today)
        if not raw:
            raise EmptyRefreshError(f"no rows returned for {index_name!r} from {start} to {today}")
        written = 0
        for day, close in parse_close_rows(raw):
            if last_day is not None and day <= last_day:
                continue
            self._store.put(
                Record(self._dataset, index_name, _ist_midnight_utc(day), SOURCE, {"close": close})
            )
            written += 1
        return RefreshResult(self._dataset, written, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `164 passed, 33 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/loaders/index_close.py tests/test_index_close.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add index close loader for price index and overnight rate index"
git push
```

---

### Task 3: Statistics

**Files:**
- Create: `src/athena/metrics/__init__.py` (empty file), `src/athena/metrics/stats.py`
- Test: `tests/test_metrics_stats.py`

**Interfaces:**
- Produces (`athena.metrics.stats`): `TRADING_DAYS=252`, `MIN_OBS=20`; `annualized_volatility(returns, periods=252, min_obs=20) -> float`; `max_drawdown(levels, min_obs=2) -> float` (negative fraction, `0.0` if never falls); `beta(asset, benchmark, min_obs=20) -> float`; `jensen_alpha(asset, benchmark, riskfree, periods=252, min_obs=20) -> float` (annualised; beta fitted on excess returns); `sharpe(asset, riskfree, periods=252, min_obs=20) -> float`; `tracking_error(asset, index, periods=252, min_obs=20) -> float`; `tracking_difference(asset_levels, index_levels) -> float`. Too few observations or zero variance raise `InsufficientData`; unequal lengths raise `ValueError`.

- [ ] **Step 1: Write the failing tests `tests/test_metrics_stats.py`**

```python
import math
import statistics

import pytest

from athena.contracts import InsufficientData
from athena.metrics import stats

BENCH = [((i * 7) % 11 - 5) / 500 for i in range(30)]  # varied, non-constant daily returns


def test_volatility_of_alternating_returns_matches_the_closed_form():
    returns = [0.01, -0.01] * 10
    expected = math.sqrt(20 * 0.01**2 / 19) * math.sqrt(252)
    assert stats.annualized_volatility(returns) == pytest.approx(expected)


def test_too_few_observations_raise_insufficient_data():
    with pytest.raises(InsufficientData, match="at least 20"):
        stats.annualized_volatility([0.01] * 5)
    with pytest.raises(InsufficientData):
        stats.max_drawdown([100.0])


def test_max_drawdown_is_the_worst_peak_to_trough_fall():
    assert stats.max_drawdown([100, 120, 90, 110, 80, 130]) == pytest.approx(80 / 120 - 1)
    assert stats.max_drawdown([100, 101, 102]) == 0.0


def test_beta_recovers_a_known_multiple():
    asset = [2.0 * r for r in BENCH]
    assert stats.beta(asset, BENCH) == pytest.approx(2.0)


def test_beta_needs_a_benchmark_that_moves():
    with pytest.raises(InsufficientData, match="zero variance"):
        stats.beta([0.01] * 25, [0.0] * 25)


def test_alpha_recovers_a_known_intercept_and_beta():
    asset = [1.5 * r + 0.0004 for r in BENCH]
    rf = [0.0] * len(BENCH)
    assert stats.jensen_alpha(asset, BENCH, rf) == pytest.approx(0.0004 * 252)
    assert stats.beta(asset, BENCH) == pytest.approx(1.5)


def test_alpha_is_zero_for_an_asset_that_earns_exactly_the_riskfree_rate():
    rf = [0.0002] * len(BENCH)
    assert stats.jensen_alpha(rf, BENCH, rf) == pytest.approx(0.0, abs=1e-12)


def test_sharpe_matches_the_closed_form():
    rf = [0.0002] * 30
    excess = [((i * 5) % 7 - 3) / 400 for i in range(30)]
    asset = [r + x for r, x in zip(rf, excess)]
    expected = statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252)
    assert stats.sharpe(asset, rf) == pytest.approx(expected)


def test_sharpe_with_constant_excess_return_is_undefined():
    with pytest.raises(InsufficientData, match="zero variance"):
        stats.sharpe([0.001] * 25, [0.0] * 25)


def test_tracking_error_is_annualised_std_of_active_returns():
    index = BENCH
    active = [0.001 if i % 2 else -0.001 for i in range(30)]
    asset = [r + a for r, a in zip(index, active)]
    assert stats.tracking_error(asset, index) == pytest.approx(statistics.stdev(active) * math.sqrt(252))
    assert stats.tracking_error(index, index) == pytest.approx(0.0, abs=1e-12)


def test_tracking_difference_is_cumulative_return_gap():
    assert stats.tracking_difference([100, 110], [100, 112]) == pytest.approx(0.10 - 0.12)


def test_unequal_lengths_are_rejected():
    with pytest.raises(ValueError, match="equal-length"):
        stats.tracking_error([0.01] * 25, [0.01] * 24)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_stats.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.metrics'`.

- [ ] **Step 3: Create the empty `src/athena/metrics/__init__.py` and write `src/athena/metrics/stats.py`**

```python
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from athena.contracts import InsufficientData

TRADING_DAYS = 252
MIN_OBS = 20


def _array(values: Sequence[float], name: str, min_obs: int) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or len(array) < min_obs:
        raise InsufficientData(f"{name} needs at least {min_obs} observations, got {len(array)}")
    return array


def _paired(a: Sequence[float], b: Sequence[float], name: str, min_obs: int) -> tuple[np.ndarray, np.ndarray]:
    first, second = _array(a, name, min_obs), _array(b, name, min_obs)
    if len(first) != len(second):
        raise ValueError(f"{name} needs equal-length series, got {len(first)} and {len(second)}")
    return first, second


def annualized_volatility(returns: Sequence[float], periods: int = TRADING_DAYS, min_obs: int = MIN_OBS) -> float:
    values = _array(returns, "volatility", min_obs)
    return float(np.std(values, ddof=1) * math.sqrt(periods))


def max_drawdown(levels: Sequence[float], min_obs: int = 2) -> float:
    """Worst peak-to-trough fall as a negative fraction (0.0 if the series never falls)."""
    values = _array(levels, "max drawdown", min_obs)
    peaks = np.maximum.accumulate(values)
    return float(np.min(values / peaks - 1.0))


def beta(asset: Sequence[float], benchmark: Sequence[float], min_obs: int = MIN_OBS) -> float:
    a, b = _paired(asset, benchmark, "beta", min_obs)
    variance = float(np.var(b, ddof=1))
    if variance == 0.0:
        raise InsufficientData("beta is undefined: the benchmark has zero variance")
    return float(np.cov(a, b, ddof=1)[0, 1] / variance)


def jensen_alpha(
    asset: Sequence[float],
    benchmark: Sequence[float],
    riskfree: Sequence[float],
    periods: int = TRADING_DAYS,
    min_obs: int = MIN_OBS,
) -> float:
    """Annualised Jensen's alpha from per-period returns (beta fitted on excess returns)."""
    a, b = _paired(asset, benchmark, "alpha", min_obs)
    _, r = _paired(asset, riskfree, "alpha", min_obs)
    excess_a, excess_b = a - r, b - r
    slope = beta(excess_a, excess_b, min_obs)
    return float((np.mean(excess_a) - slope * np.mean(excess_b)) * periods)


def sharpe(
    asset: Sequence[float], riskfree: Sequence[float], periods: int = TRADING_DAYS, min_obs: int = MIN_OBS
) -> float:
    a, r = _paired(asset, riskfree, "sharpe", min_obs)
    excess = a - r
    deviation = float(np.std(excess, ddof=1))
    if deviation == 0.0:
        raise InsufficientData("sharpe is undefined: excess returns have zero variance")
    return float(np.mean(excess) / deviation * math.sqrt(periods))


def tracking_error(
    asset: Sequence[float], index: Sequence[float], periods: int = TRADING_DAYS, min_obs: int = MIN_OBS
) -> float:
    a, i = _paired(asset, index, "tracking error", min_obs)
    return float(np.std(a - i, ddof=1) * math.sqrt(periods))


def tracking_difference(asset_levels: Sequence[float], index_levels: Sequence[float]) -> float:
    """Cumulative return of the asset minus cumulative return of the index over the same window."""
    a, i = _paired(asset_levels, index_levels, "tracking difference", 2)
    return float((a[-1] / a[0] - 1.0) - (i[-1] / i[0] - 1.0))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `176 passed, 33 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/metrics tests/test_metrics_stats.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add metrics statistics (volatility, drawdown, beta, alpha, sharpe, tracking)"
git push
```

---

### Task 4: Series helpers

**Files:**
- Create: `src/athena/metrics/series.py`
- Test: `tests/test_metrics_series.py`

**Interfaces:**
- Consumes: `Bar`, `InsufficientData`, `DataStore.history`, `ist_date`.
- Produces (`athena.metrics.series`): `Series = dict[date, float]`; `series_from_bars(bars) -> Series` (close keyed by IST date); `series_from_store(store, dataset, key, field) -> Series` (payload `field`, keyed by IST date of `as_of`); `common_dates(*series) -> list[date]` (sorted intersection); `levels_on(series, days) -> list[float]`; `returns_from_levels(levels) -> list[float]`; `level_asof(series, day) -> float | None`; `riskfree_returns(rate_index, days) -> list[float]` (level ratios between consecutive days using as-of levels; raises `InsufficientData` if the index starts after the first day).

- [ ] **Step 1: Write the failing tests `tests/test_metrics_series.py`**

```python
from datetime import date, datetime, timezone

import pytest

from athena.contracts import Bar, InsufficientData, Record
from athena.metrics.series import (
    common_dates,
    level_asof,
    levels_on,
    returns_from_levels,
    riskfree_returns,
    series_from_bars,
    series_from_store,
)
from athena.store import DataStore

UTC = timezone.utc
D = lambda day: date(2026, 10, day)  # noqa: E731


def test_returns_from_levels():
    assert returns_from_levels([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])
    assert returns_from_levels([100.0]) == []


def test_common_dates_intersects_and_sorts():
    a = {D(1): 1.0, D(5): 2.0, D(6): 3.0}
    b = {D(6): 9.0, D(1): 8.0, D(2): 7.0}
    assert common_dates(a, b) == [D(1), D(6)]
    assert common_dates(a) == [D(1), D(5), D(6)]
    assert common_dates() == []


def test_levels_on_follows_the_given_dates():
    assert levels_on({D(1): 1.0, D(5): 5.0}, [D(5), D(1)]) == [5.0, 1.0]


def test_level_asof_uses_the_latest_earlier_level():
    series = {D(1): 10.0, D(5): 50.0}
    assert level_asof(series, D(3)) == 10.0
    assert level_asof(series, D(5)) == 50.0
    assert level_asof(series, D(9)) == 50.0
    assert level_asof(series, date(2026, 9, 30)) is None


def test_riskfree_returns_use_index_level_ratios_between_dates():
    rate_index = {D(1): 2600.0, D(2): 2601.0, D(3): 2602.0, D(5): 2605.0}  # D(4) missing (weekend)
    got = riskfree_returns(rate_index, [D(1), D(3), D(5)])
    assert got == pytest.approx([2602.0 / 2600.0 - 1.0, 2605.0 / 2602.0 - 1.0])


def test_riskfree_returns_raise_when_index_starts_after_first_day():
    with pytest.raises(InsufficientData, match="does not cover"):
        riskfree_returns({D(3): 1.0}, [D(1), D(3)])


def test_series_from_bars_keys_by_ist_trading_date():
    bar = Bar("SBIN", datetime(2026, 9, 30, 18, 30, tzinfo=UTC), 1, 1, 1, 954.1, 10, datetime(2026, 10, 5, tzinfo=UTC), "jugaad")
    assert series_from_bars([bar]) == {D(1): 954.1}


def test_series_from_store_reads_history_by_ist_date():
    store = DataStore()
    for day, value in ((29, 1.0), (30, 2.0)):  # IST midnight of 30 Sep and 1 Oct, stored as UTC
        store.put(Record("index.price", "NIFTY 50", datetime(2026, 9, day, 18, 30, tzinfo=UTC), "x", {"close": value}))
    assert series_from_store(store, "index.price", "NIFTY 50", "close") == {date(2026, 9, 30): 1.0, D(1): 2.0}
    assert series_from_store(store, "index.price", "OTHER", "close") == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_series.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.metrics.series'`.

- [ ] **Step 3: Write `src/athena/metrics/series.py`**

```python
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date

from athena.contracts import Bar, InsufficientData
from athena.store import DataStore
from athena.trading_calendar import ist_date

Series = dict[date, float]  # level (price / index value) keyed by IST trading date


def series_from_bars(bars: Sequence[Bar]) -> Series:
    return {ist_date(bar.timestamp): bar.close for bar in bars}


def series_from_store(store: DataStore, dataset: str, key: str, field: str) -> Series:
    return {ist_date(record.as_of): float(record.payload[field]) for record in store.history(dataset, key)}


def common_dates(*series: Series) -> list[date]:
    if not series:
        return []
    shared = set(series[0])
    for other in series[1:]:
        shared &= set(other)
    return sorted(shared)


def levels_on(series: Series, days: Sequence[date]) -> list[float]:
    return [series[day] for day in days]


def returns_from_levels(levels: Sequence[float]) -> list[float]:
    return [later / earlier - 1.0 for earlier, later in zip(levels, levels[1:])]


def level_asof(series: Series, day: date) -> float | None:
    """The level on `day`, or the most recent earlier level; None if the series starts later."""
    ordered = sorted(series)
    position = bisect_right(ordered, day)
    return series[ordered[position - 1]] if position else None


def riskfree_returns(rate_index: Series, days: Sequence[date]) -> list[float]:
    """Per-period risk-free returns between consecutive `days`, from an accrual index such as the
    Nifty 1D Rate Index. Raises InsufficientData if the index starts after the first day."""
    levels = [level_asof(rate_index, day) for day in days]
    if any(level is None for level in levels):
        raise InsufficientData("the risk-free index does not cover the start of the window")
    return returns_from_levels(levels)  # type: ignore[arg-type]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `184 passed, 33 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/metrics/series.py tests/test_metrics_series.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add metrics series helpers and risk-free returns"
git push
```

---

### Task 5: Metrics packet builder

**Files:**
- Create: `src/athena/metrics/packets.py`
- Test: `tests/test_metrics_packets.py`

**Interfaces:**
- Consumes: `stats`, `series` helpers, `InsufficientData`.
- Produces (`athena.metrics.packets`): `DEFAULT_WINDOW=253`, `RISKFREE_NAME="NIFTY 1D RATE INDEX"`, `TRACKING_NOTE`; `build_packet(instrument, as_of, asset, benchmark=None, benchmark_name=None, riskfree=None, tracking_index=None, tracking_index_name=None, window=253) -> dict` returning `{"instrument", "as_of" (ISO), "metrics": {name: {"value", "unit", "inputs", "window", "source", ["note"]}}, "missing": [names], "missing_reasons": {name: reason}}`. Metrics: `volatility_annualized`, `max_drawdown`, `beta`, `alpha_annualized`, `sharpe`, `tracking_error`, `tracking_difference`. A series that is `None` gives `"required series not provided"`; too little data gives the `InsufficientData` message. Tracking metrics carry `TRACKING_NOTE` (market-price basis). The packet is JSON-serialisable.

- [ ] **Step 1: Write the failing tests `tests/test_metrics_packets.py`**

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.metrics.packets import RISKFREE_NAME, TRACKING_NOTE, build_packet

UTC = timezone.utc
AS_OF = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
START = date(2026, 1, 1)


def days(n):
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def levels(dates, daily_returns, start=100.0):
    out, level = {}, start
    for d, r in zip(dates, [0.0] + daily_returns):
        level *= 1.0 + r
        out[d] = level
    return out


N = 120
DATES = days(N)
BENCH_R = [((i * 7) % 11 - 5) / 500 for i in range(N - 1)]
ASSET_R = [1.5 * r + 0.0003 for r in BENCH_R]
INDEX_R = [r + (0.0005 if i % 2 else -0.0005) for i, r in enumerate(BENCH_R)]
BENCH = levels(DATES, BENCH_R)
ASSET = levels(DATES, ASSET_R)
RATE = levels(DATES, [0.0002] * (N - 1), start=2600.0)
TRACKED = levels(DATES, INDEX_R)


def full_packet(**overrides):
    args = dict(
        instrument="SBIN", as_of=AS_OF, asset=ASSET, benchmark=BENCH, benchmark_name="NIFTY 50",
        riskfree=RATE, tracking_index=TRACKED, tracking_index_name="NIFTY 50 TRI",
    )
    args.update(overrides)
    return build_packet(**args)


def test_full_packet_has_every_metric_and_nothing_missing():
    packet = full_packet()
    assert packet["instrument"] == "SBIN"
    assert packet["as_of"] == AS_OF.isoformat()
    assert set(packet["metrics"]) == {
        "volatility_annualized", "max_drawdown", "beta", "alpha_annualized",
        "sharpe", "tracking_error", "tracking_difference",
    }
    assert packet["missing"] == [] and packet["missing_reasons"] == {}


def test_values_match_the_construction_of_the_series():
    metrics = full_packet()["metrics"]
    assert metrics["beta"]["value"] == pytest.approx(1.5, abs=1e-4)
    assert metrics["alpha_annualized"]["value"] == pytest.approx(0.0004 * 252, abs=1e-3)
    assert metrics["beta"]["unit"] == "ratio" and metrics["volatility_annualized"]["unit"] == "fraction"
    assert metrics["beta"]["inputs"] == ["asset", "NIFTY 50"]
    assert metrics["alpha_annualized"]["inputs"] == ["asset", "NIFTY 50", RISKFREE_NAME]
    assert metrics["max_drawdown"]["value"] <= 0.0
    assert metrics["tracking_error"]["value"] > 0.0
    assert metrics["tracking_error"]["note"] == TRACKING_NOTE
    assert "note" not in metrics["beta"]


def test_window_text_reports_the_returns_used_and_the_date_range():
    metric = full_packet(window=61)["metrics"]["volatility_annualized"]
    assert metric["window"] == f"60 returns {DATES[-61]}..{DATES[-1]}"


def test_missing_benchmark_marks_beta_and_alpha_missing_with_reasons():
    packet = full_packet(benchmark=None)
    assert {"beta", "alpha_annualized"} <= set(packet["missing"])
    assert "beta" not in packet["metrics"] and "sharpe" in packet["metrics"]
    assert packet["missing_reasons"]["beta"] == "required series not provided"


def test_missing_riskfree_marks_alpha_and_sharpe_missing():
    packet = full_packet(riskfree=None)
    assert {"alpha_annualized", "sharpe"} <= set(packet["missing"])
    assert "beta" in packet["metrics"]


def test_riskfree_index_that_starts_late_shrinks_the_window_to_the_overlap():
    late_rate = {d: v for d, v in RATE.items() if d >= DATES[60]}
    packet = full_packet(riskfree=late_rate)
    assert "sharpe" in packet["metrics"]
    assert packet["metrics"]["sharpe"]["window"].startswith("59 returns")


def test_riskfree_index_with_too_little_overlap_marks_alpha_and_sharpe_missing():
    barely = {d: v for d, v in RATE.items() if d >= DATES[100]}
    packet = full_packet(riskfree=barely)
    assert {"alpha_annualized", "sharpe"} <= set(packet["missing"])
    assert "at least 20" in packet["missing_reasons"]["sharpe"]


def test_no_tracking_index_marks_tracking_metrics_missing():
    packet = full_packet(tracking_index=None)
    assert {"tracking_error", "tracking_difference"} <= set(packet["missing"])


def test_short_history_marks_metrics_missing_instead_of_raising():
    short = {d: v for d, v in list(ASSET.items())[:10]}
    packet = full_packet(asset=short)
    assert "volatility_annualized" in packet["missing"]
    assert "at least 20" in packet["missing_reasons"]["volatility_annualized"]
    assert "tracking_error" in packet["missing"]
    assert "tracking_difference" in packet["metrics"]


def test_packet_is_json_serialisable():
    import json

    json.dumps(full_packet())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_packets.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.metrics.packets'`.

- [ ] **Step 3: Write `src/athena/metrics/packets.py`**

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from athena.contracts import InsufficientData
from athena.metrics import stats
from athena.metrics.series import (
    Series,
    common_dates,
    levels_on,
    returns_from_levels,
    riskfree_returns,
)

DEFAULT_WINDOW = stats.TRADING_DAYS + 1  # 253 levels = 252 daily returns
RISKFREE_NAME = "NIFTY 1D RATE INDEX"
TRACKING_NOTE = (
    "ETF exchange closing prices against the index total-return series; includes premium/discount "
    "noise, so it overstates NAV-based tracking error (free NAV is not available)"
)


def _window_text(days: list[date]) -> str:
    return f"{len(days) - 1} returns {days[0]}..{days[-1]}"


def build_packet(
    instrument: str,
    as_of: datetime,
    asset: Series,
    benchmark: Series | None = None,
    benchmark_name: str | None = None,
    riskfree: Series | None = None,
    tracking_index: Series | None = None,
    tracking_index_name: str | None = None,
    window: int = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Metrics packet (TRD section 3): every figure a specialist may cite, computed in code.

    `asset` and `tracking_index` are price/NAV and total-return levels; `benchmark` should be a price
    index for stocks; `riskfree` is an accrual index (Nifty 1D Rate Index). Each metric uses the
    last `window` dates its own inputs share. A metric that cannot be computed is listed in
    `missing` with a reason, never silently dropped.
    """
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}

    def attempt(
        name: str, unit: str, inputs: list[str], needed: list[Series | None], compute: Callable, note: str | None = None
    ) -> None:
        if any(series is None for series in needed):
            reasons[name] = "required series not provided"
            return
        days = common_dates(*needed)[-window:]  # type: ignore[arg-type]
        try:
            value = compute(days)
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metrics[name] = {
            "value": round(float(value), 6),
            "unit": unit,
            "inputs": inputs,
            "window": _window_text(days) if len(days) > 1 else "0 returns",
            "source": "computed from daily closing levels",
        }
        if note:
            metrics[name]["note"] = note

    def asset_returns(days: list[date]) -> list[float]:
        return returns_from_levels(levels_on(asset, days))

    attempt(
        "volatility_annualized", "fraction", ["asset"], [asset],
        lambda days: stats.annualized_volatility(asset_returns(days)),
    )
    attempt(
        "max_drawdown", "fraction", ["asset"], [asset],
        lambda days: stats.max_drawdown(levels_on(asset, days)),
    )
    attempt(
        "beta", "ratio", ["asset", benchmark_name or "benchmark"], [asset, benchmark],
        lambda days: stats.beta(asset_returns(days), returns_from_levels(levels_on(benchmark, days))),
    )

    def alpha(days: list[date]) -> float:
        return stats.jensen_alpha(
            asset_returns(days),
            returns_from_levels(levels_on(benchmark, days)),  # type: ignore[arg-type]
            riskfree_returns(riskfree, days),  # type: ignore[arg-type]
        )

    attempt(
        "alpha_annualized", "fraction", ["asset", benchmark_name or "benchmark", RISKFREE_NAME],
        [asset, benchmark, riskfree], alpha,
    )

    def sharpe(days: list[date]) -> float:
        return stats.sharpe(asset_returns(days), riskfree_returns(riskfree, days))  # type: ignore[arg-type]

    attempt("sharpe", "ratio", ["asset", RISKFREE_NAME], [asset, riskfree], sharpe)
    attempt(
        "tracking_error", "fraction", ["asset", tracking_index_name or "tracking_index"],
        [asset, tracking_index],
        lambda days: stats.tracking_error(
            asset_returns(days), returns_from_levels(levels_on(tracking_index, days))  # type: ignore[arg-type]
        ),
        note=TRACKING_NOTE,
    )
    attempt(
        "tracking_difference", "fraction", ["asset", tracking_index_name or "tracking_index"],
        [asset, tracking_index],
        lambda days: stats.tracking_difference(
            levels_on(asset, days), levels_on(tracking_index, days)  # type: ignore[arg-type]
        ),
        note=TRACKING_NOTE,
    )

    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `194 passed, 33 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/metrics/packets.py tests/test_metrics_packets.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add metrics packet builder"
git push
```

---

### Task 6: Live metrics test, TRD update, graph

**Files:**
- Create: `tests/live/test_live_metrics.py`
- Modify: `TRD.md`

- [ ] **Step 1: Write `tests/live/test_live_metrics.py`**

```python
from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.clock import utc_now
from athena.loaders.index_close import PRICE_DATASET, RATE_DATASET, RATE_INDEX, IndexCloseLoader
from athena.loaders.index_tri import DATASET as TRI_DATASET
from athena.loaders.index_tri import IndexTriLoader
from athena.metrics.packets import build_packet
from athena.metrics.series import series_from_bars, series_from_store
from athena.store import DataStore
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live

LOOKBACK_DAYS = 420


@pytest.fixture(scope="module")
def world():
    since = ist_date(utc_now()) - timedelta(days=LOOKBACK_DAYS)
    store = DataStore()
    IndexCloseLoader(store).refresh(since=since)
    IndexCloseLoader(store, dataset=RATE_DATASET, default_index=RATE_INDEX).refresh(since=since)
    IndexTriLoader(store).refresh(since=since)
    adapter = JugaadPriceAdapter()
    return {
        "since": since,
        "price_index": series_from_store(store, PRICE_DATASET, "NIFTY 50", "close"),
        "rate": series_from_store(store, RATE_DATASET, RATE_INDEX, "close"),
        "tri": series_from_store(store, TRI_DATASET, "NIFTY 50", "tri"),
        "sbin": series_from_bars(adapter.fetch_ohlcv("SBIN", "1d", since=since)),
        "niftybees": series_from_bars(adapter.fetch_ohlcv("NIFTYBEES", "1d", since=since)),
    }


def test_live_stock_packet_is_complete_and_sane(world):
    packet = build_packet(
        "SBIN", utc_now(), world["sbin"],
        benchmark=world["price_index"], benchmark_name="NIFTY 50",
        riskfree=world["rate"],
    )
    metrics = packet["metrics"]
    print("SBIN", {k: v["value"] for k, v in metrics.items()}, packet["missing_reasons"])
    assert {"volatility_annualized", "max_drawdown", "beta", "alpha_annualized", "sharpe"} <= set(metrics)
    assert 0.10 < metrics["volatility_annualized"]["value"] < 0.70
    assert -0.80 < metrics["max_drawdown"]["value"] < 0.0
    assert 0.3 < metrics["beta"]["value"] < 2.2
    assert metrics["beta"]["window"].startswith("2")  # roughly a year of returns


def test_live_riskfree_is_a_plausible_overnight_rate(world):
    days = sorted(world["rate"])[-253:]
    growth = world["rate"][days[-1]] / world["rate"][days[0]] - 1.0
    years = (days[-1] - days[0]).days / 365.0
    annualised = (1.0 + growth) ** (1.0 / years) - 1.0
    print("overnight rate, annualised over the window:", round(annualised, 4))
    assert 0.02 < annualised < 0.10


def test_live_etf_tracking_error_against_the_index_tri(world):
    packet = build_packet(
        "NIFTYBEES", utc_now(), world["niftybees"],
        tracking_index=world["tri"], tracking_index_name="NIFTY 50 TRI",
    )
    metrics = packet["metrics"]
    print("NIFTYBEES", {k: v["value"] for k, v in metrics.items()}, packet["missing_reasons"])
    assert {"tracking_error", "tracking_difference"} <= set(metrics)
    assert metrics["tracking_error"]["value"] < 0.04  # market-price basis, see the packet note
    assert abs(metrics["tracking_difference"]["value"]) < 0.10
```

- [ ] **Step 2: Run the default and live suites**

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest --live tests/live/test_live_metrics.py -q -s
```

Expected: `194 passed, 36 skipped`, then `3 passed` with the printed SBIN and NIFTYBEES values near those under "Verified findings". If a live bound fails, a source has changed: investigate before touching an assertion.

- [ ] **Step 3: Update `TRD.md`**

1. §6.2 table: add these rows before the closing of the table:

```markdown
| niftyindices `NIFTY 1D RATE INDEX` via jugaad-data `index_raw` | Works; daily overnight-rate accrual index; ratio between two dates is the risk-free return (5.25% annualised over the last year) |
| niftyindices `NIFTY 50` price index via jugaad-data `index_raw` | Works; stock benchmark |
| FRED India short-rate series (`IRSTCB01INM156N`, `INTDSRINM193N`) | Stale (end 2023 / 2022); only `INDIRLTLT01STM` (10-year yield) is recent, with ~3-month lag; FRED resets plain Python `requests` connections (curl works) |
```

2. §9 open decision 6: replace its text with:

```markdown
6. ~~**India risk-free rate source**~~ **Resolved 5 Oct 2026:** the Nifty 1D Rate Index (an overnight-rate accrual index from niftyindices) supplies per-period risk-free returns as level ratios. It is an overnight proxy, slightly below a 91-day T-bill; no free T-bill series was found.
```

3. §2.13: append this paragraph at the end of the section:

```markdown
*Implemented in Plan 0d (stock and ETF metrics).* Stock benchmark is the Nifty 50 price index; ETF tracking index is the Nifty 50 TRI. ETF tracking error is measured on exchange closing prices, so it includes premium/discount noise and overstates NAV-based tracking error (NIFTYBEES: about 2.5% on this basis); the packet carries a note saying so. Mutual-fund metrics (expense drag, alpha vs category benchmark, overlap) are not implemented.
```

4. Revision history, add at the top of the list:

```markdown
- **Oct 5, 2026 (Phase 0d)** — Metrics engine implemented for stocks and ETFs; open decision 6 (risk-free rate) resolved (see `docs/superpowers/plans/2026-10-05-phase-0d-metrics-engine.md`).
```

- [ ] **Step 4: Commit, push, refresh graph**

```bash
git add tests/live/test_live_metrics.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live metrics test; document metrics engine and risk-free decision in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.13 -> task):** pure-function package with tests against known values (Tasks 3-5); alpha/beta by regression (Task 3, on excess returns), tracking error as annualised std of active returns and tracking difference (Task 3), volatility/drawdown/Sharpe (Task 3); metrics packet shape from TRD §3 (Task 5); risk-free rate (Task 2 + 4); coverage honesty: unavailable metrics listed in `missing` with reasons (Task 5). Not in this plan: expense drag, holdings overlap, category-benchmark alpha (mutual funds on hold); evaluation harness (Plan 0e).

**Deviations from TRD §2.13 as first written:** the TRD said alpha/beta use a 3-year window and "net-of-fee fund returns versus the category benchmark TRI"; this plan uses a 1-year (252-return) default window for stocks and ETFs against the Nifty 50 price index (stocks) and TRI (ETF tracking). The 3-year fund window applies when mutual funds are built.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `Series`, `build_packet`, `riskfree_returns`, `IndexCloseLoader`, and the dataset constants match across Tasks 1-6. Test totals: 152 (after 0c) + 3 + 9 + 12 + 8 + 10 = 194 passed; skipped 33 + 3 live = 36.

**Verified before writing:** all offline tests passed in a scratch copy (194), and the 3 live tests passed against real NSE data, including a diagnostic that ruled out a date-alignment bug in the ETF tracking-error figure.
