"""The analyst scorecard: what each analyst's calls were worth, from the point-in-time run logs.

    python -m athena.evaluation.scorecard .athena/pit_runs/wh_fund.jsonl [more logs] [--out .athena/reports/scorecard.json]

A call is a signal (bullish, neutral, bearish) with a confidence; its outcome is the stock's return in excess of the benchmark over the horizon, as the log holds it. For each analyst and
horizon the card gives: the hit rate of the calls that took a side against the share of all cases that beat the benchmark; the average excess return of the calls by rating, and the spread
between bullish and bearish ones (the slugging that hit rate hides); the rank correlation of the signed confidence with the excess return on each date, its average and a t-statistic over the
dates; and the cases behind each figure. Nothing here says an analyst has skill: it says how big the evidence is, and `verdict` says so in words. Dates a horizon apart overlap in the returns they cover and the same stocks recur, so a t-statistic
over dates is optimistic: read one above 2 as a lead to test on new data, not a finding."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from athena.valuation.calibrate import spearman

SIDE = {"bullish": 1, "bearish": -1, "neutral": 0}
MIN_NAMES = 8  # stocks on one date before that date's rank correlation counts
MIN_DATES = 6  # dates before an average correlation has a t-statistic
MIN_SIDE_CALLS = 30  # calls that took a side before a hit rate is read as anything
T_EDGE = 2.0  # |t| the average correlation needs before the card calls it evidence
REPORT_PATH = Path(".athena") / "reports" / "scorecard.json"


def cases(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One case per analyst, stock, date and horizon: repeated runs reduced to their most common signal (a tie is neutral) with the confidence averaged over the runs that gave it. Rows
    without an excess return, or where the analyst gave no signal, are left out (the analyst abstained)."""
    grouped: dict[tuple[str, str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("signal") in SIDE and r.get("excess") is not None:
            grouped[(r["specialist"], r["symbol"], r["as_of"], int(r["horizon"]))].append(r)
    out = []
    for (analyst, symbol, as_of, horizon), runs in grouped.items():
        signals = [r["signal"] for r in runs]
        top = max(signals.count(s) for s in set(signals))
        leaders = [s for s in set(signals) if signals.count(s) == top]
        signal = leaders[0] if len(leaders) == 1 else "neutral"
        confidence = statistics.fmean(int(r.get("confidence") or 0) for r in runs if r["signal"] == signal) if signal in signals else 0.0
        out.append({"analyst": analyst, "symbol": symbol, "as_of": as_of, "horizon": horizon, "signal": signal, "confidence": confidence, "excess": float(runs[0]["excess"])})
    return out


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def card(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The figures for one analyst at one horizon from its cases."""
    beat = [c["excess"] > 0 for c in selected]
    sides = [c for c in selected if SIDE[c["signal"]] != 0 and c["excess"] != 0]
    hits = [SIDE[c["signal"]] * c["excess"] > 0 for c in sides]
    by_rating = {s: [c["excess"] for c in selected if c["signal"] == s] for s in SIDE}
    spread = (_mean(by_rating["bullish"]) - _mean(by_rating["bearish"])) if by_rating["bullish"] and by_rating["bearish"] else None  # type: ignore[operator]
    dates: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for c in selected:
        dates[c["as_of"]].append(c)
    ics = []
    for day_cases in dates.values():
        scores = [SIDE[c["signal"]] * c["confidence"] for c in day_cases]
        if len(day_cases) >= MIN_NAMES and len(set(scores)) > 1:
            rho = spearman(scores, [c["excess"] for c in day_cases])
            if rho is not None and math.isfinite(rho):
                ics.append(rho)
    mean_ic = _mean(ics)
    t = mean_ic / (statistics.stdev(ics) / math.sqrt(len(ics))) if len(ics) >= MIN_DATES and statistics.stdev(ics) > 0 and mean_ic is not None else None
    return {
        "cases": len(selected), "dates": len(dates), "side_calls": len(sides), "hit_rate": _mean([float(h) for h in hits]), "base_rate": _mean([float(b) for b in beat]),
        "excess_by_rating": {s: _mean(v) for s, v in by_rating.items()}, "calls_by_rating": {s: len(v) for s, v in by_rating.items()}, "spread": spread,
        "ic_dates": len(ics), "mean_ic": mean_ic, "ic_t": t, "verdict": _verdict(len(sides), _mean([float(h) for h in hits]), _mean([float(b) for b in beat]), mean_ic, t, spread),
    }


def _verdict(side_calls: int, hit_rate: float | None, base_rate: float | None, mean_ic: float | None, t: float | None, spread: float | None) -> str:
    if side_calls < MIN_SIDE_CALLS or hit_rate is None:
        return f"too few calls that took a side ({side_calls}) to say anything"
    if t is None:
        return "too few dates for a t-statistic; the hit rate and spread are all there is, and they are noisy"
    if abs(t) >= T_EDGE and mean_ic is not None:
        if spread is None or (spread > 0) == (mean_ic > 0):
            return "the rank correlation is " + ("positive" if mean_ic > 0 else "negative") + f" and larger than chance at t = {t:.1f}"
        return f"the two measures disagree (rank correlation t = {t:.1f}, but the bullish-minus-bearish spread points the other way): not read as evidence of skill"
    return f"no evidence of skill: the rank correlation is within chance (t = {t:.1f})"


def build(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    all_cases = cases(rows)
    out: dict[str, Any] = {}
    for analyst in sorted({c["analyst"] for c in all_cases}):
        for horizon in sorted({c["horizon"] for c in all_cases if c["analyst"] == analyst}):
            out.setdefault(analyst, {})[str(horizon)] = card([c for c in all_cases if c["analyst"] == analyst and c["horizon"] == horizon])
    return out


def format_card(scorecard: Mapping[str, Any]) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    def signed(value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.1%}"

    lines = []
    for analyst, per in scorecard.items():
        for horizon, c in per.items():
            by = c["excess_by_rating"]
            ic = "n/a" if c["mean_ic"] is None else f"{c['mean_ic']:+.2f} over {c['ic_dates']} dates" + ("" if c["ic_t"] is None else f" (t {c['ic_t']:+.1f})")
            lines.append(
                f"{analyst} {horizon}d: {c['cases']} cases on {c['dates']} dates; hit rate {pct(c['hit_rate'])} of {c['side_calls']} (a coin: {pct(c['base_rate'])} beat the benchmark); "
                f"excess bullish {signed(by['bullish'])} / neutral {signed(by['neutral'])} / bearish {signed(by['bearish'])}, spread {signed(c['spread'])}; rank IC {ic}. {c['verdict']}."
            )
    return "\n".join(lines) or "no scored calls"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("logs", nargs="+", help="run logs (JSON lines with symbol, as_of, specialist, signal, confidence, horizon, excess)")
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args(argv)
    rows = [json.loads(line) for path in args.logs for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    scorecard = build(rows)
    print(format_card(scorecard))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scorecard, indent=1), encoding="utf-8")
    print(f"saved to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
