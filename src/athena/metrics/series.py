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
