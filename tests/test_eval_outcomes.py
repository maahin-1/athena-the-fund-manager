from datetime import date, timedelta

import pytest
from bar_factory import make_bars

from athena.evaluation.outcomes import INLINE, OUTPERFORM, UNDERPERFORM, forward_outcome
from athena.trading_calendar import ist_date

BARS = make_bars([100.0 + i for i in range(200)])  # a steady rise of one rupee a day
DAYS = [ist_date(bar.timestamp) for bar in BARS]


def index_for(levels):
    return {day: level for day, level in zip(DAYS, levels)}


def test_the_outcome_is_the_stock_return_less_the_index_return_over_the_same_dates():
    flat = index_for([1000.0] * 200)
    outcome = forward_outcome(BARS, flat, DAYS[10], 63)
    assert (outcome.start, outcome.end) == (DAYS[10], DAYS[73])
    assert outcome.stock_return == pytest.approx(173 / 110 - 1) and outcome.index_return == 0 and outcome.excess == pytest.approx(outcome.stock_return)
    assert outcome.label == OUTPERFORM and outcome.signal == "bullish"


def test_a_stock_that_keeps_pace_with_the_index_is_inline_and_one_that_lags_it_underperforms():
    pace = index_for([1000.0 * (100.0 + i) / 100.0 for i in range(200)])
    assert forward_outcome(BARS, pace, DAYS[10], 63).label == INLINE
    faster = index_for([1000.0 * (1 + 0.02 * i) for i in range(200)])  # +2% of the start a day: the index runs away from the stock
    lagging = forward_outcome(BARS, faster, DAYS[10], 63)
    assert lagging.label == UNDERPERFORM and lagging.signal == "bearish" and lagging.excess < -0.05


def test_the_dead_zone_is_set_by_the_band_and_defaults_by_horizon():
    pace = index_for([1000.0] * 200)
    near = forward_outcome(BARS, pace, DAYS[10], 63, band=10.0)
    assert near.label == INLINE
    assert forward_outcome(BARS, pace, DAYS[10], 126).horizon_days == 126


def test_an_outcome_that_is_not_known_yet_or_has_no_index_level_is_none():
    flat = index_for([1000.0] * 200)
    assert forward_outcome(BARS, flat, DAYS[150], 63) is None  # 150 + 63 is past the last bar
    assert forward_outcome(BARS, {}, DAYS[10], 63) is None
    assert forward_outcome(BARS, flat, DAYS[0] - timedelta(days=30), 63) is None  # before the first bar


def test_an_as_of_that_is_not_a_trading_day_uses_the_close_before_it_and_the_index_level_before_it():
    weekend = next(day for day in (DAYS[10] + timedelta(days=k) for k in range(1, 6)) if day not in DAYS)
    flat = index_for([1000.0] * 200)
    assert forward_outcome(BARS, flat, weekend, 63).start in DAYS and forward_outcome(BARS, flat, weekend, 63).start < weekend
    assert isinstance(weekend, date)
