"""python -m athena.valuation cost SYMBOL | audit

    cost SYMBOL   the cost of capital for one company, with every input and where it came from
    audit         the cost of capital for every stored company: how many get one, what is missing for the rest, and how the results are spread"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb

from athena.contracts import AthenaError
from athena.fundamentals.store import DEFAULT_PATH as FUNDAMENTALS, open_read
from athena.reference.store import DEFAULT_PATH as REFERENCE, ReferenceStore
from athena.valuation import industry as ind
from athena.valuation import inputs, ledger, mix, scenarios, wacc

WAREHOUSE = Path(".athena") / "warehouse.duckdb"
SANE_COST_OF_CAPITAL = (0.06, 0.30)  # a rupee cost of capital outside this is listed for a look, not hidden


def prices(warehouse: Path) -> dict[str, float]:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return {s: c for s, c in con.execute("SELECT symbol, close FROM screen WHERE kind = 'stock' AND close IS NOT NULL").fetchall()}
    finally:
        con.close()


def one(fund, ref: ReferenceStore, symbol: str, price: float | None, basis: str, today: date) -> wacc.CompanyCapital | None:
    company = inputs.load_company_inputs(fund, symbol, basis, price)
    if company is None and basis == "consolidated":  # a company that files no consolidated results is valued on its standalone ones
        company = inputs.load_company_inputs(fund, symbol, "standalone", price)
    if company is None:
        return None
    prepared = mix.prepare(fund, ref, company, ind.lookup(ref, symbol, today), today)
    return wacc.company_cost_of_capital(ref, prepared.company, prepared.industry, today)


def cmd_cost(args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    today = date.today()
    price = args.price if args.price is not None else prices(Path(args.warehouse)).get(symbol)
    with open_read(args.fundamentals) as fund, ReferenceStore(args.reference, read_only=True) as ref:
        got = one(fund, ref, symbol, price, args.basis, today)
    if got is None:
        print(f"{symbol}: no statements stored", file=sys.stderr)
        return 2
    print(f"{symbol} on {today}: price {price}")
    if not got.ok:
        print("no cost of capital; missing: " + ", ".join(got.missing))
        return 1
    print(f"industry {got.industry}; {got.firm_kind} firm; rating {got.rating}; interest cover {got.interest_cover}")
    print(f"beta {got.unlevered_beta:.3f} unlevered, {got.levered_beta:.3f} levered; equity weight {got.equity_weight:.3f}")
    print(f"USD: cost of equity {got.usd.cost_of_equity:.4f}  cost of debt {got.usd.pre_tax_cost_of_debt:.4f}  capital {got.usd.cost_of_capital:.4f}")
    print(f"INR: cost of equity {got.inr.cost_of_equity:.4f}  cost of debt {got.inr.pre_tax_cost_of_debt:.4f}  capital {got.inr.cost_of_capital:.4f}"
          f"  (range {got.low_inr.cost_of_capital:.4f} to {got.high_inr.cost_of_capital:.4f})")
    print(f"risk-free: {got.riskfree_usd:.4f} USD, {got.riskfree_inr:.4f} INR")
    for line in (*got.assumptions, *got.flags):
        print("  - " + line)
    return 0


def value_one(fund, ref: ReferenceStore, symbol: str, price: float | None, basis: str, today: date) -> scenarios.ValuationRange | None:
    company = inputs.load_company_inputs(fund, symbol, basis, price)
    if company is None and basis == "consolidated":
        company = inputs.load_company_inputs(fund, symbol, "standalone", price)
    if company is None:
        return None
    prepared = mix.prepare(fund, ref, company, ind.lookup(ref, symbol, today), today)
    company, industry = prepared.company, prepared.industry
    capital = wacc.company_cost_of_capital(ref, company, industry, today)
    return scenarios.value_company(ref, company, industry, capital, today)


def cmd_value(args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    today = date.today()
    price = args.price if args.price is not None else prices(Path(args.warehouse)).get(symbol)
    with open_read(args.fundamentals) as fund, ReferenceStore(args.reference, read_only=True) as ref:
        got = value_one(fund, ref, symbol, price, args.basis, today)
    if got is None:
        print(f"{symbol}: no statements stored", file=sys.stderr)
        return 2
    print(f"{symbol} on {today}: price {price}; model {got.model}")
    if not got.ok:
        print("no value; " + "; ".join(got.missing))
        return 1
    ratio = "n/a (the base value is not positive)" if got.price_to_base is None else f"{got.price_to_base:.2f}"
    print(f"value per share: low {got.low:,.1f}  base {got.base:,.1f}  high {got.high:,.1f}   price/base {ratio}")
    for s in got.scenarios:
        print(f"  {s.name:5} {s.value_per_share:12,.1f}  growth {s.growth}  margin {s.target_margin}  cost {s.cost_of_capital}  roe {s.roe}")
    if got.implied_growth is not None:
        print(f"the price implies revenue growth of {got.implied_growth:.1%} a year for 5 years (company {got.company_growth}, industry {got.industry_growth})")
    if got.justified_price_to_book is not None:
        print(f"justified price to book {got.justified_price_to_book:.2f}")
    for line in (*got.assumptions, *got.flags):
        print("  - " + line)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    today = date.today()
    close = prices(Path(args.warehouse))
    done: list[float] = []
    why: Counter[str] = Counter()
    odd: list[tuple[str, float]] = []
    with open_read(args.fundamentals) as fund, ReferenceStore(args.reference, read_only=True) as ref:
        symbols = [r[0] for r in fund._con.execute("SELECT symbol FROM companies ORDER BY symbol").fetchall()]
        for symbol in symbols:
            got = one(fund, ref, symbol, close.get(symbol), args.basis, today)
            if got is None:
                why["no statements"] += 1
            elif not got.ok:
                why[", ".join(got.missing)] += 1
            else:
                value = got.inr.cost_of_capital
                done.append(value)
                if not SANE_COST_OF_CAPITAL[0] <= value <= SANE_COST_OF_CAPITAL[1]:
                    odd.append((symbol, value))
    print(f"companies: {len(symbols)}; with a cost of capital: {len(done)}")
    if done:
        q = statistics.quantiles(done, n=10)
        print(f"rupee cost of capital: min {min(done):.4f}  10% {q[0]:.4f}  median {statistics.median(done):.4f}  90% {q[8]:.4f}  max {max(done):.4f}")
    print(f"outside {SANE_COST_OF_CAPITAL[0]:.2f} to {SANE_COST_OF_CAPITAL[1]:.2f}: {len(odd)}  e.g. {sorted(odd, key=lambda t: t[1])[:5]}")
    print("why not:")
    for reason, count in why.most_common(12):
        print(f"  {count:5d}  {reason}")
    return 0


def cmd_audit_value(args: argparse.Namespace) -> int:
    today = date.today()
    close = prices(Path(args.warehouse))
    models: Counter[str] = Counter()
    why: Counter[str] = Counter()
    ratios_: list[float] = []
    wide: list[tuple[str, float]] = []
    with open_read(args.fundamentals) as fund, ReferenceStore(args.reference, read_only=True) as ref:
        symbols = [r[0] for r in fund._con.execute("SELECT symbol FROM companies ORDER BY symbol").fetchall()]
        for symbol in symbols:
            got = value_one(fund, ref, symbol, close.get(symbol), args.basis, today)
            if got is None:
                why["no statements"] += 1
            elif not got.ok:
                why[(got.missing[0] if got.missing else "unknown")[:90]] += 1
            else:
                models[got.model] += 1
                if got.price_to_base is not None:
                    ratios_.append(got.price_to_base)
                if got.high and got.low and got.base and got.base > 0 and (got.high - got.low) / got.base > 3.0:
                    wide.append((symbol, (got.high - got.low) / got.base))
    print(f"companies {len(symbols)}; valued {sum(models.values())} {dict(models)}")
    if ratios_:
        q = statistics.quantiles(ratios_, n=10)
        print(f"price / base value: 10% {q[0]:.2f}  25% {statistics.quantiles(ratios_, n=4)[0]:.2f}  median {statistics.median(ratios_):.2f}  75% {statistics.quantiles(ratios_, n=4)[2]:.2f}  90% {q[8]:.2f}")
    print(f"range wider than three times the base value: {len(wide)}")
    print("why not valued:")
    for reason, count in why.most_common(10):
        print(f"  {count:5d}  {reason}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    snap = date.fromisoformat(args.date) if args.date else date.today()
    prices = ledger.stored_prices(Path(args.warehouse))
    with open_read(args.fundamentals) as fund, ReferenceStore(args.reference, read_only=True) as ref, ledger.Ledger(args.ledger) as book:
        if book.has(snap):
            print(f"a snapshot for {snap} is already recorded; nothing done")
            return 0
        symbols = [r[0] for r in fund._con.execute("SELECT symbol FROM companies ORDER BY symbol").fetchall()]

        def value(symbol: str, price: float):
            company = inputs.load_company_inputs(fund, symbol, args.basis, price)
            basis = args.basis
            if company is None and args.basis == "consolidated":
                company, basis = inputs.load_company_inputs(fund, symbol, "standalone", price), "standalone"
            if company is None:
                return None
            prepared = mix.prepare(fund, ref, company, ind.lookup(ref, symbol, snap), snap)
            capital = wacc.company_cost_of_capital(ref, prepared.company, prepared.industry, snap)
            got = scenarios.value_company(ref, prepared.company, prepared.industry, capital, snap)
            return got, (prepared.industry.industry if prepared.industry else None), len(got.flags), basis

        recorded, total, stale = ledger.take_snapshot(book, value, symbols, prices, snap, note="monthly" if not args.date else "dated")
    print(f"snapshot {snap}: {recorded} value ranges recorded of {total} companies ({stale} left out for a missing or stale price)")
    return 0


def cmd_ledger_score(args: argparse.Namespace) -> int:
    with ledger.Ledger(args.ledger, read_only=True) as book:
        scores = ledger.score(book, Path(args.warehouse), args.months)
        held = book.snapshot_dates()
    if not scores:
        print(f"no snapshot is {args.months} months old yet ({len(held)} held: {', '.join(d.isoformat() for d in held) or 'none'})")
        return 0
    f = lambda v, p=3: "-" if v is None else f"{v:.{p}f}"  # noqa: E731
    print(f"{'snapshot':12} {'n':>5} {'rank IC':>8} {'inside':>7} {'above':>6} {'below':>6} {'gap then':>9} {'gap now':>8}")
    for s in scores:
        print(f"{s.snap_date.isoformat():12} {s.n:5d} {f(s.ic):>8} {f(s.inside, 2):>7} {f(s.above, 2):>6} {f(s.below, 2):>6} {f(s.median_gap_before, 2):>9} {f(s.median_gap_after, 2):>8}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.valuation", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fundamentals", default=str(FUNDAMENTALS))
    parser.add_argument("--reference", default=str(REFERENCE))
    parser.add_argument("--warehouse", default=str(WAREHOUSE))
    parser.add_argument("--ledger", default=str(ledger.DEFAULT_PATH))
    parser.add_argument("--basis", default="consolidated", choices=("consolidated", "standalone"))
    sub = parser.add_subparsers(dest="command", required=True)
    cost = sub.add_parser("cost")
    cost.add_argument("symbol")
    cost.add_argument("--price", type=float, default=None)
    cost.set_defaults(run=cmd_cost)
    value = sub.add_parser("value")
    value.add_argument("symbol")
    value.add_argument("--price", type=float, default=None)
    value.set_defaults(run=cmd_value)
    sub.add_parser("audit").set_defaults(run=cmd_audit)
    sub.add_parser("audit-value").set_defaults(run=cmd_audit_value)
    snap = sub.add_parser("snapshot", help="record every company's value range in the ledger")
    snap.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    snap.set_defaults(run=cmd_snapshot)
    scored = sub.add_parser("ledger-score", help="score the ledger's snapshots that are old enough")
    scored.add_argument("--months", type=int, default=12)
    scored.set_defaults(run=cmd_ledger_score)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except AthenaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
