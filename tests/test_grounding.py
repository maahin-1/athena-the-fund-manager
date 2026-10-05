import pytest

from athena.evaluation.grounding import check_grounded, collect_values, numbers_in_text

PACKET = {
    "instrument": "SBIN",
    "as_of": "2026-10-05T04:00:00+00:00",
    "metrics": {
        "beta": {"value": 1.101388, "unit": "ratio", "window": "252 returns 2025-10-01..2026-10-01"},
        "volatility_annualized": {"value": 0.237161, "unit": "fraction"},
        "max_drawdown": {"value": -0.234892, "unit": "fraction"},
    },
    "price": 22421.95,
}


def test_numbers_in_text_reads_signs_commas_decimals_and_percents():
    assert numbers_in_text("beta 1.10, vol 23.7%, drawdown -23.5%, level 22,421.95") == [
        "1.10", "23.7%", "-23.5%", "22,421.95",
    ]


def test_digits_glued_to_letters_are_not_numbers():
    assert numbers_in_text("FY2026 results in Q3") == []


def test_collect_values_walks_nesting_and_numbers_inside_strings():
    values = collect_values(PACKET)
    assert 1.101388 in values and 22421.95 in values
    assert 252.0 in values and 2026.0 in values  # from the window and as_of strings
    assert collect_values({"flag": True}) == []


@pytest.mark.parametrize(
    "reasoning",
    [
        "Beta of 1.10 means the stock moves with the market.",
        "Annualised volatility is 23.7%.",
        "A drawdown of 23.5% over the year; the price is 22,421.95.",
        "Volatility of roughly 24% (rounded).",
        "Beta is 1.1.",
    ],
)
def test_figures_present_in_the_data_are_grounded(reasoning):
    result = check_grounded(reasoning, PACKET)
    assert result.ok, result.ungrounded
    assert result.checked >= 1


def test_invented_figures_are_flagged():
    result = check_grounded("Beta is 1.10 but volatility is 31.2% and alpha is 4.5%.", PACKET)
    assert not result.ok
    assert result.ungrounded == ("31.2%", "4.5%")
    assert result.checked == 3


def test_precision_matters_a_figure_off_by_more_than_rounding_is_flagged():
    assert not check_grounded("Beta is 1.15.", PACKET).ok
    assert check_grounded("Beta is 1.1.", PACKET).ok


def test_small_integers_are_ignored_by_default_but_can_be_checked():
    text = "Three of 5 factors over 2 years."
    assert check_grounded(text, PACKET).ok
    assert not check_grounded(text, PACKET, ignore_small_integers=False).ok


def test_text_without_figures_is_trivially_grounded():
    result = check_grounded("The stock looks fairly valued.", PACKET)
    assert result.ok and result.checked == 0
