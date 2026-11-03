"""What happened to a stock's price after a date: the outcome side of the cross-sectional test of the statement measures (design spec 6b).

Everything here reads prices that have splits and bonuses applied. The entry is the last close on or before the entry date; the window runs to the end date. A stock whose window holds a large overnight
break that no record explains is marked, because an unrecorded split would read as a collapse; the test leaves those out of its primary result and keeps them for a second run."""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from athena.contracts import Bar
from athena.trading_calendar import ist_date

MAX_ENTRY_GAP_DAYS = 7  # the entry close must be this recent, or the price is stale
LIQUIDITY_DAYS = 90  # about 60 trading days
MIN_LIQUID_BARS = 40
VOL_BARS = 250
MIN_VOL_RETURNS = 150
CEASED_GAP_DAYS = 14  # no bar in the last two weeks of the window: trading stopped
TRADE_FOR_TRADE = ("BE", "BZ")


@dataclass(frozen=True)
class Outcome:
    symbol: str
    entry_close: float
    max_fall: float | None  # the largest fall below the entry close at any close in the window, 0 to 1; None when no bar follows the entry
    final_return: float | None
    excess_return: float | None  # final return less the benchmark's over the same dates
    max_relative_fall: float | None  # the largest fall of the stock's path against the benchmark's, 0 to 1
    ceased: bool
    trade_for_trade: bool
    unexplained_break: bool
    volatility: float | None  # standard deviation of daily returns over the year before entry
    traded_value: float | None  # average daily close x volume over about 60 trading days before entry (as printed, in rupees)
    last_bar: date | None


def volatility(closes: Sequence[float]) -> float | None:
    """Standard deviation of daily simple returns, or None with fewer than `MIN_VOL_RETURNS` of them."""
    returns = [b / a - 1.0 for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    return statistics.pstdev(returns) if len(returns) >= MIN_VOL_RETURNS else None


def _last_on_or_before(series: Mapping[date, float], day: date) -> float | None:
    keys = [d for d in series if d <= day]
    return series[max(keys)] if keys else None


def outcome(
    symbol: str,
    bars: Sequence[Bar],
    entry: date,
    end: date,
    benchmark: Mapping[date, float],
    *,
    series_at_entry: str | None = None,
    series_in_window: Sequence[str] = (),
    unexplained_days: Sequence[date] = (),
    traded_value: float | None = None,
    liquid_bars: int = 0,
) -> Outcome | None:
    """The outcome of one stock, or None when it fails the entry floor (no close within `MAX_ENTRY_GAP_DAYS` before the entry date, or fewer than `MIN_LIQUID_BARS` bars in the `LIQUIDITY_DAYS` before it)."""
    dated = [(ist_date(b.timestamp), b.close) for b in sorted(bars, key=lambda b: b.timestamp) if b.close and b.close > 0]
    before = [(d, c) for d, c in dated if d <= entry]
    if not before or (entry - before[-1][0]).days > MAX_ENTRY_GAP_DAYS or liquid_bars < MIN_LIQUID_BARS:
        return None
    entry_day, entry_close = before[-1]
    window = [(d, c) for d, c in dated if entry < d <= end]
    bench_entry = _last_on_or_before(benchmark, entry_day)
    vol = volatility([c for _d, c in before[-(VOL_BARS + 1):]])
    broke = any(entry < d <= end for d in unexplained_days)
    tft = series_at_entry == "EQ" and any(s in TRADE_FOR_TRADE for s in series_in_window)
    if not window:
        return Outcome(symbol, entry_close, None, None, None, None, True, tft, broke, vol, traded_value, entry_day)
    fall = max(0.0, 1.0 - min(c for _d, c in window) / entry_close)
    final = window[-1][1] / entry_close - 1.0
    excess = relative = None
    if bench_entry:
        bench_final = _last_on_or_before(benchmark, window[-1][0])
        if bench_final:
            excess = final - (bench_final / bench_entry - 1.0)
        paths = [(c / entry_close) / (b / bench_entry) for d, c in window if (b := _last_on_or_before(benchmark, d))]
        if paths:
            relative = max(0.0, 1.0 - min(paths))
    ceased = (end - window[-1][0]).days > CEASED_GAP_DAYS
    return Outcome(symbol, entry_close, fall, final, excess, relative, ceased, tft, broke, vol, traded_value, window[-1][0])


def fifths(values: Mapping[str, float]) -> dict[str, int]:
    """Rank each symbol into a fifth (0 smallest to 4 largest) of the values given."""
    return _cut(values, 5)


def thirds(values: Mapping[str, float]) -> dict[str, int]:
    return _cut(values, 3)


def _cut(values: Mapping[str, float], parts: int) -> dict[str, int]:
    ordered = sorted(values, key=lambda s: (values[s], s))
    n = len(ordered)
    return {s: min(parts - 1, i * parts // n) for i, s in enumerate(ordered)} if n else {}


def is_finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
