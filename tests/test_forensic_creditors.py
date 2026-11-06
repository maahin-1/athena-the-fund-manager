from __future__ import annotations

from athena.forensic.checks.creditors import creditors_checks, creditors_table
from athena.forensic.checks.document import NOT_BUILT, locate, run_checks
from athena.forensic.checks.rows import read_table

BALANCE = """RESTATED STATEMENT OF ASSETS AND LIABILITIES
(All amounts are in Rs. Millions)
Particulars As at March 31,
2026
As at March 31,
2025
TOTAL NON-CURRENT ASSETS (A) 200.00 160.00
TOTAL CURRENT ASSETS (B) 100.00 90.00
TOTAL ASSETS (A+B) 300.00 250.00
Equity share capital 40.00 40.00
Other equity 160.00 120.00
TOTAL EQUITY (A) 200.00 160.00
iii) Trade payables 28
Total outstanding dues of micro enterprises
and
small enterprises 70.00 50.00
Total outstanding dues of creditors other
than micro enterprises and small enterprises 30.00 20.00
TOTAL CURRENT LIABILITIES (C) 100.00 90.00
TOTAL EQUITY & LIABILITIES 300.00 250.00
"""


def litigation(total="100.00", date="March 31, 2026", unit="₹ million") -> str:
    return f"""VIII. Outstanding dues to creditors
In accordance with the SEBI ICDR Regulations, our Company considers all creditors to whom the amount due exceeds 5% of the
total trade payables as at {date} (“Material Creditors”). Details of outstanding dues are disclosed below:
Types of creditors Number of creditors Amount ({unit})
Dues to micro, small and medium enterprises  164 70.00
Dues to Material Creditor(s)  3 20.00
Dues to other creditors 58 10.00
Total  225 {total}
"""


def pages(lit: str, balance: str = BALANCE):
    return [(5, "litigation", lit), (9, "financials", balance)]


def run(lit: str, balance: str = BALANCE):
    p = pages(lit, balance)
    found, _ = locate(p, ())
    return creditors_checks(p, found)


def test_the_total_of_the_dues_to_creditors_is_the_two_trade_payables_lines_of_the_balance_sheet_added():
    (c,) = run(litigation())
    assert c.status == "ok" and [t.printed for t in c.left] == ["100.00"] and [t.printed for t in c.right] == ["70.00", "30.00"]


def test_a_label_wrapped_over_three_lines_is_still_the_micro_and_small_enterprises_line():
    table = read_table(9, BALANCE)
    assert any(r.label == "total outstanding dues of micro enterprises and small enterprises" for r in table.rows)


def test_a_total_that_does_not_match_is_never_a_difference_it_is_not_run_with_the_reason():
    (c,) = run(litigation(total="286.08"))
    assert c.status == "not_run" and "wider or narrower" in c.reason


def test_a_date_that_is_not_a_year_end_is_not_compared():
    (c,) = run(litigation(date="August 31, 2026"))
    assert c.status == "not_run" and "year end" in c.reason


def test_units_that_differ_are_not_compared():
    (c,) = run(litigation(unit="₹ in lakhs"), BALANCE)
    assert c.status == "not_run" and "lakh" in c.reason


def test_a_balance_sheet_without_both_lines_is_not_run_and_a_draft_without_the_table_has_no_check():
    (c,) = run(litigation(), BALANCE.replace("Total outstanding dues of creditors other\nthan micro enterprises and small enterprises 30.00 20.00\n", ""))
    assert c.status == "not_run" and "not both read" in c.reason
    assert creditors_table([(5, "litigation", "nothing here")]) is None
    assert run("nothing here") == []


def test_the_check_is_part_of_the_document_checks():
    result = run_checks(pages(litigation()), None)
    assert any(c["id"] == "creditors_equal_trade_payables" and c["status"] == "ok" for c in result["checks"])
    assert not any("trade payables" in item for item in NOT_BUILT)
