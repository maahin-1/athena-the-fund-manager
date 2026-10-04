# Phase 0a — Foundation Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the dependency-free core every later Phase 0 piece plugs into: shared contracts, per-dataset freshness checks, a DuckDB point-in-time data store, a fallback chain with health tracking, coverage-label derivation, and an adapter health canary.

**Architecture:** A small Python package `athena` (src layout). Every dataset value is a `Record(dataset, key, as_of, source, payload)`; the store keeps history so reads can be point-in-time; freshness, fallback, coverage, and canary are separate pure modules that depend only on `contracts.py`. No network access anywhere in this plan; real sources arrive in Plan 0b.

**Tech Stack:** Python >= 3.11, DuckDB, pytest.

**Spec:** `TRD.md` §2.11 (data layer), §2.2 (coverage derivation), §3 (contracts), §5 (freshness table, fallback chains, canary); `PRD.md` FR-14.

**Plan series:** 0a (this plan) -> 0b batch loaders + live adapters -> 0c instrument resolver -> 0d metrics engine + evaluation harness. Each produces working, tested software on its own.

**Suggested models (from Jev lane classification):** Tasks 1-2 (scaffold and contracts every later module depends on) on Opus; Tasks 3-8 on Sonnet at medium effort. Escalate a task one lane only after its tests fail twice.

## Global Constraints

- Python `>=3.11`; the only runtime dependency is `duckdb>=1.0`; dev dependency is `pytest>=8`.
- Free data only; no paid vendor and no network calls in tests.
- Every datetime in the system is timezone-aware UTC. A naive datetime is a bug and must raise `ValueError`.
- Every stored value carries `as_of` and `source`.
- Fail loud: empty or malformed input raises a named `AthenaError` subclass, never returns silently.
- Staleness limits are the proposed defaults from `TRD.md` §5 (tunable constants in `freshness.py`).
- Business-day age ignores exchange holidays (documented limitation; weekends only).
- Coverage labels are exactly `full`, `partial`, `insufficient`.

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, dependencies, pytest config |
| `.gitignore` | Ignore venv, caches, DuckDB files |
| `src/athena/__init__.py` | Package marker and version |
| `src/athena/contracts.py` | `Coverage`, `Record`, `Quote`, `Bar`, `RefreshResult`, `DataAdapter`, `BrokerAdapter`, `BatchLoader`, error classes |
| `src/athena/freshness.py` | `Limit`, `DEFAULT_LIMITS`, `age_in_business_days`, `check_fresh` |
| `src/athena/store.py` | `DataStore` (DuckDB): `put`, `put_many`, `latest`, `point_in_time`, `export_parquet` |
| `src/athena/fallback.py` | `HealthRegistry`, `ChainResult`, `FallbackChain` |
| `src/athena/coverage.py` | `derive_coverage` |
| `src/athena/canary.py` | `CanaryCheck`, `CanaryResult`, `run_canary` |
| `tests/test_*.py` | One test module per source module |

All commands run from the project root `D:\projects\learn-project\athena-the-fund-manager` in Git Bash.

---

### Task 1: Project scaffold and git

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/athena/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: importable package `athena` with `athena.__version__ == "0.1.0"`; a working `pytest` command via `.venv/Scripts/python -m pytest`.

- [ ] **Step 1: Initialise git (the folder is not a repo yet) and create the virtualenv**

```bash
git init
python -m venv .venv
```

Expected: `Initialized empty Git repository ...`; `.venv/` exists.

