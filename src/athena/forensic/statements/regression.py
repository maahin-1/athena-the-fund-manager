"""Continuous version of the cross-sectional test of the statement measures (design spec 6b to 6h): does a company's excess return over the benchmark fall as the number of elevated measures rises?

The binary tests ask whether a bad outcome (a fall of 30, 50 or 70 percent) is more common among companies with several elevated measures. This asks the same of the whole return, with no cut-off: the slope of the
excess return on the number of elevated measures, holding fixed the fifth of size, the third of volatility and how many of the 14 measures could be computed at all. It reads the saved result of a backtest run
(`python -m athena.forensic.statements.backtest --out FILE`), so it adds no new data, and it changes no weight or band.

    python -m athena.forensic.statements.regression FILE [FILE ...] [--out PATH]

**Fixed before any result was read (Nov 9, 2026).** The expectation is a negative slope. Variant B (excess return winsorised at the 1st and 99th percentile, because a single stock rose more than thirteen
times in one window) is the primary; variant C (the percentile rank of the excess return, which no outlier can move) is the check; variant A (the raw return) is shown and not read. A date is *supported* when
the 95 percent bootstrap interval of the slope lies wholly below zero in both B and C; *opposite* when it lies wholly above zero in both; otherwise *not shown*. The claim is replicated only if both dates are
supported. The two windows overlap by fifteen months and the companies are the same, so they are not independent, and the result is a second look, not a second proof. The smallest slope the sample could reliably
see (80 percent power, 5 percent two-sided) is reported as 2.8 bootstrap standard errors, so a null reads as "nothing larger than this", not "nothing"."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

DRAWS = 2000
SEED = 11
WINSOR = 0.01
POWER_FACTOR = 2.8  # (1.96 + 0.84): the effect that would be seen 80 percent of the time at a 5 percent two-sided level
MIN_ROWS = 200


def cov_of(computed: int) -> int:
    """How many of the 14 measures could be computed: 0 for up to 3, 1 for 4 to 7, 2 for 8 or more (the same bins as the binary test)."""
    return 0 if computed <= 3 else 1 if computed <= 7 else 2


def load(path: Path | str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the run's header, its usable rows) from a saved backtest result. A row is usable with an excess return, a volatility third, and no unexplained price break in its window (an unrecorded split would read as a collapse)."""
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [r for r in result["rows"] if r.get("excess_return") is not None and r.get("vol_third") is not None and not r.get("unexplained_break")]
    return {"as_of": result["as_of"], "end": result["end"]}, rows


def design(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[str]]:
    """The columns: a constant, the number of elevated measures, two coverage dummies, four size dummies and two volatility dummies (the first of each is the base)."""
    cols = [np.ones(len(rows)), np.array([float(r["elevated"]) for r in rows])]
    names = ["const", "elevated"]
    for label, key, levels in (("cov", None, (1, 2)), ("size", "size_fifth", (1, 2, 3, 4)), ("vol", "vol_third", (1, 2))):
        for level in levels:
            if key is None:
                col = np.array([1.0 if cov_of(int(r["computed"])) == level else 0.0 for r in rows])
            else:
                col = np.array([1.0 if int(r[key]) == level else 0.0 for r in rows])
            cols.append(col)
            names.append(f"{label}{level}")
    return np.column_stack(cols), names


def winsorise(y: np.ndarray, p: float = WINSOR) -> np.ndarray:
    lo, hi = np.quantile(y, [p, 1 - p])
    return np.clip(y, lo, hi)


def ranks(y: np.ndarray) -> np.ndarray:
    """Percentile rank 0 to 1 (ties share the mean rank)."""
    order = y.argsort(kind="mergesort")
    sorted_y = y[order]
    r = np.empty(len(y))
    i = 0
    while i < len(y):
        j = i
        while j + 1 < len(y) and sorted_y[j + 1] == sorted_y[i]:
            j += 1
        r[order[i: j + 1]] = (i + j) / 2.0  # tied values share the mean of their positions
        i = j + 1
    return r / (len(y) - 1)


