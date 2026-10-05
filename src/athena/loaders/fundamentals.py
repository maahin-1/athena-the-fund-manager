from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import AthenaError, EmptyRefreshError, Record, RefreshResult
from athena.store import DataStore
from athena.trading_calendar import ist_date

DATASET = "equity.fundamentals"  # freshness limit already declared: one quarter plus 45 days
SOURCE = "yfinance"
INFO_KEYS = (
    "sector", "industry", "sharesOutstanding", "marketCap", "currentPrice", "dividendRate", "financialCurrency",
)
INCOME_LINES = (
    "Total Revenue", "Net Income", "Operating Income", "EBIT", "Gross Profit", "Diluted EPS", "Interest Expense", "Pretax Income",
)
BALANCE_LINES = (
    "Total Assets", "Stockholders Equity", "Total Debt", "Current Assets", "Current Liabilities",
    "Ordinary Shares Number", "Cash And Cash Equivalents",
)
CASHFLOW_LINES = ("Operating Cash Flow", "Capital Expenditure", "Depreciation And Amortization", "Free Cash Flow")
DUPLICATE_KEY_LINES = ("Total Revenue", "Net Income")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def frame_to_lines(frame: Any, lines: tuple[str, ...]) -> dict[str, dict[str, float | None]]:
    """{line item: {period end (ISO): value}} for the wanted line items of a statement frame (items x periods)."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    out: dict[str, dict[str, float | None]] = {}
    for line in lines:
        if line in frame.index:
            out[line] = {column.date().isoformat(): _number(frame.loc[line, column]) for column in frame.columns}
    return out


def blank_duplicate_periods(income: dict[str, dict[str, float | None]], label: str) -> list[str]:
    """Yahoo sometimes repeats one period's figures under a second period. Periods whose revenue and net income
    both equal another period's are blanked (set to None) in every income line, and each is reported."""
    revenue, profit = (income.get(name, {}) for name in DUPLICATE_KEY_LINES)
    groups: dict[tuple[float, float], list[str]] = {}
    for period in revenue:
        pair = (revenue.get(period), profit.get(period))
        if None not in pair:
            groups.setdefault(pair, []).append(period)  # type: ignore[arg-type]
    flags: list[str] = []
    for periods in groups.values():
        if len(periods) > 1:
            for period in periods:
                for series in income.values():
                    if period in series:
                        series[period] = None
            flags.append(f"{label} periods {', '.join(sorted(periods))} carry identical figures (possible duplicate); treated as missing")
    return flags


def earnings_rows(frame: Any) -> list[dict[str, Any]]:
    """Earnings calendar and surprises as plain dicts; dates are IST dates (Yahoo stamps them in US Eastern time)."""
    if frame is None or getattr(frame, "empty", True):
        return []
    rows = []
    for stamp, row in frame.iterrows():
        rows.append(
            {
                "date": ist_date(stamp.to_pydatetime()).isoformat(),
                "estimate": _number(row.get("EPS Estimate")),
                "reported": _number(row.get("Reported EPS")),
                "surprise_pct": _number(row.get("Surprise(%)")),
            }
        )
    return rows


def payload_from_frames(
    info: Mapping[str, Any], income: Any, balance: Any, cashflow: Any, quarterly_income: Any, earnings: Any
) -> dict[str, Any]:
    """The stored payload: only the line items the metrics use, with duplicates blanked and flagged."""
    annual_income = frame_to_lines(income, INCOME_LINES)
    quarterly = frame_to_lines(quarterly_income, INCOME_LINES)
    flags = blank_duplicate_periods(annual_income, "annual") + blank_duplicate_periods(quarterly, "quarterly")
    return {
        "info": {key: info.get(key) for key in INFO_KEYS},
        "annual": {
            "income": annual_income,
            "balance": frame_to_lines(balance, BALANCE_LINES),
            "cashflow": frame_to_lines(cashflow, CASHFLOW_LINES),
        },
        "quarterly": {"income": quarterly},
        "earnings_dates": earnings_rows(earnings),
        "quality_flags": flags,
    }


def default_fetch(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}.NS")
    try:
        earnings = ticker.earnings_dates
    except Exception:  # the calendar is optional; the statements are not
        earnings = None
    return payload_from_frames(
        ticker.info or {}, ticker.financials, ticker.balance_sheet, ticker.cashflow, ticker.quarterly_financials, earnings
    )


class FundamentalsLoader:
    """Financial statements, key facts and the earnings calendar for one NSE stock at a time (the free source is
    Yahoo; it gives only today's snapshot, never what was known on an earlier date)."""

    def __init__(
        self,
        store: DataStore,
        fetch: Callable[[str], dict[str, Any]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {DATASET: {"cadence": "quarterly", "source": SOURCE, "scope": "one symbol per refresh"}}

    def refresh(self, dataset: str = DATASET, since: Any = None, **params: Any) -> RefreshResult:
        if dataset != DATASET:
            raise ValueError(f"this loader only provides {DATASET!r}, got {dataset!r}")
        symbol = params.get("symbol")
        if not symbol:
            raise ValueError("refresh needs symbol=...")
        try:
            payload = self._fetch(symbol)
        except AthenaError:
            raise
        except Exception as exc:  # the free source can fail in many ways (network, rate limit, format)
            raise EmptyRefreshError(f"could not fetch statements for {symbol!r}: {type(exc).__name__}") from exc
        income = payload.get("annual", {}).get("income", {})
        if not any(series for series in income.values()):
            raise EmptyRefreshError(f"no financial statements for {symbol!r} (the symbol may be wrong or renamed)")
        now = self._clock()
        self._store.put(Record(DATASET, symbol, now, SOURCE, payload))
        return RefreshResult(DATASET, 1, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
