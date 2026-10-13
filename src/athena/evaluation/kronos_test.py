"""Test Kronos (a pretrained candlestick forecaster) on Indian stocks, beside a plain momentum ranking.

    python -m athena.evaluation.kronos_test export --out .athena/kronos/inputs.jsonl     # bars up to each date, no later bar
    (run scripts/kronos_forecast.py in the Kronos environment: it writes .athena/kronos/forecasts.jsonl)
    python -m athena.evaluation.kronos_test score --name wh_kronos                       # rank, score and print the report

Kronos forecasts a stock, not its excess over the index, so on each date the stocks are ranked by predicted return (top third bullish,
bottom third bearish, the rest neutral) and scored against the same outcome labels as the 2e test. See
docs/superpowers/specs/2026-10-13-phase-2i-kronos-validation-design.md."""
from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError, Bar
from athena.evaluation.pit_cases import month_ends
from athena.evaluation.pit_market import Market, load, rule_trend
from athena.evaluation.pit_run import RUN_DIR, read_rows, report
from athena.price_integrity.history import CheckedHistory
from athena.trading_calendar import ist_date
from athena.warehouse.packets import BENCHMARK
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

KRONOS_DIR = Path(".athena") / "kronos"
STOCKS_FILE = KRONOS_DIR / "stocks.json"  # the stocks the export used: the draw depends on the latest day loaded, so it is pinned
HORIZONS = (63, 126)  # trading days; 252 is not forecast
LOOKBACK = 400
MIN_LOOKBACK = 200


def adjusted_bars(market: Market) -> dict[str, list[Bar]]:
    history = CheckedHistory(lambda symbol: list(market.raw[symbol]), lambda symbol: (), lambda symbol: market.actions.get(symbol, []))
    return {symbol: history(symbol) for symbol in market.raw}


def bars_up_to(bars: Sequence[Bar], day: date) -> list[Bar]:
    return [bar for bar in bars if ist_date(bar.timestamp) <= day]


