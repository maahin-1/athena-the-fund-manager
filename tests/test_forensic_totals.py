from __future__ import annotations

from athena.forensic.checks.document import NOT_BUILT, run_checks
from athena.forensic.checks.totals import LITIGATION, read_line, total_checks

SECTION = "capital_structure"


def table(a_shares="23,289,700", a_pct="97.00", b_shares="720,300", b_pct="3.00", c_shares="24,010,000", c_pct="100.00", extra="") -> str:
    """The shape of a promoter table in a draft: wrapped names, a total per group, a total of the totals, blanks for the columns after the offer."""
    return f"""Name Pre-Offer Post-Offer#
Number of Equity Shares of face value of ₹ 10 each
Percentage of pre-Offer Equity Share capital (%)
Promoters (including Promoter Selling Shareholders)
Dharmesh Ashokbhai
Makadiya
8,763,650 36.50 [●] [●]
Kalariya Chiragbhai
Dineshbhai
6,842,850 28.50 [●] [●]
Kalariya Archanaben 2,160,900 9.00 [●] [●]
Bhargav Chaturbhai
Kavar
5,522,300 23.00 [●] [●]
Total (A) {a_shares} {a_pct} [●] [●]
Promoter Group (other than Promoters)
Chaturbhai Harjibhai
Kavar
240,100 1.00 [●] [●]
Patel Khushbu Indravadan 240,100 1.00 [●] [●]
Jalpa Bhargav Kavar 240,100 1.00 [●] [●]
Total (B) {b_shares} {b_pct} [●] [●]
Total (C=A+B) {c_shares} {c_pct} [●] [●]
{extra}"""


def run(text: str, figures=None, section: str = SECTION):
    return total_checks([(7, section, text)], figures)[0]


def statuses(checks) -> dict[str, str]:
    return {c.id: c.status for c in checks}


def test_a_table_with_wrapped_names_and_blank_columns_is_read_and_every_sum_holds():
    checks, notes = total_checks([(7, SECTION, table())], {"pre_offer_shares": 24_010_000})
    got = statuses(checks)
    assert got["holding_shares:7:1"] == got["holding_pct:7:1"] == "ok"  # the promoters add up to Total (A)
    assert got["holding_shares:7:2"] == got["holding_pct:7:2"] == "ok"  # the promoter group to Total (B)
    assert got["holding_total_shares:7:3"] == got["holding_total_pct:7:3"] == "ok"  # (A) and (B) to Total (C=A+B)
    assert not notes and all(s == "ok" for s in got.values())


def test_a_share_count_that_does_not_add_up_differs_exactly_with_no_rounding_allowed():
    checks = run(table(a_shares="23,289,701"))
    differing = [c for c in checks if c.status == "differs"]
    assert {c.id for c in differing} == {"holding_shares:7:1", "holding_total_shares:7:3"}  # the total of the totals is out by the same one share
    assert all(c.tolerance < 1e-6 for c in differing)  # a count is exact: no half unit is allowed
    assert "23,289,701" in {t.printed for c in differing for t in (*c.left, *c.right)}


def test_a_percentage_is_allowed_the_rounding_of_its_rows_and_no_more():
    ok = statuses(run(table(a_pct="97.01")))  # one hundredth out is within the rounding of four rows
    assert ok["holding_pct:7:1"] == "ok"
    bad = statuses(run(table(a_pct="96.50")))
    assert bad["holding_pct:7:1"] == "differs" and bad["holding_shares:7:1"] == "ok"  # only the percentage is wrong


def test_a_line_with_a_count_that_did_not_read_as_a_row_stops_a_difference_being_claimed():
    broken = table().replace("Kalariya Archanaben 2,160,900 9.00 [●] [●]", "Kalariya Archanaben 2,160,900\n9.00 [●] [●]")  # the row came out over two lines
    got = statuses(run(broken))
    assert got["holding_shares:7:1"] == "not_run"
    check = next(c for c in run(broken) if c.id == "holding_shares:7:1")
    assert "did not read as a row" in check.reason


def test_a_sentence_that_quotes_a_count_above_a_table_does_not_hide_a_difference_in_it():
    intro = "As of the date of this Draft Red Herring Prospectus, our Promoters hold 23,289,700 Equity Shares of face\nvalue of ₹10 each.\n"
    assert statuses(run(intro + table(a_shares="23,289,701")))["holding_shares:7:1"] == "differs"


