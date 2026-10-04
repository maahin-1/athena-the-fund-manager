# Phase 0b — Equity and ETF Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the free equity/ETF data sources into the Phase 0a core: an NSE trading-holiday calendar that fixes the freshness check, NSE equity and ETF master lists, an OHLCV price adapter pair (jugaad-data, Yahoo) with a calendar-aware fallback chain, and an index total-return (TRI) loader.

**Architecture:** Each source is a small class that takes its network call as an injected function (default = the real library), so tests run offline with fakes and a separate opt-in `--live` suite proves the real sources still work. Loaders write `Record`s into the `DataStore`; adapters return `Bar`s; everything fails loudly with the Phase 0a error classes.

**Tech Stack:** Python >= 3.11, DuckDB, requests, jugaad-data, yfinance, pytest.

**Spec:** `TRD.md` §2.11, §5 (freshness, fallback chains), §6/6.1 (sources); source behaviour below was verified against the live sources on 5 Oct 2026.

**Plan series:** 0a (done) -> **0b (this plan)** -> 0c instrument resolver -> 0d metrics engine + evaluation harness. **On hold by request:** everything mutual-fund (AMFI NAV adapter, TER capture, holdings parsers).

**Suggested models:** all tasks Sonnet at medium effort. Escalate a task one lane only after its tests fail twice. Task 8 (live suite) may need Opus if a real source has changed shape.

## Verified source behaviour (5 Oct 2026) — read before coding

- **Holidays:** `jugaad_data.nse.NSELive().holiday_list()` returns a dict keyed by segment (`CM` = equities); each row has `tradingDate` like `"02-Oct-2026"`. Only the current calendar year is returned. 2 Oct 2026 (Friday) is a trading holiday.
- **jugaad-data prices:** `stock_df(symbol, from_date, to_date, series="EQ")` returns a DataFrame with naive `DATE` values that are **IST midnight expressed in UTC** (e.g. `2026-09-30 18:30` is the 1 Oct session). Columns used: `OPEN HIGH LOW CLOSE VOLUME`. Prices are unadjusted.
- **Yahoo prices:** `yf.Ticker("SBIN.NS").history(...)` returns tz-aware `Asia/Kolkata` midnight dates and **invents rows on market holidays** (flat price, zero volume, e.g. NIFTYBEES on 2 Oct 2026). Use `auto_adjust=False` so prices match NSE's unadjusted series.
- **NSE masters:** `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` (headers have leading spaces, e.g. `" SERIES"`) and `.../eq_etfseclist.csv` (columns include `Symbol, SecurityName, ISINNumber, Underlying Asset, ETF Underlying, Underlying Key`). Both download with a browser-like `User-Agent`.
- **Index TRI:** `NSEIndexHistory().index_tri_raw(name, index_name, from_date, to_date)` returns rows `{"Date": "01 Oct 2026", "TotalReturnsIndex": "34070.01", "NTR_Value": "29584.52", ...}`. (`nsepython.index_total_returns` is broken: its endpoint now returns HTML. Do not use it.)
- **Broken, out of scope here:** NSE live quotes and the option chain fail in both jugaad-data and nsepython. Adapters in this plan declare `fetch_quote: False`.

## Global Constraints

- Python `>=3.11`; free data only; no network in the default test run (live tests are opt-in via `--live`).
- Every datetime is timezone-aware UTC; "trading date" always means the **IST** calendar date (`ist_date`).
- Fail loud: empty or reshaped source data raises `EmptyRefreshError` / `SchemaChangedError`, never returns silently.
- Prices are unadjusted NSE prices.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` (modify) | Add runtime dependencies |
| `src/athena/clock.py` | `utc_now()` default clock |
| `src/athena/contracts.py` (modify) | Add `UnsupportedOperation` |
| `src/athena/fallback.py` (modify) | Treat empty results as failures |
| `src/athena/trading_calendar.py` | `IST`, `ist_date`, `TradingCalendar` |
| `src/athena/freshness.py` (modify) | Optional calendar for business-day age; new dataset limits |
| `src/athena/loaders/__init__.py`, `loaders/nse_holidays.py` | Holiday loader, `load_calendar` |
| `src/athena/loaders/nse_masters.py` | NSE equity and ETF master loader |
| `src/athena/loaders/index_tri.py` | Index TRI loader (incremental) |
| `src/athena/adapters/__init__.py`, `adapters/prices.py` | Bar normalisation, price adapters, `ohlcv_chain` |
| `tests/conftest.py`, `tests/live/test_live_sources.py` | Opt-in live suite |
| `tests/test_*.py` | One test module per source module |

All commands run from the project root in Git Bash.

---

### Task 1: Dependencies, clock, `UnsupportedOperation`, empty-result fallback

**Files:**
- Modify: `pyproject.toml`, `src/athena/contracts.py`, `src/athena/fallback.py`, `tests/test_contracts.py`, `tests/test_fallback.py`
- Create: `src/athena/clock.py`, `tests/test_clock.py`

**Interfaces:**
- Produces: `athena.clock.utc_now() -> datetime` (tz-aware UTC); `athena.contracts.UnsupportedOperation(AthenaError)`; `FallbackChain.run` now counts an empty sized result (`[]`, `{}`, `""`) as `"returned no data"` exactly like `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_clock.py`:

```python
from datetime import timedelta

from athena.clock import utc_now


def test_utc_now_is_timezone_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
```

In `tests/test_contracts.py`, change the import block ending `    StaleDataError,\n)` to:

```python
    StaleDataError,
    UnsupportedOperation,
)
```

and change the parametrize line to:

```python
@pytest.mark.parametrize(
    "exc", [StaleDataError, EmptyRefreshError, SchemaChangedError, AllSourcesFailed, UnsupportedOperation]
)
```

Append to `tests/test_fallback.py`:

```python
def test_empty_sequence_counts_as_failure():
    chain = FallbackChain([("a", ok([])), ("b", ok([1]))])
    result = chain.run()
    assert result.source == "b"
    assert result.failures == (("a", "returned no data"),)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_clock.py tests/test_contracts.py tests/test_fallback.py -q`
Expected: FAIL (`ModuleNotFoundError: athena.clock`, `ImportError: UnsupportedOperation`, and the new fallback test).

- [ ] **Step 3: Implement**

`src/athena/clock.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

In `src/athena/contracts.py`, after the `AllSourcesFailed` class add:

```python
class UnsupportedOperation(AthenaError):
    """An adapter was asked for a capability its describe() map declares absent."""
```

In `src/athena/fallback.py`, replace

```python
            if value is None:
                failures.append((name, "returned no data"))
                continue
```

with

```python
            if value is None or (hasattr(value, "__len__") and len(value) == 0):
                failures.append((name, "returned no data"))
                continue
```

In `pyproject.toml`, replace the `dependencies` line with:

```toml
dependencies = ["duckdb>=1.0", "requests>=2.31", "jugaad-data>=0.35", "yfinance>=1.0"]
```

- [ ] **Step 4: Install and run the full suite**

```bash
.venv/Scripts/python -m pip install -q -e ".[dev]"
.venv/Scripts/python -m pytest -q
```

