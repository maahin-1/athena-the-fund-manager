from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from athena.documents.sections import Section
from athena.documents.store import DocStore
from athena.forensic.checks.document import offer_checks, prefer_layout, run_checks
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


def test_a_number_broken_after_its_thousands_comma_is_joined_and_a_half_number_is_not_an_amount() -> None:
    page = "Particulars For the year ended 31st March, 2026 For the year ended 31st March, 2025\nTOTAL INCOME 12, 331.03 4, 032.79\nTOTAL EXPENSES 11,000.00 3,000.00\nPROFIT BEFORE TAX 1,331.03 1,032.79"
    table = read_table(3, page)
    assert table is not None and {r.label: r.values for r in table.rows}["total income"] == (12331.03, 4032.79)
    assert parse_number("12,") is None and parse_number("032.79") is None and parse_number("0.79") == (0.79, 2)  # pieces of a number are not amounts
    assert parse_number("(1") is None and parse_number("4,114.03)") is None and parse_number("(1,114.03)") == (-1114.03, 2)  # nor are the halves of a bracketed one


def test_a_year_range_is_the_year_in_which_it_ends_and_a_broken_decimal_is_joined() -> None:
    page = "Particulars 2025-26 2024-25\nTOTAL INCOME 21,002. 68 19,000.50\nTOTAL EXPENSES 20,000.00 18,000.00\nPROFIT BEFORE TAX 1,002.68 1,000.50"
    table = read_table(3, page)
    assert table is not None and table.years == (2026, 2025)  # not (2025, 2024): `2025-26` is the year ended March 2026
    assert {r.label: r.values for r in table.rows}["total income"] == (21002.68, 19000.5)
    glued = read_table(3, "2023-24Particulars\n2024-25 2025-26\nTOTAL INCOME 1.00 2.00 3.00\nTOTAL EXPENSES 0.50 1.00 1.50")
    assert glued is not None and glued.years == (2024, 2025, 2026)


def test_a_page_of_adjustments_to_profit_is_not_taken_for_an_income_statement() -> None:
    page = ("Particulars For the year ended 31st March, 2026 For the year ended 31st March, 2025\nProfit before tax 1,000.00 900.00\nDepreciation and amortization expense 100.00 90.00\n"
            "Other income (102.30) (42.31)\nInterest income (5.00) (4.00)\nChanges in working capital 20.00 10.00")
    table = read_table(5, page)
    assert table is not None and classify(table) != "income"  # no revenue, total income or total expenses line: the pieces of an income statement are there, the statement is not


def test_two_tables_that_disagree_almost_everywhere_are_two_statements_not_many_errors() -> None:
    other = income(revenue="1,400.00", other="30.00", total="1,430.00", expenses="1,200.00", pbt="230.00")  # another entity's figures on the financial page, in both years
    for old, new in (("900.00", "1,300.00"), ("915.00", "1,325.00"), ("15.00", "25.00"), ("810.00", "1,100.00"), ("105.00", "225.00"), ("92.00 78.50", "202.00 198.50")):
        other = other.replace(old, new)
    result = run_checks(doc([income()], [other]), None)
    assert result["summary"]["differs"] == 0
    assert any(c["reason"].startswith("the two tables differ in most of their figures") for c in result["checks"])


def test_statements_of_no_stated_basis_are_not_compared_when_there_are_more_than_two() -> None:
    a, b, c = income(), income(revenue="1,100.00", total="1,120.00", pbt="220.00"), income()
    result = run_checks(doc([a, b], [c]), None)
    assert not any(i.startswith("revenue_same_in") for i in statuses(result))  # three tables, none says consolidated or standalone: two of them may be two entities


def test_a_statement_that_runs_onto_the_next_page_is_one_statement() -> None:
    assets_only = "\n".join(balance().splitlines()[:11])
    rest = "Particulars As at March 31,\n2026\nAs at March 31,\n2025\nEquity share capital 40.00 40.00\nOther equity 160.00 120.00\nTOTAL EQUITY (A) 200.00 160.00\nTOTAL NON-CURRENT LIABILITIES (B) 60.00 50.00\nTOTAL CURRENT LIABILITIES (C) 40.00 40.00\nTOTAL EQUITY & LIABILITIES (A+B+C) 300.00 250.00\n"
    split_result = run_checks(doc([assets_only + "\nTOTAL ASSETS (A+B) 300.00 250.00\n", rest], []), None)
    assert statuses(split_result)["balance_sheet_balances@2:2026"] == "ok" and statuses(split_result)["liabilities_add_up@2:2025"] == "ok"


