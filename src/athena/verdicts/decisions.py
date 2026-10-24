"""Typed decisions, and how they turned out.

    python -m athena.verdicts.decisions            # settle every stored verdict that is old enough, against the NIFTY 50, and print how each kind of decision did

A verdict is a rating (Buy, Overweight, Hold, Underweight, Sell) with a conviction. A *decision* is what a person should do with it: BUY, SELL, HOLD, or REVIEW when the verdict should not be
acted on without a person looking (the analysts disagree, fewer than two formed a view, or something held it back). The rules are written here, in code; no model decides them.

Settling sets each stored verdict against what happened: the stock's return from the verdict's price day over the next 5, 20 and 60 trading days, less the NIFTY 50's over the same days. A
verdict is settled only once that many trading days exist after it, and the figures come from the split-adjusted prices the warehouse holds, so a split in between does not read as a loss."""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

BUY, SELL, HOLD, REVIEW = "BUY", "SELL", "HOLD", "REVIEW"
RATING_DECISION = {"Buy": BUY, "Overweight": BUY, "Hold": HOLD, "Underweight": SELL, "Sell": SELL}
HORIZONS = (5, 20, 60)  # trading days
MIN_ANALYSTS = 2
BENCHMARK = "Nifty 50"
HEAD = "decision"


def decide(verdict: Mapping[str, Any], taking_part: Sequence[str], conflict: Mapping[str, Any] | None) -> dict[str, Any]:
    """The decision for a verdict: its type, the reason, and the horizons it will be settled at. REVIEW wins over the rating when the analysts disagree, fewer than two formed a view, or the
    verdict was held back (by price data, for example)."""
    rating = str(verdict["verdict"])
    if conflict and conflict.get("bullish") and conflict.get("bearish"):
        kind, why = REVIEW, f"analysts disagree (bullish: {', '.join(conflict['bullish'])}; bearish: {', '.join(conflict['bearish'])})"
    elif len(taking_part) < MIN_ANALYSTS:
        kind, why = REVIEW, f"only {len(taking_part)} analyst(s) formed a view; at least {MIN_ANALYSTS} are needed to act"
    elif verdict.get("held_back_by"):
        kind, why = REVIEW, f"held back by {verdict['held_back_by']}"
    else:
        kind, why = RATING_DECISION.get(rating, HOLD), f"{rating} from {len(taking_part)} analysts"
    return {"type": kind, "reason": why, "rating": rating, "conviction": verdict.get("conviction"), "settle_after_trading_days": list(HORIZONS)}


def _return(series: Sequence[tuple[date, float]], start: int, end: int) -> float | None:
    a, b = series[start][1], series[end][1]
    return b / a - 1.0 if a and a > 0 else None


def settle_one(
    made_for: date, stock: Sequence[tuple[date, float]], benchmark: Mapping[date, float], horizons: Sequence[int] = HORIZONS
) -> dict[int, dict[str, float]]:
    """For each horizon with enough trading days after `made_for`: the stock's return, the benchmark's and the excess. `stock` is the split-adjusted closes by day, oldest first. A horizon
    not reached yet is absent, never guessed."""
    days = [d for d, _ in stock]
    if made_for not in days:
        return {}
    i = days.index(made_for)
    bench_days = sorted(benchmark)
    out: dict[int, dict[str, float]] = {}
    for h in horizons:
        if i + h >= len(stock):
            continue
        end = days[i + h]
        start_b = [d for d in bench_days if d <= made_for]
        end_b = [d for d in bench_days if d <= end]
        if not start_b or not end_b or end_b[-1] <= start_b[-1]:
            continue
        r = _return(stock, i, i + h)
        b0, b1 = benchmark[start_b[-1]], benchmark[end_b[-1]]
        if r is None or not b0 or b0 <= 0:
            continue
        out[h] = {"stock": r, "benchmark": b1 / b0 - 1.0, "excess": r - (b1 / b0 - 1.0)}
    return out


