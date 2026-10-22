from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from athena.contracts import AthenaError
from athena.reference import country as c
from athena.reference.store import ReferenceStore

MIRROR = Path(".athena/damodaran/site/pc/datasets")


def workbook(path: Path, day=datetime(2026, 7, 1), mature=0.042, header=True):
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "ERPs by country"
    ws.append(["Country and Equity Risk Premiums"])
    ws.append(["Date of update:", day, "a note"])
    ws.append(["Enter the current risk premium for a mature equity market", None, None, None, mature, "note"])
    ws.append(["Enter the current risk premium for the US =", 0.0442])
    ws.append(["Do you want to adjust ...", "Yes"])
    ws.append(["If yes, enter the multiplier to use on the default spread (...)", None, None, None, 1.55])
    ws.append([])
    if header:
        ws.append(["Country", "Africa", "Moody's rating", "Rating-based Default Spread", "Total Equity Risk Premium", "Country Risk Premium", "Sovereign CDS", "Total Equity Risk Premium2"])
    ws.append(["India", "Asia", "Baa3", 0.0175, 0.0692, 0.0272, 0.0072, 0.0532])
    ws.append(["Albania", "Eastern Europe & Russia", "Ba3", 0.0286, 0.0865, 0.0445, "NA", "NA"])
    ws.append([None, None])
    ws.append(["a footnote", "x", "y", 1, 2, 3])
    book.save(path)
    return path


def test_the_country_table_the_date_and_the_mature_premium_are_read(tmp_path):
    parsed = c.parse_country_workbook(workbook(tmp_path / "ctryprem.xlsx"))
    assert (parsed.as_of, parsed.mature_erp, parsed.us_erp, parsed.vol_multiplier) == (date(2026, 7, 1), 0.042, 0.0442, 1.55)
    assert parsed.rows[0] == ("India", "Asia", "Baa3", 0.0175, 0.0692, 0.0272)
    assert [r[0] for r in parsed.rows] == ["India", "Albania"]  # the table ends at the blank row; the footnote is not a country


def test_a_workbook_without_the_country_table_is_refused(tmp_path):
    with pytest.raises(AthenaError, match="lacks"):
        c.parse_country_workbook(workbook(tmp_path / "ctryprem.xlsx", header=False))


def test_each_dated_file_is_loaded_under_its_own_date_and_a_past_date_reads_the_version_then_current(tmp_path):
    workbook(tmp_path / "ctryprem.xlsx", day=datetime(2026, 1, 1), mature=0.0423)
    workbook(tmp_path / "ctrypremJuly26.xlsx", day=datetime(2026, 7, 1), mature=0.042)
    (tmp_path / "ctryprem_old_copy.xlsx").write_bytes(b"not a workbook")  # a name outside the pattern is ignored
    with ReferenceStore(":memory:") as store:
        assert len(c.load_country_files(store, tmp_path)) == 2
        assert store.input("erp_mature", date(2026, 3, 1)).value == 0.0423
        assert store.input("erp_mature", date(2026, 9, 1)).value == 0.042
        assert store.country_premium("India", date(2026, 3, 1))[4] == date(2026, 1, 1)


@pytest.mark.skipif(not (MIRROR / "ctrypremJuly26.xlsx").exists(), reason="the Damodaran mirror is not on this machine")
def test_the_real_july_2026_file_gives_india_baa3():
    parsed = c.parse_country_workbook(MIRROR / "ctrypremJuly26.xlsx")
    india = next(r for r in parsed.rows if r[0] == "India")
    assert parsed.as_of == date(2026, 7, 1) and india[2] == "Baa3"
    assert round(india[3], 4) == 0.0175 and round(india[5], 4) == 0.0272