def test_the_total_of_a_table_of_another_shape_is_not_checked_and_is_counted():
    buildup = """Date of allotment Number of shares Face value Price Percentage
12-Mar-2019 1,000,000 10 10.00 4.17
03-Jun-2021 7,763,650 10 25.00 32.34
Total 8,763,650 36.50 [●]
"""
    checks, notes = total_checks([(7, SECTION, buildup)], None)
    assert checks == [] and len(notes) == 1 and "1 total row(s)" in notes[0]


def test_a_wide_pattern_row_is_not_read_as_a_narrow_one():
    kind, _ = read_line("Total 30*^ 61,940,590* - - 61,940,590* 100.00", 3)
    assert kind == "mixed"
    kind, row = read_line("Kalariya Archanaben 2,160,900 9.00 [●] [●]", 3)
    assert kind == "row" and (row.count, row.second, row.label) == (2160900, 9.0, "Kalariya Archanaben")
    kind, row = read_line("Nachiketa Gift-Ads Private Limited 1,386,610 29.54", 3)
    assert kind == "row" and row.count == 1386610
    assert read_line("Promoter Group (other than Promoters)", 3)[0] == "label"
    assert read_line("Total (B) Nil NA [●] [●]", 3)[1].count == 0
    assert read_line("Total (B) 620 Negligible [●] [●]", 3)[1].second is None


def test_a_total_of_the_totals_above_it_is_checked_when_the_total_is_not_lettered():
    text = """Promoters
Ashish Agarwal 75,654,000 35.81 [●] [●]
Richhi Agarwal 225,000 0.11 [●] [●]
Total 75,879,000 35.92 [●] [●]
Promoter Group
Sulochana Agarwal 20,610,000 9.76 [●] [●]
Avyakt Agarwal 90,000 0.04 [●] [●]
Total 20,700,000 9.80 [●] [●]
Total 96,579,000 45.72 [●] [●]
"""
    got = statuses(run(text))
    assert set(got.values()) == {"ok"} and "holding_shares:7:3" in got  # the last total is checked as the total of the two above it
    wrong = statuses(run(text.replace("Total 96,579,000", "Total 96,579,001")))
    assert wrong["holding_shares:7:3"] == "differs"


def test_a_lettered_total_is_not_the_sum_of_the_lettered_totals_before_it():
    text = """Name Pre-Offer
Ashish Agarwal 100 50.00 [●]
Richhi Agarwal 100 50.00 [●]
Total (A) 200 100.00 [●]
Avyakt Agarwal 30 0.04 [●]
Total (B) 30 0.04 [●]
"""
    assert all(c.status == "ok" for c in run(text, {"pre_offer_shares": 200}))  # (B) has one holder, so there is nothing to check, and (A) is not summed into it


def test_a_composite_whose_part_is_missing_does_not_run():
    text = """Name Pre-Offer
Ashish Agarwal 100 50.00 [●]
Richhi Agarwal 100 50.00 [●]
Total (A) 200 100.00 [●]
Total (A+B) 230 100.00 [●]
"""
    c = next(c for c in run(text) if c.id.startswith("holding_total"))
    assert c.status == "not_run" and "B" in c.reason


def test_a_holding_printed_as_100_percent_reaches_the_shares_before_the_offer_to_the_rounding_of_its_percentage():
    figures = {"pre_offer_shares": 24_010_000}
    tie = next(c for c in run(table(), figures) if c.id == "holding_reaches_capital")
    assert tie.status == "ok"
    # three hundred and ten short of thirty-one million prints as 100.00: within the rounding of the percentage
    near = table(c_shares="30,999,690")
    assert next(c for c in run(near, {"pre_offer_shares": 31_000_000}) if c.id == "holding_reaches_capital").status == "ok"
    far = next(c for c in run(table(), {"pre_offer_shares": 25_000_000}) if c.id == "holding_reaches_capital")
    assert far.status == "not_run" and "another date" in far.reason  # never a difference: the table may be of an earlier date


def test_the_tie_says_why_it_did_not_run():
    assert next(c for c in run(table(), None) if c.id == "holding_reaches_capital").reason.startswith("the number of shares before the offer was not found")
    assert next((c for c in run("nothing here", None)), None) is None


def test_only_the_sections_that_print_holdings_are_read():
    assert total_checks([(7, "risk_factors", table())], None) == ([], [])


