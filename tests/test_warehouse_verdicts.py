from datetime import date, datetime, timedelta, timezone

from athena.warehouse.warehouse import Warehouse

T0 = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)


def put(w, symbol, minutes, close=100.0, verdict="Hold"):
    w.put_verdict(symbol, T0 + timedelta(minutes=minutes), date(2026, 10, 9), close, date(2026, 3, 31), date(2026, 6, 30), {"verdict": verdict})


def test_the_latest_verdict_is_the_newest_and_all_are_kept():
    w = Warehouse(":memory:")
    put(w, "TCS", 0, verdict="Hold")
    put(w, "TCS", 5, verdict="Buy")
    latest = w.latest_verdict("TCS")
    assert latest["payload"]["verdict"] == "Buy" and latest["made_at"] == T0 + timedelta(minutes=5)
    assert latest["data_as_of"] == date(2026, 10, 9) and latest["close"] == 100.0
    assert latest["annual_period"] == date(2026, 3, 31) and latest["quarter"] == date(2026, 6, 30)
    assert [v["payload"]["verdict"] for v in w.verdict_history("TCS")] == ["Buy", "Hold"]


def test_no_verdict_is_none_and_symbols_come_oldest_judged_first():
    w = Warehouse(":memory:")
    assert w.latest_verdict("TCS") is None and w.verdict_symbols() == []
    put(w, "TCS", 10)
    put(w, "INFY", 0)
    put(w, "INFY", 1)
    assert w.verdict_symbols() == ["INFY", "TCS"]


def test_a_missing_fact_is_stored_as_none():
    w = Warehouse(":memory:")
    w.put_verdict("SBIN", T0, date(2026, 10, 9), None, None, None, {"verdict": "Hold"})
    latest = w.latest_verdict("SBIN")
    assert latest["close"] is None and latest["annual_period"] is None and latest["quarter"] is None


def test_a_database_without_the_table_reads_as_empty_when_opened_read_only(tmp_path):
    import duckdb

    path = tmp_path / "old.duckdb"
    duckdb.connect(str(path)).close()
    w = Warehouse(path, read_only=True)
    assert w.latest_verdict("TCS") is None and w.verdict_symbols() == [] and w.verdict_history("TCS") == []
    w.close()
