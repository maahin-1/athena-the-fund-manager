# Phase 1j-a: Event-verified price adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prices reach every consumer already corrected for recorded splits and bonuses, with any large move that no record explains left alone and flagged on the dashboard.

**Architecture:** A new `athena.price_integrity` package holds the event model, a pure `reconcile(bars, events)` function and a `CheckedHistory` wrapper placed at the one seam where bars are fetched (`history_fetcher` -> `RequestCache`). Events come from Yahoo's split list through an injectable provider, so every test runs offline. Spec: `docs/superpowers/specs/2026-10-10-phase-1j-data-integrity-design.md`.

**Tech Stack:** Python 3.13, yfinance (already a dependency), pytest, Streamlit (dashboard notes only).

## Global Constraints

- Nothing is adjusted from a guess: an adjustment needs a recorded event whose predicted overnight gap matches the observed gap within 0.75x to 1.33x.
- An overnight gap (open over the previous close) below 0.6 or above 1.7 with no matching event is returned as unexplained, never adjusted (`LOW_BREAK`, `HIGH_BREAK` from `athena.backtest.adjust`).
- Event ratio means shares received per share held: 5.0 for a 5:1 split, 2.0 for a 1:1 bonus. Earlier prices are multiplied by 1/ratio, earlier volume by ratio.
- Raw bars are never mutated; the wrapper returns new `Bar` objects. Latest prices are never changed by adjustment.
- A failed event lookup must not fail the request: it is reported in the notes and unexplained moves are still flagged.
- Dates are IST trading dates (`athena.trading_calendar.ist_date`). Yahoo dates and exchange dates can differ by one day, so match within one trading day.
- Do not edit `.env`. Do not run the live dashboard from inside a test.
- Existing tests keep passing (923 passed, 73 skipped at the start of this plan).

## File Structure

- Create `src/athena/price_integrity/__init__.py` (empty)
- Create `src/athena/price_integrity/events.py`: `PriceEvent`, `yahoo_split_events`
- Create `src/athena/price_integrity/reconcile.py`: `Move`, `Applied`, `Reconciled`, `reconcile`, `describe`
- Create `src/athena/price_integrity/history.py`: `CheckedHistory`
- Modify `src/athena/cli.py`: `LiveSources` gains `history`; `live_sources` wraps the fetcher
- Modify `src/athena/dashboard/service.py`: optional `price_notes`; `live_service` wraps `long_history`
- Modify `src/athena/evaluation/golden.py`, `src/athena/evaluation/capture.py`: snapshots carry `events`; packets are built from reconciled bars
- Create `tests/test_price_events.py`, `tests/test_price_reconcile.py`, `tests/test_price_history.py`, `tests/test_price_benchmark.py`
- Create `tests/golden/price_events.json`
- Modify `TRD.md`

---

### Task 1: The event model and the Yahoo source

**Files:**
- Create: `src/athena/price_integrity/__init__.py`, `src/athena/price_integrity/events.py`
- Test: `tests/test_price_events.py`

**Interfaces:**
- Produces: `PriceEvent(day: date, ratio: float, source: str = "yahoo")` with property `factor` (1/ratio); `yahoo_split_events(symbol: str, ticker=None) -> list[PriceEvent]`, raising `AthenaError` when the lookup fails.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date

import pandas as pd
import pytest

from athena.contracts import AthenaError
from athena.price_integrity.events import PriceEvent, yahoo_split_events


class FakeTicker:
    def __init__(self, splits=None, error=None):
        self._splits, self._error = splits, error

    @property
    def splits(self):
        if self._error:
            raise self._error
        return self._splits


def series(pairs):
    return pd.Series([v for _, v in pairs], index=pd.DatetimeIndex([d for d, _ in pairs], tz="Asia/Kolkata"), dtype=float)


def test_a_five_for_one_split_is_a_fifth_of_the_price():
    event = PriceEvent(date(2026, 1, 14), 5.0)
    assert event.factor == pytest.approx(0.2) and event.source == "yahoo"


def test_yahoo_splits_become_events_dated_in_ist():
    got = yahoo_split_events("KOTAKBANK", FakeTicker(series([("2026-01-14", 5.0), ("2024-10-28", 2.0)])))
    assert got == [PriceEvent(date(2024, 10, 28), 2.0), PriceEvent(date(2026, 1, 14), 5.0)]


