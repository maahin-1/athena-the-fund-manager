"""Does a change in who owns a stock say anything about its return afterwards? A test of the ownership analyst's inputs, with no model in it.

    python -m athena.ownership.study [--horizons 21,63] [--out .athena/reports/ownership-study.json]

For each quarter end with a shareholding pattern for enough stocks, every stock's change in a holder group's share since the quarter before (and since a year before) is a score. The score is
treated as known `LAG_DAYS` after the quarter end (the filing deadline is 21 days; the margin covers late filings and revisions), and the stock's return is measured from the first close on or
after that day over `horizon` trading days, less the median stock's. The rank correlation of the score with that excess return is taken per quarter; the report gives its mean over quarters, a
t-statistic over the quarters, the share of quarters that are positive, and the top fifth minus the bottom fifth. Prices are the warehouse's adjusted closes, closes only, no costs.
Limits: the exchange files hold patterns from about Dec 2023 for most stocks, so there are only a handful of quarters, and the universe is the Nifty 500 names that were fetched; a t-statistic
over so few quarters is weak evidence either way."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from athena.evaluation.walkforward import MIN_NAMES, MIN_PRICE, Panel
from athena.ownership.packet import GROUPS, Quarter, quarters
from athena.valuation.calibrate import quintile_returns, spearman

LAG_DAYS = 45
REPORT_PATH = Path(".athena") / "reports" / "ownership-study.json"
SIGNALS = ("promoters", "fii", "dii", "mutual_funds", "shareholders")


def score(now: Quarter, before: Quarter, name: str) -> float | None:
    """The change in one holder group's share (percentage points), or in the number of shareholders (a fraction), from `before` to `now`."""
    if name == "shareholders":
        return now.holders / before.holders - 1.0 if now.holders and before.holders else None
    if name in now.pct and name in before.pct:
        return now.pct[name] - before.pct[name]
    return None


def _months(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def scores_by_quarter(by_symbol: Mapping[str, Sequence[Quarter]], back: int) -> dict[date, dict[str, dict[str, float]]]:
    """`{quarter end: {signal: {symbol: change since `back` quarters earlier}}}` from each stock's usable quarters."""
    out: dict[date, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for symbol, rows in by_symbol.items():
        for now in rows:
            before = next((q for q in rows if _months(q.end, now.end) == 3 * back), None)
            if before is None:
                continue
            for name in SIGNALS:
                value = score(now, before, name)
                if value is not None and math.isfinite(value):
                    out[now.end][name][symbol] = value
    return out


def evaluate(panel: Panel, scores: Mapping[date, Mapping[str, Mapping[str, float]]], horizon: int) -> dict[str, list[dict[str, Any]]]:
    """Per signal, one row per quarter end: the stocks, the rank correlation with the excess return over `horizon` trading days, and the top fifth minus the bottom fifth."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for end, by_signal in sorted(scores.items()):
        start = next((i for i, d in enumerate(panel.days) if d >= end + timedelta(days=LAG_DAYS)), None)
        if start is None or start + horizon >= len(panel.days):
            continue
        for name, values in by_signal.items():
            names, forward = [], []
            for symbol, _ in values.items():
                a = panel.px.get(symbol)
                if a is not None and a[start] >= MIN_PRICE and a[start + horizon] > 0:
                    names.append(symbol)
                    forward.append(a[start + horizon] / a[start] - 1.0)
            if len(names) < MIN_NAMES:
                continue
            median = statistics.median(forward)
            excess = [f - median for f in forward]
            xs = [values[s] for s in names]
            rho = spearman(xs, excess)
            quintiles = quintile_returns(xs, excess)
            if rho is None or quintiles is None:
                continue
            out[name].append({"quarter": end, "entry": panel.days[start], "n": len(names), "ic": rho, "top_minus_bottom": quintiles[-1] - quintiles[0]})
    return out


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ics = [r["ic"] for r in rows]
    spreads = [r["top_minus_bottom"] for r in rows]
    t = statistics.fmean(ics) / (statistics.stdev(ics) / math.sqrt(len(ics))) if len(ics) >= 3 and statistics.stdev(ics) > 0 else None
    return {
        "quarters": len(rows), "mean_ic": statistics.fmean(ics) if ics else None, "ic_t": t, "positive": sum(1 for x in ics if x > 0),
        "mean_top_minus_bottom": statistics.fmean(spreads) if spreads else None, "stocks_per_quarter": statistics.fmean(r["n"] for r in rows) if rows else None,
    }


def format_row(name: str, back: int, horizon: int, s: Mapping[str, Any]) -> str:
    if not s["quarters"]:
        return f"{name} over {back}q, {horizon} days: no quarters"
    t = "n/a" if s["ic_t"] is None else f"{s['ic_t']:+.1f}"
    return (f"{name:13s} change over {back}q, {horizon:3d} days: {s['quarters']} quarters, {s['stocks_per_quarter']:.0f} stocks, mean rank IC {s['mean_ic']:+.3f} (t {t}), "
            f"positive in {s['positive']} of {s['quarters']}, top fifth minus bottom fifth {s['mean_top_minus_bottom']:+.2%}")


def main(argv: Sequence[str] | None = None) -> int:
    from athena.fundamentals.store import DEFAULT_PATH as FUND_PATH
    from athena.fundamentals.store import open_read
    from athena.trading_calendar import ist_date
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--fund", default=str(FUND_PATH))
    parser.add_argument("--horizons", default="21,63")
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args(argv)
    by_symbol: dict[str, list[Quarter]] = {}
    with open_read(args.fund) as store:
        symbols = [r[0] for r in store._con.execute("SELECT DISTINCT symbol FROM shareholding").fetchall()]
        for symbol in symbols:
            rows = quarters(store.holdings(symbol))
            if rows:
                by_symbol[symbol] = rows
    with Warehouse(args.db, read_only=True, wait_seconds=300) as warehouse:
        history = adjusted_history(warehouse)
        series = {s: [(ist_date(b.timestamp), b.close) for b in history(s)] for s in by_symbol}
    panel = Panel.build({s: rows for s, rows in series.items() if rows})
    report: dict[str, Any] = {"lag_days": LAG_DAYS, "stocks": len(by_symbol), "results": {}}
    for back in (1, 4):
        scores = scores_by_quarter(by_symbol, back)
        for horizon in (int(h) for h in args.horizons.split(",")):
            for name, rows in evaluate(panel, scores, horizon).items():
                s = summary(rows)
                report["results"][f"{name}_{back}q_{horizon}d"] = {**s, "rows": [{**r, "quarter": r["quarter"].isoformat(), "entry": r["entry"].isoformat()} for r in rows]}
                print(format_row(name, back, horizon, s))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