def test_a_closing_total_alone_on_the_next_page_joins_the_statement() -> None:
    body = balance().replace("TOTAL EQUITY & LIABILITIES (A+B+C) 300.00 250.00\n", "")
    tail = "As at March 31,\n2026\nAs at March 31,\n2025\nTotal equity and liabilities 300.00 250.00\n[The remainder of this page has been intentionally left blank]"
    result = run_checks(doc([body, tail], []), None)
    assert statuses(result)["balance_sheet_balances@2:2026"] == "ok"


def test_a_line_the_relation_does_not_name_is_included_when_it_reconciles_and_not_judged_when_it_does_not() -> None:
    # total equity 260 = share capital 40 + other equity 160 + instruments entirely equity in nature 60 (a line the relation does not name)
    with_instruments = balance().replace("Other equity 160.00 120.00\nTOTAL EQUITY (A) 200.00 160.00", "Other equity 160.00 120.00\nInstruments entirely equity in nature 60.00 40.00\nTOTAL EQUITY (A) 260.00 200.00")
    result = run_checks(doc([with_instruments], []), None)
    equity = {c["id"]: c for c in result["checks"]}["equity_adds_up@2:2026"]
    assert equity["status"] == "ok" and "included" in equity["reason"]
    unexplained = balance().replace("Other equity 160.00 120.00\nTOTAL EQUITY (A) 200.00 160.00", "Other equity 160.00 120.00\nSecurities premium reserve 7.00 7.00\nTOTAL EQUITY (A) 260.00 200.00")
    unjudged = {c["id"]: c for c in run_checks(doc([unexplained], []), None)["checks"]}["equity_adds_up@2:2026"]
    assert unjudged["status"] == "not_run" and "does not name" in unjudged["reason"]
    plain = {c["id"]: c for c in run_checks(doc([balance().replace("TOTAL EQUITY (A) 200.00", "TOTAL EQUITY (A) 230.00")], []), None)["checks"]}["equity_adds_up@2:2026"]
    assert plain["status"] == "differs"  # nothing unnamed sits between the terms: a real difference stays one


def test_a_statement_whose_own_arithmetic_mostly_fails_is_set_aside_as_misread() -> None:
    wrecked = income(revenue="1,000.00", other="21.00", total="1,000.00", expenses="1.00", pbt="2.00")  # total income, profit before tax and profit after tax all fail
    result = run_checks(doc([wrecked], [wrecked]), None)
    assert result["summary"]["differs"] == 0
    assert any("probably misread" in n for n in result["notes"])
    assert any(c["status"] == "not_run" and c["reason"].startswith("page probably misread") for c in result["checks"])


def test_a_nil_year_after_a_note_number_stays_a_nil_year() -> None:
    page = "Particulars Note No. March 31, 2026 March 31, 2025 March 31, 2024\nEquity share capital 20 240.10 0.10 -\nOther equity 21 474.40 255.71 34.16\nTotal Equity 714.50 255.81 34.16\nTotal Assets 1,440.36 601.11 339.66"
    table = read_table(2, page)
    assert table is not None
    rows = {r.label: r.values for r in table.rows}
    assert rows["equity share capital 20"] == (240.10, 0.10, 0.0)  # not (20, 240.10, 0.10): the dash is the third year
    blocks = read_table(3, "Particulars Note No. For the year ended 31st March, 2026 For the year ended 31st March, 2025\nRevenue from operations 26 25,925.24 21,878.19 -  -\nTotal income 26,060.60 21,925.18")
    assert blocks is not None and {r.label: r.values for r in blocks.rows}["revenue from operations 26"] == (25925.24, 21878.19)  # a block of nils as wide as the figures is still dropped