def test_a_reverse_split_is_a_ratio_below_one_and_empty_or_unit_rows_are_ignored():
    got = yahoo_split_events("X", FakeTicker(series([("2025-03-03", 0.1), ("2025-04-01", 1.0), ("2025-05-01", 0.0)])))
    assert got == [PriceEvent(date(2025, 3, 3), 0.1)]


def test_a_stock_that_never_split_has_no_events_and_a_failed_lookup_says_so():
    assert yahoo_split_events("X", FakeTicker(series([]))) == []
    with pytest.raises(AthenaError, match="corporate actions unavailable for X"):
        yahoo_split_events("X", FakeTicker(error=RuntimeError("blocked")))
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python -m pytest tests/test_price_events.py -q`
Expected: FAIL with `ModuleNotFoundError: athena.price_integrity`

- [ ] **Step 3: Implement**

`src/athena/price_integrity/__init__.py` is empty. `src/athena/price_integrity/events.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from athena.contracts import AthenaError


@dataclass(frozen=True)
class PriceEvent:
    """A recorded corporate action that changes the price without changing value. `ratio` is shares received per share
    held: 5.0 for a 5:1 split, 2.0 for a 1:1 bonus, 0.1 for a 1:10 consolidation."""

    day: date  # the ex-date, an IST trading date
    ratio: float
    source: str = "yahoo"

    @property
    def factor(self) -> float:
        """What every earlier price is multiplied by to read on today's basis."""
        return 1.0 / self.ratio


def yahoo_split_events(symbol: str, ticker=None) -> list[PriceEvent]:
    """Yahoo's split list (splits and bonus issues, with ratios) for an NSE symbol, oldest first. A stock that never split
    gives an empty list. Raises `AthenaError` when Yahoo cannot be reached."""
    try:
        if ticker is None:
            import yfinance

            ticker = yfinance.Ticker(f"{symbol}.NS")
        splits = ticker.splits
    except Exception as exc:
        raise AthenaError(f"corporate actions unavailable for {symbol}: {exc}") from exc
    events = [PriceEvent(stamp.date(), float(ratio)) for stamp, ratio in splits.items() if ratio and ratio > 0 and ratio != 1]
    return sorted(events, key=lambda event: event.day)
```

- [ ] **Step 4: Run it to see it pass**

Run: `.venv/Scripts/python -m pytest tests/test_price_events.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/athena/price_integrity tests/test_price_events.py
git commit -m "feat: price events from Yahoo's split list"
```

---

### Task 2: Reconcile prices against recorded events

**Files:**
- Create: `src/athena/price_integrity/reconcile.py`
- Test: `tests/test_price_reconcile.py`

**Interfaces:**
- Consumes: `PriceEvent` (Task 1); `LOW_BREAK`, `HIGH_BREAK`, `require_positive_prices` from `athena.backtest.adjust`; `ist_date`.
- Produces: `reconcile(bars: Sequence[Bar], events: Sequence[PriceEvent]) -> Reconciled`; `Reconciled(bars: tuple[Bar, ...], applied: tuple[Applied, ...], unseen: tuple[PriceEvent, ...], unexplained: tuple[Move, ...])`; `Applied(event: PriceEvent, observed: float)`; `Move(day: date, ratio: float)`; `describe(result) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

import pytest
from bar_factory import make_bars

from athena.price_integrity.events import PriceEvent
from athena.price_integrity.reconcile import reconcile
from athena.trading_calendar import ist_date

FLAT = [100.0] * 30


def split_series(ratio, before=30, after=30):
    """`before` flat closes at 100, then `after` flat closes at 100/ratio: a split with no market move."""
    return make_bars([100.0] * before + [100.0 / ratio] * after)


def day_of(bars, index):
    return ist_date(bars[index].timestamp)


def test_a_recorded_split_that_matches_the_price_gap_is_adjusted_by_the_recorded_ratio():
    bars = split_series(5.0)
    result = reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)])
    assert [b.close for b in result.bars] == pytest.approx([20.0] * 60)
    assert result.bars[0].volume == pytest.approx(5000.0) and result.bars[-1].volume == 1000.0  # volume times the ratio, before only
    assert len(result.applied) == 1 and result.applied[0].observed == pytest.approx(0.2) and not result.unexplained and not result.unseen


