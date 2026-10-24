import math
from datetime import date, timedelta

from athena.market import regime

DAYS = [date(2024, 1, 1) + timedelta(days=i) for i in range(600)]


def series(daily):
    """Closes from a list of daily returns."""
    price, out = 100.0, {}
    for d, r in zip(DAYS, daily):
        price *= 1 + r
        out[d] = price
    return out


def wobble(i, size):
    return size * (1 if i % 2 else -1)


def test_a_quiet_rising_market_after_wilder_years_is_calm():
    daily = [wobble(i, 0.012) for i in range(450)] + [0.0008 + wobble(i, 0.002) for i in range(150)]
    g = regime.gauge(series(daily))
    assert g["state"] == regime.CALM and g["vol_percentile"] <= 1 / 3 and g["drawdown_from_252d_high"] < 0.05 and g["close_vs_200d_average"] > 0


def test_a_sudden_burst_of_volatility_is_stressed_even_without_a_big_fall():
    daily = [wobble(i, 0.003) for i in range(570)] + [wobble(i, 0.03) for i in range(30)]
    g = regime.gauge(series(daily))
    assert g["state"] == regime.STRESSED and g["vol_percentile"] >= regime.STRESS_VOL_PERCENTILE


def test_a_deep_fall_is_stressed_whatever_the_volatility():
    daily = [0.0005 + wobble(i, 0.004) for i in range(540)] + [-0.012] * 60
    g = regime.gauge(series(daily))
    assert g["drawdown_from_252d_high"] >= regime.STRESS_DRAWDOWN and g["state"] == regime.STRESSED


def test_a_market_in_between_is_normal_and_too_little_history_gives_nothing():
    daily = [wobble(i, 0.003) for i in range(200)] + [wobble(i, 0.012) for i in range(200)] + [wobble(i, 0.006) for i in range(200)]
    assert regime.gauge(series(daily))["state"] == regime.NORMAL
    assert regime.gauge(dict(list(series([0.001] * 600).items())[:100])) is None


def test_the_pe_percentile_is_reported_beside_the_state_not_in_it():
    closes = series([0.0008 + wobble(i, 0.002) for i in range(600)])
    pe = {d: 20.0 + math.sin(i / 30) for i, d in enumerate(DAYS)}
    g = regime.gauge(closes, pe)
    assert g["pe"] is not None and 0 <= g["pe_percentile"] <= 1
    assert regime.gauge(closes)["pe"] is None
    assert "P/E" in regime.format_gauge(g) and "not enough" in regime.format_gauge(None)
