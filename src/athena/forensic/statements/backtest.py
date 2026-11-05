"""Cross-sectional test of the statement measures (design spec 6b): did the companies with several elevated measures on a date do worse afterwards than the rest, once size and volatility are allowed for?

Measures and convergence are computed as of the entry date from filings made by then; outcomes are read from split-adjusted prices over the window after it. The comparison is the rate of a bad outcome in
the high group (three or more elevated measures) against the rate it would have with the low group's rates inside each size fifth (direct standardisation), with a bootstrap interval. A bad outcome is a bad
outcome, not proof of anything. Nothing in this module changes a weight or a band.

    python -m athena.forensic.statements.backtest [--as-of 2025-07-01] [--end 2026-11-01] [--warehouse PATH] [--fundamentals PATH] [--out PATH]"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from athena.contracts import AthenaError
from athena.forensic.statements.annuals import connect
from athena.forensic.statements.detect import Universe
from athena.forensic.statements.outcomes import LIQUIDITY_DAYS, Outcome, fifths, outcome, thirds
from athena.fundamentals.history import SuccessionTable
from athena.fundamentals.store import DEFAULT_PATH as FUNDAMENTALS
from athena.warehouse.history import adjusted_history
from athena.warehouse.warehouse import DEFAULT_PATH as WAREHOUSE, Warehouse

CUTOFFS = (0.30, 0.50, 0.70)
HIGH_AT = 3  # elevated measures for the high group
LOW_AT = 1  # at most this many for the low group
MIN_LOW_PER_STRATUM = 10
MIN_COMPUTED_SENSITIVITY = 8
DRAWS = 2000
BENCHMARK = "Nifty 500"


@dataclass(frozen=True)
class Row:
    symbol: str
    elevated: int
    computed: int
    measures: frozenset[str]  # the measures elevated
    computed_measures: frozenset[str]
    size: int  # fifth of the pre-entry traded value
    vol: int | None  # third of the pre-entry volatility
    out: Outcome

    @property
    def cov(self) -> int:
        """How many of the 14 measures could be computed: 0 for up to 3, 1 for 4 to 7, 2 for 8 or more. A company with few measures has few chances to reach three elevated."""
        return 0 if self.computed <= 3 else 1 if self.computed <= 7 else 2


def wilson(bad: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval for a rate."""
    if n == 0:
        return 0.0, 1.0
    p = bad / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def standardised(high: Sequence[tuple[int, bool]], low: Sequence[tuple[int, bool]], draws: int = DRAWS, seed: int = 7) -> dict[str, Any] | None:
    """Observed bad outcomes in the high group over those expected if it had the low group's rate in each stratum. Items are (stratum, bad). Strata where the low group has fewer than
    `MIN_LOW_PER_STRATUM` companies are left out of both. A ratio above 1 means the high group did worse than its size mix explains; the interval is a bootstrap over companies in both groups."""
    strata = sorted({s for s, _ in high} | {s for s, _ in low})
    index = {s: i for i, s in enumerate(strata)}
    lows = np.array([index[s] for s, _ in low], dtype=int)
    low_bad = np.array([b for _, b in low], dtype=float)
    n_low = np.bincount(lows, minlength=len(strata)) if len(lows) else np.zeros(len(strata), dtype=int)
    keep = n_low >= MIN_LOW_PER_STRATUM
    if not keep.any():
        return None
    h = [(index[s], b) for s, b in high if keep[index[s]]]
    if not h:
        return None
    hs = np.array([i for i, _ in h], dtype=int)
    hb = np.array([b for _, b in h], dtype=float)

    def ratio(hs_: np.ndarray, hb_: np.ndarray, ls_: np.ndarray, lb_: np.ndarray) -> tuple[float, float, float] | None:
        n = np.bincount(ls_, minlength=len(strata)).astype(float)
        bad = np.bincount(ls_, weights=lb_, minlength=len(strata))
        if (n[np.unique(hs_)] == 0).any():
            return None
        rate = np.divide(bad, n, out=np.zeros_like(bad), where=n > 0)
        expected = float(rate[hs_].sum())
        observed = float(hb_.sum())
        return (observed / expected if expected > 0 else float("nan")), observed, expected

    point = ratio(hs, hb, lows, low_bad)
    if point is None:
        return None
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(draws):
        hi = rng.integers(0, len(hs), len(hs))
        li = rng.integers(0, len(lows), len(lows))
        r = ratio(hs[hi], hb[hi], lows[li], low_bad[li])
        if r is not None and np.isfinite(r[0]):
            samples.append(r[0])
    lo, hi_ = (float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))) if len(samples) > 100 else (float("nan"), float("nan"))
    return {
        "ratio": None if not np.isfinite(point[0]) else round(point[0], 2), "ci95": [None if not np.isfinite(lo) else round(lo, 2), None if not np.isfinite(hi_) else round(hi_, 2)],
        "high_n": len(h), "high_bad": int(point[1]), "high_expected": round(point[2], 1), "low_n": int(n_low[keep].sum()),
    }