def test_the_event_date_may_be_one_day_off_from_the_price_gap():
    bars = split_series(2.0)
    assert reconcile(bars, [PriceEvent(day_of(bars, 31), 2.0)]).applied
    assert reconcile(bars, [PriceEvent(day_of(bars, 29), 2.0)]).applied
    assert not reconcile(bars, [PriceEvent(day_of(bars, 35), 2.0)]).applied


def test_a_market_move_on_top_of_the_split_still_matches_but_a_wrong_size_does_not():
    bars = make_bars([100.0] * 30 + [22.0] * 30)  # 5:1 split plus a 10% rise on the day
    assert reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)]).applied
    bars = make_bars([100.0] * 30 + [50.0] * 30)  # a halving, but the record says 5:1
    result = reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)])
    assert not result.applied and result.unseen and [b.close for b in result.bars] == [100.0] * 30 + [50.0] * 30


def test_a_halving_with_no_record_is_left_alone_and_reported_as_unexplained():
    bars = split_series(2.0)  # looks exactly like a 1:1 bonus; only a record could tell it from a crash
    result = reconcile(bars, [])
    assert [b.close for b in result.bars] == [100.0] * 30 + [50.0] * 30
    assert [(m.day, round(m.ratio, 2)) for m in result.unexplained] == [(day_of(bars, 30), 0.5)]


def test_a_record_with_no_gap_in_the_prices_is_unseen_not_applied():
    bars = make_bars(FLAT + FLAT)
    result = reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)])
    assert result.unseen and not result.applied and [b.close for b in result.bars] == [100.0] * 60


def test_two_events_compose_and_a_reverse_split_scales_up():
    bars = make_bars([100.0] * 20 + [50.0] * 20 + [25.0] * 20)
    result = reconcile(bars, [PriceEvent(day_of(bars, 20), 2.0), PriceEvent(day_of(bars, 40), 2.0)])
    assert [b.close for b in result.bars] == pytest.approx([25.0] * 60)
    bars = make_bars([10.0] * 20 + [100.0] * 20)
    assert [b.close for b in reconcile(bars, [PriceEvent(day_of(bars, 20), 0.1)]).bars] == pytest.approx([100.0] * 40)


def test_input_is_not_changed_and_unsorted_bars_come_back_in_date_order():
    bars = split_series(5.0)
    before = [b.close for b in bars]
    result = reconcile(list(reversed(bars)), [PriceEvent(day_of(bars, 30), 5.0)])
    assert [b.close for b in bars] == before
    assert [b.timestamp for b in result.bars] == sorted(b.timestamp for b in bars)
