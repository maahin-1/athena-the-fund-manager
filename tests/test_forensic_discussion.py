from __future__ import annotations

from athena.forensic.checks.discussion import discussion_checks, sentences
from athena.forensic.checks.document import NOT_BUILT, locate, run_checks

INCOME = """RESTATED STATEMENT OF PROFIT AND LOSS
(All amounts are in Rs. Millions)
Particulars For the year ended
31st March, 2026
For the year ended
31st March, 2025
Revenue from operations 1,000.00 900.00
Other income 20.00 15.00
TOTAL INCOME 1,020.00 915.00
TOTAL EXPENSES 900.00 810.00
PROFIT BEFORE TAX 120.00 105.00
Current tax 30.00 26.00
Deferred tax (2.00) 1.00
PROFIT FOR THE YEAR (A) 92.00 80.00
"""

TOTAL = "Our total income increased by 11.48% to ₹ 1,020.00 million in Fiscal 2026 from ₹ 915.00 million in Fiscal 2025."
REVENUE = "Our revenue from operations increased by 11.11% to ₹ 1,000.00 million in Fiscal 2026 from ₹ 900.00 million in Fiscal 2025."


def run(text: str, income: str = INCOME):
    pages = [(5, "mdna", text), (9, "financials", income)]
    found, _ = locate(pages, ())
    return discussion_checks(pages, found)


def kinds(text: str, income: str = INCOME) -> dict[str, str]:
    return {c.id.split(":")[0] + (c.id[-1] if c.id.startswith("discussion_figure") else ""): c.status for c in run(text, income)}


def test_a_sentence_that_follows_from_its_figures_and_matches_the_statement_holds_on_all_three_counts():
    assert kinds(TOTAL) == {"discussion_change": "ok", "discussion_figurea": "ok", "discussion_figureb": "ok"}
    assert {c.status for c in run(TOTAL + " " + REVENUE)} == {"ok"} and len(run(TOTAL + " " + REVENUE)) == 6


def test_a_percentage_that_does_not_follow_from_the_figures_differs_and_the_figures_still_hold():
    got = kinds(TOTAL.replace("11.48%", "13.48%"))
    assert got["discussion_change"] == "differs" and got["discussion_figurea"] == got["discussion_figureb"] == "ok"


def test_the_rounding_of_the_percentage_and_of_the_figures_is_allowed_and_no_more():
    assert kinds(TOTAL.replace("11.48%", "11.47%"))["discussion_change"] == "ok"  # 1020 / 915 is 11.475
    assert kinds(TOTAL.replace("11.48%", "11.40%"))["discussion_change"] == "differs"


def test_a_direction_word_that_disagrees_with_the_figures_differs():
    got = {c.id.split(":")[0]: c for c in run(TOTAL.replace("increased", "decreased"))}
    assert got["discussion_change"].status == "differs" and got["discussion_change"].left[0].value < 0


def test_a_figure_that_is_not_the_statements_differs_even_when_the_percentage_fits_it():
    text = "Our total income increased by 11.59% to ₹ 1,021.00 million in Fiscal 2026 from ₹ 915.00 million in Fiscal 2025."  # 1021 / 915 is 11.585
    got = kinds(text)
    assert got["discussion_change"] == "ok" and got["discussion_figureb"] == "differs" and got["discussion_figurea"] == "ok"


def test_the_sentence_may_name_the_year_first_and_may_state_a_compound_rate():
    from_to = "Our total income increased by 11.48% from ₹ 915.00 million in Fiscal 2025 to ₹ 1,020.00 million in Fiscal 2026."
    assert {c.status for c in run(from_to)} == {"ok"}
    cagr = "Our revenue from operations grew at a CAGR of 11.11% to ₹ 1,000.00 million in Fiscal 2026 from ₹ 900.00 million in Fiscal 2025."
    assert [c.id.split(":")[0] for c in run(cagr)][0] == "discussion_cagr" and {c.status for c in run(cagr)} == {"ok"}
    over_two = "Our total income grew at a CAGR of 10.00% to ₹ 1,210.00 million in Fiscal 2026 from ₹ 1,000.00 million in Fiscal 2024."
    assert next(c for c in run(over_two) if c.id.startswith("discussion_cagr")).status == "ok"  # (1210 / 1000) ** (1 / 2) - 1
    assert next(c for c in run(over_two.replace("10.00%", "21.00%")) if c.id.startswith("discussion_cagr")).status == "differs"


