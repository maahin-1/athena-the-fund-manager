# Phase 0c — Instrument Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn raw user input (a ticker, an NSE-style variant, an ISIN, or a company name) into a routed instrument: asset class, canonical identifier, confidence, and the specialist set to run. Deterministic lookups first, fuzzy name matching second, an optional model classifier third, and "ask the user with candidates" last. Never guess silently.

**Architecture:** An in-memory `InstrumentIndex` built from the NSE equity and ETF master records in the `DataStore` (refusing stale masters). `InstrumentResolver.resolve()` returns a `Resolution` or an `Ambiguity`, or raises `UnknownInstrument`. ISIN checksum handling and the routing table are separate tiny modules. The optional model classifier is a `Protocol` with no implementation in this plan (adoption is gated on the labeled set in Plan 0d).

**Tech Stack:** Python >= 3.11, stdlib `difflib` for fuzzy matching, pytest. No new dependencies.

**Spec:** `TRD.md` §2.12 (resolver), §3 (classification schema, routing table), §5 (staleness); `PRD.md` FR-1.

**Plan series:** 0a, 0b (done) -> **0c (this plan)** -> 0d metrics engine + evaluation harness. **Scope:** equity and ETF only (the NSE masters exist). Mutual funds are on hold and bonds have no master list, so an unlisted fund ISIN raises `UnknownInstrument` with an explicit "mutual-fund support is not available yet" note.

**Suggested models:** Sonnet at medium effort for all tasks. This plan was prototyped end to end in a scratch copy first: every test below passed, and a 27-case labeled run against the live NSE lists was 27/27 correct, so Task 4 should go green on the first run.

## Verified behaviour (5 Oct 2026, live NSE masters: 2,593 equities + 351 ETFs)

- Index build 0.04 s; exact lookups ~0 ms; fuzzy lookups 18–80 ms.
- ISIN check-digit algorithm (Luhn over letters expanded to numbers) validated against four real ISINs (`INE062A01020`, `INF204KB14I2`, `INE467B01029`, `INE002A01018`). ISIN prefix `INF` = fund/ETF units and `INE` = corporate securities were observed in the NSE lists; they are used only as an error-message hint, never as a decision rule.
- **Known limitation, deliberate:** candidate *ranking* for ambiguous input is by edit similarity, so `SBI` lists `SBIN, BI, SBIBPB, BIL` (a tiny symbol like `BI` outranks `SBILIFE`) and `TATA` does not surface the Tata group names first. Resolution decisions are unaffected (these cases correctly return an `Ambiguity`), but candidate quality should be measured and improved with the labeled set in Plan 0d (a prefix/word-containment boost is the likely fix). ETF names in the NSE list are squashed (`NIPINDETFNIFTYBEES`), so ETF lookup by free-text name is weak; tickers and ISINs work.

## Global Constraints

- Free data only; no network in the default test run (live tests are opt-in via `--live`).
- Resolution thresholds are proposed defaults in `resolver.py` (`ACCEPT_SCORE=0.90`, `ACCEPT_MARGIN=0.05`, `CANDIDATE_FLOOR=0.55`, `MODEL_THRESHOLD=0.85`); tune on the Plan 0d labeled set, not by hand.
- An ETF wins over an equity with the same symbol; index funds are mutual funds (out of scope here).
- The resolver refuses stale masters via `check_fresh` (7-day limit from Plan 0b).
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/athena/contracts.py` (modify) | Add `UnknownInstrument` |
| `src/athena/store.py` (modify) | Add `latest_records(dataset)` |
| `src/athena/isin.py` | ISIN shape, check digit, checksum, prefix hint |
| `src/athena/routing.py` | `ROUTING` table and `route()` |
| `src/athena/resolver.py` | Input/name normalisation, `Candidate`, `Resolution`, `Ambiguity`, `Classifier`, `InstrumentIndex`, `InstrumentResolver` |
| `tests/test_isin.py`, `tests/test_routing.py`, `tests/test_resolver.py` | Offline tests |
| `tests/test_contracts.py`, `tests/test_store.py` (modify) | New error and `latest_records` tests |
| `tests/live/test_live_resolver.py` | 27 labeled cases against the real NSE lists |

All commands run from the project root in Git Bash.

---

### Task 1: `UnknownInstrument` and `DataStore.latest_records`

**Files:**
- Modify: `src/athena/contracts.py`, `src/athena/store.py`, `tests/test_contracts.py`, `tests/test_store.py`

**Interfaces:**
- Produces: `athena.contracts.UnknownInstrument(AthenaError)`; `DataStore.latest_records(dataset: str) -> list[Record]` (the newest record for every key in a dataset, sorted by key; `[]` for an unknown dataset).

- [ ] **Step 1: Write the failing tests**

In `tests/test_contracts.py`, add `UnknownInstrument,` to the import list (after `StaleDataError,`) and replace the parametrize decorator with:

```python
@pytest.mark.parametrize(
    "exc",
    [StaleDataError, EmptyRefreshError, SchemaChangedError, AllSourcesFailed, UnsupportedOperation, UnknownInstrument],
)
```

Append to `tests/test_store.py`:

```python
def test_latest_records_returns_newest_per_key_sorted():
    store = DataStore()
    store.put_many([rec(2, 1.0, key="B"), rec(5, 3.0, key="B"), rec(3, 2.0, key="A")])
    store.put(Record("mf.ter", "A", datetime(2026, 10, 3, tzinfo=UTC), "x", {"ter": 1}))
    got = store.latest_records("mf.nav")
    assert [(r.key, r.payload["nav"]) for r in got] == [("A", 2.0), ("B", 3.0)]
    assert store.latest_records("missing") == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_contracts.py tests/test_store.py -q`
Expected: FAIL (`ImportError: cannot import name 'UnknownInstrument'`).

- [ ] **Step 3: Implement**

In `src/athena/contracts.py`, immediately before `class UnsupportedOperation`, add:

```python
class UnknownInstrument(AthenaError):
    """An input could not be matched to any known instrument."""


