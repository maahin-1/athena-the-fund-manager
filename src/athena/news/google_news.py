from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date

from athena.contracts import AthenaError
from athena.news.model import SourceResult
from athena.news.publisher_feeds import http_get
from athena.news.rss import parse_rss

URL = "https://news.google.com/rss/search"
MIN_GAP = 1.0  # seconds between requests
OFF_NOTE = (
    "Google News is off, so press coverage is limited to NSE announcements and the last day or three of publisher feeds. "
    "Set ATHENA_GOOGLE_NEWS=1 to turn it on (its robots.txt disallows crawling; personal use at about one request a second)."
)


class GoogleNews:
    """Google News RSS search for a company over a date range. It found all five benchmark events with press coverage, but
    its robots.txt disallows crawling, so it does nothing unless `enabled`. Links stay Google's wrapper URLs."""

    def __init__(
        self,
        get: Callable[..., bytes] = http_get,
        enabled: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ):
        self._get, self.enabled, self._sleep, self._now = get, enabled, sleep, now
        self._last: float | None = None

    def __call__(self, symbol: str, names: tuple[str, ...], since: date, until: date, extra: str = "") -> SourceResult:
        if not self.enabled:
            return SourceResult([], (OFF_NOTE,))
        words = f" {extra}" if extra else ""
        query = f'"{names[0]}"{words} after:{since.isoformat()} before:{until.isoformat()}'
        if self._last is not None:
            wait = MIN_GAP - (self._now() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._now()
        try:
            body = self._get(URL, params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
            items = parse_rss(body, "Google News", "google news", publisher_from_source=True)
        except AthenaError as exc:
            raise AthenaError(f"Google News unavailable for {symbol}: {exc}") from exc
        return SourceResult(items)
