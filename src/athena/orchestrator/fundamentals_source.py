from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.loaders.fundamentals import DATASET, FundamentalsLoader
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.news.model import NewsItem
from athena.news.schemes import scheme_completions, scheme_flag
from athena.resolver import Resolution
from athena.trading_calendar import ist_date

SCHEME_YEARS = 4  # about as far back as the statements go


class LiveFundamentals:
    """Fetches a stock's statements (Yahoo) into the store and builds its fundamentals packet against the last close
    and the NIFTY 50 valuation history. The three fundamentals specialists and the dashboard all call it for the same
    request, so each symbol is fetched once until `clear()`. Raises an `AthenaError` when the source has nothing for
    the symbol."""

    def __init__(
        self,
        loader: FundamentalsLoader,
        index_history: Mapping[date, Mapping[str, float]],
        fetch_bars: Callable[[str], list[Bar]],
        clock: Callable[[], datetime] = utc_now,
        filings: Callable[[str, date, date], list[NewsItem]] | None = None,
    ):
        self._loader = loader
        self._index_history = index_history
        self._fetch_bars = fetch_bars
        self._clock = clock
        self._filings = filings  # the exchange's announcements for a symbol between two dates, to find mergers and schemes
        self._packets: dict[str, dict[str, Any]] = {}

    def __call__(self, resolution: Resolution) -> dict[str, Any]:
        symbol = resolution.identifier
        if symbol not in self._packets:
            self._loader.refresh(symbol=symbol)
            record = self._loader.read(DATASET, symbol)
            bars = self._fetch_bars(symbol)
            price = bars[-1].close if bars else None
            packet = build_fundamentals_packet(symbol, record.as_of, record.payload, price, self._clock(), self._index_history)
            packet["data_quality_flags"] = packet["data_quality_flags"] + self._scheme_flags(symbol)
            self._packets[symbol] = packet
        return self._packets[symbol]

    def _scheme_flags(self, symbol: str) -> list[str]:
        """What the exchange's filings say about a merger or scheme taking effect in the years the statements cover. A source that
        cannot be reached is a flag too, never an error: the figures just are not checked for it."""
        if self._filings is None:
            return []
        today = ist_date(self._clock())
        try:
            filings = self._filings(symbol, today - timedelta(days=365 * SCHEME_YEARS), today)
        except AthenaError as exc:
            return [f"{exc}; mergers and schemes of arrangement could not be checked, so growth across one would read as organic"]
        flag = scheme_flag(scheme_completions(filings))
        return [flag] if flag else []

    def clear(self) -> None:
        self._packets.clear()
