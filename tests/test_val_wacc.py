from datetime import date

import pytest

from athena.reference.store import ReferenceStore
from athena.valuation import capital as cap
from athena.valuation.industry import IndustryContext
from athena.valuation.inputs import CompanyInputs
from athena.valuation.wacc import company_cost_of_capital

DAY = date(2026, 10, 22)


def reference() -> ReferenceStore:
    store = ReferenceStore(":memory:")
    store.put_input("us_10y_yield", date(2026, 10, 21), 0.0420, "test")
    store.put_input("tax_marginal", date(2026, 4, 1), 0.25, "test")
    store.put_input("inflation_inr", date(2026, 4, 1), 0.04, "test")
    store.put_input("inflation_usd", date(2026, 4, 1), 0.02, "test")
    store.put_input("erp_mature", date(2026, 7, 1), 0.042, "test")
    store.put_input("large_firm_cap_crore", date(2026, 4, 1), 40000.0, "test")
    store.put_country_premiums([
        ("India", "Asia", "Baa3", 0.0175, 0.0692, 0.0272, date(2026, 7, 1)),
        ("United States", "North America", "Aa1", 0.0022, 0.0442, 0.0022, date(2026, 7, 1)),
    ])
    return store


def industry(beta=0.9) -> IndustryContext:
    return IndustryContext("Steel", "nse_ticker", 1.0, 185, date(2026, 1, 5), {"unlevered_beta": beta} if beta is not None else {}, ())


def company(**over) -> CompanyInputs:
    base = dict(
        symbol="XYZ", basis="consolidated", layout="indas", period_end=date(2026, 6, 30), flow_basis="TTM", price=100.0, shares=1e8, revenue=5000.0, operating_profit=900.0,
        depreciation=300.0, ebit=600.0, interest=100.0, pbt=520.0, tax=130.0, net_profit=390.0, effective_tax_rate=0.25, revenue_cagr_3y=0.1, book_equity=3000.0,
        borrowings=1000.0, cash_and_bank=200.0, investments=50.0, minority=0.0, capex=300.0, market_cap=1000.0, invested_capital=3800.0, missing=(),
    )
    base.update(over)
    return CompanyInputs(**base)


def test_the_rupee_cost_of_capital_follows_the_dollar_steps_by_hand():
    with reference() as store:
        got = company_cost_of_capital(store, company(), industry(), DAY)
    assert got.ok and got.firm_kind == "small" and got.industry == "Steel"
    rf_usd = 0.0420 - 0.0022
    assert got.riskfree_usd == pytest.approx(rf_usd)
    assert got.interest_cover == pytest.approx(6.0) and got.rating == "A2/A"  # 600 / 100 = 6.0 starts the smaller-firm A2/A band
    kd_usd = rf_usd + 0.007751 + 0.0175
    debt = cap.market_value_of_debt(1000.0, 100.0, 3.0, kd_usd)
    beta = 0.9 * (1 + 0.75 * debt / 1000.0)
    coe_usd = rf_usd + beta * 0.0692
    assert got.levered_beta == pytest.approx(beta) and got.usd.cost_of_equity == pytest.approx(coe_usd)
    assert got.inr.cost_of_equity == pytest.approx(1.04 / 1.02 * (1 + coe_usd) - 1)
    we = 1000.0 / (1000.0 + debt)
    expected = we * got.inr.cost_of_equity + (1 - we) * got.inr.pre_tax_cost_of_debt * 0.75
    assert got.inr.cost_of_capital == pytest.approx(expected)
    assert got.low_inr.cost_of_capital < got.inr.cost_of_capital < got.high_inr.cost_of_capital
    assert got.riskfree_inr == pytest.approx(1.04 / 1.02 * (1 + rf_usd) - 1)
    assert any("US 10-year yield 0.0420" in line for line in got.assumptions) and any("leases are not in the stored borrowings" in line for line in got.assumptions)


def test_a_large_firm_a_lender_and_a_loss_maker_are_each_treated_by_their_own_rule():
    with reference() as store:
        large = company_cost_of_capital(store, company(market_cap=90000.0), industry(), DAY)
        lender = company_cost_of_capital(store, company(layout="bank"), industry(), DAY)
        loss = company_cost_of_capital(store, company(ebit=-50.0), industry(), DAY)
    assert large.firm_kind == "large" and large.rating == "A1/A+"  # cover 6.0 sits in 5.5 to 6.5 for a large manufacturer
    assert lender.firm_kind == "financial" and lender.rating == "Aaa/AAA"  # 6.0 times cover is far above a lender's top band of 3
    assert loss.rating == "Ba2/BB" and any("going concern" in f for f in loss.flags)


def test_anything_missing_is_named_and_no_cost_of_capital_is_given():
    with reference() as store:
        no_beta = company_cost_of_capital(store, company(), industry(beta=None), DAY)
        no_price = company_cost_of_capital(store, company(price=None, market_cap=None), industry(), DAY)
        no_industry = company_cost_of_capital(store, company(), None, DAY)
        before_any_input = company_cost_of_capital(store, company(), industry(), date(2026, 1, 1))
    assert not no_beta.ok and no_beta.missing == ("industry unlevered beta",) and no_beta.inr is None
    assert no_price.missing == ("company market_cap", "company price")
    assert not no_industry.ok and "industry unlevered beta" in no_industry.missing
    assert not before_any_input.ok and "input us_10y_yield" in before_any_input.missing and "country premium for India" in before_any_input.missing
