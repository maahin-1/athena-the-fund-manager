"""A signal study on a frozen cohort, in which nobody is dropped for having an unknown outcome (WP04 step 5, Q5).

The rules are the locked ones in `athena.evaluation.policy` (docs/audit-2026-11-12/WP04-STUDY-POLICY.md), written before any comparison was run. In short:

* The cohort on a selection date is every security with a close that day, filtered by rules that use only what was known that day: the price as quoted then (not adjusted for later splits) and the
  traded value in the sessions before entry. It is frozen: nothing later (a delisting, a demerger, a restatement) removes a member, and every excluded name is counted by reason.
* A member with a close at the end of its window is `priced`. One with none (suspended, delisted) is `no_end_price`: its proceeds are UNKNOWN, and it is shown under two ASSUMED scenarios, `zero_recovery`
  (total loss, -100%) and `last_close` (valued at the last price it traded at, from the entry close). They are a sensitivity interval, not bounds: recovery or liquidation proceeds could exceed the last
  traded price, so nothing built on them is a bounded return. One whose window holds a demerger, scheme, capital reduction or unreadable ratio is `restructured`: its return cannot be read from the price
  alone, so it is unknown too, with the unadjusted price return shown apart as `raw_price`.
* The result is an ASSOCIATION (rank correlation and quintile spreads of a signal with later returns). It is reported under those scenarios with the unknown share beside it. Portfolio returns are
  `not_computed`: an executable return needs an entry rule, costs and a liquidity model, and the two are never mixed.

The older `walkforward.run` (kept, and labelled legacy in the report) drops a name with no close at the end of its window and applies its price floor to the adjusted close."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np

from athena.evaluation import walkforward as wf
from athena.evaluation.policy import POLICY, StudyPolicy
from athena.valuation.calibrate import quintile_returns, spearman

PRICE_FLOOR = "price below the floor"
NO_QUOTE = "no nominal close"
THIN = "median traded value below the floor"
NO_TRADES = "no traded value on every one of the last {n} sessions"
RESTRUCTURING_KINDS = {"demerger", "scheme", "capital_reduction"}


@dataclass(frozen=True)
class Cohort:
    """The frozen set for one selection date. `members` are eligible; `ineligible` counts the rest by reason; `universe` is every name with a close that day."""

    index: int
    day: date
    members: tuple[str, ...]
    ineligible: dict[str, int]
    universe: int
    fingerprint: str  # of the date, the members and the policy: it can only change if something known on the date changes


@dataclass(frozen=True)
class Outcome:
    status: str  # "priced", "no_end_price" or "restructured"
    forward: float | None  # the measured return; None when unknown
    loss_case: float | None  # the assumed total-loss scenario (equals `forward` when priced; -100% for a stopped name; None when restructured). An assumption, not a limit on the truth
    last_mark_case: float | None  # the assumed last-traded-price scenario (the return to the last close a stopped name traded at). Proceeds could exceed it
    raw_price: float | None = None  # restructured only: the unadjusted price return, a named sensitivity, not the result


def freeze_cohort(panel: wf.Panel, i: int, policy: StudyPolicy = POLICY) -> Cohort:
    """The cohort on `panel.days[i]`, from information up to and including that day only."""
    n = policy.liquidity_sessions
    members: list[str] = []
    reasons: Counter[str] = Counter()
    universe = 0
    raw = panel.raw or {}
    traded = panel.traded or {}
    for symbol, adjusted in sorted(panel.px.items()):
        if not (np.isfinite(adjusted[i]) and adjusted[i] > 0):
            continue  # no close that day: not in the universe on that day
        universe += 1
        quote = raw.get(symbol)
        if quote is None or not np.isfinite(quote[i]) or quote[i] <= 0:
            reasons[NO_QUOTE] += 1
            continue
        if quote[i] < policy.min_nominal_price:
            reasons[PRICE_FLOOR] += 1
            continue
        window = traded.get(symbol)
        values = window[max(0, i - n + 1) : i + 1] if window is not None else np.array([])
        if len(values) < n or not np.isfinite(values).all():
            reasons[NO_TRADES.format(n=n)] += 1
            continue
        if float(np.median(values)) < policy.min_median_traded_value:
            reasons[THIN] += 1
            continue
        members.append(symbol)
    digest = hashlib.sha256(json.dumps([panel.days[i].isoformat(), members, policy.fingerprint()]).encode("utf-8")).hexdigest()[:16]
    return Cohort(i, panel.days[i], tuple(members), dict(reasons), universe, digest)


def _last_index(values: np.ndarray, start: int, end: int) -> int:
    for k in range(end, start - 1, -1):
        if np.isfinite(values[k]) and values[k] > 0:
            return k
    return start


def outcome(panel: wf.Panel, symbol: str, i: int, horizon: int, unadjustable: Mapping[str, Sequence[date]] | None = None) -> Outcome:
    """How the return of `symbol` from the close on day `i` to day `i + horizon` is known. A restructuring in the window comes first (the price is then not the whole story), then a missing end price."""
    adjusted = panel.px[symbol]
    end = i + horizon
    entry_day, end_day = panel.days[i], panel.days[end]
    if any(entry_day < d <= end_day for d in (unadjustable or {}).get(symbol, ())):
        sensitivity = None
        if panel.raw is not None and symbol in panel.raw:
            quote = panel.raw[symbol]
            if np.isfinite(quote[i]) and quote[i] > 0 and np.isfinite(quote[end]) and quote[end] > 0:
                sensitivity = float(quote[end] / quote[i] - 1.0)
        return Outcome("restructured", None, None, None, sensitivity)
    if np.isfinite(adjusted[end]) and adjusted[end] > 0:
        forward = float(adjusted[end] / adjusted[i] - 1.0)
        return Outcome("priced", forward, forward, forward)
    last = _last_index(adjusted, i, end)
    return Outcome("no_end_price", None, -1.0, float(adjusted[last] / adjusted[i] - 1.0))


def _stats(scores: Sequence[float], forward: Sequence[float], min_names: int) -> dict[str, Any] | None:
    """Rank correlation of the scores with the returns less the group's median, and the top fifth's, for one set of names; None when the set is too small to say."""
    if len(scores) < min_names:
        return None
    median = statistics.median(forward)
    excess = [f - median for f in forward]
    rho = spearman(list(scores), excess)
    quintiles = quintile_returns(list(scores), excess)
    if rho is None or quintiles is None:
        return None
    return {"n": len(scores), "ic": rho, "top_minus_bottom": quintiles[-1] - quintiles[0], "top_excess": quintiles[-1]}


def run_cohort(
    panel: wf.Panel, signal: wf.Signal, since: date, horizon: int = 21, policy: StudyPolicy = POLICY, unadjustable: Mapping[str, Sequence[date]] | None = None, min_names: int = wf.MIN_NAMES,
) -> list[dict[str, Any]]:
    """One row per test date with enough scored names: the date, the cohort's accounting (every name counted: universe, ineligible by reason, scored, and the scored split into priced, no_end_price and
    restructured, with the unknown share), and the association three ways: `measurable` (priced names only), `zero_recovery` and `last_close` (priced names plus the stopped ones under the named assumed
    scenario; the restructured stay out of both because their return is unknown). The two scenarios are a sensitivity interval, not bounds on the truth. Nothing is dropped without being counted."""
    if panel.raw is None or panel.traded is None:
        raise ValueError("a cohort study needs the panel's nominal closes and traded values (`load_panel`)")
    open_panel = replace(panel, min_price=0.0)  # the adjusted-price floor of the legacy signals is off: eligibility is the cohort's nominal-price rule
    rows: list[dict[str, Any]] = []
    for i in wf.test_dates(panel, since, horizon):
        frozen = freeze_cohort(panel, i, policy)
        scores = {s: v for s, v in signal(open_panel, i).items() if s in set(frozen.members) and math.isfinite(v)}
        if len(scores) < min_names:
            continue
        found = {s: outcome(panel, s, i, horizon, unadjustable) for s in scores}
        counts = Counter(o.status for o in found.values())
        priced = [s for s, o in found.items() if o.status == "priced"]
        stopped = [s for s, o in found.items() if o.status == "no_end_price"]
        association = {
            "measurable": _stats([scores[s] for s in priced], [found[s].forward for s in priced], min_names),  # type: ignore[misc]
            "zero_recovery": _stats([scores[s] for s in priced + stopped], [found[s].loss_case for s in priced + stopped], min_names),  # type: ignore[misc]
            "last_close": _stats([scores[s] for s in priced + stopped], [found[s].last_mark_case for s in priced + stopped], min_names),  # type: ignore[misc]
        }
        unknown = counts["no_end_price"] + counts["restructured"]
        rows.append({
            "date": panel.days[i], "cohort": frozen.fingerprint,
            "accounting": {
                "universe": frozen.universe, "ineligible": dict(frozen.ineligible), "scored": len(scores), "priced": counts["priced"], "no_end_price": counts["no_end_price"],
                "restructured": counts["restructured"], "unknown_share": unknown / len(scores),
            },
            "association": association,
        })
    return rows


def _scenario_summary(rows: Sequence[Mapping[str, Any]], scenario: str) -> dict[str, Any]:
    flat = [{"date": r["date"], **r["association"][scenario]} for r in rows if r["association"][scenario] is not None]
    summary = wf._summary([{"ic": r["ic"], "top_minus_bottom": r["top_minus_bottom"], "top_excess": r["top_excess"]} for r in flat])
    return {**summary, "mean_n": statistics.fmean(r["n"] for r in flat) if flat else None}


def report_cohort(rows: Sequence[Mapping[str, Any]], policy: StudyPolicy = POLICY) -> dict[str, Any]:
    """Overall and by calendar year, each association under the measurable names and under the two assumed terminal scenarios, with the cohort accounting totals and the policy fingerprint. The interval
    between the scenarios is a sensitivity interval of assumptions, not a bound on any true result. It is an ASSOCIATION study: no portfolio return is in it."""
    def block(subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        out = {scenario: _scenario_summary(subset, scenario) for scenario in ("measurable", "zero_recovery", "last_close")}
        ends = [out["zero_recovery"]["mean_ic"], out["last_close"]["mean_ic"]]
        out["scenario_interval_mean_ic"] = sorted(ends) if all(e is not None for e in ends) else None
        return out

    by_year: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_year[r["date"].year].append(r)
    totals = Counter()
    ineligible: Counter[str] = Counter()
    for r in rows:
        a = r["accounting"]
        totals.update({k: a[k] for k in ("universe", "scored", "priced", "no_end_price", "restructured")})
        ineligible.update(a["ineligible"])
    scored = totals["scored"]
    return {
        "study": policy.signal_study, "portfolio_returns": policy.portfolio_returns, "study_policy": policy.fingerprint(),
        "label": "signal-ranking association on a frozen cohort; terminal and restructured names are in the accounting, with unknown proceeds shown under two assumed scenarios; not a portfolio return",
        "scenario_note": "zero_recovery assumes a stopped name's total loss and last_close values it at its last traded price. They are a sensitivity interval of assumptions, not bounds: recovery or liquidation proceeds could "
                         "exceed the last price, and restructured names have no scenario at all. No aggregate here is a bounded return.",
        "overall": block(rows), "years": {str(y): block(v) for y, v in sorted(by_year.items())},
        "cohort_accounting": {**{k: totals[k] for k in ("universe", "scored", "priced", "no_end_price", "restructured")}, "unknown_share": (totals["no_end_price"] + totals["restructured"]) / scored if scored else None,
                              "ineligible": dict(ineligible), "dates": len(rows)},
    }


def format_cohort(name: str, rep: Mapping[str, Any]) -> str:
    o, acc = rep["overall"], rep["cohort_accounting"]
    if not acc["dates"]:
        return f"{name}: no test dates"
    rng = o["scenario_interval_mean_ic"]
    span = "n/a" if rng is None else f"{rng[0]:+.3f} to {rng[1]:+.3f}"
    measurable = o["measurable"]["mean_ic"]
    share = "n/a" if acc["unknown_share"] is None else f"{acc['unknown_share']:.1%}"
    return (f"{name}: {acc['dates']} dates; mean rank IC {'n/a' if measurable is None else f'{measurable:+.3f}'} on names with an end price; {span} under two assumed scenarios for stopped names (total loss, or valued at their last traded price; a sensitivity interval, not a bound: proceeds could exceed the last price) "
            f"({share} of the cohort had an unknown outcome: {acc['no_end_price']} stopped, {acc['restructured']} restructured). Association only; no portfolio return.")


def load_panel(warehouse: Any, adjusted: Callable[[str], Sequence[tuple[date, float]]]) -> tuple[wf.Panel, dict[str, list[date]]]:
    """The panel from a warehouse: adjusted closes (`adjusted(symbol)`), the close as quoted (`bars`) and close x volume as traded, plus, per symbol, the ex-dates of the corporate actions a price
    cannot be adjusted for (demerger, scheme, capital reduction). The old price floor is off (`min_price` 0). Only securities the warehouse holds prices for can be in the cohort."""
    from athena.trading_calendar import ist_date

    adj: dict[str, list[tuple[date, float]]] = {}
    raw: dict[str, dict[date, float]] = {}
    value: dict[str, dict[date, float]] = {}
    unadjustable: dict[str, list[date]] = {}
    for entry in warehouse.universe("stock"):
        symbol = entry["symbol"]
        series = list(adjusted(symbol))
        if not series:
            continue
        adj[symbol] = series
        bars = warehouse.bars(symbol)
        raw[symbol] = {ist_date(b.timestamp): float(b.close) for b in bars}
        value[symbol] = {ist_date(b.timestamp): float(b.close) * float(b.volume) for b in bars}
        dates = sorted(a.day for a in warehouse.actions(symbol) if a.kind in RESTRUCTURING_KINDS)
        if dates:
            unadjustable[symbol] = dates
    base = wf.Panel.build(adj)
    index = {d: k for k, d in enumerate(base.days)}

    def lay(source: Mapping[str, Mapping[date, float]]) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for symbol, by_day in source.items():
            arr = np.full(len(base.days), np.nan)
            for d, v in by_day.items():
                if d in index:
                    arr[index[d]] = v
            out[symbol] = arr
        return out

    return replace(base, raw=lay(raw), traded=lay(value), min_price=0.0), unadjustable
