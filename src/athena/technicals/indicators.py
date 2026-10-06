from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import talib

from athena.technicals.candles import Candle

OVERLAY, PANE = "overlay", "pane"  # drawn on the price chart, or in a sub-chart of its own
MAX_INDICATORS = 8


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float = 1.0
    integer: bool = True


@dataclass(frozen=True)
class Line:
    """One drawn series, aligned one-to-one with the candles; `None` where the indicator is still warming up."""

    name: str
    values: tuple[float | None, ...]
    style: str = "solid"  # solid | dot | dots (markers only) | bars


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    label: str
    placement: str
    params: tuple[Param, ...]
    compute: Callable[[Mapping[str, np.ndarray], Mapping[str, float]], list[Line]]
    levels: tuple[float, ...] = ()  # horizontal reference lines in a pane
    y_range: tuple[float, float] | None = None
    check: Callable[[Mapping[str, float]], str | None] = lambda params: None  # a rule across parameters


@dataclass(frozen=True)
class Selected:
    """One indicator the person chose: plain data, so a selection can be stored or sent as JSON."""

    id: str
    key: str
    params: Mapping[str, float]

    def to_dict(self) -> dict:
        return {"id": self.id, "key": self.key, "params": dict(self.params)}

    @staticmethod
    def from_dict(data: Mapping) -> Selected:
        return Selected(str(data["id"]), str(data["key"]), dict(data["params"]))


def _clean(values: np.ndarray) -> tuple[float | None, ...]:
    return tuple(None if math.isnan(value) else float(value) for value in values)


def _int(p: Mapping[str, float], name: str) -> int:
    return int(p[name])


def _rolling(values: np.ndarray, length: int, pick: Callable[[np.ndarray], float]) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(length - 1, len(values)):
        out[i] = pick(values[i + 1 - length : i + 1])
    return out


def _sma(a, p):
    return [Line(f"SMA {_int(p, 'length')}", _clean(talib.SMA(a["close"], _int(p, "length"))))]


def _ema(a, p):
    return [Line(f"EMA {_int(p, 'length')}", _clean(talib.EMA(a["close"], _int(p, "length"))))]


def _wma(a, p):
    return [Line(f"WMA {_int(p, 'length')}", _clean(talib.WMA(a["close"], _int(p, "length"))))]


def _bbands(a, p):
    length, width = _int(p, "length"), float(p["width"])
    upper, _, lower = talib.BBANDS(a["close"], length, width, width)
    tag = f"({length}, {width:g})"
    return [Line(f"Bollinger upper {tag}", _clean(upper), "dot"), Line(f"Bollinger lower {tag}", _clean(lower), "dot")]


def _sar(a, p):
    return [Line("Parabolic SAR", _clean(talib.SAR(a["high"], a["low"], float(p["acceleration"]), float(p["maximum"]))), "dots")]


def _channel(a, p):
    length = _int(p, "length")
    return [
        Line(f"High {length}", _clean(_rolling(a["high"], length, np.max)), "dot"),
        Line(f"Low {length}", _clean(_rolling(a["low"], length, np.min)), "dot"),
    ]


def _rsi(a, p):
    return [Line(f"RSI {_int(p, 'length')}", _clean(talib.RSI(a["close"], _int(p, "length"))))]


def _macd(a, p):
    macd, signal, histogram = talib.MACD(a["close"], _int(p, "fast"), _int(p, "slow"), _int(p, "signal"))
    return [Line("MACD", _clean(macd)), Line("Signal", _clean(signal)), Line("Histogram", _clean(histogram), "bars")]


def _stoch(a, p):
    slow_k, slow_d = talib.STOCH(
        a["high"], a["low"], a["close"], fastk_period=_int(p, "k"), slowk_period=_int(p, "smooth"), slowk_matype=0,
        slowd_period=_int(p, "d"), slowd_matype=0,
    )
    return [Line("%K", _clean(slow_k)), Line("%D", _clean(slow_d))]


