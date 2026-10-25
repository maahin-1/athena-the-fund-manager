from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time
from typing import Any

from athena.contracts import AthenaError
from athena.pit.nse_results import IST, FilingRef
from athena.pit.xbrl import Filing

MARKET_CLOSE = time(15, 30)
ANNUAL_PERIODS = 5
QUARTERLY_PERIODS = 8
ALLOWED_INFO = ("sector", "industry")
CURRENCY = "INR"  # the exchange's filings are in rupees whatever the company's main reporting currency


def known_by(as_of: date) -> datetime:
    """The moment the market closed on `as_of`: a filing after it was not public when that day's analysis was made."""
    return datetime.combine(as_of, MARKET_CLOSE, tzinfo=IST)


def statements_as_of(
    refs: Sequence[FilingRef],
    load: Callable[[FilingRef], Filing],
    as_of: date,
    info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What the company had filed by the close of `as_of`, as the stored-statements payload the fundamentals packet reads
    (the same shape the Yahoo loader stores), every figure as first reported then.

    Consolidated filings are used when the company has them, standalone otherwise; one company is never mixed across the two.
    A period filed twice (a revision) takes the latest filing that was public by `as_of`. `info` carries what the filings
    cannot (the sector, today's price); the share count is read from the latest filing. Filings that cannot be loaded are
    skipped and named in `quality_flags`."""
    cutoff = known_by(as_of)
    public = [ref for ref in refs if ref.filed <= cutoff]
    consolidated = any(ref.consolidated for ref in public)
    chosen = [ref for ref in public if ref.consolidated == consolidated]
    latest: dict[tuple[date, bool], FilingRef] = {}
    for ref in sorted(chosen, key=lambda r: r.filed):
        latest[(ref.period_end, ref.annual)] = ref  # a later filing of the same period replaces the earlier

    flags: list[str] = []
    annual: dict[str, dict[str, float | None]] = {"income": {}, "balance": {}, "cashflow": {}}
    quarterly_income: dict[str, dict[str, float | None]] = {}
    loaded: list[tuple[FilingRef, Filing]] = []
    for key in sorted(latest)[-(ANNUAL_PERIODS + QUARTERLY_PERIODS) * 2:]:
        ref = latest[key]
        try:
            loaded.append((ref, load(ref)))
        except AthenaError as exc:
            flags.append(f"the filing for the period ended {ref.period_end} could not be read ({exc}); that period is missing")

    def put(group: dict[str, dict[str, float | None]], line: str, day: date, value: float | None) -> None:
        group.setdefault(line, {})[day.isoformat()] = value

    annual_days: list[date] = []
    quarter_days: list[date] = []
    for ref, filing in loaded:
        if filing.is_annual and ref.annual:
            annual_days.append(filing.quarter_end)
            for line, value in filing.cumulative.items():
                put(annual["income"], line, filing.quarter_end, value)
            for line, value in filing.balance.items():
                put(annual["balance"], line, filing.quarter_end, value)
            for line, value in filing.cashflow.items():
                put(annual["cashflow"], line, filing.quarter_end, value)
            if filing.shares:
                put(annual["balance"], "Ordinary Shares Number", filing.quarter_end, filing.shares)
            if "Depreciation And Amortization" in filing.cumulative:
                put(annual["cashflow"], "Depreciation And Amortization", filing.quarter_end, filing.cumulative["Depreciation And Amortization"])
        # every filing's own quarter (an annual filing carries the fourth quarter too)
        quarter_days.append(filing.quarter_end)
        for line, value in filing.income.items():
            put(quarterly_income, line, filing.quarter_end, value)
    keep_annual = sorted(set(annual_days))[-ANNUAL_PERIODS:]
    keep_quarters = sorted(set(quarter_days))[-QUARTERLY_PERIODS:]
    for group in (annual["income"], annual["balance"], annual["cashflow"]):
        for line in group:
            group[line] = {day: value for day, value in group[line].items() if date.fromisoformat(day) in keep_annual}
    for line in quarterly_income:
        quarterly_income[line] = {day: value for day, value in quarterly_income[line].items() if date.fromisoformat(day) in keep_quarters}

    shares = None
    if loaded:
        newest = max((filing for _, filing in loaded), key=lambda f: f.quarter_end)
        shares = newest.shares
    base = {key: info[key] for key in ALLOWED_INFO if info and key in info}  # nothing that describes today may leak into a past date
    if loaded and any(f.taxonomy == "banking" for _, f in loaded):
        base.setdefault("sector", "Financial Services")
    return {
        "info": {**base, "sharesOutstanding": shares, "financialCurrency": CURRENCY},
        "annual": annual,
        "quarterly": {"income": quarterly_income},
        "earnings_dates": [],
        "quality_flags": flags,
    }
