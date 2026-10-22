import pytest

from athena.valuation import excess_return as e


def test_a_bank_earning_above_its_cost_for_five_years_is_worth_more_than_its_book_by_hand():
    x = e.BankInputs(book_value_per_share=100.0, roe=0.15, book_growth=0.10, cost_of_equity=0.12, stable_roe=0.12, stable_growth=0.06)
    r = e.run_excess_return(x)
    # cash to shareholders in year n = (0.15 - 0.10) x opening book = 0.05 x 100 x 1.1^(n-1)
    cash = [5.0 * 1.1 ** (n - 1) for n in range(1, 6)]
    pv_cash = sum(c / 1.12 ** n for n, c in zip(range(1, 6), cash))
    book5 = 100.0 * 1.1**5
    assert [round(y.cash_to_shareholders, 6) for y in r.years] == pytest.approx([round(c, 6) for c in cash])
    assert r.terminal_price_to_book == pytest.approx(1.0)  # a stable ROE equal to the cost of equity adds nothing after year 5
    assert r.pv_terminal == pytest.approx(book5 / 1.12**5)
    assert r.value_per_share == pytest.approx(pv_cash + book5 / 1.12**5) and r.justified_price_to_book == pytest.approx(r.value_per_share / 100.0)
    assert r.value_per_share > 100.0


def test_a_bank_that_only_earns_its_cost_of_equity_is_worth_its_book():
    x = e.BankInputs(250.0, roe=0.12, book_growth=0.08, cost_of_equity=0.12, stable_roe=0.12, stable_growth=0.06)
    assert e.run_excess_return(x).value_per_share == pytest.approx(250.0)


def test_a_bank_earning_below_its_cost_is_worth_less_than_its_book_and_a_higher_stable_roe_adds_value():
    base = e.BankInputs(100.0, roe=0.08, book_growth=0.08, cost_of_equity=0.12, stable_roe=0.12, stable_growth=0.06)
    assert e.run_excess_return(base).value_per_share < 100.0
    richer = e.BankInputs(100.0, roe=0.08, book_growth=0.08, cost_of_equity=0.12, stable_roe=0.16, stable_growth=0.06)
    assert e.run_excess_return(richer).value_per_share > e.run_excess_return(base).value_per_share


def test_a_cost_of_equity_at_or_below_the_stable_growth_or_a_non_positive_book_is_refused():
    with pytest.raises(ValueError, match="cost of equity"):
        e.run_excess_return(e.BankInputs(100.0, 0.15, 0.1, 0.06, 0.12, 0.06))
    with pytest.raises(ValueError, match="book value"):
        e.run_excess_return(e.BankInputs(-1.0, 0.15, 0.1, 0.12, 0.12, 0.06))
