"""Typed decisions, and how they turned out.

    python -m athena.verdicts.decisions            # settle every stored verdict that is old enough, against the NIFTY 50, and print how each kind of decision did

Two statistics are printed apart and never pooled. The PROSPECTIVE settlement (`athena.timing.ENTRY_POLICY`) enters a decision at the open of the first session after the date it was completed
and needs the decision-completion time a verdict records; a verdict stored without one is `unverifiable`, not guessed. The older HISTORICAL ASSOCIATION returns from the close of the data date,
which can precede the verdict itself, and is kept only as the earlier reports computed it.

A verdict is a rating (Buy, Overweight, Hold, Underweight, Sell) with a conviction. A *decision* is what a person should do with it: BUY, SELL, HOLD, or REVIEW when the verdict should not be
acted on without a person looking (the analysts disagree, fewer than two formed a view, or something held it back). The rules are written here, in code; no model decides them.

Settling sets each stored verdict against what happened: the stock's return from the verdict's price day over the next 5, 20 and 60 trading days, less the NIFTY 50's over the same days. A
verdict is settled only once that many trading days exist after it, and the figures come from the split-adjusted prices the warehouse holds, so a split in between does not read as a loss."""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from athena.timing import ENTRY_POLICY, LEGACY_ASSOCIATION, SessionCalendar, decision_date

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


Bar = tuple[date, float | None, float | None]  # (session date, open, close), oldest first


def settle_prospective(
    decision_completed_at: datetime, stock: Sequence[Bar], benchmark: Mapping[date, tuple[float | None, float | None]], horizons: Sequence[int] = HORIZONS,
    calendar: SessionCalendar | None = None,
) -> dict[str, Any]:
    """Settle one decision under `timing.ENTRY_POLICY`: entered at the open of the first session strictly after the exchange-local date the decision was completed on, held for `h` sessions
    (the entry session is the first), and sold at the close of the last. The benchmark is measured over the same interval, from its own open on the entry session to its close on the exit session.

    The entry and exit dates come from `calendar` (`timing.SessionCalendar`: weekdays less recorded holidays, up to the last day examined), never from which prices happened to be downloaded, so
    deleting a price row cannot move them. Nothing is substituted: a stock or benchmark with no row, or no open, on the entry session, or no close on the exit session, gives that horizon a named
    `gap`; a horizon whose exit session the calendar has not reached is `pending`. Without a calendar nothing is settled (`unverifiable`). `stock` is the split-adjusted bars; `benchmark` maps
    session date -> (open, close)."""
    decided_on = decision_date(decision_completed_at)  # raises for a naive time: a decision without a time zone has no exchange date
    if calendar is None:
        return {**_unverifiable("no exchange-session calendar was supplied: the entry session cannot be fixed from the prices alone, because a missing price row would move it"), "decision_date": decided_on.isoformat()}
    entry = calendar.first_after(decided_on)
    out: dict[str, Any] = {"policy": ENTRY_POLICY, "decision_date": decided_on.isoformat(), "entry_session": entry.isoformat() if entry else None, "horizons": {}}
    if entry is None:
        out["status"] = "pending"
        return out
    bars = {day: (o, c) for day, o, c in stock}
    results: dict[int, dict[str, Any]] = {}
    for h in horizons:
        window = calendar.sessions_from(entry, h)
        if window is None:
            results[h] = {"status": "pending", "reason": f"the {h}th session from {entry.isoformat()} is not established yet (the calendar runs to {calendar.through.isoformat()})"}
            continue
        exit_day = window[-1]
        base = {"entry_date": entry.isoformat(), "exit_date": exit_day.isoformat()}
        stock_open, _ = bars.get(entry, (None, None))
        _, stock_close = bars.get(exit_day, (None, None))
        bench_open, _ = benchmark.get(entry, (None, None))
        _, bench_close = benchmark.get(exit_day, (None, None))
        if not stock_open or stock_open <= 0:
            what = "has no open price on" if entry in bars else "has no row for (an established session, not a holiday)"
            results[h] = {**base, "status": "gap", "reason": f"the stock {what} the entry session {entry.isoformat()}"}
        elif not stock_close or stock_close <= 0:
            results[h] = {**base, "status": "gap", "reason": f"the stock has no close price on the exit session {exit_day.isoformat()} (not trading?); no earlier close is substituted"}
        elif entry not in benchmark:
            results[h] = {**base, "status": "gap", "reason": f"the benchmark has no row for the entry session {entry.isoformat()} (an established session, not a holiday); the entry does not move to a later date"}
        elif not bench_open or bench_open <= 0:
            results[h] = {**base, "status": "gap", "reason": f"the benchmark open on {entry.isoformat()} is not held, so the benchmark cannot be measured over the same interval"}
        elif exit_day not in benchmark:
            results[h] = {**base, "status": "gap", "reason": f"the benchmark has no row for the exit session {exit_day.isoformat()} (an established session, not a holiday); the window is not extended"}
        elif not bench_close or bench_close <= 0:
            results[h] = {**base, "status": "gap", "reason": f"the benchmark close on {exit_day.isoformat()} is not held"}
        else:
            r, b = stock_close / stock_open - 1.0, bench_close / bench_open - 1.0
            results[h] = {**base, "status": "settled", "entry_price": stock_open, "exit_price": stock_close, "stock": r, "benchmark": b, "excess": r - b}
    out["horizons"] = results
    states = {r["status"] for r in results.values()}
    out["status"] = "settled" if states == {"settled"} else "pending" if states == {"pending"} else "gap" if states == {"gap"} else "partial"
    return out


