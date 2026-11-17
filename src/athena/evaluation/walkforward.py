"""Walk-forward test of a rule that scores stocks from prices alone.

    python -m athena.evaluation.walkforward [--since 2011-01-01] [--horizon 21] [--out .athena/reports/walkforward.json]

A *signal* is a fixed written rule: on each test date it gives every stock a score from prices up to that date. Nothing is fitted, so every year is an out-of-sample test of the rule as
written. For each test date (every `horizon` trading days, so the forward windows do not overlap) the harness gives the rank correlation of the score with the stock's later return over the next
`horizon` days, less the median stock's, and the return of the top fifth of scores less the bottom fifth. It reports those by calendar year and overall, with a t-statistic over the dates and the
share of years in which the rule worked. Prices are the warehouse's split- and bonus-adjusted closes, and every stock that traded on a date is in that date's universe, including those that later
stopped trading once the older years are loaded. Limits: closes only (no dividends), no trading costs or turnover (the cost model is separate), prices under `MIN_PRICE` rupees are left out as too
thin to trade, and a stock needs a close on the test date and on the end of its window."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from athena.valuation.calibrate import quintile_returns, spearman

MIN_PRICE = 10.0
MIN_NAMES = 100  # stocks a date needs before its correlation counts
REPORT_PATH = Path(".athena") / "reports" / "walkforward.json"
Signal = Callable[["Panel", int], Mapping[str, float]]


@dataclass(frozen=True)
class Panel:
    """Adjusted closes on a common list of trading days: `px[symbol][i]` is the close on `days[i]`, nan where the stock did not trade."""

    days: list[date]
    px: dict[str, np.ndarray]
    raw: dict[str, np.ndarray] | None = None  # the close as quoted that day (not adjusted for later splits): what a price floor must be tested against (`athena.evaluation.cohort`)
    traded: dict[str, np.ndarray] | None = None  # close x volume in rupees as traded that day, nan where the stock did not trade
    min_price: float = MIN_PRICE  # the floor the signals apply to the ADJUSTED close: the legacy study's; the cohort study sets it to 0 and applies the nominal-price rule itself

    @classmethod
    def build(cls, series: Mapping[str, Sequence[tuple[date, float]]]) -> Panel:
        days = sorted({d for rows in series.values() for d, _ in rows})
        index = {d: i for i, d in enumerate(days)}
        px: dict[str, np.ndarray] = {}
        for symbol, rows in series.items():
            arr = np.full(len(days), np.nan)
            for d, close in rows:
                arr[index[d]] = close
            px[symbol] = arr
        return cls(days, px)


def momentum_12_1(panel: Panel, i: int) -> dict[str, float]:
    """The return from 252 to 21 trading days ago (the last month left out, as it tends to reverse)."""
    if i < 252:
        return {}
    return {s: a[i - 21] / a[i - 252] - 1.0 for s, a in panel.px.items() if a[i - 21] > 0 and a[i - 252] > 0 and a[i] >= panel.min_price}


def reversal_1m(panel: Panel, i: int) -> dict[str, float]:
    """Minus the last 21 days' return: last month's losers score high."""
    if i < 21:
        return {}
    return {s: -(a[i] / a[i - 21] - 1.0) for s, a in panel.px.items() if a[i - 21] > 0 and a[i] >= panel.min_price}


def low_volatility(panel: Panel, i: int) -> dict[str, float]:
    """Minus the standard deviation of daily returns over the last 126 trading days: the steadier the stock, the higher the score."""
    if i < 126:
        return {}
    out = {}
    for s, a in panel.px.items():
        window = a[i - 126 : i + 1]
        if np.isnan(window).any() or a[i] < panel.min_price or (window <= 0).any():
            continue
        out[s] = -float(np.std(np.diff(window) / window[:-1]))
    return out


SIGNALS: dict[str, Signal] = {"momentum_12_1": momentum_12_1, "reversal_1m": reversal_1m, "low_volatility": low_volatility}


def test_dates(panel: Panel, since: date, horizon: int) -> list[int]:
    """Indexes of the test dates: every `horizon` trading days from `since`, each with a full forward window."""
    first = next((i for i, d in enumerate(panel.days) if d >= since), len(panel.days))
    return list(range(first, len(panel.days) - horizon, horizon))


def run(panel: Panel, signal: Signal, since: date, horizon: int = 21) -> list[dict[str, Any]]:
    """One row per test date with at least `MIN_NAMES` stocks: the date, the stocks, the rank correlation and the top-minus-bottom-fifth return."""
    rows = []
    for i in test_dates(panel, since, horizon):
        scores = signal(panel, i)
        names, forward = [], []
        for s, score in scores.items():
            a = panel.px[s]
            if math.isfinite(score) and a[i] > 0 and a[i + horizon] > 0:  # a stock with no close at the end of the window is left out
                names.append(s)
                forward.append(a[i + horizon] / a[i] - 1.0)
        if len(names) < MIN_NAMES:
            continue
        median = statistics.median(forward)
        excess = [f - median for f in forward]
        values = [scores[s] for s in names]
        rho = spearman(values, excess)
        quintiles = quintile_returns(values, excess)
        if rho is None or quintiles is None:
            continue
        rows.append({"date": panel.days[i], "n": len(names), "ic": rho, "top_minus_bottom": quintiles[-1] - quintiles[0], "top_excess": quintiles[-1]})
    return rows


def _summary(rows: Sequence[Mapping[str, Any]], cost: float = 0.0) -> dict[str, Any]:
    ics = [r["ic"] for r in rows]
    spreads = [r["top_minus_bottom"] for r in rows]
    t = statistics.fmean(ics) / (statistics.stdev(ics) / math.sqrt(len(ics))) if len(ics) >= 3 and statistics.stdev(ics) > 0 else None
    top = [r["top_excess"] for r in rows]
    return {
        "dates": len(rows), "mean_ic": statistics.fmean(ics) if ics else None, "ic_t": t, "mean_top_minus_bottom": statistics.fmean(spreads) if spreads else None,
        "mean_top_excess": statistics.fmean(top) if top else None, "mean_top_excess_after_costs": statistics.fmean(top) - cost if top else None,
    }


def report(rows: Sequence[Mapping[str, Any]], cost: float = 0.0) -> dict[str, Any]:
    """Overall and by calendar year, and the share of years with a positive average correlation."""
    by_year: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_year[r["date"].year].append(r)
    years = {str(y): _summary(v, cost) for y, v in sorted(by_year.items())}
    positive = [s for s in years.values() if s["mean_ic"] is not None and s["mean_ic"] > 0]
    return {"overall": _summary(rows, cost), "years": years, "years_positive": f"{len(positive)} of {len(years)}"}


def format_report(name: str, rep: Mapping[str, Any]) -> str:
    o = rep["overall"]
    if not o["dates"]:
        return f"{name}: no test dates"
    t = "n/a" if o["ic_t"] is None else f"{o['ic_t']:+.1f}"
    per_year = " ".join(f"{y[2:]}:{s['mean_ic']:+.2f}" for y, s in rep["years"].items() if s["mean_ic"] is not None)
    return (f"{name}: {o['dates']} dates, mean rank IC {o['mean_ic']:+.3f} (t {t}), top fifth minus bottom fifth {o['mean_top_minus_bottom']:+.2%} a window; "
            f"top fifth alone {o['mean_top_excess']:+.2%} ({o['mean_top_excess_after_costs']:+.2%} after the cost of trading it); "
            f"positive in {rep['years_positive']} years. By year: {per_year}")


def _main_cohort(args: argparse.Namespace, since: date, out_path: str) -> int:
    """The cohort study: a frozen, eligibility-filtered cohort that keeps stopped and restructured names, as an association reported as a range (`athena.evaluation.cohort`, policy locked in
    docs/audit-2026-11-12/WP04-STUDY-POLICY.md). Writes its own file; the legacy report is never overwritten by it."""
    from athena.evaluation import cohort
    from athena.evaluation.policy import POLICY
    from athena.trading_calendar import ist_date
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import Warehouse

    with Warehouse(args.db, read_only=True, wait_seconds=300) as warehouse:
        history = adjusted_history(warehouse)
        panel, unadjustable = cohort.load_panel(warehouse, lambda symbol: [(ist_date(b.timestamp), b.close) for b in history(symbol)])
    print(f"cohort study, policy {POLICY.fingerprint()}: frozen cohort, nominal-price and traded-value eligibility, stopped and restructured names kept")
    out: dict[str, Any] = {"study_policy": POLICY.to_dict(), "study_policy_fingerprint": POLICY.fingerprint()}
    for name, signal in SIGNALS.items():
        out[name] = cohort.report_cohort(cohort.run_cohort(panel, signal, since, args.horizon, unadjustable=unadjustable))
        print(cohort.format_cohort(name, out[name]))
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from athena.trading_calendar import ist_date
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--since", default="2011-01-01")
    parser.add_argument("--horizon", type=int, default=21)
    parser.add_argument("--legacy", action="store_true", help="the earlier study: a name with no close at the end of its window is dropped, and the price floor is applied to the adjusted close. Writes the earlier report path.")
    parser.add_argument("--out", default=None, help=f"default: {REPORT_PATH} with --legacy, else a separate cohort report beside it")
    args = parser.parse_args(argv)
    out_path = args.out or str(REPORT_PATH if args.legacy else REPORT_PATH.with_name("walkforward-cohort.json"))
    since = date.fromisoformat(args.since)
    if not args.legacy:
        return _main_cohort(args, since, out_path)
    with Warehouse(args.db, read_only=True, wait_seconds=300) as warehouse:
        history = adjusted_history(warehouse)
        series = {e["symbol"]: [(ist_date(b.timestamp), b.close) for b in history(e["symbol"])] for e in warehouse.universe("stock")}
    panel = Panel.build({s: rows for s, rows in series.items() if rows})
    from athena.costs.model import load, round_trip

    rt = round_trip(load(), "delivery", 100000.0, daily_traded_crore=100.0)  # a stock of middling liquidity; every window sells the old fifth and buys the new
    cost = rt["fraction"]
    print("legacy study: survivors of each window only, price floor on the adjusted close")
    print(f"cost of trading a window: {cost:.2%} of the value{'' if rt['verified'] else ' (rates not yet checked against a contract note)'}")
    out: dict[str, Any] = {}
    for name, signal in SIGNALS.items():
        out[name] = report(run(panel, signal, since, args.horizon), cost)
        print(format_report(name, out[name]))
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
