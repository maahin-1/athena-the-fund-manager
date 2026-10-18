from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.pit.bhavcopy import Row
from athena.symbol_changes import Rename, parse_symbol_changes
from athena.warehouse.renames import Span, build_rows, classify, event_text, refresh_renames
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 18, 20, 30, tzinfo=IST)


def span(first, first_close, last, last_close):
    return Span(first, first_close, last, last_close)


def rename(old="OLD", new="NEW", day=date(2022, 12, 20)):
    return Rename("A Company Limited", old, new, day)


# ---- the rule

def test_adjacent_bars_with_a_small_step_join():
    got = classify(rename(), span(date(2018, 1, 1), 100, date(2022, 12, 19), 1372.0), span(date(2022, 12, 20), 1389.0, date(2026, 10, 16), 900))
    assert got["joinable"] and got["retired"] and got["gap_days"] == 1 and got["reason"] == ""
    assert got["step"] == pytest.approx(1389 / 1372 - 1)


@pytest.mark.parametrize(
    "old, new, why",
    [
        (span(date(2018, 1, 1), 1, date(2022, 6, 24), 10), span(date(2025, 6, 19), 11, date(2026, 10, 16), 12), "gap"),  # three years apart
        (span(date(2018, 1, 1), 1, date(2022, 12, 19), 10), span(date(2022, 12, 20), 100, date(2026, 10, 16), 12), "step"),  # a tenfold jump
        (span(date(2018, 1, 1), 1, date(2022, 12, 19), 10), None, "no bars"),
        (None, span(date(2022, 12, 20), 100, date(2026, 10, 16), 12), "no bars"),
        (span(date(2018, 1, 1), 1, date(2023, 1, 5), 100), span(date(2022, 12, 20), 100, date(2026, 10, 16), 12), "overlap"),  # both traded at once
    ],
)
def test_anything_else_is_not_joined_and_says_why(old, new, why):
    got = classify(rename(), old, new)
    assert not got["joinable"] and why in got["reason"]


def test_a_symbol_that_kept_trading_is_not_retired_so_a_reused_symbol_is_never_a_predecessor():
    still = span(date(2018, 1, 1), 1, date(2026, 10, 16), 10)
    assert classify(rename(), still, span(date(2022, 12, 20), 10, date(2026, 10, 16), 12))["retired"] is False
    assert classify(rename(), None, None)["retired"] is True  # delisted before our data begins


def test_a_demerger_or_scheme_near_the_rename_is_kept_as_wording_not_as_a_reason_to_refuse():
    actions = [("NEW", date(2022, 12, 1), "Demerger"), ("NEW", date(2024, 1, 1), "Demerger"), ("NEW", date(2022, 12, 10), "Interim Dividend - Rs 5")]
    assert event_text(rename(), actions) == "Demerger (2022-12-01)"
    assert event_text(rename(), []) == ""


# ---- the table

def load(w, symbol, closes, start=date(2022, 12, 1)):
    for i, close in enumerate(closes):
        w.upsert_day(start + timedelta(days=i), {symbol: Row(symbol, "EQ", close, close, close, close, 10.0)}, NOW)


def test_refresh_stores_every_rename_with_its_checks_and_keeps_the_old_table_when_the_fetch_fails():
    w = Warehouse(":memory:")
    load(w, "OLD", [100, 101, 102], date(2022, 12, 15))  # to 12-17
    load(w, "NEW", [103, 104], date(2022, 12, 19))
    csv = "A Company Limited,OLD,NEW,19-Dec-2022\nOther Ltd,GONE,ELSEWHERE,01-Jan-2019\n"
    summary = refresh_renames(w, lambda: parse_symbol_changes(csv), NOW)
    assert summary == {"renames": 2, "joinable": 1, "retired": 2}
    assert [(p.old, p.new) for p in w.predecessors("NEW")] == [("OLD", "NEW")]
    assert w.predecessors("ELSEWHERE") == []  # no bars under either name: nothing to join, though the rename is recorded

    def down():
        raise AthenaError("NSE symbol changes unavailable: HTTP 503")

    with pytest.raises(AthenaError):
        refresh_renames(w, down, NOW)
    assert [(p.old, p.new) for p in w.predecessors("NEW")] == [("OLD", "NEW")]  # the last good table stands


def test_a_chain_is_followed_oldest_first_and_stops_at_a_loop_and_at_five_hops():
    w = Warehouse(":memory:")
    load(w, "A", [10, 10], date(2020, 1, 1))
    load(w, "B", [10, 10], date(2020, 1, 3))
    load(w, "C", [10, 10], date(2020, 1, 5))
    csv = "Co,A,B,03-Jan-2020\nCo,B,C,05-Jan-2020\nCo,C,A,07-Jan-2020\n"
    refresh_renames(w, lambda: parse_symbol_changes(csv), NOW)
    assert [p.old for p in w.predecessors("C")] == ["A", "B"]  # A's own record is a loop back from C, so it is not followed again

    names = [f"S{i}" for i in range(8)]
    for i, name in enumerate(names):
        load(w, name, [10, 10], date(2021, 1, 1) + timedelta(days=2 * i))
    csv = "\n".join(f"Co,{a},{b},{(date(2021, 1, 1) + timedelta(days=2 * (i + 1))):%d-%b-%Y}" for i, (a, b) in enumerate(zip(names, names[1:])))
    refresh_renames(w, lambda: parse_symbol_changes(csv), NOW)
    assert len(w.predecessors("S7")) == 5


def test_joinable_only_leaves_out_a_rename_whose_prices_do_not_join_but_statements_still_follow_it():
    w = Warehouse(":memory:")
    load(w, "OLD", [10], date(2018, 1, 1))
    load(w, "NEW", [10], date(2022, 12, 20))
    refresh_renames(w, lambda: parse_symbol_changes("Co,OLD,NEW,20-Dec-2022\n"), NOW)
    assert w.predecessors("NEW") == []
    assert [p.old for p in w.predecessors("NEW", joinable_only=False)] == ["OLD"]
    assert w.predecessors("NEW", joinable_only=False)[0].reason.startswith("gap")


def test_build_rows_reads_actions_for_the_event_wording():
    rows = build_rows(
        {"OLD": [rename("OLD", "NEW", date(2022, 12, 20))]},
        {"OLD": span(date(2018, 1, 1), 10, date(2022, 12, 19), 10), "NEW": span(date(2022, 12, 20), 10, date(2026, 1, 1), 11)},
        [("NEW", date(2022, 12, 5), "Scheme Of Arrangement - Bonus Ncrps 46:1")],
        NOW,
    )
    assert len(rows) == 1 and "Scheme Of Arrangement" in rows[0]["event"]