Expected: `52 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add pyproject.toml src/athena/clock.py src/athena/contracts.py src/athena/fallback.py tests/test_clock.py tests/test_contracts.py tests/test_fallback.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add clock, UnsupportedOperation, and empty-result fallback; add data deps"
git push
```

---

### Task 2: Trading calendar

**Files:**
- Create: `src/athena/trading_calendar.py`
- Test: `tests/test_trading_calendar.py`

**Interfaces:**
- Produces (`athena.trading_calendar`):
  - `IST: timezone` (UTC+05:30)
  - `ist_date(moment: datetime) -> date` (raises `ValueError("moment must be timezone-aware")` for naive)
  - `@dataclass(frozen=True) class TradingCalendar(holidays: frozenset[date])` with `from_dates(dates: Iterable[date]) -> TradingCalendar`, `is_trading_day(day: date) -> bool` (weekday and not a holiday), `trading_days_between(start: date, end: date) -> int` (trading days in the half-open interval `(start, end]`)

- [ ] **Step 1: Write the failing tests `tests/test_trading_calendar.py`**

```python
from datetime import date, datetime, timezone

import pytest

from athena.trading_calendar import IST, TradingCalendar, ist_date

UTC = timezone.utc
CAL = TradingCalendar.from_dates([date(2026, 10, 2), date(2026, 10, 20)])


def test_weekends_and_holidays_are_not_trading_days():
    assert not CAL.is_trading_day(date(2026, 10, 3))  # Saturday
    assert not CAL.is_trading_day(date(2026, 10, 2))  # Friday holiday
    assert CAL.is_trading_day(date(2026, 10, 1))
    assert CAL.is_trading_day(date(2026, 10, 5))


def test_trading_days_between_skips_holiday_and_weekend():
    # Thu 1 Oct -> Mon 5 Oct: Fri is a holiday, Sat and Sun are weekend, Mon counts.
    assert CAL.trading_days_between(date(2026, 10, 1), date(2026, 10, 5)) == 1
    assert CAL.trading_days_between(date(2026, 10, 1), date(2026, 10, 6)) == 2


def test_same_day_is_zero():
    assert CAL.trading_days_between(date(2026, 10, 5), date(2026, 10, 5)) == 0


def test_ist_date_uses_india_time():
    assert ist_date(datetime(2026, 9, 30, 18, 30, tzinfo=UTC)) == date(2026, 10, 1)
    assert ist_date(datetime(2026, 10, 1, 12, 0, tzinfo=UTC)) == date(2026, 10, 1)
    assert ist_date(datetime(2026, 10, 1, 0, 0, tzinfo=IST)) == date(2026, 10, 1)


def test_ist_date_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware"):
        ist_date(datetime(2026, 10, 1))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_trading_calendar.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.trading_calendar'`.

- [ ] **Step 3: Write `src/athena/trading_calendar.py`**

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def ist_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    return moment.astimezone(IST).date()


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset[date]

    @classmethod
    def from_dates(cls, dates: Iterable[date]) -> TradingCalendar:
        return cls(frozenset(dates))

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def trading_days_between(self, start: date, end: date) -> int:
        count = 0
        day = start
        while day < end:
            day += timedelta(days=1)
            if self.is_trading_day(day):
                count += 1
        return count
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_trading_calendar.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/trading_calendar.py tests/test_trading_calendar.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add IST trading calendar"
git push
```

---

### Task 3: Calendar-aware freshness and new dataset limits

**Files:**
- Modify: `src/athena/freshness.py` (replace whole file), `tests/test_freshness.py` (append)

**Interfaces:**
- Consumes: `athena.trading_calendar.TradingCalendar`, `ist_date`.
- Produces: `check_fresh(dataset, as_of, now, limits=DEFAULT_LIMITS, calendar: TradingCalendar | None = None)`. With a calendar, `business_days` ages are trading days in IST (`calendar.trading_days_between(ist_date(as_of), ist_date(now))`); without one the old weekday-only/UTC behaviour is unchanged. New `DEFAULT_LIMITS` entries: `calendar.nse_holidays` = `Limit(120, "days")`, `master.nse_equity` = `Limit(7, "days")`, `master.nse_etf` = `Limit(7, "days")`.

- [ ] **Step 1: Append the failing tests to `tests/test_freshness.py`**

Add these imports at the top of the file (alongside the existing ones): `from datetime import date` and `from athena.trading_calendar import TradingCalendar`. Then append:

```python
CAL = TradingCalendar.from_dates([date(2026, 10, 2)])


def test_holiday_calendar_prevents_false_stale_on_monday():
    nav_as_of = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)  # Thu 18:00 IST
    now = datetime(2026, 10, 5, 3, 30, tzinfo=UTC)  # Mon 09:00 IST
    with pytest.raises(StaleDataError):
        check_fresh("mf.nav", nav_as_of, now)  # weekday-only age = 2 (counts the Fri holiday)
    check_fresh("mf.nav", nav_as_of, now, calendar=CAL)  # trading-day age = 1


def test_calendar_still_flags_genuinely_stale_data():
    with pytest.raises(StaleDataError, match="2 business days"):
        check_fresh(
            "mf.nav",
            datetime(2026, 10, 1, 12, 30, tzinfo=UTC),
            datetime(2026, 10, 6, 3, 30, tzinfo=UTC),
            calendar=CAL,
        )


def test_new_dataset_limits():
    assert DEFAULT_LIMITS["calendar.nse_holidays"] == Limit(120, "days")
    assert DEFAULT_LIMITS["master.nse_equity"] == Limit(7, "days")
    assert DEFAULT_LIMITS["master.nse_etf"] == Limit(7, "days")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_freshness.py -q`
Expected: FAIL (`TypeError: check_fresh() got an unexpected keyword argument 'calendar'`, `KeyError` for the new limits).

- [ ] **Step 3: Replace `src/athena/freshness.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from athena.contracts import StaleDataError
from athena.trading_calendar import TradingCalendar, ist_date


@dataclass(frozen=True)
class Limit:
    amount: int
    unit: str  # "minutes" | "business_days" | "days"


# Proposed defaults from TRD section 5; tune from canary data.
DEFAULT_LIMITS: dict[str, Limit | None] = {
    "quote.intraday": Limit(15, "minutes"),
    "option_chain": Limit(15, "minutes"),
    "price.eod": Limit(1, "business_days"),
    "mf.nav": Limit(1, "business_days"),
    "index.tri": Limit(1, "business_days"),
    "mf.ter": Limit(7, "days"),
    "mf.holdings": Limit(45, "days"),
    "equity.fundamentals": Limit(136, "days"),  # one quarter (91) + 45 days
    "bond.price": None,  # illiquid: show the last-trade date instead
    "calendar.nse_holidays": Limit(120, "days"),
    "master.nse_equity": Limit(7, "days"),
    "master.nse_etf": Limit(7, "days"),
}


