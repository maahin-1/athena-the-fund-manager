from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from athena.strategies.model import (
    ARITH_OPS,
    COMPARE_OPS,
    LINE_NAMES,
    MAX_CONDITIONS,
    MAX_DEPTH,
    MAX_NODES,
    MAX_SHIFT,
    MAX_STOP_ATR,
    MAX_WINDOW,
    PRICE_FIELDS,
    ROLLING_FNS,
    VERSION,
    AllOf,
    AnyOf,
    Arith,
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
from athena.technicals.indicators import REGISTRY, Selected, default_params, problem

NAME_LIMIT = 60
CONDITION_KINDS = ("all", "any", "not", "op")
EXPR_KEYS = {
    "price": {"price", "shift"},
    "ind": {"ind", "params", "line", "shift"},
    "rolling": {"rolling", "of", "window", "shift"},
    "const": {"const"},
    "arith": {"arith", "left", "right", "shift"},
}


class StrategyError(ValueError):
    """The strategy is not valid; the message names the part that is wrong."""


class _Parser:
    def __init__(self) -> None:
        self.nodes = 0

    def node(self, path: str, depth: int) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise StrategyError(f"{path}: the strategy has more than {MAX_NODES} parts")
        if depth > MAX_DEPTH:
            raise StrategyError(f"{path}: nested more than {MAX_DEPTH} levels deep")

    def number(self, value: Any, path: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise StrategyError(f"{path}: must be a number")
        return value

    def whole(self, value: Any, path: str, low: int, high: int) -> int:
        number = self.number(value, path)
        if number != int(number):
            raise StrategyError(f"{path}: must be a whole number")
        if not low <= number <= high:
            raise StrategyError(f"{path}: must be between {low} and {high}")
        return int(number)

    def mapping(self, value: Any, path: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise StrategyError(f"{path}: must be an object")
        return value

    def condition(self, data: Any, path: str, depth: int) -> Cond:
        data = self.mapping(data, path)
        self.node(path, depth)
        kinds = [kind for kind in CONDITION_KINDS if kind in data]
        if len(kinds) != 1:
            raise StrategyError(f"{path}: must have exactly one of {', '.join(CONDITION_KINDS)}")
        kind = kinds[0]
        allowed = {"op", "left", "right"} if kind == "op" else {kind}
        extra = sorted(set(data) - allowed)
        if extra:
            raise StrategyError(f"{path}: unexpected key {extra[0]!r}")
        if kind in ("all", "any"):
            items = data[kind]
            if not isinstance(items, list) or not 1 <= len(items) <= MAX_CONDITIONS:
                raise StrategyError(f"{path}.{kind}: must be a list of 1 to {MAX_CONDITIONS} conditions")
            parsed = tuple(self.condition(item, f"{path}.{kind}[{n}]", depth + 1) for n, item in enumerate(items))
            return AllOf(parsed) if kind == "all" else AnyOf(parsed)
        if kind == "not":
            return Not(self.condition(data["not"], f"{path}.not", depth + 1))
        op = data["op"]
        if op not in COMPARE_OPS:
            raise StrategyError(f"{path}.op: must be one of {', '.join(COMPARE_OPS)}")
        for side in ("left", "right"):
            if side not in data:
                raise StrategyError(f"{path}: missing {side!r}")
        return Compare(op, self.expression(data["left"], f"{path}.left", depth + 1), self.expression(data["right"], f"{path}.right", depth + 1))

    def expression(self, data: Any, path: str, depth: int) -> Expr:
        data = self.mapping(data, path)
        self.node(path, depth)
        kinds = [kind for kind in EXPR_KEYS if kind in data]
        if len(kinds) != 1:
            raise StrategyError(f"{path}: must have exactly one of {', '.join(EXPR_KEYS)}")
        kind = kinds[0]
        extra = sorted(set(data) - EXPR_KEYS[kind])
        if extra:
            raise StrategyError(f"{path}: unexpected key {extra[0]!r}")
        shift = self.whole(data["shift"], f"{path}.shift", 0, MAX_SHIFT) if "shift" in data else 0
        if kind == "const":
            return Const(float(self.number(data["const"], f"{path}.const")))
        if kind == "price":
            if data["price"] not in PRICE_FIELDS:
                raise StrategyError(f"{path}.price: must be one of {', '.join(PRICE_FIELDS)}")
            return Price(data["price"], shift)
        if kind == "ind":
            return self.indicator(data, path, shift)
        if kind == "rolling":
            if data["rolling"] not in ROLLING_FNS:
                raise StrategyError(f"{path}.rolling: must be one of {', '.join(ROLLING_FNS)}")
            if "of" not in data or "window" not in data:
                raise StrategyError(f"{path}: a rolling value needs 'of' and 'window'")
            window = self.whole(data["window"], f"{path}.window", 2, MAX_WINDOW)
            return Rolling(data["rolling"], self.expression(data["of"], f"{path}.of", depth + 1), window, shift)
        if data["arith"] not in ARITH_OPS:
            raise StrategyError(f"{path}.arith: must be one of {', '.join(ARITH_OPS)}")
        for side in ("left", "right"):
            if side not in data:
                raise StrategyError(f"{path}: missing {side!r}")
        left = self.expression(data["left"], f"{path}.left", depth + 1)
        right = self.expression(data["right"], f"{path}.right", depth + 1)
        if data["arith"] == "div" and isinstance(right, Const) and right.value == 0:
            raise StrategyError(f"{path}.right: cannot divide by zero")
        return Arith(data["arith"], left, right, shift)

    def indicator(self, data: Mapping, path: str, shift: int) -> IndicatorRef:
        key = data["ind"]
        if key not in REGISTRY:
            raise StrategyError(f"{path}.ind: unknown indicator {key!r}; choose from {', '.join(REGISTRY)}")
        params = self.mapping(data.get("params", {}), f"{path}.params")
        full = {**default_params(key), **params}
        reason = problem(Selected("s-1", key, full))
        if reason:
            raise StrategyError(f"{path}.params: {reason}")
        names = LINE_NAMES[key]
        line = data.get("line", 0)
        if isinstance(line, str):
            if line not in names:
                raise StrategyError(f"{path}.line: {key} has the lines {', '.join(names)}")
            line = names.index(line)
        else:
            line = self.whole(line, f"{path}.line", 0, len(names) - 1)
        return IndicatorRef(key, tuple(sorted(full.items())), line, shift)


def parse_strategy(data: Any) -> Strategy:
    """A strategy from plain data (for example JSON), or a `StrategyError` that names what is wrong."""
    parser = _Parser()
    top = parser.mapping(data, "strategy")
    extra = sorted(set(top) - {"version", "name", "entry", "exit", "stop_atr"})
    if extra:
        raise StrategyError(f"strategy: unexpected key {extra[0]!r}")
    if "version" in top and top["version"] != VERSION:
        raise StrategyError(f"strategy.version: only version {VERSION} is supported")
    name = top.get("name", "Custom strategy")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_LIMIT:
        raise StrategyError(f"strategy.name: must be text of 1 to {NAME_LIMIT} characters")
    for side in ("entry", "exit"):
        if side not in top:
            raise StrategyError(f"strategy: missing {side!r}")
    stop = top.get("stop_atr")
    if stop is not None:
        stop = float(parser.number(stop, "strategy.stop_atr"))
        if not 0 < stop <= MAX_STOP_ATR:
            raise StrategyError(f"strategy.stop_atr: must be above 0 and at most {MAX_STOP_ATR:g}")
    return Strategy(name.strip(), parser.condition(top["entry"], "entry", 1), parser.condition(top["exit"], "exit", 1), stop)


def _expr_dict(expr: Expr) -> dict:
    if isinstance(expr, Const):
        return {"const": expr.value}
    if isinstance(expr, Price):
        out: dict = {"price": expr.field}
    elif isinstance(expr, IndicatorRef):
        out = {"ind": expr.key, "params": dict(expr.params), "line": LINE_NAMES[expr.key][expr.line]}
    elif isinstance(expr, Rolling):
        out = {"rolling": expr.fn, "of": _expr_dict(expr.of), "window": expr.window}
    else:
        out = {"arith": expr.op, "left": _expr_dict(expr.left), "right": _expr_dict(expr.right)}
    if expr.shift:
        out["shift"] = expr.shift
    return out


def _cond_dict(cond: Cond) -> dict:
    if isinstance(cond, Compare):
        return {"op": cond.op, "left": _expr_dict(cond.left), "right": _expr_dict(cond.right)}
    if isinstance(cond, Not):
        return {"not": _cond_dict(cond.item)}
    return {"all" if isinstance(cond, AllOf) else "any": [_cond_dict(item) for item in cond.items]}


def to_dict(strategy: Strategy) -> dict:
    """Plain data that `parse_strategy` turns back into an equal strategy."""
    out = {"version": VERSION, "name": strategy.name, "entry": _cond_dict(strategy.entry), "exit": _cond_dict(strategy.exit)}
    if strategy.stop_atr is not None:
        out["stop_atr"] = strategy.stop_atr
    return out
