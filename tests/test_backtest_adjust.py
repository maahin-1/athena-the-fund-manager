from dataclasses import replace

import pytest
from bar_factory import make_bars

from athena.backtest import adjust
from athena.contracts import InsufficientData


@pytest.mark.parametrize("field", ["open", "close"])
@pytest.mark.parametrize("value", [0.0, -1.0])
def test_a_non_positive_price_is_refused_not_divided_by(field, value):
    bars = make_bars([100.0, 101.0, 102.0])
    bars[1] = replace(bars[1], **{field: value})
    with pytest.raises(InsufficientData, match="non-positive prices"):
        adjust.require_positive_prices(bars)


def test_nothing_in_the_backtest_package_adjusts_prices_from_a_guess_any_more():
    """Prices are adjusted only against a recorded event, in athena.price_integrity."""
    assert not hasattr(adjust, "adjust_for_splits") and not hasattr(adjust, "adjustment_note")