def age_in_business_days(as_of: datetime, now: datetime) -> int:
    """Weekdays in the half-open interval (as_of date, now date]. Holidays are ignored."""
    day = as_of.date()
    end = now.date()
    count = 0
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def check_fresh(
    dataset: str,
    as_of: datetime,
    now: datetime,
    limits: dict[str, Limit | None] = DEFAULT_LIMITS,
    calendar: TradingCalendar | None = None,
) -> None:
    if as_of.tzinfo is None or now.tzinfo is None:
        raise ValueError("as_of and now must be timezone-aware")
    if dataset not in limits:
        raise ValueError(f"no staleness limit declared for dataset {dataset!r}")
    limit = limits[dataset]
    if limit is None:
        return

    if limit.unit == "minutes":
        age = int((now - as_of).total_seconds() // 60)
        age_text, limit_text = f"{age} minutes", f"{limit.amount} minutes"
    elif limit.unit == "business_days":
        if calendar is not None:
            age = calendar.trading_days_between(ist_date(as_of), ist_date(now))
        else:
            age = age_in_business_days(as_of, now)
        age_text, limit_text = f"{age} business days", f"{limit.amount} business days"
    elif limit.unit == "days":
        age = (now - as_of).days
        age_text, limit_text = f"{age} days", f"{limit.amount} days"
    else:
        raise ValueError(f"unknown limit unit {limit.unit!r}")

    if age > limit.amount:
        raise StaleDataError(f"{dataset} data is {age_text} old, limit is {limit_text}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `60 passed` (the 8 original freshness tests still pass unchanged).

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/freshness.py tests/test_freshness.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: make freshness check holiday-aware via trading calendar"
git push
```

---

### Task 4: NSE holiday loader

**Files:**
- Create: `src/athena/loaders/__init__.py` (empty file), `src/athena/loaders/nse_holidays.py`
- Test: `tests/test_nse_holidays.py`

**Interfaces:**
- Consumes: `DataStore`, `Record`, `RefreshResult`, `EmptyRefreshError`, `SchemaChangedError`, `TradingCalendar`, `utc_now`.
- Produces (`athena.loaders.nse_holidays`):
  - `DATASET = "calendar.nse_holidays"`, `SOURCE = "nse.holiday_list"`
  - `parse_holidays(payload: dict, segment: str = "CM") -> list[date]` (sorted, unique; `SchemaChangedError` for a missing segment or malformed row; `EmptyRefreshError` for an empty segment)
  - `class NseHolidayLoader(store, fetch=default_fetch, clock=utc_now)` implementing `BatchLoader`: `refresh(dataset=DATASET, since=None) -> RefreshResult` writes one `Record` per calendar year (`key=str(year)`, `payload={"dates": ["2026-10-02", ...]}`), `rows_written` = number of years; `read(dataset, key, **params) -> Record` raises `EmptyRefreshError` if nothing is stored
  - `load_calendar(store: DataStore, years: Iterable[int]) -> TradingCalendar` (raises `EmptyRefreshError` for a year with no stored record)

- [ ] **Step 1: Write the failing tests `tests/test_nse_holidays.py`**

```python
from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.nse_holidays import DATASET, NseHolidayLoader, load_calendar, parse_holidays
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)


def row(day):
    return {"tradingDate": day, "weekDay": "x", "description": "d", "Sr_no": 1}


PAYLOAD = {
    "CM": [row("20-Oct-2026"), row("02-Oct-2026"), row("25-Dec-2026"), row("01-Jan-2027")],
    "FO": [row("03-Mar-2026")],
}


def make_loader(store, payload=PAYLOAD):
    return NseHolidayLoader(store, fetch=lambda: payload, clock=lambda: NOW)


def test_parse_returns_sorted_unique_cm_dates():
    assert parse_holidays(PAYLOAD) == [
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 12, 25),
        date(2027, 1, 1),
    ]


def test_missing_segment_is_schema_change():
    with pytest.raises(SchemaChangedError, match="CM"):
        parse_holidays({"FO": [row("03-Mar-2026")]})


def test_empty_segment_is_empty_refresh():
    with pytest.raises(EmptyRefreshError):
        parse_holidays({"CM": []})


@pytest.mark.parametrize("bad", [{"tradingDate": "2026-10-02"}, {"x": 1}])
def test_malformed_row_is_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_holidays({"CM": [bad]})


def test_refresh_writes_one_record_per_year():
    store = DataStore()
    result = make_loader(store).refresh()
    assert result.rows_written == 2
    assert result.dataset == DATASET
    assert store.latest(DATASET, "2026").payload == {"dates": ["2026-10-02", "2026-10-20", "2026-12-25"]}
    assert store.latest(DATASET, "2027").payload == {"dates": ["2027-01-01"]}
    assert store.latest(DATASET, "2026").as_of == NOW


def test_read_returns_record_and_missing_raises():
    store = DataStore()
    loader = make_loader(store)
    with pytest.raises(EmptyRefreshError):
        loader.read(DATASET, "2026")
    loader.refresh()
    assert loader.read(DATASET, "2026").source == "nse.holiday_list"


def test_load_calendar_across_years():
    store = DataStore()
    make_loader(store).refresh()
    cal = load_calendar(store, [2026, 2027])
    assert not cal.is_trading_day(date(2026, 10, 2))
    assert not cal.is_trading_day(date(2027, 1, 1))
    assert cal.is_trading_day(date(2026, 10, 1))


def test_load_calendar_missing_year_raises():
    store = DataStore()
    make_loader(store).refresh()
    with pytest.raises(EmptyRefreshError, match="2028"):
        load_calendar(store, [2028])


def test_wrong_dataset_is_rejected():
    with pytest.raises(ValueError, match="calendar.nse_holidays"):
        make_loader(DataStore()).refresh("other.dataset")


def test_loader_satisfies_batchloader_protocol():
    assert isinstance(make_loader(DataStore()), BatchLoader)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_nse_holidays.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.loaders'`.

- [ ] **Step 3: Create `src/athena/loaders/__init__.py` (empty) and write `src/athena/loaders/nse_holidays.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar

DATASET = "calendar.nse_holidays"
SOURCE = "nse.holiday_list"


def default_fetch() -> dict[str, Any]:
    from jugaad_data.nse import NSELive

    return NSELive().holiday_list()


def parse_holidays(payload: dict[str, Any], segment: str = "CM") -> list[date]:
    if segment not in payload:
        raise SchemaChangedError(f"holiday payload has no segment {segment!r}")
    rows = payload[segment]
    if not rows:
        raise EmptyRefreshError(f"holiday payload segment {segment!r} is empty")
    try:
        return sorted({datetime.strptime(row["tradingDate"], "%d-%b-%Y").date() for row in rows})
    except (KeyError, ValueError, TypeError) as exc:
        raise SchemaChangedError(f"unexpected holiday row shape: {exc!r}") from exc


class NseHolidayLoader:
    def __init__(
        self,
        store: DataStore,
        fetch: Callable[[], dict[str, Any]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {DATASET: {"cadence": "yearly", "source": SOURCE}}

    def refresh(self, dataset: str = DATASET, since: Any = None) -> RefreshResult:
        if dataset != DATASET:
            raise ValueError(f"this loader only provides {DATASET!r}, got {dataset!r}")
        days = parse_holidays(self._fetch())
        now = self._clock()
        by_year: dict[int, list[date]] = {}
        for day in days:
            by_year.setdefault(day.year, []).append(day)
        for year, dates in by_year.items():
            self._store.put(
                Record(DATASET, str(year), now, SOURCE, {"dates": [d.isoformat() for d in dates]})
            )
        return RefreshResult(DATASET, len(by_year), now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record


def load_calendar(store: DataStore, years: Iterable[int]) -> TradingCalendar:
    dates: set[date] = set()
    for year in years:
        record = store.latest(DATASET, str(year))
        if record is None:
            raise EmptyRefreshError(f"no {DATASET} data for {year}; run the holiday loader first")
        dates.update(date.fromisoformat(text) for text in record.payload["dates"])
    return TradingCalendar(frozenset(dates))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `71 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/loaders tests/test_nse_holidays.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add NSE holiday loader and calendar builder"
