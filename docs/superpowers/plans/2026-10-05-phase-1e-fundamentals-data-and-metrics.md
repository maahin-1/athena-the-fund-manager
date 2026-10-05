# Phase 1e — Fundamentals Data and Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Athena the numbers the Valuation, Moat & Quality and Earnings Intelligence specialists need, computed in code from free data, and show them on the dashboard now: financial statements, the earnings calendar and surprise history (Yahoo), and NSE index valuation history, turned into a fundamentals packet of valuation, business-quality and earnings figures, each saying which years or quarters it used. The three specialists themselves are Plan 1f; this plan is the data and metrics layer under them (TRD §2.3, §2.13, §6.1).

**Architecture:** Two batch loaders write into the existing `DataStore` like every other dataset: `FundamentalsLoader` (one stock per refresh, dataset `equity.fundamentals`, whose 136-day freshness limit already exists) and `IndexValuationLoader` (dataset `index.valuation`, new one-business-day limit). `build_fundamentals_packet` in `athena.metrics.fundamentals` is a pure function from a stored payload plus a price to a metrics packet in the shape every earlier packet uses (`metrics`, `missing`, `missing_reasons`), with a `group` on each metric and a `groups` map on the packet. Banks and lenders get the ratios that mean something for them and an explicit "not meaningful for banks and lenders" for the rest. The dashboard service fetches the statements per request and the view adds three panels (Valuation, Business quality, Earnings), each with its own as-of and coverage label.

**Tech Stack:** Python >= 3.11, yfinance and pandas (already installed), jugaad-data, pytest. No new dependencies.

**Spec:** `TRD.md` §2.3 (equity specialists, ratio-only mode), §2.13 (metrics engine), §6.1 (coverage matrix), §5 (freshness limits); `PRD.md` FR-1.

**Plan series:** 0a-0e, 1a-1d (done) -> **1e (this plan)** -> 1f (Valuation, Moat & Quality and Earnings Intelligence specialists on these packets, routed and blended) -> 1g (stock backtest) -> later phases.

**Suggested models:** Sonnet at medium effort, inline execution (four sequential tasks plus a live task). Prototyped end to end in a scratch copy first: 432 offline tests passed, six deliberate mutations were each caught by the matching test, and 5 live tests passed against real data.

## Verified findings (5 Oct 2026)

**What Yahoo gives for NSE stocks** (16 symbols checked, then five run end to end):
- 15 of 16 returned statements: **4 annual periods** (a fifth column is usually partly empty) and **5 to 6 quarters**, current to the March 2026 year and the June 2026 quarter. The 16th, `ZOMATO`, returned nothing because the NSE symbol is now `ETERNAL` (which works): stale symbols fail loudly with a hint that the symbol may be renamed.
- Line items present for non-banks: revenue, net income, operating income, EBIT, gross profit, diluted EPS, interest expense, total assets, equity, debt, current assets and liabilities, share count, operating cash flow, capex, depreciation. **Banks and NBFCs (SBIN, HDFCBANK, BAJFINANCE) have no operating income, gross profit or current assets**, as expected.
- `earnings_dates` gives the next report date plus EPS estimate, reported EPS and surprise percent for the last 24 reports, stamped in US Eastern time (converted to IST dates here).
- **Data-quality problems found:** ITC's quarterly figures contain periods with identical revenue and profit (a duplicate); SBIN has a missing quarter. The loader blanks duplicated periods and flags them; the metrics fall back or report missing instead of using bad numbers.
- Yahoo's `returnOnEquity` is empty for most stocks (so ROE is computed from the statements), `dividendYield` is a percent and disagrees with dividend rate over price (so the yield is computed from the rate), and `Capital Expenditure` is stored as a negative number.
- NSE index valuation: `NSEIndexHistory().index_pe_raw(name, start, end)` returns P/E, P/B and dividend yield per day for any index, with 1,981 NIFTY 50 rows back to 2018 in the eight-year window used here (the NSE series starts in 2015).

**Live results through the real chain** (price from jugaad-data, statements from Yahoo, NIFTY 50 history from NSE), illustrative figures only:

| Stock | Notable output |
| --- | --- |
| TCS | trailing P/E 15.5, P/B 7.1, ROE 48.7%, accruals ratio -0.017, next report in 3 days; nothing missing |
| SBIN (bank) | P/E 10.5, P/B 1.5, ROE 15.4%; the 11 cash-flow and working-capital ratios listed as "not meaningful for banks and lenders" |
| ITC | duplicated-quarter flag raised and the two periods treated as missing; dividend yield 5.95% |
| RELIANCE | owner-earnings yield 1.0% against free-cash-flow yield 4.3% (heavy capex), cash conversion 2.4 |
| KPITTECH | EPS surprise -27.8% on the last report, 0 beats in the last 4 |

The NIFTY 50 P/E (19.3) sits at the 0.9th percentile of the eight-year history, so every stock's `index_pe_percentile` is the same market-wide figure; it is context, not a stock-specific signal.