def _adx(a, p):
    length = _int(p, "length")
    return [
        Line(f"ADX {length}", _clean(talib.ADX(a["high"], a["low"], a["close"], length))),
        Line("+DI", _clean(talib.PLUS_DI(a["high"], a["low"], a["close"], length)), "dot"),
        Line("-DI", _clean(talib.MINUS_DI(a["high"], a["low"], a["close"], length)), "dot"),
    ]


def _atr(a, p):
    return [Line(f"ATR {_int(p, 'length')}", _clean(talib.ATR(a["high"], a["low"], a["close"], _int(p, "length"))))]


def _obv(a, p):
    return [Line("OBV", _clean(talib.OBV(a["close"], a["volume"])))]


def _cci(a, p):
    return [Line(f"CCI {_int(p, 'length')}", _clean(talib.CCI(a["high"], a["low"], a["close"], _int(p, "length"))))]


def _mfi(a, p):
    return [Line(f"MFI {_int(p, 'length')}", _clean(talib.MFI(a["high"], a["low"], a["close"], a["volume"], _int(p, "length"))))]


def _willr(a, p):
    return [Line(f"Williams %R {_int(p, 'length')}", _clean(talib.WILLR(a["high"], a["low"], a["close"], _int(p, "length"))))]


def _roc(a, p):
    return [Line(f"ROC {_int(p, 'length')}", _clean(talib.ROC(a["close"], _int(p, "length"))))]


def _length(default: int, minimum: int = 2, maximum: int = 400) -> Param:
    return Param("length", "Length", default, minimum, maximum)


def _macd_check(p: Mapping[str, float]) -> str | None:
    return "the fast length must be shorter than the slow length" if p["fast"] >= p["slow"] else None


REGISTRY: dict[str, IndicatorSpec] = {
    spec.key: spec
    for spec in (
        IndicatorSpec("sma", "Simple moving average", OVERLAY, (_length(50),), _sma),
        IndicatorSpec("ema", "Exponential moving average", OVERLAY, (_length(20),), _ema),
        IndicatorSpec("wma", "Weighted moving average", OVERLAY, (_length(20),), _wma),
        IndicatorSpec(
            "bbands", "Bollinger Bands", OVERLAY,
            (Param("length", "Length", 20, 5, 200), Param("width", "Width (std dev)", 2.0, 0.5, 4.0, 0.5, integer=False)), _bbands,
        ),
        IndicatorSpec(
            "sar", "Parabolic SAR", OVERLAY,
            (Param("acceleration", "Acceleration", 0.02, 0.01, 0.2, 0.01, integer=False), Param("maximum", "Maximum", 0.2, 0.1, 0.5, 0.05, integer=False)),
            _sar,
        ),
        IndicatorSpec("channel", "High-low channel (252 = 52 weeks)", OVERLAY, (_length(252, 5, 400),), _channel),
        IndicatorSpec("rsi", "RSI", PANE, (_length(14, 2, 100),), _rsi, levels=(30.0, 70.0), y_range=(0.0, 100.0)),
        IndicatorSpec(
            "macd", "MACD", PANE,
            (Param("fast", "Fast", 12, 2, 100), Param("slow", "Slow", 26, 3, 200), Param("signal", "Signal", 9, 2, 100)),
            _macd, levels=(0.0,), check=_macd_check,
        ),
        IndicatorSpec(
            "stoch", "Stochastic", PANE,
            (Param("k", "%K length", 14, 2, 100), Param("smooth", "%K smoothing", 3, 1, 20), Param("d", "%D length", 3, 1, 20)),
            _stoch, levels=(20.0, 80.0), y_range=(0.0, 100.0),
        ),
        IndicatorSpec("adx", "ADX with directional lines", PANE, (_length(14, 2, 100),), _adx, levels=(25.0,)),
        IndicatorSpec("atr", "ATR", PANE, (_length(14, 2, 100),), _atr),
        IndicatorSpec("obv", "On-balance volume", PANE, (), _obv),
        IndicatorSpec("cci", "CCI", PANE, (_length(20, 5, 100),), _cci, levels=(-100.0, 100.0)),
        IndicatorSpec("mfi", "Money flow index", PANE, (_length(14, 2, 100),), _mfi, levels=(20.0, 80.0), y_range=(0.0, 100.0)),
        IndicatorSpec("willr", "Williams %R", PANE, (_length(14, 2, 100),), _willr, levels=(-80.0, -20.0), y_range=(-100.0, 0.0)),
        IndicatorSpec("roc", "Rate of change (%)", PANE, (_length(10, 1, 100),), _roc, levels=(0.0,)),
    )
}