```

In `src/athena/store.py`, immediately before `def export_parquet`, add:

```python
    def latest_records(self, dataset: str) -> list[Record]:
        """The newest record for every key in a dataset."""
        rows = self._con.execute(
            "SELECT dataset, key, as_of, source, payload FROM ("
            "SELECT *, row_number() OVER (PARTITION BY key ORDER BY as_of DESC, rowid DESC) AS rn "
            "FROM records WHERE dataset = ?) WHERE rn = 1 ORDER BY key",
            [dataset],
        ).fetchall()
        return [Record(r[0], r[1], _from_db(r[2]), r[3], json.loads(r[4])) for r in rows]

```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `108 passed, 6 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/contracts.py src/athena/store.py tests/test_contracts.py tests/test_store.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add UnknownInstrument and DataStore.latest_records"
git push
```

---

### Task 2: ISIN helpers

**Files:**
- Create: `src/athena/isin.py`
- Test: `tests/test_isin.py`

**Interfaces:**
- Produces (`athena.isin`): `looks_like_isin(text: str) -> bool` (two capital letters, nine alphanumerics, one digit); `isin_check_digit(body: str) -> int` (check digit for the first 11 characters); `isin_checksum_ok(isin: str) -> bool`; `isin_type_hint(isin: str) -> str | None` (`"fund_or_etf"` for `INF`, `"corporate"` for `INE`, else `None`; a hint only).

- [ ] **Step 1: Write the failing tests `tests/test_isin.py`**

```python
import pytest

from athena.isin import isin_check_digit, isin_checksum_ok, isin_type_hint, looks_like_isin


@pytest.mark.parametrize("isin", ["INE062A01020", "INF204KB14I2", "INE467B01029", "INE002A01018"])
def test_real_isins_pass_the_checksum(isin):
    assert isin_checksum_ok(isin)


def test_wrong_check_digit_fails():
    assert not isin_checksum_ok("INE062A01021")


@pytest.mark.parametrize("text", ["SBIN", "INE062A0102", "INE062A010201", "ine062a01020", "1NE062A01020"])
def test_non_isin_shapes_are_rejected(text):
    assert not looks_like_isin(text)
    assert not isin_checksum_ok(text)


def test_check_digit_helper_builds_valid_isins():
    body = "INF000Z99ZZ"
    assert isin_checksum_ok(body + str(isin_check_digit(body)))


def test_type_hint_is_a_prefix_hint_only():
    assert isin_type_hint("INF204KB14I2") == "fund_or_etf"
    assert isin_type_hint("INE062A01020") == "corporate"
    assert isin_type_hint("US0378331005") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_isin.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.isin'`.

- [ ] **Step 3: Write `src/athena/isin.py`**