def export_row(bars_by_symbol: Mapping[str, Sequence[Bar]], day: date, lookback: int = LOOKBACK) -> dict[str, Any] | None:
    """One date's inputs: the last `lookback` adjusted bars up to `day` for every stock, all the same length (the shortest history,
    at most `lookback`), as [YYYY-MM-DD, open, high, low, close, volume]. None when a stock has too little history."""
    cut = {symbol: bars_up_to(bars, day) for symbol, bars in bars_by_symbol.items()}
    length = min([lookback] + [len(bars) for bars in cut.values()])
    if length < MIN_LOOKBACK:
        return None
    stocks = {
        symbol: [[ist_date(b.timestamp).isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in bars[-length:]]
        for symbol, bars in cut.items()
    }
    return {"as_of": day.isoformat(), "lookback": length, "stocks": stocks}


def thirds(values: Mapping[str, float]) -> dict[str, str]:
    """Top third bullish, bottom third bearish, the rest neutral (ties by name, so the result never depends on dict order)."""
    order = sorted(values, key=lambda s: (-values[s], s))
    third = len(order) // 3
    return {s: "bullish" if i < third else "bearish" if i >= len(order) - third else "neutral" for i, s in enumerate(order)}


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out

    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def past_return(bars: Sequence[Bar], day: date, trading_days: int) -> float | None:
    seen = bars_up_to(bars, day)
    if len(seen) <= trading_days:
        return None
    return seen[-1].close / seen[-1 - trading_days].close - 1


def score_rows(
    forecasts: Mapping[tuple[str, str], Mapping[str, float]],
    outcomes: Mapping[tuple[str, str, int], tuple[str, float]],
    momentum: Mapping[tuple[str, str, int], float],
) -> tuple[list[dict[str, Any]], dict[str, dict[int, list[float]]]]:
    """Log rows in the 2e format for `kronos` (ranked by predicted return) and `rank_momentum` (ranked by the stock's own past return
    over the same number of days), and each date's rank correlation with the realised excess return, by analyst and horizon."""
    rows: list[dict[str, Any]] = []
    ics: dict[str, dict[int, list[float]]] = {"kronos": {h: [] for h in HORIZONS}, "rank_momentum": {h: [] for h in HORIZONS}}
    days = sorted({day for _, day in forecasts})
    for day in days:
        symbols = sorted(s for s, d in forecasts if d == day)
        for horizon in HORIZONS:
            known = [s for s in symbols if (s, day, horizon) in outcomes]
            for name, value in (
                ("kronos", {s: forecasts[(s, day)].get(f"ret{horizon}") for s in known}),
                ("rank_momentum", {s: momentum.get((s, day, horizon)) for s in known}),
            ):
                usable = {s: v for s, v in value.items() if v is not None}
                if len(usable) < 6:
                    continue
                calls = thirds(usable)
                ic = spearman([usable[s] for s in usable], [outcomes[(s, day, horizon)][1] for s in usable])
                if ic is not None:
                    ics[name][horizon].append(ic)
                for s, signal in calls.items():
                    label, excess = outcomes[(s, day, horizon)]
                    rows.append({
                        "symbol": s, "as_of": day, "specialist": name, "run": 0, "blinded": False, "source": "warehouse", "signal": signal,
                        "confidence": 0, "coverage": "full", "error": "", "notes": [], "horizon": horizon, "label": label, "excess": round(excess, 4),
                    })
    return rows, ics


def ic_summary(ics: Mapping[str, Mapping[int, Sequence[float]]]) -> str:
    lines = ["Rank correlation between the ranking and the realised excess return, per date:"]
    for name, per in ics.items():
        for horizon, values in per.items():
            if values:
                positive = sum(1 for v in values if v > 0)
                lines.append(f"  {name}, {horizon}-day: mean {sum(values) / len(values):+.3f} over {len(values)} dates, positive on {positive}")
    return "\n".join(lines)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_rows(path)


def market_for(path: Path, symbols: Sequence[str]) -> Market:
    with Warehouse(path, read_only=True, wait_seconds=300) as warehouse:
        return Market({s: warehouse.bars(s) for s in symbols}, {s: warehouse.actions(s) for s in symbols}, warehouse.index_close(BENCHMARK))


def export(db: Path, first: date, last: date, out: Path, top: int, mid: int) -> int:
    symbols, market = load(db, first, top, mid)
    out.parent.mkdir(parents=True, exist_ok=True)
    STOCKS_FILE.write_text(json.dumps({"symbols": symbols, "chosen_on": date.today().isoformat()}), encoding="utf-8")
    bars = adjusted_bars(market)
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8") as handle:
        for day in month_ends(first, last):
            row = export_row(bars, day)
            if row is None:
                continue
            handle.write(json.dumps(row) + "\n")
            count += 1
    print(f"{len(symbols)} stocks, {count} dates written to {out}")
    return 0


def score(db: Path, first: date, last: date, forecasts_path: Path, name: str, top: int, mid: int) -> int:
    market = market_for(db, json.loads(STOCKS_FILE.read_text(encoding="utf-8"))["symbols"])
    builder, bars = market.builder(), adjusted_bars(market)
    forecasts = {(r["symbol"], r["as_of"]): r for r in read_jsonl(forecasts_path)}
    if not forecasts:
        raise AthenaError(f"no forecasts in {forecasts_path}")
    outcomes: dict[tuple[str, str, int], tuple[str, float]] = {}
    momentum: dict[tuple[str, str, int], float] = {}
    rule: list[dict[str, Any]] = []
    for symbol, day_text in sorted(forecasts):
        day = date.fromisoformat(day_text)
        try:
            case = builder.build(symbol, day, False)
        except AthenaError:
            continue
        signal = rule_trend(case.packets["quant_technical"])["signal"]
        for horizon in HORIZONS:
            outcome = case.outcomes.get(horizon)
            if outcome is not None:
                outcomes[(symbol, day_text, horizon)] = (outcome.label, outcome.excess)
                rule.append({
                    "symbol": symbol, "as_of": day_text, "specialist": "rule_trend", "run": 0, "blinded": False, "source": "warehouse", "signal": signal,
                    "confidence": 0, "coverage": "full", "error": "", "notes": [], "horizon": horizon, "label": outcome.label, "excess": round(outcome.excess, 4),
                })
            back = past_return(bars[symbol], day, horizon)
            if back is not None:
                momentum[(symbol, day_text, horizon)] = back
    rows, ics = score_rows(forecasts, outcomes, momentum)
    rows += rule
    path = RUN_DIR / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    print(report(read_rows(path), ["kronos", "rank_momentum", "rule_trend"]))
    print()
    print(ic_summary(ics))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.evaluation.kronos_test", description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["export", "score"])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--from", dest="start", default="2024-01-31")
    parser.add_argument("--to", dest="end", default="2026-07-31")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--mid", type=int, default=20)
    parser.add_argument("--out", default=str(KRONOS_DIR / "inputs.jsonl"))
    parser.add_argument("--forecasts", default=str(KRONOS_DIR / "forecasts.jsonl"))
    parser.add_argument("--name", default="wh_kronos")
    args = parser.parse_args(argv)
    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if args.action == "export":
        return export(Path(args.db), first, last, Path(args.out), args.top, args.mid)
    return score(Path(args.db), first, last, Path(args.forecasts), args.name, args.top, args.mid)


if __name__ == "__main__":
    raise SystemExit(main())