def default_params(key: str) -> dict[str, float]:
    return {param.name: param.default for param in REGISTRY[key].params}


def next_id(selection: Sequence[Selected], key: str) -> str:
    taken = {item.id for item in selection}
    n = 1
    while f"{key}-{n}" in taken:
        n += 1
    return f"{key}-{n}"


def default_selection() -> list[Selected]:
    """What the dashboard has always drawn: SMA 50 and 200, Bollinger Bands (20, 2) and RSI 14."""
    selection: list[Selected] = []
    for key, params in (("sma", {"length": 50}), ("sma", {"length": 200}), ("bbands", {}), ("rsi", {})):
        selection.append(Selected(next_id(selection, key), key, {**default_params(key), **params}))
    return selection


def problem(item: Selected) -> str | None:
    """Why this choice cannot be drawn, or None when it can."""
    spec = REGISTRY.get(item.key)
    if spec is None:
        return f"unknown indicator {item.key!r}"
    unexpected = set(item.params) - {param.name for param in spec.params}
    if unexpected:
        return f"{spec.label} has no setting called {sorted(unexpected)[0]!r}"
    for param in spec.params:
        value = item.params.get(param.name, param.default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return f"{param.label} must be a number"
        if param.integer and value != int(value):
            return f"{param.label} must be a whole number"
        if not param.minimum <= value <= param.maximum:
            return f"{param.label} must be between {param.minimum:g} and {param.maximum:g}"
    return spec.check({param.name: item.params.get(param.name, param.default) for param in spec.params})


def validate(selection: Sequence[Selected]) -> tuple[list[Selected], dict[str, str]]:
    """The choices that can be drawn, and a reason for each that cannot (keyed by id). More than `MAX_INDICATORS`
    or a repeated id counts against the later ones."""
    valid: list[Selected] = []
    problems: dict[str, str] = {}
    seen: set[str] = set()
    for item in selection:
        reason = problem(item)
        if reason is None and item.id in seen:
            reason = "this id is already used"
        if reason is None and len(valid) >= MAX_INDICATORS:
            reason = f"at most {MAX_INDICATORS} indicators can be shown"
        seen.add(item.id)
        if reason:
            problems[item.id] = reason
        else:
            valid.append(item)
    return valid, problems


def arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray]:
    return {
        "open": np.array([c.open for c in candles], dtype=float),
        "high": np.array([c.high for c in candles], dtype=float),
        "low": np.array([c.low for c in candles], dtype=float),
        "close": np.array([c.close for c in candles], dtype=float),
        "volume": np.array([c.volume for c in candles], dtype=float),
    }


def compute(candles: Sequence[Candle], item: Selected) -> list[Line]:
    """The lines for one valid choice. Raises ValueError for one that `problem` rejects."""
    reason = problem(item)
    if reason:
        raise ValueError(reason)
    spec = REGISTRY[item.key]
    params = {param.name: item.params.get(param.name, param.default) for param in spec.params}
    return spec.compute(arrays(candles), params)


def short_history(candles: Sequence[Candle], selection: Sequence[Selected]) -> list[str]:
    """Names of the lines that have no value at all because the history is too short for their settings."""
    empty = []
    for item in selection:
        for line in compute(candles, item):
            if all(value is None for value in line.values):
                empty.append(line.name)
    return empty
