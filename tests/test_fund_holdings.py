import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.contracts import AthenaError
from athena.fundamentals import holdings
from athena.fundamentals.shareholding import parse_shareholding
from athena.fundamentals.store import FundamentalsStore
from athena.fundamentals.universe import Member

FIXTURES = Path(__file__).parent / "fixtures" / "shp"
TCS = Member("TCS", "Tata Consultancy Services", "IT", "INE467B01029")
NEW = (FIXTURES / "tcs_2026.xml").read_text(encoding="utf-8")
OLD = (FIXTURES / "tcs_2024.xml").read_text(encoding="utf-8")


def row(day, xbrl, broadcast="21-JUL-2026 15:44:00"):
    return {"date": day, "broadcastDate": broadcast, "xbrl": xbrl}


class Response:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def json(self):
        return self._payload


class FakeResults:
    """`listing` is what the exchange's list answers; `files` maps a link to its text or an error."""

    def __init__(self, listing, files):
        self.listing, self.files, self.fetched = listing, files, []

    def _get(self, url, params=None):
        if url == holdings.LISTING:
            if isinstance(self.listing, Exception):
                raise self.listing
            return Response(self.listing)
        self.fetched.append(url)
        value = self.files[url]
        if isinstance(value, Exception):
            raise value
        return Response(text=value)


def test_both_layouts_give_the_same_groups_and_percentages_from_the_shares():
    new, old = parse_shareholding(NEW), parse_shareholding(OLD)
    assert (new.symbol, new.isin, new.period_end) == ("TCS", "INE467B01029", date(2026, 6, 30))
    assert old.period_end == date(2024, 3, 31)
    assert new.percent("promoters") == pytest.approx(71.77, abs=0.01) == old.percent("promoters")
    assert new.percent("fii") == pytest.approx(9.06, abs=0.01) and old.percent("fii") == pytest.approx(12.70, abs=0.01)
    assert new.percent("dii") == pytest.approx(13.47, abs=0.01) and old.percent("dii") == pytest.approx(10.67, abs=0.01)
    assert new.percent("total") == pytest.approx(100.0)
    assert new.groups["total"].holders == 2605182 and old.groups["total"].holders == 2203209
    assert sum(new.percent(k) for k in ("promoters", "fii", "dii", "government", "public")) == pytest.approx(100.0, abs=0.05)


def test_a_file_that_is_not_a_shareholding_filing_is_refused():
    for bad in ("<html>error</html>", "<xbrl xmlns='http://www.xbrl.org/2003/instance'/>"):
        with pytest.raises(ValueError):
            parse_shareholding(bad)


def test_the_listing_gives_newest_first_with_a_link_that_works_and_a_revision_replaces_its_original():
    rows = [
        row("31-MAR-2026", "/corporate/xbrl/a.xml", "16-SEP-2026 07:38:06"),
        row("30-JUN-2026", "https://nsearchives.nseindia.com/corporate/xbrl/b.xml"),
        row("31-MAR-2026", "/corporate/xbrl/a2.xml", "20-SEP-2026 07:38:06"),  # filed again later
        row("31-DEC-2025", ""),  # no link
        row("soon", "/corporate/xbrl/c.xml"),
        {"date": "30-SEP-2025", "xbrl": "/corporate/xbrl/d.xml"},  # no broadcast time or submission date
    ]
    refs = holdings.refs_from(rows)
    assert [(r.period_end.date(), r.url.rsplit("/", 1)[-1]) for r in refs] == [(date(2026, 6, 30), "b.xml"), (date(2026, 3, 31), "a2.xml")]
    assert refs[1].url.startswith("https://nsearchives.nseindia.com/")


