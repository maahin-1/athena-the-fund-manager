import os
from datetime import date, datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

from athena.dashboard.market import PRESETS, data_note, display_value, filter_rows, sort_rows
from athena.pit.bhavcopy import Row
from athena.warehouse.screen import SCREEN_COLUMNS
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
DAY = date(2026, 10, 9)


def row(symbol, kind="stock", **kw):
    base = {column: None for column in SCREEN_COLUMNS}
    base.update(symbol=symbol, kind=kind, name=f"{symbol} Ltd", data_as_of=DAY, close=100.0, flags=0)
    base.update(kw)
    return base


ROWS = [
    row("CHEAP", pe=10.0, pb=1.5, roe=0.18, ret_1y=0.05, market_cap_cr=8000.0, trend="aligned_up", sector="Energy"),
    row("DEAR", pe=45.0, pb=9.0, roe=0.30, ret_1y=0.40, market_cap_cr=90000.0, trend="aligned_down", sector="Tech"),
    row("NOPE", pe=None, pb=None, roe=None, ret_1y=0.25, market_cap_cr=300.0, flags=1),
    row("INDEXFUND", kind="etf", ret_1y=0.12, market_cap_cr=None),
]


def symbols(rows):
    return [r["symbol"] for r in rows]


def test_a_limit_keeps_rows_inside_it_and_drops_a_row_with_no_figure():
    assert symbols(filter_rows(ROWS, ranges={"pe": (None, 20.0)})) == ["CHEAP"]  # NOPE has no P/E: it is not "under 20"
    assert symbols(filter_rows(ROWS, ranges={"pe": (5.0, 50.0)})) == ["CHEAP", "DEAR"]
    assert symbols(filter_rows(ROWS, ranges={"pe": (None, None)})) == symbols(ROWS)  # an empty limit is no limit


def test_type_text_trend_sector_and_clean_filters():
    assert symbols(filter_rows(ROWS, kinds=["etf"])) == ["INDEXFUND"]
    assert symbols(filter_rows(ROWS, text="dea")) == ["DEAR"] and symbols(filter_rows(ROWS, text="cheap ltd")) == ["CHEAP"]
    assert symbols(filter_rows(ROWS, trends=["aligned_up"])) == ["CHEAP"]
    assert symbols(filter_rows(ROWS, sectors=["Tech"])) == ["DEAR"]
    assert "NOPE" not in symbols(filter_rows(ROWS, clean_only=True))


def test_sorting_puts_rows_with_no_figure_last_either_way():
    assert symbols(sort_rows(ROWS, "market_cap_cr", True)) == ["DEAR", "CHEAP", "NOPE", "INDEXFUND"]
    assert symbols(sort_rows(ROWS, "market_cap_cr", False)) == ["NOPE", "CHEAP", "DEAR", "INDEXFUND"]


def test_every_preset_runs_on_the_rows_and_value_finds_the_cheap_profitable_one():
    for preset in PRESETS.values():
        filter_rows(ROWS, preset.kinds, "", preset.ranges, preset.trends)
    value = PRESETS["Value"]
    assert symbols(filter_rows(ROWS, value.kinds, "", value.ranges)) == ["CHEAP"]


def test_percent_columns_are_scaled_for_display_and_a_missing_figure_stays_empty():
    assert display_value(0.125, "pct") == 12.5 and display_value(None, "pct") is None and display_value(18.0, "number") == 18.0


def test_the_data_note_says_how_old_the_prices_are_and_what_is_missing():
    assert "3 days old" in data_note(DAY, date(2026, 10, 12), 150, 2603, 22) and "150 of 2,603 stocks have statements" in data_note(DAY, date(2026, 10, 12), 150, 2603, 22)
    assert "today" in data_note(DAY, DAY, 1, 1, 0)
    assert "No prices" in data_note(None, DAY, 0, 0, 0)


# ---- the page
def make_db(tmp_path, rows=ROWS):
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.upsert_day(DAY, {"CHEAP": Row("CHEAP", "EQ", 1, 1, 1, 1, 1)}, NOW)
        w.set_universe([(r["symbol"], r["kind"], r["name"], "") for r in rows], NOW)
        w.put_screen(rows, NOW, replace_all=True)
    return path


def page():
    import os
    from pathlib import Path

    from athena.dashboard.market import render_market

    render_market(Path(os.environ["MARKET_DB_FOR_TEST"]))


def open_page(path):
    os.environ["MARKET_DB_FOR_TEST"] = str(path)
    return AppTest.from_function(page, default_timeout=30).run()


def test_the_page_shows_the_data_age_a_table_and_how_many_match(tmp_path):
    app = open_page(make_db(tmp_path))
    assert not app.exception
    assert "Prices to 09 Oct 2026" in app.caption[0].value
    assert any("**3** of 4" in m.value or "3 of 4" in m.value for m in app.markdown)  # the three stocks; the ETF is off by default
    assert len(app.dataframe[0].value) == 3


def test_choosing_a_preset_filters_the_table_and_explains_it(tmp_path):
    app = open_page(make_db(tmp_path))
    app.selectbox(key="mkt_preset").select("Value").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 1 and app.dataframe[0].value.iloc[0]["Symbol"] == "CHEAP"
    assert any("P/E under 15" in c.value for c in app.caption)


def test_a_limit_that_is_not_a_number_is_reported_not_ignored_silently(tmp_path):
    app = open_page(make_db(tmp_path))
    app.text_input(key="mkt_max_pe").set_value("cheap").run()
    assert any("not a number" in w.value for w in app.warning)


def test_nothing_matching_says_so(tmp_path):
    app = open_page(make_db(tmp_path))
    app.text_input(key="mkt_min_pe").set_value("1000").run()
    assert any("Nothing matches" in i.value for i in app.info)


def test_a_missing_database_gives_the_instruction_not_a_crash(tmp_path):
    app = open_page(tmp_path / "absent.duckdb")
    assert not app.exception and any("warehouse.build" in i.value for i in app.info)


def test_an_empty_screen_table_says_to_run_the_build(tmp_path):
    app = open_page(make_db(tmp_path, rows=[]))
    assert not app.exception and any("No screen rows" in i.value for i in app.info)