def _bucket(k: int) -> str:
    return "4+" if k >= 4 else str(k)


def group_table(rows: Sequence[Row], everyone: Sequence[Row]) -> dict[str, Any]:
    """Per count of elevated measures: how many companies, how many fell by each cut-off, and the other outcomes. `rows` have a price path after entry; `everyone` includes those that stopped trading."""
    out: dict[str, Any] = {}
    for label in ("0", "1", "2", "3", "4+"):
        mine = [r for r in rows if _bucket(r.elevated) == label]
        all_mine = [r for r in everyone if _bucket(r.elevated) == label]
        entry: dict[str, Any] = {"companies": len(all_mine), "with_price_path": len(mine)}
        for c in CUTOFFS:
            bad = sum(1 for r in mine if (r.out.max_fall or 0) >= c)
            lo, hi = wilson(bad, len(mine))
            entry[f"fell_{int(c * 100)}"] = {"n": bad, "rate": round(bad / len(mine), 3) if mine else None, "ci95": [round(lo, 3), round(hi, 3)]}
        excess = [r.out.excess_return for r in mine if r.out.excess_return is not None]
        entry["median_excess_return_vs_benchmark"] = round(statistics.median(excess), 3) if excess else None
        entry["median_final_return"] = round(statistics.median([r.out.final_return for r in mine if r.out.final_return is not None]), 3) if mine else None
        entry["fell_30_relative_to_benchmark"] = round(sum(1 for r in mine if (r.out.max_relative_fall or 0) >= 0.30) / len(mine), 3) if mine else None
        entry["stopped_trading"] = round(sum(1 for r in all_mine if r.out.ceased) / len(all_mine), 3) if all_mine else None
        entry["moved_to_trade_for_trade"] = round(sum(1 for r in all_mine if r.out.trade_for_trade) / len(all_mine), 3) if all_mine else None
        out[label] = entry
    return out


STRATA: dict[str, Callable[[Row], int | None]] = {
    "size": lambda r: r.size,
    "vol": lambda r: r.vol,
    "coverage": lambda r: r.cov,  # added after the independent audit of 6b-6d, which found the low group half made of poorly measured companies
    "size_coverage": lambda r: r.size * 3 + r.cov,
}


def trend(counts: Sequence[tuple[int, int, int]]) -> dict[str, float] | None:
    """Cochran-Armitage test for a rise in the rate of a bad outcome with the count of elevated measures. Items are (score, companies, bad). Not adjusted for anything; a continuous check beside the two-group ratio."""
    n = sum(c for _, c, _ in counts)
    bad = sum(b for _, _, b in counts)
    if n == 0 or bad == 0 or bad == n:
        return None
    p = bad / n
    mean_w = sum(w * c for w, c, _ in counts) / n
    num = sum(w * (b - c * p) for w, c, b in counts)
    var = p * (1 - p) * sum(c * (w - mean_w) ** 2 for w, c, _ in counts)
    if var <= 0:
        return None
    z = num / var ** 0.5
    from math import erf, sqrt

    return {"z": round(z, 2), "p_two_sided": round(1 - erf(abs(z) / sqrt(2)), 4)}


