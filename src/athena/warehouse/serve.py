"""The Analyse tab's data from the market warehouse when it can serve it, and from the live sources when it cannot.

A stored answer is used only when it is recent and covers what was asked; every use leaves a note saying where the figures came from
and how old they are. The file is opened read-only for each read and closed again."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent
from athena.trading_calendar import ist_date
from athena.warehouse.warehouse import Warehouse

MAX_PRICE_AGE_DAYS = 5  # the warehouse's last loaded day against today
SYMBOL_GAP_DAYS = 10  # a symbol whose last bar is further back than this from the last loaded day has stopped trading
MAX_STATEMENT_AGE_DAYS = 30
WAIT_SECONDS = 5  # a long job holds the file only for its short writes
UNREADABLE = (AthenaError, duckdb.Error, OSError)


class WarehouseFirst:
    def __init__(
        self,
        path: Path | str,
        live_bars: Callable[[str], list[Bar]],
        live_events: Callable[[str], Sequence[PriceEvent]],
        live_actions: Callable[[str], Sequence[CorporateAction]],
        days: int,
        clock: Callable[[], datetime] = utc_now,
        wait_seconds: float = WAIT_SECONDS,
    ):
        self._path, self._days, self._clock, self._wait = Path(path), days, clock, wait_seconds
        self._live_bars, self._live_events, self._live_actions = live_bars, live_events, live_actions
        self._prices: dict[str, date] = {}  # symbol -> last bar's day, for the symbols whose latest bars came from the warehouse
        self._statements: dict[str, datetime] = {}

    def _read(self, read: Callable[[Warehouse], Any]) -> Any:
        if not self._path.exists():
            return None
        try:
            with Warehouse(self._path, read_only=True, wait_seconds=self._wait) as warehouse:
                return read(warehouse)
        except UNREADABLE:
            return None

    def bars(self, symbol: str) -> list[Bar]:
        today = ist_date(self._clock())
        since = today - timedelta(days=self._days)

        def read(warehouse: Warehouse) -> list[Bar] | None:
            first, last = warehouse.first_loaded_day(), warehouse.last_loaded_day()
            if first is None or last is None or first > since or (today - last).days > MAX_PRICE_AGE_DAYS:
                return None
            bars = warehouse.bars(symbol, since=since)
            if not bars or (last - ist_date(bars[-1].timestamp)).days > SYMBOL_GAP_DAYS:
                return None
            return bars

        bars = self._read(read)
        if bars:
            self._prices[symbol] = ist_date(bars[-1].timestamp)
            return bars
        self._prices.pop(symbol, None)
        return self._live_bars(symbol)

    def events(self, symbol: str) -> Sequence[PriceEvent]:
        """Yahoo's split record, not asked for when the bars came from the warehouse (its stored exchange rows cover the same ground)."""
        return () if symbol in self._prices else self._live_events(symbol)

    def actions(self, symbol: str) -> Sequence[CorporateAction]:
        if symbol in self._prices:
            stored = self._read(lambda warehouse: warehouse.actions(symbol))
            if stored is not None:
                return stored
        return self._live_actions(symbol)

    def statement(self, symbol: str) -> tuple[dict[str, Any], datetime] | None:
        """The stored statements payload and when it was fetched, when it is recent enough to use."""
        held = self._read(lambda warehouse: warehouse.statement(symbol))
        if held is None or (self._clock() - held[1]).days > MAX_STATEMENT_AGE_DAYS:
            self._statements.pop(symbol, None)
            return None
        self._statements[symbol] = held[1]
        return held

    def notes(self, symbol: str) -> tuple[str, ...]:
        out = []
        if symbol in self._prices:
            out.append(f"prices from the market database, last close {self._prices[symbol]:%Y-%m-%d}")
        if symbol in self._statements:
            out.append(f"statements fetched {self._statements[symbol]:%Y-%m-%d}")
        return tuple(out)
