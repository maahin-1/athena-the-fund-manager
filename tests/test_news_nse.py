from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.news.nse_announcements import NseAnnouncements

IST = timezone(timedelta(hours=5, minutes=30))
ROWS = [
    {"symbol": "VEDL", "an_dt": "20-Apr-2026 17:09:13", "desc": "Record Date",
     "attchmntText": "Vedanta Limited has informed the Exchange that Record date for the purpose of Scheme of Arrangement has been fixed as May 01, 2026",
     "attchmntFile": "https://nsearchives.nseindia.com/corporate/VEDL_x.pdf"},
    {"symbol": "VEDL", "an_dt": "29-Apr-2026 17:00", "desc": "Updates", "attchmntText": "Post-Demerger Update", "attchmntFile": "https://nsearchives.nseindia.com/corporate/VEDL_y.pdf"},
    {"symbol": "VEDL", "an_dt": "garbled", "desc": "Updates", "attchmntText": "x"},
    {"symbol": "VEDL", "desc": "Updates"},
]


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response, self.headers, self.calls = response, {}, []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, params))
        return FakeResponse(403) if url.endswith("nseindia.com") else self.response


def test_rows_become_news_items_newest_first_with_ist_times_and_bad_rows_dropped():
    session = FakeSession(FakeResponse(payload=ROWS))
    items = NseAnnouncements(session)("VEDL", date(2026, 4, 20), date(2026, 5, 1))
    assert [i.published for i in items] == [datetime(2026, 4, 29, 17, 0, tzinfo=IST), datetime(2026, 4, 20, 17, 9, 13, tzinfo=IST)]
    first = items[1]
    assert first.publisher == "NSE" and first.source == "nse" and first.title.startswith("Record Date: Vedanta Limited has informed")
    assert first.link.endswith("VEDL_x.pdf")


def test_the_request_carries_the_symbol_and_a_dd_mm_yyyy_window_and_the_session_is_primed_once():
    session = FakeSession(FakeResponse(payload=[]))
    client = NseAnnouncements(session)
    client("VEDL", date(2026, 4, 20), date(2026, 5, 1))
    client("KOTAKBANK", date(2026, 1, 8), date(2026, 1, 16))
    api = [c for c in session.calls if "corporate-announcements" in c[0]]
    assert api[0][1] == {"index": "equities", "symbol": "VEDL", "from_date": "20-04-2026", "to_date": "01-05-2026"}
    assert sum(c[0].endswith("nseindia.com") for c in session.calls) == 1 and len(api) == 2
    assert "Mozilla" in session.headers["User-Agent"]


@pytest.mark.parametrize("response", [FakeResponse(403), FakeResponse(payload={"error": 1}), FakeResponse(payload=None)])
def test_a_blocked_or_odd_reply_is_an_athena_error_naming_the_symbol(response):
    with pytest.raises(AthenaError, match="NSE announcements unavailable for VEDL"):
        NseAnnouncements(FakeSession(response))("VEDL", date(2026, 4, 20), date(2026, 5, 1))