def _unverifiable(reason: str) -> dict[str, Any]:
    return {"policy": ENTRY_POLICY, "status": "unverifiable", "reason": reason, "entry_session": None, "horizons": {}}


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
    history: Mapping[str, Sequence[Mapping[str, Any]]], closes: Callable[[str], Sequence[tuple[date, float]]], benchmark: Mapping[date, float],
    bars: Callable[[str], Sequence[Bar]] | None = None, bench_bars: Mapping[date, tuple[float | None, float | None]] | None = None, calendar: SessionCalendar | None = None,
) -> list[dict[str, Any]]:
    """Every stored verdict with a decision, settled as far as the days allow. `history` is the verdicts by symbol (as `Warehouse.verdict_history` gives them); nothing in it is changed.

    `outcomes` is the old statistic, kept as it was: the return from the close of the verdict's DATA date, a historical association (`timing.LEGACY_ASSOCIATION`) that can start before the verdict
    existed and is not an executable result. When `bars` and `bench_bars` are given, each row also has `settlement`: the prospective result under `timing.ENTRY_POLICY`, from the decision-completion
    time the verdict recorded (`payload["timing"]["decision_completed_at"]`). A verdict stored without that record is `unverifiable` there; it is not guessed from `made_at`. The exchange
    `calendar` (`timing.SessionCalendar`) fixes the entry and exit sessions independently of the prices; without one the settlement is `unverifiable`."""
    rows = []
    for symbol, verdicts in history.items():
        series = closes(symbol)
        held = bars(symbol) if bars is not None else None
        for v in verdicts:
            payload = v["payload"]
            decision = payload.get(HEAD) or decide(payload["verdict"], payload.get("taking_part", []), payload.get("conflict"))
            row = {"symbol": symbol, "made_at": v["made_at"], "data_as_of": v["data_as_of"], "decision": decision, "outcomes": settle_one(v["data_as_of"], series, benchmark)}
            if held is not None and bench_bars is not None:
                row["association"] = LEGACY_ASSOCIATION
                completed = (payload.get("timing") or {}).get("decision_completed_at")
                if not completed:
                    row["settlement"] = _unverifiable("no decision-completion record: the verdict was stored before timing/1, so its entry cannot be fixed without guessing")
                else:
                    try:
                        row["settlement"] = settle_prospective(datetime.fromisoformat(completed), held, bench_bars, calendar=calendar)
                    except ValueError as exc:
                        row["settlement"] = _unverifiable(f"the recorded decision-completion time cannot be used ({exc})")
            rows.append(row)
    return rows


def summarize_prospective(settled: Sequence[Mapping[str, Any]], buy_cost: float = 0.0) -> dict[str, Any]:
    """The prospective results by decision type and horizon, from the `settlement` blocks: only settled horizons enter the averages, and the rest is counted apart as pending, gap or
    unverifiable. Nothing here pools in the old data-date association."""
    groups: dict[tuple[str, int], list[float]] = defaultdict(list)
    coverage = {"settled": 0, "gap": 0, "pending": 0, "unverifiable": 0}
    for row in settled:
        block = row.get("settlement")
        if not block:
            continue
        if block.get("status") == "unverifiable":
            coverage["unverifiable"] += 1
            continue
        for h, got in block.get("horizons", {}).items():
            state = got.get("status")
            if state in coverage:
                coverage[state] += 1
            if state == "settled":
                groups[(row["decision"]["type"], int(h))].append(got["excess"])
    by_decision: dict[str, dict[str, dict[str, Any]]] = {}
    for (kind, h), values in sorted(groups.items()):
        right = None
        if kind in (BUY, SELL):
            sign = 1 if kind == BUY else -1
            right = sum(1 for v in values if sign * v > 0) / len(values)
        got = {"n": len(values), "mean_excess": statistics.fmean(values), "went_the_way_said": right}
        if kind == BUY:
            got["mean_excess_after_costs"] = got["mean_excess"] - buy_cost
        by_decision.setdefault(kind, {})[str(h)] = got
    return {"policy": ENTRY_POLICY, "by_decision": by_decision, "coverage": coverage}


