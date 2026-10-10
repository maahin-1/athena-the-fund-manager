from __future__ import annotations

from collections.abc import Callable, Sequence

from athena.contracts import AthenaError, Bar
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.reconcile import describe, reconcile


class CheckedHistory:
    """Daily bars with recorded splits and bonuses applied, in place of the raw fetcher. Whoever shows prices asks
    `notes(symbol)` for what was adjusted or could not be explained. A failed event lookup is itself a note: the prices
    are then returned raw, with every large break flagged as unexplained."""

    def __init__(self, fetch: Callable[[str], list[Bar]], events: Callable[[str], Sequence[PriceEvent]]):
        self._fetch = fetch
        self._events = events
        self._notes: dict[str, tuple[str, ...]] = {}

    def __call__(self, symbol: str) -> list[Bar]:
        raw = self._fetch(symbol)
        notes: list[str] = []
        try:
            events = list(self._events(symbol))
        except AthenaError as exc:
            events = []
            notes.append(f"{exc}. Large price breaks cannot be checked against a record.")
        result = reconcile(raw, events)
        self._notes[symbol] = tuple(notes) + describe(result)
        return list(result.bars)

    def notes(self, symbol: str) -> tuple[str, ...]:
        return self._notes.get(symbol, ())
