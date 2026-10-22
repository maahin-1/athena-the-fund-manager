"""A company's figures for valuation, read from the stored exchange filings.

Money is in rupees crore, shares in units, rates as fractions. A flow (sales, operating profit, interest...) is the trailing twelve months when four consecutive
quarters are held, else the latest financial year, and `basis` says which. EBIT here is operating profit before depreciation less depreciation: the profit of the
business itself, without other income, as Damodaran's operating income is. Every figure the filings do not give is None and is named in `missing`; nothing is filled in."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from athena.fundamentals import history, ratios
from athena.fundamentals.store import FundamentalsStore

EFFECTIVE_TAX_RANGE = (0.0, 0.5)  # an effective rate outside this is a one-off, not a rate


@dataclass(frozen=True)
class CompanyInputs:
    symbol: str
    basis: str
    layout: str
    period_end: date  # the latest period the figures run to
    flow_basis: str  # "TTM" or "FYyyyy"
    price: float | None
    shares: float | None
    revenue: float | None
    operating_profit: float | None  # before depreciation
    depreciation: float | None
    ebit: float | None
    interest: float | None
    pbt: float | None
    tax: float | None
    net_profit: float | None
    effective_tax_rate: float | None
    revenue_cagr_3y: float | None
    book_equity: float | None
    borrowings: float | None
    cash_and_bank: float | None
    investments: float | None
    minority: float | None
    capex: float | None
    market_cap: float | None  # crore
    invested_capital: float | None  # book equity + borrowings - cash and bank balances
    missing: tuple[str, ...] = field(default_factory=tuple)


def _flow(quarters: dict[date, dict[str, float]], fy: dict[date, dict[str, float]], key: str) -> tuple[float | None, str]:
    value = ratios.ttm(quarters, key)
    if value is not None:
        return value, "TTM"
    if fy:
        latest = max(fy)
        return fy[latest].get(key), f"FY{latest.year}"
    return None, ""


def _crore(value: float | None) -> float | None:
    return None if value is None else ratios.crore(value)


def load_company_inputs(
    store: FundamentalsStore, symbol: str, basis: str = "consolidated", price: float | None = None, table: dict[str, tuple[history.Predecessor, ...]] | None = None
) -> CompanyInputs | None:
    """The company's valuation inputs, or None when no statements are stored."""
    table = history.PREDECESSORS if table is None else table
    income = history.lines(store, symbol, basis, "income", table)
    balance_raw = history.lines(store, symbol, basis, "balance", table)
    if not income and not balance_raw:
        return None
    company: dict[str, Any] = store.company(symbol) or {}
    fy = ratios.annual(income)
    quarters = ratios.quarterly(income)
    balance = {end: got for (_, end), got in balance_raw.items()}
    cash = ratios.annual(history.lines(store, symbol, basis, "cashflow", table))
    latest_balance = balance[max(balance)] if balance else {}

    values: dict[str, float | None] = {}
    bases: set[str] = set()
    for key in ("sales", "operating_profit", "depreciation", "interest", "pbt", "tax", "net_profit"):
        values[key], used = _flow(quarters, fy, key)
        if used:
            bases.add(used)
    flow_basis = "TTM" if bases == {"TTM"} else ", ".join(sorted(bases)) if bases else ""
    ebit = values["operating_profit"] - values["depreciation"] if values["operating_profit"] is not None and values["depreciation"] is not None else None
    pbt, tax = values["pbt"], values["tax"]
    rate = tax / pbt if pbt and pbt > 0 and tax is not None else None
    effective = rate if rate is not None and EFFECTIVE_TAX_RANGE[0] <= rate <= EFFECTIVE_TAX_RANGE[1] else None
    equity = latest_balance["equity_capital"] + latest_balance["reserves"] if "equity_capital" in latest_balance and "reserves" in latest_balance else None
    bank = (latest_balance.get("cash", 0.0) or 0.0) + (latest_balance.get("bank_balances", 0.0) or 0.0) if ("cash" in latest_balance or "bank_balances" in latest_balance) else None
    borrowings = latest_balance.get("borrowings")
    shares = company.get("shares") or None
    capex = cash[max(cash)].get("capex") if cash else None
    latest_period = max([*quarters, *fy, *balance], default=None)
    if latest_period is None:
        return None
    market_cap = _crore(price * shares) if price is not None and shares else None
    invested = equity + borrowings - bank if equity is not None and borrowings is not None and bank is not None else None

    figures: dict[str, Any] = {
        "revenue": _crore(values["sales"]), "operating_profit": _crore(values["operating_profit"]), "depreciation": _crore(values["depreciation"]), "ebit": _crore(ebit),
        "interest": _crore(values["interest"]), "pbt": _crore(pbt), "tax": _crore(tax), "net_profit": _crore(values["net_profit"]),
        "book_equity": _crore(equity), "borrowings": _crore(borrowings), "cash_and_bank": _crore(bank), "investments": _crore(latest_balance.get("investments")),
        "minority": _crore(latest_balance.get("minority")), "capex": _crore(capex), "invested_capital": _crore(invested),
    }
    required = {"price": price, "shares": shares, "effective_tax_rate": effective, **{k: figures[k] for k in ("revenue", "ebit", "interest", "book_equity", "borrowings", "cash_and_bank")}}
    missing = tuple(name for name, value in required.items() if value is None)
    return CompanyInputs(
        symbol=symbol, basis=basis, layout=company.get("layout") or "indas", period_end=latest_period, flow_basis=flow_basis, price=price, shares=shares,
        revenue=figures["revenue"], operating_profit=figures["operating_profit"], depreciation=figures["depreciation"], ebit=figures["ebit"], interest=figures["interest"],
        pbt=figures["pbt"], tax=figures["tax"], net_profit=figures["net_profit"], effective_tax_rate=effective, revenue_cagr_3y=ratios.compounded(fy, "sales", 3),
        book_equity=figures["book_equity"], borrowings=figures["borrowings"], cash_and_bank=figures["cash_and_bank"], investments=figures["investments"],
        minority=figures["minority"], capex=figures["capex"], market_cap=market_cap, invested_capital=figures["invested_capital"], missing=missing,
    )
