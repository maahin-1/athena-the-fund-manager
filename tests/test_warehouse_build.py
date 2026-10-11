import json
from datetime import date, datetime, timedelta, timezone

from athena.pit.bhavcopy import Row
from athena.warehouse.build import run_build, write_build_status
from athena.warehouse.monitor import BAD, NONE, OK, WARN, assess_build, read_build
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 12, 20, 30, tzinfo=IST)
FIRST = date(2026, 10, 8)
ROW = Row("TCS", "EQ", 100.0, 101.0, 99.0, 100.0, 1000.0)
GOOD = {"annual": {"income": {"Net Income": {"2025-03-31": 1.0}}}}


class Store:
    def __init__(self, files=None, fail=()):
        self.files, self.fail = files or {}, set(fail)

    def day(self, day, remember_missing=True):
        if day in self.fail:
            from athena.contracts import AthenaError

            raise AthenaError("HTTP 403")
        return self.files.get(day)


def lists():
    return {"TCS": {"name": "Tata Consultancy", "isin": "INE467B01029"}}, {}


INDEX_FILE = (
    b"Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
    b"Nifty 50,09-10-2026,1,2,1,2,1,1,1,1,19.27,2.77,1.52\n"
)


def archive(url):
    return INDEX_FILE if "ind_close_all" in url else b"[]"


def build(warehouse, store, get=archive, budget=0, fetch=lambda s: GOOD, master=lists):
    return run_build(warehouse, NOW, FIRST, get, store, master, fetch, budget)


def names(outcome, ok):
    return [s["name"] for s in outcome["steps"] if s["ok"] is ok]


def test_a_clean_build_runs_every_step_and_is_ok():
    w = Warehouse(":memory:")
    outcome = build(w, Store({date(2026, 10, 8): {"TCS": ROW}, date(2026, 10, 9): {"TCS": ROW}}), budget=5)
    assert outcome["ok"] is True and [s["name"] for s in outcome["steps"]] == ["prices", "actions", "indices", "universe", "statements", "packets"]
    assert outcome["last_loaded_day"] == "2026-10-09"
    assert [u["symbol"] for u in w.universe("stock")] == ["TCS"]


def test_statements_are_skipped_when_the_budget_is_zero():
    outcome = build(Warehouse(":memory:"), Store({date(2026, 10, 9): {"TCS": ROW}}), budget=0)
    assert "statements" not in [s["name"] for s in outcome["steps"]]


def test_one_failing_step_is_recorded_and_the_rest_still_run():
    def broken_get(url):
        raise RuntimeError("archive host down")

    def broken_lists():
        raise RuntimeError("lists unavailable")

    outcome = build(Warehouse(":memory:"), Store({date(2026, 10, 9): {"TCS": ROW}}), get=broken_get, master=broken_lists)
    assert outcome["ok"] is False and set(names(outcome, False)) == {"actions", "indices", "universe"}  # both fetch from the archive host
    assert set(names(outcome, True)) == {"prices", "packets"}
    assert "archive host down" in next(s["detail"] for s in outcome["steps"] if s["name"] == "actions")


def test_a_price_run_that_had_failed_days_is_not_ok_even_if_the_rest_ran():
    outcome = build(Warehouse(":memory:"), Store({date(2026, 10, 8): {"TCS": ROW}}, fail=[date(2026, 10, 9)]))
    assert names(outcome, False) == ["prices"] and outcome["ok"] is False


def test_the_status_and_a_report_are_written_for_the_monitor_and_for_people(tmp_path):
    outcome = build(Warehouse(":memory:"), Store({date(2026, 10, 9): {"TCS": ROW}}))
    report = write_build_status(outcome, NOW, NOW + timedelta(minutes=3), tmp_path / "status.json", tmp_path / "reports")
    saved = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True and saved["finished_at"] == (NOW + timedelta(minutes=3)).isoformat()
    text = report.read_text(encoding="utf-8")
    assert "**ok**" in text and "took 3.0 minutes" in text and "prices:" in text
    assert (tmp_path / "reports" / "build_latest.md").read_text(encoding="utf-8") == text
    assert read_build(tmp_path / "status.json")["last_loaded_day"] == "2026-10-09"


def status(**kw):
    base = {"ok": True, "finished_at": NOW.isoformat(), "last_loaded_day": "2026-10-09", "steps": [{"name": "prices", "ok": True, "detail": {}}]}
    return {**base, **kw}


def test_a_recent_clean_build_is_ok_and_a_missing_one_is_none():
    level, line = assess_build(status(), NOW + timedelta(hours=2))
    assert level == OK and "2.0 h ago" in line and "2026-10-09" in line
    assert assess_build(None, NOW)[0] == NONE


def test_a_build_with_a_failed_step_is_a_warning_naming_the_step():
    level, line = assess_build(status(steps=[{"name": "indices", "ok": False, "detail": "x"}, {"name": "prices", "ok": True, "detail": {}}]), NOW + timedelta(hours=1))
    assert level == WARN and "indices" in line and "build_latest.md" in line


def test_a_build_older_than_a_day_and_a_half_is_stale():
    level, line = assess_build(status(), NOW + timedelta(hours=40))
    assert level == BAD and "stale" in line and "40 hours" in line
