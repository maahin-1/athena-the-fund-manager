"""The verdict runner's input gate and timing record (WP02, A6 and Q1): old packets cannot become a new verdict silently, and every verdict records when it was decided."""
from datetime import date, datetime, timedelta, timezone

import pytest

from athena import timing
from athena.verdicts.calibration import Calibration
from athena.verdicts.runner import NoVerdictPossible, StaleInputs, VerdictRunner
from athena.warehouse.warehouse import Warehouse

from test_verdicts_runner import DAY, NOW, analysts, database  # noqa: E402  (DAY is Friday 9 October 2026; NOW is Monday 12 October, 12:00 UTC)


def runner_at(path, when, specialists=None, tick=timedelta(0)):
    """A runner whose clock reads `when` and moves on by `tick` at every reading (a model that takes time)."""
    state = {"now": when}

    def clock():
        value = state["now"]
        state["now"] = value + tick
        return value

    return VerdictRunner(path, specialists or analysts(), Calibration(), clock, model="fake")


def test_a_fresh_packet_makes_a_verdict_and_records_how_fresh_it_was(tmp_path):
    stored = runner_at(database(tmp_path), NOW).make("TCS")
    block = stored["payload"]["timing"]
    assert block["version"] == "timing/1" and block["entry_policy"] == timing.ENTRY_POLICY
    assert block["data_as_of"] == "2026-10-09" and block["mode"] == "current"
    assert block["freshness"]["dataset"] == "price.eod" and block["freshness"]["age_trading_days"] == 1 and block["freshness"]["status"] == "fresh"


def test_the_decision_completion_time_is_taken_after_the_analysts_and_the_council_have_finished(tmp_path):
    stored = runner_at(database(tmp_path), NOW, tick=timedelta(minutes=3)).make("TCS")
    completed = datetime.fromisoformat(stored["payload"]["timing"]["decision_completed_at"])
    assert completed > stored["made_at"] and completed.tzinfo is not None  # made_at is the first reading; completion a later one
    assert stored["payload"]["timing"]["decision_date_ist"] == "2026-10-12"


def test_a_packet_older_than_the_market_calendar_allows_cannot_become_a_new_verdict(tmp_path):
    path = database(tmp_path)
    before = runner_at(path, NOW).make("TCS")
    people = analysts()
    with pytest.raises(StaleInputs, match="price.eod") as refused:
        runner_at(path, NOW + timedelta(days=10), people).make("TCS")  # the warehouse stopped updating: the 9th is nine trading days old by the 22nd
    assert isinstance(refused.value, NoVerdictPossible)
    assert all(a.packets == [] for a in people.values())  # no model was asked
    assert refused.value.latest["made_at"] == before["made_at"]  # the last verdict is still there to show
    with Warehouse(path) as w:
        assert len(w.verdict_history("TCS")) == 1  # nothing new was stored


def test_the_last_verdict_is_reported_with_an_explicit_stale_status_when_a_refresh_cannot_run(tmp_path):
    path = database(tmp_path)
    runner_at(path, NOW).make("TCS")
    state = runner_at(path, NOW + timedelta(days=10)).status("TCS")
    assert state["verdict"] is not None and state["inputs"]["fresh"] is False and "price.eod" in state["inputs"]["reason"]
    assert runner_at(path, NOW).status("TCS")["inputs"] == {"fresh": True, "reason": None, "age_trading_days": 1}


def test_a_market_holiday_does_not_make_a_packet_stale_but_the_same_gap_without_the_holiday_does(tmp_path):
    path = database(tmp_path)
    tuesday = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(StaleInputs):
        runner_at(path, tuesday).make("TCS")  # Friday's data on Tuesday: two sessions, Monday and Tuesday, have passed
    with Warehouse(path) as w:
        w.mark_day(date(2026, 10, 12), "no_session", NOW)  # Monday was an exchange holiday
    stored = runner_at(path, tuesday).make("TCS")
    assert stored["payload"]["timing"]["freshness"]["age_trading_days"] == 1


def test_a_deliberate_historical_run_is_allowed_and_says_so_in_the_record(tmp_path):
    path = database(tmp_path)
    stored = runner_at(path, NOW + timedelta(days=10)).make("TCS", historical=True)
    block = stored["payload"]["timing"]
    assert block["mode"] == "historical_explicit" and block["freshness"]["status"] == "stale_accepted_historical"
    assert block["freshness"]["age_trading_days"] > 1


def test_the_changed_stock_refresh_reports_stale_inputs_apart_from_failures_and_does_not_count_them_towards_a_stop(tmp_path):
    path = database(tmp_path, symbols=("AAA", "BBB", "CCC", "DDD"))
    for symbol in ("AAA", "BBB", "CCC", "DDD"):
        runner_at(path, NOW).make(symbol)
    with Warehouse(path) as w:  # every stock has news: new quarterly results
        rows = [(s, "fundamentals", DAY, [], {"latest_annual_period": "2026-03-31", "latest_quarter": "2026-09-30", "metrics": {}}) for s in ("AAA", "BBB", "CCC", "DDD")]
        w.put_packets(rows, NOW)
    summary = runner_at(path, NOW + timedelta(days=10)).refresh_changed(10)
    assert sorted(summary["stale"]) == ["AAA", "BBB", "CCC", "DDD"] and summary["failed"] == [] and summary["stopped"] == "" and summary["made"] == []