def test_numbers_with_a_space_before_the_comma_or_point_or_inside_a_bracket_are_joined_and_a_row_with_a_figure_too_many_is_not_guessed() -> None:
    head = "Particulars For the Year Ended March 31, 2026 For the Year Ended March 31, 2025 For the Year Ended March 31, 2024\n"
    spaced = read_table(2, head + "Proceeds from borrowings -  5,000.00  10 ,000.00\nRepayment (3,624.82) (2,808.75) (3 ,421.74)\nMovement in cash credit 629.14 - (1 79.65)\nPayment of lease liabilities (10.78) (9.26) (8 .23)\nFinance costs (1,068.59) (1,049.24) (8 91.07)")
    assert spaced is not None
    assert {r.label: r.values for r in spaced.rows} == {
        "proceeds from borrowings": (0.0, 5000.0, 10000.0), "repayment": (-3624.82, -2808.75, -3421.74), "movement in cash credit": (629.14, 0.0, -179.65),
        "payment of lease liabilities": (-10.78, -9.26, -8.23), "finance costs": (-1068.59, -1049.24, -891.07)}
    shifted = read_table(3, head + "Net cash from financing activities (4,281.86) 1,029.34 5,395.90\nNet increase in cash and cash equivalents (414.97) 110.07 21 9.08\nOther equity 21 474.40 255.71 34.16")
    assert shifted is not None
    labels = {r.label: r.values for r in shifted.rows}
    assert "net increase in cash and cash equivalents (414.97)" not in labels and not any("net increase" in k for k in labels)  # four figures against three columns: the row is left out, not read with the wrong three
    assert labels["other equity 21"] == (474.40, 255.71, 34.16) and labels["net cash from financing activities"] == (-4281.86, 1029.34, 5395.9)  # a note number in front of the figures is not a figure


def test_cash_labels_are_found_whether_they_say_equivalent_or_equivalents_and_as_at_or_at() -> None:
    from athena.forensic.checks.rows import ALIASES

    cash = ALIASES["cashflow"]
    for label in ("cash and cash equivalents at the beginning of the year", "cash and cash equivalent at the beginning of the year", "cash equivalents at the beginning of the year", "cash and cash equivalents as at the opening of the year"):
        assert cash["cash_begin"].search(label), label
    for label in ("cash and cash equivalents at the end of the year", "cash and cash equivalent at the end of the year (refer note 12)", "cash and cash equivalents as at the end of the year", "cash equivalents at the end of the year", "cash & cash equivalents at the closing of the year"):
        assert cash["cash_end"].search(label), label
    for label in ("net increase/(decrease) in cash and cash equivalents", "net increase/(decrease) in cash & cash equivalents (a+b+c)", "net change in cash and cash equivalents"):
        assert cash["net_change"].search(label), label
    assert not cash["cash_end"].search("bank balances other than cash and cash equivalents")
    balance, income_aliases = ALIASES["balance"], ALIASES["income"]
    for label in ("total non-current liabilities", "total non current liabilities", "total non - current liabilities", "total noncurrent liabilities"):
        assert balance["non_current_liabilities"].search(label), label
    for label in ("income tax adjustment for earlier years", "taxation pertaining to earlier years", "tax expense relating to earlier years", "tax in respect of earlier years"):
        assert income_aliases["tax_earlier"].search(label), label
    assert not income_aliases["tax_earlier"].search("current tax")


def figures_only(text: str) -> str:
    """A page as some drafts extract it: each statement line keeps its figures and loses its label (the label is in another block of text)."""
    out = []
    for line in text.splitlines():
        tokens = line.split()
        k = 0
        while k < len(tokens) and parse_number(tokens[len(tokens) - 1 - k]) is not None:
            k += 1
        out.append(" ".join(tokens[len(tokens) - k:]) if k >= 2 else line)
    return "\n".join(out)


def test_the_page_decides_between_the_reading_order_and_the_position_of_the_text() -> None:
    lost = figures_only(income())
    assert read_table(3, lost) is None or classify(read_table(3, lost)) is None  # the default reading cannot tell what the page is
    pages = [(3, "financials", lost), (4, "financials", income())]
    chosen, used = prefer_layout(pages, {3: income(), 4: "RESTATED STATEMENT OF PROFIT AND LOSS\nRevenue from operations 1,000.00 900.00"})
    assert used == [3] and chosen[0][2] == income() and chosen[1][2] == income()  # the position gave the rows on page 3; page 4's layout lost the year headings, so the reading order stays
    assert prefer_layout(pages, {})[1] == []
    tie, used = prefer_layout([(3, "financials", income())], {3: income()})
    assert used == [] and tie[0][2] == income()  # the same yield is not a reason to change


def test_checks_run_on_a_statement_the_reading_order_lost_when_the_position_of_the_text_gives_it() -> None:
    pages = doc([balance(), figures_only(income())], [balance(), figures_only(income())])
    without = run_checks(pages, None)
    layout = {3: income(), 5: income()}
    withit = run_checks(pages, None, layout=layout)
    assert without["summary"]["statement_tables_read"] == 2 and withit["summary"]["statement_tables_read"] == 4
    assert withit["summary"]["ok"] > without["summary"]["ok"] and withit["summary"]["differs"] == 0
    assert any("position of the text" in n and "3, 5" in n for n in withit["notes"]) and not any("position of the text" in n for n in without["notes"])


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