```python
from __future__ import annotations

import re

_ISIN_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def looks_like_isin(text: str) -> bool:
    return bool(_ISIN_SHAPE.fullmatch(text))


def isin_check_digit(body: str) -> int:
    """Check digit for the first 11 characters of an ISIN (Luhn over letters expanded to numbers)."""
    digits = "".join(str(int(char, 36)) for char in body)
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10


def isin_checksum_ok(isin: str) -> bool:
    return looks_like_isin(isin) and isin_check_digit(isin[:-1]) == int(isin[-1])


def isin_type_hint(isin: str) -> str | None:
    """Prefix hint only; never a decision rule. INF = fund/ETF units, INE = corporate securities (observed in NSE lists)."""
    if isin.startswith("INF"):
        return "fund_or_etf"
    if isin.startswith("INE"):
        return "corporate"
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_isin.py -q`
Expected: `12 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/isin.py tests/test_isin.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add ISIN checksum and prefix-hint helpers"
git push
```

---

### Task 3: Routing table

**Files:**
- Create: `src/athena/routing.py`
- Test: `tests/test_routing.py`

**Interfaces:**
- Produces (`athena.routing`): `ROUTING: dict[str, tuple[str, ...]]` for `equity`, `etf`, `mutual_fund`, `bond` (the TRD §3 routing table; the risk overlay applies to all and is not listed); `route(asset_class: str) -> tuple[str, ...]` raising `ValueError` for an unknown class.

- [ ] **Step 1: Write the failing tests `tests/test_routing.py`**

```python
import pytest

from athena.routing import ROUTING, route


def test_equity_and_etf_routes():
    assert route("equity") == ("valuation", "moat_quality", "quant_technical", "earnings_intelligence")
    assert route("etf") == ("etf_analyst", "quant_technical")


def test_every_asset_class_has_a_route():
    assert set(ROUTING) == {"equity", "etf", "mutual_fund", "bond"}


def test_unknown_asset_class_is_an_error():
    with pytest.raises(ValueError, match="crypto"):
        route("crypto")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_routing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.routing'`.

- [ ] **Step 3: Write `src/athena/routing.py`**

```python
from __future__ import annotations

ROUTING: dict[str, tuple[str, ...]] = {
    "equity": ("valuation", "moat_quality", "quant_technical", "earnings_intelligence"),
    "etf": ("etf_analyst", "quant_technical"),
    "mutual_fund": ("mutual_fund_analyst",),
    "bond": ("credit_analysis", "duration_curve"),
}


def route(asset_class: str) -> tuple[str, ...]:
    try:
        return ROUTING[asset_class]
    except KeyError:
        raise ValueError(f"unknown asset class {asset_class!r}; expected one of {sorted(ROUTING)}") from None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_routing.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/routing.py tests/test_routing.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add asset-class routing table"
git push
```

---

### Task 4: Instrument resolver

**Files:**
- Create: `src/athena/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `DataStore.latest_records`, `check_fresh`, `UnknownInstrument`, `EmptyRefreshError`, `looks_like_isin`/`isin_checksum_ok`/`isin_type_hint`, `route`, `EQUITY_DATASET`/`ETF_DATASET`, `TradingCalendar`.
- Produces (`athena.resolver`):
  - `normalize_input(text) -> str` (trim, uppercase, drop `NSE:`/`BSE:` prefix and `.NS`/`.BO` suffix); `normalize_name(text) -> str` (lowercase, `&` -> `and`, punctuation removed, `limited`/`ltd` dropped)
  - `@dataclass(frozen=True) Candidate(asset_class, identifier, name, score)`
  - `@dataclass(frozen=True) Resolution(asset_class, identifier_type, identifier, name, isin, resolution_path, confidence, candidates, routed_specialists)` (matches the TRD §3 classification schema; `identifier_type` is `"ticker"|"isin"|"name"`, `resolution_path` is `"exact"|"fuzzy"|"model"|"user_confirmed"`)
  - `@dataclass(frozen=True) Ambiguity(query, candidates, reason)`
  - `class Classifier(Protocol)`: `choose(query, candidates) -> tuple[int, float]` (index into candidates, probability)
  - `InstrumentIndex.from_store(store, now=None, calendar=None)` (raises `EmptyRefreshError` if a master is missing, `StaleDataError` if one is stale)
  - `InstrumentResolver(index, classifier=None)` with `resolve(query) -> Resolution | Ambiguity` (raises `UnknownInstrument`, or `ValueError` for an empty query) and `confirm(ambiguity, index) -> Resolution`

Resolution order inside `resolve`: exact symbol -> ISIN (checksum, then lookup) -> exact normalised name -> fuzzy (accept if top score >= 0.90 and leads the runner-up by >= 0.05) -> optional classifier (accept if probability >= 0.85) -> `Ambiguity`. No candidate above 0.55 raises `UnknownInstrument`.

- [ ] **Step 1: Write the failing tests `tests/test_resolver.py`**

```python
from datetime import datetime, timezone

