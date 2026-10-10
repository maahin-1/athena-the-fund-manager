from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from athena.evaluation.golden import GOLDEN_DIR
from athena.news.hub import NewsHub
from athena.news.model import NewsItem
from athena.news.packet import WINDOW_DAYS, build_news_packet

NEWS_CASES = GOLDEN_DIR / "news_cases.json"


@dataclass(frozen=True)
class NewsCase:
    """One frozen day in a company's news: the headlines and filings of the 30 days up to `as_of`, the corporate events the
    price checks had found, and the signals a person would accept from a news analyst reading them."""

    symbol: str
    company: str
    as_of: date
    why: str
    events: tuple[tuple[date, str, str], ...]
    items: tuple[NewsItem, ...]
    signals: tuple[str, ...]  # allowed signals; `abstain` means no view is the right answer
    sources: tuple[str, ...] = field(default_factory=tuple)  # which sources the capture used, for the record

    def packet(self) -> dict[str, Any]:
        return build_news_packet(self.symbol, self.company, self.items, self.events, self.as_of)


def _item_to_json(item: NewsItem) -> dict[str, str]:
    return {"title": item.title, "publisher": item.publisher, "published": item.published.isoformat(), "link": item.link, "source": item.source}


def case_to_json(case: NewsCase) -> dict[str, Any]:
    return {
        "symbol": case.symbol, "company": case.company, "as_of": case.as_of.isoformat(), "why": case.why,
        "events": [[day.isoformat(), kind, label] for day, kind, label in case.events],
        "items": [_item_to_json(item) for item in case.items], "signals": list(case.signals), "sources": list(case.sources),
    }


def case_from_json(raw: dict[str, Any]) -> NewsCase:
    items = tuple(
        NewsItem(i["title"], i["publisher"], datetime.fromisoformat(i["published"]), i.get("link", ""), i["source"]) for i in raw["items"]
    )
    events = tuple((date.fromisoformat(day), kind, label) for day, kind, label in raw["events"])
    return NewsCase(raw["symbol"], raw["company"], date.fromisoformat(raw["as_of"]), raw["why"], events, items, tuple(raw["signals"]), tuple(raw.get("sources", ())))


def load_news_cases(path: Path | None = None) -> list[NewsCase]:
    return [case_from_json(raw) for raw in json.loads((path or NEWS_CASES).read_text(encoding="utf-8"))]


def freeze_news(hub: NewsHub, symbol: str, company: str, as_of: date) -> tuple[tuple[NewsItem, ...], tuple[str, ...]]:
    """The 30 days of news up to `as_of` as the hub finds them now, for freezing into a golden case. Returns the items and
    the notes the hub gave (a source that was off or down)."""
    result = hub.fetch(symbol, company, as_of - timedelta(days=WINDOW_DAYS), as_of)
    return result.items, result.notes
