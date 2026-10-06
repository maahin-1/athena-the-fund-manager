import random
from datetime import date, timedelta

import pytest

from athena.backtest.engine import BacktestResult, Config, Trade
from athena.backtest.summary import format_summary, summarize

CONFIG = Config()


def weekdays(count, start=date(2025, 1, 6)):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def trade(net, reason="signal"):
    return Trade(date(2025, 1, 6), date(2025, 2, 6), 100.0, 100.0 * (1 + net), net, reason)


def result(equity, benchmark=None, in_market=None, trades=(), days=None):
    count = len(equity)
    return BacktestResult(
        "trend", CONFIG, days or weekdays(count), tuple(equity), tuple(in_market or [True] * count), tuple(trades),
        tuple(benchmark or [100.0] * count),
    )


def noisy(count, drift, vol, seed):
    rng, level, out = random.Random(seed), 100.0, []
    for _ in range(count):
        level *= 1 + rng.gauss(drift, vol)
        out.append(level)
    return out


def test_total_return_excess_over_buy_and_hold_and_drawdown_come_from_the_curves():
    s = summarize(result([105, 110, 99, 105], benchmark=[100, 102, 101, 104]))
    assert s.total_return == pytest.approx(0.05) and s.benchmark_total_return == pytest.approx(0.04)
    assert s.excess_return == pytest.approx(0.01)
    assert s.max_drawdown == pytest.approx(99 / 110 - 1)  # the fall from the 110 peak to 99
    assert s.benchmark_max_drawdown == pytest.approx(0.0, abs=1e-12) or s.benchmark_max_drawdown < 0


def test_the_drawdown_counts_from_the_starting_cash_not_just_the_first_close():
    assert summarize(result([90, 95, 99])).max_drawdown == pytest.approx(-0.10)


def test_yearly_return_uses_the_calendar_span_of_the_window():
    days = (date(2025, 1, 1), date(2026, 1, 1))
    s = summarize(result([110.0, 121.0], days=days))
    assert s.cagr == pytest.approx(1.21 ** (365.25 / 365) - 1, abs=1e-6)
    assert summarize(result([100.0, 100.0], days=(date(2025, 1, 1), date(2025, 1, 1)))).cagr is None


def test_too_short_a_series_gives_no_volatility_or_sharpe_instead_of_a_guess():
    s = summarize(result([101, 102, 103, 104]))
    assert s.volatility is None and s.sharpe is None and s.max_drawdown is not None


def test_a_long_series_gets_volatility_and_a_sharpe_that_a_positive_riskfree_rate_lowers():
    equity = noisy(120, 0.001, 0.01, seed=3)
    days = weekdays(120)
    riskfree = {day: 100.0 * (1.0004 ** i) for i, day in enumerate(days)}  # a steady overnight accrual
    plain, with_rate = summarize(result(equity, days=days)), summarize(result(equity, days=days), riskfree=riskfree)
    assert plain.volatility > 0 and plain.sharpe is not None and with_rate.sharpe < plain.sharpe


def test_a_riskfree_series_that_starts_late_drops_the_sharpe_rather_than_inventing_one():
    days = weekdays(60)
    late = {days[30]: 100.0, days[-1]: 103.0}
    assert summarize(result(noisy(60, 0.001, 0.01, 1), days=days), riskfree=late).sharpe is None


def test_trade_statistics():
    s = summarize(result([100, 101], trades=[trade(0.10), trade(-0.05, "stop"), trade(0.02)], in_market=[False, True]))
    assert (s.trades, s.stops) == (3, 1) and s.win_rate == pytest.approx(2 / 3)
    assert s.average_trade == pytest.approx((0.10 - 0.05 + 0.02) / 3) and s.worst_trade == pytest.approx(-0.05)
    assert s.time_in_market == 0.5


def test_no_trades_means_no_trade_statistics():
    s = summarize(result([100.0, 100.0]))
    assert (s.trades, s.win_rate, s.average_trade, s.worst_trade) == (0, None, None, None)


def test_the_market_return_over_the_same_window_is_reported_when_a_series_is_given():
    days = weekdays(4)
    index = {days[0]: 200.0, days[-1]: 230.0}
    assert summarize(result([100, 101, 102, 103], days=days), index=index).index_total_return == pytest.approx(0.15)
    assert summarize(result([100, 101, 102, 103], days=days)).index_total_return is None


def test_the_text_report_shows_both_columns_and_marks_missing_figures_na():
    text = format_summary(summarize(result([105, 110, 99, 105], benchmark=[100, 102, 101, 104])), "SBIN", "a rule")
    for expected in ("Backtest SBIN  rule: trend", "a rule", "strategy    buy and hold", "total return", "Sharpe", "n/a", "excess over buy and hold: 1.0%"):
        assert expected in text


def test_a_trade_that_breaks_even_is_not_counted_as_a_win():
    summary = summarize(result([100, 101, 102], trades=[trade(0.10), trade(0.0), trade(-0.05)]))
    assert summary.win_rate == pytest.approx(1 / 3)
