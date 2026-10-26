import io
import json
import zipfile
from datetime import date, datetime

import pytest

from athena.contracts import AthenaError
from athena.fundamentals import annual_reports as ar
from athena.fundamentals import lines as spec
from athena.fundamentals.store import FundamentalsStore

Y22, Y21, Y20 = date(2022, 3, 31), date(2021, 3, 31), date(2020, 3, 31)


def statement_page(kind, n=30):
    return f"Consolidated {kind}\n" + " ".join(str(1000 + i) for i in range(n))


PAGES = [statement_page("Statement of Profit and Loss"), statement_page("Balance Sheet")]


def reply(years, base=1000):
    this, last = base, base - 100
    return json.dumps({
        "unit": "crore", "years": years,
        "income": {"sales": [this, last], "other_income": [20, 20], "total_expenses": [800, 800], "interest": [30, 30], "pbt": [200, 200], "tax": [60, 60], "net_profit": [138, 138]},
        "balance": {"equity_capital": [100, 100], "reserves": [900, 900], "borrowings": [300, 300], "total_assets": [1600, 1600]},
    })


class Model:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, user):
        self.calls += 1
        return self.replies.pop(0)


def year(sales):
    return {"income": {"sales": sales * 1e7, "other_income": 20e7, "total_expenses": 800e7, "interest": 30e7, "pbt": 200e7, "tax": 60e7, "net_profit": 138e7},
            "balance": {"equity_capital": 100e7, "reserves": 900e7, "borrowings": 300e7, "total_assets": 1600e7}}


REPORTS = [
    {"title": "FY2021-22", "url": "u22", "published": datetime(2022, 3, 30)},
    {"title": "FY2020-21", "url": "u21", "published": datetime(2021, 3, 30)},
    {"title": "FY2019-20", "url": "u20", "published": datetime(2020, 3, 30)},
    {"title": "FY2023-24", "url": "u24", "published": datetime(2024, 3, 30)},
]


def test_fiscal_end_reads_the_exchanges_title():
    assert ar.fiscal_end("FY2021-22") == 2022 and ar.fiscal_end("FY1999-2000") == 2000 and ar.fiscal_end("Annual report") is None


def test_a_zip_gives_its_largest_pdf_and_anything_else_that_is_not_a_pdf_is_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("small.pdf", b"%PDF-1 small")
        z.writestr("big.pdf", b"%PDF-1 " + b"x" * 100)
        z.writestr("note.txt", b"hello")
    assert ar.pdf_bytes(buf.getvalue()).endswith(b"x" * 100)
    assert ar.pdf_bytes(b"%PDF-1.4 body") == b"%PDF-1.4 body"
    with pytest.raises(AthenaError, match="not a PDF"):
        ar.pdf_bytes(b"<html>")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("a.txt", b"x")
    with pytest.raises(AthenaError, match="no PDF"):
        ar.pdf_bytes(empty.getvalue())


def test_the_chain_starts_at_the_report_whose_year_the_filings_hold_and_each_kept_year_becomes_the_next_reference():
    # FY2021-22 report: columns 2022 (in the filings, must agree) and 2021 (new). FY2020-21 report: 2021 (now the reference) and 2020 (new).
    model = Model(reply(["2022-03-31", "2021-03-31"], 1000), reply(["2021-03-31", "2020-03-31"], 900))
    out = ar.chain(REPORTS, {Y22: year(1000)}, lambda url: PAGES, model, back_to=2021)
    assert sorted(out["kept"]) == [Y20, Y21] and model.calls == 2
    assert [s["report"] for s in out["steps"]] == ["FY2021-22", "FY2020-21"] and all(s["result"].startswith("kept") for s in out["steps"])
    assert out["kept"][Y21]["income"]["sales"] == 900 * 1e7


def test_a_report_that_disagrees_with_the_filings_keeps_nothing_and_ends_the_chain():
    model = Model(reply(["2022-03-31", "2021-03-31"], 1000).replace('"crore"', '"lakh"'))  # a wrong unit puts every line of the overlap year far from the filings
    out = ar.chain(REPORTS, {Y22: year(1000)}, lambda url: PAGES, model, back_to=2016)
    assert out["kept"] == {} and model.calls == 1 and len(out["steps"]) == 1
    assert "nothing is kept" in out["steps"][0]["result"]


def test_a_report_without_statement_pages_or_that_cannot_be_read_ends_the_chain_with_the_reason():
    out = ar.chain(REPORTS, {Y22: year(1000)}, lambda url: ["a chairman's letter"], Model(), back_to=2016)
    assert out["kept"] == {} and out["steps"][0]["result"] == "no statements found in the pages"

    def broken(url):
        raise AthenaError("HTTP 404")

    out = ar.chain(REPORTS, {Y22: year(1000)}, broken, Model(), back_to=2016)
    assert out["steps"][0]["result"].startswith("unreadable") and out["kept"] == {}
    assert ar.chain(REPORTS, {}, lambda url: PAGES, Model())["stop"].startswith("the filings hold no annual year")


def test_reports_newer_than_the_reference_or_older_than_the_limit_are_not_read():
    model = Model(reply(["2022-03-31", "2021-03-31"], 1000))
    out = ar.chain(REPORTS, {Y22: year(1000)}, lambda url: PAGES, model, back_to=2022)
    assert [s["report"] for s in out["steps"]] == ["FY2021-22"] and sorted(out["kept"]) == [Y21]
    assert "FY2023-24" not in [s["report"] for s in out["steps"]]


def test_the_reference_is_read_from_the_store_as_whole_years_with_the_consolidated_basis_first(tmp_path):
    path = tmp_path / "f.duckdb"
    with FundamentalsStore(path) as store:
        st = ar.to_statements("AAA", "consolidated", {Y22: year(1000)})
        store.save_statements(st, "https://x/1.xml", datetime(2022, 5, 1), source="nse-xbrl")
        quarter = ar.to_statements("AAA", "consolidated", {Y21: year(10)})
        quarter.income.clear()
        quarter.income[(date(2021, 1, 1), date(2021, 3, 31))] = {"sales": 5.0}  # a quarter is not a year
        store.save_statements(quarter, "https://x/2.xml", datetime(2021, 5, 1), source="nse-xbrl")
        basis, ref = ar.reference_from_store(store, "AAA")
        assert basis == "consolidated" and list(ref) == [Y22] and ref[Y22]["income"]["sales"] == 1000 * 1e7 and ref[Y22]["balance"]["total_assets"] == 1600e7
        assert ar.reference_from_store(store, "NOPE") == ("", {})


def test_kept_years_are_stored_as_annual_periods_with_the_report_as_source_and_do_not_replace_a_later_filing(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        st = ar.to_statements("AAA", "consolidated", {Y21: year(900)})
        assert list(st.income) == [(date(2020, 4, 1), Y21)] and list(st.balance) == [Y21] and st.layout == spec.INDAS
        store.save_statements(st, "https://x/r.pdf", datetime(2021, 3, 30), source=ar.SOURCE)
        assert store.lines("AAA", "consolidated", spec.INCOME)[(date(2020, 4, 1), Y21)]["sales"] == 900 * 1e7
        later = ar.to_statements("AAA", "consolidated", {Y21: year(901)})
        store.save_statements(later, "https://x/xbrl.xml", datetime(2022, 5, 1), source="nse-xbrl")
        assert store.lines("AAA", "consolidated", spec.INCOME)[(date(2020, 4, 1), Y21)]["sales"] == 901 * 1e7  # the exchange's own, filed later, wins
