from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from athena.contracts import AthenaError
from athena.documents.sections import Section
from athena.documents.store import DocStore
from athena.forensic import compare as cmp
from athena.forensic.compare import compare, sentences

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)

BALANCE = """RESTATED STATEMENT OF ASSETS AND LIABILITIES
(All amounts are in {unit})
Particulars As at March 31,
{y1}
As at March 31,
{y0}
TOTAL NON-CURRENT ASSETS (A) 200.00 160.00
TOTAL CURRENT ASSETS (B) 100.00 90.00
TOTAL ASSETS (A+B) {assets} 250.00
Equity share capital 40.00 40.00
Other equity 160.00 120.00
TOTAL EQUITY (A) 200.00 160.00
TOTAL NON-CURRENT LIABILITIES (B) 60.00 50.00
TOTAL CURRENT LIABILITIES (C) 40.00 40.00
TOTAL EQUITY & LIABILITIES (A+B+C) 300.00 250.00
"""

INCOME = """RESTATED STATEMENT OF PROFIT AND LOSS
(All amounts are in {unit})
Particulars For the year ended
31st March, {y1}
For the year ended
31st March, {y0}
Revenue from operations {rev} 900.00
Other income 20.00 15.00
TOTAL INCOME {income} 915.00
TOTAL EXPENSES 900.00 810.00
PROFIT BEFORE TAX 120.00 105.00
Current tax 30.00 26.00
Deferred tax (2.00) 1.00
PROFIT FOR THE YEAR (A) 92.00 80.00
"""

RISK_SAME = "Our business depends on a single manufacturing facility, and a disruption there would stop production for an extended period."
RISK_NUMBER = "Our top customer accounted for {pct} of our revenue from operations in Fiscal 2026 and the loss of that customer would hurt us."
RISK_GONE = "We have pledged shares of our promoters with a lender and a default on that loan could change who controls the company."
RISK_NEW = "A recent change in the regulations on export incentives could reduce the margin that we earn on sales outside India."


def make(store: DocStore, doc_id: str, *, kind: str, risk: list[str], pages_extra: list[str] | None = None, **vals) -> str:  # noqa: ANN003
    v = {"unit": "Rs. Millions", "y1": "2026", "y0": "2025", "assets": "300.00", "rev": "1,000.00", "income": "1,020.00", **vals}
    front = ["cover of the document", *(pages_extra or [])]  # extra pages in front move every later page, as a longer version does
    pages = [*front, " ".join(risk), BALANCE.format(**v), INCOME.format(**v)]
    n_front = len(front)
    sections = [Section("front", "Cover", 1, n_front), Section("risk_factors", "Risk factors", n_front + 1, n_front + 1), Section("financials", "Restated financial information", n_front + 2, n_front + 3)]
    by = {n: ("front" if n <= n_front else "risk_factors" if n == n_front + 1 else "financials") for n in range(1, n_front + 4)}
    store.add(doc_id, kind=kind, company="Demo Limited", url=f"https://www.sebi.gov.in/{doc_id}.pdf", sha256=doc_id, published=date(2026, 9, 1), pages=pages, unreadable=[], sections=sections, section_of=lambda secs, n: by[n], now=NOW)
    return doc_id


@pytest.fixture()
def store(tmp_path):  # noqa: ANN001, ANN201
    return DocStore(tmp_path / "d.duckdb")


def test_a_document_compared_with_itself_has_nothing_to_report(store) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME, RISK_GONE])
    out = compare(store, "a", "a")
    assert out["schema"] == "athena.forensic.compare/1" and out["statements"]["changed_count"] == 0 and out["statements"]["same"] > 10
    assert out["wording"]["numbers_changed_count"] == out["wording"]["only_in_earlier_count"] == out["wording"]["only_in_later_count"] == 0
    assert out["checks"]["new_differences"] == [] and out["checks"]["resolved_differences"] == [] and out["checks"]["held_in_both"] > 3
    json.dumps(out, allow_nan=False)


def test_a_restated_figure_that_changed_is_listed_and_a_rounding_is_not(store) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME])
    make(store, "b", kind="RHP", risk=[RISK_SAME], income="1,030.00", rev="1,010.00")
    changed = compare(store, "a", "b")["statements"]["changed"]
    assert {(c["statement"], c["line"], c["year"], c["earlier"], c["later"]) for c in changed} >= {("income", "total_income", 2026, "1,020.00", "1,030.00"), ("income", "revenue", 2026, "1,000.00", "1,010.00")}
    assert all(c["year"] != 2025 for c in changed)  # the earlier year was not touched
    make(store, "c", kind="RHP", risk=[RISK_SAME], income="1,020.004")
    assert compare(store, "a", "c")["statements"]["changed_count"] == 0  # a thousandth is inside the rounding of the printed two decimals plus the three


