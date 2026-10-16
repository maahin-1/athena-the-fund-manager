from datetime import date
from pathlib import Path

import pytest

from athena.fundamentals import lines as spec
from athena.fundamentals import rows
from athena.fundamentals.rows import parse_rows

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
FY24 = (date(2023, 4, 1), date(2024, 3, 31))
Q4_24 = (date(2024, 1, 1), date(2024, 3, 31))
BS24 = date(2024, 3, 31)


def load(name: str):
    return parse_rows((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))


def test_an_industrial_annual_filing_gives_the_year_the_last_quarter_the_balance_sheet_and_the_cash_flow():
    st = load("tcs_fy2024")
    assert (st.symbol, st.basis, st.layout) == ("TCS", "consolidated", "indas")
    assert st.income[FY24]["sales"] == 2408930000000.0
    assert st.income[FY24]["pbt"] == 619970000000.0
    assert st.income[Q4_24]["sales"] == 612370000000.0
    assert st.balance[BS24]["total_assets"] == 1464490000000.0
    assert st.cashflow[FY24]["cfo"] == 443380000000.0
    assert 3.5e9 < st.shares < 3.8e9


def test_derived_rows_follow_from_the_filed_lines():
    st = load("tcs_fy2024")
    inc = st.income[FY24]
    assert inc["expenses"] == pytest.approx(inc["total_expenses"] - inc["interest"] - inc["depreciation"])
    assert inc["operating_profit"] == pytest.approx(inc["sales"] - inc["expenses"])
    assert inc["opm"] == pytest.approx(100 * inc["operating_profit"] / inc["sales"])
    assert inc["tax_pct"] == pytest.approx(100 * inc["tax"] / inc["pbt"])
    bal = st.balance[BS24]
    assert bal["equity_capital"] + bal["reserves"] + bal["borrowings"] + bal["other_liabilities"] == pytest.approx(bal["total_assets"])
    assert bal["fixed_assets"] + bal.get("cwip", 0.0) + bal["investments"] + bal["other_assets"] == pytest.approx(bal["total_assets"])
    cf = st.cashflow[FY24]
    assert cf["fcf"] == pytest.approx(cf["cfo"] - cf["capex"])


def test_the_industrial_filing_reconciles_to_its_own_profit_before_tax():
    assert load("tcs_fy2024").notes == ()


def test_a_filing_from_the_results_only_era_has_income_and_no_balance_sheet_or_cash_flow():
    st = load("tcs_fy2020")
    assert (date(2019, 4, 1), date(2020, 3, 31)) in st.income
    assert st.balance == {} and st.cashflow == {}


def test_a_bank_filing_uses_the_banking_layout():
    st = load("hdfcbank_fy2024")
    assert st.layout == "bank"
    assert st.income[FY24]["sales"] == 2836490200000.0  # interest earned
    assert st.income[FY24]["pbt"] == 751841400000.0
    bal = st.balance[BS24]
    assert bal["total_assets"] == 40301942600000.0
    assert bal["deposits"] > 0 and bal["advances"] > 0
    assert bal["equity_capital"] + bal["reserves"] + bal["borrowings"] + bal["deposits"] + bal["other_liabilities"] == pytest.approx(bal["total_assets"])
    inc = st.income[FY24]
    # a bank's operating profit is the filed profit before provisions; here it also equals the lines it is made of
    assert inc["operating_profit"] > 0
    assert inc["operating_profit"] == pytest.approx(inc["sales"] + inc["other_income"] - inc["interest"] - inc["operating_expenses"], rel=1e-6)
    assert inc["opm"] == pytest.approx(100 * inc["operating_profit"] / inc["total_income"])
    assert inc["pbt"] == pytest.approx(inc["operating_profit"] - inc["provisions"] + inc["exceptional"], rel=1e-3)
    assert "fcf" not in st.cashflow[FY24]  # a bank's operating cash flow is loans and deposits; free cash flow does not describe it
    assert st.banking == {}  # the ratios the filer left at 0.00 are not reported, not zero


def test_a_lender_filing_under_ind_as_gets_the_finance_layout():
    st = load("bajfinance_fy2024")
    assert st.layout == "finance"
    assert st.income[FY24]["sales"] == 549694900000.0
    assert st.income[FY24]["pbt"] == 193019300000.0
    assert st.cashflow[FY24]["cfo"] == -727601400000.0
    bal = st.balance[BS24]
    assert bal["total_assets"] == 3757416200000.0
    assert bal["loans"] > 0 and bal["borrowings"] > 0


def test_text_that_is_not_a_results_filing_is_refused():
    with pytest.raises(ValueError):
        parse_rows("<html>error page</html>")
    with pytest.raises(ValueError):
        parse_rows("not xml at all")


def test_a_zero_in_the_first_tag_does_not_hide_the_tag_that_holds_the_figure():
    line = spec.Line("eps", "EPS", ("Diluted", "Basic"))
    assert rows._pick({"Diluted": "0.00", "Basic": "7.5"}, line) == 7.5
    assert rows._pick({"Diluted": "0.00"}, line) == 0.0
    assert rows._pick({}, line) is None
    total = spec.Line("debt", "Debt", ("A", "B"), "sum")
    assert rows._pick({"A": "10", "B": "5"}, total) == 15.0
    assert rows._pick({"B": "5"}, total) == 5.0


def test_free_cash_flow_needs_a_real_capital_expenditure_and_a_non_bank():
    assert rows.derive_cashflow({"cfo": 100.0, "capex": 30.0}, "indas")["fcf"] == 70.0
    assert "fcf" not in rows.derive_cashflow({"cfo": 100.0, "capex": 0.0}, "indas")
    assert "fcf" not in rows.derive_cashflow({"cfo": 100.0}, "indas")
    assert "fcf" not in rows.derive_cashflow({"cfo": 100.0, "capex": 30.0}, "bank")


def test_a_profit_that_does_not_follow_from_the_lines_is_noted():
    period = (date(2024, 1, 1), date(2024, 3, 31))
    assert rows.reconcile({period: {"sales": 1000.0, "expenses": 100.0, "pbt": 500.0}})
    assert not rows.reconcile({period: {"sales": 1000.0, "expenses": 100.0, "pbt": 900.0}})
    assert not rows.reconcile({period: {"sales": 1000.0, "expenses": 100.0, "exceptional": -50.0, "pbt": 850.0}})
