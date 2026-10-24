"""The analysts' statements from the exchange filings.

The fundamentals packet (`athena.metrics.fundamentals`) is computed in code from a payload of statement lines by year and quarter. The payload used to be Yahoo's
only; this module builds the same shape from the filings held in the fundamentals store, with the share count chosen from every filing's own record, so the
valuation, quality and earnings analysts read the exchange's figures. A line the filings do not give is left out (the packet then lists the figure as missing
with its reason); nothing is filled in."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from athena.fundamentals import history, ratios
from athena.fundamentals.lines import BANK, FINANCE, INSURER
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, open_read

SOURCE = "computed from NSE XBRL results filings (INR)"
LENDER_LAYOUTS = (BANK, FINANCE, INSURER)
FINANCIAL_SECTOR = "Financial Services"

# packet line name <- the stored income line (a lender's revenue is its total income when the filing gives it)
INCOME_LINES = (("Net Income", "net_profit"), ("Diluted EPS", "eps"), ("Operating Income", "operating_profit"), ("Interest Expense", "interest"))


def _iso(day: date) -> str:
    return day.isoformat()


def _put(into: dict[str, dict[str, float]], name: str, day: date, value: float | None) -> None:
    if value is not None:
        into.setdefault(name, {})[_iso(day)] = float(value)


def _income(rows: Mapping[date, Mapping[str, float]], lender: bool) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for day, got in rows.items():
        _put(out, "Total Revenue", day, got.get("total_income") if lender and got.get("total_income") is not None else got.get("sales"))
        for name, key in INCOME_LINES:
            _put(out, name, day, got.get(key))
        if "pbt" in got and "interest" in got:
            _put(out, "EBIT", day, got["pbt"] + got["interest"])
    return out


def _balance(rows: Mapping[date, Mapping[str, float]], year_ends: set[date]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for day, got in rows.items():
        if day not in year_ends:  # half-year balance sheets are not fiscal-year ones
            continue
        if "equity_capital" in got and "reserves" in got:
            _put(out, "Stockholders Equity", day, got["equity_capital"] + got["reserves"])
        _put(out, "Total Debt", day, got.get("borrowings"))
        _put(out, "Current Assets", day, got.get("current_assets"))
        _put(out, "Current Liabilities", day, got.get("current_liabilities"))
        _put(out, "Total Assets", day, got.get("total_assets"))
    return out


def _cashflow(cash: Mapping[date, Mapping[str, float]], fy: Mapping[date, Mapping[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for day, got in cash.items():
        _put(out, "Operating Cash Flow", day, got.get("cfo"))
        _put(out, "Capital Expenditure", day, got.get("capex"))
        _put(out, "Depreciation And Amortization", day, fy.get(day, {}).get("depreciation"))
    return out


def payload_from_store(
    store: FundamentalsStore, symbol: str, basis: str | None = None, table: Mapping[str, tuple[history.Predecessor, ...]] | None = None
) -> tuple[dict[str, Any], datetime] | None:
    """`(payload, when the store last read the exchange for the company)` for the packet builder, or None when the store holds no income statement for the symbol.

    The basis is consolidated when the company files one, else the first it has. A renamed company's earlier symbols are read through `history`. The share count
    is the one the store chose (`athena.fundamentals.shares`); `currentPrice` is left out, so the packet does not cross-check against a price it was not given."""
    held = store.bases(symbol)
    if not held:
        return None
    basis = basis if basis in held else "consolidated" if "consolidated" in held else held[0]
    table = history.PREDECESSORS if table is None else table
    income = history.lines(store, symbol, basis, "income", table)
    if not income:
        return None
    company = store.company(symbol) or {}
    layout = company.get("layout") or "indas"
    lender = layout in LENDER_LAYOUTS
    fy, quarters = ratios.annual(income), ratios.quarterly(income)
    balance = {end: got for (_, end), got in history.lines(store, symbol, basis, "balance", table).items()}
    cash = ratios.annual(history.lines(store, symbol, basis, "cashflow", table))
    info: dict[str, Any] = {"financialCurrency": "INR"}
    if lender:
        info["sector"] = FINANCIAL_SECTOR
    if company.get("shares"):
        info["sharesOutstanding"] = float(company["shares"])
    payload = {
        "info": info,
        "annual": {"income": _income(fy, lender), "balance": _balance(balance, set(fy)), "cashflow": _cashflow(cash, fy)},
        "quarterly": {"income": _income(quarters, lender)},
        "earnings_dates": [],
        "source": SOURCE,
        "quality_flags": [] if company.get("shares") else ["no share count is held, so per-share figures and the market value are missing"],
    }
    read = company.get("updated") or store.last_filed(symbol, basis) or datetime.min
    return payload, read if read.tzinfo else read.replace(tzinfo=timezone.utc)


def packet_filings(path: str | Path = DEFAULT_PATH) -> Callable[[str], tuple[dict[str, Any], datetime] | None] | None:
    """A `symbol -> (payload, read time)` function for `build_packets`, reading the fundamentals store; None when there is no store yet. The store is opened for each
    call and closed again, so the fetch jobs that write to it are not held up, and a store that is busy or unreadable for one symbol gives None for it."""
    if not Path(path).exists():
        return None

    def find(symbol: str) -> tuple[dict[str, Any], datetime] | None:
        try:
            with open_read(path) as store:
                return payload_from_store(store, symbol)
        except Exception:
            return None

    return find
