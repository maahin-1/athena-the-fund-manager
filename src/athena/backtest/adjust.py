from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from athena.contracts import Bar, InsufficientData
from athena.trading_calendar import ist_date

# An overnight move this large is a corporate action, not the market: NSE circuit bands make a genuine one-session move
# of 40% down or 70% up very rare. Smaller ratios, such as a 3:2 bonus (x0.67), are therefore not detected.
LOW_BREAK, HIGH_BREAK = 0.6, 1.7
COMMON_RATIOS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 2.0, 3.0, 5.0, 10.0)
SNAP_TOLERANCE = 0.10  # an observed ratio within 10% of a common one is taken to be that one


@dataclass(frozen=True)
class Adjustment:
    day: date  # the first trading day after the break
    factor: float  # multiplies every earlier price: 0.5 for a 1:1 bonus, above 1 for a reverse split


def require_positive_prices(bars: Sequence[Bar]) -> None:
    if any(bar.open <= 0 or bar.close <= 0 for bar in bars):
        raise InsufficientData("the bars contain non-positive prices")


def _snap(ratio: float) -> float:
    nearest = min(COMMON_RATIOS, key=lambda common: abs(math.log(ratio / common)))
    return nearest if abs(ratio / nearest - 1) <= SNAP_TOLERANCE else ratio


def adjust_for_splits(bars: Sequence[Bar]) -> tuple[list[Bar], tuple[Adjustment, ...]]:
    """Unadjusted exchange prices (jugaad-data) fall by half on a 1:1 bonus and by nine tenths on a 10:1 split. Find
    such breaks from the overnight move alone (this is not corporate-action data, and dividends are never adjusted) and
    rescale every earlier bar so the history reads as one continuous series: prices times the factor, volume divided
    by it. Several events compose. Returns the sorted bars and the events found, oldest first."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    require_positive_prices(ordered)
    events: list[tuple[int, float]] = []  # (index of the first bar after the break, factor)
    for i in range(1, len(ordered)):
        ratio = ordered[i].open / ordered[i - 1].close
        if ratio < LOW_BREAK or ratio > HIGH_BREAK:
            events.append((i, _snap(ratio)))
    if not events:
        return ordered, ()

    adjusted = list(ordered)
    factor, pending = 1.0, list(events)
    for i in range(len(ordered) - 1, -1, -1):  # newest first, so each bar carries the product of every later event
        while pending and pending[-1][0] > i:
            factor *= pending.pop()[1]
        if factor != 1.0:
            bar = ordered[i]
            adjusted[i] = replace(
                bar, open=bar.open * factor, high=bar.high * factor, low=bar.low * factor, close=bar.close * factor,
                volume=bar.volume / factor,
            )
    return adjusted, tuple(Adjustment(ist_date(ordered[i].timestamp), factor) for i, factor in events)


def adjustment_note(adjustments: Sequence[Adjustment]) -> str | None:
    """The one sentence every report shows when prices were adjusted, so the command line and the dashboard agree."""
    if not adjustments:
        return None
    listed = ", ".join(f"{event.day} (x{event.factor:.4g})" for event in adjustments)
    return (
        f"Prices adjusted for {len(adjustments)} split or bonus event(s) found from an overnight price break, "
        f"not from corporate-action data: {listed}."
    )
