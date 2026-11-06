from __future__ import annotations

from athena.forensic.checks.capitalisation import capitalisation_checks, parse_tail, read_lines
from athena.forensic.checks.document import NOT_BUILT, locate, run_checks

BALANCE = """RESTATED CONSOLIDATED STATEMENT OF ASSETS AND LIABILITIES
(All amounts are in Rs. Millions)
Particulars As at March 31,
2026
As at March 31,
2025
ASSETS
Property, plant and equipment 150.00 120.00
TOTAL NON-CURRENT ASSETS (A) 200.00 160.00
TOTAL CURRENT ASSETS (B) 100.00 90.00
TOTAL ASSETS (A+B) 300.00 250.00
Equity share capital 40.00 40.00
Other equity 160.00 120.00
TOTAL EQUITY (A) 200.00 160.00
TOTAL NON-CURRENT LIABILITIES (B) 60.00 50.00
TOTAL CURRENT LIABILITIES (C) 40.00 40.00
TOTAL EQUITY & LIABILITIES (A+B+C) 300.00 250.00
"""


def capitalisation(c="60.00", ratio="0.30", equity="200.00", share="40.00", a="20.00", b="40.00", date="March 31, 2026") -> str:
    return f"""CAPITALISATION STATEMENT
The following table sets forth our Company's capitalisation as at {date}, derived from our Restated Consolidated
Financial Information, and as adjusted for the Issue.
(₹ in million, except ratios)
Particulars Pre-Issue as at March 31, 2026 As adjusted for the Issue(1)
Borrowings
Current borrowings$ (A)  {a} [●]
Non-current borrowings$ (B)  {b} [●]
Total Borrowings (C=A+B)  {c} [●]
Equity
Equity share capital$* (D)  {share} [●]
Other equity$ (E)  160.00 [●]
Total equity (F=D+E)  {equity} [●]
Ratio: Total Borrowings/ Total equity (H=C/F) (in times)  {ratio} [●]
Notes:
$These terms carry the same meaning as per Schedule III of the Companies Act. 2013, 2026
"""


def pages(cap: str, with_balance: bool = True):
    out = [(7, "capitalisation", cap)]
    if with_balance:
        out.append((9, "financials", BALANCE))
    return out


def statuses(cap: str, **kw) -> dict[str, str]:
    found, _ = locate(pages(cap), ())
    return {c.id.split(":")[0] + (":" + c.id.split(":")[2] if c.id.startswith(("capitalisation_sum", "capitalisation_ratio")) else ""): c.status for c in capitalisation_checks(pages(cap), found)}


def by_id(cap: str):
    found, _ = locate(pages(cap), ())
    return {c.id: c for c in capitalisation_checks(pages(cap), found)}


def test_the_formulas_of_a_capitalisation_table_hold_and_so_do_its_equity_lines_against_the_balance_sheet():
    got = by_id(capitalisation())
    assert {c.status for c in got.values()} == {"ok"}
    assert {"capitalisation_equity_capital_in_balance_sheet", "capitalisation_other_equity_in_balance_sheet", "capitalisation_total_equity_in_balance_sheet"} <= set(got)
    assert any(i.startswith("capitalisation_sum") for i in got) and any(i.startswith("capitalisation_ratio") for i in got)


def test_a_total_that_is_not_the_sum_of_its_letters_differs():
    bad = [c for c in by_id(capitalisation(c="61.00")).values() if c.status == "differs"]
    assert [c.id.split(":")[0] for c in bad] == ["capitalisation_sum"]  # Total Borrowings (C=A+B); the ratio 61/200 = 0.305 still rounds to the printed 0.30
    worse = [c for c in by_id(capitalisation(c="70.00")).values() if c.status == "differs"]
    assert {c.id.split(":")[0] for c in worse} == {"capitalisation_sum", "capitalisation_ratio"}  # and now the ratio no longer fits either


def test_a_ratio_is_allowed_the_rounding_of_its_own_digits_and_no_more():
    assert not [c for c in by_id(capitalisation(ratio="0.30")).values() if c.status == "differs"]  # 60 / 200 = 0.30
    assert [c.id.split(":")[0] for c in by_id(capitalisation(ratio="0.31")).values() if c.status == "differs"] == ["capitalisation_ratio"]  # 60 / 200 cannot print as 0.31: a hundredth is more than the rounding of the ratio
    assert [c.id.split(":")[0] for c in by_id(capitalisation(ratio="0.35")).values() if c.status == "differs"] == ["capitalisation_ratio"]