- [ ] **Step 2: Write `.gitignore`**

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
*.duckdb
*.duckdb.wal
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "athena"
version = "0.1.0"
description = "Agentic multi-asset fund manager"
requires-python = ">=3.11"
dependencies = ["duckdb>=1.0"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 4: Write the failing smoke test `tests/test_smoke.py`**

```python
import athena


def test_package_has_version():
    assert athena.__version__ == "0.1.0"
```

- [ ] **Step 5: Install and run the test to verify it fails**

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests/test_smoke.py -v
```

Expected: FAIL with `ModuleNotFoundError` or `AttributeError: module 'athena' has no attribute '__version__'` (the `src/athena` package does not exist yet, so the editable install may also warn about no packages; that is expected).

- [ ] **Step 6: Create `src/athena/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 7: Re-install and run the test to verify it passes**

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest tests/test_smoke.py -v
```

Expected: `1 passed`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore src/athena/__init__.py tests/test_smoke.py PRD.md TRD.md docs .graphifyignore
git commit -m "chore: scaffold athena package with pytest"
```

---

### Task 2: Contracts

**Files:**
- Create: `src/athena/contracts.py`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Produces (all in `athena.contracts`):
  - `class Coverage(str, Enum)`: `FULL="full"`, `PARTIAL="partial"`, `INSUFFICIENT="insufficient"`
  - `class AthenaError(Exception)` and subclasses `StaleDataError`, `EmptyRefreshError`, `SchemaChangedError`, `AllSourcesFailed`
  - `@dataclass(frozen=True) class Record(dataset: str, key: str, as_of: datetime, source: str, payload: dict[str, Any])`; raises `ValueError("as_of must be timezone-aware")` for a naive `as_of`
  - `@dataclass(frozen=True) class Quote(symbol: str, price: float, as_of: datetime, source: str)`
  - `@dataclass(frozen=True) class Bar(symbol: str, timestamp: datetime, open: float, high: float, low: float, close: float, volume: float, as_of: datetime, source: str)`
  - `@dataclass(frozen=True) class RefreshResult(dataset: str, rows_written: int, as_of: datetime, source: str)`
  - `@runtime_checkable class DataAdapter(Protocol)`: `describe() -> dict`, `fetch_quote(symbol, **params) -> Quote`, `fetch_ohlcv(symbol, timeframe, since=None, limit=None, **params) -> list[Bar]`
  - `@runtime_checkable class BrokerAdapter(DataAdapter, Protocol)`: adds `place_order(symbol, side, qty, order_type, **params) -> Any` (Phase 5 will tighten the return type)
  - `@runtime_checkable class BatchLoader(Protocol)`: `describe() -> dict`, `refresh(dataset, since=None) -> RefreshResult`, `read(dataset, key, **params) -> Record`

- [ ] **Step 1: Write the failing tests `tests/test_contracts.py`**

```python
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from athena.contracts import (
    AllSourcesFailed,
    AthenaError,
    BatchLoader,
    BrokerAdapter,
    Coverage,
    DataAdapter,
    EmptyRefreshError,
    Record,
    RefreshResult,
    SchemaChangedError,
    StaleDataError,
)

UTC = timezone.utc


def test_coverage_values_are_exactly_three_labels():
    assert [c.value for c in Coverage] == ["full", "partial", "insufficient"]


def test_record_rejects_naive_as_of():
    with pytest.raises(ValueError, match="timezone-aware"):
        Record("mf.nav", "119551", datetime(2026, 10, 2, 18, 0), "mftool", {"nav": 10.0})


def test_record_is_frozen():
    r = Record("mf.nav", "119551", datetime(2026, 10, 2, 18, 0, tzinfo=UTC), "mftool", {"nav": 10.0})
    with pytest.raises(FrozenInstanceError):
        r.source = "other"


@pytest.mark.parametrize("exc", [StaleDataError, EmptyRefreshError, SchemaChangedError, AllSourcesFailed])
def test_errors_share_a_base_class(exc):
    assert issubclass(exc, AthenaError)


class _DataAdapterImpl:
    def describe(self):
        return {"fetch_quote": True, "fetch_ohlcv": True}

    def fetch_quote(self, symbol, **params):
        return None

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, **params):
        return []


class _BrokerAdapterImpl(_DataAdapterImpl):
    def place_order(self, symbol, side, qty, order_type, **params):
        return None


class _BatchLoaderImpl:
    def describe(self):
        return {"mf.ter": {"cadence": "weekly"}}

    def refresh(self, dataset, since=None):
        return RefreshResult(dataset, 0, datetime(2026, 10, 2, tzinfo=UTC), "amfi")

    def read(self, dataset, key, **params):
        return Record(dataset, key, datetime(2026, 10, 2, tzinfo=UTC), "amfi", {"ter": 1.0})


def test_protocols_are_runtime_checkable():
    assert isinstance(_DataAdapterImpl(), DataAdapter)
    assert isinstance(_BrokerAdapterImpl(), BrokerAdapter)
    assert isinstance(_BatchLoaderImpl(), BatchLoader)
    assert not isinstance(_DataAdapterImpl(), BrokerAdapter)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.contracts'`.

- [ ] **Step 3: Write `src/athena/contracts.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Coverage(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class AthenaError(Exception):
    """Base class for all errors raised deliberately by athena."""


class StaleDataError(AthenaError):
    """Data is older than its dataset's staleness limit."""


class EmptyRefreshError(AthenaError):
    """A batch loader pulled a source and found no rows."""


class SchemaChangedError(AthenaError):
    """A source's shape no longer matches what the loader expects."""


class AllSourcesFailed(AthenaError):
    """Every source in a fallback chain failed or was skipped."""


@dataclass(frozen=True)
class Record:
    dataset: str
    key: str
    as_of: datetime
    source: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    as_of: datetime
    source: str


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    as_of: datetime
    source: str


@dataclass(frozen=True)
class RefreshResult:
    dataset: str
    rows_written: int
    as_of: datetime
    source: str


@runtime_checkable
class DataAdapter(Protocol):
    def describe(self) -> dict: ...

    def fetch_quote(self, symbol: str, **params: Any) -> Quote: ...

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: Any = None, limit: Any = None, **params: Any
    ) -> list[Bar]: ...


@runtime_checkable
class BrokerAdapter(DataAdapter, Protocol):
    def place_order(
        self, symbol: str, side: str, qty: float, order_type: str, **params: Any
    ) -> Any: ...


@runtime_checkable
class BatchLoader(Protocol):
    def describe(self) -> dict: ...

    def refresh(self, dataset: str, since: Any = None) -> RefreshResult: ...

    def read(self, dataset: str, key: str, **params: Any) -> Record: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_contracts.py -v`
Expected: `8 passed` (the parametrized error test expands to 4 cases).

- [ ] **Step 5: Commit**

```bash
git add src/athena/contracts.py tests/test_contracts.py
git commit -m "feat: add shared contracts, errors, and adapter protocols"
```

---

### Task 3: Freshness policy

**Files:**
- Create: `src/athena/freshness.py`
- Test: `tests/test_freshness.py`

**Interfaces:**
- Consumes: `athena.contracts.StaleDataError`.
- Produces (in `athena.freshness`):
  - `@dataclass(frozen=True) class Limit(amount: int, unit: str)`; `unit` is `"minutes"`, `"business_days"` or `"days"`
  - `DEFAULT_LIMITS: dict[str, Limit | None]` with keys `quote.intraday`, `option_chain`, `price.eod`, `mf.nav`, `index.tri`, `mf.ter`, `mf.holdings`, `equity.fundamentals`, `bond.price` (`None` = no limit)
  - `age_in_business_days(as_of: datetime, now: datetime) -> int`
  - `check_fresh(dataset: str, as_of: datetime, now: datetime, limits: dict[str, Limit | None] = DEFAULT_LIMITS) -> None`; raises `StaleDataError` whose message names the dataset and the age; raises `ValueError` for an unknown dataset or a naive datetime

Reference dates used in tests (2026): 2 Oct is a Friday, 3 Oct Saturday, 5 Oct Monday, 6 Oct Tuesday.

- [ ] **Step 1: Write the failing tests `tests/test_freshness.py`**

```python
from datetime import datetime, timezone

import pytest

from athena.contracts import StaleDataError
from athena.freshness import DEFAULT_LIMITS, Limit, age_in_business_days, check_fresh

UTC = timezone.utc


def dt(day, hour=0, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_default_limits_match_the_trd_table():
    assert DEFAULT_LIMITS["quote.intraday"] == Limit(15, "minutes")
    assert DEFAULT_LIMITS["option_chain"] == Limit(15, "minutes")
    assert DEFAULT_LIMITS["price.eod"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["mf.nav"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["index.tri"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["mf.ter"] == Limit(7, "days")
    assert DEFAULT_LIMITS["mf.holdings"] == Limit(45, "days")
    assert DEFAULT_LIMITS["equity.fundamentals"] == Limit(136, "days")
    assert DEFAULT_LIMITS["bond.price"] is None


def test_business_days_skip_weekends():
    assert age_in_business_days(dt(2, 18), dt(3, 10)) == 0  # Fri -> Sat
    assert age_in_business_days(dt(2, 18), dt(5, 9)) == 1  # Fri -> Mon
    assert age_in_business_days(dt(2, 18), dt(6, 9)) == 2  # Fri -> Tue


def test_intraday_quote_fresh_at_limit_and_stale_after():
    check_fresh("quote.intraday", dt(5, 10, 0), dt(5, 10, 15))
    with pytest.raises(StaleDataError, match="quote.intraday"):
        check_fresh("quote.intraday", dt(5, 10, 0), dt(5, 10, 16))


def test_nav_published_friday_is_fresh_on_monday_stale_on_tuesday():
    check_fresh("mf.nav", dt(2, 18), dt(5, 9))
    with pytest.raises(StaleDataError, match="2 business days"):
        check_fresh("mf.nav", dt(2, 18), dt(6, 9))


def test_day_based_limit():
    check_fresh("mf.ter", dt(1), dt(8))
    with pytest.raises(StaleDataError, match="8 days"):
        check_fresh("mf.ter", dt(1), dt(9))


def test_dataset_without_limit_is_never_stale():
    check_fresh("bond.price", datetime(2020, 1, 1, tzinfo=UTC), dt(5))


def test_unknown_dataset_is_an_error():
    with pytest.raises(ValueError, match="no staleness limit declared"):
        check_fresh("made.up", dt(1), dt(2))


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        check_fresh("mf.nav", datetime(2026, 10, 2), dt(3))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_freshness.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.freshness'`.

- [ ] **Step 3: Write `src/athena/freshness.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from athena.contracts import StaleDataError


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

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_freshness.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/freshness.py tests/test_freshness.py
git commit -m "feat: add per-dataset staleness policy and freshness check"
```

---

### Task 4: DuckDB data store

**Files:**
- Create: `src/athena/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `athena.contracts.Record`.
- Produces (in `athena.store`): `class DataStore` with
  - `__init__(self, path: str = ":memory:")`
  - `put(self, record: Record) -> None`
  - `put_many(self, records: Iterable[Record]) -> int` (returns number written)
  - `latest(self, dataset: str, key: str) -> Record | None`
  - `point_in_time(self, dataset: str, key: str, at: datetime) -> Record | None` (newest record with `as_of <= at`; `ValueError` if `at` is naive)
  - `export_parquet(self, path: str) -> None`
  - `close(self) -> None`
  Timestamps are stored as UTC and returned timezone-aware UTC. Several records may share a (dataset, key); history is kept.

- [ ] **Step 1: Write the failing tests `tests/test_store.py`**

```python
from datetime import datetime, timezone

import duckdb
import pytest

from athena.contracts import Record
from athena.store import DataStore

UTC = timezone.utc


def rec(day, nav, source="mftool", key="119551"):
    return Record("mf.nav", key, datetime(2026, 10, day, 18, 0, tzinfo=UTC), source, {"nav": nav})


def test_latest_returns_none_when_empty():
    assert DataStore().latest("mf.nav", "119551") is None


def test_put_and_latest_round_trip_payload_and_timezone():
    store = DataStore()
    store.put(rec(2, 10.5))
    got = store.latest("mf.nav", "119551")
    assert got == rec(2, 10.5)
    assert got.as_of.tzinfo is not None


def test_latest_picks_greatest_as_of_regardless_of_insert_order():
    store = DataStore()
    store.put_many([rec(5, 12.0), rec(2, 10.5), rec(3, 11.0)])
    assert store.latest("mf.nav", "119551").payload == {"nav": 12.0}


def test_put_many_returns_count():
    assert DataStore().put_many([rec(2, 1.0), rec(3, 2.0)]) == 2


def test_point_in_time_returns_newest_record_at_or_before():
    store = DataStore()
    store.put_many([rec(2, 10.5), rec(3, 11.0), rec(5, 12.0)])
    at = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
    assert store.point_in_time("mf.nav", "119551", at).payload == {"nav": 11.0}


def test_point_in_time_before_first_record_is_none():
    store = DataStore()
    store.put(rec(2, 10.5))
    assert store.point_in_time("mf.nav", "119551", datetime(2026, 10, 1, tzinfo=UTC)) is None


def test_point_in_time_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        DataStore().point_in_time("mf.nav", "119551", datetime(2026, 10, 4))


def test_same_as_of_later_insert_wins():
    store = DataStore()
    store.put(rec(2, 10.5, source="first"))
    store.put(rec(2, 10.6, source="second"))
    assert store.latest("mf.nav", "119551").source == "second"


def test_keys_and_datasets_are_isolated():
    store = DataStore()
    store.put(rec(2, 10.5, key="A"))
    assert store.latest("mf.nav", "B") is None
    assert store.latest("mf.ter", "A") is None


def test_file_store_persists_across_reopen(tmp_path):
    path = str(tmp_path / "s.duckdb")
    first = DataStore(path)
    first.put(rec(2, 10.5))
    first.close()
    second = DataStore(path)
    assert second.latest("mf.nav", "119551") == rec(2, 10.5)
    second.close()


def test_export_parquet_writes_all_rows(tmp_path):
    store = DataStore()
    store.put_many([rec(2, 10.5), rec(3, 11.0)])
    out = str(tmp_path / "records.parquet")
    store.export_parquet(out)
    count = duckdb.connect().execute("SELECT count(*) FROM read_parquet(?)", [out]).fetchone()[0]
    assert count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.store'`.

- [ ] **Step 3: Write `src/athena/store.py`**

```python
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone

import duckdb

from athena.contracts import Record

_UTC = timezone.utc


def _to_db(value: datetime) -> datetime:
    return value.astimezone(_UTC).replace(tzinfo=None)


def _from_db(value: datetime) -> datetime:
    return value.replace(tzinfo=_UTC)


class DataStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._con = duckdb.connect(path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            "dataset VARCHAR NOT NULL, key VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, "
            "source VARCHAR NOT NULL, payload VARCHAR NOT NULL)"
        )

    def put(self, record: Record) -> None:
        self._con.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
            [
                record.dataset,
                record.key,
                _to_db(record.as_of),
                record.source,
                json.dumps(record.payload, sort_keys=True),
            ],
        )

    def put_many(self, records: Iterable[Record]) -> int:
        count = 0
        for record in records:
            self.put(record)
            count += 1
        return count

    def latest(self, dataset: str, key: str) -> Record | None:
        return self._select(dataset, key, None)

    def point_in_time(self, dataset: str, key: str, at: datetime) -> Record | None:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        return self._select(dataset, key, _to_db(at))

    def _select(self, dataset: str, key: str, at: datetime | None) -> Record | None:
        sql = "SELECT dataset, key, as_of, source, payload FROM records WHERE dataset = ? AND key = ?"
        params: list = [dataset, key]
        if at is not None:
            sql += " AND as_of <= ?"
            params.append(at)
        sql += " ORDER BY as_of DESC, rowid DESC LIMIT 1"
        row = self._con.execute(sql, params).fetchone()
        if row is None:
            return None
        return Record(row[0], row[1], _from_db(row[2]), row[3], json.loads(row[4]))

    def export_parquet(self, path: str) -> None:
        escaped = path.replace("'", "''")
        self._con.execute(f"COPY records TO '{escaped}' (FORMAT PARQUET)")

    def close(self) -> None:
        self._con.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
Expected: `11 passed`. If `test_same_as_of_later_insert_wins` fails because `rowid` ordering is unsupported on the installed DuckDB, replace the table with one that has an explicit `seq BIGINT` column filled from a counter and order by `seq DESC` instead, keeping all other behaviour.

- [ ] **Step 5: Commit**

```bash
git add src/athena/store.py tests/test_store.py
git commit -m "feat: add DuckDB data store with point-in-time reads"
```

---

### Task 5: Fallback chain and health registry

**Files:**
- Create: `src/athena/fallback.py`
- Test: `tests/test_fallback.py`

**Interfaces:**
- Consumes: `athena.contracts.AllSourcesFailed`.
- Produces (in `athena.fallback`):
  - `class HealthRegistry`: `mark_degraded(name: str, reason: str) -> None`, `mark_healthy(name: str) -> None`, `is_degraded(name: str) -> bool`, `reason(name: str) -> str | None`, `degraded() -> dict[str, str]`
  - `@dataclass(frozen=True) class ChainResult(value, source: str, failures: tuple[tuple[str, str], ...])`
  - `class FallbackChain`: `__init__(self, sources: Sequence[tuple[str, Callable[..., Any]]])` (raises `ValueError` if empty); `run(self, *args, health: HealthRegistry | None = None, **kwargs) -> ChainResult`. Sources are tried in order; a degraded source is skipped; an exception or a `None` result counts as a failure and the next source is tried; if none succeed, raises `AllSourcesFailed` listing every source and reason.

- [ ] **Step 1: Write the failing tests `tests/test_fallback.py`**

```python
import pytest

from athena.contracts import AllSourcesFailed
from athena.fallback import FallbackChain, HealthRegistry


def ok(value):
    return lambda *a, **k: value


def boom(*a, **k):
    raise ConnectionError("endpoint moved")


def test_first_healthy_source_wins_and_is_named():
    chain = FallbackChain([("jugaad", ok(101.5)), ("yahoo", ok(999))])
    result = chain.run("SBIN")
    assert result.value == 101.5
    assert result.source == "jugaad"
    assert result.failures == ()


def test_falls_through_on_exception_and_records_failure():
    chain = FallbackChain([("jugaad", boom), ("nsepython", ok(102.0))])
    result = chain.run("SBIN")
    assert result.source == "nsepython"
    assert result.failures == (("jugaad", "ConnectionError: endpoint moved"),)


def test_none_result_counts_as_failure():
    chain = FallbackChain([("jugaad", ok(None)), ("yahoo", ok(5))])
    result = chain.run("SBIN")
    assert result.source == "yahoo"
    assert result.failures == (("jugaad", "returned no data"),)


def test_arguments_are_forwarded():
    chain = FallbackChain([("a", lambda symbol, timeframe="1d": (symbol, timeframe))])
    assert chain.run("SBIN", timeframe="1h").value == ("SBIN", "1h")


def test_degraded_sources_are_skipped():
    health = HealthRegistry()
    health.mark_degraded("jugaad", "canary: empty response")
    chain = FallbackChain([("jugaad", ok(1)), ("yahoo", ok(2))])
    result = chain.run("SBIN", health=health)
    assert result.source == "yahoo"
    assert result.failures == (("jugaad", "skipped, degraded: canary: empty response"),)


def test_all_failed_raises_with_every_reason():
    health = HealthRegistry()
    health.mark_degraded("a", "down")
    chain = FallbackChain([("a", ok(1)), ("b", boom), ("c", ok(None))])
    with pytest.raises(AllSourcesFailed) as err:
        chain.run("X", health=health)
    message = str(err.value)
    assert "a: skipped, degraded: down" in message
    assert "b: ConnectionError: endpoint moved" in message
    assert "c: returned no data" in message


def test_empty_chain_is_rejected():
    with pytest.raises(ValueError, match="at least one source"):
        FallbackChain([])


def test_health_registry_lifecycle():
    health = HealthRegistry()
    assert not health.is_degraded("x")
    health.mark_degraded("x", "bad")
    assert health.is_degraded("x")
    assert health.reason("x") == "bad"
    assert health.degraded() == {"x": "bad"}
    health.mark_healthy("x")
    assert not health.is_degraded("x")
    assert health.reason("x") is None
    assert health.degraded() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_fallback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.fallback'`.

- [ ] **Step 3: Write `src/athena/fallback.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from athena.contracts import AllSourcesFailed


class HealthRegistry:
    def __init__(self) -> None:
        self._degraded: dict[str, str] = {}

    def mark_degraded(self, name: str, reason: str) -> None:
        self._degraded[name] = reason

    def mark_healthy(self, name: str) -> None:
        self._degraded.pop(name, None)

    def is_degraded(self, name: str) -> bool:
        return name in self._degraded

    def reason(self, name: str) -> str | None:
        return self._degraded.get(name)

    def degraded(self) -> dict[str, str]:
        return dict(self._degraded)


@dataclass(frozen=True)
class ChainResult:
    value: Any
    source: str
    failures: tuple[tuple[str, str], ...]


class FallbackChain:
    def __init__(self, sources: Sequence[tuple[str, Callable[..., Any]]]) -> None:
        if not sources:
            raise ValueError("a fallback chain needs at least one source")
        self._sources = list(sources)

    def run(self, *args: Any, health: HealthRegistry | None = None, **kwargs: Any) -> ChainResult:
        failures: list[tuple[str, str]] = []
        for name, fetch in self._sources:
            if health is not None and health.is_degraded(name):
                failures.append((name, f"skipped, degraded: {health.reason(name)}"))
                continue
            try:
                value = fetch(*args, **kwargs)
            except Exception as exc:  # any source failure moves to the next source
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                continue
            if value is None:
                failures.append((name, "returned no data"))
                continue
            return ChainResult(value, name, tuple(failures))
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
        raise AllSourcesFailed(f"all sources failed ({detail})")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_fallback.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/fallback.py tests/test_fallback.py
git commit -m "feat: add fallback chain and adapter health registry"
```

---

### Task 6: Coverage derivation

**Files:**
- Create: `src/athena/coverage.py`
- Test: `tests/test_coverage.py`

**Interfaces:**
- Consumes: `athena.contracts.Coverage`.
- Produces (in `athena.coverage`): `derive_coverage(critical: Sequence[str], optional: Sequence[str], available: Mapping[str, Any]) -> tuple[Coverage, list[str]]`. An input is missing when its key is absent, its value is `None`, or its value is an empty sized container or empty string (`0` and `0.0` count as present). Any missing critical input gives `INSUFFICIENT`; otherwise any missing optional input gives `PARTIAL`; otherwise `FULL`. The returned list holds every missing input, critical first then optional, in declaration order.

- [ ] **Step 1: Write the failing tests `tests/test_coverage.py`**

```python
from athena.contracts import Coverage
from athena.coverage import derive_coverage


def test_full_when_everything_present():
    label, missing = derive_coverage(["nav"], ["manager_tenure"], {"nav": [1.0], "manager_tenure": 5})
    assert label is Coverage.FULL
    assert missing == []


def test_partial_when_only_optional_missing():
    label, missing = derive_coverage(["nav"], ["manager_tenure"], {"nav": [1.0]})
    assert label is Coverage.PARTIAL
    assert missing == ["manager_tenure"]


def test_insufficient_when_critical_missing_and_lists_all_missing():
    label, missing = derive_coverage(["nav", "ter"], ["manager_tenure"], {"ter": 0.9})
    assert label is Coverage.INSUFFICIENT
    assert missing == ["nav", "manager_tenure"]


def test_none_empty_list_empty_dict_empty_string_count_as_missing():
    available = {"a": None, "b": [], "c": {}, "d": ""}
    label, missing = derive_coverage(["a", "b", "c", "d"], [], available)
    assert label is Coverage.INSUFFICIENT
    assert missing == ["a", "b", "c", "d"]


def test_zero_values_count_as_present():
    label, missing = derive_coverage(["beta", "alpha"], [], {"beta": 0.0, "alpha": 0})
    assert label is Coverage.FULL
    assert missing == []


def test_no_requirements_is_full():
    label, missing = derive_coverage([], [], {})
    assert label is Coverage.FULL
    assert missing == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.coverage'`.

- [ ] **Step 3: Write `src/athena/coverage.py`**

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from athena.contracts import Coverage


def _is_missing(available: Mapping[str, Any], name: str) -> bool:
    if name not in available:
        return True
    value = available[name]
    if value is None:
        return True
    if hasattr(value, "__len__") and len(value) == 0:
        return True
    return False


def derive_coverage(
    critical: Sequence[str],
    optional: Sequence[str],
    available: Mapping[str, Any],
) -> tuple[Coverage, list[str]]:
    missing_critical = [name for name in critical if _is_missing(available, name)]
    missing_optional = [name for name in optional if _is_missing(available, name)]
    missing = missing_critical + missing_optional
    if missing_critical:
        return Coverage.INSUFFICIENT, missing
    if missing_optional:
        return Coverage.PARTIAL, missing
    return Coverage.FULL, missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_coverage.py -v`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/coverage.py tests/test_coverage.py
git commit -m "feat: derive coverage label from declared required inputs"
```

---

### Task 7: Adapter health canary

**Files:**
- Create: `src/athena/canary.py`
- Test: `tests/test_canary.py`

**Interfaces:**
- Consumes: `athena.contracts.Record`, `StaleDataError`; `athena.freshness.check_fresh`; `athena.fallback.HealthRegistry`.
- Produces (in `athena.canary`):
  - `@dataclass(frozen=True) class CanaryCheck(name: str, dataset: str, fetch: Callable[[], Record | None], required_payload_keys: tuple[str, ...] = ())`
  - `@dataclass(frozen=True) class CanaryResult(name: str, ok: bool, reason: str | None)`
  - `run_canary(checks: Sequence[CanaryCheck], health: HealthRegistry, now: datetime) -> list[CanaryResult]`. For each check: a raised exception, a `None` record, an empty payload, a payload missing any required key, or a stale `as_of` (per `check_fresh` for the check's dataset) marks that adapter degraded in `health` with a reason and logs a warning on logger `athena.canary`; a passing check marks it healthy. One failing check never stops the others.

- [ ] **Step 1: Write the failing tests `tests/test_canary.py`**

```python
import logging
from datetime import datetime, timezone

from athena.canary import CanaryCheck, run_canary
from athena.contracts import Record
from athena.fallback import HealthRegistry

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)  # Monday


def nav_record(as_of=datetime(2026, 10, 2, 18, 0, tzinfo=UTC), payload=None):
    return Record("mf.nav", "119551", as_of, "mftool", payload if payload is not None else {"nav": 10.5})


def check(fetch, keys=("nav",), name="mftool"):
    return CanaryCheck(name=name, dataset="mf.nav", fetch=fetch, required_payload_keys=keys)


def test_healthy_adapter_passes_and_clears_prior_degradation():
    health = HealthRegistry()
    health.mark_degraded("mftool", "old failure")
    results = run_canary([check(lambda: nav_record())], health, NOW)
    assert [(r.name, r.ok, r.reason) for r in results] == [("mftool", True, None)]
    assert not health.is_degraded("mftool")


def test_exception_degrades_adapter():
    def fetch():
        raise ConnectionError("timeout")

    health = HealthRegistry()
    results = run_canary([check(fetch)], health, NOW)
    assert not results[0].ok
    assert "ConnectionError: timeout" in results[0].reason
    assert health.is_degraded("mftool")


def test_none_and_empty_payload_degrade_adapter():
    health = HealthRegistry()
    results = run_canary(
        [check(lambda: None, name="a"), check(lambda: nav_record(payload={}), name="b")],
        health,
        NOW,
    )
    assert [r.reason for r in results] == ["empty response", "empty response"]
    assert health.degraded().keys() == {"a", "b"}


def test_missing_required_key_means_schema_changed():
    health = HealthRegistry()
    results = run_canary([check(lambda: nav_record(payload={"price": 1}))], health, NOW)
    assert results[0].reason == "schema changed: missing keys ['nav']"
    assert health.is_degraded("mftool")


def test_stale_data_degrades_adapter_with_staleness_message():
    old = nav_record(as_of=datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    health = HealthRegistry()
    results = run_canary([check(lambda: old)], health, NOW)
    assert not results[0].ok
    assert "mf.nav data is" in results[0].reason


def test_one_failure_does_not_stop_other_checks():
    def fetch():
        raise RuntimeError("x")

    health = HealthRegistry()
    results = run_canary([check(fetch, name="bad"), check(lambda: nav_record(), name="good")], health, NOW)
    assert [(r.name, r.ok) for r in results] == [("bad", False), ("good", True)]
    assert health.is_degraded("bad") and not health.is_degraded("good")


def test_failure_is_logged_as_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="athena.canary"):
        run_canary([check(lambda: None)], HealthRegistry(), NOW)
    assert any("mftool" in message and "empty response" in message for message in caplog.messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_canary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.canary'`.

- [ ] **Step 3: Write `src/athena/canary.py`**

```python
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from athena.contracts import Record, StaleDataError
from athena.fallback import HealthRegistry
from athena.freshness import check_fresh

logger = logging.getLogger("athena.canary")


@dataclass(frozen=True)
class CanaryCheck:
    name: str
    dataset: str
    fetch: Callable[[], Record | None]
    required_payload_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanaryResult:
    name: str
    ok: bool
    reason: str | None


def _failure_reason(check: CanaryCheck, now: datetime) -> str | None:
    try:
        record = check.fetch()
    except Exception as exc:
        return f"fetch raised {type(exc).__name__}: {exc}"
    if record is None or not record.payload:
        return "empty response"
    missing = [key for key in check.required_payload_keys if key not in record.payload]
    if missing:
        return f"schema changed: missing keys {missing}"
    try:
        check_fresh(check.dataset, record.as_of, now)
    except StaleDataError as exc:
        return str(exc)
    return None


def run_canary(
    checks: Sequence[CanaryCheck], health: HealthRegistry, now: datetime
) -> list[CanaryResult]:
    results: list[CanaryResult] = []
    for check in checks:
        reason = _failure_reason(check, now)
        if reason is None:
            health.mark_healthy(check.name)
            results.append(CanaryResult(check.name, True, None))
        else:
            health.mark_degraded(check.name, reason)
            logger.warning("canary failed for %s: %s", check.name, reason)
            results.append(CanaryResult(check.name, False, reason))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_canary.py -v`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/canary.py tests/test_canary.py
git commit -m "feat: add adapter health canary"
```

---

### Task 8: Full-suite check, TRD alignment, graph refresh

**Files:**
- Modify: `TRD.md` (§3 adapter block)
- Regenerate: `graphify-out/` (via `/graphify . --update`)

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: a green full suite, a TRD whose `Adapter` contract matches `contracts.py`, and an up-to-date graph.

- [ ] **Step 1: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: `1 + 8 + 8 + 11 + 8 + 6 + 7 = 49 passed`, 0 failed.

- [ ] **Step 2: Align `TRD.md` §3 with the code (adapter split)**

In `TRD.md`, replace this block:

```python
class Adapter(Protocol):
    def describe(self) -> dict: ...        # capability map: {"fetch_ohlcv": True, "place_order": "emulated", ...}
    def fetch_quote(self, symbol: str, **params) -> Quote: ...
    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None, **params) -> list[Bar]: ...
    def place_order(self, symbol: str, side: str, qty: float, order_type: str, **params) -> Order: ...
```

with:

```python
class DataAdapter(Protocol):
    def describe(self) -> dict: ...        # capability map: {"fetch_ohlcv": True, "fetch_quote": "emulated", ...}
    def fetch_quote(self, symbol: str, **params) -> Quote: ...
    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None, **params) -> list[Bar]: ...