git push
```

---

### Task 5: NSE equity and ETF master loader

**Files:**
- Create: `src/athena/loaders/nse_masters.py`
- Test: `tests/test_nse_masters.py`

**Interfaces:**
- Consumes: `DataStore`, `Record`, `RefreshResult`, errors, `utc_now`.
- Produces (`athena.loaders.nse_masters`):
  - `EQUITY_DATASET = "master.nse_equity"`, `ETF_DATASET = "master.nse_etf"`, `SOURCE = "nse.archives"`
  - `parse_master(text: str, key_column: str, columns: dict[str, str]) -> dict[str, dict[str, str]]` (header names are whitespace-stripped; `SchemaChangedError` if a needed column is missing; `EmptyRefreshError` if no rows)
  - `class NseMasterLoader(store, fetch_text=default_fetch_text, clock=utc_now)` implementing `BatchLoader`; `refresh(dataset, since=None)` writes one `Record` per symbol; equity payload `{"name","series","isin","listing_date"}`, ETF payload `{"name","isin","underlying","underlying_class","underlying_key"}`; `read` as in Task 4.

- [ ] **Step 1: Write the failing tests `tests/test_nse_masters.py`**

```python
from datetime import datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.nse_masters import (
    EQUITY_DATASET,
    EQUITY_URL,
    ETF_DATASET,
    ETF_URL,
    NseMasterLoader,
)
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)

EQUITY_CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "SBIN,State Bank of India,EQ,01-MAR-1995,1,1,INE062A01020,1\n"
    "20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n"
)
ETF_CSV = (
    "Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue,ETF Underlying,Underlying Key\r\n"
    "NIFTYBEES,Nifty 50,NIPINDETFNIFTYBEES,08-Jan-02,1,INF204KB14I2,1,EQUITY,Nifty 50\r\n"
)


def make_loader(store, text, urls=None):
    def fetch_text(url):
        if urls is not None:
            urls.append(url)
        return text

    return NseMasterLoader(store, fetch_text=fetch_text, clock=lambda: NOW)


def test_equity_refresh_writes_a_record_per_symbol_and_tolerates_header_spaces():
    store = DataStore()
    result = make_loader(store, EQUITY_CSV).refresh(EQUITY_DATASET)
    assert result.rows_written == 2
    assert store.latest(EQUITY_DATASET, "SBIN").payload == {
        "name": "State Bank of India",
        "series": "EQ",
        "isin": "INE062A01020",
        "listing_date": "01-MAR-1995",
    }
    assert store.latest(EQUITY_DATASET, "SBIN").as_of == NOW


def test_etf_refresh_keeps_underlying_index():
    store = DataStore()
    make_loader(store, ETF_CSV).refresh(ETF_DATASET)
    assert store.latest(ETF_DATASET, "NIFTYBEES").payload == {
        "name": "NIPINDETFNIFTYBEES",
        "isin": "INF204KB14I2",
        "underlying": "Nifty 50",
        "underlying_class": "EQUITY",
        "underlying_key": "Nifty 50",
    }


def test_each_dataset_uses_its_own_url():
    urls = []
    make_loader(DataStore(), EQUITY_CSV, urls).refresh(EQUITY_DATASET)
    make_loader(DataStore(), ETF_CSV, urls).refresh(ETF_DATASET)
    assert urls == [EQUITY_URL, ETF_URL]


def test_missing_column_is_schema_change():
    bad = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\nSBIN,State Bank of India,EQ,01-MAR-1995\n"
    with pytest.raises(SchemaChangedError, match="ISIN NUMBER"):
        make_loader(DataStore(), bad).refresh(EQUITY_DATASET)


def test_header_only_and_blank_files_are_empty_refresh():
    header = EQUITY_CSV.splitlines()[0] + "\n"
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), header).refresh(EQUITY_DATASET)
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), "").refresh(EQUITY_DATASET)


def test_unknown_dataset_is_rejected():
    with pytest.raises(ValueError, match="master.nse_equity"):
        make_loader(DataStore(), EQUITY_CSV).refresh("master.other")


def test_read_missing_raises_and_present_returns():
    store = DataStore()
    loader = make_loader(store, EQUITY_CSV)
    with pytest.raises(EmptyRefreshError):
        loader.read(EQUITY_DATASET, "SBIN")
    loader.refresh(EQUITY_DATASET)
    assert loader.read(EQUITY_DATASET, "SBIN").payload["isin"] == "INE062A01020"


def test_describe_lists_both_datasets_and_protocol_holds():
    loader = make_loader(DataStore(), EQUITY_CSV)
    assert set(loader.describe()) == {EQUITY_DATASET, ETF_DATASET}
    assert isinstance(loader, BatchLoader)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_nse_masters.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.loaders.nse_masters'`.

- [ ] **Step 3: Write `src/athena/loaders/nse_masters.py`**

```python
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore

EQUITY_DATASET = "master.nse_equity"
ETF_DATASET = "master.nse_etf"
SOURCE = "nse.archives"
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
ETF_URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"

_SPECS: dict[str, tuple[str, str, dict[str, str]]] = {
    EQUITY_DATASET: (
        EQUITY_URL,
        "SYMBOL",
        {
            "name": "NAME OF COMPANY",
            "series": "SERIES",
            "isin": "ISIN NUMBER",
            "listing_date": "DATE OF LISTING",
        },
    ),
    ETF_DATASET: (
        ETF_URL,
        "Symbol",
        {
            "name": "SecurityName",
            "isin": "ISINNumber",
            "underlying": "Underlying Asset",
            "underlying_class": "ETF Underlying",
            "underlying_key": "Underlying Key",
        },
    ),
}


def default_fetch_text(url: str) -> str:
    import requests

    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return response.text


def parse_master(
    text: str, key_column: str, columns: dict[str, str]
) -> dict[str, dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise EmptyRefreshError("master file is empty")
    present = {name.strip() for name in reader.fieldnames}
    missing = [col for col in (key_column, *columns.values()) if col not in present]
    if missing:
        raise SchemaChangedError(f"master file is missing columns {missing}")
    parsed: dict[str, dict[str, str]] = {}
    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        key = row.get(key_column, "")
        if not key:
            continue
        parsed[key] = {field: row[col] for field, col in columns.items()}
    if not parsed:
        raise EmptyRefreshError("master file has no rows")
    return parsed


class NseMasterLoader:
    def __init__(
        self,
        store: DataStore,
        fetch_text: Callable[[str], str] = default_fetch_text,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch_text = fetch_text
        self._clock = clock

    def describe(self) -> dict:
        return {
            dataset: {"cadence": "weekly", "source": SOURCE, "url": spec[0]}
            for dataset, spec in _SPECS.items()
        }

    def refresh(self, dataset: str, since: Any = None) -> RefreshResult:
        if dataset not in _SPECS:
            raise ValueError(f"unknown dataset {dataset!r}; expected one of {sorted(_SPECS)}")
        url, key_column, columns = _SPECS[dataset]
        parsed = parse_master(self._fetch_text(url), key_column, columns)
        now = self._clock()
        for symbol, payload in parsed.items():
            self._store.put(Record(dataset, symbol, now, SOURCE, payload))
        return RefreshResult(dataset, len(parsed), now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `79 passed`. (The "unknown dataset" test matches `master.nse_equity` because the error message lists the expected datasets.)

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/loaders/nse_masters.py tests/test_nse_masters.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add NSE equity and ETF master loader"
git push
```