**Honest limits** (each stated in the packet's notes or here):
- **Not point-in-time.** Yahoo returns today's snapshot, never what was known on an earlier date, so fundamentals cannot be backtested honestly. The backtest plan will treat this as a hard constraint.
- **Owner earnings** deduct all capital expenditure (no maintenance/growth split); the metric's note says so.
- **Estimates and surprises** come from Yahoo's EPS estimates, whose quality for Indian stocks is unverified.
- **Valuation covers ratios only:** no DCF (needs forecasts), no peer comparables, no Graham net-net, and the index context is the NIFTY 50 only (no sector index mapping yet).
- A stale or renamed symbol raises `EmptyRefreshError`; a source that fails any other way is reported as `could not fetch statements for 'X': <error type>` without the underlying message.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` (the fundamentals chain needs no API key; the dashboard test needs the LLM keys in `.env`, read by the code at runtime, which the assistant cannot read).
- Compute in code: every number comes from `build_fundamentals_packet`; the dashboard shows them and the specialists will interpret them, nothing recomputes them.
- A figure that cannot be computed is listed in `missing` with its reason, never silently dropped and never zero-filled; ratios that mean nothing for lenders say so.
- Every panel shows as-of (latest annual period and latest quarter), source and a coverage label.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/freshness.py` | modified: one-business-day limit for `index.valuation` |
| `src/athena/loaders/index_valuation.py` | `IndexValuationLoader`, `parse_valuation_rows`, `valuation_history` |
| `src/athena/loaders/fundamentals.py` | `FundamentalsLoader`, `payload_from_frames`, `frame_to_lines`, `blank_duplicate_periods`, `earnings_rows` |
| `src/athena/metrics/fundamentals.py` | `build_fundamentals_packet`, `Statements`, constants |
| `src/athena/dashboard/view.py` | modified: three fundamentals panels and their notes |
| `src/athena/dashboard/service.py` | modified: `LiveFundamentals`, optional `fundamentals` source on `DashboardService` |
| `tests/fund_fixtures.py` | `ACME`: a company whose figures can be worked out by hand; `index_history` |
| `tests/test_index_valuation.py`, `test_fundamentals_loader.py`, `test_metrics_fundamentals.py`, `tests/live/test_live_fundamentals.py` | one test module per new source module, plus the live check |
| `tests/dash_fakes.py`, `test_dashboard_view.py`, `test_dashboard_service.py`, `test_dashboard_app.py` | modified: fundamentals cases |

---

### Task 1: Index valuation loader

**Files:**
- Modify: `src/athena/freshness.py`
- Create: `src/athena/loaders/index_valuation.py`
- Test: `tests/test_index_valuation.py`

**Interfaces:**
- Consumes: `Record`, `RefreshResult`, `EmptyRefreshError`, `SchemaChangedError`, `BatchLoader` (Phase 0a); `DataStore`; `ist_date`, `IST`; `Limit`, `DEFAULT_LIMITS`.
- Produces (`athena.loaders.index_valuation`): `DATASET = "index.valuation"`; `parse_valuation_rows(rows) -> list[tuple[date, pe, pb, div_yield]]` (sorted ascending; a malformed row raises `SchemaChangedError`); `IndexValuationLoader(store, fetch=default_fetch, clock=utc_now)` with `describe()`, `refresh(dataset=DATASET, since=None, index_name="NIFTY 50") -> RefreshResult` (incremental from the last stored day; empty response raises `EmptyRefreshError`) and `read`; `valuation_history(store, index_name="NIFTY 50") -> dict[date, {"pe", "pb", "div_yield"}]`. `fetch(index_name, start, end)` returns NSE rows with keys `DATE` (`"05 Oct 2026"`), `pe`, `pb`, `divYield` (strings, `".72"` is valid).
- Produces (`athena.freshness`): `DEFAULT_LIMITS["index.valuation"] = Limit(1, "business_days")`.

- [ ] **Step 1: Write the failing tests `tests/test_index_valuation.py`**

```python
from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.freshness import DEFAULT_LIMITS, Limit
from athena.loaders.index_valuation import DATASET, IndexValuationLoader, parse_valuation_rows, valuation_history
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)  # Mon 09:30 IST


def vrow(day, pe, pb, dy, name="Nifty 50"):
    return {"RequestNumber": "x", "Index Name": name, "pe": pe, "pb": pb, "divYield": dy, "DATE": day}


ROWS = [vrow("05 Oct 2026", "19.3", "2.77", "1.23"), vrow("01 Oct 2026", "19.1", "2.75", ".72")]


def make_loader(store, rows=ROWS, calls=None):
    def fetch(index_name, start, end):
        if calls is not None:
            calls.append((index_name, start, end))
        return rows

    return IndexValuationLoader(store, fetch=fetch, clock=lambda: NOW)


def test_parse_sorts_ascending_and_reads_a_leading_dot_yield():
    assert parse_valuation_rows(ROWS) == [(date(2026, 10, 1), 19.1, 2.75, 0.72), (date(2026, 10, 5), 19.3, 2.77, 1.23)]


@pytest.mark.parametrize("bad", [{"DATE": "2026-10-05", "pe": "1", "pb": "1", "divYield": "1"}, {"DATE": "05 Oct 2026", "pe": "1"}, {"DATE": "05 Oct 2026", "pe": "-", "pb": "1", "divYield": "1"}])
def test_a_malformed_row_is_a_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_valuation_rows([bad])


def test_refresh_stores_each_day_and_history_reads_them_by_trading_date():
    store = DataStore()
    result = make_loader(store).refresh()
    assert (result.dataset, result.rows_written, result.as_of) == (DATASET, 2, NOW)
    history = valuation_history(store)
    assert history[date(2026, 10, 5)] == {"pe": 19.3, "pb": 2.77, "div_yield": 1.23}
    assert history[date(2026, 10, 1)]["pe"] == 19.1


def test_a_second_refresh_asks_from_the_last_stored_day_and_writes_only_new_rows():
    store, calls = DataStore(), []
    loader = make_loader(store, calls=calls)
    loader.refresh()
    assert loader.refresh().rows_written == 0  # nothing newer than 5 Oct
    assert calls[1][1] == date(2026, 10, 5)


def test_an_explicit_since_and_index_name_are_honoured():
    store, calls = DataStore(), []
    make_loader(store, calls=calls).refresh(since=date(2015, 1, 1), index_name="NIFTY BANK")
    assert calls[0][0] == "NIFTY BANK" and calls[0][1] == date(2015, 1, 1)
    assert valuation_history(store, "NIFTY BANK") and valuation_history(store) == {}


def test_empty_response_fails_loud_and_wrong_dataset_is_rejected():
    store = DataStore()
    with pytest.raises(EmptyRefreshError):
        make_loader(store, rows=[]).refresh()
    with pytest.raises(ValueError):
        make_loader(store).refresh("index.tri")
    with pytest.raises(EmptyRefreshError):
        make_loader(store).read(DATASET, "NEVER")


def test_the_loader_satisfies_the_protocol_and_the_dataset_has_a_freshness_limit():
    loader = make_loader(DataStore())
    assert isinstance(loader, BatchLoader) and DATASET in loader.describe()
    assert DEFAULT_LIMITS[DATASET] == Limit(1, "business_days")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_index_valuation.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.loaders.index_valuation'`.

- [ ] **Step 3: Add the freshness limit and write `src/athena/loaders/index_valuation.py`**

In `src/athena/freshness.py`, add this line to `DEFAULT_LIMITS` directly after the `"index.price"` entry:

```python
    "index.valuation": Limit(1, "business_days"),
```

`src/athena/loaders/index_valuation.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import IST, ist_date

DATASET = "index.valuation"
SOURCE = "jugaad.index_pe_raw"
DEFAULT_INDEX = "NIFTY 50"
_DEFAULT_WINDOW_DAYS = 30


def default_fetch(index_name: str, start: date, end: date) -> list[dict]:
    from jugaad_data.nse import NSEIndexHistory

    return NSEIndexHistory().index_pe_raw(index_name, start, end)


def parse_valuation_rows(rows: list[dict]) -> list[tuple[date, float, float, float]]:
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(row["DATE"], "%d %b %Y").date()
            parsed.append((day, float(row["pe"]), float(row["pb"]), float(row["divYield"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise SchemaChangedError(f"unexpected index valuation row shape: {exc!r}") from exc
    return sorted(parsed)


def _ist_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)


class IndexValuationLoader:
    """Daily price-to-earnings, price-to-book and dividend yield of an NSE index (NSE publishes these back to 2015)."""

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
        return {DATASET: {"cadence": "daily", "source": SOURCE, "series": ["pe", "pb", "div_yield"]}}

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
            raise EmptyRefreshError(f"no valuation rows returned for {index_name!r} from {start} to {today}")
        written = 0
        for day, pe, pb, div_yield in parse_valuation_rows(raw):
            if last_day is not None and day <= last_day:
                continue
            self._store.put(Record(DATASET, index_name, _ist_midnight_utc(day), SOURCE, {"pe": pe, "pb": pb, "div_yield": div_yield}))
            written += 1
        return RefreshResult(DATASET, written, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record


def valuation_history(store: DataStore, index_name: str = DEFAULT_INDEX) -> dict[date, dict[str, float]]:
    """Stored valuation rows for an index, keyed by IST trading date."""
    return {ist_date(record.as_of): dict(record.payload) for record in store.history(DATASET, index_name)}
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_index_valuation.py tests/test_freshness.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `9 passed` plus the freshness tests, then `394 passed, 48 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/freshness.py src/athena/loaders/index_valuation.py tests/test_index_valuation.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add NSE index valuation loader (P/E, P/B, dividend yield)"
git push
```

---

### Task 2: Fundamentals loader

**Files:**
- Create: `src/athena/loaders/fundamentals.py`
- Test: `tests/test_fundamentals_loader.py`

**Interfaces:**
- Consumes: `Record`, `RefreshResult`, `EmptyRefreshError`, `AthenaError`, `BatchLoader`; `DataStore`; `ist_date`; pandas frames (tests build them synthetically).
- Produces (`athena.loaders.fundamentals`): `DATASET = "equity.fundamentals"`, `SOURCE = "yfinance"`, line-item tuples `INCOME_LINES`, `BALANCE_LINES`, `CASHFLOW_LINES`, `INFO_KEYS`; `frame_to_lines(frame, lines) -> {line: {period_iso: float | None}}`; `blank_duplicate_periods(income, label) -> list[str]` (mutates: periods whose revenue and net income both equal another period's are set to `None` in every line; returns one flag per group); `earnings_rows(frame) -> list[{"date", "estimate", "reported", "surprise_pct"}]` (IST dates); `payload_from_frames(info, income, balance, cashflow, quarterly_income, earnings) -> dict` with keys `info`, `annual` (`income`, `balance`, `cashflow`), `quarterly` (`income`), `earnings_dates`, `quality_flags`; `default_fetch(symbol)` (the thin Yahoo call, `<symbol>.NS`); `FundamentalsLoader(store, fetch=default_fetch, clock=utc_now)` with `describe()`, `refresh(dataset=DATASET, since=None, symbol=...) -> RefreshResult` and `read(dataset, key) -> Record`. A fetch that returns no annual income at all, or that raises something other than an `AthenaError`, raises `EmptyRefreshError` naming the symbol.

- [ ] **Step 1: Write the failing tests `tests/test_fundamentals_loader.py`**

```python
from datetime import datetime, timezone

import pandas as pd
import pytest

from athena.contracts import BatchLoader, EmptyRefreshError
from athena.loaders.fundamentals import (
    DATASET,
    FundamentalsLoader,
    blank_duplicate_periods,
    earnings_rows,
    frame_to_lines,
    payload_from_frames,
)
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
YEAR_ENDS = [pd.Timestamp("2026-03-31"), pd.Timestamp("2025-03-31"), pd.Timestamp("2024-03-31")]


def frame(rows, columns=YEAR_ENDS):
    return pd.DataFrame(rows, index=list(rows), columns=None) if False else pd.DataFrame(list(rows.values()), index=list(rows), columns=columns)


INCOME = frame({"Total Revenue": [300.0, 200.0, 100.0], "Net Income": [30.0, 20.0, 10.0], "Diluted EPS": [3.0, 2.0, float("nan")], "Unwanted Line": [1, 2, 3]})


def test_frame_to_lines_keeps_only_wanted_items_with_iso_periods_and_none_for_gaps():
    lines = frame_to_lines(INCOME, ("Total Revenue", "Diluted EPS", "Operating Income"))
    assert lines == {
        "Total Revenue": {"2026-03-31": 300.0, "2025-03-31": 200.0, "2024-03-31": 100.0},
        "Diluted EPS": {"2026-03-31": 3.0, "2025-03-31": 2.0, "2024-03-31": None},
    }  # an item the frame lacks is simply absent


def test_an_empty_or_missing_frame_gives_no_lines():
    assert frame_to_lines(None, ("Total Revenue",)) == {} and frame_to_lines(pd.DataFrame(), ("Total Revenue",)) == {}


def test_periods_with_identical_revenue_and_profit_are_blanked_and_flagged():
    quarters = {
        "Total Revenue": {"2025-12-31": 19918.2, "2025-09-30": 21372.9, "2025-06-30": 19918.2},
        "Net Income": {"2025-12-31": 4931.2, "2025-09-30": 5244.2, "2025-06-30": 4931.2},
        "Diluted EPS": {"2025-12-31": 3.94, "2025-09-30": 4.18, "2025-06-30": 3.94},
    }
    flags = blank_duplicate_periods(quarters, "quarterly")
    assert quarters["Total Revenue"] == {"2025-12-31": None, "2025-09-30": 21372.9, "2025-06-30": None}
    assert quarters["Diluted EPS"]["2025-12-31"] is None and quarters["Diluted EPS"]["2025-09-30"] == 4.18
    assert len(flags) == 1 and "2025-06-30, 2025-12-31" in flags[0] and "possible duplicate" in flags[0]


def test_distinct_and_missing_periods_are_left_alone():
    income = {"Total Revenue": {"a": 1.0, "b": 2.0, "c": None}, "Net Income": {"a": 1.0, "b": 2.0, "c": None}}
    assert blank_duplicate_periods(income, "annual") == []
    assert income["Total Revenue"] == {"a": 1.0, "b": 2.0, "c": None}


def test_earnings_rows_convert_us_eastern_stamps_to_ist_dates_and_nan_to_none():
    stamps = pd.DatetimeIndex([pd.Timestamp("2026-10-29 06:00", tz="America/New_York"), pd.Timestamp("2026-07-31 07:00", tz="America/New_York")])
    table = pd.DataFrame({"EPS Estimate": [3.34, 3.20], "Reported EPS": [float("nan"), 3.76], "Surprise(%)": [float("nan"), 17.48]}, index=stamps)
    assert earnings_rows(table) == [
        {"date": "2026-10-29", "estimate": 3.34, "reported": None, "surprise_pct": None},
        {"date": "2026-07-31", "estimate": 3.20, "reported": 3.76, "surprise_pct": 17.48},
    ]
    assert earnings_rows(None) == []


def test_payload_keeps_the_known_info_keys_and_structure():
    info = {"sector": "Technology", "sharesOutstanding": 100, "irrelevant": "x"}
    payload = payload_from_frames(info, INCOME, None, None, INCOME, None)
    assert payload["info"]["sector"] == "Technology" and payload["info"]["sharesOutstanding"] == 100 and "irrelevant" not in payload["info"]
    assert payload["info"]["marketCap"] is None
    assert set(payload["annual"]) == {"income", "balance", "cashflow"} and payload["annual"]["balance"] == {}
    assert payload["quarterly"]["income"]["Total Revenue"]["2026-03-31"] == 300.0
    assert payload["earnings_dates"] == [] and payload["quality_flags"] == []


def test_payload_flags_a_duplicated_quarter():
    quarterly = frame({"Total Revenue": [5.0, 6.0, 5.0], "Net Income": [1.0, 2.0, 1.0]})
    payload = payload_from_frames({}, INCOME, None, None, quarterly, None)
    assert len(payload["quality_flags"]) == 1 and payload["quarterly"]["income"]["Total Revenue"]["2026-03-31"] is None


def make_loader(payload, store=None):
    store = store or DataStore()
    return FundamentalsLoader(store, fetch=lambda symbol: payload, clock=lambda: NOW), store


GOOD = payload_from_frames({"sector": "Technology"}, INCOME, None, None, None, None)


def test_refresh_stores_one_record_per_symbol_and_read_returns_the_latest():
    loader, store = make_loader(GOOD)
    result = loader.refresh(symbol="TCS")
    assert (result.dataset, result.rows_written, result.as_of, result.source) == (DATASET, 1, NOW, "yfinance")
    record = loader.read(DATASET, "TCS")
    assert record.as_of == NOW and record.payload["info"]["sector"] == "Technology"
    assert store.latest(DATASET, "TCS") is not None


def test_empty_statements_fail_loud_with_a_hint_about_renamed_symbols():
    loader, _ = make_loader(payload_from_frames({}, None, None, None, None, None))
    with pytest.raises(EmptyRefreshError, match="renamed"):
        loader.refresh(symbol="ZOMATO")


def test_refresh_validates_its_arguments_and_read_needs_a_prior_refresh():
    loader, _ = make_loader(GOOD)
    with pytest.raises(ValueError):
        loader.refresh()
    with pytest.raises(ValueError):
        loader.refresh("mf.nav", symbol="TCS")
    with pytest.raises(EmptyRefreshError):
        loader.read(DATASET, "NEVER")


def test_loader_satisfies_the_batch_loader_protocol_and_describes_itself():
    loader, _ = make_loader(GOOD)
    assert isinstance(loader, BatchLoader)
    assert DATASET in loader.describe()


def test_a_source_failure_becomes_a_refresh_error_that_names_the_symbol_but_not_the_internals():
    def broken(symbol):
        raise ConnectionError("secret-host.example connection reset")

    loader = FundamentalsLoader(DataStore(), fetch=broken, clock=lambda: NOW)
    with pytest.raises(EmptyRefreshError) as caught:
        loader.refresh(symbol="TCS")
    assert "TCS" in str(caught.value) and "ConnectionError" in str(caught.value) and "secret-host" not in str(caught.value)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_fundamentals_loader.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.loaders.fundamentals'`.

- [ ] **Step 3: Write `src/athena/loaders/fundamentals.py`**

```python
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import AthenaError, EmptyRefreshError, Record, RefreshResult
from athena.store import DataStore
from athena.trading_calendar import ist_date

DATASET = "equity.fundamentals"  # freshness limit already declared: one quarter plus 45 days
SOURCE = "yfinance"
INFO_KEYS = (
    "sector", "industry", "sharesOutstanding", "marketCap", "currentPrice", "dividendRate", "financialCurrency",
)
INCOME_LINES = (
    "Total Revenue", "Net Income", "Operating Income", "EBIT", "Gross Profit", "Diluted EPS", "Interest Expense", "Pretax Income",
)
BALANCE_LINES = (
    "Total Assets", "Stockholders Equity", "Total Debt", "Current Assets", "Current Liabilities",
    "Ordinary Shares Number", "Cash And Cash Equivalents",
)
CASHFLOW_LINES = ("Operating Cash Flow", "Capital Expenditure", "Depreciation And Amortization", "Free Cash Flow")
DUPLICATE_KEY_LINES = ("Total Revenue", "Net Income")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def frame_to_lines(frame: Any, lines: tuple[str, ...]) -> dict[str, dict[str, float | None]]:
    """{line item: {period end (ISO): value}} for the wanted line items of a statement frame (items x periods)."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    out: dict[str, dict[str, float | None]] = {}
    for line in lines:
        if line in frame.index:
            out[line] = {column.date().isoformat(): _number(frame.loc[line, column]) for column in frame.columns}
    return out


def blank_duplicate_periods(income: dict[str, dict[str, float | None]], label: str) -> list[str]:
    """Yahoo sometimes repeats one period's figures under a second period. Periods whose revenue and net income
    both equal another period's are blanked (set to None) in every income line, and each is reported."""
    revenue, profit = (income.get(name, {}) for name in DUPLICATE_KEY_LINES)
    groups: dict[tuple[float, float], list[str]] = {}
    for period in revenue:
        pair = (revenue.get(period), profit.get(period))
        if None not in pair:
            groups.setdefault(pair, []).append(period)  # type: ignore[arg-type]
    flags: list[str] = []
    for periods in groups.values():
        if len(periods) > 1:
            for period in periods:
                for series in income.values():
                    if period in series:
                        series[period] = None
            flags.append(f"{label} periods {', '.join(sorted(periods))} carry identical figures (possible duplicate); treated as missing")
    return flags


def earnings_rows(frame: Any) -> list[dict[str, Any]]:
    """Earnings calendar and surprises as plain dicts; dates are IST dates (Yahoo stamps them in US Eastern time)."""
    if frame is None or getattr(frame, "empty", True):
        return []
    rows = []
    for stamp, row in frame.iterrows():
        rows.append(
            {
                "date": ist_date(stamp.to_pydatetime()).isoformat(),
                "estimate": _number(row.get("EPS Estimate")),
                "reported": _number(row.get("Reported EPS")),
                "surprise_pct": _number(row.get("Surprise(%)")),
            }
        )
    return rows


def payload_from_frames(
    info: Mapping[str, Any], income: Any, balance: Any, cashflow: Any, quarterly_income: Any, earnings: Any
) -> dict[str, Any]:
    """The stored payload: only the line items the metrics use, with duplicates blanked and flagged."""
    annual_income = frame_to_lines(income, INCOME_LINES)
    quarterly = frame_to_lines(quarterly_income, INCOME_LINES)
    flags = blank_duplicate_periods(annual_income, "annual") + blank_duplicate_periods(quarterly, "quarterly")
    return {
        "info": {key: info.get(key) for key in INFO_KEYS},
        "annual": {
            "income": annual_income,
            "balance": frame_to_lines(balance, BALANCE_LINES),
            "cashflow": frame_to_lines(cashflow, CASHFLOW_LINES),
        },
        "quarterly": {"income": quarterly},
        "earnings_dates": earnings_rows(earnings),
        "quality_flags": flags,
    }


def default_fetch(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}.NS")
    try:
        earnings = ticker.earnings_dates
    except Exception:  # the calendar is optional; the statements are not
        earnings = None
    return payload_from_frames(
        ticker.info or {}, ticker.financials, ticker.balance_sheet, ticker.cashflow, ticker.quarterly_financials, earnings
    )


class FundamentalsLoader:
    """Financial statements, key facts and the earnings calendar for one NSE stock at a time (the free source is
    Yahoo; it gives only today's snapshot, never what was known on an earlier date)."""

    def __init__(
        self,
        store: DataStore,
        fetch: Callable[[str], dict[str, Any]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {DATASET: {"cadence": "quarterly", "source": SOURCE, "scope": "one symbol per refresh"}}

    def refresh(self, dataset: str = DATASET, since: Any = None, **params: Any) -> RefreshResult:
        if dataset != DATASET:
            raise ValueError(f"this loader only provides {DATASET!r}, got {dataset!r}")
        symbol = params.get("symbol")
        if not symbol:
            raise ValueError("refresh needs symbol=...")
        try:
            payload = self._fetch(symbol)
        except AthenaError:
            raise
        except Exception as exc:  # the free source can fail in many ways (network, rate limit, format)
            raise EmptyRefreshError(f"could not fetch statements for {symbol!r}: {type(exc).__name__}") from exc
        income = payload.get("annual", {}).get("income", {})
        if not any(series for series in income.values()):
            raise EmptyRefreshError(f"no financial statements for {symbol!r} (the symbol may be wrong or renamed)")
        now = self._clock()
        self._store.put(Record(DATASET, symbol, now, SOURCE, payload))
        return RefreshResult(DATASET, 1, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_fundamentals_loader.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `12 passed`, then `406 passed, 48 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/loaders/fundamentals.py tests/test_fundamentals_loader.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add fundamentals loader with duplicate-period detection"
git push
```

---

### Task 3: Fundamentals metrics packet

**Files:**
- Create: `tests/fund_fixtures.py`, `src/athena/metrics/fundamentals.py`
- Test: `tests/test_metrics_fundamentals.py`

**Interfaces:**
- Consumes: `InsufficientData` from `athena.contracts`; `ist_date`; a stored payload in the Task 2 shape.
- Produces (`athena.metrics.fundamentals`): `FINANCIAL_SECTORS`, `LENDER_REASON = "not meaningful for banks and lenders"`, `GROUPS = ("valuation", "quality", "earnings")`; `Statements.from_payload(payload)` with `annual_series(group, line)`, `quarterly_series(line)`, `is_financial`; `build_fundamentals_packet(instrument, as_of, payload, price, now, index_history=None) -> dict` with keys `instrument`, `as_of`, `sector`, `is_lender`, `latest_annual_period`, `latest_quarter`, `data_quality_flags`, `metrics`, `missing`, `missing_reasons`, `groups` (metric name -> group). Each metric is `{"value", "unit", "inputs", "window", "source", "group"[, "note"]}`.
  - valuation: `last_price`, `market_cap` (INR crore), `pe_trailing`, `pb`, `earnings_yield`, `fcf_yield`, `owner_earnings_yield`, `peg`, `graham_number_premium`, `dividend_yield`, `index_pe`, `pe_vs_index`, `index_pe_percentile` (0-100).
  - quality: `roe_latest`, `roe_average`, `roe_minimum`, `net_margin_latest`, `operating_margin_latest`, `operating_margin_change` (percentage points), `gross_margin_latest`, `revenue_cagr`, `earnings_cagr`, `debt_to_equity`, `current_ratio`, `interest_coverage`, `asset_turnover`.
  - earnings: `accruals_ratio`, `cash_conversion`, `revenue_growth_yoy_quarter`, `net_income_growth_yoy_quarter`, `eps_surprise_last`, `eps_surprise_average_4`, `beats_last_4`, `days_to_next_earnings`.
- Produces (`tests/fund_fixtures.py`): `ACME` (payload: Technology sector, 10 crore shares, four fiscal years to March 2026, five quarters to June 2026, five earnings dates), `PRICE = 266.2`, `NOW`, `YEARS`, `QUARTERS`, `annual(values)`, `quarterly(values)`, `by(periods, values, scale)`, `index_history(latest_pe=19.3)` (300 trading days).

- [ ] **Step 1: Create the shared fixture and write the failing tests**

`tests/fund_fixtures.py`:

```python
"""A company whose fundamentals can be worked out by hand, shared by the metrics and dashboard tests."""
from datetime import date, datetime, timedelta, timezone

C = 1e7  # one crore
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)  # 5 Oct 2026 IST
YEARS = ["2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31"]
QUARTERS = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