def test_pages_that_are_not_next_to_each_other_do_not_make_one_table():
    first, second = "Name\nAshish Agarwal 100 50.00 [●]\nRichhi Agarwal 100 50.00 [●]\n", "Total 300 100.00 [●]\n"
    figures = {"pre_offer_shares": 300}
    together, _ = total_checks([(7, SECTION, first), (8, SECTION, second)], figures)
    apart, notes = total_checks([(7, SECTION, first), (9, SECTION, second)], figures)
    assert together[0].status == "differs"  # 100 + 100 is not 300
    assert [c.id for c in apart] == ["holding_reaches_capital"] and "1 total row(s)" in notes[0]  # the total was set aside, not checked against rows it does not belong to


def creditors(msme="164 168.26", material="3 532.96", other="58 83.72", total="225 784.94") -> str:
    return f"""VIII. Outstanding dues to creditors
Types of creditors Number of creditors Amount (₹ million)
Dues to micro, small and medium enterprises  {msme}
Dues to Material Creditor(s)  {material}
Dues to other creditors {other}
Total  {total}
"""


def test_a_table_of_dues_to_creditors_is_read_and_the_numbers_and_amounts_add_up():
    checks = total_checks([(9, "litigation", creditors())], None)[0]
    assert {c.id: c.status for c in checks} == {"litigation_count:9:1": "ok", "litigation_amount:9:1": "ok"}


def test_a_wrong_number_or_amount_of_creditors_differs_and_only_in_its_own_column():
    wrong_number = {c.id: c.status for c in total_checks([(9, "litigation", creditors(total="226 784.94"))], None)[0]}
    assert wrong_number == {"litigation_count:9:1": "differs", "litigation_amount:9:1": "ok"}
    wrong_amount = {c.id: c.status for c in total_checks([(9, "litigation", creditors(total="225 785.94"))], None)[0]}
    assert wrong_amount == {"litigation_count:9:1": "ok", "litigation_amount:9:1": "differs"}
    one_hundredth = total_checks([(9, "litigation", creditors(total="225 784.95"))], None)[0]
    amount = next(c for c in one_hundredth if c.id.endswith("amount:9:1"))
    assert amount.status == "ok" and amount.tolerance < 0.03  # a hundredth is inside the rounding of four two-decimal figures, and the rounding allowed is no more than that


def test_rows_that_are_parts_of_a_numbered_heading_are_not_added_to_the_rows_that_contain_them():
    nested = """VII. Outstanding dues to creditors
Type of creditor No. of cases Amount outstanding
1. Dues to micro, small and medium enterprises  45 98.82
2. Dues to Material Creditor(s)
MSME 4 86.93
Other than MSME - -
3. Dues to other creditors  116 33.30
Total  161 132.12
"""
    assert {c.status for c in total_checks([(9, "litigation", nested)], None)[0]} == {"ok"}  # 45 + 116, the four are already inside the 45
    assert {c.status for c in total_checks([(9, "litigation", nested.replace("Total  161 132.12", "Total  162 132.12"))], None)[0]} == {"differs", "ok"}


def test_a_table_of_nothing_but_nil_says_nothing_and_is_not_counted():
    nil = """Indirect tax Nil Nil
Direct tax Nil Nil
Total Nil Nil
"""
    assert total_checks([(9, "litigation", nil)], None) == ([], [])


def test_the_litigation_lines_use_amounts_not_percentages():
    kind, row = read_line("Dues to other creditors 58 2,513.96", 3, LITIGATION)
    assert kind == "row" and (row.count, row.second, row.second_decimals) == (58, 2513.96, 2)
    assert read_line("Dues to other creditors 58 2,513.96", 3)[0] == "mixed"  # a holdings table has no such second figure, so the line is another shape of table
    assert read_line("Indirect tax Nil NA", 3, LITIGATION)[1].second == 0.0  # nothing counted, so nothing to add


def test_the_checks_are_part_of_the_document_checks_and_no_longer_listed_as_not_built():
    result = run_checks([(7, SECTION, table()), (9, "litigation", creditors())], None)
    assert any(c["id"].startswith("holding_") for c in result["checks"]) and any(c["id"].startswith("litigation_") for c in result["checks"])
    listed = " ".join((*result["not_built"], *NOT_BUILT))
    assert "shareholding" not in listed and "litigation" not in listed
