from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from defusedxml import ElementTree

_XBRLI = "{http://www.xbrl.org/2003/instance}"
CURRENT, CUMULATIVE, INSTANT = "OneD", "FourD", "OneI"  # the exchange's context ids: the quarter, the year to date, the balance sheet date

# canonical line (named as in the Yahoo payload) -> exchange tags to try, first one present wins. Ind-AS filers and banks use
# different tag sets. A line no tag gives is simply absent: a figure is never guessed.
INCOME_TAGS: dict[str, tuple[str, ...]] = {
    "Total Revenue": ("RevenueFromOperations", "Income"),  # banks: total income
    "Net Income": (
        "ProfitOrLossAttributableToOwnersOfParent", "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
        "ProfitLossForPeriod", "ProfitLossForThePeriod",
    ),
    "Pretax Income": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    # Some filers (TCS in 2022-23) leave the diluted line at 0.00 and report the figure on the basic line; the basic line is used then
    "Diluted EPS": (
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "DilutedEarningsLossPerShareFromContinuingOperations",
        "DilutedEarningsPerShareAfterExtraordinaryItems", "DilutedEarningsPerShareBeforeExtraordinaryItems",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "BasicEarningsLossPerShareFromContinuingOperations",
        "BasicEarningsPerShareAfterExtraordinaryItems", "BasicEarningsPerShareBeforeExtraordinaryItems",
    ),
    "Interest Expense": ("FinanceCosts", "InterestExpended"),
    "Depreciation And Amortization": ("DepreciationDepletionAndAmortisationExpense",),
}
BALANCE_TAGS: dict[str, tuple[str, ...]] = {
    "Total Assets": ("Assets",),
    "Stockholders Equity": ("EquityAttributableToOwnersOfParent", "Equity"),
    "Current Assets": ("CurrentAssets",),
    "Current Liabilities": ("CurrentLiabilities",),
    "Cash And Cash Equivalents": ("CashAndCashEquivalents",),
}
DEBT_TAGS = ("BorrowingsNoncurrent", "BorrowingsCurrent")
BANK_EQUITY_TAGS = ("Capital", "ReservesAndSurplus")  # a bank's equity is its capital plus reserves
CASHFLOW_TAGS = {"Operating Cash Flow": ("CashFlowsFromUsedInOperatingActivities",)}
CAPEX_TAGS = (
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
)


@dataclass(frozen=True)
class Filing:
    """One results filing as the company first reported it. `income` is the reporting quarter, `cumulative` the year to date
    (the whole year in an annual filing), `balance` the balance sheet at the period end; every figure is in rupees."""

    symbol: str
    nature: str  # "consolidated" | "standalone"
    taxonomy: str  # "indas" | "banking"
    quarter_start: date
    quarter_end: date
    year_start: date
    income: dict[str, float] = field(default_factory=dict)
    cumulative: dict[str, float] = field(default_factory=dict)
    balance: dict[str, float] = field(default_factory=dict)
    cashflow: dict[str, float] = field(default_factory=dict)
    shares: float | None = None  # paid-up equity capital over face value
    audited: bool | None = None

    @property
    def is_annual(self) -> bool:
        return (self.quarter_end - self.year_start).days > 300


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


def _first(facts: dict[str, str], tags: tuple[str, ...]) -> float | None:
    """The first tag's value that is not zero; a line every tag gives as zero is zero. Filers fill the tag they do not use with
    0.00, and that must not hide the tag that holds the figure."""
    found = None
    for tag in tags:
        value = _number(facts.get(tag))
        if value:
            return value
        if value is not None:
            found = value
    return found


def _quarter_start(end: date) -> date:
    """The first day of the three-month quarter that ends on `end`, for filings that state only the end."""
    month = end.month - 2
    year = end.year
    if month < 1:
        month, year = month + 12, year - 1
    return date(year, month, 1)


def parse_filing(text: str) -> Filing:
    """A results filing's XBRL as a `Filing`. Raises ValueError for anything that is not one (the exchange returns an error
    page for filings that predate XBRL)."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError(f"not an XBRL filing: {exc}") from exc
    dimensioned = {
        context.get("id") for context in root.iter(f"{_XBRLI}context") if context.find(f"{_XBRLI}scenario") is not None
    }
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
    start = _day(current.get("DateOfStartOfReportingPeriod")) or _quarter_start(end)
    year_start = (
        _day(whole.get("DateOfStartOfReportingPeriod")) or _day(current.get("DateOfStartOfFinancialYear")) or start
    )  # older filings give the financial year's start instead of the reporting period's
    nature = (current.get("NatureOfReportStandaloneConsolidated") or "").strip().lower()
    taxonomy = "banking" if "InterestEarned" in current or "InterestExpended" in current else "indas"
    audited = current.get("WhetherResultsAreAuditedOrUnaudited")

    def lines(facts: dict[str, str]) -> dict[str, float]:
        out = {name: value for name, tags in INCOME_TAGS.items() if (value := _first(facts, tags)) is not None}
        if taxonomy == "indas":
            expenses, finance = _first(facts, ("Expenses",)), out.get("Interest Expense", 0.0)
            revenue = _first(facts, ("RevenueFromOperations",))
            if "Pretax Income" in out:
                out["EBIT"] = out["Pretax Income"] + finance
            if expenses is not None and revenue is not None:
                out["Operating Income"] = revenue - (expenses - finance)
        return out

    balance = {name: value for name, tags in BALANCE_TAGS.items() if (value := _first(instant, tags)) is not None}
    if "Stockholders Equity" not in balance and all(tag in instant for tag in BANK_EQUITY_TAGS):
        balance["Stockholders Equity"] = sum(float(instant[tag]) for tag in BANK_EQUITY_TAGS)
    debt = [value for tag in DEBT_TAGS if (value := _number(instant.get(tag))) is not None]
    if debt:
        balance["Total Debt"] = sum(debt)
    cashflow = {name: value for name, tags in CASHFLOW_TAGS.items() if (value := _first(whole, tags)) is not None}
    capex = [value for tag in CAPEX_TAGS if (value := _number(whole.get(tag))) is not None]
    if capex:
        cashflow["Capital Expenditure"] = -sum(capex)  # outflows are negative, as in the Yahoo payload
    paid_up = _number(current.get("PaidUpValueOfEquityShareCapital"))
    face_value = _number(current.get("FaceValueOfEquityShareCapital"))
    return Filing(
        symbol=(current.get("Symbol") or "").strip(),
        nature=nature if nature in ("consolidated", "standalone") else "",
        taxonomy=taxonomy,
        quarter_start=start,
        quarter_end=end,
        year_start=year_start,
        income=lines(current),
        cumulative=lines(whole),
        balance=balance,
        cashflow=cashflow,
        shares=paid_up / face_value if paid_up and face_value else None,
        audited=None if audited is None else audited.strip().lower() == "audited",
    )
