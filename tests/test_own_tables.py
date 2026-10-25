from datetime import date, datetime

import numpy as np
import pytest

from athena.reference.store import ReferenceStore, StatRow
from athena.valuation import own_tables as ot
from athena.valuation.inputs import CompanyInputs

FY = (date(2024, 4, 1), date(2025, 3, 31))
PREV_FY = (date(2023, 4, 1), date(2024, 3, 31))


def rows_for(symbol, filed, income, balance, basis="consolidated", period=FY, restated=None):
    """Statement-line rows `(symbol, basis, statement, start, end, key, value, filed)` for one annual filing."""
    out = [(symbol, basis, "income", period[0], period[1], k, v, filed) for k, v in income.items()]
    out += [(symbol, basis, "balance", period[1], period[1], k, v, filed) for k, v in balance.items()]
    return out


INCOME = {"sales": 1000e7, "operating_profit": 200e7, "depreciation": 50e7, "interest": 10e7, "pbt": 150e7, "tax": 30e7, "net_profit": 120e7}
BALANCE = {"equity_capital": 10e7, "reserves": 590e7, "borrowings": 100e7, "cash": 20e7, "bank_balances": 30e7, "investments": 0.0, "minority": 0.0}
COUNTS = [{"filed": datetime(2025, 5, 20), "declared": 10e7, "from_profit": None, "period_end": date(2025, 3, 31)}]


def test_a_filing_made_after_the_day_is_not_seen_and_a_restatement_replaces_the_earlier_figure_only_from_its_filing_day():
    first = rows_for("ABC", datetime(2025, 5, 20), INCOME, BALANCE)
    restated = rows_for("ABC", datetime(2025, 9, 1), {**INCOME, "net_profit": 100e7}, BALANCE)
    lines = ot.Lines(first + restated)
    assert lines.asof("ABC", "consolidated", "income", date(2025, 5, 19)) == {}
    assert lines.asof("ABC", "consolidated", "income", date(2025, 6, 1))[FY]["net_profit"] == 120e7
    assert lines.asof("ABC", "consolidated", "income", date(2025, 9, 1))[FY]["net_profit"] == 100e7
    assert lines.symbols() == {"ABC"}


def test_the_share_count_is_the_one_the_filings_stated_by_the_day():
    counts = [
        {"filed": datetime(2025, 5, 20), "declared": 20e7, "from_profit": None, "period_end": date(2025, 3, 31)},
        {"filed": datetime(2024, 5, 20), "declared": 10e7, "from_profit": None, "period_end": date(2024, 3, 31)},
    ]
    assert ot.shares_asof(counts, date(2025, 1, 1)) == 10e7  # the bonus was not yet declared
    assert ot.shares_asof(counts, date(2025, 6, 1)) == 20e7
    assert ot.shares_asof(counts, date(2020, 1, 1)) is None


def test_a_company_as_it_stood_on_a_day_uses_only_what_was_filed_and_the_price_and_shares_of_that_day():
    lines = ot.Lines(rows_for("ABC", datetime(2025, 5, 20), INCOME, BALANCE))
    assert ot.company_asof(lines, "ABC", "indas", COUNTS, 100.0, date(2025, 5, 1)) is None  # nothing filed yet
    got = ot.company_asof(lines, "ABC", "indas", COUNTS, 100.0, date(2025, 6, 1))
    assert got is not None and got.basis == "consolidated"
    assert got.market_cap == pytest.approx(100.0 * 10e7 / 1e7)  # 10 crore shares at Rs 100: Rs 1,000 crore
    assert got.revenue == pytest.approx(1000.0) and got.net_profit == pytest.approx(120.0) and got.ebit == pytest.approx(150.0)
    standalone = ot.Lines(rows_for("XYZ", datetime(2025, 5, 20), INCOME, BALANCE, basis="standalone"))
    assert ot.company_asof(standalone, "XYZ", "indas", COUNTS, 100.0, date(2025, 6, 1)).basis == "standalone"


