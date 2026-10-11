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


# ---- corporate actions
import json  # noqa: E402

from athena.warehouse.actions import fetch_actions, ingest_actions, rows_to_actions  # noqa: E402
from athena.warehouse.history import adjusted_history, market_price_flags  # noqa: E402


def nse_row(symbol, ex, subject, face="10"):
    return {"symbol": symbol, "exDate": ex, "subject": subject, "faceVal": face, "series": "EQ", "isin": "INE000"}


def reply(rows):
    return lambda url: json.dumps(rows).encode()


def test_rows_become_actions_and_unusable_rows_are_counted_not_guessed():
    rows, skipped = rows_to_actions([nse_row("TCS", "14-Jan-2026", "Bonus 1:1"), {"symbol": "X", "subject": "Bonus 1:1"}, nse_row("Y", "bad", "Bonus 1:1"), nse_row("", "14-Jan-2026", "Bonus 1:1")])
    assert [(s, d.isoformat(), subj) for s, d, subj, *_ in rows] == [("TCS", "2026-01-14", "Bonus 1:1")] and skipped == 3


def test_the_request_asks_for_the_whole_market_over_a_date_window():
    asked = []
    fetch_actions(lambda url: asked.append(url) or b"[]", date(2024, 10, 1), date(2026, 12, 31))
    assert "index=equities" in asked[0] and "from_date=01-10-2024" in asked[0] and "to_date=31-12-2026" in asked[0] and "symbol" not in asked[0]


def test_a_reply_that_is_not_a_list_is_an_error():
    for bad in (None, b"<html>denied</html>", b'{"a": 1}'):
        with pytest.raises(AthenaError):
            fetch_actions(lambda url, bad=bad: bad, date(2026, 1, 1), date(2026, 2, 1))


def test_actions_are_read_back_parsed_with_their_face_value():
    w = Warehouse(":memory:")
    ingest_actions(w, reply([nse_row("KOTAKBANK", "14-Jan-2026", "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share", "1"), nse_row("TCS", "05-Feb-2026", "Dividend - Rs 10 Per Share")]), date(2026, 2, 1), NOW)
    kotak = w.actions("KOTAKBANK")
    assert [(a.day, a.kind, a.ratio, a.face_value) for a in kotak] == [(date(2026, 1, 14), "split", 5.0, 1.0)]
    assert w.actions("TCS") == []  # an ordinary dividend does not change the price series we adjust
    assert w.actions_status()["rows"] == 2 and w.actions_status()["symbols"] == 2


def test_a_later_run_replaces_the_window_so_a_withdrawn_action_does_not_linger():
    w = Warehouse(":memory:")
    ingest_actions(w, reply([nse_row("A", "14-Jan-2026", "Bonus 1:1"), nse_row("B", "20-Jan-2026", "Bonus 1:1")]), date(2026, 2, 1), NOW, since=date(2026, 1, 1))
    ingest_actions(w, reply([nse_row("A", "14-Jan-2026", "Bonus 1:1")]), date(2026, 2, 1), NOW)  # B was withdrawn: the recheck window covers both
    assert [a.kind for a in w.actions("A")] == ["bonus"] and w.actions("B") == []


def test_the_first_run_reaches_back_two_years_and_later_runs_only_the_recent_past():
    w = Warehouse(":memory:")
    asked = []
    ingest_actions(w, lambda url: asked.append(url) or json.dumps([nse_row("A", "14-Jan-2026", "Bonus 1:1")]).encode(), date(2026, 10, 12), NOW)
    ingest_actions(w, lambda url: asked.append(url) or b"[]", date(2026, 10, 12), NOW)
    assert "from_date=12-10-2024" in asked[0] and "from_date=28-08-2026" in asked[1]


def test_a_full_run_reads_back_from_since_even_when_actions_are_held():
    w = Warehouse(":memory:")
    asked = []
    ingest_actions(w, lambda url: asked.append(url) or json.dumps([nse_row("A", "14-Jan-2026", "Bonus 1:1")]).encode(), date(2026, 10, 12), NOW)
    ingest_actions(w, lambda url: asked.append(url) or b"[]", date(2026, 10, 12), NOW, since=date(2023, 1, 2), full=True)
    assert "from_date=02-01-2023" in asked[1]


def test_adjusted_history_applies_a_recorded_split_and_a_split_with_a_bonus_the_same_day(tmp_path):
    w = Warehouse(":memory:")
    closes = {date(2026, 1, 12): 100.0, date(2026, 1, 13): 100.0, date(2026, 1, 14): 10.0, date(2026, 1, 15): 10.0}  # a tenth after a 10-to-2 split plus a 1:1 bonus
    for day, close in closes.items():
        w.upsert_day(day, {"ACME": row("ACME", close)}, NOW)
    ingest_actions(w, reply([nse_row("ACME", "14-Jan-2026", "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"), nse_row("ACME", "14-Jan-2026", "Bonus 1:1")]), date(2026, 1, 20), NOW, since=date(2026, 1, 1))
    history = adjusted_history(w)
    assert [round(b.close, 4) for b in history("ACME")] == [10.0, 10.0, 10.0, 10.0]
    assert market_price_flags(w, ["ACME"]) == {}


def test_a_break_with_no_record_is_flagged_and_left_alone():
    w = Warehouse(":memory:")
    for day, close in {date(2026, 1, 12): 100.0, date(2026, 1, 13): 20.0}.items():
        w.upsert_day(day, {"ACME": row("ACME", close)}, NOW)
    assert list(market_price_flags(w, ["ACME"])) == ["ACME"]
    assert [b.close for b in adjusted_history(w)("ACME")] == [100.0, 20.0]
