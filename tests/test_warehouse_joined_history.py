from datetime import date, datetime, timedelta, timezone

from athena.pit.bhavcopy import Row
from athena.symbol_changes import parse_symbol_changes
from athena.trading_calendar import ist_date
from athena.warehouse.chart_bars import chart_bars
from athena.warehouse.history import adjusted_history
from athena.warehouse.renames import refresh_renames
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 18, 20, 30, tzinfo=IST)
SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"  # five for one


def world(rename_csv="Co Ltd,OLD,NEW,20-Dec-2022\n", actions=()):
    """OLD trades to 2022-12-17 at about 1400. NEW trades from 2022-12-20 at about 1400 as printed, and falls to 280 on 2022-12-22, the
    ex-date of a five for one split."""
    w = Warehouse(":memory:")
    days: dict[date, dict[str, Row]] = {}
    for day, symbol, close, volume in [
        (date(2022, 12, 15), "OLD", 1400, 100.0), (date(2022, 12, 16), "OLD", 1390, 100.0), (date(2022, 12, 17), "OLD", 1380, 100.0),
        (date(2022, 12, 20), "NEW", 1385, 500.0), (date(2022, 12, 21), "NEW", 1395, 500.0), (date(2022, 12, 22), "NEW", 280, 2500.0), (date(2022, 12, 23), "NEW", 281, 2500.0),
    ]:
        days.setdefault(day, {})[symbol] = Row(symbol, "EQ", close, close, close, close, volume)
    for day, rows in sorted(days.items()):
        w.upsert_day(day, rows, NOW)
    w.replace_actions(list(actions), date(2018, 1, 1), date(2027, 1, 1), NOW)
    refresh_renames(w, lambda: parse_symbol_changes(rename_csv), NOW)
    return w


def test_the_old_symbols_bars_are_joined_and_a_later_split_puts_them_on_the_new_footing():
    w = world(actions=[("NEW", date(2022, 12, 22), SPLIT, 2.0, "EQ", "X")])
    bars = adjusted_history(w)("NEW")
    assert [ist_date(b.timestamp).isoformat() for b in bars][:3] == ["2022-12-15", "2022-12-16", "2022-12-17"]
    assert all(b.symbol == "NEW" for b in bars)
    closes = [round(b.close, 2) for b in bars]
    assert closes[:5] == [280.0, 278.0, 276.0, 277.0, 279.0]  # every bar before the ex-date divided by five, the old symbol's included
    assert [b.volume for b in bars][:3] == [500.0, 500.0, 500.0]  # volume the other way


def test_an_action_listed_under_both_symbols_is_applied_once():
    w = world(actions=[("NEW", date(2022, 12, 22), SPLIT, 2.0, "EQ", "X"), ("OLD", date(2022, 12, 22), SPLIT, 2.0, "EQ", "X")])
    assert round(adjusted_history(w)("NEW")[0].close, 2) == 280.0


def test_a_symbol_with_nothing_before_it_is_unchanged_and_a_non_joinable_rename_is_not_joined():
    w = world()
    assert [ist_date(b.timestamp).isoformat() for b in adjusted_history(w)("OLD")] == ["2022-12-15", "2022-12-16", "2022-12-17"]
    far = world("Co Ltd,OLD,NEW,20-Dec-2024\n")  # the rename date does not matter; the bars do: these are adjacent so they join
    assert len(adjusted_history(far)("NEW")) == 7
    gap = Warehouse(":memory:")
    gap.upsert_day(date(2018, 1, 2), {"OLD": Row("OLD", "EQ", 10, 10, 10, 10, 1.0)}, NOW)
    gap.upsert_day(date(2022, 12, 20), {"NEW": Row("NEW", "EQ", 10, 10, 10, 10, 1.0)}, NOW)
    refresh_renames(gap, lambda: parse_symbol_changes("Co,OLD,NEW,20-Dec-2022\n"), NOW)
    assert len(adjusted_history(gap)("NEW")) == 1


def test_the_chart_helper_says_what_it_joined():
    w = world(actions=[("NEW", date(2022, 12, 22), SPLIT, 2.0, "EQ", "X")])
    body = chart_bars(w, "NEW", 5000)
    assert len(body["bars"]) == 7 and body["bars"][0]["t"] == 1671062400  # 2022-12-15
    assert body["joined"] == [{"symbol": "OLD", "until": "2022-12-20", "note": "Earlier history is OLD's, the symbol until 2022-12-20."}]
    assert chart_bars(w, "OLD", 5000)["joined"] == []
    assert len(chart_bars(w, "NEW", 3)["bars"]) == 3  # the limit counts the joined series
