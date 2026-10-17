import io
from datetime import datetime, timedelta, timezone

from athena.contracts import AthenaError
from athena.fundamentals import documents
from athena.fundamentals.store import FundamentalsStore
from athena.fundamentals.universe import Member

TCS = Member("TCS", "Tata Consultancy Services", "IT", "INE467B01029")
HOST = "https://nsearchives.nseindia.com"
IST = timezone(timedelta(hours=5, minutes=30))


def announcement(when, category, text, file, size="100 KB"):
    return {"an_dt": when, "desc": category, "attchmntText": text, "attchmntFile": f"{HOST}/corporate/{file}", "attFileSize": size}


CALL = "Analysts/Institutional Investor Meet/Con. Call Updates"
ANNOUNCEMENTS = [
    announcement("16-Oct-2026 16:29:00", CALL, "Tata Consultancy Services Limited has informed the Exchange about Schedule of meet", "a.pdf"),
    announcement("14-Oct-2026 16:00:08", CALL, "Tata Consultancy Services Limited has informed the Exchange about Transcript", "b.pdf"),
    announcement("12-Oct-2026 09:00:00", "Press Release", "Tata Consultancy Services Limited has informed the Exchange about Press release on results", "c.pdf"),
    announcement("10-Oct-2026 09:00:00", "Loss of Share Certificates", "x", "d.pdf"),
    announcement("09-Oct-2026 09:00:00", "Record Date", "x", "e.pdf"),
    announcement("01-Jan-2017 09:00:00", CALL, "before the window", "f.pdf"),
    {"an_dt": "08-Oct-2026 09:00:00", "desc": "Updates", "attchmntText": "bad host", "attchmntFile": "https://evil.example/x.pdf"},
    {"an_dt": "garbage", "desc": "Updates", "attchmntText": "bad date", "attchmntFile": f"{HOST}/corporate/g.pdf"},
]
ANNUAL = {
    "data": [
        {"fromYr": "2025", "toYr": "2026", "broadcast_dttm": "15-MAY-2026 23:48:30", "fileName": f"{HOST}/annual_reports/AR_1.pdf", "attFileSize": "16.62 MB"},
        {"fromYr": "2018", "toYr": "2019", "broadcast_dttm": "-", "fileName": f"{HOST}/annual_reports/AR_2.zip", "attFileSize": None},
        {"fromYr": "2017", "toYr": "2018", "broadcast_dttm": "20-JUN-2018 18:38:13", "fileName": "http://insecure.example/AR_3.zip"},
        {"fromYr": "x", "toYr": "2016", "fileName": f"{HOST}/annual_reports/AR_4.zip"},
    ]
}


def test_annual_reports_are_listed_newest_year_first_and_an_unknown_broadcast_time_does_not_drop_one():
    docs = documents.parse_annual(ANNUAL)
    assert [d.title for d in docs] == ["FY2025-26", "FY2018-19"]
    assert docs[0].size == "16.62 MB" and docs[1].published.date().isoformat() == "2019-03-31"
    assert all(d.url.startswith(HOST) for d in docs)  # a link off the exchange's host or an unreadable year is left out
    assert documents.parse_annual({"data": "nope"}) == [] and documents.parse_annual(None) == []


def test_announcements_split_into_investor_call_items_and_others_with_routine_notices_and_old_ones_dropped():
    docs = documents.parse_announcements(ANNOUNCEMENTS)
    assert [(d.kind, d.title) for d in docs] == [("concall", "Schedule of meet"), ("concall", "Transcript"), ("announcement", "Press release on results")]
    assert docs[0].published == datetime(2026, 10, 16, 16, 29, tzinfo=IST)


def test_only_the_newest_other_announcements_are_kept_but_every_call_item_is(monkeypatch):
    monkeypatch.setattr(documents, "KEEP_OTHER", 2)
    rows = [announcement(f"{day:02d}-Oct-2026 10:00:00", "Press Release", f"n{day}", f"p{day}.pdf") for day in range(1, 10)]
    rows += [announcement(f"{day:02d}-Sep-2026 10:00:00", CALL, f"c{day}", f"c{day}.pdf") for day in range(1, 6)]
    docs = documents.parse_announcements(rows)
    assert sum(d.kind == "concall" for d in docs) == 5
    assert [d.title for d in docs if d.kind == "announcement"] == ["n9", "n8"]


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeResults:
    def __init__(self, annual, announcements):
        self.answers = {documents.ANNUAL: annual, documents.ANNOUNCEMENTS: announcements}
        self.calls = []

    def _get(self, url, params=None):
        self.calls.append((url, params))
        value = self.answers[url]
        if isinstance(value, Exception):
            raise value
        return Response(value)


def test_a_company_is_fetched_and_stored_and_a_new_listing_replaces_the_old_one(tmp_path):
    results = FakeResults(ANNUAL, ANNOUNCEMENTS)
    path = tmp_path / "f.duckdb"
    report = documents.ingest_symbol(TCS, results, path)
    assert (report.fetched, report.failed) == (5, 0)
    assert [p for _, p in results.calls] == [{"index": "equities", "symbol": "TCS"}] * 2
    with FundamentalsStore(path) as store:
        assert [d["title"] for d in store.documents("TCS", "annual_report")] == ["FY2025-26", "FY2018-19"]
        assert [d["title"] for d in store.documents("TCS", "concall")] == ["Schedule of meet", "Transcript"]
    results.answers[documents.ANNOUNCEMENTS] = [ANNOUNCEMENTS[1]]
    documents.ingest_symbol(TCS, results, path)
    with FundamentalsStore(path) as store:
        assert [d["title"] for d in store.documents("TCS", "concall")] == ["Transcript"]  # what the exchange lists now is what is kept
        assert len(store.documents("TCS", "annual_report")) == 2


def test_one_list_failing_keeps_the_other_and_a_refusal_stops_the_run(tmp_path):
    path = tmp_path / "f.duckdb"
    half = FakeResults(AthenaError("HTTP 500"), ANNOUNCEMENTS)
    report = documents.ingest_symbol(TCS, half, path)
    assert report.failed == 1 and report.fetched == 3
    blocked = FakeResults(ANNUAL, AthenaError("HTTP 403"))
    out = io.StringIO()
    assert documents.run([TCS], blocked, path, resume=False, max_age_days=7, limit=None, out=out) == 3
    assert "BLOCKED" in out.getvalue()
    with FundamentalsStore(path) as store:
        assert store.run("TCS#docs")["status"] == "blocked"
