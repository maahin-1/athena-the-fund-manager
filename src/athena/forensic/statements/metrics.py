"""The measures. Each takes a company's annual series and returns one number with the years it rests on, or None when the statements do not allow it (too few consecutive years, a missing line, a loss
where a ratio of profits is meaningless). A measure never guesses: a gap is a None, not a zero.

Each carries the direction in which it is concerning (`bad`: "high" or "low"), what it measures, and the innocent explanations a reader should weigh first. The measures follow the literature on earnings
quality (Sloan 1996 on accruals; Beneish 1999 on the indices of manipulation: receivables, asset quality, sales growth) and ordinary forensic practice (cash flow against profit, other income, tax rate,
cash held beside debt). A full Beneish M-score needs cost of goods sold and selling expenses, which the statements held here do not carry, so it is not computed; the components that can be are.

All amounts are in rupees as filed; ratios do not depend on the unit."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from athena.forensic.statements.annuals import Annuals, Year

STATUTORY_TAX = 0.2517  # the base rate under section 115BAA, the rate most companies now pay


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    unit: str  # "x" (a ratio), "pct" (a share), "pp" (percentage points a year), "days"
    bad: str  # "high" or "low": the side that is concerning
    years_needed: int
    what: str
    innocent: tuple[str, ...]
    fn: Callable[[Annuals], tuple[float, dict[str, Any]] | None] = field(repr=False, compare=False, default=lambda a: None)


@dataclass(frozen=True)
class Value:
    key: str
    value: float
    series: dict[str, Any]  # the figures it rests on, by year end, in crore where they are amounts


def _g(mapping: Any, key: str) -> float | None:
    value = mapping.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _cr(value: float | None) -> float | None:
    return None if value is None else round(value / 1e7, 2)


def _cagr(first: float, last: float, periods: int) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or periods <= 0:
        return None
    return (last / first) ** (1.0 / periods) - 1.0


def _sums(years: list[Year], getter: Callable[[Year], float | None]) -> float | None:
    parts = [getter(y) for y in years]
    return None if any(p is None for p in parts) else float(sum(parts))  # type: ignore[arg-type]


def _inc(key: str) -> Callable[[Year], float | None]:
    return lambda y: _g(y.income, key)


def _bal(key: str) -> Callable[[Year], float | None]:
    return lambda y: _g(y.balance, key)


def _cf(key: str) -> Callable[[Year], float | None]:
    return lambda y: _g(y.cashflow, key)


def _series(years: list[Year], **getters: Callable[[Year], float | None]) -> dict[str, Any]:
    return {name: {y.fy_end.isoformat(): _cr(get(y)) for y in years} for name, get in getters.items()}


# --- the measures ---
def cfo_to_profit(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(3)
    if not ys:
        return None
    cfo, profit = _sums(ys, _cf("cfo")), _sums(ys, _inc("net_profit"))
    if cfo is None or profit is None or profit <= 0:
        return None
    return cfo / profit, {**_series(ys, cfo=_cf("cfo"), net_profit=_inc("net_profit")), "cfo_total_crore": _cr(cfo), "profit_total_crore": _cr(profit)}


def accruals_ratio(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(2)
    if not ys:
        return None
    profit, cfo, ta0, ta1 = _g(ys[1].income, "net_profit"), _g(ys[1].cashflow, "cfo"), _g(ys[0].balance, "total_assets"), _g(ys[1].balance, "total_assets")
    if None in (profit, cfo, ta0, ta1) or (ta0 + ta1) <= 0:  # type: ignore[operator]
        return None
    return (profit - cfo) / ((ta0 + ta1) / 2.0), {**_series(ys, net_profit=_inc("net_profit"), cfo=_cf("cfo"), total_assets=_bal("total_assets"))}  # type: ignore[operator]


def _growth_gap(key: str, a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(4)
    if not ys:
        return None
    first, last, s0, s1 = _g(ys[0].balance, key), _g(ys[-1].balance, key), _g(ys[0].income, "sales"), _g(ys[-1].income, "sales")
    item, base = _cagr(first, last, 3), _cagr(s0, s1, 3)  # type: ignore[arg-type]
    if item is None or base is None:
        return None
    return (item - base) * 100.0, {**_series(ys, **{key: _bal(key)}, sales=_inc("sales")), f"{key}_growth_pct_a_year": round(item * 100, 1), "sales_growth_pct_a_year": round(base * 100, 1)}


def receivables_vs_sales(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    return _growth_gap("receivables", a)


def inventory_vs_sales(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    return _growth_gap("inventory", a)


def payables_vs_sales(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    return _growth_gap("payables", a)


def profit_vs_sales_growth(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(4)
    if not ys:
        return None
    p0, p1, s0, s1 = _g(ys[0].income, "net_profit"), _g(ys[-1].income, "net_profit"), _g(ys[0].income, "sales"), _g(ys[-1].income, "sales")
    profit, sales = _cagr(p0, p1, 3), _cagr(s0, s1, 3)  # type: ignore[arg-type]
    if profit is None or sales is None:
        return None
    return (profit - sales) * 100.0, {**_series(ys, net_profit=_inc("net_profit"), sales=_inc("sales")), "profit_growth_pct_a_year": round(profit * 100, 1), "sales_growth_pct_a_year": round(sales * 100, 1)}


def other_income_share(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(3)
    if not ys:
        return None
    other, pbt = _sums(ys, _inc("other_income")), _sums(ys, _inc("pbt"))
    if other is None or pbt is None or pbt <= 0:
        return None
    return other / pbt * 100.0, _series(ys, other_income=_inc("other_income"), pbt=_inc("pbt"))


def effective_tax_rate(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(3)
    if not ys:
        return None
    tax, pbt = _sums(ys, _inc("tax")), _sums(ys, _inc("pbt"))
    if tax is None or pbt is None or pbt <= 0:
        return None
    return tax / pbt * 100.0, {**_series(ys, tax=_inc("tax"), pbt=_inc("pbt")), "statutory_base_rate_pct": round(STATUTORY_TAX * 100, 2)}


def additions_vs_capex(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(4)
    if not ys:
        return None
    capex, dep = _sums(ys[1:], _cf("capex")), _sums(ys[1:], _inc("depreciation"))
    fa0, fa1 = (_g(ys[0].balance, "fixed_assets"), _g(ys[-1].balance, "fixed_assets"))
    cw0_held, cw1_held = _g(ys[0].balance, "cwip"), _g(ys[-1].balance, "cwip")
    if (cw0_held is None) != (cw1_held is None):  # a line held in one year and not the other is a gap, not a zero: the change in it would be invented
        return None
    cw0, cw1 = cw0_held or 0.0, cw1_held or 0.0
    if capex is None or dep is None or fa0 is None or fa1 is None or capex <= 0:
        return None
    implied = (fa1 + cw1) - (fa0 + cw0) + dep
    return implied / capex, {**_series(ys, fixed_assets=_bal("fixed_assets"), cwip=_bal("cwip"), capex=_cf("capex"), depreciation=_inc("depreciation")), "implied_additions_crore": _cr(implied), "cash_capex_crore": _cr(capex)}


def cwip_share(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(3)
    if not ys:
        return None
    fa, cw = _g(ys[-1].balance, "fixed_assets"), _g(ys[-1].balance, "cwip")
    if fa is None or cw is None or fa + cw <= 0:
        return None
    return cw / (fa + cw) * 100.0, _series(ys, fixed_assets=_bal("fixed_assets"), cwip=_bal("cwip"))


def cash_beside_debt(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(2)
    if not ys:
        return None
    last = ys[-1]
    debt, ta = _g(last.balance, "borrowings"), _g(last.balance, "total_assets")
    held = [v for v in (_g(last.balance, "cash"), _g(last.balance, "bank_balances")) if v is not None]
    if debt is None or ta is None or ta <= 0 or debt / ta < 0.15 or not held:  # only meaningful for a company that borrows a lot, and when at least one cash line is held
        return None
    cash = sum(held)
    return cash / debt * 100.0, {**_series(ys, borrowings=_bal("borrowings"), cash=_bal("cash"), bank_balances=_bal("bank_balances"), total_assets=_bal("total_assets"))}


def implied_interest_rate(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(4)
    if not ys:
        return None
    interest = _sums(ys[1:], _inc("interest"))
    avg = [(_g(x.balance, "borrowings"), _g(y.balance, "borrowings")) for x, y in zip(ys, ys[1:])]
    if interest is None or any(p is None or q is None for p, q in avg):
        return None
    base = sum((p + q) / 2.0 for p, q in avg)  # type: ignore[operator]
    if base <= 0 or interest <= 0:
        return None
    return interest / base * 100.0, _series(ys, interest=_inc("interest"), borrowings=_bal("borrowings"))


def asset_quality_index(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(2)
    if not ys:
        return None

    def soft(y: Year) -> float | None:
        ca, fa, ta = _g(y.balance, "current_assets"), _g(y.balance, "fixed_assets"), _g(y.balance, "total_assets")
        return None if None in (ca, fa, ta) or ta <= 0 else 1.0 - (ca + fa) / ta  # type: ignore[operator]

    before, after = soft(ys[0]), soft(ys[1])
    if before is None or after is None or before <= 0:
        return None
    return after / before, _series(ys, current_assets=_bal("current_assets"), fixed_assets=_bal("fixed_assets"), total_assets=_bal("total_assets"))


def q4_profit_share(a: Annuals) -> tuple[float, dict[str, Any]] | None:
    ys = a.run(3)
    if not ys:
        return None
    shares, detail = [], {}
    for y in ys:
        q, fy = a.quarters.get(y.fy_end), _g(y.income, "net_profit")
        if q is None or fy is None or fy <= 0 or "net_profit" not in q:
            return None
        shares.append(q["net_profit"] / fy)
        detail[y.fy_end.isoformat()] = {"q4_profit_crore": _cr(q["net_profit"]), "year_profit_crore": _cr(fy)}
    return sum(shares) / len(shares) * 100.0, detail


MEASURES: tuple[Measure, ...] = (
    Measure("cfo_to_profit", "Operating cash flow against profit, 3 years", "x", "low", 3, "Cash the business generated from operations for each rupee of profit it reported over three years.",
            ("a fast-growing business ties cash up in stock and customers' dues", "a one-off tax or working-capital outflow", "profit includes a large non-cash gain"), cfo_to_profit),
    Measure("accruals_ratio", "Accruals over average assets (Sloan)", "x", "high", 2, "Profit not backed by cash, as a share of assets; high positive values are the classic low-quality-earnings sign.",
            ("heavy investment in working capital while growing", "a lumpy contract settled after year end"), accruals_ratio),
    Measure("receivables_vs_sales", "Receivables growth less sales growth, per year over 3 years", "pp", "high", 4, "How much faster customers' dues grew than sales.",
            ("longer credit terms to win a large customer", "sales weighted to the last months of the year", "a change in what is reported as receivables"), receivables_vs_sales),
    Measure("inventory_vs_sales", "Inventory growth less sales growth, per year over 3 years", "pp", "high", 4, "How much faster stock grew than sales.",
            ("stocking ahead of a launch or a price rise", "new capacity ramping up"), inventory_vs_sales),
    Measure("payables_vs_sales", "Payables growth less sales growth, per year over 3 years", "pp", "high", 4, "How much faster dues to suppliers grew than sales; stretching suppliers flatters operating cash flow.",
            ("better terms negotiated with suppliers", "a change in the mix of purchases"), payables_vs_sales),
    Measure("profit_vs_sales_growth", "Profit growth less sales growth, per year over 3 years", "pp", "high", 4, "Profit growing much faster than sales means margins widened.",
            ("operating leverage on a fixed cost base", "a commodity or input-price cycle", "a low starting base"), profit_vs_sales_growth),
    Measure("other_income_share", "Other income as a share of profit before tax, 3 years", "pct", "high", 3, "How much of the profit comes from outside the main business.",
            ("a cash-rich group earning interest", "a one-off gain on selling an asset"), other_income_share),
    Measure("effective_tax_rate", "Tax paid as a share of profit before tax, 3 years", "pct", "low", 3, "A tax rate far below the statutory one on growing profits.",
            ("a tax holiday or a deduction for a new plant", "carried-forward losses", "profit earned in a low-tax unit"), effective_tax_rate),
    Measure("additions_vs_capex", "Fixed assets added against cash capex, 3 years", "x", "high", 4, "Fixed assets (with work in progress) added each year, from the balance sheet, against the cash spent on capex.",
            ("an acquisition, a lease or a transfer of assets with no cash capex", "revaluation", "capex paid in an earlier year"), additions_vs_capex),
    Measure("cwip_share", "Work in progress as a share of fixed assets plus work in progress", "pct", "high", 3, "A high and persistent share means projects that are not yet earning, or costs parked on the balance sheet.",
            ("a large plant being built on schedule", "a project cycle just before commissioning"), cwip_share),
    Measure("cash_beside_debt", "Cash and bank balances as a share of borrowings, for a heavy borrower", "pct", "high", 2, "A company that borrows heavily and also holds large cash.",
            ("cash held for a known repayment or a regulatory need", "cash trapped in a subsidiary or a foreign unit"), cash_beside_debt),
    Measure("implied_interest_rate", "Interest paid over average borrowings, 3 years", "pct", "low", 4, "An interest rate far below what the market charges suggests borrowings are missing from the balance sheet or interest is capitalised.",
            ("cheap government or group funding", "interest capitalised into a project", "borrowings that fell during the year"), implied_interest_rate),
    Measure("asset_quality_index", "Asset quality index (Beneish)", "x", "high", 2, "The share of assets that are neither current assets nor fixed assets, this year against last; a rise means more costs may be sitting on the balance sheet as assets.",
            ("a large investment or acquisition", "a new loan to a group company"), asset_quality_index),
    Measure("q4_profit_share", "Share of the year's profit made in the last quarter, 3 years", "pct", "high", 3, "A fourth quarter that carries far more than a quarter of the year's profit.",
            ("a seasonal business", "year-end settlements and incentives"), q4_profit_share),
)
BY_KEY = {m.key: m for m in MEASURES}


def compute(a: Annuals) -> dict[str, Value]:
    """Every measure that the company's statements allow."""
    out: dict[str, Value] = {}
    for m in MEASURES:
        result = m.fn(a)
        if result is not None:
            out[m.key] = Value(m.key, round(result[0], 4), result[1])
    return out