def by(periods, values, scale=C):
    return {p: (None if v is None else v * scale) for p, v in zip(periods, values)}


def annual(values, scale=C):
    return by(YEARS, values, scale)


def quarterly(values, scale=C):
    return by(QUARTERS, values, scale)


# A company whose figures can be worked out by hand (amounts in INR crore, 10 crore shares, price 266.2).
ACME = {
    "info": {"sector": "Technology", "sharesOutstanding": 1e8, "dividendRate": 5.0},
    "annual": {
        "income": {
            "Total Revenue": annual([1000, 1100, 1210, 1331]),
            "Net Income": annual([100, 110, 121, 133.1]),
            "Operating Income": annual([130, 165, 181.5, 199.65]),
            "EBIT": annual([130, 165, 181.5, 199.65]),
            "Gross Profit": annual([400, 440, 484, 532.4]),
            "Interest Expense": annual([-20, -20, -20, -20]),
            "Diluted EPS": by(YEARS, [10, 11, 12.1, 13.31], 1),
        },
        "balance": {
            "Stockholders Equity": annual([500, 600, 700, 800]),
            "Total Debt": annual([200, 200, 200, 200]),
            "Total Assets": annual([1000, 1100, 1200, 1300]),
            "Current Assets": annual([400, 450, 480, 500]),
            "Current Liabilities": annual([250, 250, 250, 250]),
        },
        "cashflow": {
            "Operating Cash Flow": annual([120, 130, 140, 160]),
            "Capital Expenditure": annual([-50, -50, -60, -60]),
            "Depreciation And Amortization": annual([30, 30, 35, 40]),
        },
    },
    "quarterly": {
        "income": {
            "Total Revenue": quarterly([300, 320, 330, 350, 345]),
            "Net Income": quarterly([30, 31, 32, 33, 36]),
            "Diluted EPS": by(QUARTERS, [2.8, 3.0, 3.2, 3.4, 3.71], 1),
        }
    },
    "earnings_dates": [
        {"date": "2026-10-20", "estimate": 3.9, "reported": None, "surprise_pct": None},
        {"date": "2026-07-20", "estimate": 3.5, "reported": 3.71, "surprise_pct": 6.0},
        {"date": "2026-04-20", "estimate": 3.3, "reported": 3.4, "surprise_pct": 3.03},
        {"date": "2026-01-20", "estimate": 3.3, "reported": 3.2, "surprise_pct": -3.03},
        {"date": "2025-10-20", "estimate": 2.9, "reported": 3.0, "surprise_pct": 3.45},
    ],
    "quality_flags": [],
}
PRICE = 266.2