import pytest

from athena.contracts import EmptyRefreshError, Record, StaleDataError, UnknownInstrument
from athena.isin import isin_check_digit
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import (
    Ambiguity,
    Candidate,
    InstrumentIndex,
    InstrumentResolver,
    Resolution,
    normalize_input,
    normalize_name,
)
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)

EQUITIES = {
    "SBIN": ("State Bank of India", "INE062A01020"),
    "SBILIFE": ("SBI Life Insurance Company Limited", "INE123W01016"),
    "SBICARD": ("SBI Cards and Payment Services Limited", "INE018E01016"),
    "TCS": ("Tata Consultancy Services Limited", "INE467B01029"),
    "RELIANCE": ("Reliance Industries Limited", "INE002A01018"),
    "INFY": ("Infosys Limited", "INE009A01021"),
    "M&M": ("Mahindra & Mahindra Limited", "INE101A01026"),
    "BAJAJ-AUTO": ("Bajaj Auto Limited", "INE917I01010"),
    "TATAMOTORS": ("Tata Motors Limited", "INE155A01022"),
    "TATASTEEL": ("Tata Steel Limited", "INE081A01020"),
    "NIFTYBEES": ("Shadow Equity With ETF Symbol", "INE000000000"),
}
ETFS = {
    "NIFTYBEES": ("NIPINDETFNIFTYBEES", "INF204KB14I2"),
    "BANKBEES": ("NIPPON INDIA ETF BANK BEES", "INF204KB15I9"),
}


def make_store(equity_as_of=NOW, etf_as_of=NOW):
    store = DataStore()
    for symbol, (name, isin) in EQUITIES.items():
        store.put(Record(EQUITY_DATASET, symbol, equity_as_of, "nse.archives", {"name": name, "series": "EQ", "isin": isin, "listing_date": "x"}))
    for symbol, (name, isin) in ETFS.items():
        store.put(Record(ETF_DATASET, symbol, etf_as_of, "nse.archives", {"name": name, "isin": isin, "underlying": "u", "underlying_class": "EQUITY", "underlying_key": "k"}))
    return store


@pytest.fixture
def resolver():
    return InstrumentResolver(InstrumentIndex.from_store(make_store(), now=NOW))


def test_normalizers():
    assert normalize_input("  nse:sbin.ns ") == "SBIN"
    assert normalize_input("m&m") == "M&M"
    assert normalize_name("Mahindra & Mahindra Limited") == "mahindra and mahindra"
    assert normalize_name("Tata Consultancy Services Ltd.") == "tata consultancy services"


@pytest.mark.parametrize("query", ["SBIN", "sbin", " SBIN.NS ", "NSE:SBIN"])
def test_ticker_variants_resolve_exactly(resolver, query):
    result = resolver.resolve(query)
    assert isinstance(result, Resolution)
    assert (result.asset_class, result.identifier, result.identifier_type, result.resolution_path, result.confidence) == (
        "equity", "SBIN", "ticker", "exact", 1.0,
    )
    assert result.routed_specialists == ("valuation", "moat_quality", "quant_technical", "earnings_intelligence")


@pytest.mark.parametrize("query, symbol", [("m&m", "M&M"), ("bajaj-auto", "BAJAJ-AUTO")])
def test_symbols_with_punctuation(resolver, query, symbol):
    assert resolver.resolve(query).identifier == symbol


def test_etf_beats_equity_with_the_same_symbol(resolver):
    result = resolver.resolve("NIFTYBEES")
    assert (result.asset_class, result.isin) == ("etf", "INF204KB14I2")
    assert result.routed_specialists == ("etf_analyst", "quant_technical")


def test_isin_resolves_to_equity_and_etf(resolver):
    equity = resolver.resolve("INE062A01020")
    etf = resolver.resolve("inf204kb14i2")
    assert (equity.asset_class, equity.identifier, equity.identifier_type) == ("equity", "SBIN", "isin")
    assert (etf.asset_class, etf.identifier) == ("etf", "NIFTYBEES")


def test_bad_isin_checksum_is_rejected(resolver):
    with pytest.raises(UnknownInstrument, match="check digit"):
        resolver.resolve("INE062A01021")


def test_unlisted_fund_isin_explains_mutual_funds_are_unsupported(resolver):
    body = "INF000Z99ZZ"
    with pytest.raises(UnknownInstrument, match="mutual-fund support is not available"):
        resolver.resolve(body + str(isin_check_digit(body)))


