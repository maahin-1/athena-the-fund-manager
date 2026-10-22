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
from athena.valuation import inputs, wacc

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
    return wacc.company_cost_of_capital(ref, company, ind.lookup(ref, symbol, today), today)


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.valuation", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fundamentals", default=str(FUNDAMENTALS))
    parser.add_argument("--reference", default=str(REFERENCE))
    parser.add_argument("--warehouse", default=str(WAREHOUSE))
    parser.add_argument("--basis", default="consolidated", choices=("consolidated", "standalone"))
    sub = parser.add_subparsers(dest="command", required=True)
    cost = sub.add_parser("cost")
    cost.add_argument("symbol")
    cost.add_argument("--price", type=float, default=None)
    cost.set_defaults(run=cmd_cost)
    sub.add_parser("audit").set_defaults(run=cmd_audit)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except AthenaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
