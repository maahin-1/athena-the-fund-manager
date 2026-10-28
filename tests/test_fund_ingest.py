import calendar
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.contracts import AthenaError
from athena.fundamentals import ingest
from athena.fundamentals.store import FundamentalsStore
from athena.fundamentals.universe import Member
from athena.pit.nse_results import FilingRef

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
IST = timezone(timedelta(hours=5, minutes=30))
TCS = Member("TCS", "Tata Consultancy Services", "Information Technology", "INE467B01029")


def ref(end, annual, consolidated=True, filed=None, url=None):
    return FilingRef("TCS", end, filed or datetime(end.year, end.month, 28, 12, 0, tzinfo=IST), consolidated, annual, url or f"u-{end}-{annual}-{consolidated}")


def quarter_ends(n):
    year, month, out = 2025, 3, []
    for _ in range(n):
        out.append(date(year, month, calendar.monthrange(year, month)[1]))
        month -= 3
        if month < 1:
            month, year = month + 12, year - 1
    return out


class FakeResults:
    def __init__(self, refs, texts):
        self.refs, self.texts, self.fetched = refs, texts, []

    def filings(self, symbol):
        value = self.refs
        if isinstance(value, Exception):
            raise value
        return list(value)

    def text(self, r):
        self.fetched.append(r.url)
        value = self.texts[r.url]
        if isinstance(value, Exception):
            raise value
        return value


def test_wanted_takes_consolidated_annuals_and_the_latest_quarters_newest_first():
    refs = [ref(date(y, 3, 31), True) for y in (2022, 2023, 2024)] + [ref(e, False) for e in quarter_ends(15)] + [ref(date(2024, 3, 31), True, False)]
    chosen = ingest.wanted(refs, quarters=13)
    assert len(chosen) == 3 + 13
    assert all(r.consolidated for r in chosen)
    assert [r.period_end for r in chosen] == sorted((r.period_end for r in chosen), reverse=True)
    assert len(ingest.wanted(refs, quarters=13, standalone=True)) > len(chosen)


def test_a_company_with_no_consolidated_filings_falls_back_to_standalone():
    refs = [ref(date(2024, 3, 31), True, False), ref(date(2023, 3, 31), True, False)]
    assert len(ingest.wanted(refs)) == 2


def test_a_revised_filing_for_the_same_period_wins_over_the_original():
    original = ref(date(2024, 3, 31), True, filed=datetime(2024, 4, 10, tzinfo=IST), url="orig")
    revised = ref(date(2024, 3, 31), True, filed=datetime(2024, 5, 10, tzinfo=IST), url="rev")
    assert [r.url for r in ingest.wanted([original, revised])] == ["rev"]


def test_ingest_saves_a_filing_and_a_second_run_does_not_fetch_it_again(tmp_path):
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    filing = ref(date(2024, 3, 31), True, url="tcs24")
    results = FakeResults([filing], {"tcs24": text})
    path = tmp_path / "f.duckdb"
    report = ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    assert (report.fetched, report.failed) == (1, 0)
    with FundamentalsStore(path) as store:
        assert store.filing_status("tcs24") == "ok"
        assert store.company("TCS")["layout"] == "indas"
        assert store.lines("TCS", "consolidated", "income")
    again = ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    assert (again.fetched, again.skipped) == (0, 1)
    assert results.fetched == ["tcs24"]


def test_an_unreadable_filing_is_recorded_and_the_rest_go_on(tmp_path):
    good = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="good"), ref(date(2023, 3, 31), True, url="bad")], {"good": good, "bad": "<html>error</html>"})
    report = ingest.ingest_symbol(TCS, results, tmp_path / "f.duckdb", cache_dir=tmp_path / "cache")
    assert (report.fetched, report.failed) == (1, 1)
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.filing_status("bad") == "failed"