def test_unlisted_corporate_isin_is_unknown(resolver):
    body = "INE000Z99ZZ"
    with pytest.raises(UnknownInstrument, match="not in the NSE equity or ETF lists"):
        resolver.resolve(body + str(isin_check_digit(body)))


@pytest.mark.parametrize(
    "query, symbol",
    [("State Bank of India", "SBIN"), ("tata consultancy services", "TCS"), ("Mahindra and Mahindra", "M&M"), ("Infosys Ltd", "INFY")],
)
def test_exact_names_after_normalisation(resolver, query, symbol):
    result = resolver.resolve(query)
    assert (result.identifier, result.identifier_type, result.resolution_path) == (symbol, "name", "exact")


@pytest.mark.parametrize(
    "query, symbol",
    [("Relience Industries", "RELIANCE"), ("RELIANC", "RELIANCE"), ("Infosyss", "INFY"), ("Tata Steal", "TATASTEEL")],
)
def test_typos_resolve_fuzzily(resolver, query, symbol):
    result = resolver.resolve(query)
    assert isinstance(result, Resolution), result
    assert (result.identifier, result.resolution_path) == (symbol, "fuzzy")
    assert 0.9 <= result.confidence < 1.0


def test_ambiguous_prefix_returns_candidates_not_a_guess(resolver):
    result = resolver.resolve("SBI")
    assert isinstance(result, Ambiguity)
    assert {c.identifier for c in result.candidates} >= {"SBIN", "SBILIFE", "SBICARD"}
    assert result.candidates[0].score >= result.candidates[-1].score


def test_unknown_input_raises(resolver):
    with pytest.raises(UnknownInstrument, match="ZZZZQQ"):
        resolver.resolve("ZZZZQQ")


def test_empty_query_is_a_value_error(resolver):
    with pytest.raises(ValueError, match="empty"):
        resolver.resolve("   ")


class FakeClassifier:
    def __init__(self, index=0, probability=0.9, error=None):
        self.index, self.probability, self.error, self.seen = index, probability, error, []

    def choose(self, query, candidates):
        self.seen.append((query, list(candidates)))
        if self.error:
            raise self.error
        return self.index, self.probability


def classified(classifier):
    return InstrumentResolver(InstrumentIndex.from_store(make_store(), now=NOW), classifier)


def test_classifier_resolves_ambiguity_when_confident():
    fake = FakeClassifier(index=0, probability=0.9)
    result = classified(fake).resolve("SBI")
    assert isinstance(result, Resolution)
    assert (result.resolution_path, result.confidence) == ("model", 0.9)
    assert result.identifier == fake.seen[0][1][0].identifier
    assert all(isinstance(c, Candidate) for c in fake.seen[0][1])


def test_low_confidence_or_failing_classifier_falls_back_to_asking_the_user():
    assert isinstance(classified(FakeClassifier(probability=0.5)).resolve("SBI"), Ambiguity)
    assert isinstance(classified(FakeClassifier(error=RuntimeError("down"))).resolve("SBI"), Ambiguity)
    assert isinstance(classified(FakeClassifier(index=99, probability=0.99)).resolve("SBI"), Ambiguity)


def test_classifier_is_not_called_for_clear_matches():
    fake = FakeClassifier()
    classified(fake).resolve("SBIN")
    classified(fake).resolve("Relience Industries")
    assert fake.seen == []


def test_confirm_turns_a_chosen_candidate_into_a_resolution(resolver):
    ambiguity = resolver.resolve("SBI")
    index = [c.identifier for c in ambiguity.candidates].index("SBILIFE")
    result = resolver.confirm(ambiguity, index)
    assert (result.identifier, result.resolution_path, result.confidence) == ("SBILIFE", "user_confirmed", 1.0)


def test_index_refuses_stale_masters():
    old = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    with pytest.raises(StaleDataError, match="master.nse_equity"):
        InstrumentIndex.from_store(make_store(equity_as_of=old), now=NOW)


def test_index_requires_both_masters():
    store = DataStore()
    store.put(Record(EQUITY_DATASET, "SBIN", NOW, "nse.archives", {"name": "State Bank of India", "isin": "INE062A01020"}))
    with pytest.raises(EmptyRefreshError, match="master.nse_etf"):
        InstrumentIndex.from_store(store, now=NOW)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_resolver.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.resolver'`.

- [ ] **Step 3: Write `src/athena/resolver.py`**

