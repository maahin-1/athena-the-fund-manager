from datetime import date, datetime, timedelta, timezone

from athena.pit.bhavcopy import Row
from athena.warehouse.statements import MAX_CONSECUTIVE_ERRORS, STALE_OK_DAYS, queue, refresh_statements
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
DAY = date(2026, 10, 9)
GOOD = {"annual": {"income": {"Net Income": {"2025-03-31": 1.0}}}}
EMPTY = {"annual": {"income": {}}}


def row(symbol, close=100.0, volume=1000.0):
    return Row(symbol, "EQ", close, close, close, close, volume)


def seeded(*symbols):
    w = Warehouse(":memory:")
    w.upsert_day(DAY, {s: row(s) for s in symbols}, NOW)
    w.set_universe([(s, "stock", s, "") for s in symbols] + [("ETF1", "etf", "e", ""), ("SME1", "sme", "s", "")], NOW)
    return w


def test_never_fetched_stocks_come_first_most_traded_first_and_etfs_are_left_out():
    w = Warehouse(":memory:")
    w.upsert_day(DAY, {"SMALL": row("SMALL", 10.0, 100.0), "BIG": row("BIG", 100.0, 1e6), "ETF1": row("ETF1")}, NOW)
    w.set_universe([("SMALL", "stock", "", ""), ("BIG", "stock", "", ""), ("ETF1", "etf", "", "")], NOW)
    assert queue(w, NOW, DAY) == ["BIG", "SMALL"]


def test_ok_stale_failed_and_empty_come_back_on_their_own_schedules():
    w = seeded("OK_FRESH", "OK_STALE", "FAILED_OLD", "FAILED_TODAY", "EMPTY_OLD", "EMPTY_NEW")
    day = timedelta(days=1)
    w.put_statement("OK_FRESH", "ok", GOOD, "", NOW - 5 * day)
    w.put_statement("OK_STALE", "ok", GOOD, "", NOW - (STALE_OK_DAYS + 1) * day)
    w.put_statement("FAILED_OLD", "error", None, "boom", NOW - 2 * day)
    w.put_statement("FAILED_TODAY", "error", None, "boom", NOW)
    w.put_statement("EMPTY_OLD", "empty", None, "", NOW - 91 * day)
    w.put_statement("EMPTY_NEW", "empty", None, "", NOW - 10 * day)
    assert queue(w, NOW, DAY) == ["FAILED_OLD", "OK_STALE", "EMPTY_OLD"]


def test_a_refresh_stores_each_outcome_and_works_only_up_to_the_budget():
    w = seeded("A", "B", "C", "D")

    def fetch(symbol):
        if symbol == "B":
            raise RuntimeError("HTTP 429")
        return EMPTY if symbol == "C" else GOOD

    summary = refresh_statements(w, fetch, NOW, budget=3, sleep=lambda s: None)
    assert (summary["ok"], summary["error"], summary["empty"], summary["due"]) == (1, 1, 1, 4)
    state = w.statement_state()
    assert {s: v[0] for s, v in state.items()} == {"A": "ok", "B": "error", "C": "empty"} and "D" not in state
    assert w.statement("A")[0] == GOOD and w.statement("B") is None and w.statement("C") is None


def test_a_failed_refresh_keeps_the_old_payload_and_its_age():
    w = seeded("A")
    old = NOW - timedelta(days=40)
    w.put_statement("A", "ok", GOOD, "", old)

    def down(symbol):
        raise RuntimeError("down")

    refresh_statements(w, down, NOW, budget=1, sleep=lambda s: None)
    payload, fetched = w.statement("A")
    status, _, _, attempts = w.statement_state()["A"]
    assert payload == GOOD and fetched == old.astimezone(timezone.utc) and (status, attempts) == ("error", 1)


def test_five_failures_in_a_row_stop_the_run():
    w = seeded(*[f"S{i}" for i in range(8)])
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        raise RuntimeError("blocked")

    summary = refresh_statements(w, fetch, NOW, budget=8, sleep=lambda s: None)
    assert summary["stopped"] is True and len(calls) == MAX_CONSECUTIVE_ERRORS


def test_a_success_after_failures_resets_the_attempt_count():
    w = seeded("A")
    w.put_statement("A", "error", None, "boom", NOW - timedelta(days=3))
    w.put_statement("A", "error", None, "boom", NOW - timedelta(days=2))
    assert w.statement_state()["A"][3] == 2
    w.put_statement("A", "ok", GOOD, "", NOW)
    assert w.statement_state()["A"][3] == 0 and w.statement("A") is not None


def test_status_counts_the_statements_by_outcome():
    w = seeded("A", "B")
    w.put_statement("A", "ok", GOOD, "", NOW)
    w.put_statement("B", "empty", None, "", NOW)
    assert w.status()["statements"] == {"ok": 1, "empty": 1}
