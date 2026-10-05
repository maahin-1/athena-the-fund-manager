from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import numpy as np
import talib

from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import MONTH, WEEK, Candle, candles_from_bars, resample

# (timeframe, moving-average length). Slope compares the average with its value SLOPE_LOOKBACK candles ago.
TREND_FRAMES = (("daily", 50), ("weekly", 20), ("monthly", 10))
SLOPE_LOOKBACK = 5
BOLLINGER_PERIOD = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_SHORT, VOLUME_LONG = 20, 50
SWING_WINDOW = 60
STOP_ATR_MULTIPLE = 2.0
REWARD_RISK_NOTE = (
    f"long side: reward is the distance up to the {SWING_WINDOW}-day high, risk is a stop "
    f"{STOP_ATR_MULTIPLE:g} x ATR{ATR_PERIOD} below the last close; 0 means price is at the high"
)
SOURCE = "computed from daily OHLCV (ta-lib)"


def _arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray]:
    return {
        "open": np.array([c.open for c in candles], dtype=float),
        "high": np.array([c.high for c in candles], dtype=float),
        "low": np.array([c.low for c in candles], dtype=float),
        "close": np.array([c.close for c in candles], dtype=float),
        "volume": np.array([c.volume for c in candles], dtype=float),
    }


def _need(count: int, minimum: int, what: str) -> None:
    if count < minimum:
        raise InsufficientData(f"{what} needs {minimum} candles, have {count}")


def _trend(candles: Sequence[Candle], length: int) -> str:
    _need(len(candles), length + SLOPE_LOOKBACK, f"{length}-candle trend")
    close = _arrays(candles)["close"]
    average = talib.SMA(close, length)
    above = close[-1] > average[-1]
    rising = average[-1] > average[-1 - SLOPE_LOOKBACK]
    if above and rising:
        return "up"
    if not above and not rising:
        return "down"
    return "mixed"


def build_technical_packet(instrument: str, as_of: datetime, bars: Sequence[Bar]) -> dict[str, Any]:
    """Technical-analysis packet in the metrics-packet shape (TRD section 3): every figure a
    Quant/Technical specialist may cite, computed in code. A figure that cannot be computed is
    listed in `missing` with a reason, never silently dropped."""
    daily = candles_from_bars(bars)
    frames = {"daily": daily, "weekly": resample(daily, WEEK), "monthly": resample(daily, MONTH)}
    data = _arrays(daily)
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    window = f"{len(daily)} daily candles {daily[0].day}..{daily[-1].day}" if daily else "0 candles"

    def attempt(name: str, unit: str, inputs: list[str], compute: Callable[[], Any], note: str | None = None) -> None:
        try:
            value = compute()
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metrics[name] = {
            "value": value if isinstance(value, str) else round(float(value), 4),
            "unit": unit,
            "inputs": inputs,
            "window": window,
            "source": SOURCE,
        }
        if note:
            metrics[name]["note"] = note

    def sma(length: int) -> float:
        _need(len(daily), length, f"{length}-day average")
        return float(talib.SMA(data["close"], length)[-1])

    def bollinger() -> tuple[float, float, float]:
        _need(len(daily), BOLLINGER_PERIOD, "Bollinger Bands")
        upper, middle, lower = talib.BBANDS(data["close"], BOLLINGER_PERIOD, 2, 2)
        return float(upper[-1]), float(middle[-1]), float(lower[-1])

    def percent_b() -> float:
        upper, _, lower = bollinger()
        if upper == lower:
            raise InsufficientData("Bollinger Bands have zero width")
        return (daily[-1].close - lower) / (upper - lower)

    def bandwidth() -> float:
        upper, middle, lower = bollinger()
        return (upper - lower) / middle

    def atr() -> float:
        _need(len(daily), ATR_PERIOD + 1, f"ATR{ATR_PERIOD}")
        return float(talib.ATR(data["high"], data["low"], data["close"], ATR_PERIOD)[-1])

    def rsi() -> float:
        _need(len(daily), RSI_PERIOD + 1, f"RSI{RSI_PERIOD}")
        return float(talib.RSI(data["close"], RSI_PERIOD)[-1])

    def volume_ratio() -> float:
        _need(len(daily), VOLUME_LONG, "volume ratio")
        long_average = data["volume"][-VOLUME_LONG:].mean()
        if long_average <= 0:
            raise InsufficientData("no volume data")
        return float(data["volume"][-VOLUME_SHORT:].mean() / long_average)

    def high_60d() -> float:
        _need(len(daily), SWING_WINDOW, f"{SWING_WINDOW}-day high")
        return float(data["high"][-SWING_WINDOW:].max())

    def reward_risk() -> float:
        risk = STOP_ATR_MULTIPLE * atr()
        if risk <= 0:
            raise InsufficientData("ATR is zero")
        return max(high_60d() - daily[-1].close, 0.0) / risk

    def last_close() -> float:
        _need(len(daily), 1, "last close")
        return daily[-1].close

    attempt("last_close", "price", ["close"], last_close)
    for name, length in TREND_FRAMES:
        attempt(
            f"trend_{name}", "label", ["close", f"sma_{length}"], lambda n=name, ln=length: _trend(frames[n], ln),
            note=f"close vs its {length}-candle average and that average's {SLOPE_LOOKBACK}-candle slope: up, down or mixed",
        )
    labels = [metrics[f"trend_{name}"]["value"] for name, _ in TREND_FRAMES if f"trend_{name}" in metrics]

    def alignment() -> str:
        if len(labels) < len(TREND_FRAMES):
            raise InsufficientData("alignment needs all three timeframes")
        if all(label == "up" for label in labels):
            return "aligned_up"
        if all(label == "down" for label in labels):
            return "aligned_down"
        return "not_aligned"

    attempt("trend_alignment", "label", [f"trend_{name}" for name, _ in TREND_FRAMES], alignment)
    attempt("sma_50", "price", ["close"], lambda: sma(50))
    attempt("sma_200", "price", ["close"], lambda: sma(200))
    attempt("rsi_14", "index", ["close"], rsi)
    attempt("bollinger_pct_b", "ratio", ["close"], percent_b, note="0 = lower band, 1 = upper band")
    attempt("bollinger_bandwidth", "fraction", ["close"], bandwidth, note="band width as a fraction of the middle band")
    attempt("atr_14", "price", ["high", "low", "close"], atr)
    attempt(
        f"volume_ratio_{VOLUME_SHORT}_{VOLUME_LONG}", "ratio", ["volume"], volume_ratio,
        note="average volume of the last 20 days over the last 50; above 1 means rising participation",
    )
    attempt("high_60d", "price", ["high"], high_60d)
    attempt("reward_risk", "ratio", ["high", "low", "close"], reward_risk, note=REWARD_RISK_NOTE)

    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
    }
