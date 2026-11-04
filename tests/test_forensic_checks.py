from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from athena.documents.sections import Section
from athena.documents.store import DocStore
from athena.forensic.checks.document import offer_checks, run_checks
from athena.forensic.checks.lane import checks_tool, run_code_lane
from athena.forensic.checks.rows import classify, parse_number, read_table
from athena.forensic.model import Case
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.text.lane import DocData, document_tools
from athena.forensic.tools import base_registry

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def balance(ta="300.00", tel="300.00", ta25="250.00", tel25="250.00", head="Particulars As at March 31,\n2026\nAs at March 31,\n2025", extra="") -> str:
    return f"""RESTATED STATEMENT OF ASSETS AND LIABILITIES
(All amounts are in Rs. Millions)
{head}
ASSETS
Property, plant and equipment 150.00 120.00
Investments - 0.06
TOTAL NON-CURRENT ASSETS (A) 200.00 160.00
Cash and cash equivalents 10.00 8.00
Bank balances other than cash and cash equivalents 5.00 4.00
TOTAL CURRENT ASSETS (B) 100.00 90.00
TOTAL ASSETS (A+B) {ta} {ta25}
Equity share capital 40.00 40.00
Other equity 160.00 120.00
TOTAL EQUITY (A) 200.00 160.00
TOTAL NON-CURRENT LIABILITIES (B) 60.00 50.00
TOTAL CURRENT LIABILITIES (C) 40.00 40.00
TOTAL EQUITY & LIABILITIES (A+B+C) {tel} {tel25}
{extra}"""


def income(revenue="1,000.00", other="20.00", total="1,020.00", expenses="900.00", pbt="120.00", unit="Rs. Millions") -> str:
    return f"""RESTATED STATEMENT OF PROFIT AND LOSS
(All amounts are in {unit})
Particulars For the year ended
31st March, 2026
For the year ended
31st March, 2025
Revenue from operations {revenue} 900.00
Other income {other} 15.00
TOTAL INCOME {total} 915.00
TOTAL EXPENSES {expenses} 810.00
PROFIT BEFORE TAX {pbt} 105.00
Current tax 30.00 26.00
Deferred tax (2.00) 1.00
Income tax adjustment for earlier years - (0.50)
PROFIT FOR THE YEAR (A) 92.00 78.50
"""


def doc(summary: list[str], financials: list[str]) -> list[tuple[int, str, str]]:
    """Page 1 is the cover, then the summary pages (2, 3), then the financial pages (4, 5), one statement to a page."""
    pages = [(1, "front", "cover")] + [(2 + i, "summary_financials", t) for i, t in enumerate(summary)]
    return pages + [(2 + len(summary) + i, "financials", t) for i, t in enumerate(financials)]


def statuses(result: dict) -> dict[str, str]:
    return {c["id"]: c["status"] for c in result["checks"]}


def test_numbers_are_read_as_printed() -> None:
    assert parse_number("1,354.39") == (1354.39, 2)
    assert parse_number("(286.66)") == (-286.66, 2)
    assert parse_number("-") == (0.0, 0)
    assert parse_number("12") == (12.0, 0)
    assert parse_number("(A+B)") is None and parse_number("March,") is None


def test_a_table_is_read_with_its_years_labels_nil_and_negative_figures() -> None:
    table = read_table(2, income())
    assert table is not None and table.years == (2026, 2025) and classify(table) == "income"
    rows = {r.label: r for r in table.rows}
    assert rows["revenue from operations"].values == (1000.0, 900.0) and rows["deferred tax"].values == (-2.0, 1.0)
    assert rows["income tax adjustment for earlier years"].values == (0.0, -0.5) and rows["total income"].decimals == (2, 2)


def test_a_note_column_and_a_block_of_dashes_after_the_figures_do_not_shift_the_values() -> None:
    page = "Note No. For the year ended 31st March, 2026 For the year ended 31st March, 2025\nRevenue from operations 26 25,925.24 21,878.19 -  -\nTOTAL INCOME 26,060.60 21,925.18\nTOTAL EXPENSES 23,741.38 19,783.44\nPROFIT BEFORE TAX 2,319.22 2,141.74"
    table = read_table(3, page)
    assert table is not None
    assert {r.label: r.values for r in table.rows}["revenue from operations 26"] == (25925.24, 21878.19)


