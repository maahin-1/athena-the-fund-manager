"""One results filing as rows keyed by period. The exchange files three contexts: `OneD` the reporting quarter (in an annual filing, the last
quarter), `FourD` the year to date (the whole year in an annual filing) and `OneI` the balance sheet date; the true period of each is stated by
the `DateOfStartOfReportingPeriod` and `DateOfEndOfReportingPeriod` facts, not by the context's own dates. Every figure is in rupees."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from defusedxml import ElementTree

from athena.fundamentals import lines as spec

_XBRLI = "{http://www.xbrl.org/2003/instance}"
CURRENT, CUMULATIVE, INSTANT = "OneD", "FourD", "OneI"
RECONCILE_TOLERANCE = 0.005  # of sales


@dataclass(frozen=True)
class Statements:
    symbol: str
    basis: str  # "consolidated" | "standalone" | ""
    layout: str  # spec.INDAS | spec.FINANCE | spec.BANK
    audited: bool | None
    shares: float | None  # paid-up equity capital over face value
    income: dict[tuple[date, date], dict[str, float]]
    balance: dict[date, dict[str, float]]
    cashflow: dict[tuple[date, date], dict[str, float]]
    banking: dict[tuple[date, date], dict[str, float]]
    notes: tuple[str, ...]


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def _quarter_start(end: date) -> date:
    month, year = end.month - 2, end.year
    if month < 1:
        month, year = month + 12, year - 1
    return date(year, month, 1)


def _pick(facts: dict[str, str], line: spec.Line) -> float | None:
    """The line's value from one context's facts. "first": the first tag that is not zero (filers fill the tag they do not use with 0.00, and that
    must not hide the tag that holds the figure), a zero only when every tag present is zero; "sum": the total of the tags present."""
    if line.how == "sum":
        values = [v for tag in line.tags if (v := _number(facts.get(tag))) is not None]
        return sum(values) if values else None
    found = None
    for tag in line.tags:
        value = _number(facts.get(tag))
        if value:
            return value
        if value is not None:
            found = value
    return found


def _lines(facts: dict[str, str], table: tuple[spec.Line, ...]) -> dict[str, float]:
    return {line.key: v for line in table if (v := _pick(facts, line)) is not None}


def derive_income(got: dict[str, float], layout: str) -> dict[str, float]:
    """Operating expenses, operating profit, OPM % and tax % from the filed lines. For industrial and lender filings operating expenses are total
    expenses without interest and depreciation, operating profit is sales less those, and OPM is over sales. For banks, expenses are operating
    expenses (provisions are shown apart), operating profit is the filed profit before provisions and contingencies (interest earned plus other
    income less interest expended less operating expenses when the filing omits it) and OPM is that over total income. A consolidated bank with
    insurance subsidiaries has other income and expenses that are far larger than a lender's; the page says which basis it shows."""
    out = dict(got)
    sales = got.get("sales")
    base = sales  # what OPM is a percentage of
    if layout == spec.BANK:
        interest, opex = got.get("interest"), got.get("operating_expenses")
        if None not in (sales, interest, opex):
            other = got.get("other_income", 0.0)
            out["expenses"] = opex
            out["operating_profit"] = got.get("pre_provision_profit", sales + other - interest - opex)
            base = got.get("total_income", sales + other)
    else:
        total = got.get("total_expenses")
        if None not in (sales, total):
            out["expenses"] = total - got.get("interest", 0.0) - got.get("depreciation", 0.0)
            out["operating_profit"] = sales - out["expenses"]
    if base and "operating_profit" in out:
        out["opm"] = out["operating_profit"] / base * 100
    pbt, tax = got.get("pbt"), got.get("tax")
    if pbt and tax is not None:
        out["tax_pct"] = tax / pbt * 100
    return out


def derive_balance(got: dict[str, float]) -> dict[str, float]:
    """Other assets and other liabilities as the remainder of the total, as Screener shows them (minority interest sits in other liabilities)."""
    out = dict(got)
    total = got.get("total_assets")
    if total is None:
        return out
    out["other_assets"] = total - got.get("fixed_assets", 0.0) - got.get("cwip", 0.0) - got.get("investments", 0.0)
    funded = got.get("equity_capital", 0.0) + got.get("reserves", 0.0) + got.get("borrowings", 0.0) + got.get("deposits", 0.0)
    out["other_liabilities"] = total - funded
    return out


