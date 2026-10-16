from datetime import date

import pytest

from athena.fundamentals import ratios as r


def fy(year):
    return (date(year - 1, 4, 1), date(year, 3, 31))


def quarter(end):
    return (date(end.year, end.month - 2, 1), end)


def test_periods_are_sorted_into_years_and_quarters_by_length():
    income = {fy(2024): {"sales": 1.0}, quarter(date(2024, 3, 31)): {"sales": 2.0}, (date(2023, 4, 1), date(2023, 9, 30)): {"sales": 3.0}}
    assert list(r.annual(income)) == [date(2024, 3, 31)]
    assert list(r.quarterly(income)) == [date(2024, 3, 31)]  # the half year is neither


def test_ttm_sums_four_consecutive_quarters_and_refuses_gaps():
    ends = [date(2024, 3, 31), date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30)]
    quarters = {e: {"net_profit": 25.0} for e in ends}
    assert r.ttm(quarters, "net_profit") == 100.0
    assert r.ttm({e: quarters[e] for e in ends[:3]}, "net_profit") is None  # only three quarters
    skipped = {e: {"net_profit": 1.0} for e in (date(2024, 3, 31), date(2023, 12, 31), date(2023, 6, 30), date(2023, 3, 31))}
    assert r.ttm(skipped, "net_profit") is None  # September 2023 is missing: not twelve months
    assert r.ttm(quarters, "missing") is None


def test_compound_growth():
    assert r.cagr(200.0, 100.0, 5) == pytest.approx(14.8698, abs=1e-3)
    assert r.cagr(100.0, 0.0, 5) is None and r.cagr(-5.0, 100.0, 5) is None and r.cagr(None, 100.0, 5) is None
    annual = {date(2024, 3, 31): {"sales": 200.0}, date(2021, 3, 31): {"sales": 100.0}, date(2019, 3, 31): {"sales": 50.0}}
    assert r.compounded(annual, "sales", 3) == pytest.approx(r.cagr(200.0, 100.0, 3))
    assert r.compounded(annual, "sales", 5) == pytest.approx(r.cagr(200.0, 50.0, 5))
    assert r.compounded(annual, "sales", 10) is None  # not enough history is not a zero


INCOME = {"sales": 1000.0, "pbt": 200.0, "interest": 20.0, "net_profit": 150.0, "operating_profit": 260.0}
BALANCE = {
    "equity_capital": 100.0, "reserves": 400.0, "borrowings": 250.0, "total_assets": 1000.0, "current_liabilities": 300.0, "current_assets": 500.0,
    "receivables": 100.0, "inventory": 50.0, "payables": 80.0,
}
CASH = {"cfo": 220.0, "capex": 60.0, "fcf": 160.0}


def test_industrial_year_ratios():
    out = r.year_ratios("indas", INCOME, BALANCE, CASH)
    assert out["ebit"] == 220.0
    assert out["roce"] == pytest.approx(220 / 700 * 100)
    assert out["roe"] == pytest.approx(30.0)
    assert out["interest_cover"] == pytest.approx(11.0)
    assert out["debt_to_equity"] == pytest.approx(0.5)
    assert out["debtor_days"] == pytest.approx(36.5)
    assert out["inventory_days"] == pytest.approx(18.25)
    assert out["days_payable"] == pytest.approx(29.2)
    assert out["cash_conversion_cycle"] == pytest.approx(36.5 + 18.25 - 29.2)
    assert out["working_capital_days"] == pytest.approx(73.0)
    assert out["fcf"] == 160.0
    assert out["cfo_to_ebitda"] == pytest.approx(220 / 260 * 100)


def test_a_ratio_with_a_missing_input_is_absent_and_other_layouts_skip_what_does_not_apply():
    thin = r.year_ratios("indas", {"sales": 1000.0, "pbt": 200.0}, {"total_assets": 1000.0}, {})
    assert "roce" not in thin and "debtor_days" not in thin and thin["ebit"] == 200.0
    bank = r.year_ratios("bank", {"net_profit": 150.0}, {"equity_capital": 100.0, "reserves": 400.0, "advances": 800.0, "deposits": 1000.0}, {})
    assert bank == {"roe": pytest.approx(30.0), "credit_deposit": pytest.approx(80.0)}
    lender = r.year_ratios("finance", INCOME, BALANCE, CASH)
    assert "roce" not in lender and "debtor_days" not in lender and lender["roe"] == pytest.approx(30.0)


def test_market_ratios_use_ttm_earnings_when_four_quarters_exist():
    ends = [date(2024, 3, 31), date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30)]
    quarters = {e: {"net_profit": 25.0, "operating_profit": 40.0} for e in ends}
    balance = {"equity_capital": 100.0, "reserves": 400.0, "borrowings": 250.0, "cash": 30.0, "bank_balances": 20.0}
    out = r.market_ratios(100.0, 10.0, quarters, {}, balance)
    assert out["market_cap"] == 1000.0
    assert out["pe"] == pytest.approx(10.0) and out["pe_period"] == "TTM"
    assert out["book_value"] == pytest.approx(50.0) and out["pb"] == pytest.approx(2.0)
    assert out["ev"] == pytest.approx(1200.0) and out["ev_ebitda"] == pytest.approx(7.5)


def test_market_ratios_fall_back_to_the_latest_year_and_say_so():
    fy_income = {date(2024, 3, 31): {"net_profit": 80.0, "operating_profit": 100.0}}
    out = r.market_ratios(100.0, 10.0, {}, fy_income, {"equity_capital": 100.0, "reserves": 400.0})
    assert out["pe"] == pytest.approx(12.5) and out["pe_period"] == "FY2024"
    assert "ev" not in out  # no borrowings figure, so no enterprise value
    assert r.market_ratios(100.0, None, {}, fy_income, {}) == {}


def test_crore():
    assert r.crore(2408930000000.0) == 240893.0