```python
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Protocol

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, UnknownInstrument
from athena.freshness import check_fresh
from athena.isin import isin_checksum_ok, isin_type_hint, looks_like_isin
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.routing import route
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar

logger = logging.getLogger("athena.resolver")

ACCEPT_SCORE = 0.90  # proposed defaults; tune on the labeled resolver set
ACCEPT_MARGIN = 0.05
CANDIDATE_FLOOR = 0.55
MODEL_THRESHOLD = 0.85
MAX_CANDIDATES = 5
_NAME_STOPWORDS = {"limited", "ltd"}


def normalize_input(text: str) -> str:
    cleaned = " ".join(text.strip().upper().split())
    for prefix in ("NSE:", "BSE:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    for suffix in (".NS", ".BO"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned


def normalize_name(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    words = re.sub(r"[^a-z0-9 ]", " ", lowered).split()
    return " ".join(word for word in words if word not in _NAME_STOPWORDS)


@dataclass(frozen=True)
class Candidate:
    asset_class: str
    identifier: str
    name: str
    score: float


@dataclass(frozen=True)
class Resolution:
    asset_class: str
    identifier_type: str  # "ticker" | "isin" | "name"
    identifier: str
    name: str
    isin: str
    resolution_path: str  # "exact" | "fuzzy" | "model" | "user_confirmed"
    confidence: float
    candidates: tuple[Candidate, ...]
    routed_specialists: tuple[str, ...]


@dataclass(frozen=True)
class Ambiguity:
    query: str
    candidates: tuple[Candidate, ...]
    reason: str


class Classifier(Protocol):
    def choose(self, query: str, candidates: Sequence[Candidate]) -> tuple[int, float]:
        """Return (index into candidates, probability). Only ever chooses among the given candidates."""
        ...


@dataclass(frozen=True)
class _Entry:
    asset_class: str
    symbol: str
    name: str
    isin: str
    norm_name: str


class InstrumentIndex:
    def __init__(self, entries: Sequence[_Entry]) -> None:
        self.entries = list(entries)
        self.by_symbol: dict[str, _Entry] = {}
        self.by_isin: dict[str, _Entry] = {}
        self.by_name: dict[str, list[_Entry]] = {}
        for entry in self.entries:  # callers pass equities before ETFs so an ETF wins a symbol tie
            self.by_symbol[entry.symbol] = entry
            if entry.isin:
                self.by_isin[entry.isin] = entry
        for entry in self.by_symbol.values():
            self.by_name.setdefault(entry.norm_name, []).append(entry)

    @classmethod
    def from_store(
        cls,
        store: DataStore,
        now: datetime | None = None,
        calendar: TradingCalendar | None = None,
    ) -> InstrumentIndex:
        now = now or utc_now()
        entries: list[_Entry] = []
        for dataset, asset_class in ((EQUITY_DATASET, "equity"), (ETF_DATASET, "etf")):
            records = store.latest_records(dataset)
            if not records:
                raise EmptyRefreshError(f"no {dataset} data; run the NSE master loader first")
            check_fresh(dataset, max(record.as_of for record in records), now, calendar=calendar)
            for record in records:
                name = record.payload["name"]
                entries.append(
                    _Entry(asset_class, record.key, name, record.payload["isin"], normalize_name(name))
                )
        return cls(entries)


def _score(query: str, target: str) -> float:
    if not query or not target:
        return 0.0
    matcher = SequenceMatcher(None, query, target, autojunk=False)
    if matcher.real_quick_ratio() < CANDIDATE_FLOOR or matcher.quick_ratio() < CANDIDATE_FLOOR:
        return 0.0
    return matcher.ratio()


class InstrumentResolver:
    def __init__(self, index: InstrumentIndex, classifier: Classifier | None = None) -> None:
        self._index = index
        self._classifier = classifier

    def resolve(self, query: str) -> Resolution | Ambiguity:
        text = normalize_input(query)
        if not text:
            raise ValueError("empty query")

        entry = self._index.by_symbol.get(text)
        if entry:
            return self._resolved(entry, "ticker", "exact", 1.0)

        if looks_like_isin(text):
            return self._resolve_isin(text)

        name_key = normalize_name(query)
        matches = self._index.by_name.get(name_key, [])
        if len(matches) == 1:
            return self._resolved(matches[0], "name", "exact", 1.0)
        if len(matches) > 1:
            candidates = tuple(self._candidate(m, 1.0) for m in matches[:MAX_CANDIDATES])
            return Ambiguity(query, candidates, "the name matches more than one instrument")

        return self._resolve_fuzzy(query, text, name_key)

    def confirm(self, ambiguity: Ambiguity, index: int) -> Resolution:
        chosen = ambiguity.candidates[index]
        entry = self._index.by_symbol[chosen.identifier]
        return self._resolved(entry, "ticker", "user_confirmed", 1.0, ambiguity.candidates)

    def _resolve_isin(self, text: str) -> Resolution:
        if not isin_checksum_ok(text):
            raise UnknownInstrument(f"{text} is not a valid ISIN (check digit does not match)")
        entry = self._index.by_isin.get(text)
        if entry:
            return self._resolved(entry, "isin", "exact", 1.0)
        note = ""
        if isin_type_hint(text) == "fund_or_etf":
            note = " It looks like a fund ISIN (INF); mutual-fund support is not available yet."
        raise UnknownInstrument(f"ISIN {text} is not in the NSE equity or ETF lists.{note}")

    def _resolve_fuzzy(self, query: str, text: str, name_key: str) -> Resolution | Ambiguity:
        scored: list[tuple[float, str, _Entry]] = []
        for entry in self._index.by_symbol.values():
            name_score = _score(name_key, entry.norm_name)
            symbol_score = _score(text.lower(), entry.symbol.lower())
            best = max(name_score, symbol_score)
            if best >= CANDIDATE_FLOOR:
                scored.append((best, "name" if name_score >= symbol_score else "ticker", entry))
        if not scored:
            raise UnknownInstrument(f"no instrument matches {query!r}")
        scored.sort(key=lambda item: (-item[0], item[2].asset_class != "etf", item[2].symbol))
        candidates = tuple(self._candidate(e, s) for s, _, e in scored[:MAX_CANDIDATES])

        top_score, top_kind, top_entry = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if top_score >= ACCEPT_SCORE and top_score - runner_up >= ACCEPT_MARGIN:
            return self._resolved(top_entry, top_kind, "fuzzy", round(top_score, 4), candidates)

        if self._classifier is not None:
            try:
                index, probability = self._classifier.choose(query, candidates)
            except Exception:
                logger.warning("classifier failed for %r; asking the user instead", query, exc_info=True)
            else:
                if probability >= MODEL_THRESHOLD and 0 <= index < len(candidates):
                    entry = self._index.by_symbol[candidates[index].identifier]
                    return self._resolved(entry, "name", "model", probability, candidates)
        return Ambiguity(query, candidates, "no single instrument is a clear match")

    @staticmethod
    def _candidate(entry: _Entry, score: float) -> Candidate:
        return Candidate(entry.asset_class, entry.symbol, entry.name, round(score, 4))

    def _resolved(
        self,
        entry: _Entry,
        identifier_type: str,
        path: str,
        confidence: float,
        candidates: tuple[Candidate, ...] = (),
    ) -> Resolution:
        return Resolution(
            asset_class=entry.asset_class,
            identifier_type=identifier_type,
            identifier=entry.symbol,
            name=entry.name,
            isin=entry.isin,
            resolution_path=path,
            confidence=confidence,
            candidates=candidates,
            routed_specialists=route(entry.asset_class),
        )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: `152 passed, 6 skipped`. (If a fuzzy test fails, do not loosen the test: the thresholds were tuned against these exact cases and the live set.)

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/resolver.py tests/test_resolver.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add instrument resolver with fuzzy matching and optional classifier"
git push
```

