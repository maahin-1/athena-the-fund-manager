from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, NamedTuple

from athena.contracts import InsufficientData
from athena.trading_calendar import ist_date

FINANCIAL_SECTORS = frozenset({"Financial Services"})
LENDER_REASON = "not meaningful for banks and lenders"
PRICE_CURRENCY = "INR"  # NSE prices; a statement in another currency cannot be set against them without an exchange rate
CRORE = 1e7
SOURCE = "computed from Yahoo statements (INR)"
GRAHAM_MULTIPLIER = 22.5  # Graham's ceiling: P/E 15 x P/B 1.5
QUARTER_SPAN_DAYS = 290  # four consecutive quarter-ends span about 273 days
# What the cross-checks accept, set from 40 real stocks on Oct 10, 2026: EPS over net income per current share ranged 0.94 to
# 1.12 (a buyback, a rights issue); a split or bonus not restated in the EPS gives 2x or more, so 0.75 to 1.33 passes every real
# case and stops a share-count mismatch. Our close and Yahoo's current price both read within a few percent on the same day.
EPS_BASIS_RANGE = (0.75, 1.33)
PRICE_AGREEMENT = (0.8, 1.25)
STALE_ANNUAL_DAYS = 456  # fiscal year-ends are a year apart and filed within months: older than 15 months is out of date
STALE_QUARTER_DAYS = 200  # a quarter is filed within 45 days of its end, so a newer one should exist after about 135
YEAR_WINDOW = (350, 380)  # days between a quarter and the same quarter a year earlier
MIN_INDEX_HISTORY = 250  # trading days needed before an index percentile means anything
VALUATION, QUALITY, EARNINGS = "valuation", "quality", "earnings"


class R(NamedTuple):
    value: float | str
    window: str
    note: str | None = None


def _periods(series: Mapping[str, float | None]) -> list[tuple[date, float]]:
    return sorted((date.fromisoformat(period), value) for period, value in series.items() if value is not None)


@dataclass(frozen=True)
class Statements:
    """Parsed `equity.fundamentals` payload with small accessors; missing items give empty series, never errors."""

    info: Mapping[str, Any]
    annual: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    quarterly: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    earnings_dates: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Statements:
        return cls(
            payload.get("info", {}), payload.get("annual", {}), payload.get("quarterly", {}), tuple(payload.get("earnings_dates", ()))
        )

    @property
    def is_financial(self) -> bool:
        return self.info.get("sector") in FINANCIAL_SECTORS

    def annual_series(self, group: str, line: str) -> list[tuple[date, float]]:
        return _periods(self.annual.get(group, {}).get(line, {}))

    def quarterly_series(self, line: str) -> list[tuple[date, float]]:
        return _periods(self.quarterly.get("income", {}).get(line, {}))


# (industry key, own metric, name of the industry figure, name of the comparison, "ratio" = own / industry or "gap" = own - industry, unit, lenders too)
INDUSTRY_COMPARISONS = (
    ("pe", "pe_trailing", "industry_pe", "pe_vs_industry", "ratio", "ratio", True),
    ("pb", "pb", "industry_pb", "pb_vs_industry", "ratio", "ratio", True),
    ("roe", "roe_latest", "industry_roe", "roe_vs_industry", "gap", "fraction", True),
    ("operating_margin", "operating_margin_latest", "industry_operating_margin", "operating_margin_vs_industry", "gap", "fraction", False),
)