def test_a_refusal_from_the_exchange_stops_the_run_and_records_the_block(tmp_path):
    results = FakeResults([ref(date(2024, 3, 31), True, url="u")], {"u": AthenaError("HTTP 403")})
    path = tmp_path / "f.duckdb"
    with pytest.raises(ingest.Blocked):
        ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    out = io.StringIO()
    code = ingest.run([TCS], results, path, resume=False, max_age_days=30, limit=None, quarters=13, standalone=False, out=out)
    assert code == 3
    with FundamentalsStore(path) as store:
        assert store.run("TCS")["status"] == "blocked"
        assert store.blocked_until() > datetime.now(timezone.utc).replace(tzinfo=None)
    # while the block lasts, a new run refuses to start
    assert ingest.run([TCS], results, path, resume=True, max_age_days=30, limit=None, quarters=13, standalone=False, out=io.StringIO()) == 3


def test_resume_skips_a_company_finished_recently(tmp_path):
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="tcs24")], {"tcs24": text})
    path = tmp_path / "f.duckdb"
    args = dict(resume=True, max_age_days=30, limit=None, quarters=13, standalone=False)
    assert ingest.run([TCS], results, path, out=io.StringIO(), **args) == 0
    listed = []
    results.filings = lambda symbol: listed.append(symbol) or []  # a resumed run must not even ask for the list
    out = io.StringIO()
    assert ingest.run([TCS], results, path, out=out, **args) == 0
    assert listed == [] and "skipped" in out.getvalue()


def test_refresh_reads_a_stored_filing_again_from_the_kept_copy_and_drops_what_the_old_mapping_left(tmp_path):
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="tcs24")], {"tcs24": text})
    path = tmp_path / "f.duckdb"
    ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    with FundamentalsStore(path) as store:  # a row an earlier mapping stored by mistake
        store._con.execute(
            "INSERT INTO statement_lines VALUES ('TCS', 'consolidated', 'income', DATE '2023-04-01', DATE '2024-03-31', 'wrong_row', 1.0, 'tcs24', now(), 'nse-xbrl')"
        )
    plain = ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    assert plain.skipped == 1  # without --refresh the stored filing is left alone
    again = ingest.ingest_symbol(TCS, results, path, refresh=True, cache_dir=tmp_path / "cache")
    assert (again.fetched, again.skipped) == (1, 0)
    assert results.fetched == ["tcs24"]  # still one download: the kept copy was read
    with FundamentalsStore(path) as store:
        assert all("wrong_row" not in got for got in store.lines("TCS", "consolidated", "income").values())
        assert store.lines("TCS", "consolidated", "income")


def test_earlier_symbols_of_a_renamed_company_are_fetched_after_it_and_never_twice():
    from athena.fundamentals.history import Predecessor

    table = {"NEW": (Predecessor("OLD", "renamed"),)}
    members = [Member("NEW", "New Ltd", "IT", "INE1"), Member("OTHER", "Other", "IT", "INE2"), Member("OLD", "Old Ltd", "IT", "INE1")]
    out = ingest.with_predecessors(members, table)
    assert [m.symbol for m in out] == ["NEW", "OLD", "OTHER"]
    assert out[1].name == "New Ltd"  # the earlier symbol is the same company
    assert ingest.with_predecessors([Member("OTHER", "Other", "IT", "INE2")], table)[0].symbol == "OTHER"


def test_a_filing_the_exchange_answers_404_for_is_remembered_as_gone_and_not_asked_for_again_until_refreshed(tmp_path):
    good = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="good"), ref(date(2023, 3, 31), True, url="gone")], {"good": good, "gone": AthenaError("HTTP 404")})
    path = tmp_path / "f.duckdb"
    kwargs = dict(resume=False, max_age_days=30, limit=None, quarters=13, standalone=False, out=io.StringIO(), cache_dir=tmp_path / "cache")
    ingest.run([TCS], results, path, **kwargs)
    with FundamentalsStore(path) as store:
        assert store.run("TCS")["status"] == "ok"  # a dead link is not a reason to retry the company on every resume
        assert store.filing_state("gone")[0] == "gone"
    asked = results.fetched.count("gone")
    results.texts["gone"] = good
    ingest.run([TCS], results, path, **kwargs)
    assert results.fetched.count("gone") == asked  # not asked again
    ingest.run([TCS], results, path, **{**kwargs, "refresh": True})
    with FundamentalsStore(path) as store:
        assert store.filing_state("gone")[0] == "ok"