```

- [ ] **Step 2: Run to see failure**

Run: `.venv/Scripts/python -m pytest tests/test_price_reconcile.py -q`
Expected: FAIL with `ModuleNotFoundError: athena.price_integrity.reconcile`

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from athena.backtest.adjust import HIGH_BREAK, LOW_BREAK, require_positive_prices
from athena.contracts import Bar
from athena.price_integrity.events import PriceEvent
from athena.trading_calendar import ist_date

MATCH_LOW, MATCH_HIGH = 0.75, 1.33  # the observed overnight gap over the one the ratio predicts: the market adds its own move
SEARCH_DAYS = 1  # the ex-date may be a trading day off between sources


@dataclass(frozen=True)
class Move:
    day: date
    ratio: float  # open over the previous close


@dataclass(frozen=True)
class Applied:
    event: PriceEvent
    observed: float  # the overnight gap that agreed with the record


@dataclass(frozen=True)
class Reconciled:
    bars: tuple[Bar, ...]
    applied: tuple[Applied, ...]  # recorded events whose size the prices confirmed: these were adjusted
    unseen: tuple[PriceEvent, ...]  # recorded, but no matching gap (already adjusted at source, or outside the history)
    unexplained: tuple[Move, ...]  # large gaps no record explains: left exactly as they are


def reconcile(bars: Sequence[Bar], events: Sequence[PriceEvent]) -> Reconciled:
    """Adjust earlier prices for a recorded split or bonus only when the overnight gap near its date agrees with the
    recorded ratio. A gap with no record is never adjusted, so a genuine crash stays a crash."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    require_positive_prices(ordered)
    days = [ist_date(bar.timestamp) for bar in ordered]
    gaps = {i: ordered[i].open / ordered[i - 1].close for i in range(1, len(ordered))}

    claimed: dict[int, float] = {}  # index of the first bar after the break -> the recorded factor
    applied: list[Applied] = []
    unseen: list[PriceEvent] = []
    for event in sorted(events, key=lambda e: e.day):
        near = [i for i in gaps if abs((days[i] - event.day).days) <= SEARCH_DAYS and i not in claimed]
        fits = [(abs(math.log(gaps[i] / event.factor)), i) for i in near if MATCH_LOW <= gaps[i] / event.factor <= MATCH_HIGH]
        if fits:
            index = min(fits)[1]
            claimed[index] = event.factor
            applied.append(Applied(event, gaps[index]))
        else:
            unseen.append(event)

    unexplained = tuple(Move(days[i], gap) for i, gap in gaps.items() if i not in claimed and (gap < LOW_BREAK or gap > HIGH_BREAK))

    adjusted = list(ordered)
    factor, pending = 1.0, sorted(claimed.items())
    for i in range(len(ordered) - 1, -1, -1):  # newest first, so each bar carries the product of every later event
        while pending and pending[-1][0] > i:
            factor *= pending.pop()[1]
        if factor != 1.0:
            bar = ordered[i]
            adjusted[i] = replace(
                bar, open=bar.open * factor, high=bar.high * factor, low=bar.low * factor, close=bar.close * factor,
                volume=bar.volume / factor,
            )
    return Reconciled(tuple(adjusted), tuple(applied), tuple(unseen), unexplained)


def describe(result: Reconciled) -> tuple[str, ...]:
    """The sentences the dashboard shows about what was done, and what could not be explained."""
    notes: list[str] = []
    if result.applied:
        listed = ", ".join(f"{a.event.day} (x{a.event.factor:.4g}, {a.event.source})" for a in result.applied)
        notes.append(f"Prices adjusted for {len(result.applied)} recorded split or bonus event(s), each confirmed by the price gap: {listed}.")
    if result.unexplained:
        listed = ", ".join(f"{m.day} (x{m.ratio:.2g})" for m in result.unexplained)
        notes.append(
            f"Unexplained overnight price break(s) left unadjusted: {listed}. A demerger, merger or genuine move cannot be "
            "told from a split without a record; volatility, drawdown and trend figures that span it are unreliable."
        )
    if result.unseen:
        listed = ", ".join(f"{e.day} (x{e.factor:.4g})" for e in result.unseen)
        notes.append(f"Recorded event(s) with no matching price break, so nothing was adjusted: {listed}.")
    return tuple(notes)
```

- [ ] **Step 4: Run to see pass**

Run: `.venv/Scripts/python -m pytest tests/test_price_reconcile.py -q`
Expected: 7 passed. If `test_the_event_date_may_be_one_day_off` fails on the 35-day case, `SEARCH_DAYS` is too wide; it must stay 1.

- [ ] **Step 5: Commit**

```bash
git add src/athena/price_integrity/reconcile.py tests/test_price_reconcile.py
git commit -m "feat: adjust prices only for recorded events that the price gap confirms"
```

---

### Task 3: The wrapper at the fetch seam

**Files:**
- Create: `src/athena/price_integrity/history.py`
- Test: `tests/test_price_history.py`

**Interfaces:**
- Consumes: `reconcile`, `describe` (Task 2); `PriceEvent`.
- Produces: `CheckedHistory(fetch: Callable[[str], list[Bar]], events: Callable[[str], Sequence[PriceEvent]])`, callable as `fetch(symbol) -> list[Bar]`, with `notes(symbol) -> tuple[str, ...]` for the last fetch of that symbol.

- [ ] **Step 1: Write the failing test**