def format_prospective(summary: Mapping[str, Any], total: int) -> str:
    cov = summary["coverage"]
    lines = [f"Prospective settlement, policy {summary['policy']} (entered at the next session's open after the decision; {total} stored verdicts)"]
    for kind, per in summary["by_decision"].items():
        for h, c in per.items():
            way = "" if c["went_the_way_said"] is None else f"; went the way said {c['went_the_way_said']:.0%}"
            way += f"; {c['mean_excess_after_costs']:+.1%} after costs" if "mean_excess_after_costs" in c else ""
            lines.append(f"{kind} for {h} trading days: {c['n']} settled, average excess return {c['mean_excess']:+.1%}{way}")
    lines.append(f"horizons: {cov['settled']} settled, {cov['pending']} pending, {cov['gap']} gap (a price the policy needs is missing), and {cov['unverifiable']} unverifiable verdicts (stored without a decision-completion record)")
    return "\n".join(lines)


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


def session_calendar(warehouse: Any) -> SessionCalendar | None:
    """The exchange's sessions from the warehouse's own day records, not from its prices: the days it marked `no_session` are holidays, and it is established up to the last day it loaded. None
    when it has loaded no day at all (nothing can then be settled)."""
    from athena.warehouse.warehouse import NO_SESSION

    through = warehouse.last_loaded_day()
    if through is None:
        return None
    marked = warehouse.day_status(date(1990, 1, 1), through)
    return SessionCalendar(frozenset(day for day, state in marked.items() if state == NO_SESSION), through)


def main(argv: Sequence[str] | None = None) -> int:
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    with Warehouse(args.db, read_only=True, wait_seconds=120) as warehouse:
        history = {symbol: warehouse.verdict_history(symbol) for symbol in warehouse.verdict_symbols()}
        benchmark = warehouse.index_close(BENCHMARK)
        bench_bars = warehouse.index_open_close(BENCHMARK)
        calendar = session_calendar(warehouse)
        adjusted = adjusted_history(warehouse)
        cache: dict[str, list[tuple[date, float]]] = {}
        held: dict[str, list[Bar]] = {}

        def closes(symbol: str) -> list[tuple[date, float]]:
            if symbol not in cache:
                from athena.trading_calendar import ist_date

                cache[symbol] = [(ist_date(b.timestamp), b.close) for b in adjusted(symbol)]
            return cache[symbol]

        def bars(symbol: str) -> list[Bar]:
            if symbol not in held:
                from athena.trading_calendar import ist_date

                held[symbol] = [(ist_date(b.timestamp), b.open, b.close) for b in adjusted(symbol)]
            return held[symbol]

        settled = settle_all(history, closes, benchmark, bars=bars, bench_bars=bench_bars, calendar=calendar)
    from athena.costs.model import load, round_trip

    cost = round_trip(load(), "delivery", 100000.0)
    print(format_prospective(summarize_prospective(settled, cost["fraction"]), len(settled)))
    print()
    print(f"Historical association ({LEGACY_ASSOCIATION}): return from the close of each verdict's DATA date. It can begin before the verdict existed and is NOT an executable result; kept for comparison with earlier reports.")
    print(format_summary(summarize(settled, cost["fraction"]), len(settled)))
    print(f"(costs: a delivery round trip's charges, {cost['fraction']:.2%} of the value, no slippage{'' if cost['verified'] else '; the rates are not yet checked against a contract note'})")
    for row in settled:
        got = ", ".join(f"{h}d {o['excess']:+.1%}" for h, o in sorted(row["outcomes"].items())) or "not settled yet"
        block = row.get("settlement") or {}
        pro = ", ".join(f"{h}d {o['excess']:+.1%}" for h, o in sorted(block.get("horizons", {}).items()) if o.get("status") == "settled") or block.get("status", "n/a")
        print(f"  {row['symbol']} {row['data_as_of']} {row['decision']['type']} ({row['decision']['rating']}): association {got}; prospective {pro}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
