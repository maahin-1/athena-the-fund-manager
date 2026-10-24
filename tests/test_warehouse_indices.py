from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError, SchemaChangedError
from athena.pit.bhavcopy import Row
from athena.warehouse.indices import MAX_CONSECUTIVE_FAILURES, ingest_indices, parse_index_file
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
HEADER = "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
FILE = (
    HEADER
    + "Nifty 50,09-10-2026,22314.95,22580.75,22294.75,22520.45,288.65,1.3,296205852,26129.26,19.27,2.77,1.52\n"
    + "Nifty Midcap 150,09-10-2026,100,110,90,105,1,1,0,0,-,-,-\n"
)


def loaded_days(*days):
    w = Warehouse(":memory:")
    for day in days:
        w.upsert_day(day, {"TCS": Row("TCS", "EQ", 1, 1, 1, 1, 1)}, NOW)
    return w


def test_the_file_gives_each_index_with_a_blank_where_the_exchange_has_no_figure():
    rows = parse_index_file(FILE)
    assert rows[0] == ("Nifty 50", 22314.95, 22580.75, 22294.75, 22520.45, 19.27, 2.77, 1.52)
    assert rows[1][0] == "Nifty Midcap 150" and rows[1][5:] == (None, None, None)


def test_an_unknown_header_is_a_schema_change():
    with pytest.raises(SchemaChangedError):
        parse_index_file("A,B\n1,2\n")


def test_only_loaded_days_without_an_index_file_are_fetched_and_the_series_reads_back():
    w = loaded_days(date(2026, 10, 8), date(2026, 10, 9))
    asked = []
    summary = ingest_indices(w, lambda url: asked.append(url) or FILE.encode(), date(2026, 10, 1), NOW)
    assert summary["loaded"] == 2 and len(asked) == 2 and "09102026" in asked[1]
    assert w.index_close("Nifty 50") == {date(2026, 10, 8): 22520.45, date(2026, 10, 9): 22520.45}
    assert w.index_valuation("Nifty 50")[date(2026, 10, 9)] == {"pe": 19.27, "pb": 2.77, "div_yield": 1.52}
    assert w.index_valuation("Nifty Midcap 150") == {}  # no P/E that day: left out, not zero
    again = []
    ingest_indices(w, lambda url: again.append(url) or FILE.encode(), date(2026, 10, 1), NOW)
    assert again == []


def test_a_day_the_exchange_has_no_index_file_for_is_left_for_the_next_run():
    w = loaded_days(date(2026, 10, 8), date(2026, 10, 9))
    summary = ingest_indices(w, lambda url: None if "08102026" in url else FILE.encode(), date(2026, 10, 1), NOW)
    assert summary["loaded"] == 1 and summary["missing"] == ["2026-10-08"]
    assert w.index_days_loaded(date(2026, 10, 1), date(2026, 10, 9)) == {date(2026, 10, 9)}


def test_three_refusals_in_a_row_stop_the_run():
    w = loaded_days(*[date(2026, 10, d) for d in (1, 2, 5, 6, 7)])

    def refuse(url):
        raise AthenaError("HTTP 403")

    summary = ingest_indices(w, refuse, date(2026, 10, 1), NOW)
    assert summary["stopped"] is True and len(summary["failed"]) == MAX_CONSECUTIVE_FAILURES


def test_nothing_is_fetched_before_any_price_day_is_loaded():
    assert ingest_indices(Warehouse(":memory:"), lambda url: FILE.encode(), date(2026, 10, 1), NOW)["loaded"] == 0


def test_an_index_listed_twice_in_one_file_keeps_the_row_with_a_close():
    text = FILE + "Nifty 50,09-10-2026,-,-,-,-,0,0,0,0,-,-,-\n" + "Nifty Midcap 150,09-10-2026,1,2,0,1.5,0,0,0,0,10,2,1\n"
    rows = {r[0]: r for r in parse_index_file(text)}
    assert len(rows) == 2
    assert rows["Nifty 50"][4] == 22520.45
    assert rows["Nifty Midcap 150"][4] == 1.5