```python
from bar_factory import make_bars

from athena.contracts import AthenaError
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.history import CheckedHistory
from athena.trading_calendar import ist_date

BARS = make_bars([100.0] * 30 + [20.0] * 30, symbol="KOTAKBANK")
EX_DATE = ist_date(BARS[30].timestamp)


def test_fetched_bars_come_back_adjusted_with_a_note_saying_so():
    history = CheckedHistory(lambda symbol: list(BARS), lambda symbol: [PriceEvent(EX_DATE, 5.0)])
    assert [b.close for b in history("KOTAKBANK")] == [20.0] * 60
    assert "adjusted for 1 recorded" in history.notes("KOTAKBANK")[0]
    assert history.notes("OTHER") == ()


def test_a_failed_event_lookup_does_not_fail_the_request_and_still_flags_the_break():
    def broken(symbol):
        raise AthenaError("corporate actions unavailable for KOTAKBANK: blocked")

    history = CheckedHistory(lambda symbol: list(BARS), broken)
    assert [b.close for b in history("KOTAKBANK")] == [100.0] * 30 + [20.0] * 30
    notes = history.notes("KOTAKBANK")
    assert "corporate actions unavailable" in notes[0] and any("Unexplained" in note for note in notes)


def test_no_events_and_no_breaks_means_no_notes_and_the_bars_are_returned_untouched():
    flat = make_bars([100.0] * 40)
    history = CheckedHistory(lambda symbol: list(flat), lambda symbol: [])
    assert history("X") == flat and history.notes("X") == ()
```

- [ ] **Step 2: Run to see failure**

Run: `.venv/Scripts/python -m pytest tests/test_price_history.py -q`
Expected: FAIL with `ModuleNotFoundError: athena.price_integrity.history`

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

from collections.abc import Callable, Sequence

from athena.contracts import AthenaError, Bar
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.reconcile import describe, reconcile


class CheckedHistory:
    """Daily bars with recorded splits and bonuses applied, in place of the raw fetcher. Whoever shows prices asks
    `notes(symbol)` for what was adjusted or could not be explained. A failed event lookup is itself a note: the prices
    are then returned raw, with every large break flagged as unexplained."""

    def __init__(self, fetch: Callable[[str], list[Bar]], events: Callable[[str], Sequence[PriceEvent]]):
        self._fetch = fetch
        self._events = events
        self._notes: dict[str, tuple[str, ...]] = {}

    def __call__(self, symbol: str) -> list[Bar]:
        raw = self._fetch(symbol)
        notes: list[str] = []
        try:
            events = list(self._events(symbol))
        except AthenaError as exc:
            events = []
            notes.append(f"{exc}. Large price breaks cannot be checked against a record.")
        result = reconcile(raw, events)
        self._notes[symbol] = tuple(notes) + describe(result)
        return list(result.bars)

    def notes(self, symbol: str) -> tuple[str, ...]:
        return self._notes.get(symbol, ())
```

- [ ] **Step 4: Run to see pass**

Run: `.venv/Scripts/python -m pytest tests/test_price_history.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/athena/price_integrity/history.py tests/test_price_history.py
git commit -m "feat: a checked price history that reports what it adjusted"
```

---

### Task 4: Wire the wrapper into the live sources and the dashboard

**Files:**
- Modify: `src/athena/cli.py` (`LiveSources`, `live_sources`)
- Modify: `src/athena/dashboard/service.py` (`DashboardService.__init__`, `view`, `live_service`)
- Test: `tests/test_dashboard_service.py` (add), `tests/test_cli.py` only if a LiveSources field breaks it

**Interfaces:**
- Consumes: `CheckedHistory`, `yahoo_split_events`.
- Produces: `LiveSources.history: CheckedHistory`; `DashboardService(..., price_notes: Callable[[str], Sequence[str]] | None = None)`; the view's `notes` gains the price notes after the existing ones.

- [ ] **Step 1: Read the two files' current call sites with the graph, not whole files**

Run: `graphify explain "live_sources()"` and `graphify explain "DashboardService"` from the project root, then read only `src/athena/cli.py:65-92` and `src/athena/dashboard/service.py:30-70` to edit them.

- [ ] **Step 2: Write the failing test** (in `tests/test_dashboard_service.py`, using that file's existing `make_service` helper; read its signature at the top first)

```python
def test_the_price_notes_for_the_symbol_are_added_to_the_view_notes():
    service = make_service(price_notes=lambda symbol: (f"Prices adjusted for {symbol}.",))
    view = service.view("sbin")
    assert view.notes[-1] == "Prices adjusted for SBIN."


def test_without_a_price_notes_source_the_view_notes_are_unchanged():
    assert all("Prices adjusted" not in note for note in make_service().view("sbin").notes)
```

If `make_service` has no `price_notes` parameter, add `price_notes=None` and pass it through to `DashboardService`.

- [ ] **Step 3: Run to see failure**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard_service.py -q`
Expected: FAIL with `TypeError ... unexpected keyword argument 'price_notes'`

