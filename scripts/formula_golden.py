"""Write the golden file the page's formula tests check against, from the real strategy engine.

    python scripts/formula_golden.py            # rewrite terminal/web/test/fixtures/formula-golden.json
    python scripts/formula_golden.py --check    # fail if the committed file differs from a fresh run

The file holds fixed bars, the indicator catalogue as the Python registry states it, and a list of cases: the formula text, the tree it must compile to, and what
the engine (`athena.strategies.compile`, TA-Lib underneath) computes for that tree on the bars. The page's tests require that the text compiles to the tree and
that the page's own evaluator gives the engine's numbers, so a formula, a chart line and a backtest cannot quietly disagree."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from athena.strategies.compile import _Context, _cond, _expr  # noqa: E402
from athena.strategies.parse import _Parser  # noqa: E402
from athena.technicals.candles import Candle  # noqa: E402
from athena.technicals.indicators import REGISTRY  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "terminal" / "web" / "test" / "fixtures" / "formula-golden.json"
BARS = 420


def bars() -> list[dict]:
    """A fixed random walk with a trend, some gaps and a volume that moves with the day's range: enough to make every indicator do something."""
    rng = np.random.RandomState(20261018)
    price, out, day = 500.0, [], date(2024, 1, 1)
    for i in range(BARS):
        drift = 0.0006 + 0.012 * math.sin(i / 37.0) * 0.1
        change = drift + rng.normal(0, 0.014)
        o = price * (1 + rng.normal(0, 0.003))
        c = price * (1 + change)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.006)))
        low = min(o, c) * (1 - abs(rng.normal(0, 0.006)))
        v = int(1_000_000 * (1 + 8 * abs(h - low) / c) * (0.6 + rng.random()))
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({"day": day.isoformat(), "o": round(o, 2), "h": round(h, 2), "l": round(low, 2), "c": round(c, 2), "v": v})
        price, day = c, day + timedelta(days=1)
    return out


# ---- building trees the way the page's compiler writes them (complete settings, the line named)
def P(field, shift=0):
    return {"price": field, **({"shift": shift} if shift else {})}


def K(value):
    return {"const": value}


def I(key, line=None, shift=0, **params):
    spec = REGISTRY[key]
    from athena.strategies.model import LINE_NAMES

    full = {p.name: params.get(p.name, p.default) for p in spec.params}
    full = {k: (int(v) if float(v).is_integer() and spec.params[[p.name for p in spec.params].index(k)].integer else v) for k, v in full.items()}
    return {"ind": key, "params": full, "line": line or LINE_NAMES[key][0], **({"shift": shift} if shift else {})}


def A(op, left, right, shift=0):
    return {"arith": op, "left": left, "right": right, **({"shift": shift} if shift else {})}


def R(fn, of, window, shift=0):
    return {"rolling": fn, "of": of, "window": window, **({"shift": shift} if shift else {})}


def C(op, left, right):
    return {"op": op, "left": left, "right": right}


EXPRS = [
    ("close", "close", P("close")),
    ("shifted close", "close[3]", P("close", 3)),
    ("volume", "volume", P("volume")),
    ("a constant", "2.5", K(2.5)),
    ("typical price", "(high + low + close) / 3", A("div", A("add", A("add", P("high"), P("low")), P("close")), K(3))),
    ("negative close", "-close", A("sub", K(0), P("close"))),
    ("a divide that can be zero", "(close - close[1]) / (volume - volume)", A("div", A("sub", P("close"), P("close", 1)), A("sub", P("volume"), P("volume")))),
    ("highest close, a day back", "highest(close, 20)[1]", R("max", P("close"), 20, 1)),
    ("lowest low", "lowest(low, 10)", R("min", P("low"), 10)),
    ("average close", "average(close, 5)", R("mean", P("close"), 5)),
    ("average of a moving average", "average(sma(10), 5)", R("mean", I("sma", length=10), 5)),
    ("sma", "sma(20)", I("sma", length=20)),
    ("sma default", "sma()", I("sma")),
    ("ema", "ema(12)", I("ema", length=12)),
    ("wma", "wma(10)", I("wma", length=10)),
    ("bollinger upper", "bbands(20, 2).upper", I("bbands", "upper", length=20, width=2)),
    ("bollinger lower wide", "bbands(20, 2.5).lower", I("bbands", "lower", length=20, width=2.5)),
    ("parabolic sar", "sar()", I("sar")),
    ("channel high", "channel(50).high", I("channel", "high", length=50)),
    ("channel low", "channel(50).low", I("channel", "low", length=50)),
    ("rsi", "rsi(14)", I("rsi", length=14)),
    ("macd line", "macd().macd", I("macd", "macd")),
    ("macd signal", "macd(12, 26, 9).signal", I("macd", "signal")),
    ("macd histogram", "macd().histogram", I("macd", "histogram")),
    ("stochastic k", "stoch().k", I("stoch", "k")),
    ("stochastic d", "stoch(14, 3, 3).d", I("stoch", "d")),
    ("adx", "adx(14).adx", I("adx", "adx", length=14)),
    ("plus di", "adx().plus_di", I("adx", "plus_di")),
    ("minus di", "adx().minus_di", I("adx", "minus_di")),
    ("atr", "atr(14)", I("atr", length=14)),
    ("obv", "obv()", I("obv")),
    ("cci", "cci(20)", I("cci", length=20)),
    ("mfi", "mfi(14)", I("mfi", length=14)),
    ("williams r", "willr(14)", I("willr", length=14)),
    ("roc", "roc(10)", I("roc", length=10)),
    ("a difference of averages", "sma(20) - sma(50)", A("sub", I("sma", length=20), I("sma", length=50))),
    ("a shifted indicator", "rsi(14)[2]", I("rsi", length=14, shift=2)),
    ("a shifted sum", "(close + open)[2]", A("add", P("close"), P("open"), 2)),
    ("atr as a share of price", "atr(14) / close * 100", A("mul", A("div", I("atr", length=14), P("close")), K(100))),
]

