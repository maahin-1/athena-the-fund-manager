from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from athena.contracts import AthenaError
from athena.news.model import NewsItem, clip
from athena.price_integrity.nse_actions import HEADERS, HOME, TIMEOUT

API = "https://www.nseindia.com/api/corporate-announcements"
IST = timezone(timedelta(hours=5, minutes=30))
_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y")


def _when(text: str) -> datetime | None:
    for form in _FORMATS:
        try:
            return datetime.strptime(text.strip(), form).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


class NseAnnouncements:
    """A company's own filings as the exchange publishes them (board outcomes, record dates, scheme updates), through the
    public website endpoint that NSE's pages use. It is primary-source text, not press: the best free confirmation that
    an event happened, and sometimes generic ('Updates'). Unofficial, so any failure is an `AthenaError`."""

    def __init__(self, session=None):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._session.headers.update(HEADERS)
        self._primed = False

    def __call__(self, symbol: str, since: date, until: date) -> list[NewsItem]:
        params = {"index": "equities", "symbol": symbol, "from_date": since.strftime("%d-%m-%Y"), "to_date": until.strftime("%d-%m-%Y")}
        try:
            if not self._primed:
                self._session.get(HOME, timeout=TIMEOUT)  # cookies; its own status does not matter
                self._primed = True
            response = self._session.get(API, params=params, timeout=TIMEOUT)
            if response.status_code != 200:
                raise AthenaError(f"HTTP {response.status_code}")
            rows = response.json()
            if not isinstance(rows, list):
                raise AthenaError("unexpected reply shape")
        except Exception as exc:
            raise AthenaError(f"NSE announcements unavailable for {symbol}: {exc}") from exc
        items = []
        for row in rows:
            when = _when(str(row.get("an_dt", "")))
            if when is None:
                continue
            title = clip(f"{row.get('desc', '')}: {row.get('attchmntText', '')}")
            items.append(NewsItem(title, "NSE", when, str(row.get("attchmntFile") or ""), "nse"))
        return sorted(items, key=lambda item: item.published, reverse=True)
