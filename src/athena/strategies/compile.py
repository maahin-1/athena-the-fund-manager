from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import talib

from athena.backtest.rules import ENTRY, EXIT, SeriesRule
from athena.strategies.describe import describe
from athena.strategies.model import (
    AllOf,
    Compare,
    Cond,
    Const,
    Expr,
    IndicatorRef,
    Not,
    Price,
    Rolling,
    Strategy,
)
from athena.technicals.candles import Candle
from athena.technicals.indicators import Selected, arrays, compute

ATR_LENGTH = 14  # the length that sizes a protective stop, as in the technical packet


class CompiledStrategy:
    """Entry and exit signals for every bar, computed once. The value at bar t uses only bars up to t."""

    def __init__(self, enter: np.ndarray, leave: np.ndarray, atr: np.ndarray) -> None:
        self._enter, self._leave, self._atr = enter, leave, atr

    def decide(self, index: int, holding: bool) -> tuple[str | None, float | None]:
        wants = self._leave[index] if holding else self._enter[index]
        atr = self._atr[index]
        return (EXIT if holding else ENTRY) if wants else None, (None if np.isnan(atr) else float(atr))


def _shift(values: np.ndarray, bars: int) -> np.ndarray:
    if bars == 0:
        return values
    out = np.full(len(values), np.nan)
    if bars < len(values):
        out[bars:] = values[:-bars]
    return out


def _rolling(values: np.ndarray, fn: str, window: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if window <= len(values):
        view = np.lib.stride_tricks.sliding_window_view(values, window)
        out[window - 1 :] = {"max": np.max, "min": np.min, "mean": np.mean}[fn](view, axis=1)  # NaN in a window gives NaN
    return out


class _Context:
    def __init__(self, candles: Sequence[Candle]) -> None:
        self.size = len(candles)
        self.candles = candles
        self.series = arrays(candles)
        self._indicators: dict[tuple, list[np.ndarray]] = {}

    def indicator(self, ref: IndicatorRef) -> np.ndarray:
        key = (ref.key, ref.params)
        if key not in self._indicators:
            lines = compute(self.candles, Selected("s-1", ref.key, dict(ref.params)))
            self._indicators[key] = [np.array([np.nan if v is None else v for v in line.values]) for line in lines]
        return self._indicators[key][ref.line]


def _expr(expr: Expr, ctx: _Context) -> np.ndarray:
    if isinstance(expr, Const):
        return np.full(ctx.size, expr.value)
    if isinstance(expr, Price):
        return _shift(ctx.series[expr.field], expr.shift)
    if isinstance(expr, IndicatorRef):
        return _shift(ctx.indicator(expr), expr.shift)
    if isinstance(expr, Rolling):
        return _shift(_rolling(_expr(expr.of, ctx), expr.fn, expr.window), expr.shift)
    left, right = _expr(expr.left, ctx), _expr(expr.right, ctx)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = {"add": left + right, "sub": left - right, "mul": left * right, "div": left / right}[expr.op]
    result[~np.isfinite(result)] = np.nan  # a division by a zero value is unknown, not infinite
    return _shift(result, expr.shift)


def _cond(cond: Cond, ctx: _Context) -> tuple[np.ndarray, np.ndarray]:
    """(value, known): three-valued logic, so a bar where something is not yet computable is never a signal, and
    `not` of an unknown stays unknown instead of becoming true."""
    if isinstance(cond, Compare):
        left, right = _expr(cond.left, ctx), _expr(cond.right, ctx)
        known = np.isfinite(left) & np.isfinite(right)
        with np.errstate(invalid="ignore"):
            if cond.op in ("crosses_above", "crosses_below"):
                prev_left, prev_right = _shift(left, 1), _shift(right, 1)
                known = known & np.isfinite(prev_left) & np.isfinite(prev_right)
                value = (left > right) & (prev_left <= prev_right) if cond.op == "crosses_above" else (left < right) & (prev_left >= prev_right)
            else:
                value = {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[cond.op]
        return value & known, known
    if isinstance(cond, Not):
        value, known = _cond(cond.item, ctx)
        return known & ~value, known
    parts = [_cond(item, ctx) for item in cond.items]
    values = np.array([value for value, _ in parts])
    knowns = np.array([known for _, known in parts])
    if isinstance(cond, AllOf):
        value = values.all(axis=0)
        known = value | (knowns & ~values).any(axis=0)  # all true, or one known false
    else:
        value = values.any(axis=0)
        known = value | (knowns & ~values).all(axis=0)  # one true, or all known false
    return value, known


def compile_strategy(strategy: Strategy, candles: Sequence[Candle]) -> CompiledStrategy:
    ctx = _Context(candles)
    enter, _ = _cond(strategy.entry, ctx)
    leave, _ = _cond(strategy.exit, ctx)
    atr = talib.ATR(ctx.series["high"], ctx.series["low"], ctx.series["close"], ATR_LENGTH) if ctx.size else np.array([])
    return CompiledStrategy(enter, leave, atr)


def strategy_rule(strategy: Strategy) -> SeriesRule:
    """The strategy as a rule the backtest engine can run, described in plain English."""
    return SeriesRule(strategy.name, describe(strategy), lambda candles: compile_strategy(strategy, candles), strategy.stop_atr)