def test_a_page_whose_year_columns_cannot_be_read_gives_no_table() -> None:
    assert read_table(1, "Some narrative text with 1,234.00 and 5,678.00 but no heading of years") is None
    assert read_table(1, "Particulars as at\nTotal assets 1.00 2.00") is None


def test_a_consistent_document_has_no_differences_and_every_statement_is_checked() -> None:
    result = run_checks(doc([balance(), income()], [balance(), income()]), None)
    assert result["summary"]["differs"] == 0 and result["summary"]["run"] > 20
    ids = statuses(result)
    assert ids["balance_sheet_balances@2:2026"] == "ok" and ids["profit_after_tax@3:2026"] == "ok"  # 120 - (30 - 2 + 0) = 92
    assert ids["total_assets_same_in_4_and_2:2026"] == "ok" and ids["revenue_same_in_5_and_3:2025"] == "ok"


def test_a_total_that_does_not_balance_is_a_difference_and_a_rounding_gap_is_not() -> None:
    result = run_checks(doc([balance(tel="300.40"), income()], [balance(), income()]), None)
    assert statuses(result)["balance_sheet_balances@2:2026"] == "differs" and statuses(result)["balance_sheet_balances@4:2026"] == "ok"
    rounding = run_checks(doc([balance(tel="300.01"), income()], [balance(), income()]), None)  # a hundredth: the rounding of two printed figures
    assert statuses(rounding)["balance_sheet_balances@2:2026"] == "ok"


def test_the_same_figure_printed_differently_in_two_places_is_a_difference() -> None:
    changed = income(revenue="1,100.00", total="1,120.00", pbt="220.00")
    result = run_checks(doc([balance(), income()], [balance(), changed]), None)
    found = {i: s for i, s in statuses(result).items() if i.startswith("revenue_same_in") and "2026" in i}
    assert list(found.values()) == ["differs"]


def test_two_units_or_a_factor_of_a_thousand_is_not_run_not_a_difference() -> None:
    other_unit = run_checks(doc([balance(), income()], [balance(), income(unit="Rs. Lakhs")]), None)
    assert all(s == "not_run" for i, s in statuses(other_unit).items() if i.startswith("revenue_same_in"))
    small = income(revenue="1.00", other="0.02", total="1.02", expenses="0.90", pbt="0.12")
    thousand = run_checks(doc([balance(), income()], [balance(), small]), None)
    rev = [c for c in thousand["checks"] if c["id"].startswith("revenue_same_in") and c["year"] == 2026]
    assert rev and rev[0]["status"] == "not_run" and "units" in rev[0]["reason"]


def test_a_line_printed_twice_with_different_figures_is_ambiguous_and_not_used() -> None:
    twice = income() + "TOTAL INCOME 999.00 888.00\n"
    result = run_checks(doc([twice], [twice]), None)
    assert all(s == "not_run" for i, s in statuses(result).items() if i.startswith("total_income_adds_up"))
    assert "printed more than once" in next(c["reason"] for c in result["checks"] if c["id"].startswith("total_income_adds_up"))


def test_consolidated_and_standalone_are_never_compared_with_each_other() -> None:
    cons = "CONSOLIDATED " + income(revenue="1,000.00")
    stand = "STANDALONE " + income(revenue="700.00", total="720.00", expenses="600.00")
    result = run_checks(doc([cons], [stand]), None)
    assert not any(i.startswith("revenue_same_in") for i in statuses(result))


def test_no_statements_found_is_said_and_not_counted_as_clean() -> None:
    result = run_checks([(1, "front", "cover"), (2, "business", "We sell steel")], None)
    assert result["summary"]["run"] == 0 and result["checks"][0]["id"] == "statements_found" and result["checks"][0]["status"] == "not_run"
    assert result["summary"]["not_run"] == 3  # no statements, and the two offer checks without figures


