from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.pit.bhavcopy import BhavcopyStore, Row
from athena.warehouse.build import format_status
from athena.warehouse.ingest import MAX_CONSECUTIVE_FAILURES, ingest_prices, ingest_universe, ready_through, universe_rows
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 12, 20, 30, tzinfo=IST)  # a Monday evening, after the file is out


def row(symbol, close=100.0, series="EQ"):
    return Row(symbol, series, close - 1, close + 1, close - 2, close, 1000.0)


class FakeStore:
    """Days by date; a date not in `files` has no file; a date in `fail` raises like a refused request."""

    def __init__(self, files=None, fail=()):
        self.files, self.fail, self.asked = files or {}, set(fail), []

    def day(self, day, remember_missing=True):
        self.asked.append((day, remember_missing))
        if day in self.fail:
            raise AthenaError("price file refused: HTTP 403")
        return self.files.get(day)


def test_a_day_loads_replaces_and_reads_back_as_bars_stamped_like_the_other_adapters():
    w = Warehouse(":memory:")
    w.upsert_day(date(2026, 10, 9), {"TCS": row("TCS", 3000), "SBIN": row("SBIN", 800)}, NOW)
    w.upsert_day(date(2026, 10, 9), {"TCS": row("TCS", 3100)}, NOW)  # a reload replaces the day, not adds to it
    bars = w.bars("TCS")
    assert [b.close for b in bars] == [3100] and w.bars("SBIN") == []
    assert bars[0].timestamp == datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc) and bars[0].source == "bhavcopy"
    assert w.last_loaded_day() == date(2026, 10, 9)


def test_bars_can_be_cut_by_date():
    w = Warehouse(":memory:")
    for d in (7, 8, 9):
        w.upsert_day(date(2026, 10, d), {"TCS": row("TCS", d)}, NOW)
    assert [b.close for b in w.bars("TCS", since=date(2026, 10, 8), until=date(2026, 10, 8))] == [8]


def test_a_failed_write_leaves_the_day_as_it_was():
    w = Warehouse(":memory:")
    w.upsert_day(date(2026, 10, 9), {"TCS": row("TCS", 3000)}, NOW)
    with pytest.raises(Exception):
        w.upsert_day(date(2026, 10, 9), {"BAD": Row("BAD", "EQ", 1, 1, 1, "x", 1)}, NOW)  # a close that is not a number
    assert [b.close for b in w.bars("TCS")] == [3000]


def test_the_current_day_is_tried_only_after_seven_in_the_evening():
    assert ready_through(datetime(2026, 10, 12, 18, 59, tzinfo=IST)) == date(2026, 10, 11)
    assert ready_through(datetime(2026, 10, 12, 19, 0, tzinfo=IST)) == date(2026, 10, 12)
    assert ready_through(datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)) == date(2026, 10, 12)  # 01:30 IST on the 13th: the 12th's file is the latest out


def test_ingest_loads_files_marks_holidays_and_skips_what_is_settled():
    w = Warehouse(":memory:")
    store = FakeStore({date(2026, 9, 1): {"TCS": row("TCS")}, date(2026, 9, 3): {"TCS": row("TCS")}})  # 2 Sept has no file
    summary = ingest_prices(w, store, date(2026, 9, 1), NOW, until=date(2026, 9, 3))
    assert (summary["loaded"], summary["no_session"], summary["pending"]) == (2, 1, 0)
    assert w.day_status(date(2026, 9, 1), date(2026, 9, 3))[date(2026, 9, 2)] == "no_session"
    again = FakeStore()
    assert ingest_prices(w, again, date(2026, 9, 1), NOW, until=date(2026, 9, 3))["skipped"] == 3 and again.asked == []


def test_weekends_are_never_asked_for():
    store = FakeStore()
    ingest_prices(Warehouse(":memory:"), store, date(2026, 9, 5), NOW, until=date(2026, 9, 6))  # Saturday and Sunday
    assert store.asked == []


def test_a_recent_missing_file_is_pending_and_not_remembered_so_a_late_file_is_picked_up():
    w = Warehouse(":memory:")
    store = FakeStore()
    ingest_prices(w, store, date(2026, 10, 12), NOW)  # today's file is not out yet
    assert w.day_status(date(2026, 10, 12), date(2026, 10, 12)) == {date(2026, 10, 12): "pending"}
    assert store.asked == [(date(2026, 10, 12), False)]
    store.files[date(2026, 10, 12)] = {"TCS": row("TCS")}
    summary = ingest_prices(w, store, date(2026, 10, 12), NOW)
    assert summary["loaded"] == 1 and w.last_loaded_day() == date(2026, 10, 12)