CONDS = [
    ("close above its average", "close > sma(50)", C("gt", P("close"), I("sma", length=50))),
    ("at least", "close >= highest(close, 20)[1]", C("ge", P("close"), R("max", P("close"), 20, 1))),
    ("below", "rsi(14) < 30", C("lt", I("rsi", length=14), K(30))),
    ("at most", "volume <= average(volume, 20)", C("le", P("volume"), R("mean", P("volume"), 20))),
    ("crosses above", "ema(12) crosses above ema(26)", C("crosses_above", I("ema", length=12), I("ema", length=26))),
    ("crossover", "crossover(close, sma(20))", C("crosses_above", P("close"), I("sma", length=20))),
    ("crosses below", "close crosses below sma(50)", C("crosses_below", P("close"), I("sma", length=50))),
    ("crossunder", "crossunder(macd().macd, macd().signal)", C("crosses_below", I("macd", "macd"), I("macd", "signal"))),
    ("and", "close > sma(200) and rsi(14) < 70", {"all": [C("gt", P("close"), I("sma", length=200)), C("lt", I("rsi", length=14), K(70))]}),
    ("or", "rsi(14) < 30 or rsi(14) > 70", {"any": [C("lt", I("rsi", length=14), K(30)), C("gt", I("rsi", length=14), K(70))]}),
    ("not", "not close > sma(20)", {"not": C("gt", P("close"), I("sma", length=20))}),
    ("not of an unknown stays unknown", "not close > sma(300)", {"not": C("gt", P("close"), I("sma", length=300))}),
    ("a chain of three", "close > open and close > sma(10) and volume > average(volume, 10)", {"all": [C("gt", P("close"), P("open")), C("gt", P("close"), I("sma", length=10)), C("gt", P("volume"), R("mean", P("volume"), 10))]}),
    ("or over and", "rsi(14) < 30 or close > sma(20) and adx(14).adx > 25", {"any": [C("lt", I("rsi", length=14), K(30)), {"all": [C("gt", P("close"), I("sma", length=20)), C("gt", I("adx", "adx", length=14), K(25))]}]}),
    ("a condition on a divide that can be zero", "(close - close[1]) / (volume - volume) > 0", C("gt", A("div", A("sub", P("close"), P("close", 1)), A("sub", P("volume"), P("volume"))), K(0))),
    ("bollinger rebound", "close crosses above bbands(20, 2).lower", C("crosses_above", P("close"), I("bbands", "lower", length=20, width=2))),
    ("breakout", "close >= highest(close, 252)[1]", C("ge", P("close"), R("max", P("close"), 252, 1))),
]


def number(x: float) -> float | None:
    return None if not math.isfinite(x) else round(float(x), 9)


def build() -> dict:
    data = bars()
    candles = [Candle(date.fromisoformat(b["day"]), b["o"], b["h"], b["l"], b["c"], float(b["v"])) for b in data]
    ctx = _Context(candles)
    cases = []
    for name, text, tree in EXPRS:
        values = _expr(_Parser().expression(tree, "formula", 1), ctx)
        cases.append({"name": name, "text": text, "kind": "expr", "tree": tree, "values": [number(v) for v in values]})
    for name, text, tree in CONDS:
        value, known = _cond(_Parser().condition(tree, "formula", 1), ctx)
        cases.append({"name": name, "text": text, "kind": "cond", "tree": tree, "value": [bool(v) for v in value], "known": [bool(k) for k in known]})
    catalogue = [
        {
            "key": spec.key, "placement": spec.placement,
            "params": [{"name": p.name, "default": p.default, "min": p.minimum, "max": p.maximum, "integer": p.integer} for p in spec.params],
            "lines": list(__import__("athena.strategies.model", fromlist=["LINE_NAMES"]).LINE_NAMES[spec.key]),
        }
        for spec in REGISTRY.values()
    ]
    return {"bars": data, "catalogue": catalogue, "cases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed file differs from a fresh run")
    args = parser.parse_args()
    fresh = build()
    text = json.dumps(fresh, indent=0, separators=(",", ":"))
    if args.check:
        same = OUT.exists() and json.loads(OUT.read_text(encoding="utf-8")) == json.loads(text)
        print("golden file is current" if same else "golden file is out of date: run python scripts/formula_golden.py")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(fresh['cases'])} cases, {BARS} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