---

### Task 6: Price adapters and calendar-aware fallback chain

**Files:**
- Create: `src/athena/adapters/__init__.py` (empty file), `src/athena/adapters/prices.py`
- Test: `tests/test_prices.py`

**Interfaces:**
- Consumes: `Bar`, `SchemaChangedError`, `UnsupportedOperation`, `FallbackChain`, `TradingCalendar`, `ist_date`, `utc_now`.
- Produces (`athena.adapters.prices`):
  - `bars_from_jugaad(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]` (ascending; `source="jugaad"`; `SchemaChangedError` for missing columns **or** a `DATE` that is not 18:30 — the verified "IST midnight as naive UTC" convention)
  - `bars_from_yahoo(rows, symbol, as_of) -> list[Bar]` (ascending; `source="yahoo"`; tz-aware dates converted to UTC)
  - `drop_non_trading_days(bars: list[Bar], calendar: TradingCalendar) -> list[Bar]`
  - `class JugaadPriceAdapter(fetch_rows=None, clock=None)` and `class YahooPriceAdapter(...)`, both with `name`, `describe()` (`fetch_ohlcv: True, fetch_quote: False`), `fetch_quote` -> `UnsupportedOperation`, `fetch_ohlcv(symbol, timeframe, since=None, limit=None, **params)` (only `"1d"`; `params["until"]` optional; default window = 30 days ending today in the clock's UTC date)
  - `ohlcv_chain(adapters: Sequence, calendar: TradingCalendar) -> FallbackChain`; `chain.run(symbol, since=None, limit=None, until=None)`; holiday/weekend bars are removed, then `limit` keeps the newest N, and an empty result triggers fallback to the next adapter.

- [ ] **Step 1: Write the failing tests `tests/test_prices.py`**

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.adapters.prices import (
    JugaadPriceAdapter,
    YahooPriceAdapter,
    bars_from_jugaad,
    bars_from_yahoo,
    drop_non_trading_days,
    ohlcv_chain,
)
from athena.contracts import AllSourcesFailed, DataAdapter, SchemaChangedError, UnsupportedOperation
from athena.trading_calendar import IST, TradingCalendar, ist_date

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
CAL = TradingCalendar.from_dates([date(2026, 10, 2)])


def jrow(naive, close, volume=1000):
    return {"DATE": naive, "OPEN": close - 1, "HIGH": close + 1, "LOW": close - 2, "CLOSE": close, "VOLUME": volume}


def yrow(day, close, volume=1000):
    return {
        "Date": datetime(2026, 10, day, tzinfo=IST),
        "Open": close,
        "High": close,
        "Low": close,
        "Close": close,
        "Volume": volume,
    }


def jugaad(rows=None, error=None, calls=None):
    def fetch(symbol, start, end):
        if calls is not None:
            calls.append((symbol, start, end))
        if error:
            raise error
        return rows

    return JugaadPriceAdapter(fetch_rows=fetch, clock=lambda: NOW)


def yahoo(rows=None, error=None):
    def fetch(symbol, start, end):
        if error:
            raise error
        return rows

    return YahooPriceAdapter(fetch_rows=fetch, clock=lambda: NOW)


def test_jugaad_dates_are_ist_midnight_stored_as_naive_utc():
    bars = bars_from_jugaad([jrow(datetime(2026, 9, 30, 18, 30), 954.1, 7390452)], "SBIN", NOW)
    bar = bars[0]
    assert bar.timestamp == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert ist_date(bar.timestamp) == date(2026, 10, 1)
    assert (bar.symbol, bar.close, bar.volume, bar.source, bar.as_of) == ("SBIN", 954.1, 7390452.0, "jugaad", NOW)


def test_jugaad_bars_are_sorted_ascending():
    rows = [jrow(datetime(2026, 9, 30, 18, 30), 2.0), jrow(datetime(2026, 9, 29, 18, 30), 1.0)]
    assert [b.close for b in bars_from_jugaad(rows, "X", NOW)] == [1.0, 2.0]


def test_jugaad_missing_column_is_schema_change():
    row = jrow(datetime(2026, 9, 30, 18, 30), 1.0)
    del row["VOLUME"]
    with pytest.raises(SchemaChangedError, match="VOLUME"):
        bars_from_jugaad([row], "X", NOW)


def test_jugaad_unexpected_timestamp_convention_fails_loud():
    with pytest.raises(SchemaChangedError, match="timestamp"):
        bars_from_jugaad([jrow(datetime(2026, 10, 1, 0, 0), 1.0)], "X", NOW)


def test_yahoo_dates_convert_to_utc_and_keep_ist_trading_date():
    bar = bars_from_yahoo([yrow(1, 954.1)], "SBIN", NOW)[0]
    assert bar.timestamp.utcoffset() == timedelta(0)
    assert ist_date(bar.timestamp) == date(2026, 10, 1)
    assert (bar.source, bar.close) == ("yahoo", 954.1)


def test_yahoo_missing_column_is_schema_change():
    row = yrow(1, 1.0)
    del row["Close"]
    with pytest.raises(SchemaChangedError, match="Close"):
        bars_from_yahoo([row], "X", NOW)


def test_drop_non_trading_days_removes_holiday_and_weekend_rows():
    bars = bars_from_yahoo([yrow(1, 1.0), yrow(2, 2.0, 0), yrow(3, 3.0), yrow(5, 5.0)], "X", NOW)
    kept = drop_non_trading_days(bars, CAL)
    assert [ist_date(b.timestamp) for b in kept] == [date(2026, 10, 1), date(2026, 10, 5)]


def test_adapter_default_window_and_limit():
    calls = []
    rows = [jrow(datetime(2026, 9, 29, 18, 30), 950.0), jrow(datetime(2026, 9, 30, 18, 30), 954.1)]
    bars = jugaad(rows, calls=calls).fetch_ohlcv("SBIN", "1d", limit=1)
    assert calls == [("SBIN", date(2026, 9, 5), date(2026, 10, 5))]
    assert [b.close for b in bars] == [954.1]


def test_adapter_explicit_since_and_until():
    calls = []
    jugaad([jrow(datetime(2026, 9, 29, 18, 30), 1.0)], calls=calls).fetch_ohlcv(
        "SBIN", "1d", since=date(2026, 9, 21), until=date(2026, 10, 3)
    )
    assert calls == [("SBIN", date(2026, 9, 21), date(2026, 10, 3))]


def test_adapters_reject_other_timeframes_and_quotes():
    adapter = jugaad([])
    with pytest.raises(UnsupportedOperation, match="1d"):
        adapter.fetch_ohlcv("SBIN", "1h")
    with pytest.raises(UnsupportedOperation, match="quotes"):
        adapter.fetch_quote("SBIN")


def test_adapters_describe_capabilities_and_satisfy_protocol():
    for adapter in (jugaad([]), yahoo([])):
        assert adapter.describe()["fetch_ohlcv"] is True
        assert adapter.describe()["fetch_quote"] is False
        assert isinstance(adapter, DataAdapter)


def test_yahoo_adapter_returns_bars():
    bars = yahoo([yrow(1, 954.1)]).fetch_ohlcv("SBIN", "1d")
    assert [b.close for b in bars] == [954.1]


def test_chain_falls_back_to_yahoo_and_removes_holiday_row():
    chain = ohlcv_chain(
        [jugaad(error=ConnectionError("nse down")), yahoo([yrow(1, 954.1), yrow(2, 256.5, 0), yrow(5, 955.0)])],
        CAL,
    )
    result = chain.run("SBIN")
    assert result.source == "yahoo"
    assert [ist_date(b.timestamp) for b in result.value] == [date(2026, 10, 1), date(2026, 10, 5)]
    assert result.failures == (("jugaad", "ConnectionError: nse down"),)


def test_chain_treats_holiday_only_result_as_no_data():
    holiday_only = [jrow(datetime(2026, 10, 1, 18, 30), 256.5)]  # IST 2 Oct = holiday
    chain = ohlcv_chain([jugaad(holiday_only), yahoo([yrow(1, 954.1)])], CAL)
    result = chain.run("SBIN")
    assert result.source == "yahoo"
    assert result.failures == (("jugaad", "returned no data"),)


def test_chain_limit_keeps_newest_trading_bars():
    chain = ohlcv_chain([yahoo([yrow(1, 1.0), yrow(5, 5.0), yrow(6, 6.0)])], CAL)
    result = chain.run("X", limit=2)
    assert [ist_date(b.timestamp) for b in result.value] == [date(2026, 10, 5), date(2026, 10, 6)]


def test_chain_raises_when_every_adapter_fails():
    chain = ohlcv_chain([jugaad(error=RuntimeError("a")), yahoo(error=RuntimeError("b"))], CAL)
    with pytest.raises(AllSourcesFailed):
        chain.run("SBIN")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_prices.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.adapters'`.

- [ ] **Step 3: Create `src/athena/adapters/__init__.py` (empty) and write `src/athena/adapters/prices.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import Bar, Quote, SchemaChangedError, UnsupportedOperation
from athena.fallback import FallbackChain
from athena.trading_calendar import TradingCalendar, ist_date

UTC = timezone.utc
_DEFAULT_WINDOW_DAYS = 30
_JUGAAD_COLUMNS = ("DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")
_YAHOO_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")


def bars_from_jugaad(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
    bars = []
    for row in rows:
        missing = [c for c in _JUGAAD_COLUMNS if c not in row]
        if missing:
            raise SchemaChangedError(f"jugaad row missing columns {missing}")
        ts = row["DATE"]
        # jugaad-data returns IST midnight as a naive UTC value: the 1 Oct session is 30 Sep 18:30.
        if (ts.hour, ts.minute) != (18, 30):
            raise SchemaChangedError(
                f"unexpected jugaad timestamp {ts!r}: expected 18:30 (IST midnight as naive UTC)"
            )
        stamp = datetime(ts.year, ts.month, ts.day, ts.hour, ts.minute, tzinfo=UTC)
        bars.append(
            Bar(
                symbol, stamp, float(row["OPEN"]), float(row["HIGH"]), float(row["LOW"]),
                float(row["CLOSE"]), float(row["VOLUME"]), as_of, "jugaad",
            )
        )
    return sorted(bars, key=lambda bar: bar.timestamp)


def bars_from_yahoo(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
    bars = []
    for row in rows:
        missing = [c for c in _YAHOO_COLUMNS if c not in row]
        if missing:
            raise SchemaChangedError(f"yahoo row missing columns {missing}")
        ts = row["Date"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            raise SchemaChangedError("yahoo timestamp must be timezone-aware")
        bars.append(
            Bar(
                symbol, ts.astimezone(UTC), float(row["Open"]), float(row["High"]), float(row["Low"]),
                float(row["Close"]), float(row["Volume"]), as_of, "yahoo",
            )
        )
    return sorted(bars, key=lambda bar: bar.timestamp)


def drop_non_trading_days(bars: list[Bar], calendar: TradingCalendar) -> list[Bar]:
    return [bar for bar in bars if calendar.is_trading_day(ist_date(bar.timestamp))]


class _DailyBarAdapter:
    name = ""

    def __init__(
        self,
        fetch_rows: Callable[[str, date, date], list[dict]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fetch_rows = fetch_rows or self._default_fetch
        self._clock = clock or utc_now

    def describe(self) -> dict:
        return {"fetch_ohlcv": True, "fetch_quote": False, "timeframes": ["1d"]}

    def fetch_quote(self, symbol: str, **params: Any) -> Quote:
        raise UnsupportedOperation(f"{self.name} adapter does not provide quotes")

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: Any = None, limit: Any = None, **params: Any
    ) -> list[Bar]:
        if timeframe != "1d":
            raise UnsupportedOperation(
                f"{self.name} adapter only supports the 1d timeframe, got {timeframe!r}"
            )
        now = self._clock()
        end = params.get("until") or now.date()
        start = since or end - timedelta(days=_DEFAULT_WINDOW_DAYS)
        bars = self._to_bars(self._fetch_rows(symbol, start, end), symbol, now)
        return bars[-limit:] if limit else bars


class JugaadPriceAdapter(_DailyBarAdapter):
    name = "jugaad"

    @staticmethod
    def _default_fetch(symbol: str, start: date, end: date) -> list[dict]:
        from jugaad_data.nse import stock_df

        return stock_df(symbol, from_date=start, to_date=end, series="EQ").to_dict("records")

    @staticmethod
    def _to_bars(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
        return bars_from_jugaad(rows, symbol, as_of)


class YahooPriceAdapter(_DailyBarAdapter):
    name = "yahoo"

    @staticmethod
    def _default_fetch(symbol: str, start: date, end: date) -> list[dict]:
        import yfinance as yf

        frame = yf.Ticker(f"{symbol}.NS").history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
        )
        return frame.reset_index().to_dict("records")

    @staticmethod
    def _to_bars(rows: list[dict], symbol: str, as_of: datetime) -> list[Bar]:
        return bars_from_yahoo(rows, symbol, as_of)


def ohlcv_chain(adapters: Sequence[Any], calendar: TradingCalendar) -> FallbackChain:
    def source(adapter: Any) -> Callable[..., list[Bar]]:
        def fetch(symbol: str, since: Any = None, limit: Any = None, until: Any = None) -> list[Bar]:
            bars = adapter.fetch_ohlcv(symbol, "1d", since=since, until=until)
            bars = drop_non_trading_days(bars, calendar)
            return bars[-limit:] if limit else bars

        return fetch

    return FallbackChain([(adapter.name, source(adapter)) for adapter in adapters])
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `95 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/adapters tests/test_prices.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add price adapters and calendar-aware OHLCV fallback chain"
git push
```

---

### Task 7: Index TRI loader

**Files:**
- Create: `src/athena/loaders/index_tri.py`
- Test: `tests/test_index_tri.py`

**Interfaces:**
- Consumes: `DataStore`, `Record`, `RefreshResult`, errors, `utc_now`, `IST`, `ist_date`.
- Produces (`athena.loaders.index_tri`):
  - `DATASET = "index.tri"`, `SOURCE = "jugaad.index_tri_raw"`, `DEFAULT_INDEX = "NIFTY 50"`
  - `parse_tri_rows(rows: list[dict]) -> list[tuple[date, float, float]]` (sorted ascending `(date, tri, ntr)`; `SchemaChangedError` on a bad row)
  - `class IndexTriLoader(store, fetch=default_fetch, clock=utc_now)` implementing `BatchLoader`; `refresh(dataset=DATASET, since=None, **params)` with optional `index_name` (default `"NIFTY 50"`). `fetch(index_name, start, end)`. One `Record` per trading date: `key=index_name`, `as_of` = that date's IST midnight in UTC, `payload={"tri": float, "ntr": float}`. Window: `since` if given, else the latest stored date, else 30 days before today (IST). Only dates **newer than the latest stored date** are written, so re-running never duplicates. An empty fetch raises `EmptyRefreshError`.

- [ ] **Step 1: Write the failing tests `tests/test_index_tri.py`**

```python
from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.index_tri import DATASET, IndexTriLoader, parse_tri_rows
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # Mon 09:30 IST


def trow(day, tri, ntr):
    return {"RequestNumber": "x", "Index Name": "Nifty 50", "Date": day, "TotalReturnsIndex": tri, "NTR_Value": ntr}


ROWS = [trow("30 Sep 2026", "34371.66", "29846.45"), trow("01 Oct 2026", "34070.01", "29584.52")]


def make_loader(store, rows=ROWS, calls=None):
    def fetch(index_name, start, end):
        if calls is not None:
            calls.append((index_name, start, end))
        return rows

    return IndexTriLoader(store, fetch=fetch, clock=lambda: NOW)


def test_parse_sorts_ascending_and_converts_numbers():
    assert parse_tri_rows(ROWS) == [
        (date(2026, 9, 30), 34371.66, 29846.45),
        (date(2026, 10, 1), 34070.01, 29584.52),
    ]


@pytest.mark.parametrize(
    "bad", [{"Date": "2026-10-01", "TotalReturnsIndex": "1", "NTR_Value": "1"}, {"Date": "01 Oct 2026"}]
)
def test_malformed_row_is_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_tri_rows([bad])


def test_refresh_writes_a_record_per_date_stamped_at_ist_midnight():
    store = DataStore()
    result = make_loader(store).refresh()
    assert result.rows_written == 2
    latest = store.latest(DATASET, "NIFTY 50")
    assert latest.as_of == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)  # 1 Oct 00:00 IST
    assert latest.payload == {"tri": 34070.01, "ntr": 29584.52}
    assert latest.source == "jugaad.index_tri_raw"


