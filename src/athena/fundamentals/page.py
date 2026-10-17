"""One company as the document the Fundamentals page draws: key figures, quarters, annual statements, ratios and growth, from the stored filings.

    python -m athena.fundamentals.page TCS --basis consolidated --price 3200

Rupee figures are given in crores; per-share figures, percentages, days and multiples as they are. A figure the filings do not give is `None`. Every
ratio row says how it is worked out. The price (when given) comes from the caller; this module reads no market data."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from typing import Any

from athena.contracts import AthenaError
from athena.fundamentals import history, ratios, shareholding
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, open_read

QUARTERS_SHOWN = 13
PLAIN = {"opm", "tax_pct", "eps"}  # percentages and per-share figures are not rupees

INCOME_ROWS = {
    "indas": (
        ("sales", "Sales"), ("expenses", "Expenses"), ("operating_profit", "Operating Profit"), ("opm", "OPM %"), ("other_income", "Other Income"),
        ("interest", "Interest"), ("depreciation", "Depreciation"), ("pbt", "Profit before tax"), ("tax_pct", "Tax %"), ("net_profit", "Net Profit"),
        ("eps", "EPS in Rs"),
    ),
    "bank": (
        ("sales", "Revenue"), ("interest", "Interest Expended"), ("operating_expenses", "Operating Expenses"), ("pre_provision_profit", "Pre-provision Profit"),
        ("provisions", "Provisions"), ("other_income", "Other Income"), ("pbt", "Profit before tax"), ("tax_pct", "Tax %"), ("net_profit", "Net Profit"),
        ("eps", "EPS in Rs"),
    ),
}
INCOME_ROWS["finance"] = INCOME_ROWS["indas"]
INCOME_ROWS["insurer"] = (
    ("gross_premium", "Gross Premium"), ("net_premium", "Net Premium"), ("investment_income", "Investment Income"), ("sales", "Total Income"),
    ("benefits_paid", "Benefits Paid"), ("actuarial_liability", "Change in Actuarial Liability"), ("commission", "Commission"),
    ("operating_expenses", "Operating Expenses"), ("total_expenses", "Total Expenses"), ("pbt", "Profit before tax"), ("tax_pct", "Tax %"),
    ("net_profit", "Net Profit"), ("eps", "EPS in Rs"),
)
BALANCE_ROWS = (
    ("equity_capital", "Equity Capital"), ("reserves", "Reserves"), ("deposits", "Deposits"), ("borrowings", "Borrowings"),
    ("other_liabilities", "Other Liabilities"), ("total_assets", "Total Assets"), ("fixed_assets", "Fixed Assets"), ("cwip", "CWIP"),
    ("investments", "Investments"), ("advances", "Advances"), ("loans", "Loans"), ("policyholders_funds", "Policyholders' Funds"),
    ("other_assets", "Other Assets"),
)
CASH_ROWS = (
    ("cfo", "Cash from Operating Activity"), ("cfi", "Cash from Investing Activity"), ("cff", "Cash from Financing Activity"),
    ("net_cash", "Net Cash Flow"), ("fcf", "Free Cash Flow"),
)
BANKING_ROWS = (("cet1", "CET1 Ratio %"), ("at1", "AT1 Ratio %"), ("roa", "Return on Assets %"), ("gross_npa", "Gross NPA %"))
RATIO_ROWS = (
    ("roe", "ROE %", "net profit / (equity capital + reserves)"),
    ("roce", "ROCE %", "EBIT / (total assets - current liabilities); EBIT = profit before tax + interest"),
    ("interest_cover", "Interest Cover", "EBIT / interest"),
    ("debt_to_equity", "Debt to Equity", "borrowings / (equity capital + reserves)"),
    ("debtor_days", "Debtor Days", "trade receivables / sales x 365"),
    ("inventory_days", "Inventory Days", "inventories / sales x 365"),
    ("days_payable", "Days Payable", "trade payables / sales x 365"),
    ("cash_conversion_cycle", "Cash Conversion Cycle", "debtor days + inventory days - payable days"),
    ("working_capital_days", "Working Capital Days", "(current assets - current liabilities) / sales x 365"),
    ("cfo_to_ebitda", "CFO / EBITDA %", "operating cash flow / operating profit"),
    ("credit_deposit", "Credit / Deposit %", "advances / deposits"),
)
HOLDING_ORDER = ("promoters", "fii", "dii", "government", "public", "mutual_funds", "insurance", "banks", "pension_funds", "retail", "employee_trusts")
GROWTH = (("sales", "Compounded Sales Growth"), ("net_profit", "Compounded Profit Growth"))


def _iso(day: date) -> str:
    return day.isoformat()


def _scale(key: str, value: float | None) -> float | None:
    if value is None:
        return None
    return float(value) if key in PLAIN else ratios.crore(value)


def _table(by_end: dict[date, dict[str, float]], ends: Sequence[date], rows: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """The rows that some period has, each with one value per period (None where missing)."""
    out = []
    for key, label in rows:
        values = [_scale(key, by_end.get(e, {}).get(key)) for e in ends]
        if any(v is not None for v in values):
            out.append({"key": key, "label": label, "values": values})
    return out


def _ttm_figures(layout: str, quarters: dict[date, dict[str, float]], keys: Sequence[str]) -> dict[str, float | None]:
    """The trailing-twelve-month figure of each income row. Rupee rows and EPS are sums of the latest four quarters; OPM is TTM operating profit over
    TTM sales (total income for a bank); tax % is TTM tax over TTM profit before tax."""
    out: dict[str, float | None] = {}
    for key in keys:
        if key == "opm":
            profit, base = ratios.ttm(quarters, "operating_profit"), ratios.ttm(quarters, "total_income" if layout == "bank" else "sales")
            out[key] = profit / base * 100 if profit is not None and base else None
        elif key == "tax_pct":
            tax, pbt = ratios.ttm(quarters, "tax"), ratios.ttm(quarters, "pbt")
            out[key] = tax / pbt * 100 if tax is not None and pbt else None
        else:
            out[key] = ratios.ttm(quarters, key)
    return out


def _ttm_growth(quarters: dict[date, dict[str, float]], key: str) -> float | None:
    """Trailing twelve months against the twelve months before: the latest four consecutive quarters over the four a year earlier, in percent."""
    ends = sorted(quarters, reverse=True)
    now_total = ratios.ttm(quarters, key)
    if now_total is None or len(ends) < 8:
        return None
    earlier = {e: quarters[e] for e in ends[4:]}
    if not 350 <= (ends[0] - ends[4]).days <= 380:
        return None
    before = ratios.ttm(earlier, key)
    if before is None or before <= 0 or now_total <= 0:
        return None
    return (now_total / before - 1) * 100


def _average(values: list[float | None], count: int) -> float | None:
    """The mean of the last `count` years' values, when every one of them is held."""
    last = values[-count:]
    if len(last) < count or any(v is None for v in last):
        return None
    return float(sum(last)) / count  # type: ignore[arg-type]


