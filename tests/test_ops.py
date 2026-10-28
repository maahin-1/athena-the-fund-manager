import io
import json
import os
from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from athena.contracts import AthenaError
from athena.fundamentals import annual_reports as ar
from athena.fundamentals import pdf_extract as pe
from athena.fundamentals import runner
from athena.fundamentals.ingest import Blocked, Report, Stall
from athena.fundamentals.store import FundamentalsStore
from athena.fundamentals.universe import Member
from athena.ops import cli, gates
from athena.ops.jobs import Job, JobLocked, all_status, pid_alive, read_status

IST = timezone(timedelta(hours=5, minutes=30))
EVENING = datetime(2026, 10, 27, 22, 0, tzinfo=IST)  # a Tuesday after the end-of-day files are out


def member(symbol):
    return Member(symbol, symbol, "", "")


# ---- jobs and locks
def test_a_job_keeps_a_status_file_and_says_how_it_ended(tmp_path):
    with Job("demo", status_dir=tmp_path / "s", lock_dir=tmp_path / "l") as job:
        job.beat(done=3, last="TCS")
        assert read_status("demo", tmp_path / "s")["state"] == "running"
        job.note_error("one thing went wrong")
    row = read_status("demo", tmp_path / "s")
    assert row["state"] == "done" and row["progress"]["done"] == 3 and row["last_error"] == "one thing went wrong" and row["pid"] == os.getpid()
    with pytest.raises(RuntimeError):
        with Job("bad", status_dir=tmp_path / "s", lock_dir=tmp_path / "l"):
            raise RuntimeError("boom")
    assert read_status("bad", tmp_path / "s")["state"] == "failed" and "boom" in read_status("bad", tmp_path / "s")["last_error"]
    assert [r["name"] for r in all_status(tmp_path / "s")] == ["bad", "demo"]


def test_a_lock_held_by_a_running_process_refuses_a_second_job_and_a_dead_holders_lock_is_taken_over(tmp_path):
    lock_dir = tmp_path / "l"
    lock_dir.mkdir()
    (lock_dir / "nse.lock").write_text(json.dumps({"pid": os.getppid(), "job": "other"}))  # the parent process is running
    with pytest.raises(JobLocked, match="nse"):
        with Job("second", locks=("nse",), status_dir=tmp_path / "s", lock_dir=lock_dir):
            pass
    (lock_dir / "nse.lock").write_text(json.dumps({"pid": 2_000_000_000, "job": "dead"}))  # no such process
    with Job("third", locks=("nse", "writer:x.duckdb"), status_dir=tmp_path / "s", lock_dir=lock_dir):
        assert json.loads((lock_dir / "nse.lock").read_text())["job"] == "third"
    assert not (lock_dir / "nse.lock").exists() and not list(lock_dir.glob("*.lock"))  # released on exit


def test_pid_alive_is_true_for_this_process_and_false_for_a_missing_one():
    assert pid_alive(os.getpid()) and not pid_alive(2_000_000_000) and not pid_alive(0)


def test_a_dead_job_that_still_says_running_is_reported_died_and_a_silent_one_stalled(tmp_path):
    now = datetime.now(timezone.utc)
    row = {"name": "holdings", "pid": 7, "state": "running", "heartbeat": now.isoformat(), "progress": {"done": 12, "last": "TCS"}, "last_error": "store busy"}
    level, line = cli.assess_job(row, now, alive=lambda pid: False)
    assert level == gates.BAD and "DIED" in line and "after 12 companies" in line and "store busy" in line
    old = {**row, "heartbeat": (now - timedelta(minutes=45)).isoformat()}
    assert "STALLED" in cli.assess_job(old, now, alive=lambda pid: True)[1]
    assert cli.assess_job(row, now, alive=lambda pid: True)[0] == gates.OK
    assert cli.assess_job({**row, "state": "stopped", "last_error": "model down"}, now)[0] == gates.WARN
    assert cli.assess_job({**row, "state": "failed"}, now)[0] == gates.BAD


