from __future__ import annotations

from athena.strategies.model import (
    LINE_NAMES,
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
from athena.technicals.indicators import REGISTRY

INDICATOR_NAMES = {
    "sma": "simple moving average", "ema": "exponential moving average", "wma": "weighted moving average",
    "bbands": "Bollinger Bands", "sar": "Parabolic SAR", "channel": "high-low channel", "rsi": "RSI", "macd": "MACD",
    "stoch": "Stochastic", "adx": "ADX", "atr": "ATR", "obv": "on-balance volume", "cci": "CCI", "mfi": "money flow index",
    "willr": "Williams %R", "roc": "rate of change",
}
OPERATORS = {
    "gt": "is above", "ge": "is at or above", "lt": "is below", "le": "is at or below",
    "crosses_above": "crosses above", "crosses_below": "crosses below",
}
ROLLING_WORDS = {"max": "highest", "min": "lowest", "mean": "average"}
ARITH_SYMBOLS = {"add": "+", "sub": "-", "mul": "x", "div": "/"}


def _bars(count: int) -> str:
    return f"{count} bar{'s' if count != 1 else ''}"


def _ago(shift: int) -> str:
    return f" {_bars(shift)} ago" if shift else ""


def _expr(expr: Expr, article: bool = True) -> str:
    the = "the " if article else ""
    if isinstance(expr, Const):
        return f"{expr.value:g}"
    if isinstance(expr, Price):
        return f"{the}{expr.field}{_ago(expr.shift)}"
    if isinstance(expr, IndicatorRef):
        values = dict(expr.params)
        settings = ", ".join(f"{param.name} {values[param.name]:g}" for param in REGISTRY[expr.key].params)
        names = LINE_NAMES[expr.key]
        line = f" {names[expr.line]} line" if len(names) > 1 else ""
        return f"{the}{INDICATOR_NAMES[expr.key]}{f' ({settings})' if settings else ''}{line}{_ago(expr.shift)}"
    if isinstance(expr, Rolling):
        tail = f", as of {_bars(expr.shift)} ago" if expr.shift else ""
        return f"{the}{ROLLING_WORDS[expr.fn]} {_expr(expr.of, False)} over the last {_bars(expr.window)}{tail}"
    return f"({_expr(expr.left)} {ARITH_SYMBOLS[expr.op]} {_expr(expr.right)}){_ago(expr.shift)}"


def _cond(cond: Cond, nested: bool = False) -> str:
    if isinstance(cond, Compare):
        return f"{_expr(cond.left)} {OPERATORS[cond.op]} {_expr(cond.right)}"
    if isinstance(cond, Not):
        return f"it is not true that {_cond(cond.item, True)}"
    joiner = " and " if isinstance(cond, AllOf) else " or "
    parts = [_cond(item, True) for item in cond.items]
    text = joiner.join(parts)
    return f"({text})" if nested and len(parts) > 1 else text


def describe(strategy: Strategy) -> str:
    """The strategy in plain English, for the person to read and confirm before it runs."""
    stop = (
        f"Stop out if the price falls {strategy.stop_atr:g} x ATR below the entry fill." if strategy.stop_atr is not None
        else "No protective stop."
    )
    return f"Buy when {_cond(strategy.entry)}. Sell when {_cond(strategy.exit)}. {stop}"
