from dataclasses import replace
from pathlib import Path

import pytest

from athena.valuation import dcf

GINZU = Path(".athena/damodaran/site/pc/fcffsimpleginzu.xlsx")

# the sample company in fcffsimpleginzu.xlsx ('Input sheet'), with the workbook's saved results as the expected values
ALMARAI = dcf.DcfInputs(
    revenue=21765.4, growth_year1=0.05, growth_years_2_5=0.05, margin_year1=3060.9 / 21765.4, target_margin=3060.9 / 21765.4, margin_convergence_year=5,
    sales_to_capital_1_5=1.708537671031893, sales_to_capital_6_10=1.708537671031893, effective_tax_rate=0.175, marginal_tax_rate=0.25,
    cost_of_capital=0.0705501574064654, riskfree_rate=0.0458, mature_equity_premium=0.0423, debt=45063.0, cash=19000.0, shares=4315.0,
    minority_interests=1558.0, non_operating_assets=21119.0,
)


def test_the_sample_company_reproduces_the_workbook_year_by_year_and_down_to_the_value_per_share():
    r = dcf.run_fcff(ALMARAI)
    assert [round(y.revenue, 2) for y in r.years[:3]] == [22853.67, 23996.35, 25196.17]
    assert r.years[5].growth == pytest.approx(0.04916) and r.years[9].growth == pytest.approx(0.0458)  # growth fades to the terminal rate by year 10
    assert [y.tax_rate for y in r.years][:6] == pytest.approx([0.175] * 5 + [0.19]) and r.years[9].tax_rate == pytest.approx(0.25)
    assert r.years[0].fcff == pytest.approx(1982.69672, abs=1e-4) and r.years[1].fcff == pytest.approx(2081.83156, abs=1e-4) and r.years[9].fcff == pytest.approx(2755.7086, abs=1e-3)
    assert r.years[0].reinvestment == pytest.approx(668.8079, abs=1e-4)
    assert r.years[9].cost_of_capital == pytest.approx(0.0881) and r.terminal_cost_of_capital == pytest.approx(0.0881)
    assert r.years[0].pv == pytest.approx(1852.03534, abs=1e-4)
    assert r.terminal.fcff == pytest.approx(1855.22803, abs=1e-3) and r.terminal.reinvestment == pytest.approx(2008.7339, abs=1e-3)
    assert r.terminal_value == pytest.approx(43858.81873, abs=1e-2)
    assert (r.pv_cash_flows, r.pv_terminal) == (pytest.approx(16394.53893, abs=1e-3), pytest.approx(21122.99184, abs=1e-3))
    assert r.operating_assets == pytest.approx(37517.53077, abs=1e-3)
    assert r.equity_value == pytest.approx(31015.53077, abs=1e-3)
    assert r.value_per_share == pytest.approx(7.18784, abs=1e-5)


def test_margin_follows_the_workbook_formula_to_its_target_and_stays_there():
    # his formula: target - (target - year-1 margin) / convergence year x (convergence year - n), with year 1 itself set to the year-1 margin; so the first step is the
    # larger one (0.05 to 0.09) and the steps after it are equal (0.02)
    r = dcf.run_fcff(replace(ALMARAI, margin_year1=0.05, target_margin=0.15, margin_convergence_year=5))
    assert [round(y.margin, 4) for y in r.years] == [0.05, 0.09, 0.11, 0.13, 0.15, 0.15, 0.15, 0.15, 0.15, 0.15]
    assert r.terminal.margin == pytest.approx(0.15)


def test_an_operating_loss_is_carried_forward_and_shelters_the_profit_that_follows():
    x = replace(ALMARAI, margin_year1=-0.10, target_margin=0.10, margin_convergence_year=3, effective_tax_rate=0.25)
    r = dcf.run_fcff(x)
    assert r.years[0].ebit < 0 and r.years[0].nopat == r.years[0].ebit  # a loss is not taxed and not reduced
    assert r.years[0].nol_end == pytest.approx(-r.years[0].ebit)
    first_profit = next(y for y in r.years if y.ebit > 0)
    assert first_profit.nopat > first_profit.ebit * 0.75  # taxed only on what exceeds the loss carried in
    carried = dcf.run_fcff(replace(ALMARAI, nol_start=1000.0, effective_tax_rate=0.25)).years[0]
    assert carried.nopat == pytest.approx(carried.ebit - (carried.ebit - 1000.0) * 0.25)


def test_a_failure_probability_blends_the_value_with_what_is_left_if_the_firm_fails():
    base = dcf.run_fcff(ALMARAI)
    failing = dcf.run_fcff(replace(ALMARAI, probability_of_failure=0.2, distress_proceeds=10000.0))
    assert failing.operating_assets == pytest.approx(base.operating_assets * 0.8 + 10000.0 * 0.2)
    assert failing.value_per_share < base.value_per_share


def test_terminal_growth_above_the_terminal_cost_of_capital_is_refused_not_computed():
    with pytest.raises(ValueError, match="terminal cost of capital"):
        dcf.run_fcff(replace(ALMARAI, terminal_growth=0.09))
    with pytest.raises(ValueError, match="convergence"):
        dcf.run_fcff(replace(ALMARAI, margin_convergence_year=11))
    with pytest.raises(ValueError, match="fraction"):
        dcf.run_fcff(replace(ALMARAI, probability_of_failure=1.5))


def test_the_growth_a_price_implies_gives_that_price_back_and_an_unreachable_price_gives_none():
    price = dcf.run_fcff(replace(ALMARAI, growth_year1=0.12, growth_years_2_5=0.12)).value_per_share
    g = dcf.reverse_growth(price, ALMARAI)
    assert g == pytest.approx(0.12, abs=1e-4)
    assert dcf.reverse_growth(1e9, ALMARAI) is None
    m = dcf.reverse_margin(dcf.run_fcff(replace(ALMARAI, target_margin=0.22)).value_per_share, ALMARAI)
    assert m == pytest.approx(0.22, abs=1e-4)


@pytest.mark.skipif(not GINZU.exists(), reason="the Damodaran mirror is not on this machine")
def test_the_expected_values_above_are_the_ones_saved_in_the_workbook():
    import warnings

    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sheet = openpyxl.load_workbook(GINZU, data_only=True)["Valuation output"]
    assert sheet["B33"].value == pytest.approx(7.18784, abs=1e-5) and sheet["B21"].value == pytest.approx(37517.53077, abs=1e-3)
    assert sheet["B18"].value == pytest.approx(43858.81873, abs=1e-2) and sheet["C9"].value == pytest.approx(1982.69672, abs=1e-4)


def test_the_implied_return_is_the_one_rate_at_which_the_cash_flows_are_worth_the_price():
    price = 9.0
    rate = dcf.implied_return(price, ALMARAI)
    assert rate is not None and 0.04 < rate < 0.12
    roic = dcf.run_fcff(ALMARAI).terminal_roic
    again = dcf.run_fcff(replace(ALMARAI, cost_of_capital=rate, terminal_cost_of_capital=rate, terminal_roic=roic))
    assert again.value_per_share == pytest.approx(price, abs=1e-4)
    assert dcf.implied_return(12.0, ALMARAI) < rate < dcf.implied_return(6.0, ALMARAI)  # a higher price earns a lower return
    assert dcf.implied_return(1e9, ALMARAI) is None and dcf.implied_return(-5.0, ALMARAI) is None  # outside what any rate can give
