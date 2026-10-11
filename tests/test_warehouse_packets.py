from datetime import date, datetime, timedelta, timezone

from fund_fixtures import ACME

from athena.pit.bhavcopy import Row
from athena.warehouse.packets import ACTIVE_WITHIN_DAYS, FUNDAMENTALS, MIN_BARS, TECHNICAL, build_packets
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 12, 20, 30, tzinfo=IST)
LAST = date(2026, 10, 9)


def trading_days(n, end=LAST):
    days, day = [], end
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def row(symbol, close):
    return Row(symbol, "EQ", close, close + 1, close - 1, close, 10000.0)


def market(days=120):
    w = Warehouse(":memory:")
    for i, day in enumerate(trading_days(days)):
        rows = {"ACME": row("ACME", 100.0 + i * 0.1 + (i % 5)), "FUND": row("FUND", 50.0 + i * 0.05)}
        if day >= LAST - timedelta(days=2):
            rows["NEW"] = row("NEW", 10.0)  # too few bars for a packet
        if i < 70:
            rows["DEAD"] = row("DEAD", 5.0 + i * 0.01)  # traded for 70 days, then stopped
        w.upsert_day(day, rows, NOW)
    w.set_universe([("ACME", "stock", "Acme", ""), ("FUND", "etf", "Fund", ""), ("NEW", "stock", "New", ""), ("DEAD", "stock", "Dead", ""), ("TINY", "sme", "Tiny", "")], NOW)
    return w


def test_stocks_and_etfs_get_a_technical_packet_and_only_stocks_with_statements_get_a_fundamentals_packet():
    w = market()
    w.put_statement("ACME", "ok", ACME, "", NOW - timedelta(days=3))
    summary = build_packets(w, NOW)
    assert summary["technical"] == 2 and summary["fundamentals"] == 1 and not summary["failed"]
    assert summary["no_prices"] == 1 and summary["inactive"] == 1 and summary["no_statements"] == 0
    assert w.packet("ACME", TECHNICAL)["packet"]["instrument"] == "ACME"
    assert w.packet("ACME", FUNDAMENTALS)["packet"]["instrument"] == "ACME"
    assert w.packet("FUND", TECHNICAL) is not None and w.packet("FUND", FUNDAMENTALS) is None
    assert w.packet("TINY", TECHNICAL) is None and w.packet("NEW", TECHNICAL) is None and w.packet("DEAD", TECHNICAL) is None


def test_a_packet_carries_the_day_of_its_last_price_and_when_it_was_built():
    w = market()
    build_packets(w, NOW)
    held = w.packet("ACME", TECHNICAL)
    assert held["data_as_of"] == LAST and held["built_at"] == NOW.astimezone(timezone.utc) and held["flags"] == []
    assert held["packet"]["as_of"].startswith("2026-10-09T16:00")


def test_a_stock_without_statements_is_counted_and_old_statements_are_flagged():
    w = market()
    summary = build_packets(w, NOW)
    assert summary["no_statements"] == 1 and w.packet("ACME", FUNDAMENTALS) is None
    w.put_statement("ACME", "ok", ACME, "", NOW - timedelta(days=45))
    build_packets(w, NOW)
    assert w.packet("ACME", FUNDAMENTALS)["flags"] == ["statements fetched 45 days ago"]


def test_a_price_break_no_record_explains_is_flagged_on_both_packets():
    w = market()
    day = trading_days(120)[-1]
    w.upsert_day(day, {"ACME": row("ACME", 20.0), "FUND": row("FUND", 60.0)}, NOW)  # ACME falls about 80% in a night with no record
    w.put_statement("ACME", "ok", ACME, "", NOW)
    build_packets(w, NOW)
    for kind in (TECHNICAL, FUNDAMENTALS):
        flags = w.packet("ACME", kind)["flags"]
        assert len(flags) == 1 and "no recorded action" in flags[0] and str(day) in flags[0]
    assert w.packet("FUND", TECHNICAL)["flags"] == []


def test_a_rebuild_replaces_the_packet_and_the_counts_are_in_the_status():
    w = market()
    w.put_statement("ACME", "ok", ACME, "", NOW)
    build_packets(w, NOW)
    build_packets(w, NOW + timedelta(hours=1))
    assert w.packet("ACME", TECHNICAL)["built_at"] == (NOW + timedelta(hours=1)).astimezone(timezone.utc)
    assert w.status()["packets"] == {"technical": 2, "fundamentals": 1}


def test_only_the_asked_symbols_are_built():
    w = market()
    build_packets(w, NOW, symbols=["FUND"])
    assert w.status()["packets"] == {"technical": 1}


def test_nothing_is_built_for_an_empty_warehouse():
    assert build_packets(Warehouse(":memory:"), NOW)["technical"] == 0
    assert MIN_BARS == 60 and ACTIVE_WITHIN_DAYS == 10
