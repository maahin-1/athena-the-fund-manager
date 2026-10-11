"""Prices read out of the warehouse with recorded splits and bonuses applied, and the market-wide check of which stocks have a
price break nothing explains."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from athena.price_integrity.events import PriceEvent
from athena.price_integrity.history import FLAG_BARS, CheckedHistory
from athena.warehouse.warehouse import Warehouse


def adjusted_history(warehouse: Warehouse, events: Callable[[str], Sequence[PriceEvent]] | None = None) -> CheckedHistory:
    """The same `CheckedHistory` the app uses, reading raw bars and the exchange's actions from the warehouse. `events` (Yahoo's
    split record, a request per stock) is optional: without it every large break must be explained by the exchange's own rows
    or is flagged."""
    return CheckedHistory(lambda symbol: warehouse.bars(symbol), events or (lambda symbol: ()), warehouse.actions)


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