def slope(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.linalg.lstsq(x, y, rcond=None)[0][1])


def fit_variant(x: np.ndarray, y: np.ndarray, draws: int = DRAWS, seed: int = SEED, transform: Any = None) -> dict[str, Any]:
    """The slope on the number of elevated measures with a percentile bootstrap interval (rows resampled with replacement; a transform of the outcome such as winsorising is redone inside each draw)."""
    rng = np.random.default_rng(seed)
    n = len(y)
    point = slope(x, transform(y) if transform else y)
    boots = np.empty(draws)
    for i in range(draws):
        idx = rng.integers(0, n, n)
        yy = y[idx]
        boots[i] = slope(x[idx], transform(yy) if transform else yy)
    lo, hi = np.quantile(boots, [0.025, 0.975])
    se = float(boots.std(ddof=1))
    return {"slope": round(point, 5), "ci95": [round(float(lo), 5), round(float(hi), 5)], "bootstrap_se": round(se, 5), "smallest_slope_seen": round(POWER_FACTOR * se, 5)}


def by_group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The plain picture beside the model: companies, median and mean excess return by number of elevated measures (3 and above together)."""
    out: dict[str, Any] = {}
    for label, pick in (("0", lambda k: k == 0), ("1", lambda k: k == 1), ("2", lambda k: k == 2), ("3+", lambda k: k >= 3)):
        ys = np.array([r["excess_return"] for r in rows if pick(int(r["elevated"]))])
        out[label] = {"companies": int(len(ys)), "median": round(float(np.median(ys)), 4) if len(ys) else None, "mean_winsorised": round(float(winsorise(ys).mean()), 4) if len(ys) else None}
    return out


def reading(b: Mapping[str, Any], c: Mapping[str, Any]) -> str:
    """The rule fixed above: supported when both intervals are wholly below zero, opposite when both are wholly above, otherwise not shown."""
    if b["ci95"][1] < 0 and c["ci95"][1] < 0:
        return "supported at this date"
    if b["ci95"][0] > 0 and c["ci95"][0] > 0:
        return "opposite at this date"
    return "not shown at this date"


def regress(rows: Sequence[Mapping[str, Any]], draws: int = DRAWS, seed: int = SEED) -> dict[str, Any]:
    if len(rows) < MIN_ROWS:
        return {"companies": len(rows), "reading": f"not computable: {len(rows)} usable companies, at least {MIN_ROWS} are needed"}
    x, names = design(rows)
    y = np.array([float(r["excess_return"]) for r in rows])
    a = fit_variant(x, y, draws, seed)
    b = fit_variant(x, y, draws, seed, winsorise)
    c = fit_variant(x, y, draws, seed, ranks)
    return {"companies": len(rows), "controls": names[2:], "A_raw": a, "B_winsorised_primary": b, "C_rank_check": c, "by_elevated": by_group(rows), "reading": reading(b, c),
            "unit": "slope = change in excess return (a fraction: 0.01 is one percentage point) per additional elevated measure; for the rank variant, change in percentile rank (0 to 1) per measure"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", help="saved backtest results, one per entry date")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    results = []
    for f in args.files:
        header, rows = load(f)
        res = regress(rows)
        res["as_of"], res["end"] = header["as_of"], header["end"]
        rich = [r for r in rows if int(r["computed"]) >= 8]
        res["at_least_8_measures_computed"] = regress(rich)
        results.append(res)
    verdicts = [r["reading"] for r in results]
    out = {"results": results, "replicated": all(v == "supported at this date" for v in verdicts) and len(verdicts) >= 2,
           "caution": "The windows overlap and the companies are the same, so two dates are not two independent tests. A slope that is not shown is not a slope of zero: see smallest_slope_seen."}
    text = json.dumps(out, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