def index_history(latest_pe=19.3):
    days, day = [], date(2026, 10, 5)
    while len(days) < 300:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return {d: {"pe": latest_pe if i == 0 else (18.0 if i < 150 else 21.0), "pb": 2.7, "div_yield": 1.2} for i, d in enumerate(days)}
```

`tests/test_metrics_fundamentals.py`:

```python
import copy
import json
import pytest

from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet

from fund_fixtures import ACME, NOW, PRICE, QUARTERS, annual, by, index_history


def packet(payload=ACME, price=PRICE, history=None, **overrides):
    return build_fundamentals_packet("ACME", NOW, payload, price, NOW, history if history is not None else index_history(), **overrides)


def value(p, name):
    return p["metrics"][name]["value"]


def test_valuation_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "last_price") == PRICE and value(p, "market_cap") == pytest.approx(2662.0)
    assert value(p, "pe_trailing") == pytest.approx(20.0, abs=1e-3)  # 266.2 over 3.0 + 3.2 + 3.4 + 3.71
    assert value(p, "pb") == pytest.approx(3.3275, abs=1e-3)  # book value 80 per share
    assert value(p, "earnings_yield") == pytest.approx(0.05, abs=1e-4)
    assert value(p, "fcf_yield") == pytest.approx(100 / 2662, abs=1e-4)  # (160 - 60) over market cap
    assert value(p, "owner_earnings_yield") == pytest.approx(113.1 / 2662, abs=1e-4)  # 133.1 + 40 - 60
    assert value(p, "peg") == pytest.approx(2.0, abs=5e-3)  # P/E 20 over 10 percent growth
    assert value(p, "graham_number_premium") == pytest.approx(266.2 / (22.5 * 13.31 * 80) ** 0.5 - 1, abs=1e-4)
    assert value(p, "dividend_yield") == pytest.approx(5 / 266.2, abs=1e-4)


def test_index_context_compares_the_stock_with_the_nifty_50():
    p = packet()
    assert value(p, "index_pe") == 19.3 and value(p, "pe_vs_index") == pytest.approx(20 / 19.3, abs=1e-3)
    assert value(p, "index_pe_percentile") == pytest.approx(100 * 150 / 300, abs=0.01)  # 149 days at 18.0 plus itself, below the 150 days at 21.0


def test_quality_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "roe_latest") == pytest.approx(133.1 / 750, abs=1e-4)  # average of 800 and 700
    assert value(p, "roe_minimum") == pytest.approx(133.1 / 750, abs=1e-4)
    assert 0.17 < value(p, "roe_average") < 0.21
    assert value(p, "net_margin_latest") == pytest.approx(0.1, abs=1e-6)
    assert value(p, "operating_margin_latest") == pytest.approx(0.15, abs=1e-6)
    assert value(p, "operating_margin_change") == pytest.approx(2.0, abs=1e-6)  # 13 percent to 15 percent
    assert value(p, "gross_margin_latest") == pytest.approx(0.4, abs=1e-6)
    assert value(p, "revenue_cagr") == pytest.approx(0.1, abs=2e-3) and value(p, "earnings_cagr") == pytest.approx(0.1, abs=2e-3)
    assert value(p, "debt_to_equity") == pytest.approx(0.25) and value(p, "current_ratio") == pytest.approx(2.0)
    assert value(p, "interest_coverage") == pytest.approx(199.65 / 20, abs=1e-3)
    assert value(p, "asset_turnover") == pytest.approx(1331 / 1250, abs=1e-3)


def test_earnings_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "accruals_ratio") == pytest.approx((133.1 - 160) / 1250, abs=1e-4)
    assert value(p, "cash_conversion") == pytest.approx(160 / 133.1, abs=1e-4)
    assert value(p, "revenue_growth_yoy_quarter") == pytest.approx(0.15) and value(p, "net_income_growth_yoy_quarter") == pytest.approx(0.2)
    assert value(p, "eps_surprise_last") == pytest.approx(0.06) and value(p, "beats_last_4") == 3
    assert value(p, "eps_surprise_average_4") == pytest.approx((6.0 + 3.03 - 3.03 + 3.45) / 400, abs=1e-4)
    assert value(p, "days_to_next_earnings") == 15


def test_a_complete_company_has_nothing_missing_and_every_metric_names_its_group_unit_inputs_and_window():
    p = packet()
    assert p["missing"] == [] and p["missing_reasons"] == {}
    for name, metric in p["metrics"].items():
        assert metric["group"] in ("valuation", "quality", "earnings") and metric["unit"] and metric["inputs"] and metric["window"], name
    assert p["latest_annual_period"] == "2026-03-31" and p["latest_quarter"] == "2026-06-30" and p["is_lender"] is False


def test_the_packet_is_json_ready():
    json.dumps(packet())


def test_a_lender_gets_no_cash_flow_or_working_capital_ratios_with_the_reason_stated():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    for line in ("Operating Income", "EBIT", "Gross Profit", "Interest Expense"):
        del bank["annual"]["income"][line]
    for line in ("Current Assets", "Current Liabilities"):
        del bank["annual"]["balance"][line]
    p = packet(bank)
    assert p["is_lender"] is True
    for name in ("fcf_yield", "owner_earnings_yield", "operating_margin_latest", "operating_margin_change", "gross_margin_latest",
                 "debt_to_equity", "current_ratio", "interest_coverage", "asset_turnover", "accruals_ratio", "cash_conversion"):
        assert p["missing_reasons"][name] == LENDER_REASON, name
    for name in ("pe_trailing", "pb", "graham_number_premium", "roe_latest", "net_margin_latest", "revenue_cagr", "eps_surprise_last", "peg"):
        assert name in p["metrics"], name


def test_without_a_price_every_price_based_figure_is_missing_but_fundamentals_still_compute():
    p = packet(price=None)
    for name in ("last_price", "market_cap", "pe_trailing", "pb", "fcf_yield", "peg", "graham_number_premium", "dividend_yield"):
        assert p["missing_reasons"][name] == "no current price", name
    assert "roe_latest" in p["metrics"] and "revenue_cagr" in p["metrics"] and "eps_surprise_last" in p["metrics"]


def test_losses_and_stalled_growth_remove_the_ratios_that_would_mislead():
    losing = copy.deepcopy(ACME)
    losing["quarterly"]["income"]["Diluted EPS"] = by(QUARTERS, [-1, -1, -1, -1, -1], 1)
    p = packet(losing)
    assert p["missing_reasons"]["pe_trailing"] == "earnings per share is not positive"
    assert "graham_number_premium" in p["missing"] and "earnings_yield" in p["missing"]
    flat = copy.deepcopy(ACME)
    flat["annual"]["income"]["Net Income"] = annual([100, 100, 100, 100])
    assert packet(flat)["missing_reasons"]["peg"] == "earnings are not growing"


def test_too_few_fiscal_years_remove_the_growth_and_persistence_figures():
    short = copy.deepcopy(ACME)
    for group in short["annual"].values():
        for line, series in group.items():
            group[line] = {period: v for period, v in series.items() if period >= "2025-03-31"}
    p = packet(short)
    for name in ("revenue_cagr", "earnings_cagr", "peg", "roe_average", "roe_minimum"):
        assert name in p["missing"], name
    assert "at least 3 annual periods" in p["missing_reasons"]["revenue_cagr"]
    assert "roe_latest" in p["metrics"]


def test_a_gap_in_the_quarters_falls_back_to_annual_eps_and_says_so():
    gappy = copy.deepcopy(ACME)
    gappy["quarterly"]["income"]["Diluted EPS"]["2025-12-31"] = None
    p = packet(gappy)
    assert value(p, "pe_trailing") == pytest.approx(PRICE / 13.31, abs=1e-3)
    assert "annual EPS used" in p["metrics"]["pe_trailing"]["note"]
    assert "fiscal year to 2026-03-31" in p["metrics"]["pe_trailing"]["window"]


def test_quarter_growth_needs_the_same_quarter_a_year_earlier():
    no_base = copy.deepcopy(ACME)
    del no_base["quarterly"]["income"]["Total Revenue"]["2025-06-30"]
    p = packet(no_base)
    assert p["missing_reasons"]["revenue_growth_yoy_quarter"] == "the same quarter a year earlier is not available"
    assert "net_income_growth_yoy_quarter" in p["metrics"]


