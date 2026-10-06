from __future__ import annotations

from dataclasses import dataclass

VERSION = 1
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
COMPARE_OPS = ("gt", "ge", "lt", "le", "crosses_above", "crosses_below")
ARITH_OPS = ("add", "sub", "mul", "div")
ROLLING_FNS = ("max", "min", "mean")
MAX_DEPTH = 8  # nesting of conditions and expressions
MAX_NODES = 60
MAX_CONDITIONS = 8  # inside one all / any
MAX_WINDOW = 1000
MAX_SHIFT = 500
MAX_STOP_ATR = 10.0

# The names of an indicator's lines, in the order the registry computes them, so a strategy can say which one it means.
LINE_NAMES: dict[str, tuple[str, ...]] = {
    "sma": ("value",), "ema": ("value",), "wma": ("value",), "bbands": ("upper", "lower"), "sar": ("value",),
    "channel": ("high", "low"), "rsi": ("value",), "macd": ("macd", "signal", "histogram"), "stoch": ("k", "d"),
    "adx": ("adx", "plus_di", "minus_di"), "atr": ("value",), "obv": ("value",), "cci": ("value",), "mfi": ("value",),
    "willr": ("value",), "roc": ("value",),
}


@dataclass(frozen=True)
class Price:
    field: str
    shift: int = 0


@dataclass(frozen=True)
class IndicatorRef:
    key: str
    params: tuple[tuple[str, float], ...]  # sorted by name, complete (defaults filled in)
    line: int = 0
    shift: int = 0


@dataclass(frozen=True)
class Const:
    value: float


@dataclass(frozen=True)
class Arith:
    op: str
    left: Expr
    right: Expr
    shift: int = 0


@dataclass(frozen=True)
class Rolling:
    fn: str
    of: Expr
    window: int
    shift: int = 0


Expr = Price | IndicatorRef | Const | Arith | Rolling


@dataclass(frozen=True)
class Compare:
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class AllOf:
    items: tuple[Cond, ...]


@dataclass(frozen=True)
class AnyOf:
    items: tuple[Cond, ...]


@dataclass(frozen=True)
class Not:
    item: Cond


Cond = Compare | AllOf | AnyOf | Not


@dataclass(frozen=True)
class Strategy:
    """A long-only strategy as data: when to buy, when to sell, and an optional protective stop. Nothing in it can run
    code; it is validated by `parse_strategy` and turned into signals by `compile_strategy`."""

    name: str
    entry: Cond
    exit: Cond
    stop_atr: float | None = None
