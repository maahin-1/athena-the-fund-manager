from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from athena.contracts import AthenaError
from athena.reference import damodaran as d
from athena.reference.store import ReferenceStore

MIRROR = Path(".athena/damodaran/site/pc/datasets")


def workbook(path: Path, rows, sheet="Industry Averages", dated=True):
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = sheet
    if dated:
        ws.append(["Date updated:", datetime(2026, 1, 5)])
    for r in rows:
        ws.append(r)
    book.save(path)
    return path


TABLE = [
    ["Created by:", "someone"],
    ["Industry Name", "Number of firms", "Beta ", "D/E Ratio", "Cost of Capital (Local Currency)", "Beta"],
    ["Steel", 185, 1.06, 0.26, "NA", 7.0],
    ["Total Market", 5170, 0.766, 0.3, 0.109, 8.0],
    [None, None, None, None, None, None],
    ["Source: a note under the table", None, None, None, None, None],
]


def test_a_workbook_becomes_one_row_per_numeric_cell_with_the_date_the_firm_count_and_clean_names(tmp_path):
    rows = d.parse_industry_workbook(workbook(tmp_path / "waccIndia.xlsx", TABLE), "India", "India")
    by = {(r.industry, r.metric): r for r in rows}
    assert by[("Steel", "beta")].value == 1.06 and by[("Steel", "beta")].n_firms == 185
    assert by[("Steel", "beta_2")].value == 7.0  # a repeated header is told apart, not lost
    assert ("Steel", "cost_of_capital_local_currency") not in by  # 'NA' is no number
    assert by[("Total Market", "cost_of_capital_local_currency")].value == 0.109
    assert {r.as_of for r in rows} == {date(2026, 1, 5)}
    assert {r.dataset for r in rows} == {"wacc"}  # the region suffix is not part of the dataset's name
    assert all(r.region == "India" and r.source == "damodaran" for r in rows)
    assert not any(r.industry.startswith("Source") for r in rows)  # a note under the table is not an industry


def test_the_suffix_is_removed_from_the_dataset_name():
    assert d.dataset_name("waccIndia.xls", "India") == "wacc"
    assert d.dataset_name("beta.xls", "India") == "beta"
    assert d.dataset_name("India.xls", "India") == "India"  # nothing would be left


@pytest.mark.parametrize(
    "rows,dated,sheet,message",
    [(TABLE, False, "Industry Averages", "Date updated"), ([["no header"], ["x", 1]], True, "Industry Averages", "header"), (TABLE, True, "Other", "Industry Averages"), ([["Industry Name", "Beta"]], True, "Industry Averages", "no industry rows")],
)
def test_a_workbook_that_does_not_look_right_is_refused_with_the_reason(tmp_path, rows, dated, sheet, message):
    with pytest.raises(AthenaError, match=message):
        d.parse_industry_workbook(workbook(tmp_path / "xIndia.xlsx", rows, sheet=sheet, dated=dated), "India", "India")


original = d.parse_industry_workbook


def test_loading_a_directory_reports_what_it_skipped_and_still_loads_the_rest(tmp_path):
    # .xls workbooks cannot be written here, so the directory loader is exercised through a fake parser
    (tmp_path / "goodIndia.xls").write_bytes(b"")
    (tmp_path / "badIndia.xls").write_bytes(b"")
    calls = []

    def fake(path, region, suffix):
        calls.append(path.name)
        if path.name.startswith("bad"):
            raise AthenaError("bad: broken")
        return original(workbook(tmp_path / "waccIndia.xlsx", TABLE), region, suffix)

    d.parse_industry_workbook = fake
    try:
        with ReferenceStore(":memory:") as store:
            report = d.load_industry_dir(store, tmp_path)
            assert (report.files, report.skipped) == (1, [("badIndia.xls", "bad: broken")])
            assert store.industry_stats("Steel")["wacc.beta"][0] == 1.06
    finally:
        d.parse_industry_workbook = original
    assert sorted(calls) == ["badIndia.xls", "goodIndia.xls"]


@pytest.mark.skipif(not (MIRROR / "waccIndia.xls").exists(), reason="the Damodaran mirror is not on this machine")
def test_the_real_india_cost_of_capital_workbook_gives_the_published_market_row():
    rows = d.parse_industry_workbook(MIRROR / "waccIndia.xls", "India", "India")
    total = {r.metric: r for r in rows if r.industry == "Total Market"}
    assert total["beta"].n_firms == 5170
    assert round(total["beta"].value, 3) == 0.766
    assert round(total["cost_of_equity"].value, 3) == 0.094
    assert round(total["cost_of_capital"].value, 3) == 0.083
    assert round(total["cost_of_capital_local_currency"].value, 3) == 0.109
    assert total["beta"].as_of == date(2026, 1, 5)


@pytest.mark.skipif(not MIRROR.exists(), reason="the Damodaran mirror is not on this machine")
def test_every_india_workbook_in_the_mirror_loads():
    with ReferenceStore(":memory:") as store:
        report = d.load_industry_dir(store, MIRROR)
        assert report.skipped == [], report.skipped
        assert report.files == 29
        assert len(store.industries()) >= 90
