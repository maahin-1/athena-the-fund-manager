"""Prices read out of the warehouse with recorded splits and bonuses applied, and the market-wide check of which stocks have a
price break nothing explains."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace

from athena.contracts import Bar
from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.history import FLAG_BARS, CheckedHistory
from athena.warehouse.warehouse import Warehouse


def joined_bars(warehouse: Warehouse, symbol: str) -> list[Bar]:
    """The symbol's bars as printed, with those of its earlier symbols in front (see `athena.warehouse.renames`: only renames whose prices
    join). The earlier bars are given the symbol's name, and none that fall on or after the next symbol's first day is kept."""
    own = warehouse.bars(symbol)
    for step in reversed(warehouse.predecessors(symbol)):
        if not own:
            break
        before = [replace(bar, symbol=symbol) for bar in warehouse.bars(step.old, until=step.last_old_day)]
        own = [bar for bar in before if bar.timestamp < own[0].timestamp] + own
    return own


def joined_actions(warehouse: Warehouse, symbol: str) -> list[CorporateAction]:
    """The corporate actions of the symbol and of its earlier symbols, each once (the exchange lists a past action under the symbol of today)."""
    found: dict[CorporateAction, None] = {}
    for name in (*(step.old for step in warehouse.predecessors(symbol)), symbol):
        for action in warehouse.actions(name):
            found.setdefault(action)
    return sorted(found, key=lambda action: action.day)


def adjusted_history(warehouse: Warehouse, events: Callable[[str], Sequence[PriceEvent]] | None = None) -> CheckedHistory:
    """The same `CheckedHistory` the app uses, reading raw bars and the exchange's actions from the warehouse. A company that changed its
    symbol is read as one series: the earlier symbols' bars and actions come in too, so each split is applied at its own ex-date and the
    old bars sit on the same footing as the new, with no per-company handling. `events` (Yahoo's split record, a request per stock) is
    optional: without it every large break must be explained by the exchange's own rows or is flagged."""
    return CheckedHistory(lambda symbol: joined_bars(warehouse, symbol), events or (lambda symbol: ()), lambda symbol: joined_actions(warehouse, symbol))


def market_price_flags(warehouse: Warehouse, symbols: Iterable[str], bars: int = FLAG_BARS) -> dict[str, tuple[str, ...]]:
    """For each symbol, the large overnight breaks in its last `bars` bars that no record explains. Symbols with none are left out."""
    history = adjusted_history(warehouse)
    flagged = {}
    for symbol in symbols:
        history(symbol)
        flags = history.price_flags(symbol, bars)
        if flags:
            flagged[symbol] = flags
    return flagged