def _industry_metrics(metrics: dict[str, dict[str, Any]], reasons: dict[str, str], groups: dict[str, str], industry: Mapping[str, Any], lender: bool) -> None:
    """Add the industry's figure and the stock against it. Each is computed in code from the benchmark table and the stock's own figure; one that cannot be is listed
    in `missing` with the reason."""
    values = industry.get("values", {})
    source = f"Damodaran India industry table for {industry.get('industry')} ({industry.get('n_firms')} firms, dated {industry.get('data_as_of')})"
    for key, own, industry_name, compare_name, how, unit, lenders_too in INDUSTRY_COMPARISONS:
        groups[industry_name] = groups[compare_name] = VALUATION if key in ("pe", "pb") else QUALITY
        if lender and not lenders_too:
            reasons[industry_name] = reasons[compare_name] = LENDER_REASON
            continue
        benchmark = values.get(key)
        if benchmark is None:
            reasons[industry_name] = reasons[compare_name] = f"the industry table gives no {key} for {industry.get('industry')}"
            continue
        metrics[industry_name] = {
            "value": round(float(benchmark), 4), "unit": unit if how == "ratio" else "fraction", "inputs": [key], "window": str(industry.get("data_as_of")),
            "source": source, "group": groups[industry_name],
        }
        if own not in metrics:
            reasons[compare_name] = f"{own} is not available"
            continue
        mine = float(metrics[own]["value"])
        if how == "ratio":
            if benchmark <= 0:
                reasons[compare_name] = "the industry figure is not positive"
                continue
            value, note = mine / benchmark, f"{own} over the industry figure: above 1 is dearer than the industry"
        else:
            value, note = mine - float(benchmark), f"{own} minus the industry figure: above 0 is better than the industry"
        metrics[compare_name] = {
            "value": round(value, 4), "unit": unit, "inputs": [own, industry_name], "window": str(industry.get("data_as_of")), "source": source,
            "group": groups[compare_name], "note": note,
        }


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise InsufficientData(message)


def _span(points: list[tuple[date, float]]) -> str:
    return f"{points[0][0].isoformat()}..{points[-1][0].isoformat()} ({len(points)} periods)"


def _aligned(first: list[tuple[date, float]], second: list[tuple[date, float]]) -> list[tuple[date, float, float]]:
    other = dict(second)
    return [(day, value, other[day]) for day, value in first if day in other]


def _cagr(points: list[tuple[date, float]], what: str) -> tuple[float, str]:
    _need(len(points) >= 3, f"{what} needs at least 3 annual periods, have {len(points)}")
    (start, first), (end, last) = points[0], points[-1]
    _need(first > 0 and last > 0, f"{what} needs positive first and last values")
    years = (end - start).days / 365.25
    return (last / first) ** (1 / years) - 1, _span(points)


def _percentile(values: list[float], x: float) -> float:
    return sum(1 for value in values if value <= x) / len(values)


def stale_statement_flags(annual: list[date], quarterly: list[date], today: date) -> list[str]:
    """Sentences for statements that stop too long ago: a company that merged away, stopped reporting, or a source that is
    behind. Nothing is withheld; the periods in each figure's window already say which year it is from."""
    flags = []
    if annual and (today - annual[-1]).days > STALE_ANNUAL_DAYS:
        flags.append(f"the latest fiscal year in the statements ended {annual[-1].isoformat()}, over 15 months ago; the company may have stopped reporting or the source is behind")
    if quarterly and (today - quarterly[-1]).days > STALE_QUARTER_DAYS:
        flags.append(f"the latest quarter in the statements ended {quarterly[-1].isoformat()}, over {STALE_QUARTER_DAYS} days ago; trailing figures may miss recent results")
    return flags