def test_a_year_that_one_version_adds_and_one_it_drops_are_named_and_a_different_unit_is_not_compared(store) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME])
    make(store, "b", kind="RHP", risk=[RISK_SAME], y1="2027", y0="2026")
    out = compare(store, "a", "b")["statements"]
    assert "income unspecified FY2027" in out["years_added"] and "income unspecified FY2025" in out["years_dropped"]
    assert out["same"] > 0 or out["changed_count"] > 0  # FY2026 is in both
    make(store, "c", kind="RHP", risk=[RISK_SAME], unit="Rs. Lakhs")
    lakhs = compare(store, "a", "c")["statements"]
    assert lakhs["changed_count"] == 0 and any("different units" in n for n in lakhs["not_compared"])


def test_offer_figures_say_set_changed_added_and_removed_and_a_blank_that_was_set_is_not_a_change(store) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME])
    make(store, "b", kind="RHP", risk=[RISK_SAME])
    store.save_figures("a", {"offer_total": {"printed": "[●]", "crore": None, "page": 5}, "fresh_issue": {"printed": "800.00", "crore": 800.0, "page": 5}, "offer_for_sale": {"printed": "200.00", "crore": 200.0, "page": 5},
                             "pre_offer_shares": 1000000, "objects": [{"object": "repay borrowings", "printed": "300.00", "crore": 300.0}, {"object": "working capital", "printed": "[●]", "crore": None}]}, NOW)
    store.save_figures("b", {"offer_total": {"printed": "1,000.00", "crore": 1000.0, "page": 5}, "fresh_issue": {"printed": "850.00", "crore": 850.0, "page": 5}, "offer_for_sale": {"printed": "200.00", "crore": 200.0, "page": 5},
                             "pre_offer_shares": 1000000, "objects": [{"object": "repay borrowings", "printed": "300.00", "crore": 300.0}, {"object": "general corporate purposes", "printed": "50.00", "crore": 50.0}]}, NOW)
    rows = {r["item"]: r for r in compare(store, "a", "b")["figures"]}
    assert rows["size of the offer (crore)"]["status"] == "set" and rows["fresh issue (crore)"]["status"] == "changed" and rows["offer for sale (crore)"]["status"] == "same"
    assert rows["shares before the offer"]["status"] == "same" and rows["object: repay borrowings"]["status"] == "same"
    assert rows["object: working capital"]["status"] == "removed" and rows["object: general corporate purposes"]["status"] == "added"


def test_a_risk_sentence_with_other_numbers_is_a_change_of_numbers_and_the_others_are_added_or_removed(store) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME, RISK_NUMBER.format(pct="61.00%"), RISK_GONE])
    make(store, "b", kind="RHP", risk=[RISK_SAME, RISK_NUMBER.format(pct="48.50%"), RISK_NEW])
    wording = compare(store, "a", "b")["wording"]
    assert wording["same"] == 1 and wording["numbers_changed_count"] == 1
    assert "61.00%" in wording["numbers_changed"][0]["before"] and "48.50%" in wording["numbers_changed"][0]["after"]
    assert [s[:21] for s in wording["only_in_earlier"]] == ["We have pledged share"] and [s[:20] for s in wording["only_in_later"]] == ["A recent change in t"]
    assert all(len(s) >= cmp.MIN_SENTENCE for s in sentences(store, "a").values())  # a heading or a table row is not a claim


def test_a_check_that_holds_in_the_earlier_version_and_differs_in_the_later_is_a_new_difference_whatever_page_it_is_on(store) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME])
    make(store, "b", kind="RHP", risk=[RISK_SAME], pages_extra=["an extra page of notes that moves nothing"], assets="305.00")
    checks = compare(store, "a", "b")["checks"]
    both = {"total assets equal total equity and liabilities", "total assets are non-current plus current assets"}  # 305 breaks the two relations it is in; the page moved from 3 to 4 and that changes nothing
    assert {c["title"].split(",")[0] for c in checks["new_differences"]} == both
    reverse = compare(store, "b", "a")["checks"]
    assert reverse["new_differences"] == [] and {t.split(",")[0] for t in reverse["resolved_differences"]} == both


def test_a_document_that_is_not_held_is_refused_and_the_command_says_so(store, tmp_path, capsys) -> None:  # noqa: ANN001
    make(store, "a", kind="DRHP", risk=[RISK_SAME])
    with pytest.raises(AthenaError, match="no document zzz"):
        compare(store, "a", "zzz")
    assert cmp.main(["--earlier", "a", "--later", "zzz", "--db", str(tmp_path / "d.duckdb")]) == 1
    assert "no document zzz" in capsys.readouterr().err
