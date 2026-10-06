from __future__ import annotations

from athena.strategies.model import Strategy
from athena.strategies.parse import parse_strategy


def _close() -> dict:
    return {"price": "close"}


def _sma(length: int) -> dict:
    return {"ind": "sma", "params": {"length": length}}


PRESET_DATA: dict[str, dict] = {
    "breakout_52w": {
        "name": "52-week high breakout",
        "entry": {"op": "ge", "left": _close(), "right": {"rolling": "max", "of": _close(), "window": 252, "shift": 1}},
        "exit": {"op": "le", "left": _close(), "right": {"rolling": "min", "of": _close(), "window": 252, "shift": 1}},
    },
    "golden_cross": {
        "name": "Golden cross",
        "entry": {"op": "crosses_above", "left": _sma(50), "right": _sma(200)},
        "exit": {"op": "crosses_below", "left": _sma(50), "right": _sma(200)},
    },
    "rsi_reversion": {
        "name": "RSI mean reversion",
        "entry": {"op": "lt", "left": {"ind": "rsi", "params": {"length": 14}}, "right": {"const": 30}},
        "exit": {"op": "gt", "left": {"ind": "rsi", "params": {"length": 14}}, "right": {"const": 55}},
        "stop_atr": 2.0,
    },
    "bollinger_rebound": {
        "name": "Bollinger rebound",
        "entry": {"op": "crosses_above", "left": _close(), "right": {"ind": "bbands", "params": {"length": 20, "width": 2.0}, "line": "lower"}},
        "exit": {"op": "crosses_above", "left": _close(), "right": _sma(20)},
        "stop_atr": 2.0,
    },
}

PRESETS: dict[str, Strategy] = {key: parse_strategy(data) for key, data in PRESET_DATA.items()}