def _key_figures(layout: str, price: float | None, shares: float | None, quarters: dict, fy_income: dict, balance: dict[date, dict[str, float]], year: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    latest_balance = balance[max(balance)] if balance else {}
    if price is not None:
        out.append({"label": "Current Price", "value": price, "unit": "Rs", "how": "last close held by the terminal"})
        market = ratios.market_ratios(price, shares, quarters, fy_income, latest_balance)
        if "market_cap" in market:
            out.append({"label": "Market Cap", "value": ratios.crore(market["market_cap"]), "unit": "Rs Cr", "how": "price x shares outstanding"})
        if "pe" in market:
            out.append({"label": "Stock P/E", "value": market["pe"], "unit": "x", "how": f"market cap / net profit ({market['pe_period']})"})
        if "book_value" in market:
            out.append({"label": "Book Value", "value": market["book_value"], "unit": "Rs", "how": "(equity capital + reserves) / shares"})
        if "pb" in market:
            out.append({"label": "Price to Book", "value": market["pb"], "unit": "x", "how": "price / book value"})
        if "ev_ebitda" in market and layout == "indas":
            out.append({"label": "EV / EBITDA", "value": market["ev_ebitda"], "unit": "x", "how": "(market cap + borrowings - cash) / operating profit"})
    for key, label, how in RATIO_ROWS:
        if key in ("roe", "roce", "debt_to_equity") and key in year:
            out.append({"label": label, "value": year[key], "unit": "%" if key != "debt_to_equity" else "x", "how": f"{how} (latest financial year)"})
    return out


def _shareholding(held: dict[date, dict[str, tuple[float, float | None]]]) -> dict[str, Any]:
    """Each holder group's share of the company in percent, by quarter (oldest first), and the number of shareholders. A group a quarter does not
    report is None."""
    ends = sorted(held)[-QUARTERS_SHOWN:]
    rows: list[dict[str, Any]] = []

    def percent(end: date, key: str) -> float | None:
        total, group = held[end].get("total"), held[end].get(key)
        return group[0] / total[0] * 100 if total and total[0] and group else None

    for key in HOLDING_ORDER:
        values = [percent(e, key) for e in ends]
        if any(v is not None for v in values):
            rows.append({"key": key, "label": shareholding.LABELS[key], "values": values})
        if key == "public":  # the number of shareholders sits under the main groups
            holders = [held[e]["total"][1] if "total" in held[e] else None for e in ends]
            if any(v is not None for v in holders):
                rows.append({"key": "holders", "label": "No. of Shareholders", "values": holders})
    return {"ends": [_iso(e) for e in ends], "rows": rows}


def build_page(
    store: FundamentalsStore, symbol: str, basis: str, price: float | None, price_note: str = "", table: dict[str, tuple[history.Predecessor, ...]] | None = None
) -> dict[str, Any]:
    table = history.PREDECESSORS if table is None else table
    income = history.lines(store, symbol, basis, "income", table)
    balance_raw = history.lines(store, symbol, basis, "balance", table)
    if not income and not balance_raw:
        return {"symbol": symbol, "basis": basis, "error": "no statements stored"}
    company = store.company(symbol) or {}
    layout = company.get("layout") or "indas"
    fy_income = ratios.annual(income)
    quarters = ratios.quarterly(income)
    balance = {end: got for (_, end), got in balance_raw.items()}
    cash = ratios.annual(history.lines(store, symbol, basis, "cashflow", table))
    banking = ratios.annual(history.lines(store, symbol, basis, "banking", table))

    years = sorted(fy_income)
    q_ends = sorted(quarters)[-QUARTERS_SHOWN:]
    income_rows = INCOME_ROWS.get(layout, INCOME_ROWS["indas"])
    annual_income = _table(fy_income, years, income_rows)
    ttm_values = _ttm_figures(layout, quarters, [key for key, _ in income_rows])
    for r in annual_income:
        r["ttm"] = _scale(r["key"], ttm_values.get(r["key"]))

    year_ratio = {e: ratios.year_ratios(layout, fy_income[e], balance.get(e, {}), cash.get(e, {})) for e in years}
    ratio_rows = []
    for key, label, how in RATIO_ROWS:
        values = [year_ratio[e].get(key) for e in years]
        if any(v is not None for v in values):
            ratio_rows.append({"key": key, "label": label, "values": values, "how": how})

    growth = []
    for key, title in GROWTH:
        rows = [{"label": f"{n} Years", "value": ratios.compounded(fy_income, key, n)} for n in (10, 5, 3)]
        rows.append({"label": "TTM", "value": _ttm_growth(quarters, key)})
        growth.append({"title": title, "rows": rows})
    roe_series = [year_ratio[e].get("roe") for e in years]
    growth.append({
        "title": "Return on Equity",
        "rows": [{"label": f"{n} Years", "value": _average(roe_series, n)} for n in (10, 5, 3)] + [{"label": "Last Year", "value": roe_series[-1] if roe_series else None}],
    })

    gaps = []
    for name, held in (("balance sheets", balance), ("cash flows", cash)):
        first = min((y for y in years if y in held), default=None)
        if years and first is not None and first > years[0]:
            gaps.append(f"{name} start at FY{first.year}; the earlier years are not held yet")
    if years and not any(r["key"] == "sales" for r in annual_income):
        gaps.append("this company files in a layout without revenue lines (insurers are not supported yet), so its statements are mostly empty")
    held = sorted({*income, *balance_raw})
    coverage = {"periods": len(held), "first": held[0][1], "last": held[-1][1]} if held else None
    last_year = year_ratio[years[-1]] if years else {}
    return {
        "symbol": symbol, "name": company.get("name") or symbol, "layout": layout, "basis": basis, "bases": history.bases(store, symbol, table), "history": history.used(store, symbol, basis, table),
        "as_of": _iso(max([*quarters, *fy_income])) if (quarters or fy_income) else None,
        "price": price, "price_note": price_note,
        "key": _key_figures(layout, price, company.get("shares"), quarters, fy_income, balance, last_year),
        "quarters": {"ends": [_iso(e) for e in q_ends], "rows": _table(quarters, q_ends, income_rows)},
        "annual": {
            "ends": [_iso(e) for e in years], "income": annual_income,
            "balance": _table(balance, years, BALANCE_ROWS), "cashflow": _table(cash, years, CASH_ROWS), "banking": _table(banking, years, BANKING_ROWS),
        },
        "ratios": {"ends": [_iso(e) for e in years], "rows": ratio_rows},
        "growth": growth,
        "shareholding": _shareholding(history.holdings(store, symbol, table)),
        "coverage": {"periods": coverage["periods"], "first": _iso(coverage["first"]), "last": _iso(coverage["last"])} if coverage else None,
        "gaps": gaps,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol")
    parser.add_argument("--basis", default="consolidated", choices=("consolidated", "standalone"))
    parser.add_argument("--price", type=float, default=None)
    parser.add_argument("--price-note", default="")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    try:
        with open_read(args.store) as store:
            doc = build_page(store, args.symbol.upper(), args.basis, args.price, args.price_note)
    except AthenaError as exc:
        doc = {"symbol": args.symbol.upper(), "basis": args.basis, "error": str(exc), "busy": True}
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