# ---- the shared job loop
class Fetch:
    """A fetch that raises or returns what each company is told to do."""

    def __init__(self, plan):
        self.plan, self.calls = dict(plan), []

    def __call__(self, m):
        self.calls.append(m.symbol)
        what = self.plan.get(m.symbol, Report(m.symbol, fetched=1))
        if isinstance(what, Exception):
            raise what
        return what


def go(tmp_path, members, fetch, **kw):
    out = io.StringIO()
    code = runner.run_each(members, fetch, tmp_path / "f.duckdb", key=lambda m: m.symbol, resume=False, max_age_days=30, limit=None, out=out, sleep=lambda s: None, **kw)
    return code, out.getvalue()


def test_one_company_failing_with_an_unforeseen_error_is_recorded_and_the_run_goes_on(tmp_path):
    fetch = Fetch({"B": ValueError("a report nobody foresaw")})
    code, text = go(tmp_path, [member(s) for s in "ABC"], fetch)
    assert code == 0 and fetch.calls == ["A", "B", "C"] and "B            ERROR ValueError" in text
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.run("B")["status"] == "failed" and "foresaw" in store.run("B")["error"] and store.run("C")["status"] == "ok"


def test_a_busy_store_is_waited_for_and_tried_once_more_and_five_in_a_row_stop_the_run(tmp_path):
    calls = {"n": 0}

    def flaky(m):
        calls["n"] += 1
        if calls["n"] == 1:
            raise duckdb.IOException("Insufficient system resources")
        return Report(m.symbol, fetched=1)

    code, text = go(tmp_path, [member("A")], flaky)
    assert code == 0 and calls["n"] == 2 and "waiting 20s" in text
    broken = Fetch({s: duckdb.IOException("ReadFile failed") for s in "ABCDEFG"})
    code, text = go(tmp_path / "x", [member(s) for s in "ABCDEFG"], broken)
    assert code == runner.EXIT_STORE and "the store failed" in text and len(broken.calls) == 10  # five companies, each tried twice


def test_the_model_not_answering_stops_this_run_alone_and_does_not_pause_the_exchange_jobs(tmp_path):
    model = Report("X", failed=1, model_failed=1, notes=["all sources failed"])
    fetch = Fetch({s: model for s in "ABCDEFG"})
    code, text = go(tmp_path, [member(s) for s in "ABCDEFG"], fetch)
    assert code == runner.EXIT_MODEL and "language model did not answer" in text and "Nothing was recorded against the exchange" in text
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.blocked_until() is None  # no block row: other jobs are not paused


def test_five_exchange_failures_in_a_row_still_stop_the_run_and_pause_the_exchange_jobs(tmp_path):
    bad = Report("X", failed=1)
    code, text = go(tmp_path, [member(s) for s in "ABCDEFG"], Fetch({s: bad for s in "ABCDEFG"}))
    assert code == 3 and "no reply from the exchange" in text
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.blocked_until() is not None
    assert Stall().saw(Report("X", failed=1, model_failed=1)) is False