def test_an_old_missing_file_is_a_holiday_and_is_remembered():
    store = FakeStore()
    ingest_prices(Warehouse(":memory:"), store, date(2026, 9, 2), NOW, until=date(2026, 9, 2))
    assert store.asked == [(date(2026, 9, 2), True)]


def test_a_refused_day_is_a_gap_and_three_in_a_row_stop_the_run():
    w = Warehouse(":memory:")
    days = [date(2026, 9, d) for d in (1, 2, 3, 4, 7)]
    store = FakeStore({d: {"TCS": row("TCS")} for d in days}, fail=days[:MAX_CONSECUTIVE_FAILURES])
    summary = ingest_prices(w, store, days[0], NOW, until=days[-1])
    assert summary["stopped"] is True and len(summary["failed"]) == 3 and summary["loaded"] == 0
    assert w.gaps(days[0], days[-1]) == days  # nothing settled; the next run resumes from the start


def test_one_refusal_does_not_stop_the_run():
    w = Warehouse(":memory:")
    store = FakeStore({date(2026, 9, 1): {"A": row("A")}, date(2026, 9, 3): {"A": row("A")}}, fail=[date(2026, 9, 2)])
    summary = ingest_prices(w, store, date(2026, 9, 1), NOW, until=date(2026, 9, 3))
    assert summary["loaded"] == 2 and summary["failed"] == ["2026-09-02"] and not summary["stopped"]
    assert w.gaps(date(2026, 9, 1), date(2026, 9, 3)) == [date(2026, 9, 2)]


def test_universe_kinds_come_from_the_master_lists_and_unknown_symbols_are_other():
    rows = universe_rows({"TCS": {"name": "Tata Consultancy", "isin": "INE467B01029"}}, {"ITBEES": {"name": "IT ETF", "isin": "INF1"}}, {"TCS": "EQ", "ITBEES": "EQ", "SMEQ": "SM", "MYSTERY": "EQ"})
    assert rows == [
        ("ITBEES", "etf", "IT ETF", "INF1"), ("MYSTERY", "other", "", ""), ("SMEQ", "sme", "", ""), ("TCS", "stock", "Tata Consultancy", "INE467B01029"),
    ]


def test_universe_includes_what_traded_on_the_last_loaded_day():
    w = Warehouse(":memory:")
    w.upsert_day(date(2026, 10, 9), {"NEWCO": row("NEWCO"), "TINY": row("TINY", series="SM")}, NOW)
    assert ingest_universe(w, {"TCS": {"name": "T", "isin": "I"}}, {}, NOW) == 3
    assert {u["symbol"]: u["kind"] for u in w.universe()} == {"TCS": "stock", "NEWCO": "other", "TINY": "sme"}
    assert [u["symbol"] for u in w.universe("stock")] == ["TCS"]


def test_status_reports_how_far_the_data_goes_and_what_is_missing():
    w = Warehouse(":memory:")
    w.upsert_day(date(2026, 10, 8), {"TCS": row("TCS"), "SBIN": row("SBIN")}, NOW)
    w.mark_day(date(2026, 10, 9), "no_session", NOW)
    status = w.status(date(2026, 10, 7), date(2026, 10, 12))
    assert status["last_loaded_day"] == date(2026, 10, 8) and status["symbols_on_last_day"] == 2 and status["bar_rows"] == 2
    assert status["gaps"] == [date(2026, 10, 7), date(2026, 10, 12)]
    text = format_status(status, date(2026, 10, 7))
    assert "1 loaded, 1 with no session" in text and "2 weekdays not settled" in text


def test_the_file_survives_a_restart(tmp_path):
    path = tmp_path / "w.duckdb"
    w = Warehouse(path)
    w.upsert_day(date(2026, 10, 9), {"TCS": row("TCS", 3000)}, NOW)
    w.close()
    assert [b.close for b in Warehouse(path).bars("TCS")] == [3000]


def test_the_real_store_leaves_no_marker_for_a_late_file(tmp_path):
    store = BhavcopyStore(directory=tmp_path, fetch=lambda url: None)
    assert store.day(date(2026, 10, 12), remember_missing=False) is None
    assert not list(tmp_path.iterdir())
    assert store.day(date(2026, 10, 12)) is None and len(list(tmp_path.iterdir())) == 1  # remembered only when asked to
