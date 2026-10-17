from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.fundamentals.integrated import FundamentalsResults, integrated_ref

LEGACY_ROW = {
    "toDate": "31-Dec-2024", "filingDate": "09-Jan-2025 21:39:00", "consolidated": "Consolidated",
    "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_117182_1341298_09012025093940.xml",
}
NEW_ROW = {
    "symbol": "TCS", "qe_Date": "30-SEP-2026", "broadcast_Date": "08-Oct-2026 15:40:12", "consolidated": "Consolidated", "type_Sub": "Original",
    "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1734607_08102026100810_WEB.xml",
}


class Response:
    def __init__(self, payload=None, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        return self._payload


class FakeSession:
    """Answers the three kinds of request the client makes. `integrated` is a list of pages (each a list of rows); `legacy` an object or an error status."""

    def __init__(self, legacy=None, integrated=None, legacy_status=200, integrated_status=200, total=None):
        self.headers = {}
        self.legacy, self.integrated = legacy or [], integrated or [[]]
        self.legacy_status, self.integrated_status, self.total = legacy_status, integrated_status, total
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "integrated-filing-results" in url:
            if self.integrated_status != 200:
                return Response(status=self.integrated_status)
            page = int(params.get("page", 1))
            rows = self.integrated[page - 1] if page <= len(self.integrated) else []
            total = self.total if self.total is not None else sum(len(p) for p in self.integrated)
            return Response({"data": rows, "page": page, "size": params.get("size"), "totalCount": total})
        if "corporates-financial-results" in url:
            rows = self.legacy if (params or {}).get("period") == "Quarterly" else []  # the client asks for the quarterly and the annual list
            return Response(rows, status=self.legacy_status)
        return Response(text="<xbrl/>")  # the home page, or a filing


def results(session):
    return FundamentalsResults(session, sleep=lambda s: None, clock=lambda: 1e9)


def test_a_new_listing_row_becomes_a_filing_reference():
    ref = integrated_ref("TCS", NEW_ROW)
    assert (ref.symbol, ref.period_end, ref.consolidated) == ("TCS", date(2026, 9, 30), True)
    assert ref.filed.year == 2026 and ref.filed.month == 10 and ref.filed.hour == 15
    assert ref.url.endswith("_WEB.xml")
    assert ref.annual is False
    assert integrated_ref("TCS", {**NEW_ROW, "qe_Date": "31-MAR-2026"}).annual is True  # a March filing carries the whole year
    assert integrated_ref("TCS", {**NEW_ROW, "consolidated": "Standalone"}).consolidated is False


def test_rows_without_a_usable_xbrl_link_or_date_are_skipped():
    assert integrated_ref("TCS", {**NEW_ROW, "xbrl": "https://nsearchives.nseindia.com/corporate/null"}) is None
    assert integrated_ref("TCS", {**NEW_ROW, "xbrl": ""}) is None
    assert integrated_ref("TCS", {**NEW_ROW, "qe_Date": "soon"}) is None
    assert integrated_ref("TCS", {**NEW_ROW, "broadcast_Date": "-"}) is None


def test_filings_are_the_old_list_followed_by_the_new_one():
    session = FakeSession(legacy=[LEGACY_ROW], integrated=[[NEW_ROW]])
    refs = results(session).filings("TCS")
    assert [r.period_end for r in refs] == [date(2024, 12, 31), date(2026, 9, 30)]  # oldest first, as NseResults returns them
    asked = [(u.rsplit("/", 1)[-1], p.get("symbol")) for u, p in session.calls if "/api/" in u]
    assert ("integrated-filing-results", "TCS") in asked


def test_a_long_listing_is_read_page_by_page():
    pages = [[{**NEW_ROW, "qe_Date": "30-SEP-2026"}], [{**NEW_ROW, "qe_Date": "30-JUN-2026", "xbrl": NEW_ROW["xbrl"].replace("1734607", "1690004")}]]
    session = FakeSession(integrated=pages, total=2)
    refs = results(session).integrated("TCS", size=1)
    assert len(refs) == 2
    pages_asked = [p["page"] for u, p in session.calls if "integrated-filing-results" in u]
    assert pages_asked == [1, 2]


def test_one_list_failing_does_not_hide_the_other():
    refs = results(FakeSession(legacy=[LEGACY_ROW], integrated_status=500)).filings("TCS")
    assert [r.period_end for r in refs] == [date(2024, 12, 31)]
    refs = results(FakeSession(legacy_status=500, integrated=[[NEW_ROW]])).filings("TCS")
    assert [r.period_end for r in refs] == [date(2026, 9, 30)]


def test_both_lists_failing_is_an_error_and_a_refusal_is_reported_as_one():
    with pytest.raises(AthenaError):
        results(FakeSession(legacy_status=500, integrated_status=500)).filings("TCS")
    with pytest.raises(AthenaError, match="HTTP 403"):
        results(FakeSession(legacy=[LEGACY_ROW], integrated_status=403)).filings("TCS")
