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