---

### Task 5: Live labeled resolver test, TRD update, graph

**Files:**
- Create: `tests/live/test_live_resolver.py`
- Modify: `TRD.md`

**Interfaces:**
- Consumes: the real NSE masters via `NseMasterLoader`.
- Produces: 27 opt-in labeled cases (`pytest --live`), the seed of the Plan 0d resolver evaluation set.

- [ ] **Step 1: Write `tests/live/test_live_resolver.py`**

```python
import pytest

from athena.contracts import UnknownInstrument
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.resolver import Ambiguity, InstrumentIndex, InstrumentResolver, Resolution
from athena.store import DataStore

pytestmark = pytest.mark.live

EQUITY = "equity"
ETF = "etf"
AMBIGUOUS = "AMBIGUOUS"
UNKNOWN = "UNKNOWN"

# (query, expected) where expected is (asset_class, symbol), AMBIGUOUS or UNKNOWN.
CASES = [
    ("SBIN", (EQUITY, "SBIN")),
    ("reliance", (EQUITY, "RELIANCE")),
    ("TCS.NS", (EQUITY, "TCS")),
    ("NSE:INFY", (EQUITY, "INFY")),
    ("HDFCBANK", (EQUITY, "HDFCBANK")),
    ("M&M", (EQUITY, "M&M")),
    ("BAJAJ-AUTO", (EQUITY, "BAJAJ-AUTO")),
    ("ITC", (EQUITY, "ITC")),
    ("NIFTYBEES", (ETF, "NIFTYBEES")),
    ("JUNIORBEES", (ETF, "JUNIORBEES")),
    ("GOLDBEES", (ETF, "GOLDBEES")),
    ("BANKBEES", (ETF, "BANKBEES")),
    ("INE062A01020", (EQUITY, "SBIN")),
    ("INF204KB14I2", (ETF, "NIFTYBEES")),
    ("State Bank of India", (EQUITY, "SBIN")),
    ("Tata Consultancy Services", (EQUITY, "TCS")),
    ("Infosys", (EQUITY, "INFY")),
    ("Reliance Industries", (EQUITY, "RELIANCE")),
    ("Relience Industries", (EQUITY, "RELIANCE")),
    ("Hindustan Unilever", (EQUITY, "HINDUNILVR")),
    ("Larsen and Toubro", (EQUITY, "LT")),
    ("Asian Paints", (EQUITY, "ASIANPAINT")),
    ("RELIANC", (EQUITY, "RELIANCE")),
    ("SBI", AMBIGUOUS),
    ("TATA", AMBIGUOUS),
    ("nifty 50 etf", AMBIGUOUS),
    ("ZZZZQQ", UNKNOWN),
]


@pytest.fixture(scope="module")
def resolver():
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    return InstrumentResolver(InstrumentIndex.from_store(store))


@pytest.mark.parametrize("query, expected", CASES)
def test_live_resolver_labeled_cases(resolver, query, expected):
    try:
        result = resolver.resolve(query)
    except UnknownInstrument:
        assert expected == UNKNOWN
        return
    if isinstance(result, Ambiguity):
        assert expected == AMBIGUOUS
    else:
        assert isinstance(result, Resolution)
        assert expected == (result.asset_class, result.identifier)
```