def test_a_blocked_exchange_stops_the_run_and_a_job_run_writes_its_status_and_refuses_a_second(tmp_path, monkeypatch):
    monkeypatch.setattr("athena.ops.jobs.STATUS_DIR", tmp_path / "status")
    monkeypatch.setattr("athena.ops.jobs.LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr("athena.fundamentals.runner.Job", lambda name, locks: Job(name, locks, tmp_path / "status", tmp_path / "locks"))
    code, text = go(tmp_path, [member("A"), member("B")], Fetch({"B": Blocked("HTTP 403")}), job="demo")
    row = read_status("demo", tmp_path / "status")
    assert code == 3 and row["state"] == "blocked" and row["exit_code"] == 3 and "HTTP 403" in row["last_error"]
    code, text = go(tmp_path / "y", [member("A")], Fetch({}), job="demo")
    assert code == 0 and read_status("demo", tmp_path / "status")["state"] == "done" and read_status("demo", tmp_path / "status")["progress"]["done"] == 1
    (tmp_path / "locks").mkdir(exist_ok=True)
    (tmp_path / "locks" / "nse.lock").write_text(json.dumps({"pid": os.getppid(), "job": "holdings"}))
    code, text = go(tmp_path / "z", [member("A")], Fetch({}), job="demo")
    assert code == runner.EXIT_LOCKED and "held by pid" in text and "holdings" in text


# ---- annual reports
def test_unit_spellings_the_model_copies_from_real_reports_are_understood():
    for unit, factor in (("lacs", 1e5), ("Lakhs", 1e5), ("Rs. in crores", 1e7), ("crore", 1e7), ("cr", 1e7), ("mn", 1e6), ("'000", 1e3), ("Rupees", 1.0)):
        reply = {"unit": unit, "years": ["2019-03-31"], "income": {"sales": [1.0]}, "balance": {}}
        assert pe.normalise(reply)[date(2019, 3, 31)]["income"]["sales"] == factor, unit
    with pytest.raises(ValueError, match="unknown unit"):
        pe.normalise({"unit": "dollars", "years": ["2019-03-31"]})


def test_statement_pages_are_cached_so_a_retry_does_not_read_the_report_again(tmp_path):
    cache = ar.BlocksCache(tmp_path / "blocks")
    assert cache.get("u", "consolidated") is None
    cache.put("u", "consolidated", {"income": "text"})
    assert cache.get("u", "consolidated") == {"income": "text"} and cache.get("u", "standalone") is None


def test_a_model_failure_in_a_report_is_flagged_as_the_models_and_a_bad_report_is_not(tmp_path):
    reports = [{"title": "FY2018-19", "url": "u", "published": datetime(2019, 3, 30)}]
    ref = {date(2019, 3, 31): {"income": {"sales": 1e10}, "balance": {}}}
    page = "Consolidated Statement of Profit and Loss Total expenses Profit before tax " + " ".join(str(1000 + i) for i in range(40))

    class Dead:
        def complete(self, system, user):
            raise AthenaError("all sources failed (nvidia: HTTP 404)")

    out = ar.chain(reports, ref, lambda url: [page], Dead(), back_to=2019, blocks_cache=ar.BlocksCache(tmp_path / "b"))
    assert out["steps"][0]["result"].startswith("unreadable") and out["steps"][0]["model"] is True
    assert ar.BlocksCache(tmp_path / "b").get("u", "") is not None  # the pages were kept for the retry

    def damaged(url):
        raise AthenaError("the archive is damaged")

    out = ar.chain(reports, ref, damaged, Dead(), back_to=2019)
    assert out["steps"][0]["model"] is False


# ---- gates
def make_warehouse(path, days, bars_per_day=10, index_days=()):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE days (day DATE PRIMARY KEY, status VARCHAR, rows INTEGER, loaded_at TIMESTAMP)")
    con.execute("CREATE TABLE bars (symbol VARCHAR, day DATE, close DOUBLE)")
    con.execute("CREATE TABLE index_days (day DATE PRIMARY KEY, rows INTEGER, loaded_at TIMESTAMP)")
    for day, status in days:
        con.execute("INSERT INTO days VALUES (?, ?, 0, now())", [day, status])
        if status == "loaded":
            n = bars_per_day(day) if callable(bars_per_day) else bars_per_day
            con.executemany("INSERT INTO bars VALUES (?, ?, 1.0)", [(f"S{i}", day) for i in range(n)])
    for day in index_days:
        con.execute("INSERT INTO index_days VALUES (?, 1, now())", [day])
    con.close()


def weekday_run(last, n):
    out, day = [], last
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return sorted(out)


def test_expected_last_day_is_today_after_nine_in_the_evening_and_the_weekday_before_otherwise():
    assert gates.expected_last_day(EVENING) == date(2026, 10, 27)
    assert gates.expected_last_day(datetime(2026, 10, 27, 10, 0, tzinfo=IST)) == date(2026, 10, 26)
    assert gates.expected_last_day(datetime(2026, 10, 26, 10, 0, tzinfo=IST)) == date(2026, 10, 23)  # a Monday morning: Friday


def test_prices_fresh_says_ok_when_current_or_when_the_gap_is_recorded_holidays_and_bad_when_days_are_missing(tmp_path):
    days = [(d, "loaded") for d in weekday_run(date(2026, 10, 27), 30)]
    make_warehouse(tmp_path / "w.duckdb", days)
    assert gates.prices_fresh(EVENING, tmp_path / "w.duckdb").level == gates.OK
    behind = [(d, "loaded") for d in weekday_run(date(2026, 10, 22), 30)]
    make_warehouse(tmp_path / "w2.duckdb", behind)
    gate = gates.prices_fresh(EVENING, tmp_path / "w2.duckdb")
    assert gate.level == gates.BAD and "not loaded" in gate.detail
    holiday = [(d, "loaded") for d in weekday_run(date(2026, 10, 22), 30)] + [(date(2026, 10, 23), "no_session"), (date(2026, 10, 26), "no_session"), (date(2026, 10, 27), "no_session")]
    make_warehouse(tmp_path / "w3.duckdb", holiday)
    assert gates.prices_fresh(EVENING, tmp_path / "w3.duckdb").level == gates.OK
    assert gates.prices_fresh(EVENING, tmp_path / "missing.duckdb").level == gates.WARN


def test_a_no_session_weekday_that_has_other_data_is_not_a_holiday_and_a_missing_weekday_is_a_hole(tmp_path):
    run = weekday_run(date(2026, 10, 27), 300)  # more than 400 calendar days of weekdays
    make_warehouse(tmp_path / "clean.duckdb", [(d, "loaded") for d in run])
    clean = gates.prices_no_holes(EVENING, tmp_path / "clean.duckdb", tmp_path / "no-derivs.duckdb")
    assert clean.level == gates.WARN and "not readable" in clean.detail  # no derivatives file here, said plainly; nothing else is wrong
    make_warehouse(tmp_path / "w.duckdb", [(d, "loaded") for d in run])
    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    con.execute("DELETE FROM days WHERE day = ?", [run[100]])  # a hole
    con.execute("UPDATE days SET status = 'no_session' WHERE day = ?", [run[120]])  # said to be a holiday ...
    con.execute("INSERT INTO index_days VALUES (?, 5, now())", [run[120]])  # ... but the index has rows for it
    con.close()
    gate = gates.prices_no_holes(EVENING, tmp_path / "w.duckdb", tmp_path / "no-derivs.duckdb")
    assert gate.level == gates.BAD and "no entry at all" in gate.detail and "were not holidays" in gate.detail


def test_a_partial_last_day_is_caught_by_breadth(tmp_path):
    days = [(d, "loaded") for d in weekday_run(date(2026, 10, 27), 25)]
    make_warehouse(tmp_path / "w.duckdb", days, bars_per_day=lambda d: 4 if d == date(2026, 10, 27) else 10)
    assert gates.prices_breadth(tmp_path / "w.duckdb").level == gates.BAD
    make_warehouse(tmp_path / "ok.duckdb", days, bars_per_day=10)
    assert gates.prices_breadth(tmp_path / "ok.duckdb").level == gates.OK


def test_derivatives_one_day_behind_is_a_warning_and_more_is_bad(tmp_path):
    make_warehouse(tmp_path / "w.duckdb", [(d, "loaded") for d in weekday_run(date(2026, 10, 27), 10)])

    def derivs(last):
        con = duckdb.connect(str(tmp_path / "d.duckdb"))
        con.execute("DROP TABLE IF EXISTS derivs_days")
        con.execute("CREATE TABLE derivs_days (day DATE PRIMARY KEY, has_file BOOLEAN)")
        for d in weekday_run(last, 5):
            con.execute("INSERT INTO derivs_days VALUES (?, TRUE)", [d])
        con.close()

    derivs(date(2026, 10, 27))
    assert gates.derivs_in_step(tmp_path / "d.duckdb", tmp_path / "w.duckdb").level == gates.OK
    derivs(date(2026, 10, 26))
    assert gates.derivs_in_step(tmp_path / "d.duckdb", tmp_path / "w.duckdb").level == gates.WARN
    derivs(date(2026, 10, 22))
    assert gates.derivs_in_step(tmp_path / "d.duckdb", tmp_path / "w.duckdb").level == gates.BAD


def make_fundamentals(path, quarter_rows, odd_dates=()):
    with FundamentalsStore(path) as store:
        con = store._con
        for symbol, period in quarter_rows:
            con.execute("INSERT INTO shareholding VALUES (?, ?, 'promoters', 1, 1, 'u', now())", [symbol, period])
        for symbol, period in odd_dates:
            con.execute("INSERT INTO shareholding VALUES (?, ?, 'promoters', 1, 1, 'u', now())", [symbol, period])


def test_shareholding_coverage_is_judged_only_after_the_deadline_and_ignores_dates_that_are_no_quarter_end(tmp_path):
    prev, new = date(2026, 6, 30), date(2026, 9, 30)
    rows = [(f"S{i}", prev) for i in range(100)] + [(f"S{i}", new) for i in range(60)]
    make_fundamentals(tmp_path / "f.duckdb", rows, odd_dates=[("ZZ", date(2026, 10, 23))])
    soon = datetime(2026, 10, 27, 12, 0, tzinfo=IST)
    gate = gates.shareholding_cover(soon, tmp_path / "f.duckdb")
    assert gate.level == gates.WARN and "not judged yet" in gate.detail and "no quarter end" in gate.detail and "60 of the 100" in gate.detail
    later = datetime(2026, 12, 20, 12, 0, tzinfo=IST)
    assert gates.shareholding_cover(later, tmp_path / "f.duckdb").level == gates.BAD
    full = [(f"S{i}", prev) for i in range(100)] + [(f"S{i}", new) for i in range(98)]
    make_fundamentals(tmp_path / "g.duckdb", full)
    assert gates.shareholding_cover(later, tmp_path / "g.duckdb").level == gates.OK


def test_fundamentals_health_counts_failures_and_flags_duplicate_keys(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        store.record_filing("u1", "TCS", "consolidated", date(2024, 3, 31), True, datetime(2024, 5, 1), "failed", "x")
        store.set_run("TCS", "partial")
        store.set_run("INFY", "ok")
    gate = gates.fundamentals_health(tmp_path / "f.duckdb")
    assert gate.level == gates.WARN and "1 filing(s) failed" in gate.detail and "1 partial" in gate.detail and "0 duplicate" in gate.detail
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        for _ in range(2):
            store._con.execute("INSERT INTO statement_lines VALUES ('TCS', 'consolidated', 'income', ?, ?, 'sales', 1, 'u', now(), 'nse-xbrl')", [date(2023, 4, 1), date(2024, 3, 31)])
    assert gates.fundamentals_health(tmp_path / "f.duckdb").level == gates.BAD


def test_a_store_a_long_job_is_writing_is_reported_busy_not_failed(tmp_path):
    path = tmp_path / "busy.duckdb"
    holder = duckdb.connect(str(path))  # an open writer in this process; a read-only open from another connection is refused
    holder.execute("CREATE TABLE days (day DATE)")
    gate = gates.prices_breadth(path)
    assert gate.level == gates.WARN and "busy" in gate.detail or "could not check" in gate.detail
    holder.close()
    assert gates.worst([gates.Gate("a", gates.OK, ""), gates.Gate("b", gates.WARN, "")]) == gates.WARN
    assert gates.worst([gates.Gate("a", gates.BAD, ""), gates.Gate("b", gates.WARN, "")]) == gates.BAD
