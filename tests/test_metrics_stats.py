import math
import statistics

import pytest

from athena.contracts import InsufficientData
from athena.metrics import stats

BENCH = [((i * 7) % 11 - 5) / 500 for i in range(30)]  # varied, non-constant daily returns


def test_volatility_of_alternating_returns_matches_the_closed_form():
    returns = [0.01, -0.01] * 10
    expected = math.sqrt(20 * 0.01**2 / 19) * math.sqrt(252)
    assert stats.annualized_volatility(returns) == pytest.approx(expected)


def test_too_few_observations_raise_insufficient_data():
    with pytest.raises(InsufficientData, match="at least 20"):
        stats.annualized_volatility([0.01] * 5)
    with pytest.raises(InsufficientData):
        stats.max_drawdown([100.0])


def test_max_drawdown_is_the_worst_peak_to_trough_fall():
    assert stats.max_drawdown([100, 120, 90, 110, 80, 130]) == pytest.approx(80 / 120 - 1)
    assert stats.max_drawdown([100, 101, 102]) == 0.0


def test_beta_recovers_a_known_multiple():
    asset = [2.0 * r for r in BENCH]
    assert stats.beta(asset, BENCH) == pytest.approx(2.0)


def test_beta_needs_a_benchmark_that_moves():
    with pytest.raises(InsufficientData, match="zero variance"):
        stats.beta([0.01] * 25, [0.0] * 25)


def test_alpha_recovers_a_known_intercept_and_beta():
    asset = [1.5 * r + 0.0004 for r in BENCH]
    rf = [0.0] * len(BENCH)
    assert stats.jensen_alpha(asset, BENCH, rf) == pytest.approx(0.0004 * 252)
    assert stats.beta(asset, BENCH) == pytest.approx(1.5)


def test_alpha_is_zero_for_an_asset_that_earns_exactly_the_riskfree_rate():
    rf = [0.0002] * len(BENCH)
    assert stats.jensen_alpha(rf, BENCH, rf) == pytest.approx(0.0, abs=1e-12)


def test_sharpe_matches_the_closed_form():
    rf = [0.0002] * 30
    excess = [((i * 5) % 7 - 3) / 400 for i in range(30)]
    asset = [r + x for r, x in zip(rf, excess)]
    expected = statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252)
    assert stats.sharpe(asset, rf) == pytest.approx(expected)


def test_sharpe_with_constant_excess_return_is_undefined():
    with pytest.raises(InsufficientData, match="zero variance"):
        stats.sharpe([0.001] * 25, [0.0] * 25)


def test_tracking_error_is_annualised_std_of_active_returns():
    index = BENCH
    active = [0.001 if i % 2 else -0.001 for i in range(30)]
    asset = [r + a for r, a in zip(index, active)]
    assert stats.tracking_error(asset, index) == pytest.approx(statistics.stdev(active) * math.sqrt(252))
    assert stats.tracking_error(index, index) == pytest.approx(0.0, abs=1e-12)


def test_tracking_difference_is_cumulative_return_gap():
    assert stats.tracking_difference([100, 110], [100, 112]) == pytest.approx(0.10 - 0.12)


def test_unequal_lengths_are_rejected():
    with pytest.raises(ValueError, match="equal-length"):
        stats.tracking_error([0.01] * 25, [0.01] * 24)


TAIL = [i / 1000 for i in range(-50, 50)]  # 100 returns from -5.0% to +4.9%


def test_value_at_risk_is_the_loss_at_the_fifth_percentile():
    # the 5th percentile sits 4.95 steps above the worst return: -0.050 + 4.95 x 0.001 = -0.04505
    assert stats.value_at_risk(TAIL) == pytest.approx(0.04505)
    assert stats.value_at_risk(TAIL, confidence=0.99) == pytest.approx(0.04901)


def test_expected_shortfall_is_the_average_loss_beyond_the_cutoff_and_never_below_var():
    assert stats.expected_shortfall(TAIL) == pytest.approx(0.048)  # mean of -0.050 ... -0.046
    assert stats.expected_shortfall(TAIL) >= stats.value_at_risk(TAIL)
    assert stats.expected_shortfall(TAIL, confidence=0.99) >= stats.value_at_risk(TAIL, confidence=0.99)


def test_value_at_risk_and_shortfall_are_zero_when_the_whole_tail_was_a_gain():
    gains = [0.01 + i / 10000 for i in range(30)]
    assert stats.value_at_risk(gains) == 0.0 and stats.expected_shortfall(gains) == 0.0


def test_value_at_risk_and_shortfall_need_enough_observations():
    for function in (stats.value_at_risk, stats.expected_shortfall):
        with pytest.raises(InsufficientData, match="at least 20"):
            function([-0.01] * 5)