def test_point_in_time_reads_the_earlier_day():
    store = DataStore()
    make_loader(store).refresh()
    earlier = store.point_in_time(DATASET, "NIFTY 50", datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
    assert earlier.payload["tri"] == 34371.66


def test_default_window_is_thirty_days_and_since_overrides_it():
    calls = []
    make_loader(DataStore(), calls=calls).refresh()
    make_loader(DataStore(), calls=calls).refresh(since=date(2026, 9, 25))
    assert calls == [
        ("NIFTY 50", date(2026, 9, 5), date(2026, 10, 5)),
        ("NIFTY 50", date(2026, 9, 25), date(2026, 10, 5)),
    ]


def test_index_name_is_a_parameter():
    calls = []
    make_loader(DataStore(), calls=calls).refresh(index_name="NIFTY NEXT 50")
    assert calls[0][0] == "NIFTY NEXT 50"


def test_rerun_writes_only_newer_dates():
    store = DataStore()
    calls = []
    loader = make_loader(store, calls=calls)
    assert loader.refresh().rows_written == 2
    assert loader.refresh().rows_written == 0
    assert calls[1] == ("NIFTY 50", date(2026, 10, 1), date(2026, 10, 5))  # resumes from latest stored day
    newer = ROWS + [trow("05 Oct 2026", "34100.00", "29600.00")]
    assert make_loader(store, rows=newer).refresh().rows_written == 1
    assert store.latest(DATASET, "NIFTY 50").payload["tri"] == 34100.0


def test_empty_fetch_raises():
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), rows=[]).refresh()


