from datetime import date

import pytest

from athena.reference.store import ReferenceStore
from athena.valuation import scenarios as sc
from athena.valuation.industry import IndustryContext
from athena.valuation.inputs import CompanyInputs
from athena.valuation.wacc import company_cost_of_capital

DAY = date(2026, 10, 22)


def reference() -> ReferenceStore:
    store = ReferenceStore(":memory:")
    for key, value in (("us_10y_yield", 0.042), ("tax_marginal", 0.25), ("inflation_inr", 0.04), ("inflation_usd", 0.02), ("erp_mature", 0.042), ("large_firm_cap_crore", 40000.0)):
        store.put_input(key, date(2026, 4, 1), value, "test")
    store.put_country_premiums([("India", "Asia", "Baa3", 0.0175, 0.0692, 0.0272, date(2026, 7, 1)), ("United States", "North America", "Aa1", 0.0022, 0.0442, 0.0022, date(2026, 7, 1))])
    return store


def industry(**values) -> IndustryContext:
    base = {"unlevered_beta": 0.9, "beta": 1.1, "operating_margin": 0.10, "expected_revenue_growth_5y": 0.10, "sales_to_capital": 1.5, "roe": 0.13, "fundamental_growth": 0.10}
    base.update(values)
    return IndustryContext("Steel", "nse_ticker", 1.0, 185, date(2026, 1, 5), {k: v for k, v in base.items() if v is not None}, ())


def company(**over) -> CompanyInputs:
    base = dict(
        symbol="XYZ", basis="consolidated", layout="indas", period_end=date(2026, 6, 30), flow_basis="TTM", price=100.0, shares=1e8, revenue=5000.0, operating_profit=900.0,
        depreciation=300.0, ebit=600.0, interest=100.0, pbt=520.0, tax=130.0, net_profit=390.0, effective_tax_rate=0.25, revenue_cagr_3y=0.10, book_equity=3000.0,
        borrowings=1000.0, cash_and_bank=200.0, investments=50.0, minority=0.0, capex=300.0, market_cap=1000.0, invested_capital=3800.0, missing=(),
        roe_3y_average=0.14, book_growth_3y=0.12, book_value_per_share=300.0,
    )
    base.update(over)
    return CompanyInputs(**base)


NO_INDUSTRY = object()


def value(c=None, i=None, **kw):
    with reference() as store:
        c = c or company(**kw)
        i = industry() if i is None else (None if i is NO_INDUSTRY else i)
        cap = company_cost_of_capital(store, c, i, DAY)
        return sc.value_company(store, c, i, cap, DAY)


def test_an_operating_company_gets_a_bear_base_and_bull_value_in_that_order_with_every_rule_written_down():
    got = value()
    assert got.ok and got.model == "fcff"
    names = [s.name for s in got.scenarios]
    values = [s.value_per_share for s in got.scenarios]
    assert names == ["bear", "base", "bull"] and values == sorted(values)
    assert (got.low, got.base, got.high) == (values[0], values[1], values[2])
    base = got.scenarios[1]
    assert base.growth == pytest.approx((0.10 + (1.10 * 1.04 / 1.02 - 1)) / 2)  # the company's three-year growth and the industry's, the latter turned into rupees
    assert base.target_margin == pytest.approx((600 / 5000 + 0.10) / 2)
    text = " ".join(got.assumptions)
    assert "revenue growth" in text and "sales to capital 1.41" in text and "US 10-year yield" in text and any("business mix" in f for f in got.flags)


def test_the_price_the_value_would_need_is_solved_for_and_a_grid_shows_growth_against_margin():
    first = value()
    price = (first.base + first.high) / 2  # between the base and the bull value
    got = value(price=price)
    assert got.implied_growth is not None and got.implied_margin is not None
    assert got.implied_growth > got.scenarios[1].growth  # the market is paying for more growth than the base case has
    assert len(got.grid_growth) == 5 and len(got.grid_margin) == 5 and len(got.grid_values) == 5
    assert got.grid_values[2][2] == pytest.approx(got.base)  # the middle of the grid is the base case
    assert got.grid_values[4][4] > got.grid_values[0][0]
    cheaper = value(price=(first.low + first.base) / 2)
    assert cheaper.implied_growth < got.implied_growth  # a lower price implies less growth


def test_a_loss_maker_is_valued_on_a_move_to_the_industry_margin_and_flagged():
    got = value(ebit=-200.0, operating_profit=100.0)
    assert got.ok and any("operating loss" in f for f in got.flags)
    assert got.scenarios[1].target_margin == pytest.approx(0.10)


def test_what_cannot_be_valued_says_why_and_a_lender_or_insurer_is_routed_to_its_own_rule():
    assert not value(revenue=None).ok and "company revenue" in value(revenue=None).missing[0]
    assert not value(i=NO_INDUSTRY).ok
    no_growth = value(c=company(revenue_cagr_3y=None), i=industry(expected_revenue_growth_5y=None))
    assert not no_growth.ok and "no revenue growth" in no_growth.missing[0]
    insurer = value(layout="insurer")
    assert not insurer.ok and insurer.model == "unsupported"
    bank = value(layout="bank")
    assert bank.ok and bank.model == "excess_return" and bank.justified_price_to_book is not None
    assert bank.low <= bank.base <= bank.high and "a lender's debt is its raw material" in " ".join(bank.flags)


def test_the_bank_cases_keep_their_order_whether_it_earns_above_or_below_its_cost_of_equity():
    for roe, industry_roe in ((0.30, 0.25), (0.06, 0.05)):
        got = value(layout="bank", roe_3y_average=roe, i=industry(roe=industry_roe))
        assert got.low <= got.base <= got.high, (roe, got.low, got.base, got.high)