def test_a_company_is_fetched_parsed_and_stored_and_a_second_run_fetches_nothing(tmp_path):
    results = FakeResults([row("30-JUN-2026", "/x/new.xml"), row("31-MAR-2024", "/x/old.xml", "18-APR-2024 05:05:00")], {f"{holdings.ARCHIVE}/x/new.xml": NEW, f"{holdings.ARCHIVE}/x/old.xml": OLD})
    path = tmp_path / "f.duckdb"
    report = holdings.ingest_symbol(TCS, results, path)
    assert (report.fetched, report.failed) == (2, 0)
    with FundamentalsStore(path) as store:
        held = store.holdings("TCS")
        assert sorted(held) == [date(2024, 3, 31), date(2026, 6, 30)]
        assert held[date(2026, 6, 30)]["promoters"] == (2596558628.0, 5.0)
        assert store.filing_status(f"{holdings.ARCHIVE}/x/new.xml") == "ok"
    again = holdings.ingest_symbol(TCS, results, path)
    assert (again.fetched, again.skipped) == (0, 2)
    assert len(results.fetched) == 2
    refreshed = holdings.ingest_symbol(TCS, results, path, refresh=True)
    assert refreshed.fetched == 2
    with FundamentalsStore(path) as store:
        assert len(store.holdings("TCS")[date(2026, 6, 30)]) == len(parse_shareholding(NEW).groups)  # replaced, not doubled


def test_only_the_latest_quarters_asked_for_are_kept(tmp_path):
    results = FakeResults([row("30-JUN-2026", "/x/new.xml"), row("31-MAR-2024", "/x/old.xml", "18-APR-2024 05:05:00")], {f"{holdings.ARCHIVE}/x/new.xml": NEW, f"{holdings.ARCHIVE}/x/old.xml": OLD})
    report = holdings.ingest_symbol(TCS, results, tmp_path / "f.duckdb", quarters=1)
    assert report.fetched == 1 and results.fetched == [f"{holdings.ARCHIVE}/x/new.xml"]


def test_an_unreadable_file_is_recorded_and_the_rest_go_on(tmp_path):
    results = FakeResults([row("30-JUN-2026", "/x/new.xml"), row("31-MAR-2024", "/x/bad.xml", "18-APR-2024 05:05:00")], {f"{holdings.ARCHIVE}/x/new.xml": NEW, f"{holdings.ARCHIVE}/x/bad.xml": "<html>nope</html>"})
    report = holdings.ingest_symbol(TCS, results, tmp_path / "f.duckdb")
    assert (report.fetched, report.failed) == (1, 1)
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.filing_status(f"{holdings.ARCHIVE}/x/bad.xml") == "failed"


def test_a_refusal_stops_the_run_records_the_block_and_a_rerun_waits(tmp_path):
    results = FakeResults(AthenaError("NSE shareholding list unavailable for TCS: HTTP 403"), {})
    path = tmp_path / "f.duckdb"
    out = io.StringIO()
    kwargs = dict(resume=False, max_age_days=30, limit=None, quarters=12)
    assert holdings.run([TCS], results, path, out=out, **kwargs) == 3
    assert "BLOCKED" in out.getvalue()
    with FundamentalsStore(path) as store:
        assert store.run("TCS#shp")["status"] == "blocked"
        assert store.blocked_until() > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=20)
    assert holdings.run([TCS], results, path, out=io.StringIO(), **kwargs) == 3


def test_a_list_failure_that_is_not_a_refusal_is_counted_and_resume_skips_a_finished_company(tmp_path):
    path = tmp_path / "f.duckdb"
    bad = FakeResults(AthenaError("NSE shareholding list unavailable for TCS: HTTP 500"), {})
    assert holdings.run([TCS], bad, path, resume=False, max_age_days=30, limit=None, quarters=12, out=io.StringIO()) == 0
    good = FakeResults([row("30-JUN-2026", "/x/new.xml")], {f"{holdings.ARCHIVE}/x/new.xml": NEW})
    out = io.StringIO()
    holdings.run([TCS], good, path, resume=True, max_age_days=30, limit=None, quarters=12, out=out)
    assert "fetched   1" in out.getvalue()  # the failed company was tried again
    out = io.StringIO()
    holdings.run([TCS], good, path, resume=True, max_age_days=30, limit=None, quarters=12, out=out)
    assert "skipped (finished" in out.getvalue()