def test_share_capital_that_is_not_the_balance_sheets_differs_but_total_equity_is_only_ever_confirmed():
    got = by_id(capitalisation(share="41.00", equity="201.00"))
    assert got["capitalisation_equity_capital_in_balance_sheet"].status == "differs"
    total = got["capitalisation_total_equity_in_balance_sheet"]
    assert total.status == "not_run" and "may state total equity differently" in total.reason  # owners' equity, or with or without non-controlling interest


def test_a_date_that_is_not_a_year_end_is_not_compared_with_the_balance_sheet():
    got = by_id(capitalisation(date="September 30, 2026"))
    assert got["capitalisation_total_equity_in_balance_sheet"].status == "not_run" and "year end" in got["capitalisation_total_equity_in_balance_sheet"].reason


def test_a_year_the_extraction_broke_is_still_read():
    assert by_id(capitalisation(date="March 31, 202 6"))["capitalisation_total_equity_in_balance_sheet"].status == "ok"


def test_a_label_that_wraps_over_lines_with_its_figure_alone_on_the_next_is_one_row_and_a_dash_is_nil():
    wrapped = """Particulars Pre-Offer as at March 31, 2026
Total Borrowings
Current borrowings# (A)  -  [●]
Non-current borrowings (including current maturities of long-
term borrowings)# (B)
49.79  [●]
Total Borrowings (A+B=C)  49.79  [●]
"""
    lines = read_lines([(3, "capitalisation", wrapped)])
    assert [(x.value, x.own[:12]) for x in lines] == [(0.0, "Current borr"), (49.79, "term borrowi"), (49.79, "Total Borrow")]
    assert parse_tail(lines[1].label) == ("B", None, ())
    assert [c.status for c in capitalisation_checks([(3, "capitalisation", wrapped)], [])] == ["ok"]


def test_a_total_with_no_formula_is_the_sum_of_the_rows_above_it_since_the_last_total():
    table = """Total Equity (A)  300.00 [●]
Current borrowings* 100.00 [●]
Interest accrued and due on borrowings 5.50 [●]
Current maturities 4.50 [●]
Total Borrowings (B)  110.00 [●]
Total (A+B)  410.00 [●]
"""
    def of(text: str) -> list[str]:
        return [c.status for c in capitalisation_checks([(3, "capitalisation", text)], []) if c.id.startswith(("capitalisation_rows", "capitalisation_sum"))]

    assert of(table) == ["ok", "ok"]  # (B) is the three rows; (A+B) is its letters. (A) has no rows above it.
    assert of(table.replace("110.00", "111.00")) == ["differs", "differs"]


def test_a_ratio_with_two_names_and_no_letters_is_checked_against_the_rows_of_those_names():
    table = """Total Equity (A)  300.00 [●]
Total Borrowings (B)  60.00 [●]
Ratio: Total borrowings/ Total equity (in times)  0.20 [●]
"""
    def of(text: str) -> list[str]:
        return [c.status for c in capitalisation_checks([(3, "capitalisation", text)], []) if c.id.startswith("capitalisation_ratio")]

    assert of(table) == ["ok"]
    assert of(table.replace("0.20", "0.50")) == ["differs"]


def test_a_note_under_the_table_and_a_page_number_are_not_rows():
    lines = read_lines([(7, "capitalisation", capitalisation())])
    assert not any("2013" in x.printed or x.printed == "7" for x in lines) and len(lines) == 7


def test_the_formulas_parse_in_the_orders_drafts_print_them():
    assert parse_tail("Total Borrowings (C = A+B)") == ("C", "+", ("A", "B"))
    assert parse_tail("Total Borrowings (A+B=C)") == ("C", "+", ("A", "B"))
    assert parse_tail("Total Equity (G= D+E+F)") == ("G", "+", ("D", "E", "F"))
    assert parse_tail("Ratio: Non-current borrowings (B) / Total Equity (G) (I = B/G)") == ("I", "/", ("B", "G"))
    assert parse_tail("Ratio: Total Borrowings/ Total Equity (C)/ (D)") == (None, "/", ("C", "D"))
    assert parse_tail("Ratio: Total Borrowings/ Total equity (H=C/G) (in times)") == ("H", "/", ("C", "G"))
    assert parse_tail("Total (A+B)") == (None, "+", ("A", "B"))
    assert parse_tail("Total Equity (D)") == ("D", None, ())
    assert parse_tail("Current borrowings*") == (None, None, ())


def test_the_checks_are_part_of_the_document_checks_and_the_indebtedness_item_says_why_it_is_left():
    result = run_checks(pages(capitalisation()), None)
    assert any(c["id"].startswith("capitalisation_") for c in result["checks"])
    assert any("indebtedness" in item and "later date" in item for item in NOT_BUILT)
