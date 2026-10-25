from datetime import date
from pathlib import Path

import pytest
from test_val_scenarios import DAY, company, industry, reference

from athena.valuation import distress
from athena.valuation import scenarios as sc
from athena.valuation.ratings import TABLES
from athena.valuation.wacc import company_cost_of_capital

WORKBOOK = Path(".athena/damodaran/site/pc/fcffsimpleginzu.xlsx")


def test_every_synthetic_rating_falls_in_one_row_of_the_table_and_worse_ratings_fail_more_often():
    seen = {}
    for table in TABLES.values():
        for _, rating, _ in table:
            seen[rating] = distress.bucket(rating)
    assert seen["Aaa/AAA"] == "AAA" and seen["Aa2/AA"] == "AA" and seen["A3/A-"] == "A" and seen["A1/A+"] == "A" and seen["Baa2/BBB"] == "BBB"
    assert seen["Ba1/BB+"] == "BB" and seen["Ba2/BB"] == "BB" and seen["B1/B+"] == "B" and seen["B3/B-"] == "B"
    assert seen["Caa/CCC"] == seen["Ca2/CC"] == seen["C2/C"] == seen["D2/D"] == "CCC/C"
    order = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC/C"]
    tens = [distress.BY_RATING[b][9] for b in order]
    assert tens == sorted(tens)
    for row in distress.BY_RATING.values():
        assert list(row) == sorted(row)  # a cumulative chance never falls with the horizon


def test_the_failure_chance_is_the_default_rate_for_the_horizon_and_a_horizon_outside_the_table_is_refused():
    assert distress.failure_probability("Ba2/BB") == pytest.approx(0.1178)
    assert distress.failure_probability("Ba2/BB", 5) == pytest.approx(0.0652)
    assert distress.failure_probability("D2/D", 1) == pytest.approx(0.2708)
    for horizon in (0, 11):
        with pytest.raises(ValueError):
            distress.failure_probability("Ba2/BB", horizon)


def test_proceeds_are_tied_to_the_going_concern_value_or_to_book_capital_and_nothing_when_there_is_no_book_capital():
    tied = distress.failure("B2/B", 1000.0)
    assert tied.probability == pytest.approx(0.2374) and tied.proceeds is None and tied.tie == "value" and tied.bucket == "B"
    assert "23.7%" in tied.sentence() and "50% of its going-concern value" in tied.sentence()
    got = distress.failure("B2/B", 1000.0, tie="book")
    assert got.proceeds == pytest.approx(500.0) and "Rs 500 cr" in got.sentence()
    assert distress.failure("B2/B", -50.0, tie="book").proceeds == 0.0 and distress.failure("B2/B", None, tie="book").proceeds == 0.0
    assert distress.failure("B2/B", 1000.0, share=0.0, tie="book").proceeds == 0.0
    with pytest.raises(ValueError):
        distress.failure("B2/B", 1000.0, share=1.5)
    with pytest.raises(ValueError):
        distress.failure("B2/B", 1000.0, tie="fair")


@pytest.mark.skipif(not WORKBOOK.exists(), reason="the Damodaran mirror is not on this machine")
def test_the_table_is_the_workbooks():
    import openpyxl

    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    try:
        rows = {row[0]: row[1:11] for row in book["Failure Rate worksheet"].iter_rows(min_row=6, max_row=12, values_only=True)}
    finally:
        book.close()
    assert set(rows) == set(distress.BY_RATING)
    for rating, values in rows.items():
        assert list(values) == pytest.approx(list(distress.BY_RATING[rating]), abs=1e-4)


def value(policy: sc.Policy, **kw):
    with reference() as store:
        c = company(**kw)
        cap = company_cost_of_capital(store, c, industry(), DAY)
        return cap, sc.value_company(store, c, industry(), cap, DAY, policy)


def test_the_valuation_blends_in_a_failure_value_that_lowers_the_value_by_chance_times_the_gap_and_says_so():
    on = sc.Policy(detail=False)
    off = sc.Policy(detail=False, failure=False)
    cap, with_failure = value(on)
    _, without = value(off)
    assert with_failure.ok and without.ok
    p = distress.failure_probability(cap.rating)
    assert p > 0 and with_failure.base < without.base  # a firm that is going concern in the DCF is worth less once failure is possible
    text = " ".join(with_failure.assumptions)
    assert "chance of failure" in text and f"{p:.1%}" in text and "going-concern value" in text
    assert "chance of failure" not in " ".join(without.assumptions)
    assert without.base - with_failure.base > 0


def test_zero_share_and_a_tiny_horizon_move_the_value_the_way_the_rule_says():
    cheap = value(sc.Policy(detail=False, failure_horizon=1))[1].base
    long = value(sc.Policy(detail=False, failure_horizon=10))[1].base
    assert long < cheap  # a longer horizon is a bigger chance of failing
    none = value(sc.Policy(detail=False, failure_share=0.0))[1].base
    half = value(sc.Policy(detail=False, failure_share=0.5))[1].base
    assert none < half  # recovering nothing is worse than recovering half


def test_an_operating_loss_is_flagged_with_the_row_it_was_given():
    _, got = value(sc.Policy(detail=False), ebit=-200.0, pbt=-300.0, net_profit=-300.0, tax=0.0, operating_profit=100.0, effective_tax_rate=None)
    if got.ok:
        assert any("real chance is likely higher" in f for f in got.flags)
        assert "11.8%" in " ".join(got.assumptions)  # the BB row, as the cost of capital treats an operating loss
