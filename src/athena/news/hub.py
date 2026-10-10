from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from athena.contracts import AthenaError
from athena.news.model import NewsItem, SourceResult, company_names

# One signature for every source: (symbol, company names, since, until, extra search words) -> items or a SourceResult.
Source = Callable[..., "list[NewsItem] | SourceResult"]


@dataclass(frozen=True)
class NewsResult:
    items: tuple[NewsItem, ...]  # newest first, duplicates removed
    notes: tuple[str, ...]  # a source that failed or is off, in the reader's words


def _key(item: NewsItem) -> str:
    return re.sub(r"[^a-z0-9]", "", item.title.lower())[:60]


class NewsHub:
    """Every news source behind one call. A source that fails is a note and the rest still answer; the same headline from
    two sources is kept once (the first source listed wins)."""

    def __init__(self, sources: Sequence[tuple[str, Source]]):
        self._sources = list(sources)

    def fetch(self, symbol: str, company: str, since: date, until: date, extra: str = "") -> NewsResult:
        names = company_names(company)
        seen: set[str] = set()
        items: list[NewsItem] = []
        notes: list[str] = []
        for _, source in self._sources:
            try:
                result = source(symbol, names, since, until, extra)
            except AthenaError as exc:
                notes.append(str(exc))
                continue
            if not isinstance(result, SourceResult):
                result = SourceResult(list(result))
            notes.extend(result.notes)
            for item in result.items:
                if _key(item) not in seen:
                    seen.add(_key(item))
                    items.append(item)
        return NewsResult(tuple(sorted(items, key=lambda item: item.published, reverse=True)), tuple(notes))


def default_sources(nse: Callable, feeds: Callable, google: Callable) -> list[tuple[str, Source]]:
    """The three clients adapted to the hub's one signature, in the order that decides which copy of a duplicate wins:
    the exchange's own text first, then the publishers, then Google."""
    return [
        ("NSE announcements", lambda symbol, names, since, until, extra="": nse(symbol, since, until)),
        ("publisher feeds", lambda symbol, names, since, until, extra="": feeds(symbol, names, since, until)),
        ("Google News", lambda symbol, names, since, until, extra="": google(symbol, names, since, until, extra)),
    ]