def headline(pool: Sequence[Row], flag_of: Callable[[Row], bool], by: str) -> dict[str, Any] | None:
    stratum = STRATA[by]
    usable = [r for r in pool if stratum(r) is not None]
    high = [(stratum(r), flag_of(r)) for r in usable if r.elevated >= HIGH_AT]
    low = [(stratum(r), flag_of(r)) for r in usable if r.elevated <= LOW_AT]
    result = standardised(high, low)
    if result is not None:
        result["raw_high_rate"] = round(sum(b for _, b in high) / len(high), 3) if high else None
        result["raw_low_rate"] = round(sum(b for _, b in low) / len(low), 3) if low else None
    return result


def analyses(rows: Sequence[Row], everyone: Sequence[Row]) -> dict[str, Any]:
    """Every comparison the spec names, on one set of rows."""
    out: dict[str, Any] = {"companies": len(everyone), "with_price_path": len(rows), "groups": group_table(rows, everyone)}
    head: dict[str, Any] = {}
    for c in CUTOFFS:
        flag = lambda r, c=c: (r.out.max_fall or 0) >= c  # noqa: E731
        head[f"fell_{int(c * 100)}"] = {
            "standardised_on_size": headline(rows, flag, "size"), "standardised_on_volatility": headline(rows, flag, "vol"),
            "standardised_on_coverage": headline(rows, flag, "coverage"), "standardised_on_size_and_coverage": headline(rows, flag, "size_coverage"),
            "trend_with_count_of_elevated": trend([(min(k, 4), sum(1 for r in rows if min(r.elevated, 4) == k), sum(1 for r in rows if min(r.elevated, 4) == k and flag(r))) for k in range(5)]),
        }
    head["fell_30_relative_to_benchmark"] = {"standardised_on_size": headline(rows, lambda r: (r.out.max_relative_fall or 0) >= 0.30, "size")}
    head["stopped_trading"] = {"standardised_on_size": headline(everyone, lambda r: r.out.ceased, "size")}
    head["moved_to_trade_for_trade"] = {"standardised_on_size": headline(everyone, lambda r: r.out.trade_for_trade, "size")}
    out["high_vs_low"] = head
    per: dict[str, Any] = {}
    for key in sorted({m for r in rows for m in r.computed_measures}):
        have = [r for r in rows if key in r.computed_measures]
        entry: dict[str, Any] = {"elevated": sum(1 for r in have if key in r.measures), "computed": len(have)}
        for c in (0.30, 0.50):
            high = [(r.size, (r.out.max_fall or 0) >= c) for r in have if key in r.measures]
            low = [(r.size, (r.out.max_fall or 0) >= c) for r in have if key not in r.measures]
            entry[f"fell_{int(c * 100)}"] = standardised(high, low, draws=500)
        per[key] = entry
    out["per_measure"] = per
    return out


COMPOSITE = ("cfo_to_profit", "accruals_ratio")  # the one composite named in spec 6g; they share inputs, so either elevated is one idea
LEAN_AT = 1.2


def composite_verdict(by_size: Mapping[str, Any] | None, by_vol: Mapping[str, Any] | None) -> str:
    """The reading fixed in spec 6g before the run. Supported: ratio above 1 with the lower end of its interval above 1 on both size and volatility. Leaning: at least 1.2 on both, lower end above 1 on one.
    Anything else is not replicated; no other control or cut-off is allowed to rescue it."""
    def parts(r: Mapping[str, Any] | None) -> tuple[float, float] | None:
        if not r or r.get("ratio") is None or not r.get("ci95") or r["ci95"][0] is None:
            return None
        return float(r["ratio"]), float(r["ci95"][0])

    s, v = parts(by_size), parts(by_vol)
    if s is None or v is None:
        return "not computable"
    if all(ratio > 1 and lo > 1 for ratio, lo in (s, v)):
        return "supported at this date"
    if all(ratio >= LEAN_AT for ratio, _ in (s, v)) and any(lo > 1 for _, lo in (s, v)):
        return "leaning"
    return "not replicated"


