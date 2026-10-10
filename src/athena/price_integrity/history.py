from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from athena.contracts import AthenaError, Bar
from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.merge import merge_events
from athena.price_integrity.reconcile import Reconciled, describe, reconcile
from athena.trading_calendar import ist_date

FLAG_BARS = 253  # the risk and trend figures read about a year of closes


class CheckedHistory:
    """Daily bars with recorded splits and bonuses applied, in place of the raw fetcher. Whoever shows prices asks
    `notes(symbol)` for what was adjusted or could not be explained. Either source failing is itself a note and never stops the
    other: with neither, the prices come back raw with every large break flagged as unexplained."""

    def __init__(
        self,
        fetch: Callable[[str], list[Bar]],
        events: Callable[[str], Sequence[PriceEvent]],
        actions: Callable[[str], Sequence[CorporateAction]] | None = None,
    ):
        self._fetch = fetch
        self._events = events
        self._actions = actions
        self._notes: dict[str, tuple[str, ...]] = {}
        self._results: dict[str, Reconciled] = {}

    def __call__(self, symbol: str) -> list[Bar]:
        raw = self._fetch(symbol)
        notes: list[str] = []
        events: list[PriceEvent] = []
        actions: list[CorporateAction] = []
        try:
            events = list(self._events(symbol))
        except AthenaError as exc:
            notes.append(f"{exc}. Large price breaks cannot be fully checked against a record.")
        if self._actions is not None:
            try:
                actions = list(self._actions(symbol))
            except AthenaError as exc:
                notes.append(f"{exc}. Demergers and the exchange's own split and bonus rows could not be checked.")
        merged = merge_events(events, actions)
        result = reconcile(raw, merged.events, actions)
        self._notes[symbol] = tuple(notes) + merged.conflicts + merged.notes + describe(result)
        self._results[symbol] = result
        return list(result.bars)

    def notes(self, symbol: str) -> tuple[str, ...]:
        return self._notes.get(symbol, ())

    def price_flags(self, symbol: str, bars: int = FLAG_BARS) -> tuple[str, ...]:
        """The large overnight breaks no record explains, inside the last `bars` bars of the last fetch of `symbol`: the
        reason a figure measured over that stretch cannot be trusted. Empty when there are none (or nothing was fetched)."""
        result = self._results.get(symbol)
        if result is None or not result.bars:
            return ()
        since = ist_date(result.bars[-bars:][0].timestamp)
        return tuple(f"{m.day} (x{m.ratio:.2g})" for m in result.unexplained if m.day >= since)

    def events_found(self, symbol: str) -> tuple[tuple[date, str, str], ...]:
        """What the last fetch of `symbol` found, as (day, kind, label), oldest first: events it adjusted for, large breaks
        it could not explain, and exchange rows it only noted. For whoever wants to look for news about them."""
        result = self._results.get(symbol)
        if result is None:
            return ()
        found = [(a.event.day, a.event.kind, f"{a.event.kind} (x{a.event.factor:.4g})") for a in result.applied]
        found += [(m.day, "unexplained", f"unexplained price break (x{m.ratio:.2g})") for m in result.unexplained]
        found += [(a.day, a.kind, a.text) for a in result.noted]
        return tuple(sorted(found))