- [ ] **Step 4: Implement**

In `service.py`, add the parameter and append the notes after `build_view`:

```python
        price_notes: Callable[[str], Sequence[str]] | None = None,
    ):
        ...
        self._price_notes = price_notes
```

```python
        view = build_view(result, bars, technical, risk, fundamentals, note)
        if self._price_notes is None:
            return view
        return replace(view, notes=view.notes + tuple(self._price_notes(result.resolution.identifier)))
```

(`from dataclasses import replace` at the top.) In `live_service`, build one `CheckedHistory` for the long history and pass the other's notes:

```python
    long_history = lru_cache(maxsize=8)(
        CheckedHistory(history_fetcher(sources.chain, days=365 * BACKTEST_YEARS), sources.events)
    )
    return DashboardService(
        orchestrator, sources.bars, risk_world_from_store(sources.store), fundamentals=sources.fundamentals,
        long_history=long_history, price_notes=sources.history.notes,
    )
```

In `cli.py`, `LiveSources` gains `history: CheckedHistory` and `events: Callable[[str], Sequence[PriceEvent]]`; in `live_sources` replace the bars line:

```python
    events = lru_cache(maxsize=512)(yahoo_split_events)  # events change rarely: one Yahoo call per symbol per session
    history = CheckedHistory(history_fetcher(chain), events)
    bars = RequestCache(history)
```

and return `LiveSources(resolver, llm_router, chain, store, bars, fundamentals, history, events)`. Any other constructor of `LiveSources` (tests) gets the two new fields.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (923 + the new ones)

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat: every price consumer reads event-verified prices, and the dashboard says what was adjusted"
```

---

### Task 5: The benchmark, from frozen snapshots

**Files:**
- Modify: `src/athena/evaluation/golden.py` (`Snapshot.events`, snapshot JSON, `build_packets`), `src/athena/evaluation/capture.py`
- Create: `tests/golden/price_events.json`
- Test: `tests/test_price_benchmark.py`

**Interfaces:**
- Produces: `Snapshot.events: tuple[PriceEvent, ...]` (default `()`), written and read in the snapshot JSON as `[[iso day, ratio], ...]`; `build_packets` reconciles `snapshot.bars` with `snapshot.events` before building packets.

- [ ] **Step 1: Add events to snapshots**

`Snapshot` gains `events: tuple[PriceEvent, ...] = ()`; `snapshot_to_json` writes `"events": [[e.day.isoformat(), e.ratio] for e in snapshot.events]`; `snapshot_from_json` reads `raw.get("events", [])`. `capture_symbol` fills it with `yahoo_split_events(identifier)` (an `AthenaError` becomes an empty tuple plus a printed warning). Add `backfill_events(directory)` to `capture.py`, which loads each existing snapshot, sets `events` from Yahoo and rewrites the file without touching its bars. Run it once: `.venv/Scripts/python -m athena.evaluation.capture --backfill-events`. Capture VEDL, which is not yet a snapshot: `... capture VEDL`.

- [ ] **Step 2: Write `tests/golden/price_events.json`** from the frozen snapshots' recorded events, after reading them (do not write the answers from memory):

```json
[
 {"symbol": "KOTAKBANK", "applied": ["2026-01-14"], "unexplained": []},
 {"symbol": "HDFCBANK", "applied": ["2025-08-26"], "unexplained": []},
 {"symbol": "RELIANCE", "applied": ["2024-10-28"], "unexplained": []},
 {"symbol": "VEDL", "applied": [], "unexplained": ["<the demerger day printed by the capture>"]}
]
```

Check each date against `reconcile` output and the snapshot's biggest overnight move before committing the file; a date is written only if the data shows it.

- [ ] **Step 3: Write the failing tests**

```python
import json
from dataclasses import replace

import pytest

from athena.evaluation.golden import GOLDEN_DIR, load_snapshot
from athena.price_integrity.reconcile import reconcile