def composite_test(rows: Sequence[Row]) -> dict[str, Any]:
    """The cash-flow composite of spec 6g on rows with a price path: elevated if operating cash flow against profit or accruals is elevated, against companies that have one of them computed and neither elevated.
    Companies with neither computed are out and counted. The primary is the 50% fall; 30% and 70% are shown beside it and not read."""
    mine = set(COMPOSITE)
    pool = [r for r in rows if r.computed_measures & mine]
    out: dict[str, Any] = {
        "measures": list(COMPOSITE), "neither_computed": len(rows) - len(pool),
        "elevated_n": sum(1 for r in pool if r.measures & mine), "comparison_n": sum(1 for r in pool if not r.measures & mine),
        "both_elevated_n": sum(1 for r in pool if mine <= r.measures),
    }
    for c in CUTOFFS:
        flag = lambda r, c=c: (r.out.max_fall or 0) >= c  # noqa: E731
        result: dict[str, Any] = {}
        for by in ("size", "vol"):
            stratum = STRATA[by]
            usable = [r for r in pool if stratum(r) is not None]
            found = standardised([(stratum(r), flag(r)) for r in usable if r.measures & mine], [(stratum(r), flag(r)) for r in usable if not r.measures & mine])
            if found is not None:
                found["raw_high_rate"] = round(sum(flag(r) for r in usable if r.measures & mine) / max(1, out["elevated_n"]), 3)
                found["raw_low_rate"] = round(sum(flag(r) for r in usable if not r.measures & mine) / max(1, out["comparison_n"]), 3)
            result[by] = found
        out[f"fell_{int(c * 100)}"] = result
    out["verdict"] = composite_verdict(out["fell_50"]["size"], out["fell_50"]["vol"])
    return out


def _benchmark(wh: Warehouse, name: str) -> dict[date, float]:
    names = {n.casefold(): n for n in wh.index_names()}
    if name.casefold() not in names:
        raise AthenaError(f"benchmark {name!r} is not in the warehouse; indices held include {sorted(names.values())[:8]}")
    return wh.index_close(names[name.casefold()])