def test_a_beta_is_the_slope_of_weekly_returns_on_the_markets():
    rng = np.random.default_rng(7)
    days = 700
    market = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, days))
    daily = market[1:] / market[:-1] - 1
    leveraged = 50 * np.cumprod(np.concatenate([[1.0], 1 + 1.5 * daily]))  # moves one and a half times the market every day, so every week too
    flat = np.full(days, 30.0)
    short = np.full(days, np.nan)
    short[-30:] = 40.0  # too little history
    got = ot.weekly_betas({"HI": leveraged, "FLAT": flat, "NEW": short}, market, days - 1)
    assert got["HI"] == pytest.approx(1.5, abs=0.15)
    assert got["FLAT"] == pytest.approx(0.0, abs=1e-9)
    assert "NEW" not in got
    assert ot.weekly_betas({"HI": leveraged}, market, 100) == {}  # a day too early to have two years: not enough weeks


def firm(symbol, **kw):
    base = dict(
        symbol=symbol, basis="consolidated", layout="indas", period_end=date(2025, 3, 31), flow_basis="FY2025", price=100.0, shares=1.0, revenue=1000.0, operating_profit=200.0, depreciation=50.0,
        ebit=150.0, interest=10.0, pbt=140.0, tax=35.0, net_profit=105.0, effective_tax_rate=0.25, revenue_cagr_3y=0.1, book_equity=600.0, borrowings=100.0, cash_and_bank=50.0, investments=0.0,
        minority=0.0, capex=20.0, market_cap=1800.0, invested_capital=650.0,
    )
    base.update(kw)
    return CompanyInputs(**base)


def test_an_industry_is_summed_the_way_damodaran_sums_it():
    a = firm("A")
    b = firm("B", revenue=500.0, operating_profit=50.0, ebit=20.0, pbt=10.0, tax=4.0, net_profit=-6.0, book_equity=200.0, borrowings=300.0, cash_and_bank=10.0, market_cap=600.0, invested_capital=490.0)
    got = ot.aggregate([a, b], {"A": 1.0, "B": 1.4}, lender=False)
    assert got["pe"].value == pytest.approx(1800.0 / 105.0) and got["pe"].n == 1  # only the money-making firm counts
    assert got["pb"].value == pytest.approx(2400.0 / 800.0) and got["pb"].n == 2
    assert got["roe"].value == pytest.approx((105.0 - 6.0) / 800.0)
    assert got["net_margin"].value == pytest.approx(99.0 / 1500.0)
    assert got["operating_margin"].value == pytest.approx(170.0 / 1500.0)
    assert got["sales_to_capital"].value == pytest.approx(1500.0 / 1140.0)
    assert got["tax_rate"].value == pytest.approx(39.0 / 150.0)
    assert got["debt_to_capital"].value == pytest.approx(400.0 / (400.0 + 2400.0))
    assert got["beta"].value == pytest.approx(1.2) and got["beta"].n == 2
    d_e = 400.0 / 2400.0
    unlevered = 1.2 / (1 + (1 - 39.0 / 150.0) * d_e)
    cash_share = 60.0 / (2400.0 + 400.0)
    assert got["unlevered_beta"].value == pytest.approx(unlevered / (1 - cash_share))


def test_a_lender_gets_only_the_measures_that_mean_something_for_it_and_a_missing_figure_is_absent_not_zero():
    bank = firm("BANK", revenue=None, ebit=None, operating_profit=None, borrowings=None, cash_and_bank=None, invested_capital=None, pbt=None, tax=None)
    got = ot.aggregate([bank], {}, lender=True)
    assert set(got) == {"pe", "pb", "roe"}  # no beta for want of a regression, no margins for a lender
    assert set(ot.aggregate([bank], {}, lender=False)) == {"pe", "pb", "roe"}  # and a firm with no revenue or debt figures has no margins either
    assert set(ot.aggregate([firm("Z", market_cap=None)], {}, lender=False)) == {"tax_rate"}  # no price: only the measure that needs none


