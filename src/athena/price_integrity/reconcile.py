from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from athena.backtest.adjust import HIGH_BREAK, LOW_BREAK, require_positive_prices
from athena.contracts import Bar
from athena.price_integrity.events import PriceEvent
from athena.trading_calendar import ist_date

MATCH_LOW, MATCH_HIGH = 0.75, 1.33  # the observed overnight gap over the one the ratio predicts: the market adds its own move
SEARCH_DAYS = 1  # the ex-date may be a trading day (not a calendar day) off between sources


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
        anchor = bisect_left(days, event.day)  # the first trading day on or after the ex-date
        near = [i for i in range(anchor - SEARCH_DAYS, anchor + SEARCH_DAYS + 1) if i in gaps and i not in claimed]
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
