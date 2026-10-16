"""A stored company as text tables, the way Screener lays them out: annual columns, newest last, rows in its order, figures in crores.

    python -m athena.fundamentals.show TCS --basis consolidated"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date

from athena.fundamentals import ratios
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore

INCOME_ROWS = (
    ("sales", "Sales"), ("expenses", "Expenses"), ("operating_profit", "Operating Profit"), ("opm", "OPM %"), ("other_income", "Other Income"),
    ("interest", "Interest"), ("depreciation", "Depreciation"), ("pbt", "Profit before tax"), ("tax_pct", "Tax %"), ("net_profit", "Net Profit"),
    ("eps", "EPS in Rs"),
)
BALANCE_ROWS = (
    ("equity_capital", "Equity Capital"), ("reserves", "Reserves"), ("deposits", "Deposits"), ("borrowings", "Borrowings"),
    ("other_liabilities", "Other Liabilities"), ("total_assets", "Total Assets"), ("fixed_assets", "Fixed Assets"), ("cwip", "CWIP"),
    ("investments", "Investments"), ("advances", "Advances"), ("loans", "Loans"), ("other_assets", "Other Assets"),
)
CASH_ROWS = (
    ("cfo", "Cash from Operating Activity"), ("cfi", "Cash from Investing Activity"), ("cff", "Cash from Financing Activity"),
    ("net_cash", "Net Cash Flow"), ("fcf", "Free Cash Flow"),
)
PLAIN = {"opm", "tax_pct", "eps"}  # percentages and per-share figures are not rupees


def _cell(key: str, value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}" if key in PLAIN else f"{ratios.crore(value):,.0f}"


def _table(title: str, by_end: dict[date, dict[str, float]], rows: Sequence[tuple[str, str]]) -> list[str]:
    ends = sorted(by_end)
    present = [(key, label) for key, label in rows if any(key in by_end[e] for e in ends)]
    if not ends or not present:
        return [f"{title}: not available", ""]
    lines = [f"{title}  (Rs crore)", f"{'':<32}" + "".join(f"{e:%b %Y}".rjust(12) for e in ends)]
    for key, label in present:
        lines.append(f"{label:<32}" + "".join(_cell(key, by_end[e].get(key)).rjust(12) for e in ends))
    return [*lines, ""]


def render(store: FundamentalsStore, symbol: str, basis: str) -> str:
    income = store.lines(symbol, basis, "income")
    if not income and not store.lines(symbol, basis, "balance"):
        return f"{symbol} ({basis}): no statements stored"
    balance = {end: got for (_, end), got in store.lines(symbol, basis, "balance").items()}
    cash = {p[1]: got for p, got in store.lines(symbol, basis, "cashflow").items() if 330 <= (p[1] - p[0]).days <= 380}
    company = store.company(symbol) or {}
    out = [f"{symbol}  {company.get('name', '')}  [{basis}, layout {company.get('layout', '?')}]", ""]
    out += _table("Profit & Loss", ratios.annual(income), INCOME_ROWS)
    out += _table("Balance Sheet", balance, BALANCE_ROWS)
    out += _table("Cash Flows", cash, CASH_ROWS)
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--basis", default="consolidated", choices=("consolidated", "standalone"))
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    with FundamentalsStore(args.store, read_only=True) as store:
        print(render(store, args.symbol.upper(), args.basis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
