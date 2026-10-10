from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from athena.contracts import AthenaError
from athena.news.model import SourceResult, mentions
from athena.news.rss import parse_rss

# Verified live on Oct 10, 2026. They hold only the last day or three, so they serve current headlines, never past events.
FEEDS = (
    ("Economic Times", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Business Standard", "https://www.business-standard.com/rss/companies-101.rss"),
    ("Mint", "https://www.livemint.com/rss/markets"),
    ("Mint", "https://www.livemint.com/rss/companies"),
    ("BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    ("BusinessLine", "https://www.thehindubusinessline.com/companies/feeder/default.rss"),
)
CACHE_MINUTES = 10
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AthenaResearch/0.1"


def http_get(url: str, params: dict | None = None) -> bytes:
    """A plain GET for a feed, as bytes. Anything but a 200 is an `AthenaError`."""
    import requests

    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,*/*"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=25)
    except requests.RequestException as exc:
        raise AthenaError(str(exc)) from exc
    if response.status_code != 200:
        raise AthenaError(f"HTTP {response.status_code}")
    return response.content


class PublisherFeeds:
    """The business sections of four Indian publishers' public RSS feeds, kept for ten minutes, searched for a company's
    name. Publisher-allowed and key-less, but each feed is only a day or three deep and cannot be filtered by company, so
    a given stock gets few items: this is a live supplement, not a history."""

    def __init__(self, get: Callable[..., bytes] = http_get, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._get = get
        self._clock = clock
        self._cache: dict[str, tuple[datetime, list]] = {}

    def __call__(self, symbol: str, names: tuple[str, ...], since: date, until: date) -> SourceResult:
        now = self._clock()
        items, notes, failed = [], [], 0
        for publisher, url in FEEDS:
            cached = self._cache.get(url)
            if cached is None or now - cached[0] > timedelta(minutes=CACHE_MINUTES):
                try:
                    cached = (now, parse_rss(self._get(url), publisher, "publisher feed"))
                except AthenaError as exc:
                    failed += 1
                    notes.append(f"{publisher} feed unavailable: {exc}")
                    continue
                self._cache[url] = cached
            items += [i for i in cached[1] if since <= i.published.date() <= until and mentions(i, names)]
        if failed == len(FEEDS):
            raise AthenaError("every publisher feed failed")
        return SourceResult(sorted(items, key=lambda i: i.published, reverse=True), tuple(notes))