def summarize(settled: Sequence[Mapping[str, Any]], buy_cost: float = 0.0) -> dict[str, dict[str, dict[str, Any]]]:
    """By decision type and horizon: how many were settled, the average excess return, and for BUY and SELL the share that went the way they said (excess above or below zero). A BUY is
    also given its average excess return after `buy_cost`, the fraction of the value that buying and selling it costs (`athena.costs.model`); a SELL is an avoided loss and carries no cost."""
    groups: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in settled:
        for h, got in row["outcomes"].items():
            groups[(row["decision"]["type"], int(h))].append(got["excess"])
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for (kind, h), values in sorted(groups.items()):
        right = None
        if kind in (BUY, SELL):
            sign = 1 if kind == BUY else -1
            right = sum(1 for v in values if sign * v > 0) / len(values)
        got = {"n": len(values), "mean_excess": statistics.fmean(values), "went_the_way_said": right}
        if kind == BUY:
            got["mean_excess_after_costs"] = got["mean_excess"] - buy_cost
        out.setdefault(kind, {})[str(h)] = got
    return out


def settle_all(
    history: Mapping[str, Sequence[Mapping[str, Any]]], closes: Callable[[str], Sequence[tuple[date, float]]], benchmark: Mapping[date, float]
) -> list[dict[str, Any]]:
    """Every stored verdict with a decision, settled as far as the days allow. `history` is the verdicts by symbol (as `Warehouse.verdict_history` gives them)."""
    rows = []
    for symbol, verdicts in history.items():
        series = closes(symbol)
        for v in verdicts:
            payload = v["payload"]
            decision = payload.get(HEAD) or decide(payload["verdict"], payload.get("taking_part", []), payload.get("conflict"))
            rows.append({"symbol": symbol, "made_at": v["made_at"], "data_as_of": v["data_as_of"], "decision": decision, "outcomes": settle_one(v["data_as_of"], series, benchmark)})
    return rows


def format_summary(summary: Mapping[str, Mapping[str, Mapping[str, Any]]], total: int) -> str:
    lines = [f"{total} stored verdicts"]
    for kind, per in summary.items():
        for h, c in per.items():
            way = "" if c["went_the_way_said"] is None else f"; went the way said {c['went_the_way_said']:.0%}"
            way += f"; {c['mean_excess_after_costs']:+.1%} after costs" if "mean_excess_after_costs" in c else ""
            lines.append(f"{kind} after {h} trading days: {c['n']} settled, average excess return {c['mean_excess']:+.1%}{way}")
    if len(lines) == 1:
        lines.append("none old enough to settle yet")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    with Warehouse(args.db, read_only=True, wait_seconds=120) as warehouse:
        history = {symbol: warehouse.verdict_history(symbol) for symbol in warehouse.verdict_symbols()}
        benchmark = warehouse.index_close(BENCHMARK)
        adjusted = adjusted_history(warehouse)
        cache: dict[str, list[tuple[date, float]]] = {}

        def closes(symbol: str) -> list[tuple[date, float]]:
            if symbol not in cache:
                from athena.trading_calendar import ist_date

                cache[symbol] = [(ist_date(b.timestamp), b.close) for b in adjusted(symbol)]
            return cache[symbol]

        settled = settle_all(history, closes, benchmark)
    from athena.costs.model import load, round_trip

    cost = round_trip(load(), "delivery", 100000.0)
    print(format_summary(summarize(settled, cost["fraction"]), len(settled)))
    print(f"(costs: a delivery round trip's charges, {cost['fraction']:.2%} of the value, no slippage{'' if cost['verified'] else '; the rates are not yet checked against a contract note'})")
    for row in settled:
        got = ", ".join(f"{h}d {o['excess']:+.1%}" for h, o in sorted(row["outcomes"].items())) or "not settled yet"
        print(f"  {row['symbol']} {row['data_as_of']} {row['decision']['type']} ({row['decision']['rating']}): {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