def derive_cashflow(got: dict[str, float], layout: str = spec.INDAS) -> dict[str, float]:
    """Free cash flow: operating cash flow less capital expenditure (our definition; the page says so). Not for banks (their operating cash flow
    is dominated by loans and deposits), and not when the filing's capital expenditure is zero, which is how filers leave a line they do not use."""
    out = dict(got)
    if layout != spec.BANK and "cfo" in got and got.get("capex", 0.0) > 0:
        out["fcf"] = got["cfo"] - got["capex"]
    return out


def reconcile(income: dict[tuple[date, date], dict[str, float]]) -> tuple[str, ...]:
    """A note for each period whose profit before tax does not follow from its lines (within half a percent of sales, with or without the
    exceptional items). A note is information, not an error: associates and one-offs can explain it."""
    notes = []
    for (_, end), got in income.items():
        sales, pbt = got.get("sales"), got.get("pbt")
        if sales is None or pbt is None:
            continue
        base = (
            sales + got.get("other_income", 0.0) - got.get("interest", 0.0) - got.get("depreciation", 0.0) - got.get("expenses", 0.0)
            - got.get("provisions", 0.0)
        )
        exceptional = got.get("exceptional", 0.0)
        if min(abs(pbt - base), abs(pbt - base - exceptional), abs(pbt - base + exceptional)) > RECONCILE_TOLERANCE * abs(sales):
            notes.append(f"{end}: profit before tax does not follow from the lines (difference {pbt - base:,.0f})")
    return tuple(notes)


def parse_rows(text: str) -> Statements:
    """A results filing as `Statements`. Raises ValueError for anything that is not one (the exchange answers with an error page for filings that
    predate XBRL)."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError(f"not an XBRL filing: {exc}") from exc
    dimensioned = {c.get("id") for c in root.iter(f"{_XBRLI}context") if c.find(f"{_XBRLI}scenario") is not None}
    by_context: dict[str, dict[str, str]] = {}
    for element in root:
        ref = element.get("contextRef")
        if ref is None or ref in dimensioned or not (element.text or "").strip():
            continue
        by_context.setdefault(ref, {})[element.tag.rsplit("}", 1)[-1]] = element.text.strip()
    current, whole, instant = (by_context.get(name, {}) for name in (CURRENT, CUMULATIVE, INSTANT))
    end = _day(current.get("DateOfEndOfReportingPeriod"))
    if end is None:
        raise ValueError("the filing does not say which period it covers")
    quarter = (_day(current.get("DateOfStartOfReportingPeriod")) or _quarter_start(end), end)
    year = (
        _day(whole.get("DateOfStartOfReportingPeriod")) or _day(current.get("DateOfStartOfFinancialYear")) or quarter[0],
        _day(whole.get("DateOfEndOfReportingPeriod")) or end,
    )
    present = set(current) | set(whole) | set(instant)
    if "InterestExpended" in present:
        layout = spec.BANK
    elif {"Loans", "DebtSecurities"} <= present:
        layout = spec.FINANCE
    else:
        layout = spec.INDAS

    income: dict[tuple[date, date], dict[str, float]] = {}
    for period, facts in ((quarter, current), (year, whole)):
        got = derive_income(_lines(facts, spec.INCOME_LINES[layout]), layout)
        if got and period not in income:  # the quarter's own context wins when both name one period
            income[period] = got
    balance_lines = derive_balance(_lines(instant, spec.BALANCE_LINES[layout]))
    balance = {end: balance_lines} if balance_lines else {}
    cash = derive_cashflow(_lines(whole, spec.CASHFLOW_LINES), layout)
    cashflow = {year: cash} if cash else {}
    banking: dict[tuple[date, date], dict[str, float]] = {}
    for period, facts in ((quarter, current), (year, whole)):
        got = {key: value for key, value in _lines(facts, spec.BANKING_LINES).items() if value}  # a filer's 0.00 means "not reported", not zero
        if got and period not in banking:
            banking[period] = got

    nature = (current.get("NatureOfReportStandaloneConsolidated") or "").strip().lower()
    audited = current.get("WhetherResultsAreAuditedOrUnaudited")
    paid_up, face_value = _number(current.get("PaidUpValueOfEquityShareCapital")), _number(current.get("FaceValueOfEquityShareCapital"))
    return Statements(
        symbol=(current.get("Symbol") or "").strip(),
        basis=nature if nature in ("consolidated", "standalone") else "",
        layout=layout,
        audited=None if audited is None else audited.strip().lower() == "audited",
        shares=paid_up / face_value if paid_up and face_value else None,
        income=income,
        balance=balance,
        cashflow=cashflow,
        banking=banking,
        notes=reconcile(income),
    )