def collect(as_of: date, end: date, fundamentals: Path | str = FUNDAMENTALS, warehouse: Path | str = WAREHOUSE, benchmark_name: str = BENCHMARK) -> tuple[list[Row], list[Row], dict[str, Any]]:
    """Every company's measures at `as_of` joined with its outcome, as (rows with a price path, all rows, what was left out and why)."""
    universe = Universe.build(connect(fundamentals), as_of, SuccessionTable(warehouse))  # statements filed under an old symbol join their company's current one
    left_out: Counter[str] = Counter()
    with Warehouse(warehouse, read_only=True) as wh:
        history = adjusted_history(wh)
        benchmark = _benchmark(wh, benchmark_name)
        raw = duckdb.connect(str(warehouse), read_only=True)
        floor = as_of - timedelta(days=LIQUIDITY_DAYS)
        liquid = {s: (n, tv) for s, n, tv in raw.execute("SELECT symbol, count(*), avg(close * volume) FROM bars WHERE day > ? AND day <= ? AND close > 0 GROUP BY symbol", [floor, as_of]).fetchall()}
        at_entry = {s: (ser, day) for s, ser, day in raw.execute("SELECT symbol, arg_max(series, day), max(day) FROM bars WHERE day > ? AND day <= ? GROUP BY symbol", [as_of - timedelta(days=7), as_of]).fetchall()}
        in_window: dict[str, set[str]] = defaultdict(set)
        for s, ser in raw.execute("SELECT DISTINCT symbol, series FROM bars WHERE day > ? AND day <= ? AND series IS NOT NULL", [as_of, end]).fetchall():
            in_window[s].add(ser)
        raw.close()
        found: dict[str, tuple[Outcome, frozenset[str], int]] = {}
        for symbol in sorted(universe.annuals):
            try:
                bars = history(symbol)
            except AthenaError:
                left_out["no usable prices"] += 1
                continue
            if not bars:
                left_out["no prices"] += 1
                continue
            # the bars joined across a rename (`joined_bars`) are the ones outcome() reads, so the floor, the traded value and the series are read across the earlier symbols too; a company renamed after the entry date traded under its old symbol then
            names = [step.old for step in wh.predecessors(symbol)] + [symbol]
            n = sum(liquid.get(x, (0, None))[0] for x in names)
            weighted = [(liquid[x][0], liquid[x][1]) for x in names if x in liquid and liquid[x][1] is not None]
            tv = sum(c * v for c, v in weighted) / sum(c for c, _ in weighted) if weighted else None
            latest = max((at_entry[x] for x in names if x in at_entry), key=lambda t: t[1], default=(None, None))[0]
            series_after = sorted(set().union(*(in_window.get(x, set()) for x in names)))
            breaks = [day for day, kind, _ in history.events_found(symbol) if kind == "unexplained"]
            o = outcome(symbol, bars, as_of, end, benchmark, series_at_entry=latest, series_in_window=series_after, unexplained_days=breaks, traded_value=tv, liquid_bars=n)
            if o is None:
                left_out["failed the entry floor (no close in the week before entry, or under 40 bars in the 90 days before)"] += 1
                continue
            found[symbol] = (o, frozenset(f.key for f in universe.findings(symbol) if f.elevated), len(universe.values.get(symbol, {})))
    size = fifths({s: o.traded_value for s, (o, _, _) in found.items() if o.traded_value})
    vol = thirds({s: o.volatility for s, (o, _, _) in found.items() if o.volatility is not None})
    counts = universe.elevated_counts()
    everyone = [
        Row(s, counts[s], computed, elevated, frozenset(universe.values.get(s, {})), size[s], vol.get(s), o)
        for s, (o, elevated, computed) in found.items() if s in size
    ]
    left_out["no traded value before entry"] = len(found) - len(everyone)
    return [r for r in everyone if not r.out.unexplained_break and r.out.max_fall is not None], everyone, dict(left_out)


def run(as_of: date, end: date, fundamentals: Path | str = FUNDAMENTALS, warehouse: Path | str = WAREHOUSE) -> dict[str, Any]:
    rows, everyone, left_out = collect(as_of, end, fundamentals, warehouse)
    clean_everyone = [r for r in everyone if not r.out.unexplained_break]
    wide = [r for r in everyone if r.out.max_fall is not None]
    rich = [r for r in rows if r.computed >= MIN_COMPUTED_SENSITIVITY]
    rich_all = [r for r in clean_everyone if r.computed >= MIN_COMPUTED_SENSITIVITY]
    return {
        "as_of": as_of.isoformat(), "end": end.isoformat(), "benchmark": BENCHMARK, "left_out": left_out,
        "unexplained_price_break_in_window": sum(1 for r in everyone if r.out.unexplained_break),
        "primary": analyses(rows, clean_everyone),
        "cash_flow_composite": composite_test(rows),
        "cash_flow_composite_keeping_unexplained_breaks": composite_test(wide),
        "sensitivity_keeping_unexplained_breaks": analyses(wide, everyone),
        "sensitivity_at_least_8_measures_computed": analyses(rich, rich_all),
        "rows": [
            {"symbol": r.symbol, "elevated": r.elevated, "computed": r.computed, "measures": sorted(r.measures), "size_fifth": r.size, "vol_third": r.vol, "max_fall": r.out.max_fall,
             "excess_return": r.out.excess_return, "ceased": r.out.ceased, "trade_for_trade": r.out.trade_for_trade, "unexplained_break": r.out.unexplained_break}
            for r in everyone
        ],
    }


