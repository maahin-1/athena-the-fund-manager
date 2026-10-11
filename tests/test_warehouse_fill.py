import json
from datetime import date, datetime, timedelta, timezone

from athena.pit.bhavcopy import Row
from athena.warehouse.fill import STREAK_TO_PAUSE, read_status, run_fill
from athena.warehouse.monitor import BAD, NONE, OK, PAUSED, assess, describe, watch
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc)
DAY = date(2026, 10, 9)
GOOD = {"annual": {"income": {"Net Income": {"2025-03-31": 1.0}}}}
EMPTY = {"annual": {"income": {}}}


def row(symbol, volume=1000.0):
    return Row(symbol, "EQ", 100.0, 100.0, 100.0, 100.0, volume)


def make_db(tmp_path, symbols):
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.upsert_day(DAY, {s: row(s, 1000.0 - i) for i, s in enumerate(symbols)}, NOW)
        w.set_universe([(s, "stock", s, "") for s in symbols], NOW)
    return path


def fill(tmp_path, db, fetch, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("build_after", False)
    return run_fill(db, fetch, tmp_path / "status.json", tmp_path / "reports", clock=lambda: NOW, **kw)


def test_every_due_stock_is_fetched_and_the_status_and_report_say_what_happened(tmp_path):
    db = make_db(tmp_path, ["A", "B", "C", "D"])

    def fetch(symbol):
        if symbol == "C":
            return EMPTY
        if symbol == "D":
            raise RuntimeError("HTTP 500 boom")
        return GOOD

    status = fill(tmp_path, db, fetch, max_pauses=0, build_after=True)
    saved = read_status(tmp_path / "status.json")
    assert (saved["ok"], saved["empty"], saved["error"], saved["done"], saved["total"]) == (2, 1, 1, 4, 4)
    assert saved["state"] == "finished" and saved["errors"] == {"RuntimeError: HTTP 500 boom": 1}
    report = (tmp_path / "reports" / "statements_fill_latest.md").read_text(encoding="utf-8")
    assert "**finished**" in report and "2 with statements" in report and "1 failed" in report and "RuntimeError: HTTP 500 boom" in report and "D" in report
    assert status.report.endswith(".md")
    with Warehouse(db, read_only=True) as w:
        assert {s: v[0] for s, v in w.statement_state().items()} == {"A": "ok", "B": "ok", "C": "empty", "D": "error"}


def test_a_second_run_does_only_what_is_left(tmp_path):
    db = make_db(tmp_path, ["A", "B", "C", "D"])
    seen = []
    fill(tmp_path, db, lambda s: seen.append(s) or GOOD, limit=2)
    assert seen == ["A", "B"]  # the most traded first
    fill(tmp_path, db, lambda s: seen.append(s) or GOOD)
    assert seen == ["A", "B", "C", "D"] and read_status(tmp_path / "status.json")["total"] == 2


def test_the_database_is_free_for_others_between_the_fetches(tmp_path):
    db = make_db(tmp_path, ["A", "B"])
    probes = []

    def fetch(symbol):
        with Warehouse(db) as other:  # a read-write open while the job is mid-run: the job is not holding the file
            probes.append(other.last_loaded_day())
        return GOOD

    fill(tmp_path, db, fetch)
    assert probes == [DAY, DAY]


def test_refusals_pause_the_job_and_the_stocks_that_failed_are_tried_again(tmp_path):
    db = make_db(tmp_path, [f"S{i}" for i in range(8)])
    calls, sleeps = [], []

    def fetch(symbol):
        calls.append(symbol)
        if len(calls) <= STREAK_TO_PAUSE:
            raise RuntimeError("Too Many Requests")
        return GOOD

    status = fill(tmp_path, db, fetch, sleep=sleeps.append, pause_seconds=600.0)
    assert 600.0 in sleeps and status.pauses == 1 and status.state == "finished"
    assert (status.ok, status.error, status.done) == (8, 0, 8)  # the five that failed were retried and now count once, as ok
    assert calls[:5] == ["S0", "S1", "S2", "S3", "S4"] and calls[5:10] == ["S0", "S1", "S2", "S3", "S4"]


def test_a_block_that_never_lifts_stops_the_job_with_a_reason(tmp_path):
    db = make_db(tmp_path, [f"S{i}" for i in range(10)])

    def fetch(symbol):
        raise RuntimeError("Too Many Requests")

    status = fill(tmp_path, db, fetch, max_pauses=2)
    assert status.state == "stopped" and status.pauses == 3 and "Too Many Requests" in status.reason
    assert read_status(tmp_path / "status.json")["state"] == "stopped"
    assert "**stopped**" in (tmp_path / "reports" / "statements_fill_latest.md").read_text(encoding="utf-8")


def test_the_status_file_is_written_while_the_job_runs(tmp_path):
    db = make_db(tmp_path, ["A", "B", "C"])
    seen = []

    def fetch(symbol):
        seen.append(read_status(tmp_path / "status.json"))
        return GOOD

    fill(tmp_path, db, fetch)
    assert [(s["state"], s["done"], s["total"]) for s in seen] == [("running", 0, 3), ("running", 1, 3), ("running", 2, 3)]


# ---- monitor
def status(**kw):
    base = {"state": "running", "total": 100, "done": 40, "ok": 39, "empty": 1, "error": 0, "current": "TCS", "pauses": 0, "pause_seconds": 600.0,
            "updated_at": NOW.isoformat(), "started_at": (NOW - timedelta(minutes=20)).isoformat(), "errors": {}, "reason": "", "report": ""}
    return {**base, **kw}


def test_a_running_job_with_a_fresh_heartbeat_is_fine():
    level, line = assess(status(), NOW + timedelta(seconds=20))
    assert level == OK and "40/100" in line and "TCS" in line


def test_a_silent_job_is_stalled():
    level, line = assess(status(), NOW + timedelta(minutes=10))
    assert level == BAD and "stalled" in line and "10 min" in line


def test_a_pause_is_expected_to_be_quiet_for_its_length_but_not_longer():
    assert assess(status(state="paused"), NOW + timedelta(minutes=8))[0] == PAUSED
    assert assess(status(state="paused"), NOW + timedelta(minutes=20))[0] == BAD


def test_finished_stopped_and_missing_are_reported_as_such():
    assert assess(status(state="finished"), NOW + timedelta(days=1))[0] == OK
    level, line = assess(status(state="stopped", reason="blocked"), NOW)
    assert level == BAD and "blocked" in line
    assert assess(None, NOW)[0] == NONE


def test_the_description_gives_rate_time_left_and_the_main_failures():
    text = describe(status(error=3, errors={"RuntimeError: 429": 3}), NOW + timedelta(seconds=10))
    assert "stocks a minute" in text and "minutes to go" in text and "3 x RuntimeError: 429" in text


def test_watching_prints_at_each_five_percent_and_ends_when_the_job_does():
    sequence = [status(done=d, state="running") for d in (0, 3, 6, 6, 12)] + [status(done=100, state="finished", total=100, ok=100, empty=0)]
    feed, lines = iter(sequence), []
    level = watch(lambda: next(feed), lambda: NOW, lambda s: None, lines.append)
    assert level == OK and len(lines) == 4  # the start, 6%, 12% and the finish; 3% and the repeat of 6% are not news
    assert lines[-1].startswith("finished")


def test_watching_ends_on_a_stall():
    lines = []
    level = watch(lambda: status(updated_at=(NOW - timedelta(hours=1)).isoformat()), lambda: NOW, lambda s: None, lines.append)
    assert level == BAD and "stalled" in lines[0]