def test_a_part_of_the_business_is_checked_for_its_percentage_but_its_figures_are_not_taken_for_the_statements():
    part = "Our revenue from operations from our QSR vertical increased by 40.00% to ₹ 700.00 million in Fiscal 2026 from ₹ 500.00 million in Fiscal 2025."
    assert [c.id.split(":")[0] for c in run(part)] == ["discussion_change"] and run(part)[0].status == "ok"
    assert [c.status for c in run(part.replace("40.00%", "30.00%"))] == ["differs"]


def test_a_subject_is_the_name_nearest_the_verb_and_a_heading_before_it_is_not_part_of_the_sentence():
    text = "Revenue from operations Our total income increased by 11.48% to ₹ 1,020.00 million in Fiscal 2026 from ₹ 915.00 million in Fiscal 2025."
    assert kinds(text) == {"discussion_change": "ok", "discussion_figurea": "ok", "discussion_figureb": "ok"}


def test_units_that_differ_and_lines_that_were_not_read_are_not_run_with_the_reason():
    lakhs = TOTAL.replace("million", "lakhs")
    figures = [c for c in run(lakhs) if c.id.startswith("discussion_figure")]
    assert {c.status for c in figures} == {"not_run"} and "lakh" in figures[0].reason
    missing = [c for c in run(TOTAL, INCOME.replace("PROFIT FOR THE YEAR (A) 92.00 80.00\n", "")) if c.id.startswith("discussion_figure")]
    assert {c.status for c in missing} == {"ok"}  # total income is still there
    pat = [c for c in run("Our profit for the year increased by 15.00% to ₹ 92.00 million in Fiscal 2026 from ₹ 80.00 million in Fiscal 2025.", INCOME.replace("PROFIT FOR THE YEAR (A) 92.00 80.00\n", "")) if c.id.startswith("discussion_figure")]
    assert {c.status for c in pat} == {"not_run"} and "no restated income statement" in pat[0].reason


def test_a_sentence_in_another_form_is_not_read_and_one_repeated_is_read_once():
    assert list(sentences([(5, "mdna", "Our total income was ₹ 1,020.00 million in Fiscal 2026.")])) == []
    assert len(list(sentences([(5, "mdna", TOTAL), (6, "mdna", TOTAL)]))) == 1
    assert list(sentences([(5, "risk_factors", TOTAL)])) == []


TABLE = """Key financial metrics
Particulars Fiscal 2026 Fiscal 2025
Revenue from Operations (1) ₹ in million 1,000.00 900.00
Total Income (3) ₹ in million 1,020.00 915.00
Revenue from operations from our QSR vertical 600.00 500.00
Profit After Tax margin (PAT)(5) % 9.02 8.74
Add: Profit for the year 92.00 80.00
"""


def test_a_key_figure_table_is_checked_line_by_line_against_the_statements_and_only_whole_lines_are_taken():
    got = {c.id: c.status for c in run(TABLE)}
    assert got == {f"discussion_table:5:{k}:{y}": "ok" for k in ("revenue", "total_income") for y in (2026, 2025)}  # no vertical, no margin, no line with a lead-in
    wrong = {c.id: c.status for c in run(TABLE.replace("1,020.00 915.00", "1,020.00 925.00"))}
    assert wrong["discussion_table:5:total_income:2025"] == "differs" and wrong["discussion_table:5:total_income:2026"] == "ok"


def test_the_checks_are_part_of_the_document_checks_and_no_longer_listed_as_not_built():
    result = run_checks([(5, "mdna", TOTAL), (9, "financials", INCOME)], None)
    assert sum(1 for c in result["checks"] if c["id"].startswith("discussion_")) == 3
    assert "restated figures against the management discussion" not in NOT_BUILT
    assert any("other than a stated change" in item for item in NOT_BUILT)  # what is left is said, not hidden