- [ ] **Step 2: Verify the default run skips them and the live run passes**

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest --live tests/live/test_live_resolver.py -q
```

Expected: `152 passed, 33 skipped`, then `27 passed`. If a case fails, a real list has changed: read the result, fix the resolver or correct the label if the instrument was renamed; do not weaken the assertion.

- [ ] **Step 3: Update `TRD.md`**

In §2.12, after the numbered resolution steps, add this paragraph:

```markdown
*Implemented in Plan 0c (equity and ETF only; funds and bonds unsupported until their masters exist).* The model classifier is a `Classifier` protocol with no implementation yet. Candidate ranking is by edit similarity and is known to be noisy for short ambiguous queries (e.g. `SBI`); it is to be measured and improved against the labeled set. ETF free-text name search is weak because NSE ETF names are squashed (`NIPINDETFNIFTYBEES`); tickers and ISINs work.
```

Add a revision-history line at the top of the list:

```markdown
- **Oct 5, 2026 (Phase 0c)** — Instrument resolver implemented for equity and ETF (see `docs/superpowers/plans/2026-10-05-phase-0c-instrument-resolver.md`).
```

- [ ] **Step 4: Commit, push, refresh graph**

```bash
git add tests/live/test_live_resolver.py TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "test: add live labeled resolver cases; document resolver in TRD"
git push
.venv/Scripts/python -m graphify update .
```

---

## Self-Review (completed)

**Spec coverage (TRD §2.12 -> task):** normalise (Task 4), ISIN checksum + prefix hint (Task 2, 4), exact match with ETF-over-equity precedence (Task 4), fuzzy name match with candidates (Task 4), optional model classifier behind a `Classifier` interface with a 0.85 threshold and ask-the-user fallback (Task 4), routing table (Task 3), stale-master refusal (Task 4). PRD FR-1 acceptance: ambiguous input returns candidates, never a silent guess (Task 4 tests). Not in this plan: the Jev classifier implementation (gated on Plan 0d's labeled set), fund/bond masters, the 100–200-instrument labeled set (Plan 0d; this plan seeds it with 27).

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `Candidate`, `Resolution`, `Ambiguity`, `InstrumentIndex`, `route`, `DataStore.latest_records`, and the master dataset constants match across Tasks 1–5. Test totals: 106 (after 0b) + 2 (Task 1) + 12 + 3 + 29 = 152 passed; skipped 6 live + 27 new live = 33.

**Verified before writing:** all offline tests passed in a scratch copy (151 there, +1 for the contracts case added in Task 1), and the 27 live cases passed against the real NSE lists.