def test_the_rows_use_damodarans_dataset_and_metric_names_so_a_reader_can_switch_the_source():
    rows = ot.build_tables(date(2026, 1, 5), {"Steel": [firm("A")], "Bank (Money Center)": [firm("B")]}, {"A": 1.0, "B": 1.0})
    by = {(r.industry, f"{r.dataset}.{r.metric}"): r for r in rows}
    assert by[("Steel", "pbv.pbv")].source == "own" and by[("Steel", "pbv.pbv")].as_of == date(2026, 1, 5)
    assert ("Steel", "margin.net_margin") in by and ("Bank (Money Center)", "margin.net_margin") not in by  # a lender has no margins
    assert ("Bank (Money Center)", "roe.roe_unadjusted") in by
    from athena.valuation.industry import FIELDS

    assert set(ot.NAMES.values()) <= set(FIELDS.values())  # every name is one the valuation code already reads


def test_the_divergence_report_sets_our_tables_against_his_by_industry_and_measure():
    with ReferenceStore(":memory:") as ref:
        day = date(2026, 1, 5)
        his = [StatRow("damodaran", "India", "pbv", "Steel", "pbv", 2.0, 40, day, "f"), StatRow("damodaran", "India", "pbv", "Cement", "pbv", 3.0, 12, day, "f"),
               StatRow("damodaran", "India", "roe", "Steel", "roe_unadjusted", 0.10, 40, day, "f")]
        ours = [StatRow("own", "India", "pbv", "Steel", "pbv", 2.2, 35, day, "o"), StatRow("own", "India", "pbv", "Cement", "pbv", 1.5, 11, day, "o"),
                StatRow("own", "India", "roe", "Steel", "roe_unadjusted", 0.30, 35, day, "o"), StatRow("own", "India", "pe", "Glass", "x", 9.0, 3, day, "o")]
        ref.put_stats(his + ours)
        gaps = ot.divergence(ref, day)
    by = {(g.industry, g.measure): g for g in gaps}
    assert set(by) == {("Steel", "pb"), ("Cement", "pb"), ("Steel", "roe")}  # only what both have
    assert by[("Steel", "pb")].ratio == pytest.approx(1.1) and by[("Cement", "pb")].ratio == pytest.approx(0.5)
    summary = ot.summarize(gaps)
    assert summary["pb"]["industries"] == 2 and summary["pb"]["within_band"] == 0.5 and summary["roe"]["median_ratio"] == pytest.approx(3.0)
    text = ot.report(gaps, day, enough=10)
    assert "| pb | 2 |" in text and "Cement" in text and "within" in text


def test_a_filing_in_the_wrong_unit_is_set_aside_and_two_listings_of_one_company_count_once():
    sane = firm("A")
    wrong_unit = firm("WRONG", revenue=503151.0, net_profit=-5051571.0, market_cap=51.0, book_equity=40.0)
    assert ot.misscaled(wrong_unit) and not ot.misscaled(sane)
    assert not ot.misscaled(firm("EMPTY", market_cap=None, book_equity=None, revenue=None, net_profit=None))  # nothing to compare
    ordinary = firm("FEL", market_cap=23.0, revenue=2193.0, net_profit=-2877.0, book_equity=900.0, borrowings=100.0)
    dvr = firm("FELDVR", market_cap=158.0, revenue=2193.0, net_profit=-2877.0, book_equity=900.0, borrowings=100.0)
    firms, aside, merged = ot.prepare([sane, wrong_unit, ordinary, dvr])
    assert aside == 1 and merged == 1 and len(firms) == 2
    both = next(f for f in firms if f.symbol == "FEL")
    assert both.market_cap == pytest.approx(181.0) and both.revenue == 2193.0  # market values added, statements once
    rows = ot.build_tables(date(2026, 1, 5), {"Apparel": [sane, wrong_unit, ordinary, dvr]}, {})
    assert "1 set aside as misreported" in rows[0].file and "1 share classes merged" in rows[0].file
    net = next(r for r in rows if r.metric == "net_margin")
    assert net.value == pytest.approx((105.0 - 2877.0) / (1000.0 + 2193.0))  # the wrong-unit firm is in no sum