class BrokerAdapter(DataAdapter, Protocol):   # Phase 5
    def place_order(self, symbol: str, side: str, qty: float, order_type: str, **params) -> Order: ...
```

Also change the sentence "Every `Quote` and `Bar` result carries `as_of` and `source`." only if it no longer holds (it still does; leave it). Add one line to the TRD Revision history: `- **Oct 3, 2026 (code)** — Adapter protocol split into DataAdapter and BrokerAdapter to match contracts.py (Phase 0a).`

- [ ] **Step 3: Refresh the graph**

Run `/graphify . --update` so the graph reflects the revised TRD and the new `src/athena` code (code files are extracted by AST, no LLM needed). Then query to confirm: `/graphify query "What does the freshness module depend on?"` and check the answer cites `src/athena/freshness.py`.

- [ ] **Step 4: Commit**

```bash
git add TRD.md graphify-out
git commit -m "docs: align TRD adapter contract with code; refresh graph"
```

---

## Self-Review (completed)

**Spec coverage (TRD / PRD references -> task):**
- TRD §2.11 data layer: store with `as_of`/`source`, point-in-time history, Parquet export -> Task 4. Batch loaders and live adapters are intentionally deferred to Plan 0b.
- TRD §3 contracts (`Record`, `DataAdapter`/`BrokerAdapter`, `BatchLoader`) -> Task 2; Task 8 aligns the TRD text.
- TRD §5 staleness table -> Task 3; fallback chains (mechanism) -> Task 5; daily canary (check logic; scheduling is a later runtime concern) -> Task 7.
- TRD §2.2 coverage derivation from declared required inputs -> Task 6 (wiring into the specialist base class is Phase 1).
- PRD FR-14 acceptance: stale fixture refuses naming dataset and age (Task 3), coverage-gap fixture (Task 6), simulated outage caught by canary and logged (Task 7).
- Not in this plan by design: instrument resolver (0c), metrics engine and evaluation harness (0d), concrete loaders/adapters (0b).

**Placeholder scan:** no TBD/TODO; every step has full code or an exact command. The only conditional is the `rowid` fallback note in Task 4 Step 4, which gives the exact alternative.

**Type consistency:** `Record`, `HealthRegistry`, `check_fresh`, `Coverage` signatures match across Tasks 2-7. Test counts: smoke 1, contracts 8, freshness 8, store 11, fallback 8, coverage 6, canary 7 = 49.

**Not executed:** the code in this plan was written but not run; Task steps 2 and 4 in each task are where it is first verified.
