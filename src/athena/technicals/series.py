from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import talib

from athena.technicals.candles import Candle
from athena.technicals.packet import BOLLINGER_PERIOD, RSI_PERIOD


@dataclass(frozen=True)
class IndicatorSeries:
    """Indicator values aligned one-to-one with the candles; `None` where the indicator is still warming up."""

    days: tuple[date, ...]
    sma_50: tuple[float | None, ...]
    sma_200: tuple[float | None, ...]
    bb_upper: tuple[float | None, ...]
    bb_middle: tuple[float | None, ...]
    bb_lower: tuple[float | None, ...]
    rsi_14: tuple[float | None, ...]


def _clean(values: np.ndarray) -> tuple[float | None, ...]:
    return tuple(None if math.isnan(value) else float(value) for value in values)


def indicator_series(candles: Sequence[Candle]) -> IndicatorSeries:
    """Full indicator history for charting, from the same ta-lib calls and periods the technical packet uses."""
    close = np.array([c.close for c in candles], dtype=float)
    upper, middle, lower = talib.BBANDS(close, BOLLINGER_PERIOD, 2, 2)
    return IndicatorSeries(
        days=tuple(c.day for c in candles),
        sma_50=_clean(talib.SMA(close, 50)),
        sma_200=_clean(talib.SMA(close, 200)),
        bb_upper=_clean(upper),
        bb_middle=_clean(middle),
        bb_lower=_clean(lower),
        rsi_14=_clean(talib.RSI(close, RSI_PERIOD)),
    )