def test_a_short_index_history_keeps_the_index_pe_but_drops_the_percentile():
    short = {d: row for d, row in list(index_history().items())[:50]}
    p = packet(history=short)
    assert "index_pe" in p["metrics"] and "index_pe_percentile" in p["missing"]
    assert "pe_vs_index" in p["metrics"]


def test_no_upcoming_date_and_no_dividend_are_reported_not_invented():
    quiet = copy.deepcopy(ACME)
    quiet["earnings_dates"] = quiet["earnings_dates"][1:]
    quiet["info"]["dividendRate"] = None
    p = packet(quiet)
    assert p["missing_reasons"]["days_to_next_earnings"] == "no upcoming earnings date"
    assert p["missing_reasons"]["dividend_yield"] == "no dividend reported"


def test_an_empty_payload_is_all_missing_not_an_error_and_flags_travel_with_the_packet():
    empty = packet({}, history={})
    assert empty["metrics"].keys() == {"last_price"} and len(empty["missing"]) > 20
    flagged = copy.deepcopy(ACME)
    flagged["quality_flags"] = ["quarterly periods x, y carry identical figures (possible duplicate); treated as missing"]
    assert packet(flagged)["data_quality_flags"] == flagged["quality_flags"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_fundamentals.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.metrics.fundamentals'`.

- [ ] **Step 3: Write `src/athena/metrics/fundamentals.py`**

```python
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, NamedTuple

from athena.contracts import InsufficientData
from athena.trading_calendar import ist_date

FINANCIAL_SECTORS = frozenset({"Financial Services"})
LENDER_REASON = "not meaningful for banks and lenders"
CRORE = 1e7
SOURCE = "computed from Yahoo statements (INR)"
GRAHAM_MULTIPLIER = 22.5  # Graham's ceiling: P/E 15 x P/B 1.5
QUARTER_SPAN_DAYS = 290  # four consecutive quarter-ends span about 273 days
YEAR_WINDOW = (350, 380)  # days between a quarter and the same quarter a year earlier
MIN_INDEX_HISTORY = 250  # trading days needed before an index percentile means anything
VALUATION, QUALITY, EARNINGS = "valuation", "quality", "earnings"
GROUPS = (VALUATION, QUALITY, EARNINGS)


class R(NamedTuple):
    value: float | str
    window: str
    note: str | None = None


def _periods(series: Mapping[str, float | None]) -> list[tuple[date, float]]:
    return sorted((date.fromisoformat(period), value) for period, value in series.items() if value is not None)


@dataclass(frozen=True)
class Statements:
    """Parsed `equity.fundamentals` payload with small accessors; missing items give empty series, never errors."""

    info: Mapping[str, Any]
    annual: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    quarterly: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    earnings_dates: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Statements:
        return cls(
            payload.get("info", {}), payload.get("annual", {}), payload.get("quarterly", {}), tuple(payload.get("earnings_dates", ()))
        )

    @property
    def is_financial(self) -> bool:
        return self.info.get("sector") in FINANCIAL_SECTORS

    def annual_series(self, group: str, line: str) -> list[tuple[date, float]]:
        return _periods(self.annual.get(group, {}).get(line, {}))

    def quarterly_series(self, line: str) -> list[tuple[date, float]]:
        return _periods(self.quarterly.get("income", {}).get(line, {}))


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise InsufficientData(message)


def _span(points: list[tuple[date, float]]) -> str:
    return f"{points[0][0].isoformat()}..{points[-1][0].isoformat()} ({len(points)} periods)"


def _aligned(first: list[tuple[date, float]], second: list[tuple[date, float]]) -> list[tuple[date, float, float]]:
    other = dict(second)
    return [(day, value, other[day]) for day, value in first if day in other]


def _cagr(points: list[tuple[date, float]], what: str) -> tuple[float, str]:
    _need(len(points) >= 3, f"{what} needs at least 3 annual periods, have {len(points)}")
    (start, first), (end, last) = points[0], points[-1]
    _need(first > 0 and last > 0, f"{what} needs positive first and last values")
    years = (end - start).days / 365.25
    return (last / first) ** (1 / years) - 1, _span(points)


def _percentile(values: list[float], x: float) -> float:
    return 100.0 * sum(1 for value in values if value <= x) / len(values)


def build_fundamentals_packet(
    instrument: str,
    as_of: datetime,
    payload: Mapping[str, Any],
    price: float | None,
    now: datetime,
    index_history: Mapping[date, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Valuation, quality and earnings figures for one stock, in the metrics-packet shape (TRD section 3).

    Every figure is computed here from the stored statements; one that cannot be computed (too few periods, a
    lender where the ratio has no meaning, a missing price) is listed in `missing` with the reason."""
    st = Statements.from_payload(payload)
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    groups: dict[str, str] = {}

    def attempt(name: str, group: str, unit: str, inputs: list[str], compute: Callable[[], R]) -> None:
        groups[name] = group
        try:
            result = compute()
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metric: dict[str, Any] = {
            "value": result.value if isinstance(result.value, str) else round(float(result.value), 4),
            "unit": unit,
            "inputs": inputs,
            "window": result.window,
            "source": SOURCE,
            "group": group,
        }
        if result.note:
            metric["note"] = result.note
        metrics[name] = metric

    def non_lender() -> None:
        _need(not st.is_financial, LENDER_REASON)

    def have_price() -> float:
        _need(price is not None and price > 0, "no current price")
        return price  # type: ignore[return-value]

    def shares() -> float:
        reported = st.info.get("sharesOutstanding")
        if reported:
            return float(reported)
        counted = st.annual_series("balance", "Ordinary Shares Number")
        _need(bool(counted), "no share count")
        return counted[-1][1]

    def eps_ttm() -> tuple[float, str, str | None]:
        quarters = st.quarterly_series("Diluted EPS")[-4:]
        if len(quarters) == 4 and (quarters[-1][0] - quarters[0][0]).days <= QUARTER_SPAN_DAYS:
            return sum(value for _, value in quarters), f"last 4 quarters to {quarters[-1][0].isoformat()}", None
        annual = st.annual_series("income", "Diluted EPS")
        _need(bool(annual), "no diluted EPS")
        return annual[-1][1], f"fiscal year to {annual[-1][0].isoformat()}", "annual EPS used because four consecutive quarters were not available"

    def book_value_per_share() -> float:
        equity = st.annual_series("balance", "Stockholders Equity")
        _need(bool(equity), "no stockholders' equity")
        return equity[-1][1] / shares()

    def earnings_cagr() -> tuple[float, str]:
        return _cagr(st.annual_series("income", "Net Income"), "earnings growth")

    # ---- valuation
    attempt("last_price", VALUATION, "INR", ["close"], lambda: R(have_price(), "last close"))
    attempt("market_cap", VALUATION, "INR crore", ["close", "shares"], lambda: R(have_price() * shares() / CRORE, "last close x shares outstanding"))

    def pe() -> R:
        eps, window, note = eps_ttm()
        _need(eps > 0, "earnings per share is not positive")
        return R(have_price() / eps, window, note)

    attempt("pe_trailing", VALUATION, "ratio", ["close", "diluted EPS"], pe)

    def pb() -> R:
        bvps = book_value_per_share()
        _need(bvps > 0, "book value is not positive")
        return R(have_price() / bvps, "latest fiscal year equity")

    attempt("pb", VALUATION, "ratio", ["close", "stockholders equity", "shares"], pb)

    def earnings_yield() -> R:
        result = pe()
        return R(1 / float(result.value), result.window, result.note)

    attempt("earnings_yield", VALUATION, "fraction", ["close", "diluted EPS"], earnings_yield)

    def cash_yield(owner: bool) -> R:
        non_lender()
        if owner:
            profit_and_depreciation = _aligned(st.annual_series("income", "Net Income"), st.annual_series("cashflow", "Depreciation And Amortization"))
            earned = [(day, profit + depreciation) for day, profit, depreciation in profit_and_depreciation]
        else:
            earned = st.annual_series("cashflow", "Operating Cash Flow")
        joined = _aligned(earned, st.annual_series("cashflow", "Capital Expenditure"))
        _need(bool(joined), "no fiscal year has all the cash-flow lines")
        day, cash, spend = joined[-1]
        note = "capital expenditure is not split into maintenance and growth, so all of it is deducted" if owner else None
        return R((cash - abs(spend)) / (have_price() * shares()), f"fiscal year to {day.isoformat()}", note)

    attempt("fcf_yield", VALUATION, "fraction", ["operating cash flow", "capital expenditure", "market cap"], lambda: cash_yield(False))
    attempt(
        "owner_earnings_yield", VALUATION, "fraction",
        ["net income", "depreciation and amortization", "capital expenditure", "market cap"], lambda: cash_yield(True),
    )

    def peg() -> R:
        growth, window = earnings_cagr()
        _need(growth > 0, "earnings are not growing")
        return R(float(pe().value) / (growth * 100), window, "trailing P/E over the earnings growth rate in percent")

    attempt("peg", VALUATION, "ratio", ["pe_trailing", "net income"], peg)

    def graham() -> R:
        eps, window, _ = eps_ttm()
        bvps = book_value_per_share()
        _need(eps > 0 and bvps > 0, "Graham's number needs positive earnings and book value")
        return R(have_price() / math.sqrt(GRAHAM_MULTIPLIER * eps * bvps) - 1, window, "above 0 means the price is above Graham's ceiling")

    attempt("graham_number_premium", VALUATION, "fraction", ["close", "diluted EPS", "book value per share"], graham)

    def dividend_yield() -> R:
        rate = st.info.get("dividendRate")
        _need(bool(rate), "no dividend reported")
        return R(float(rate) / have_price(), "annual dividend rate over last close", "computed from the dividend rate, not Yahoo's yield field")

    attempt("dividend_yield", VALUATION, "fraction", ["dividend rate", "close"], dividend_yield)

    def index_pe() -> R:
        _need(bool(index_history), "no index valuation history")
        latest = max(index_history)  # type: ignore[arg-type]
        return R(index_history[latest]["pe"], f"NIFTY 50 on {latest.isoformat()}")  # type: ignore[index]

    attempt("index_pe", VALUATION, "ratio", ["NIFTY 50 P/E"], index_pe)
    attempt("pe_vs_index", VALUATION, "ratio", ["pe_trailing", "index_pe"], lambda: R(float(pe().value) / float(index_pe().value), "stock P/E over NIFTY 50 P/E"))

    def index_percentile() -> R:
        _need(bool(index_history) and len(index_history) >= MIN_INDEX_HISTORY, "index valuation history is too short")
        values = [row["pe"] for row in index_history.values()]  # type: ignore[union-attr]
        latest = index_history[max(index_history)]["pe"]  # type: ignore[index]
        return R(_percentile(values, latest), f"{len(values)} trading days of NIFTY 50 P/E")

    attempt("index_pe_percentile", VALUATION, "percent", ["NIFTY 50 P/E history"], index_percentile)

    # ---- quality (the ratio-only view of moat and business quality)
    def roe_by_year() -> list[tuple[date, float]]:
        equity = st.annual_series("balance", "Stockholders Equity")
        out = []
        for day, profit, closing in _aligned(st.annual_series("income", "Net Income"), equity):
            earlier = [value for d, value in equity if d < day]
            average = (closing + earlier[-1]) / 2 if earlier else closing
            if average > 0:
                out.append((day, profit / average))
        return out

    def roe(pick: Callable[[list[float]], float], minimum: int) -> R:
        values = roe_by_year()
        _need(len(values) >= minimum, f"needs {minimum} fiscal years of net income and equity, have {len(values)}")
        return R(pick([v for _, v in values]), _span(values), "net income over average equity")

    attempt("roe_latest", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(lambda v: v[-1], 1))
    attempt("roe_average", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(lambda v: sum(v) / len(v), 3))
    attempt("roe_minimum", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(min, 3))

    def margin(numerator: str, change: bool = False, lender_ok: bool = True) -> R:
        if not lender_ok:
            non_lender()
        pairs = _aligned(st.annual_series("income", numerator), st.annual_series("income", "Total Revenue"))
        _need(bool(pairs) and (len(pairs) >= 2 or not change), f"no fiscal year has {numerator} and revenue")
        ratios = [(day, top / revenue) for day, top, revenue in pairs if revenue]
        _need(bool(ratios), "revenue is zero")
        if change:
            _need(len(ratios) >= 2, "needs 2 fiscal years")
            return R((ratios[-1][1] - ratios[0][1]) * 100, _span(ratios), "latest minus earliest, in percentage points")
        return R(ratios[-1][1], f"fiscal year to {ratios[-1][0].isoformat()}")

    attempt("net_margin_latest", QUALITY, "fraction", ["net income", "revenue"], lambda: margin("Net Income"))
    attempt("operating_margin_latest", QUALITY, "fraction", ["operating income", "revenue"], lambda: margin("Operating Income", lender_ok=False))
    attempt("operating_margin_change", QUALITY, "percentage points", ["operating income", "revenue"], lambda: margin("Operating Income", change=True, lender_ok=False))
    attempt("gross_margin_latest", QUALITY, "fraction", ["gross profit", "revenue"], lambda: margin("Gross Profit", lender_ok=False))

    def cagr(group: str, line: str, what: str) -> R:
        value, window = _cagr(st.annual_series(group, line), what)
        return R(value, window, "compound annual growth between the first and last fiscal year")

    attempt("revenue_cagr", QUALITY, "fraction", ["revenue"], lambda: cagr("income", "Total Revenue", "revenue growth"))
    attempt("earnings_cagr", QUALITY, "fraction", ["net income"], lambda: cagr("income", "Net Income", "earnings growth"))

    def latest_ratio(top: tuple[str, str], bottom: tuple[str, str], what: str) -> R:
        non_lender()
        pairs = _aligned(st.annual_series(*top), st.annual_series(*bottom))
        _need(bool(pairs), f"no fiscal year has the lines for {what}")
        day, upper, lower = pairs[-1]
        _need(lower != 0, f"{what} has a zero denominator")
        return R(upper / lower, f"fiscal year to {day.isoformat()}")

    attempt("debt_to_equity", QUALITY, "ratio", ["total debt", "stockholders equity"], lambda: latest_ratio(("balance", "Total Debt"), ("balance", "Stockholders Equity"), "debt to equity"))
    attempt("current_ratio", QUALITY, "ratio", ["current assets", "current liabilities"], lambda: latest_ratio(("balance", "Current Assets"), ("balance", "Current Liabilities"), "current ratio"))

    def interest_coverage() -> R:
        non_lender()
        pairs = _aligned(st.annual_series("income", "EBIT"), [(d, abs(v)) for d, v in st.annual_series("income", "Interest Expense")])
        _need(bool(pairs), "no fiscal year has EBIT and interest expense")
        day, ebit, interest = pairs[-1]
        _need(interest > 0, "no interest expense")
        return R(ebit / interest, f"fiscal year to {day.isoformat()}")

    attempt("interest_coverage", QUALITY, "ratio", ["EBIT", "interest expense"], interest_coverage)

    def average_assets(day: date) -> float:
        assets = dict(st.annual_series("balance", "Total Assets"))
        _need(day in assets, "no total assets for the year")
        earlier = sorted((d, value) for d, value in assets.items() if d < day)
        return (assets[day] + earlier[-1][1]) / 2 if earlier else assets[day]

    def asset_turnover() -> R:
        non_lender()
        pairs = st.annual_series("income", "Total Revenue")
        _need(bool(pairs), "no revenue")
        day, revenue = pairs[-1]
        return R(revenue / average_assets(day), f"fiscal year to {day.isoformat()}")

    attempt("asset_turnover", QUALITY, "ratio", ["revenue", "total assets"], asset_turnover)

    # ---- earnings
    def accruals() -> tuple[date, float, float, float]:
        non_lender()
        joined = _aligned(st.annual_series("income", "Net Income"), st.annual_series("cashflow", "Operating Cash Flow"))
        _need(bool(joined), "no fiscal year has net income and operating cash flow")
        day, profit, cash = joined[-1]
        return day, profit, cash, average_assets(day)

    def accruals_ratio() -> R:
        day, profit, cash, assets = accruals()
        return R((profit - cash) / assets, f"fiscal year to {day.isoformat()}", "Sloan accruals: (net income - operating cash flow) over average assets; high positive values flag low earnings quality")

    def cash_conversion() -> R:
        day, profit, cash, _ = accruals()
        _need(profit > 0, "net income is not positive")
        return R(cash / profit, f"fiscal year to {day.isoformat()}", "operating cash flow over net income")

    attempt("accruals_ratio", EARNINGS, "fraction", ["net income", "operating cash flow", "total assets"], accruals_ratio)
    attempt("cash_conversion", EARNINGS, "ratio", ["operating cash flow", "net income"], cash_conversion)

    def quarter_growth(line: str) -> R:
        points = st.quarterly_series(line)
        _need(bool(points), f"no quarterly {line}")
        day, latest = points[-1]
        earlier = [value for d, value in points if YEAR_WINDOW[0] <= (day - d).days <= YEAR_WINDOW[1]]
        _need(bool(earlier), "the same quarter a year earlier is not available")
        _need(earlier[0] > 0, "the year-earlier figure is not positive")
        return R(latest / earlier[0] - 1, f"quarter to {day.isoformat()} against the same quarter a year earlier")

    attempt("revenue_growth_yoy_quarter", EARNINGS, "fraction", ["quarterly revenue"], lambda: quarter_growth("Total Revenue"))
    attempt("net_income_growth_yoy_quarter", EARNINGS, "fraction", ["quarterly net income"], lambda: quarter_growth("Net Income"))

    def surprises() -> list[tuple[str, float]]:
        rows = [(row["date"], row["surprise_pct"] / 100) for row in st.earnings_dates if row.get("reported") is not None and row.get("surprise_pct") is not None]
        _need(bool(rows), "no reported earnings with a surprise figure")
        return sorted(rows, reverse=True)

    attempt("eps_surprise_last", EARNINGS, "fraction", ["reported EPS", "EPS estimate"], lambda: R(surprises()[0][1], f"report of {surprises()[0][0]}", "against Yahoo's EPS estimate"))
    attempt("eps_surprise_average_4", EARNINGS, "fraction", ["reported EPS", "EPS estimate"], lambda: R(sum(v for _, v in surprises()[:4]) / len(surprises()[:4]), f"last {len(surprises()[:4])} reports"))
    attempt("beats_last_4", EARNINGS, "count", ["reported EPS", "EPS estimate"], lambda: R(sum(1 for _, v in surprises()[:4] if v > 0), f"of the last {len(surprises()[:4])} reports"))

    def days_to_next() -> R:
        today = ist_date(now)
        future = sorted(date.fromisoformat(row["date"]) for row in st.earnings_dates if date.fromisoformat(row["date"]) > today)
        _need(bool(future), "no upcoming earnings date")
        return R((future[0] - today).days, f"next report {future[0].isoformat()}")

    attempt("days_to_next_earnings", EARNINGS, "days", ["earnings calendar"], days_to_next)

    annual_dates = [day for day, _ in st.annual_series("income", "Total Revenue")]
    quarter_dates = [day for day, _ in st.quarterly_series("Total Revenue")]
    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "sector": st.info.get("sector"),
        "is_lender": st.is_financial,
        "latest_annual_period": annual_dates[-1].isoformat() if annual_dates else None,
        "latest_quarter": quarter_dates[-1].isoformat() if quarter_dates else None,
        "data_quality_flags": list(payload.get("quality_flags", [])),
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
        "groups": groups,
    }
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_metrics_fundamentals.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `15 passed`, then `421 passed, 48 skipped`.

- [ ] **Step 5: Mutation checks (do not commit these edits)**

In `metrics/fundamentals.py`: (a) replace `_need(not st.is_financial, LENDER_REASON)` with `pass`: expect `test_a_lender_gets_no_cash_flow_or_working_capital_ratios_with_the_reason_stated` to FAIL. Undo. (b) replace `return R((cash - abs(spend)) /` with `return R((cash - spend) /`: expect `test_valuation_figures_match_a_hand_calculation` to FAIL (capex arrives negative). Undo. (c) replace `<= QUARTER_SPAN_DAYS` with `<= 10000`: expect `test_a_gap_in_the_quarters_falls_back_to_annual_eps_and_says_so` to FAIL. Undo and confirm `git diff` is empty for `metrics/fundamentals.py`.

- [ ] **Step 6: Commit and push**

```bash
git add tests/fund_fixtures.py src/athena/metrics/fundamentals.py tests/test_metrics_fundamentals.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add fundamentals metrics packet (valuation, quality, earnings)"
git push
```

---

### Task 4: Dashboard panels

**Files:**
- Modify (replace the whole file with the version below): `src/athena/dashboard/view.py`, `src/athena/dashboard/service.py`, `tests/dash_fakes.py`, `tests/test_dashboard_view.py`, `tests/test_dashboard_service.py`, `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: Task 1-3 outputs; Plan 1d's `DashboardView`, `Panel`, `MetricRow`, `build_view`, `DashboardService`, `RequestCache`, `live_service`.
- Produces (`athena.dashboard.view`): `build_view(result, bars=(), technical=None, risk=None, fundamentals=None, fundamentals_note=None)`; with a fundamentals packet it appends three panels titled `Valuation`, `Business quality`, `Earnings`, each with as-of `"annual to <date>, quarter to <date>"`, its own rows, its own missing reasons and a coverage label (`insufficient` with no figures, `partial` with any missing, else `full`); the packet's data-quality flags become notes, a lender gets `LENDER_NOTE`, an ETF gets `ETF_FUNDAMENTALS_NOTE`, and `fundamentals_note` (a failure message) is appended. Constants `ETF_FUNDAMENTALS_NOTE`, `LENDER_NOTE`, `FUNDAMENTAL_PANELS`.
- Produces (`athena.dashboard.service`): `FundamentalsSource = Callable[[Resolution, list[Bar], datetime], dict]`; `LiveFundamentals(store, loader, index_history)` (refreshes the statements for the resolved symbol, builds the packet against the last close); `DashboardService(orchestrator, bars, risk_world, clock=utc_now, fundamentals=None)` asks the source only for `equity` resolutions and turns an `AthenaError` into the note `fundamentals unavailable: <message>`; `INDEX_VALUATION_DAYS`; `live_service` loads eight years of NIFTY 50 valuation once and wires `LiveFundamentals`.
- Produces (`tests/dash_fakes.py`): adds `FUNDAMENTALS` (the ACME packet) and `fundamentals`/`fundamentals_note` arguments on `full_view`.

- [ ] **Step 1: Replace the test files and fixtures**

`tests/dash_fakes.py`:

```python
"""Shared fixtures for the dashboard tests: a realistic result, view and service built from synthetic data."""
from datetime import datetime, timezone

from bar_factory import make_bars

from athena.contracts import AthenaError
from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, Resolution
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.technicals.packet import build_technical_packet
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
import copy

from dash_fakes import BARS, FUNDAMENTALS, RISK, TECHNICAL, ambiguous_result, full_view, ok_result
from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.dashboard.view import ETF_FUNDAMENTALS_NOTE, LENDER_NOTE, UNMAPPED_ETF_NOTE, build_view
from athena.metrics.fundamentals import build_fundamentals_packet
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


# ---- fundamentals panels
def test_a_stock_with_fundamentals_gets_three_more_panels_after_technical_and_risk():
    panels = full_view(fundamentals=FUNDAMENTALS).panels
    assert [p.title for p in panels][2:] == ["Valuation", "Business quality", "Earnings"]
    assert all(p.coverage == "full" and not p.missing for p in panels[2:])
    assert panels[2].as_of == "annual to 2026-03-31, quarter to 2026-06-30" and "NSE index valuation" in panels[2].source


def test_each_fundamentals_panel_holds_only_its_own_group_and_copies_the_packet_figures():
    valuation, quality, earnings = full_view(fundamentals=FUNDAMENTALS).panels[2:]
    names = {p.title: {row.name for row in p.rows} for p in (valuation, quality, earnings)}
    assert {"pe_trailing", "pb", "fcf_yield", "peg", "index_pe"} <= names["Valuation"]
    assert {"roe_latest", "net_margin_latest", "revenue_cagr", "debt_to_equity"} <= names["Business quality"]
    assert {"accruals_ratio", "eps_surprise_last", "days_to_next_earnings"} <= names["Earnings"]
    assert not (names["Valuation"] & names["Business quality"]) and not (names["Business quality"] & names["Earnings"])
    pe = next(row for row in valuation.rows if row.name == "pe_trailing")
    assert pe.value == FUNDAMENTALS["metrics"]["pe_trailing"]["value"] and pe.unit == "ratio" and pe.window


def test_a_group_with_missing_figures_is_partial_and_lists_why():
    thin = copy.deepcopy(ACME)
    thin["earnings_dates"] = []
    packet = build_fundamentals_packet("SBIN", NOW, thin, PRICE, NOW, index_history())
    earnings = full_view(fundamentals=packet).panels[4]
    assert earnings.coverage == "partial" and earnings.missing["eps_surprise_last"] == "no reported earnings with a surprise figure"
    assert full_view(fundamentals=packet).panels[2].coverage == "full"


def test_a_group_with_no_figures_at_all_is_insufficient():
    empty = build_fundamentals_packet("SBIN", NOW, {}, None, NOW, {})
    assert [p.coverage for p in full_view(fundamentals=empty).panels[2:]] == ["insufficient"] * 3


def test_data_quality_flags_and_the_lender_note_reach_the_notes():
    flagged = copy.deepcopy(ACME)
    flagged["quality_flags"] = ["quarterly periods a, b carry identical figures (possible duplicate); treated as missing"]
    flagged["info"]["sector"] = "Financial Services"
    notes = full_view(fundamentals=build_fundamentals_packet("SBIN", NOW, flagged, PRICE, NOW, index_history())).notes
    assert flagged["quality_flags"][0] in notes and LENDER_NOTE in notes
    assert LENDER_NOTE not in full_view(fundamentals=FUNDAMENTALS).notes


def test_an_etf_gets_no_fundamentals_panels_and_a_note_and_a_failure_note_is_passed_through():
    etf = full_view("etf")
    assert [p.title.split()[0] for p in etf.panels] == ["Technical", "Risk"] and ETF_FUNDAMENTALS_NOTE in etf.notes
    failed = full_view(fundamentals_note="fundamentals unavailable: no statements for 'X'")
    assert "fundamentals unavailable: no statements for 'X'" in failed.notes and len(failed.panels) == 2
```

`tests/test_dashboard_service.py`:

```python
from bar_factory import NOW, make_bars
from dash_fakes import FUNDAMENTALS, ambiguous_result, resolution

from athena.contracts import EmptyRefreshError
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


# ---- fundamentals source
class FakeFundamentals:
    def __init__(self, packet=None, error=None):
        self.packet, self.error, self.calls = packet, error, []

    def __call__(self, resolution, bars, now):
        self.calls.append((resolution.identifier, len(bars), now))
        if self.error:
            raise self.error
        return self.packet


def service_with(fundamentals, asset_class="equity"):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    result = OrchestrationResult(OK, "sbin", resolution(asset_class), None, {}, {}, None, 0.0, VERDICT, ())
    orchestrator = FakeOrchestrator(cache, result)
    return DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW, fundamentals=fundamentals)


def test_a_stock_view_gets_the_fundamentals_panels_built_from_the_same_bars():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source).view("sbin")
    assert source.calls == [("SBIN", len(BARS), NOW)]
    assert [p.title for p in view.panels][2:] == ["Valuation", "Business quality", "Earnings"]


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
```

`tests/test_dashboard_app.py`:

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


def test_the_page_shows_the_three_fundamentals_panels_each_with_coverage():
    app = open_app(FakeService(full_view(fundamentals=dash_fakes.FUNDAMENTALS)), "sbin")
    assert not app.exception and len(app.dataframe) == 5
    bold = texts(app.markdown)
    assert all(any(title in m for m in bold) for title in ("Valuation", "Business quality", "Earnings"))
    captions = texts(app.caption)
    assert sum("annual to 2026-03-31, quarter to 2026-06-30" in c and "coverage: full" in c for c in captions) == 3
    for frame in app.dataframe:
        assert {type(v) for v in frame.value["value"]} == {str}
```

- [ ] **Step 2: Run to verify the new cases fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_view.py tests/test_dashboard_service.py tests/test_dashboard_app.py -q`
Expected: FAIL: `ImportError` for `ETF_FUNDAMENTALS_NOTE` and `LENDER_NOTE` (and the `fundamentals` argument) until the sources below are replaced.

- [ ] **Step 3: Replace `src/athena/dashboard/view.py` and `src/athena/dashboard/service.py`**

`src/athena/dashboard/view.py`:

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
ETF_FUNDAMENTALS_NOTE = "financial statements do not apply to ETFs, so there are no valuation, quality or earnings panels"
LENDER_NOTE = "this is a bank or lender: cash-flow, margin and working-capital ratios do not apply and are listed as unavailable"
FUNDAMENTAL_PANELS = (("valuation", "Valuation"), ("quality", "Business quality"), ("earnings", "Earnings"))


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


def _fundamental_panels(packet: Mapping[str, Any]) -> list[Panel]:
    """One panel per group of the fundamentals packet, each with its own coverage label."""
    groups = packet["groups"]
    as_of = f"annual to {packet['latest_annual_period'] or 'n/a'}, quarter to {packet['latest_quarter'] or 'n/a'}"
    panels = []
    for group, title in FUNDAMENTAL_PANELS:
        rows = tuple(
            MetricRow(name, metric["value"], metric["unit"], metric.get("window") or "", metric.get("note", ""))
            for name, metric in packet["metrics"].items()
            if metric["group"] == group
        )
        missing = {name: packet["missing_reasons"][name] for name in packet["missing"] if groups[name] == group}
        coverage = "insufficient" if not rows else "partial" if missing else "full"
        source = "Yahoo statements, NSE index valuation" if group == "valuation" else "Yahoo statements"
        panels.append(Panel(title, as_of, source, coverage, rows, missing))
    return panels


def build_view(
    result: OrchestrationResult,
    bars: Sequence[Bar] = (),
    technical: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
    fundamentals: Mapping[str, Any] | None = None,
    fundamentals_note: str | None = None,
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

    if fundamentals is not None:
        panels.extend(_fundamental_panels(fundamentals))
        notes.extend(fundamentals["data_quality_flags"])
        if fundamentals["is_lender"]:
            notes.append(LENDER_NOTE)
    if fundamentals_note:
        notes.append(fundamentals_note)
    if resolution.asset_class == "etf":
        notes.append(ETF_FUNDAMENTALS_NOTE)

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

`src/athena/dashboard/service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import AthenaError, Bar
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.loaders.fundamentals import DATASET as FUNDAMENTALS_DATASET
from athena.loaders.fundamentals import FundamentalsLoader
from athena.loaders.index_valuation import IndexValuationLoader, valuation_history
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.orchestrator.builders import HISTORY_DAYS, history_fetcher
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.resolver import Resolution
from athena.store import DataStore
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date


INDEX_VALUATION_DAYS = 365 * 8  # NSE has published index P/E since 2015; eight years is plenty for a percentile


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


FundamentalsSource = Callable[[Resolution, list[Bar], datetime], dict]


class LiveFundamentals:
    """Fetches a stock's statements (Yahoo) into the store and builds its fundamentals packet against the last close
    and the NIFTY 50 valuation history. Raises an `AthenaError` when the source has nothing for the symbol."""

    def __init__(self, store: DataStore, loader: FundamentalsLoader, index_history: dict):
        self._store = store
        self._loader = loader
        self._index_history = index_history

    def __call__(self, resolution: Resolution, bars: list[Bar], now: datetime) -> dict:
        symbol = resolution.identifier
        self._loader.refresh(symbol=symbol)
        record = self._loader.read(FUNDAMENTALS_DATASET, symbol)
        price = bars[-1].close if bars else None
        return build_fundamentals_packet(symbol, record.as_of, record.payload, price, now, self._index_history)


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
    ):
        self._orchestrator = orchestrator
        self._bars = bars
        self._risk_world = risk_world
        self._clock = clock
        self._fundamentals = fundamentals

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
        fundamentals, note = None, None
        if self._fundamentals is not None and result.resolution.asset_class == "equity":
            try:
                fundamentals = self._fundamentals(result.resolution, bars, now)
            except AthenaError as exc:
                note = f"fundamentals unavailable: {exc}"
        return build_view(result, bars, technical, risk, fundamentals, note)


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel are loaded once, when the service
    is built, so a session left open across days should be restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=HISTORY_DAYS))
    IndexValuationLoader(sources.store).refresh(since=ist_date(utc_now()) - timedelta(days=INDEX_VALUATION_DAYS))
    fundamentals = LiveFundamentals(sources.store, FundamentalsLoader(sources.store), valuation_history(sources.store))
    cache = RequestCache(history_fetcher(sources.chain))
    orchestrator = build_orchestrator(sources.resolver, sources.llm_router, sources.chain, fetch_bars=cache)
    return DashboardService(orchestrator, cache, risk_world_from_store(sources.store), fundamentals=fundamentals)
```

- [ ] **Step 4: Run to verify it passes, then the full suite and lint**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_view.py tests/test_dashboard_service.py tests/test_dashboard_app.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `33 passed`, then `432 passed, 48 skipped`. Lint: `.venv/Scripts/python -m pyflakes src/athena/dashboard src/athena/loaders src/athena/metrics tests/dash_fakes.py tests/fund_fixtures.py tests/test_dashboard_*.py tests/test_metrics_fundamentals.py` prints nothing.

- [ ] **Step 5: Mutation checks (do not commit these edits)**

In `service.py`: (a) delete ` and result.resolution.asset_class == "equity"` from the fundamentals condition: expect `test_an_etf_never_asks_for_fundamentals` to FAIL. Undo. (b) change `except AthenaError as exc:` inside `view` to `except ZeroDivisionError as exc:`: expect `test_a_fundamentals_failure_becomes_a_note_and_the_rest_of_the_page_survives` to FAIL. Undo. In `view.py`: (c) replace the `coverage = ...` line in `_fundamental_panels` with `coverage = "full"`: expect `test_a_group_with_missing_figures_is_partial_and_lists_why` and `test_a_group_with_no_figures_at_all_is_insufficient` to FAIL. Undo and confirm `git diff` is empty for both files.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/dashboard/view.py src/athena/dashboard/service.py tests/dash_fakes.py tests/test_dashboard_view.py tests/test_dashboard_service.py tests/test_dashboard_app.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: show valuation, quality and earnings panels on the dashboard"
git push
```

---

### Task 5: Live checks, docs and graph refresh

**Files:**
- Create: `tests/live/test_live_fundamentals.py`
- Modify: `TRD.md`

- [ ] **Step 1: Write the live tests `tests/live/test_live_fundamentals.py`**

```python
from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.dashboard.service import live_service
from athena.loaders.fundamentals import DATASET, FundamentalsLoader
from athena.loaders.index_valuation import IndexValuationLoader, valuation_history
from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet
from athena.store import DataStore
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def world():
    now = utc_now()
    store = DataStore()
    IndexValuationLoader(store).refresh(since=ist_date(now) - timedelta(days=365 * 8))
    return {"now": now, "store": store, "history": valuation_history(store), "loader": FundamentalsLoader(store), "prices": JugaadPriceAdapter()}


def packet_for(world, symbol):
    world["loader"].refresh(symbol=symbol)
    record = world["loader"].read(DATASET, symbol)
    bars = world["prices"].fetch_ohlcv(symbol, "1d", since=ist_date(world["now"]) - timedelta(days=10))
    return build_fundamentals_packet(symbol, record.as_of, record.payload, bars[-1].close, world["now"], world["history"])


def test_live_index_valuation_history_is_long_and_current(world):
    history = world["history"]
    latest = max(history)
    print("\nNIFTY 50 P/E rows:", len(history), "latest", latest, history[latest])
    assert len(history) > 1500 and (ist_date(world["now"]) - latest).days <= 5


def test_live_non_lender_stock_has_every_core_figure(world):
    packet = packet_for(world, "TCS")
    print("\nTCS missing:", packet["missing_reasons"], "| flags:", packet["data_quality_flags"])
    for name in ("market_cap", "pe_trailing", "pb", "fcf_yield", "owner_earnings_yield", "roe_latest", "operating_margin_latest",
                 "revenue_cagr", "earnings_cagr", "debt_to_equity", "accruals_ratio", "eps_surprise_last", "index_pe_percentile"):
        assert name in packet["metrics"], (name, packet["missing_reasons"].get(name))
    assert 3 < packet["metrics"]["pe_trailing"]["value"] < 100 and packet["is_lender"] is False


def test_live_bank_drops_the_ratios_that_do_not_apply(world):
    packet = packet_for(world, "SBIN")
    assert packet["is_lender"] is True
    for name in ("fcf_yield", "operating_margin_latest", "debt_to_equity", "accruals_ratio"):
        assert packet["missing_reasons"][name] == LENDER_REASON
    assert {"pe_trailing", "pb", "roe_latest", "earnings_cagr"} <= set(packet["metrics"])


def test_live_a_renamed_symbol_fails_loud_with_a_clear_message(world):
    with pytest.raises(AthenaError, match="ZOMATO"):
        world["loader"].refresh(symbol="ZOMATO")  # now ETERNAL on the NSE


@pytest.fixture(scope="module")
def service():
    try:
        return live_service()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


def titles(view):
    return [panel.title for panel in view.panels]


def test_live_dashboard_shows_fundamentals_for_a_stock_but_not_an_etf(service):
    stock = service.view("TCS")
    print("\nTCS panels:", titles(stock), [p.coverage for p in stock.panels], "| notes:", stock.notes)
    assert titles(stock)[2:] == ["Valuation", "Business quality", "Earnings"]
    assert not any(note.startswith("fundamentals unavailable") for note in stock.notes)
    etf = service.view("NIFTYBEES")
    assert len(etf.panels) == 2 and any("do not apply to ETFs" in note for note in etf.notes)
```

- [ ] **Step 2: Run the default and live suites**

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest --live tests/live/test_live_fundamentals.py -q -s
```

Expected: `432 passed, 53 skipped`, then `5 passed` with the NIFTY 50 history length, the TCS missing list (empty) and the panel coverage labels printed. The first four live tests need no API key; the last one needs the LLM keys in `.env` and is skipped without them. Numbers change daily; a Yahoo outage fails these tests by design (the loader reports it), so re-run before concluding anything. Then look at the page: `.venv/Scripts/python -m athena.dashboard --server.headless true`, open http://localhost:8501 and try `TCS` (five panels), `SBIN` (bank note), `ITC` (a data-quality note) and `NIFTYBEES` (no fundamentals panels, with a note).

- [ ] **Step 3: Update `TRD.md`**

1. In the §6.1 table, replace the first row (`Equity Valuation, Moat, Earnings`) with: `| Equity Valuation, Moat, Earnings | Multi-year financial statements, earnings calendar and surprises, index valuation | Yahoo \`.NS\` statements (4 annual periods, 5-6 quarters; banks lack operating and working-capital lines; duplicated or missing quarters occur), Yahoo \`earnings_dates\` (24 reports with estimate and surprise), NSE index P/E, P/B and yield via jugaad-data \`index_pe_raw\` (back to 2015) | Covered, with caveats (verified 5 Oct 2026) | Ratio-only valuation and quality/earnings figures implemented in Plan 1e; lenders get an explicit "not meaningful" list; **not point-in-time, so not backtestable**; no DCF, comparables or net-net |`
2. In the §6.2 table, append three rows: `| yfinance statements (\`financials\`, \`balance_sheet\`, \`cashflow\`, \`quarterly_financials\`) | Work for 15 of 16 NSE symbols tried; \`ZOMATO\` is now \`ETERNAL\`; ITC shows a duplicated quarter, SBIN a missing one; capex is negative; \`returnOnEquity\` and \`dividendYield\` are unreliable |`, `| yfinance \`earnings_dates\` | Works; next report plus EPS estimate, reported and surprise percent for 24 reports; US Eastern timestamps |`, and `| jugaad-data \`NSEIndexHistory.index_pe_raw\` | Works; daily P/E, P/B and dividend yield for any NSE index, back to 2015 |`
3. At the end of the §2.13 paragraph (`**2.13 Metrics engine (new).**`), append: ` *Fundamentals implemented in Plan 1e:* \`metrics/fundamentals.py\` builds valuation, quality and earnings figures from stored statements; see §6.1 for the data caveats.`
4. At the end of the §2.9 paragraph, append: ` Plan 1e added Valuation, Business quality and Earnings panels for stocks (not ETFs).`
5. In the revision history, add after the Phase 1d line: `- **Oct 5, 2026 (Phase 1e)** — Fundamentals data and metrics implemented and shown on the dashboard (see docs/superpowers/plans/2026-10-05-phase-1e-fundamentals-data-and-metrics.md).`

- [ ] **Step 4: Commit, push, refresh graph**

```bash
git add tests/live/test_live_fundamentals.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live fundamentals checks; document fundamentals data in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.3, §2.13, §6.1 -> task):** statements, earnings calendar and surprises, and index valuation as stored, fresh-limited datasets (Tasks 1-2); the specialists' inputs computed in code and never by a model: Graham number and PEG and owner earnings (valuation), ROE level and persistence, margins and their trend, growth, leverage (quality), Sloan accruals, cash conversion, quarter growth, surprise history and the next report date (earnings) (Task 3); ratio-only honesty with named gaps and lender handling (Task 3); visible on the dashboard with as-of and coverage per panel (Task 4); verified on real data and the TRD coverage matrix updated (Task 5). Not in this plan: the three specialists (Plan 1f), DCF, peer comparables, Graham net-net, sector-index context, point-in-time history, any mutual-fund or bond fundamentals.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `parse_valuation_rows`/`IndexValuationLoader`/`valuation_history`, `payload_from_frames`/`FundamentalsLoader`, `build_fundamentals_packet` and its `groups`, `LiveFundamentals`/`DashboardService(fundamentals=...)`, `build_view(..., fundamentals, fundamentals_note)` and the fakes (`FUNDAMENTALS`, `full_view`) match across Tasks 1-5. Test totals: 385 + 9 + 12 + 15 + 11 = 432 passed; skipped 48 + 5 live = 53.
