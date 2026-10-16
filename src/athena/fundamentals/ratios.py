"""Ratios and growth from the stored statements. Pure functions over plain dictionaries, all in rupees (percentages and days excepted). Each formula
is in its docstring and is shown beside the number on the page, so a difference from another site is explainable.

Period dictionaries are keyed `(start, end)` as in `Statements`; `annual` and `quarterly` re-key them by end date."""
from __future__ import annotations

from datetime import date
from typing import Any

DAYS = 365
CRORE = 10_000_000


def crore(value: float) -> float:
    return value / CRORE


def _length(period: tuple[date, date]) -> int:
    return (period[1] - period[0]).days


def annual(income: dict[tuple[date, date], dict[str, float]]) -> dict[date, dict[str, float]]:
    """Financial years (periods of 330 to 380 days), by year end."""
    return {p[1]: got for p, got in income.items() if 330 <= _length(p) <= 380}


def quarterly(income: dict[tuple[date, date], dict[str, float]]) -> dict[date, dict[str, float]]:
    """Quarters (periods of 80 to 100 days), by quarter end."""
    return {p[1]: got for p, got in income.items() if 80 <= _length(p) <= 100}


def ttm(quarters: dict[date, dict[str, float]], key: str) -> float | None:
    """Trailing twelve months: the sum of the latest four quarters, which must be consecutive (each 80 to 100 days after the one before)."""
    ends = sorted(quarters, reverse=True)[:4]
    if len(ends) < 4 or any(not 80 <= (a - b).days <= 100 for a, b in zip(ends, ends[1:])):
        return None
    values = [quarters[e].get(key) for e in ends]
    return None if None in values else float(sum(values))


def cagr(latest: float | None, earlier: float | None, years: int) -> float | None:
    """Compound annual growth, in percent: (latest / earlier) ^ (1 / years) - 1. None when either value is missing or not positive."""
    if latest is None or earlier is None or earlier <= 0 or latest <= 0:
        return None
    return ((latest / earlier) ** (1 / years) - 1) * 100


def compounded(annual_map: dict[date, dict[str, float]], key: str, years: int) -> float | None:
    """CAGR of `key` over `years` financial years ending at the latest year held; None when the year `years` earlier is not held."""
    ends = sorted(annual_map)
    if not ends:
        return None
    last = ends[-1]
    prior = next((e for e in ends if e.year == last.year - years and e.month == last.month), None)
    if prior is None:
        return None
    return cagr(annual_map[last].get(key), annual_map[prior].get(key), years)


def year_ratios(layout: str, income: dict[str, float], balance: dict[str, float], cashflow: dict[str, float]) -> dict[str, float]:
    """The ratios of one financial year from its income, closing balance sheet and cash flow.

    EBIT = profit before tax + interest. ROCE = EBIT / (total assets - current liabilities). ROE = net profit / (equity capital + reserves).
    Interest cover = EBIT / interest. Debt to equity = borrowings / (equity capital + reserves). Debtor, inventory and payable days = trade
    receivables, inventories, trade payables / sales x 365. Cash conversion cycle = debtor + inventory - payable days. Working-capital days =
    (current assets - current liabilities) / sales x 365. CFO to EBITDA = operating cash flow / operating profit. Banks: ROE and advances / deposits
    only; lenders: ROE and debt to equity only (working-capital measures do not describe them)."""
    out: dict[str, float] = {}
    equity = balance["equity_capital"] + balance["reserves"] if "equity_capital" in balance and "reserves" in balance else None
    net_profit, sales, pbt, interest = income.get("net_profit"), income.get("sales"), income.get("pbt"), income.get("interest")
    if equity and net_profit is not None:
        out["roe"] = net_profit / equity * 100
    if layout == "bank":
        if balance.get("advances") and balance.get("deposits"):
            out["credit_deposit"] = balance["advances"] / balance["deposits"] * 100
        return out
    if layout == "finance":
        if equity and balance.get("borrowings") is not None:
            out["debt_to_equity"] = balance["borrowings"] / equity
        return out
    ebit = pbt + (interest or 0.0) if pbt is not None else None
    if ebit is not None:
        out["ebit"] = ebit
    if "total_assets" in balance and "current_liabilities" in balance and ebit is not None:
        capital_employed = balance["total_assets"] - balance["current_liabilities"]
        if capital_employed:
            out["roce"] = ebit / capital_employed * 100
    if ebit is not None and interest:
        out["interest_cover"] = ebit / interest
    if equity and balance.get("borrowings") is not None:
        out["debt_to_equity"] = balance["borrowings"] / equity
    if sales:
        for key, source in (("debtor_days", "receivables"), ("inventory_days", "inventory"), ("days_payable", "payables")):
            if balance.get(source) is not None:
                out[key] = balance[source] / sales * DAYS
        if {"debtor_days", "inventory_days", "days_payable"} <= out.keys():
            out["cash_conversion_cycle"] = out["debtor_days"] + out["inventory_days"] - out["days_payable"]
        if balance.get("current_assets") is not None and balance.get("current_liabilities") is not None:
            out["working_capital_days"] = (balance["current_assets"] - balance["current_liabilities"]) / sales * DAYS
    if "fcf" in cashflow:
        out["fcf"] = cashflow["fcf"]
    if cashflow.get("cfo") is not None and income.get("operating_profit"):
        out["cfo_to_ebitda"] = cashflow["cfo"] / income["operating_profit"] * 100
    return out


def market_ratios(
    price: float, shares: float | None, quarters: dict[date, dict[str, float]], fy_income: dict[date, dict[str, float]], balance: dict[str, float]
) -> dict[str, Any]:
    """The price-dependent figures. Market cap = price x shares. P/E = market cap / TTM net profit (the latest year's when four consecutive quarters
    are not held; `pe_period` says which). Book value = (equity capital + reserves) / shares; P/B = price / book value. EV = market cap + borrowings -
    cash - other bank balances. EV/EBITDA = EV / TTM operating profit (the latest year's when there is no TTM). Empty when the share count is unknown."""
    if not shares:
        return {}
    out: dict[str, Any] = {"market_cap": price * shares}
    latest = max(fy_income) if fy_income else None
    profit, profit_period = ttm(quarters, "net_profit"), "TTM"
    operating = ttm(quarters, "operating_profit")
    if profit is None and latest is not None:
        profit, profit_period = fy_income[latest].get("net_profit"), f"FY{latest.year}"
    if operating is None and latest is not None:
        operating = fy_income[latest].get("operating_profit")
    if profit and profit > 0:
        out["pe"], out["pe_period"] = out["market_cap"] / profit, profit_period
    if "equity_capital" in balance and "reserves" in balance:
        book_value = (balance["equity_capital"] + balance["reserves"]) / shares
        out["book_value"] = book_value
        if book_value > 0:
            out["pb"] = price / book_value
    if "borrowings" in balance:
        out["ev"] = out["market_cap"] + balance["borrowings"] - balance.get("cash", 0.0) - balance.get("bank_balances", 0.0)
        if operating and operating > 0:
            out["ev_ebitda"] = out["ev"] / operating
    return out
