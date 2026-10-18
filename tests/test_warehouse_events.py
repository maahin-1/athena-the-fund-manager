import json
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.warehouse.events import API, parse_events, refresh_events
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 18, 20, 30, tzinfo=IST)


def body(*rows):
    return json.dumps(list(rows)).encode()


def row(symbol, day, purpose="Financial Results", desc="To consider the results", company="A Company Limited"):
    return {"symbol": symbol, "company": company, "purpose": purpose, "bm_desc": desc, "date": day}


def test_a_reply_is_read_for_symbol_date_purpose_and_description_and_unreadable_rows_are_skipped():
    got = parse_events(body(row("AAA", "19-Oct-2026"), row("BBB", "xx"), {"symbol": "", "date": "19-Oct-2026"}, row("CCC", "20-Oct-2026", purpose="Fund Raising")))
    assert [(e["symbol"], e["day"], e["purpose"]) for e in got] == [("AAA", date(2026, 10, 19), "Financial Results"), ("CCC", date(2026, 10, 20), "Fund Raising")]
    assert got[0]["description"] == "To consider the results" and got[0]["company"] == "A Company Limited"


def test_a_reply_that_is_not_a_list_is_an_error():
    with pytest.raises(AthenaError):
        parse_events(b"{}")
    with pytest.raises(AthenaError):
        parse_events(b"<html>")


def test_a_refresh_stores_the_events_and_a_later_one_replaces_the_dates_it_covers_but_keeps_the_past():
    w = Warehouse(":memory:")
    first = refresh_events(w, lambda url: body(row("AAA", "19-Oct-2026"), row("BBB", "22-Oct-2026")), NOW)
    assert first == {"events": 2, "first": "2026-10-19", "last": "2026-10-22"}
    # a week later the listing starts at the 22nd: the 19th is past and stays; the 22nd is replaced by what the listing now says
    later = datetime(2026, 10, 25, 20, 30, tzinfo=IST)
    refresh_events(w, lambda url: body(row("BBB", "22-Oct-2026", desc="Revised"), row("CCC", "30-Oct-2026")), later)
    rows = w._con.execute("SELECT symbol, event_date, description FROM events ORDER BY event_date, symbol").fetchall()
    assert [(r[0], str(r[1]), r[2]) for r in rows] == [("AAA", "2026-10-19", "To consider the results"), ("BBB", "2026-10-22", "Revised"), ("CCC", "2026-10-30", "To consider the results")]


def test_an_event_the_exchange_dropped_from_a_date_it_still_covers_is_dropped_too():
    w = Warehouse(":memory:")
    refresh_events(w, lambda url: body(row("AAA", "19-Oct-2026"), row("BBB", "19-Oct-2026")), NOW)
    refresh_events(w, lambda url: body(row("AAA", "19-Oct-2026")), NOW)  # BBB's meeting on a covered date is gone
    assert [r[0] for r in w._con.execute("SELECT symbol FROM events").fetchall()] == ["AAA"]


def test_a_refresh_that_gets_nothing_keeps_the_table_and_says_so():
    w = Warehouse(":memory:")
    refresh_events(w, lambda url: body(row("AAA", "19-Oct-2026")), NOW)
    with pytest.raises(AthenaError, match="no events"):
        refresh_events(w, lambda url: body(), NOW)

    def refused(url):
        raise AthenaError("HTTP 403")

    with pytest.raises(AthenaError):
        refresh_events(w, refused, NOW)
    assert w._con.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert w.events_status()["events"] == 1


def test_the_calendar_url_is_the_exchanges_own():
    assert API.startswith("https://www.nseindia.com/api/event-calendar")