def build_fundamentals_packet(
    instrument: str,
    as_of: datetime,
    payload: Mapping[str, Any],
    price: float | None,
    now: datetime,
    index_history: Mapping[date, Mapping[str, float]] | None = None,
    industry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Valuation, quality and earnings figures for one stock, in the metrics-packet shape (TRD section 3).

    `industry` (optional) is the industry benchmarks of the stock as `IndustryContext.as_packet()` gives them; the packet then also holds the industry figure and the
    stock against it for P/E, P/B, return on equity and operating margin.

    Every figure is computed here from the stored statements; one that cannot be computed (too few periods, a
    lender where the ratio has no meaning, a missing price) is listed in `missing` with the reason."""
    st = Statements.from_payload(payload)
    source = str(payload.get("source") or SOURCE)
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    groups: dict[str, str] = {}

    def attempt(name: str, group: str, unit: str, inputs: list[str], compute: Callable[[], R]) -> None:
        groups[name] = group
        try:
            result = compute()
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metric: dict[str, Any] = {
            "value": result.value if isinstance(result.value, str) else round(float(result.value), 4),
            "unit": unit,
            "inputs": inputs,
            "window": result.window,
            "source": source,
            "group": group,
        }
        if result.note:
            metric["note"] = result.note
        metrics[name] = metric

    def non_lender() -> None:
        _need(not st.is_financial, LENDER_REASON)

    def same_currency() -> None:
        reported = st.info.get("financialCurrency")
        _need(not reported or reported == PRICE_CURRENCY, f"statements are in {reported} but the price is in {PRICE_CURRENCY}, and no exchange rate is applied")

    def have_price() -> float:
        _need(price is not None and price > 0, "no current price")
        reference = st.info.get("currentPrice")
        if reference:  # Yahoo's own current price: a close far from it means one of the two is on another basis
            ratio = price / float(reference)  # type: ignore[operator]
            _need(
                PRICE_AGREEMENT[0] <= ratio <= PRICE_AGREEMENT[1],
                f"the last close ({price:.2f}) is {ratio:.2f}x Yahoo's current price ({float(reference):.2f}); one of them may not be adjusted for a split or bonus",
            )
        return price  # type: ignore[return-value]

    def shares() -> float:
        reported = st.info.get("sharesOutstanding")
        if reported:
            return float(reported)
        counted = st.annual_series("balance", "Ordinary Shares Number")
        _need(bool(counted), "no share count")
        return counted[-1][1]

    def basis_checked(eps: float, net_income: float | None, window: str) -> float:
        """EPS is taken only if it agrees with net income over today's share count: Yahoo does not always restate EPS after
        a split or bonus, and an unrestated one is off by the split ratio."""
        if net_income is None or not st.info.get("sharesOutstanding"):
            return eps  # nothing to check it against
        implied = net_income / float(st.info["sharesOutstanding"])
        ratio = eps / implied if implied else math.inf
        _need(
            EPS_BASIS_RANGE[0] <= ratio <= EPS_BASIS_RANGE[1],
            f"diluted EPS ({eps:.2f}, {window}) is {ratio:.2f}x net income over the current share count ({implied:.2f}); "
            "the EPS may be on a different share count (a split, bonus or merger not restated) or leave out discontinued operations",
        )
        return eps

    def eps_ttm() -> tuple[float, str, str | None]:
        same_currency()
        quarters = st.quarterly_series("Diluted EPS")[-4:]
        if len(quarters) == 4 and (quarters[-1][0] - quarters[0][0]).days <= QUARTER_SPAN_DAYS:
            profit = dict(st.quarterly_series("Net Income"))
            window = f"last 4 quarters to {quarters[-1][0].isoformat()}"
            net = sum(profit[day] for day, _ in quarters) if all(day in profit for day, _ in quarters) else None
            return basis_checked(sum(value for _, value in quarters), net, window), window, None
        annual = st.annual_series("income", "Diluted EPS")
        _need(bool(annual), "no diluted EPS")
        window = f"fiscal year to {annual[-1][0].isoformat()}"
        profit = dict(st.annual_series("income", "Net Income"))
        return basis_checked(annual[-1][1], profit.get(annual[-1][0]), window), window, "annual EPS used because four consecutive quarters were not available"

    def book_value_per_share() -> float:
        same_currency()
        equity = st.annual_series("balance", "Stockholders Equity")
        _need(bool(equity), "no stockholders' equity")
        return equity[-1][1] / shares()

    def earnings_cagr() -> tuple[float, str]:
        return _cagr(st.annual_series("income", "Net Income"), "earnings growth")

    # ---- valuation
    def last_close() -> R:
        _need(price is not None and price > 0, "no current price")
        return R(price, "last close")  # type: ignore[arg-type]

    attempt("last_price", VALUATION, "INR", ["close"], last_close)
    attempt("market_cap", VALUATION, "INR crore", ["close", "shares"], lambda: R(have_price() * shares() / CRORE, "last close x shares outstanding"))

    def pe() -> R:
        eps, window, note = eps_ttm()
        _need(eps > 0, "earnings per share is not positive")
        return R(have_price() / eps, window, note)

    attempt("pe_trailing", VALUATION, "ratio", ["close", "diluted EPS"], pe)

    def pb() -> R:
        bvps = book_value_per_share()
        _need(bvps > 0, "book value is not positive")
        return R(have_price() / bvps, "latest fiscal year equity")

    attempt("pb", VALUATION, "ratio", ["close", "stockholders equity", "shares"], pb)

    def earnings_yield() -> R:
        result = pe()
        return R(1 / float(result.value), result.window, result.note)

    attempt("earnings_yield", VALUATION, "fraction", ["close", "diluted EPS"], earnings_yield)

    def cash_yield(owner: bool) -> R:
        non_lender()
        same_currency()
        if owner:
            profit_and_depreciation = _aligned(st.annual_series("income", "Net Income"), st.annual_series("cashflow", "Depreciation And Amortization"))
            earned = [(day, profit + depreciation) for day, profit, depreciation in profit_and_depreciation]
        else:
            earned = st.annual_series("cashflow", "Operating Cash Flow")
        joined = _aligned(earned, st.annual_series("cashflow", "Capital Expenditure"))
        _need(bool(joined), "no fiscal year has all the cash-flow lines")
        day, cash, spend = joined[-1]
        note = "capital expenditure is not split into maintenance and growth, so all of it is deducted" if owner else None
        return R((cash - abs(spend)) / (have_price() * shares()), f"fiscal year to {day.isoformat()}", note)

    attempt("fcf_yield", VALUATION, "fraction", ["operating cash flow", "capital expenditure", "market cap"], lambda: cash_yield(False))
    attempt(
        "owner_earnings_yield", VALUATION, "fraction",
        ["net income", "depreciation and amortization", "capital expenditure", "market cap"], lambda: cash_yield(True),
    )

    def peg() -> R:
        growth, window = earnings_cagr()
        _need(growth > 0, "earnings are not growing")
        return R(float(pe().value) / (growth * 100), window, "trailing P/E over the earnings growth rate in percent")

    attempt("peg", VALUATION, "ratio", ["pe_trailing", "net income"], peg)

    def graham() -> R:
        eps, window, _ = eps_ttm()
        bvps = book_value_per_share()
        _need(eps > 0 and bvps > 0, "Graham's number needs positive earnings and book value")
        return R(have_price() / math.sqrt(GRAHAM_MULTIPLIER * eps * bvps) - 1, window, "above 0 means the price is above Graham's ceiling")

    attempt("graham_number_premium", VALUATION, "fraction", ["close", "diluted EPS", "book value per share"], graham)

    def dividend_yield() -> R:
        rate = st.info.get("dividendRate")
        _need(bool(rate), "no dividend reported")
        return R(float(rate) / have_price(), "annual dividend rate over last close", "computed from the dividend rate, not Yahoo's yield field")

    attempt("dividend_yield", VALUATION, "fraction", ["dividend rate", "close"], dividend_yield)

    def index_pe() -> R:
        _need(bool(index_history), "no index valuation history")
        latest = max(index_history)  # type: ignore[arg-type]
        return R(index_history[latest]["pe"], f"NIFTY 50 on {latest.isoformat()}")  # type: ignore[index]

    attempt("index_pe", VALUATION, "ratio", ["NIFTY 50 P/E"], index_pe)
    attempt("pe_vs_index", VALUATION, "ratio", ["pe_trailing", "index_pe"], lambda: R(float(pe().value) / float(index_pe().value), "stock P/E over NIFTY 50 P/E"))

    def index_percentile() -> R:
        _need(bool(index_history) and len(index_history) >= MIN_INDEX_HISTORY, "index valuation history is too short")
        values = [row["pe"] for row in index_history.values()]  # type: ignore[union-attr]
        latest = index_history[max(index_history)]["pe"]  # type: ignore[index]
        return R(
            _percentile(values, latest),
            f"{len(values)} trading days of NIFTY 50 P/E",
            "share of those days on which the NIFTY 50 P/E was at or below today's: low means the market is cheap against its own history, high means dear",
        )

    attempt("index_pe_percentile", VALUATION, "fraction", ["NIFTY 50 P/E history"], index_percentile)

    # ---- quality (the ratio-only view of moat and business quality)
    def roe_by_year() -> list[tuple[date, float]]:
        equity = st.annual_series("balance", "Stockholders Equity")
        out = []
        for day, profit, closing in _aligned(st.annual_series("income", "Net Income"), equity):
            earlier = [value for d, value in equity if d < day]
            average = (closing + earlier[-1]) / 2 if earlier else closing
            if average > 0:
                out.append((day, profit / average))
        return out

    def roe(pick: Callable[[list[float]], float], minimum: int) -> R:
        values = roe_by_year()
        _need(len(values) >= minimum, f"needs {minimum} fiscal years of net income and equity, have {len(values)}")
        return R(pick([v for _, v in values]), _span(values), "net income over average equity")

    attempt("roe_latest", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(lambda v: v[-1], 1))
    attempt("roe_average", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(lambda v: sum(v) / len(v), 3))
    attempt("roe_minimum", QUALITY, "fraction", ["net income", "stockholders equity"], lambda: roe(min, 3))

    def margin(numerator: str, change: bool = False, lender_ok: bool = True) -> R:
        if not lender_ok:
            non_lender()
        pairs = _aligned(st.annual_series("income", numerator), st.annual_series("income", "Total Revenue"))
        _need(bool(pairs) and (len(pairs) >= 2 or not change), f"no fiscal year has {numerator} and revenue")
        ratios = [(day, top / revenue) for day, top, revenue in pairs if revenue]
        _need(bool(ratios), "revenue is zero")
        if change:
            _need(len(ratios) >= 2, "needs 2 fiscal years")
            return R((ratios[-1][1] - ratios[0][1]) * 100, _span(ratios), "latest minus earliest, in percentage points")
        return R(ratios[-1][1], f"fiscal year to {ratios[-1][0].isoformat()}")

    attempt("net_margin_latest", QUALITY, "fraction", ["net income", "revenue"], lambda: margin("Net Income"))
    attempt("operating_margin_latest", QUALITY, "fraction", ["operating income", "revenue"], lambda: margin("Operating Income", lender_ok=False))
    attempt("operating_margin_change", QUALITY, "percentage points", ["operating income", "revenue"], lambda: margin("Operating Income", change=True, lender_ok=False))
    attempt("gross_margin_latest", QUALITY, "fraction", ["gross profit", "revenue"], lambda: margin("Gross Profit", lender_ok=False))

    def cagr(group: str, line: str, what: str) -> R:
        value, window = _cagr(st.annual_series(group, line), what)
        return R(value, window, "compound annual growth between the first and last fiscal year")

    attempt("revenue_cagr", QUALITY, "fraction", ["revenue"], lambda: cagr("income", "Total Revenue", "revenue growth"))
    attempt("earnings_cagr", QUALITY, "fraction", ["net income"], lambda: cagr("income", "Net Income", "earnings growth"))

    def latest_ratio(top: tuple[str, str], bottom: tuple[str, str], what: str) -> R:
        non_lender()
        pairs = _aligned(st.annual_series(*top), st.annual_series(*bottom))
        _need(bool(pairs), f"no fiscal year has the lines for {what}")
        day, upper, lower = pairs[-1]
        _need(lower != 0, f"{what} has a zero denominator")
        return R(upper / lower, f"fiscal year to {day.isoformat()}")

    attempt("debt_to_equity", QUALITY, "ratio", ["total debt", "stockholders equity"], lambda: latest_ratio(("balance", "Total Debt"), ("balance", "Stockholders Equity"), "debt to equity"))
    attempt("current_ratio", QUALITY, "ratio", ["current assets", "current liabilities"], lambda: latest_ratio(("balance", "Current Assets"), ("balance", "Current Liabilities"), "current ratio"))

    def interest_coverage() -> R:
        non_lender()
        pairs = _aligned(st.annual_series("income", "EBIT"), [(d, abs(v)) for d, v in st.annual_series("income", "Interest Expense")])
        _need(bool(pairs), "no fiscal year has EBIT and interest expense")
        day, ebit, interest = pairs[-1]
        _need(interest > 0, "no interest expense")
        return R(ebit / interest, f"fiscal year to {day.isoformat()}")

    attempt("interest_coverage", QUALITY, "ratio", ["EBIT", "interest expense"], interest_coverage)

    def average_assets(day: date) -> float:
        assets = dict(st.annual_series("balance", "Total Assets"))
        _need(day in assets, "no total assets for the year")
        earlier = sorted((d, value) for d, value in assets.items() if d < day)
        return (assets[day] + earlier[-1][1]) / 2 if earlier else assets[day]

    def asset_turnover() -> R:
        non_lender()
        pairs = st.annual_series("income", "Total Revenue")
        _need(bool(pairs), "no revenue")
        day, revenue = pairs[-1]
        return R(revenue / average_assets(day), f"fiscal year to {day.isoformat()}")

    attempt("asset_turnover", QUALITY, "ratio", ["revenue", "total assets"], asset_turnover)

    # ---- earnings
    def accruals() -> tuple[date, float, float, float]:
        non_lender()
        joined = _aligned(st.annual_series("income", "Net Income"), st.annual_series("cashflow", "Operating Cash Flow"))
        _need(bool(joined), "no fiscal year has net income and operating cash flow")
        day, profit, cash = joined[-1]
        return day, profit, cash, average_assets(day)

    def accruals_ratio() -> R:
        day, profit, cash, assets = accruals()
        return R((profit - cash) / assets, f"fiscal year to {day.isoformat()}", "Sloan accruals: (net income - operating cash flow) over average assets; high positive values flag low earnings quality")

    def cash_conversion() -> R:
        day, profit, cash, _ = accruals()
        _need(profit > 0, "net income is not positive")
        return R(cash / profit, f"fiscal year to {day.isoformat()}", "operating cash flow over net income")

    attempt("accruals_ratio", EARNINGS, "fraction", ["net income", "operating cash flow", "total assets"], accruals_ratio)
    attempt("cash_conversion", EARNINGS, "ratio", ["operating cash flow", "net income"], cash_conversion)

    def quarter_growth(line: str) -> R:
        points = st.quarterly_series(line)
        _need(bool(points), f"no quarterly {line}")
        day, latest = points[-1]
        earlier = [value for d, value in points if YEAR_WINDOW[0] <= (day - d).days <= YEAR_WINDOW[1]]
        _need(bool(earlier), "the same quarter a year earlier is not available")
        _need(earlier[0] > 0, "the year-earlier figure is not positive")
        return R(latest / earlier[0] - 1, f"quarter to {day.isoformat()} against the same quarter a year earlier")

    attempt("revenue_growth_yoy_quarter", EARNINGS, "fraction", ["quarterly revenue"], lambda: quarter_growth("Total Revenue"))
    attempt("net_income_growth_yoy_quarter", EARNINGS, "fraction", ["quarterly net income"], lambda: quarter_growth("Net Income"))

    def surprises() -> list[tuple[str, float]]:
        rows = [(row["date"], row["surprise_pct"] / 100) for row in st.earnings_dates if row.get("reported") is not None and row.get("surprise_pct") is not None]
        _need(bool(rows), "no reported earnings with a surprise figure")
        return sorted(rows, reverse=True)

    attempt("eps_surprise_last", EARNINGS, "fraction", ["reported EPS", "EPS estimate"], lambda: R(surprises()[0][1], f"report of {surprises()[0][0]}", "against Yahoo's EPS estimate"))
    attempt("eps_surprise_average_4", EARNINGS, "fraction", ["reported EPS", "EPS estimate"], lambda: R(sum(v for _, v in surprises()[:4]) / len(surprises()[:4]), f"last {len(surprises()[:4])} reports"))
    attempt("beats_last_4", EARNINGS, "count", ["reported EPS", "EPS estimate"], lambda: R(sum(1 for _, v in surprises()[:4] if v > 0), f"of the last {len(surprises()[:4])} reports"))

    def days_to_next() -> R:
        today = ist_date(now)
        future = sorted((date.fromisoformat(row["date"]), row.get("source") or "") for row in st.earnings_dates if date.fromisoformat(row["date"]) > today)
        _need(bool(future), "no upcoming earnings date")
        return R((future[0][0] - today).days, f"next report {future[0][0].isoformat()}" + (f" ({future[0][1]})" if future[0][1] else ""))

    attempt("days_to_next_earnings", EARNINGS, "days", ["earnings calendar"], days_to_next)

    if industry is not None:
        _industry_metrics(metrics, reasons, groups, industry, st.is_financial)

    annual_dates = [day for day, _ in st.annual_series("income", "Total Revenue")]
    quarter_dates = [day for day, _ in st.quarterly_series("Total Revenue")]
    flags = list(payload.get("quality_flags", [])) + stale_statement_flags(annual_dates, quarter_dates, ist_date(now))
    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "sector": st.info.get("sector"),
        "is_lender": st.is_financial,
        "latest_annual_period": annual_dates[-1].isoformat() if annual_dates else None,
        "latest_quarter": quarter_dates[-1].isoformat() if quarter_dates else None,
        "data_quality_flags": flags,
        "industry": dict(industry) if industry is not None else None,
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
        "not_applicable": [name for name, why in reasons.items() if why == LENDER_REASON],
        "groups": groups,
    }
