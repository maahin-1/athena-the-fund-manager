from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import Bar
from athena.loaders.fundamentals import DATASET, FundamentalsLoader
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.resolver import Resolution


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
    ):
        self._loader = loader
        self._index_history = index_history
        self._fetch_bars = fetch_bars
        self._clock = clock
        self._packets: dict[str, dict[str, Any]] = {}

    def __call__(self, resolution: Resolution) -> dict[str, Any]:
        symbol = resolution.identifier
        if symbol not in self._packets:
            self._loader.refresh(symbol=symbol)
            record = self._loader.read(DATASET, symbol)
            bars = self._fetch_bars(symbol)
            price = bars[-1].close if bars else None
            self._packets[symbol] = build_fundamentals_packet(
                symbol, record.as_of, record.payload, price, self._clock(), self._index_history
            )
        return self._packets[symbol]

    def clear(self) -> None:
        self._packets.clear()
