"""The calibration study: which alternatives to the live valuation rules hold up, scored by `calibrate` on past dates.

Method, fixed before any result was read:
  - one change at a time against the live rules, then three packages whose parts were chosen for a stated reason, not by search;
  - six past dates a half-year apart; four are the development set (variants are judged on them) and the last three are the holdout (a choice made on the development
    set is only believed if the holdout agrees);
  - the score is the rank correlation of ln(base / price) with the next six months' price return, set beside plain earnings yield and book-to-price."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from athena.fundamentals.store import FundamentalsStore
from athena.reference.store import ReferenceStore
from athena.valuation.calibrate import (
    LARGE_MARKET_CAP_CRORE, MIN_MARKET_CAP_CRORE, Variant, build_snapshot, forward_metrics, level_metrics, load_actions, run_variant, summarize_ic,
)
from athena.valuation.scenarios import Policy

DEV_DATES = (date(2022, 12, 30), date(2023, 6, 30), date(2023, 12, 29), date(2024, 6, 28))
HOLDOUT_DATES = (date(2024, 12, 31), date(2025, 6, 30), date(2025, 12, 31))
HORIZON = 6
SIGNAL = "model: ln(base/price)"


def variants() -> list[Variant]:
    v, p = Variant, Policy
    singles = [
        v("baseline", "the live rules"),
        v("erp-half-crp", "equity premium = mature + half the India country premium", erp_crp_share=0.5),
        v("erp-mature-only", "equity premium = the mature-market premium (no country premium)", erp_crp_share=0.0),
        v("no-rupee-conversion", "dollar rates used as rupee rates (inflation 2% both)", inflation_inr=0.02, inflation_usd=0.02),
        v("inflation-inr-5", "rupee inflation 5%", inflation_inr=0.05),
        v("beta-blume", "industry betas shrunk a third toward the average", beta_shrink=0.33),
        v("growth-company-only", "growth from the company's own three years", policy=p(growth_company_weight=1.0)),
        v("growth-company-75", "growth 75% company, 25% industry", policy=p(growth_company_weight=0.75)),
        v("growth-industry-only", "growth from the industry's expectation", policy=p(growth_company_weight=0.0)),
        v("margin-own", "the company's current margin held", policy=p(margin_rule="own")),
        v("margin-industry", "the margin moves fully to the industry's", policy=p(margin_rule="industry")),
        v("margin-persist", "an above-industry margin is not pulled down", policy=p(margin_rule="persist")),
        v("terminal-moat-half", "half the return on capital above the cost of capital lasts for ever", policy=p(terminal_excess_share=0.5)),
        v("terminal-moat-full", "all of it lasts for ever (return on capital capped at 30%)", policy=p(terminal_excess_share=1.0)),
        v("terminal-growth-5", "terminal growth 5% instead of the rupee risk-free rate", policy=p(terminal_growth=0.05)),
        v("minority-at-pb", "minority interests at book x industry price to book", policy=p(minority_at_industry_pb=True)),
        v("no-failure", "no failure value blended in (the rules before 25 Oct)", policy=p(failure=False)),
        v("failure-book", "failure proceeds tied to book capital instead of the going-concern value", policy=p(failure_tie="book")),
        v("failure-5y", "chance of failure over 5 years instead of 10", policy=p(failure_horizon=5)),
        v("lender-roe-company", "a lender's stable ROE = its own", policy=p(lender_stable_roe="company")),
        v("lender-roe-blend", "a lender's stable ROE = mean of its own and the industry's", policy=p(lender_stable_roe="blend")),
    ]
    packages = [
        v(
            "pkg-practitioner", "half country premium, Blume betas, margins persist, half the moat lasts, lenders blend, minorities at market",
            erp_crp_share=0.5, beta_shrink=0.33, policy=p(margin_rule="persist", terminal_excess_share=0.5, lender_stable_roe="blend", minority_at_industry_pb=True),
        ),
        v("pkg-quality-persists", "live cost of capital; margins persist and half the moat lasts; lenders blend", policy=p(margin_rule="persist", terminal_excess_share=0.5, lender_stable_roe="blend")),
        v("pkg-cost-only", "half country premium and Blume betas, rules unchanged", erp_crp_share=0.5, beta_shrink=0.33),
    ]
    return singles + packages


@dataclass
class StudyResult:
    dates: list[str]
    levels: dict[str, dict[str, dict[str, Any]]]  # variant -> date -> level metrics
    forward: dict[str, dict[str, dict[str, dict[str, dict[str, Any]]]]]  # variant -> date -> tier -> signal -> metrics
    excluded: dict[str, dict[str, int]]
    sizes: dict[str, int]


def run(
    fund: FundamentalsStore, ref: ReferenceStore, warehouse: Path, today: date, dates: Sequence[date] = DEV_DATES + HOLDOUT_DATES, chosen: Sequence[Variant] | None = None,
    horizon: int = HORIZON, log: Callable[[str], None] = print,
) -> StudyResult:
    chosen = list(variants() if chosen is None else chosen)
    actions = load_actions(warehouse)
    result = StudyResult([d.isoformat() for d in dates], defaultdict(dict), defaultdict(dict), {}, {})
    for day in dates:
        snapshot = build_snapshot(fund, ref, warehouse, day, today, (horizon,), actions)
        key = day.isoformat()
        result.excluded[key] = snapshot.excluded
        result.sizes[key] = len(snapshot.rows)
        log(f"{day}: {len(snapshot.rows)} companies; excluded {snapshot.excluded}")
        for variant in chosen:
            outs = run_variant(snapshot, variant, ref, today)
            result.levels[variant.name][key] = level_metrics(outs)
            result.forward[variant.name][key] = {
                "all": forward_metrics(outs, horizon, MIN_MARKET_CAP_CRORE), "large": forward_metrics(outs, horizon, LARGE_MARKET_CAP_CRORE),
            }
    result.levels = dict(result.levels)
    result.forward = dict(result.forward)
    return result


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def ic_summary(result: StudyResult, tier: str, dates: Sequence[str], signal: str = SIGNAL) -> dict[str, dict[str, float | None]]:
    """variant -> mean per-date information coefficient of `signal` over `dates`, its t statistic and the share of dates above zero."""
    out: dict[str, dict[str, float | None]] = {}
    for variant, by_date in result.forward.items():
        out[variant] = summarize_ic([by_date[d][tier][signal]["ic"] for d in dates if d in by_date and signal in by_date[d][tier]])
    return out



def format_table(result: StudyResult, tier: str = "all", dev: Sequence[str] = (), holdout: Sequence[str] = ()) -> str:
    """One line per variant: the rank correlation of the model signal with the next period's return on the development dates and on the holdout dates, with the t statistic and the
    share of dates above zero, and the median price over base value."""
    dev_ic, hold_ic = ic_summary(result, tier, dev), ic_summary(result, tier, holdout)
    f = lambda s: "   n/a" if s["mean"] is None else f"{s['mean']:+.3f} (t {('%+.1f' % s['t']) if s['t'] is not None else 'n/a'}, {s['positive']:.0%} up)"  # noqa: E731
    lines = [f"{'variant':24s} {'development ' + str(len(dev)) + ' dates':32s} {'holdout ' + str(len(holdout)) + ' dates':32s} median price/base"]
    for name in result.forward:
        levels = [result.levels[name][d].get("median_price_to_base") for d in result.dates if d in result.levels[name]]
        got = [x for x in levels if x is not None]
        mid = sorted(got)[len(got) // 2] if got else None
        lines.append(f"{name:24s} {f(dev_ic[name]):32s} {f(hold_ic[name]):32s} {'n/a' if mid is None else f'{mid:.2f}'}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import json

    from athena.fundamentals.store import DEFAULT_PATH as FUNDAMENTALS
    from athena.fundamentals.store import open_read
    from athena.reference.store import DEFAULT_PATH as REFERENCE
    from athena.valuation.cli import WAREHOUSE

    parser = argparse.ArgumentParser(description="python -m athena.valuation.study: run the calibration study and print the variants against each other")
    parser.add_argument("--horizon", type=int, default=HORIZON, help="months ahead the return is measured over")
    parser.add_argument("--out", default=str(Path(".athena") / "reports" / "calibration-study.json"))
    parser.add_argument("--only", action="append", help="a variant name to run (repeat); default all")
    args = parser.parse_args(argv)
    chosen = [v for v in variants() if not args.only or v.name in args.only or v.name == "baseline"]
    started = __import__("time").time()
    with open_read(str(FUNDAMENTALS)) as fund, ReferenceStore(str(REFERENCE), read_only=True) as ref:
        result = run(fund, ref, WAREHOUSE, date.today(), chosen=chosen, horizon=args.horizon)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"dates": result.dates, "levels": result.levels, "forward": result.forward, "excluded": result.excluded, "sizes": result.sizes}, default=str, indent=1), encoding="utf-8")
    dev = [d.isoformat() for d in DEV_DATES]
    hold = [d.isoformat() for d in HOLDOUT_DATES]
    print(f"\nmodel signal, ln(base/price), against the next {args.horizon} months' return (all stocks)\n" + format_table(result, "all", dev, hold))
    print(f"\nlarge companies\n" + format_table(result, "large", dev, hold))
    print(f"saved {args.out} {__import__('time').time() - started:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
