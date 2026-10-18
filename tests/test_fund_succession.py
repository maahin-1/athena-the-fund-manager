from datetime import date, datetime, timedelta, timezone

from athena.fundamentals.history import SuccessionTable, chain
from athena.pit.bhavcopy import Row
from athena.symbol_changes import parse_symbol_changes
from athena.warehouse.renames import refresh_renames
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 18, 20, 30, tzinfo=IST)


def build(path, bars, csv, actions=()):
    with Warehouse(path) as w:
        for day, symbol in bars:
            w.upsert_day(day, {symbol: Row(symbol, "EQ", 10, 10, 10, 10, 1.0)}, NOW)
        w.replace_actions(list(actions), date(2018, 1, 1), date(2027, 1, 1), NOW)
        refresh_renames(w, lambda: parse_symbol_changes(csv), NOW)


def test_the_table_answers_for_every_renamed_company_with_a_note_from_the_record(tmp_path):
    path = tmp_path / "w.duckdb"
    build(
        path,
        [(date(2022, 1, 3), "A"), (date(2022, 6, 1), "B"), (date(2022, 12, 1), "C"), (date(2019, 1, 1), "X"), (date(2019, 3, 1), "Y")],
        "Alpha Co,A,B,01-Jun-2022\nAlpha Co Ltd,B,C,01-Dec-2022\nXi Co,X,Y,01-Mar-2019\n",
        [("C", date(2022, 11, 20), "Demerger", None, "EQ", "I")],
    )
    view = SuccessionTable(path)
    assert [p.symbol for p in chain("C", view)] == ["A", "B"]  # through a chain, oldest first
    assert [p.symbol for p in chain("Y", view)] == ["X"]
    assert "Alpha Co Ltd" in view["C"][0].note and "2022-12-01" in view["C"][0].note
    assert "Demerger (2022-11-20)" in view["C"][0].note  # the exchange's own record of a scheme near the rename, for the reader
    assert view.get("A") is None and "ZZZ" not in view


def test_a_symbol_that_kept_trading_is_not_a_predecessor(tmp_path):
    path = tmp_path / "w.duckdb"
    build(path, [(date(2022, 1, 3), "A"), (date(2022, 9, 1), "A"), (date(2022, 6, 2), "B")], "Co,A,B,01-Jun-2022\n")
    # A traded on after the rename to B (the symbol was reused): it is nobody's earlier name
    assert chain("B", SuccessionTable(path)) == []


def test_a_missing_warehouse_or_table_is_an_empty_view_not_an_error(tmp_path):
    assert chain("ETERNAL", SuccessionTable(tmp_path / "none.duckdb")) == []
    with Warehouse(tmp_path / "old.duckdb") as w:
        w._con.execute("DROP TABLE symbol_renames")
    assert chain("ETERNAL", SuccessionTable(tmp_path / "old.duckdb")) == []
