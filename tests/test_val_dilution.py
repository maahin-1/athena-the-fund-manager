from pathlib import Path

import pytest

from athena.valuation import dilution

WORKBOOK = Path(".athena/damodaran/site/pc/employeeoption.xls")


def test_the_workbooks_sample_company_is_reproduced():
    got = dilution.option_claim(price=20.83, strike=22.68, years=2.53, volatility=0.272, dividend_yield=0.015, riskfree=0.002, options=520.0, shares=5330.0, tax_rate=0.26)
    assert got.adjusted_price == pytest.approx(19.13946262651833, abs=1e-6)
    assert got.per_option == pytest.approx(1.8114545483312297, abs=1e-6)
    assert got.total == pytest.approx(941.9563651322394, abs=1e-3)
    assert got.after_tax == pytest.approx(697.0477101978571, abs=1e-3)


def test_vesting_scales_the_claim_and_no_options_is_no_claim():
    full = dilution.option_claim(20.83, 22.68, 2.53, 0.272, 0.015, 0.002, 520.0, 5330.0)
    half = dilution.option_claim(20.83, 22.68, 2.53, 0.272, 0.015, 0.002, 520.0, 5330.0, vested=0.5)
    assert half.total == pytest.approx(full.total * 0.5)
    none = dilution.option_claim(20.83, 22.68, 2.53, 0.272, 0.015, 0.002, 0.0, 5330.0)
    assert none.total == 0.0 and none.after_tax == 0.0 and none.adjusted_price == 20.83


def test_dilution_makes_an_option_worth_less_than_the_plain_black_scholes_value():
    plain = dilution.black_scholes_call(20.83, 22.68, 2.53, 0.272, 0.015, 0.002)
    diluted = dilution.option_claim(20.83, 22.68, 2.53, 0.272, 0.015, 0.002, 520.0, 5330.0).per_option
    assert 0 < diluted < plain


def test_a_call_is_worth_at_least_its_intrinsic_value_and_an_expired_one_exactly_that():
    assert dilution.black_scholes_call(120.0, 100.0, 0.0, 0.3, 0.0, 0.05) == 20.0
    assert dilution.black_scholes_call(80.0, 100.0, 0.0, 0.3, 0.0, 0.05) == 0.0
    assert dilution.black_scholes_call(120.0, 100.0, 2.0, 0.3, 0.0, 0.05) > 20.0


def test_nonsense_is_refused():
    with pytest.raises(ValueError):
        dilution.option_claim(20.0, 22.0, 2.0, 0.3, 0.0, 0.05, -1.0, 100.0)
    with pytest.raises(ValueError):
        dilution.option_claim(20.0, 22.0, 2.0, 0.3, 0.0, 0.05, 1.0, 100.0, vested=1.5)
    with pytest.raises(ValueError):
        dilution.option_claim(20.0, 22.0, 2.0, 0.3, 0.0, 0.05, 1.0, 100.0, tax_rate=1.0)


@pytest.mark.skipif(not WORKBOOK.exists(), reason="the Damodaran mirror is not on this machine")
def test_the_inputs_of_the_sample_are_the_workbooks():
    import xlrd

    sheet = xlrd.open_workbook(str(WORKBOOK)).sheet_by_index(0)
    assert sheet.cell_value(7, 1) == 20.83 and sheet.cell_value(13, 1) == 520.0 and sheet.cell_value(37, 1) == pytest.approx(1.8114545483312297)


def test_the_owners_option_inputs_become_a_claim_on_equity_and_a_company_without_them_says_options_are_not_modelled():
    from datetime import date

    from test_val_scenarios import DAY, company, industry, reference

    from athena.valuation import scenarios as sc
    from athena.valuation.wacc import company_cost_of_capital

    def run(options=None):
        with reference() as store:
            if options:
                store.put_input("esop_options:XYZ", date(2026, 4, 1), options[0], "owner")
                store.put_input("esop_strike:XYZ", date(2026, 4, 1), options[1], "owner")
            c = company()
            return sc.value_company(store, c, industry(), company_cost_of_capital(store, c, industry(), DAY), DAY, sc.Policy(detail=False))

    plain, with_options = run(), run((2e7, 80.0))  # 2 crore options at Rs 80 against 10 crore shares at Rs 100
    assert with_options.ok and plain.ok
    assert with_options.base < plain.base
    assert any("employee options are not modelled" in f for f in plain.flags)
    assert not any("employee options are not modelled" in f for f in with_options.flags)
    assert any("employee options: 20,000,000 at a strike of Rs 80.00" in f for f in with_options.flags)
    one = run((2e7, 8000.0))  # far out of the money: nearly nothing
    assert one.base == pytest.approx(plain.base, rel=0.01)


def test_the_owners_market_value_of_listed_holdings_replaces_the_book_value_of_investments():
    from datetime import date

    from test_val_scenarios import DAY, company, industry, reference

    from athena.valuation import scenarios as sc
    from athena.valuation.wacc import company_cost_of_capital

    def run(listed=None):
        with reference() as store:
            if listed is not None:
                store.put_input("listed_investments:XYZ", date(2026, 4, 1), listed, "owner")
            c = company(investments=50.0)
            return sc.value_company(store, c, industry(), company_cost_of_capital(store, c, industry(), DAY), DAY, sc.Policy(detail=False))

    plain, richer = run(), run(1050.0)  # the holdings are worth Rs 1,000 crore more than their book value
    assert richer.base > plain.base
    assert any("investments taken at Rs 1,050 cr" in f and "book value of Rs 50 cr" in f for f in richer.flags)
    assert any("investments too" in f for f in plain.flags) and not any("investments too" in f for f in richer.flags)
    assert run(0.0).base < plain.base  # an owner who says the holdings are worth nothing gets that
