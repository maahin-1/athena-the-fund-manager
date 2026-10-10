from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta

from athena.news.confirm import DAYS_AFTER, DAYS_BEFORE, EventRef, confirm_events, describe_confirmations
from athena.news.hub import NewsHub
from athena.news.model import company_names

MAX_EVENTS = 5  # looked up per view: each is a request to NSE and, when it is on, to Google News
EXTRA_WORDS = {"split": "(split OR bonus)", "bonus": "(split OR bonus)", "demerger": "demerger", "rights": "rights issue", "buyback": "buyback"}


class EventConfirmer:
    """For a stock's recent corporate events and unexplained price breaks, what the exchange and the press reported around
    each date. Context for the reader only: nothing here changes a price. Never raises: news being down is a sentence."""

    def __init__(
        self,
        hub: NewsHub,
        events: Callable[[str], Sequence[tuple[date, str, str]]],
        today: Callable[[], date] = date.today,
    ):
        self._hub = hub
        self._events = events
        self._today = today
        self._done: dict[tuple, tuple[str, ...]] = {}

    def __call__(self, symbol: str, company: str) -> tuple[str, ...]:
        found = sorted(self._events(symbol))[-MAX_EVENTS:]
        if not found:
            return ()
        key = (symbol, tuple(found))
        if key not in self._done:
            self._done[key] = self._look(symbol, company, found)
        return self._done[key]

    def _look(self, symbol: str, company: str, found: Sequence[tuple[date, str, str]]) -> tuple[str, ...]:
        today = self._today()
        confirmations, source_notes = [], []
        for day, kind, label in found:
            ref = EventRef(day, kind, label)
            try:
                result = self._hub.fetch(
                    symbol, company, day - timedelta(days=DAYS_BEFORE), min(day + timedelta(days=DAYS_AFTER), today), EXTRA_WORDS.get(kind, "")
                )
            except Exception as exc:  # news must never stop a view
                source_notes.append(f"{label} on {day} could not be checked against the news: {exc}")
                continue
            confirmations += confirm_events([ref], result.items, company_names(company))
            source_notes += [note for note in result.notes if note not in source_notes]
        return describe_confirmations(confirmations) + tuple(source_notes)