def test_the_offer_structure_is_checked_and_a_blank_amount_is_not_run() -> None:
    fig = {"offer_total": {"crore": 1000.0, "printed": "1,000", "page": 5}, "fresh_issue": {"crore": 800.0, "printed": "800", "page": 5}, "offer_for_sale": {"crore": 150.0, "printed": "150", "page": 5},
           "objects": [{"object": "repay borrowings", "crore": 700.0, "printed": "700"}, {"object": "general corporate purposes", "crore": None, "printed": "[●]"}]}
    by_id = {c.id: c for c in offer_checks(fig)}
    assert by_id["offer_structure"].status == "differs" and by_id["objects_within_fresh_issue"].status == "ok"
    over = {**fig, "objects": [{"object": "a", "crore": 900.0, "printed": "900"}]}
    assert {c.id: c.status for c in offer_checks(over)}["objects_within_fresh_issue"] == "differs"
    blank = {c.id: c.status for c in offer_checks({"offer_total": {"crore": None, "printed": "[●]", "page": 5}})}
    assert blank == {"offer_structure": "not_run", "objects_within_fresh_issue": "not_run"}


@pytest.fixture()
def docs(tmp_path):  # noqa: ANN001, ANN201
    def make(summary: list[str], financials: list[str]):  # noqa: ANN202
        path = tmp_path / f"d{abs(hash((tuple(summary), tuple(financials))))}.duckdb"
        store = DocStore(path)
        pages = doc(summary, financials)
        by_page = {n: sec for n, sec, _ in pages}
        last_summary = 1 + len(summary)
        sections = [Section("front", "Cover", 1, 1), Section("summary_financials", "Summary financial information", 2, last_summary), Section("financials", "Financial information", last_summary + 1, len(pages))]
        store.add("d1", kind="DRHP", company="Demo", url="u", sha256="s" + path.name, published=date(2026, 9, 1), pages=[t for _, _, t in pages], unreadable=[], sections=sections, section_of=lambda secs, n: by_page[n], now=NOW)
        return DocData(path)

    return make


def run_supervisor(tmp_path, data: DocData):  # noqa: ANN001, ANN202
    registry = base_registry([*document_tools(data), checks_tool(data)])
    sup = Supervisor(ForensicStore(tmp_path / "f.duckdb"), registry, {}, None, kind_lanes={"drhp": ("checks", "text")}, code_lanes={"checks": run_code_lane(data)})
    return sup.investigate(Case("d1-run", "drhp", "d1", date(2026, 11, 4))), sup.store


def test_the_checks_lane_establishes_a_violated_invariant_by_code_and_asks_the_text_lane_why(docs, tmp_path) -> None:  # noqa: ANN001
    data = docs([balance(tel="300.40"), income()], [balance(), income()])
    report, store = run_supervisor(tmp_path, data)
    # one wrong total makes two relations fail (it should equal total assets, and it should equal the sum of its parts): one finding, not two
    assert report["concern"]["established"] == 1 and report["concern"]["low"] == "moderate" and report["cost"]["model_calls"] == 0  # one established arithmetic difference weighs 3 of the 2 to 5 that is "moderate"
    flag = report["established"][0]
    assert flag["author"] == "checks" and flag["type"] == "arithmetic" and "page 2" in flag["text"] and flag["why"].startswith("the code's own invariant")
    assert [q["to"] for q in report["unanswered_questions"]] == ["text"]
    assert "already flagged" in report["lanes"]["checks"][0]["note"]
    assert any("Document checks:" in o.text for o in store.entries("d1-run", kind="OBSERVATION"))
    assert {n["lane"] for n in report["not_examined"]} == {"text"}  # the model lane is not built in this run, and the report says so


def test_a_clean_document_gives_no_flags_but_the_observation_says_what_ran(docs, tmp_path) -> None:  # noqa: ANN001
    report, store = run_supervisor(tmp_path, docs([balance(), income()], [balance(), income()]))
    assert report["concern"]["established"] == 0 and report["concern"]["low"] == "none established"
    assert any("Document checks:" in o.text and "0 differ" in o.text for o in store.entries("d1-run", kind="OBSERVATION"))


def test_many_differences_at_once_mean_a_misread_table_and_no_flag_is_written(docs, tmp_path) -> None:  # noqa: ANN001
    wrecked_income = income(revenue="1,000.00", other="21.00", total="1,000.00", expenses="1.00", pbt="2.00")
    wrecked_balance = balance(ta="999.00", tel="1.00", ta25="888.00", tel25="2.00")
    report, store = run_supervisor(tmp_path, docs([wrecked_balance, wrecked_income], [balance(), income()]))
    assert report["concern"]["established"] == 0 and report["concern"]["single_source"] == 0
    assert any("misread" in o.text for o in store.entries("d1-run", kind="OBSERVATION"))