CASES = json.loads((GOLDEN_DIR / "price_events.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["symbol"] for c in CASES])
def test_each_known_event_is_adjusted_and_each_unexplained_break_is_flagged(case):
    snapshot = load_snapshot(case["symbol"])
    result = reconcile(snapshot.bars, snapshot.events)
    assert sorted(str(a.event.day) for a in result.applied) == case["applied"]
    assert sorted(str(m.day) for m in result.unexplained) == case["unexplained"]


@pytest.mark.parametrize("symbol", ["KOTAKBANK", "HDFCBANK", "RELIANCE"])
def test_an_adjusted_series_has_no_break_left_where_the_event_was(symbol):
    snapshot = load_snapshot(symbol)
    closes = [b.close for b in reconcile(snapshot.bars, snapshot.events).bars]
    assert min(b / a for a, b in zip(closes, closes[1:])) > 0.7


@pytest.mark.parametrize("symbol", ["KOTAKBANK", "HDFCBANK", "RELIANCE"])
def test_the_same_series_with_its_record_removed_is_left_alone_and_flagged(symbol):
    """The genuine-crash control: a halving with no record must never be adjusted away."""
    snapshot = load_snapshot(symbol)
    result = reconcile(snapshot.bars, ())
    assert [b.close for b in result.bars] == [b.close for b in snapshot.bars] and result.unexplained
```

- [ ] **Step 4: Run to see failure, then pass**

Run: `.venv/Scripts/python -m pytest tests/test_price_benchmark.py -q`
Expected: first FAIL (no `events` on snapshots), then pass after Step 1.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "test: a price-event benchmark from real frozen snapshots, with the genuine-crash control"
```

---

### Task 6: Re-check the golden labels on adjusted data, and add Kotak

**Files:**
- Modify: `src/athena/evaluation/golden.py` (`build_packets`), `tests/golden/cases.json`

- [ ] **Step 1:** `build_packets` builds the technical packet from `reconcile(snapshot.bars, snapshot.events).bars`.
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_golden_sets.py -q`. Expected: `HDFCBANK` and `RELIANCE` may now fail their `trend_alignment` label (`aligned_down`), because the bonus no longer fakes a fall. Print the new value for each and the 1-year return on the adjusted series.
- [ ] **Step 3:** Update those two labels to what the adjusted data shows, with a one-line `why` that says the old label was drawn from unadjusted prices. Add `KOTAKBANK` with `figures {"technical.rsi_14": [0, 100]}` and `values` for `trend_alignment` set from its adjusted series. Mark all three in the commit message as needing the owner's review.
- [ ] **Step 4:** Run the full suite, then the live golden test (`pytest --live tests/live/test_live_golden.py -s`) and report the table.
- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "fix: golden labels drawn from unadjusted prices re-checked on event-verified data; add Kotak"
```

---

### Task 7: Record it, run it, hand over

- [ ] **Step 1:** Add a paragraph to TRD 2.13/2.14 and the changelog: the rule (nothing adjusted without a record that the price gap confirms), the seam, the benchmark, the open decision about retiring `adjust_for_splits`, and what is not covered (demergers and rights until the source check).
- [ ] **Step 2:** Restart the dashboard (stop the old server first), open Kotak, and confirm the volatility and drawdown are sane and the note about the adjustment shows. Compare against the Oct 10 figures (volatility 83%, drawdown −84%).
- [ ] **Step 3:** Run the full suite and commit the TRD.
- [ ] **Step 4:** Report to the owner: the Kotak before and after, the two relabelled cases, and the decision still open.

## Follow-on plans (each gated, written after the one before)

- **1j-b Source check, then exchange events:** test NSE and BSE corporate-action sources on the benchmark events (Kotak, HDFC Bank, Vedanta, a rights issue, a buyback). Build the layer only for sources that return ratios; otherwise record why not.
- **1j-c News confirmation and the news specialist:** GDELT or RSS; confirmation of events, then sentiment.
- **1j-d Statement checks:** EPS x shares against net income, scale, merger-date breaks, standalone versus consolidated.
- **1j-e Price-data checks and share classes:** ISIN-keyed history, stale days, two-source disagreement, issuer grouping.

## Self-review (done)

- Spec coverage: layers 1 and 2 are Tasks 1-4; the benchmark and the genuine-crash control are Task 5; golden re-check is Task 6; layers 3-7 are follow-on plans, named, because they depend on a source check that has not been run.
- No placeholders other than Task 5's file, whose dates must be read from the data; the step says so.
- Types: `PriceEvent.factor`, `Reconciled` fields and `CheckedHistory.notes` are used with the same names in every task.