def test_wrong_dataset_rejected_and_protocol_holds():
    loader = make_loader(DataStore())
    with pytest.raises(ValueError, match="index.tri"):
        loader.refresh("other")
    assert isinstance(loader, BatchLoader)


def test_read_missing_raises():
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore()).read(DATASET, "NIFTY 50")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_index_tri.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.loaders.index_tri'`.

- [ ] **Step 3: Write `src/athena/loaders/index_tri.py`**

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import IST, ist_date

DATASET = "index.tri"
SOURCE = "jugaad.index_tri_raw"
DEFAULT_INDEX = "NIFTY 50"
_DEFAULT_WINDOW_DAYS = 30


def default_fetch(index_name: str, start: date, end: date) -> list[dict]:
    from jugaad_data.nse import NSEIndexHistory

    return NSEIndexHistory().index_tri_raw(index_name, index_name, start, end)


def parse_tri_rows(rows: list[dict]) -> list[tuple[date, float, float]]:
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(row["Date"], "%d %b %Y").date()
            parsed.append((day, float(row["TotalReturnsIndex"]), float(row["NTR_Value"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise SchemaChangedError(f"unexpected TRI row shape: {exc!r}") from exc
    return sorted(parsed)


def _ist_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)


class IndexTriLoader:
    def __init__(
        self,
        store: DataStore,
        fetch: Callable[[str, date, date], list[dict]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {DATASET: {"cadence": "daily", "source": SOURCE, "series": ["tri", "ntr"]}}

    def refresh(self, dataset: str = DATASET, since: Any = None, **params: Any) -> RefreshResult:
        if dataset != DATASET:
            raise ValueError(f"this loader only provides {DATASET!r}, got {dataset!r}")
        index_name = params.get("index_name", DEFAULT_INDEX)
        now = self._clock()
        today = ist_date(now)
        last = self._store.latest(DATASET, index_name)
        last_day = ist_date(last.as_of) if last else None
        start = since or last_day or today - timedelta(days=_DEFAULT_WINDOW_DAYS)

        raw = self._fetch(index_name, start, today)
        if not raw:
            raise EmptyRefreshError(f"no TRI rows returned for {index_name!r} from {start} to {today}")
        written = 0
        for day, tri, ntr in parse_tri_rows(raw):
            if last_day is not None and day <= last_day:
                continue
            self._store.put(Record(DATASET, index_name, _ist_midnight_utc(day), SOURCE, {"tri": tri, "ntr": ntr}))
            written += 1
        return RefreshResult(DATASET, written, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `106 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/loaders/index_tri.py tests/test_index_tri.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add incremental index TRI loader"
git push
```

---

### Task 8: Opt-in live suite

**Files:**
- Create: `tests/conftest.py`, `tests/live/test_live_sources.py`

**Interfaces:**
- Consumes: every loader and adapter above with their real default fetchers.
- Produces: `pytest --live` runs 6 network tests; plain `pytest` skips them (`6 skipped`).

- [ ] **Step 1: Write `tests/conftest.py`**

```python
import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="run tests that hit real data sources")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real network data sources (needs --live)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
```

- [ ] **Step 2: Write `tests/live/test_live_sources.py`**

```python
from datetime import datetime, timezone

import pytest

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter
from athena.loaders.index_tri import IndexTriLoader
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.store import DataStore

pytestmark = pytest.mark.live


def test_live_holidays():
    store = DataStore()
    result = NseHolidayLoader(store).refresh()
    assert result.rows_written >= 1
    calendar = load_calendar(store, [datetime.now(timezone.utc).year])
    assert len(calendar.holidays) >= 5


def test_live_equity_master():
    store = DataStore()
    result = NseMasterLoader(store).refresh(EQUITY_DATASET)
    assert result.rows_written > 1000
    assert store.latest(EQUITY_DATASET, "SBIN").payload["isin"].startswith("INE")


def test_live_etf_master():
    store = DataStore()
    NseMasterLoader(store).refresh(ETF_DATASET)
    etf = store.latest(ETF_DATASET, "NIFTYBEES")
    assert etf.payload["isin"].startswith("INF")
    assert etf.payload["underlying_key"]


def test_live_jugaad_prices():
    bars = JugaadPriceAdapter().fetch_ohlcv("SBIN", "1d", limit=3)
    assert bars and all(bar.close > 0 for bar in bars)


def test_live_yahoo_prices():
    bars = YahooPriceAdapter().fetch_ohlcv("SBIN", "1d", limit=3)
    assert bars and all(bar.close > 0 for bar in bars)


def test_live_index_tri():
    store = DataStore()
    result = IndexTriLoader(store).refresh()
    assert result.rows_written >= 3
    assert store.latest("index.tri", "NIFTY 50").payload["tri"] > 10000
```

- [ ] **Step 3: Verify the default run skips them**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `106 passed, 6 skipped`.

- [ ] **Step 4: Run the live suite**

Run: `.venv/Scripts/python -m pytest --live tests/live -v`
Expected: `6 passed`. If one fails, the real source has changed since the 5 Oct 2026 verification: read the traceback, fix the adapter/loader (not the test), and record the new behaviour in Task 9's TRD log. Do not weaken an assertion to make it pass.

- [ ] **Step 5: Commit and push**

```bash
git add tests/conftest.py tests/live/test_live_sources.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add opt-in live data-source suite"
git push
```

---

### Task 9: TRD source log, graph refresh

**Files:**
- Modify: `TRD.md`

- [ ] **Step 1: Insert a source verification log into `TRD.md`**

Immediately before the line `## 7. Full repository reuse audit`, insert:

```markdown
### 6.2 Source verification log (5 Oct 2026)

Checked against the live sources; re-checked by `pytest --live`.

| Source | Result |
| --- | --- |
| NSE holiday list (`NSELive().holiday_list()`) | Works; segment `CM`; current year only; 2 Oct 2026 is a holiday |
| NSE `EQUITY_L.csv`, `eq_etfseclist.csv` | Work with a browser `User-Agent`; ETF list gives each ETF's underlying index |
| jugaad-data `stock_df` | Works; dates are IST midnight stored as naive UTC (18:30 the previous day); unadjusted prices |
| jugaad-data `index_tri_raw(name, index_name, from, to)` | Works; returns TRI and NTR |
| Yahoo `.NS` history | Works but invents flat zero-volume rows on market holidays; use `auto_adjust=False`; statements returned 4 years for SBIN |
| AMFI `NAVAll.txt` | Works; scheme code, ISINs, NAV, date in one file (mutual funds on hold) |
| NSE live quotes and option chain (jugaad-data, nsepython) | **Broken** (KeyError / empty); intraday quote and option-chain datasets unavailable for now |
| `nsepython.index_total_returns` | **Broken** (endpoint returns HTML); use jugaad-data |
| AMFI TER | Page builds its table in the browser; no direct file found (mutual funds on hold) |
| AMFI portfolio holdings | About 45 separate fund-house sites with differing formats (mutual funds on hold) |
```

- [ ] **Step 2: Mark open decision 8 as partly resolved**

In `TRD.md` §9, replace the text `AMC holdings file formats.` (end of open decision 8) with:

```
AMC holdings file formats. *Partly resolved 5 Oct 2026 (see §6.2): jugaad-data TRI, NSE equity/ETF lists and the AMFI NAV file are confirmed; ETF holdings, iNAV, G-sec yields, ISIN prefix rules, AMFI TER capture and AMC holdings formats remain open.*
```

- [ ] **Step 3: Add a revision-history line**

At the top of the revision-history list in `TRD.md`, add:

```
- **Oct 5, 2026 (Phase 0b)** — Added §6.2 source verification log; holiday-aware freshness; equity/ETF data layer implemented (see `docs/superpowers/plans/2026-10-05-phase-0b-equity-etf-data.md`).
```

- [ ] **Step 4: Full suite, commit, push, refresh graph**

```bash
.venv/Scripts/python -m pytest -q
git add TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "docs: record verified data-source findings in TRD"
git push
.venv/Scripts/python -m graphify update .
```

Expected: `106 passed, 6 skipped`; graph rebuilt. (Run `/graphify . --update` in the assistant if the TRD change should be re-extracted semantically.)

---

## Self-Review (completed)

**Spec coverage:**
- TRD §5 freshness holiday handling -> Tasks 2-3 (fixes the false-stale Monday bug verified in the spike).
- TRD §5 fallback chains for price (jugaad -> Yahoo) -> Task 6; the "nsepython" middle link is dropped because its quote/option paths are broken and it adds no OHLCV beyond jugaad-data (recorded in §6.2).
- TRD §2.11 batch loaders: holidays (Task 4), NSE masters (Task 5), index TRI (Task 7); `index.tri` also serves ETF tracking error and stock benchmarks later.
- TRD §6.1 coverage matrix: ETF tracking-error index series now Covered; ETF liquidity/quotes and options remain Partial/Broken (documented).
- Not in this plan: mutual-fund NAV/TER/holdings (on hold), intraday quotes and option chain (sources broken), ETF holdings/iNAV (unverified), the instrument resolver (0c), metrics/evaluation (0d).

**Placeholder scan:** none; every step has full code or an exact command; the only conditional (Task 8 Step 4) says exactly what to do.

**Type consistency:** `Record`, `RefreshResult`, `Bar`, `TradingCalendar`, `ist_date`, `utc_now` signatures match across tasks; `DataStore.latest/put` usage matches Plan 0a; adapter `name` attributes feed `ohlcv_chain`; error classes come from `contracts.py`. Test totals: 49 (0a) + 3 (Task 1) + 5 (Task 2) + 3 (Task 3) + 11 (Task 4) + 8 (Task 5) + 16 (Task 6) + 11 (Task 7) = 106 passed, plus 6 live tests skipped by default.

**Not executed:** the code in this plan was written but not run (the source behaviour it relies on was). Each task's red/green steps are where it is first verified.