def test_a_company_with_one_unreadable_filing_is_partial_and_resume_tries_it_again(tmp_path):
    good = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="good"), ref(date(2023, 3, 31), True, url="bad")], {"good": good, "bad": AthenaError("HTTP 500")})
    path = tmp_path / "f.duckdb"
    kwargs = dict(resume=True, max_age_days=30, limit=None, quarters=13, standalone=False, out=io.StringIO(), cache_dir=tmp_path / "cache")
    ingest.run([TCS], results, path, **kwargs)
    with FundamentalsStore(path) as store:
        assert store.run("TCS")["status"] == "partial"
    results.texts["bad"] = good  # the exchange serves it now
    ingest.run([TCS], results, path, **kwargs)
    with FundamentalsStore(path) as store:
        assert store.run("TCS")["status"] == "ok"


# ---- a run that stops by itself when the exchange has gone quiet

def silent(symbol):
    return Member(symbol, symbol, "", "")


def test_five_companies_in_a_row_that_fail_outright_stop_the_run_and_a_new_run_waits(tmp_path):
    from athena.fundamentals import runner

    path = tmp_path / "f.duckdb"
    members = [silent(f"S{i}") for i in range(8)]
    seen = []

    def fetch(member):
        seen.append(member.symbol)
        return ingest.Report(member.symbol, failed=1, notes=["NSE results unavailable: ReadTimeout"])

    out = io.StringIO()
    code = runner.run_each(members, fetch, path, key=lambda m: m.symbol, resume=False, max_age_days=7, limit=None, out=out)
    assert code == 3 and seen == ["S0", "S1", "S2", "S3", "S4"]
    assert "stopping" in out.getvalue() and "no reply" in out.getvalue()
    with FundamentalsStore(path) as store:
        assert store.run("S4")["status"] == "blocked" and store.run("S3")["status"] == "failed"
    assert runner.run_each(members, fetch, path, key=lambda m: m.symbol, resume=False, max_age_days=7, limit=None, out=io.StringIO()) == 3
    assert seen == ["S0", "S1", "S2", "S3", "S4"]  # nothing was asked for while the pause lasts


def test_one_company_that_gets_through_resets_the_count(tmp_path):
    from athena.fundamentals import runner

    path = tmp_path / "f.duckdb"
    members = [silent(f"S{i}") for i in range(9)]

    def fetch(member):
        if member.symbol == "S4":
            return ingest.Report(member.symbol, fetched=12)
        return ingest.Report(member.symbol, failed=1, notes=["x"])

    assert runner.run_each(members, fetch, path, key=lambda m: m.symbol, resume=False, max_age_days=7, limit=None, out=io.StringIO()) == 0


def test_a_company_with_nothing_to_fetch_is_not_a_failure(tmp_path):
    from athena.fundamentals import runner

    members = [silent(f"S{i}") for i in range(8)]  # delisted names list no filings: fetched 0, failed 0
    assert runner.run_each(members, lambda m: ingest.Report(m.symbol), tmp_path / "f.duckdb", key=lambda m: m.symbol, resume=False, max_age_days=7, limit=None, out=io.StringIO()) == 0


def test_the_statements_run_stops_the_same_way(tmp_path):
    results = FakeResults(AthenaError("NSE results unavailable for X: ReadTimeout"), {})
    out = io.StringIO()
    code = ingest.run([silent(f"S{i}") for i in range(8)], results, tmp_path / "f.duckdb", resume=False, max_age_days=7, limit=None, quarters=13, standalone=False, out=out)
    assert code == 3 and out.getvalue().count("failed   1") == 5
