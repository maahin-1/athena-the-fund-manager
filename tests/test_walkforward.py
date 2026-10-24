from datetime import date, timedelta

import numpy as np
import pytest

from athena.evaluation import walkforward as wf

DAYS = [date(2015, 1, 1) + timedelta(days=i) for i in range(900)]


def panel(trend_persists: bool, names: int = 150, seed: int = 1) -> wf.Panel:
    """Each stock drifts at its own steady rate; with persistence the drift is the same in every period (so past winners keep winning), without it the drift is redrawn each period."""
    rng = np.random.default_rng(seed)
    series = {}
    base = rng.normal(0.0, 0.002, names)
    for s in range(names):
        price, rows = 1000.0, []
        for i, d in enumerate(DAYS):
            drift = base[s] if trend_persists else rng.normal(0.0, 0.002)
            price *= 1.0 + drift + rng.normal(0.0, 0.004)
            rows.append((d, price))
        series[f"S{s}"] = rows
    return wf.Panel.build(series)


def test_a_rule_that_works_shows_a_high_rank_correlation_and_a_top_fifth_that_beats_the_bottom():
    rep = wf.report(wf.run(panel(True), wf.momentum_12_1, date(2016, 1, 1), 21))
    assert rep["overall"]["dates"] >= 15 and rep["overall"]["mean_ic"] > 0.2 and rep["overall"]["ic_t"] > 3
    assert rep["overall"]["mean_top_minus_bottom"] > 0 and rep["years_positive"].split()[0] == str(len(rep["years"]))


def test_the_same_rule_on_prices_with_no_persistence_shows_nothing():
    rep = wf.report(wf.run(panel(False), wf.momentum_12_1, date(2016, 1, 1), 21))
    assert abs(rep["overall"]["mean_ic"]) < 0.08


def test_test_dates_are_a_horizon_apart_and_each_has_a_full_forward_window():
    p = panel(True, names=5)
    idx = wf.test_dates(p, date(2016, 1, 1), 21)
    assert all(b - a == 21 for a, b in zip(idx, idx[1:])) and idx[-1] + 21 < len(p.days) and p.days[idx[0]] >= date(2016, 1, 1)


def test_a_stock_with_no_close_at_the_end_of_the_window_or_a_penny_price_is_left_out():
    p = panel(True, names=120)
    p.px["S0"][:] = 5.0  # below MIN_PRICE
    p.px["S1"][-30:] = np.nan  # stops trading before the last windows end
    assert "S0" not in wf.momentum_12_1(p, 400) and "S1" in wf.momentum_12_1(p, 400)
    last = wf.test_dates(p, date(2016, 1, 1), 21)[-1]
    got = wf.run(p, wf.momentum_12_1, date(2016, 1, 1), 21)
    assert got[-1]["n"] <= 119 and p.days[last] == got[-1]["date"]


def test_too_few_stocks_gives_no_rows_and_the_report_says_so():
    assert wf.run(panel(True, names=20), wf.momentum_12_1, date(2016, 1, 1), 21) == []
    assert "no test dates" in wf.format_report("x", wf.report([]))


def test_the_report_line_names_the_rule_the_years_and_the_share_of_good_years():
    line = wf.format_report("momentum_12_1", wf.report(wf.run(panel(True), wf.momentum_12_1, date(2016, 1, 1), 21)))
    assert line.startswith("momentum_12_1:") and "positive in" in line and "By year:" in line


def test_low_volatility_and_reversal_score_every_stock_with_enough_history():
    p = panel(True, names=30)
    assert len(wf.low_volatility(p, 300)) == 30 and len(wf.reversal_1m(p, 300)) == 30
    assert wf.low_volatility(p, 50) == {} and wf.momentum_12_1(p, 100) == {}