def render(result: Mapping[str, Any]) -> str:
    """The numbers a reader needs first, as text."""
    lines = [f"As of {result['as_of']}, outcomes to {result['end']}. Left out: {result['left_out']}. Unexplained price break in window: {result['unexplained_price_break_in_window']}."]
    for name in ("primary", "sensitivity_keeping_unexplained_breaks", "sensitivity_at_least_8_measures_computed"):
        a = result[name]
        lines += ["", f"== {name}: {a['companies']} companies, {a['with_price_path']} with a price path =="]
        lines.append("elevated | companies | fell30 | fell50 | fell70 | med excess | stopped | trade-for-trade")
        for label, g in a["groups"].items():
            lines.append(
                f"{label:>8} | {g['companies']:>9} | {_pct(g['fell_30']['rate'])} | {_pct(g['fell_50']['rate'])} | {_pct(g['fell_70']['rate'])} | {_pct(g['median_excess_return_vs_benchmark'])} | {_pct(g['stopped_trading'])} | {_pct(g['moved_to_trade_for_trade'])}"
            )
        lines.append(f"high ({HIGH_AT}+ elevated) against low (at most {LOW_AT}), ratio of observed to expected (above 1 = worse than the size/volatility mix explains):")
        for outcome_name, by in a["high_vs_low"].items():
            for control, r in by.items():
                if r and "z" in r:
                    lines.append(f"  {outcome_name} / {control}: z {r['z']} (p {r['p_two_sided']})")
                elif r:
                    lines.append(f"  {outcome_name} / {control}: {r['ratio']} (95% {r['ci95']}), high {r['high_bad']} of {r['high_n']} vs {r['high_expected']} expected; raw {r['raw_high_rate']} vs {r['raw_low_rate']}")
                else:
                    lines.append(f"  {outcome_name} / {control}: not computable")
    for name in ("cash_flow_composite", "cash_flow_composite_keeping_unexplained_breaks"):
        c = result.get(name)
        if not c:
            continue
        lines += ["", f"== {name} (spec 6g; either of {', '.join(c['measures'])} elevated against neither): elevated {c['elevated_n']}, comparison {c['comparison_n']}, neither computed {c['neither_computed']}, both elevated {c['both_elevated_n']} =="]
        for cut in ("fell_30", "fell_50", "fell_70"):
            for by in ("size", "vol"):
                r = c[cut][by]
                lines.append(f"  {cut} / {by}: " + (f"{r['ratio']} (95% {r['ci95']}), high {r['high_bad']} of {r['high_n']} vs {r['high_expected']} expected; raw {r['raw_high_rate']} vs {r['raw_low_rate']}" if r else "not computable"))
        lines.append(f"  reading by the rule fixed in the spec (50% fall, size and volatility): {c['verdict']}")
    lines += ["", "== per measure (primary, fell 30% / 50%, standardised on size; 14 looks, so some will appear by chance) =="]
    for key, e in result["primary"]["per_measure"].items():
        a, b = e["fell_30"], e["fell_50"]
        lines.append(f"{key:>24}: elevated {e['elevated']:>4} of {e['computed']:>4} | 30%: {a['ratio'] if a else 'n/a'} {a['ci95'] if a else ''} | 50%: {b['ratio'] if b else 'n/a'} {b['ci95'] if b else ''}")
    return "\n".join(lines)


def _pct(x: float | None) -> str:
    return "   n/a" if x is None else f"{x * 100:5.1f}%"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", default="2025-07-01")
    parser.add_argument("--end", default="2026-11-01")
    parser.add_argument("--warehouse", default=str(WAREHOUSE))
    parser.add_argument("--fundamentals", default=str(FUNDAMENTALS))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    as_of, end = date.fromisoformat(args.as_of), date.fromisoformat(args.end)
    result = run(as_of, end, args.fundamentals, args.warehouse)
    out = Path(args.out) if args.out else Path(".athena") / "forensic_backtest" / f"{as_of.isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(render(result))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
