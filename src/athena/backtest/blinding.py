from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from athena.backtest.adjust import require_positive_prices
from athena.contracts import Bar

BLIND_SYMBOL = "ASSET"
BLIND_BASE = 100.0
BLIND_YEARS = 28  # the Gregorian calendar repeats weekdays and month lengths every 28 years (1901-2099)


def blind_bars(bars: Sequence[Bar], base: float = BLIND_BASE, years: int = BLIND_YEARS) -> list[Bar]:
    """The same price history with its identity removed (PRD FR-11): the ticker becomes "ASSET", every date moves
    back by `years` calendar years (weekdays, months and leap years line up exactly, so weekly and monthly structure
    is unchanged), and prices are rescaled so the first close is `base`. Returns and volume are untouched, so any
    rule that uses only those must behave identically on blinded and real data."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    if not ordered:
        return []
    require_positive_prices(ordered)
    factor = base / ordered[0].close
    return [
        replace(
            bar,
            symbol=BLIND_SYMBOL,
            timestamp=bar.timestamp.replace(year=bar.timestamp.year - years),
            open=bar.open * factor,
            high=bar.high * factor,
            low=bar.low * factor,
            close=bar.close * factor,
            source="blinded",
        )
        for bar in ordered
    ]
