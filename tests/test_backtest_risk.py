import pytest
from bar_factory import NOW, make_bars

from athena.backtest.engine import Config, run_backtest
from athena.backtest.rules import Rule
from athena.contracts import Bar
from athena.strategies.compile import strategy_rule
from athena.strategies.parse import parse_strategy
from athena.technicals.candles import candles_from_bars

ALWAYS_IN = Rule("always", "enter at the first chance and never leave", lambda p: True, lambda p: False)
BASE = dict(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=0.0)


def cfg(**extra):
    return Config(**{**BASE, **extra})


def climbing(count=60):
    return make_bars([100.0 + i for i in range(count)])  # open = close, high 1 above, low 1 below: ATR is 2; entry fills at bar 21's open, 121


def replace_bar(bars, index, open_, high, low, close):
    day = bars[index]
    out = list(bars)
    out[index] = Bar(day.symbol, day.timestamp, open_, high, low, close, 1000.0, NOW, "t")
    return out


def test_a_profit_target_fills_at_the_target_when_the_day_reaches_it():
    result = run_backtest(climbing(), ALWAYS_IN, cfg(take_profit_pct=5.0))
    trade = result.trades[0]
    assert trade.reason == "target" and trade.entry_price == 121.0 and trade.exit_price == pytest.approx(121.0 * 1.05)


def test_a_gap_above_the_target_fills_at_the_open_not_the_target():
    bars = replace_bar(climbing(), 25, 150.0, 151.0, 149.0, 150.0)
    trade = run_backtest(bars, ALWAYS_IN, cfg(take_profit_pct=5.0)).trades[0]
    assert trade.reason == "target" and trade.exit_price == 150.0


def test_when_one_bar_reaches_both_the_stop_and_the_target_the_stop_is_taken():
    bars = replace_bar(climbing(), 23, 121.0, 130.0, 110.0, 125.0)  # entry 121, stop 121 - 2 x 2 = 117, target 127.05
    trade = run_backtest(bars, ALWAYS_IN, cfg(stop_atr_multiple=2.0, take_profit_pct=5.0)).trades[0]
    assert trade.reason == "stop" and trade.exit_price == pytest.approx(117.0, abs=0.01)


def test_a_trailing_stop_follows_the_highest_high_up_and_never_comes_down():
    closes = [100.0 + i for i in range(41)] + [140.0 - i for i in range(1, 20)]  # up to 140, then back down
    result = run_backtest(make_bars(closes), ALWAYS_IN, cfg(trailing_stop_atr=2.0))
    trade = result.trades[0]
    # the highest high is 141 (the peak bar's close plus one), so the level is 141 - 2 x 2 = 137
    assert trade.reason == "trailing_stop" and trade.exit_price == pytest.approx(137.0, abs=0.01) and trade.net_return > 0


def test_a_trailing_stop_that_is_never_touched_leaves_the_position_open():
    result = run_backtest(climbing(), ALWAYS_IN, cfg(trailing_stop_atr=2.0))
    assert result.trades == () and result.in_market[-1]


def test_the_higher_of_the_fixed_and_trailing_stops_decides_and_names_the_reason():
    closes = [100.0 + i for i in range(41)] + [140.0 - i for i in range(1, 20)]
    wide = run_backtest(make_bars(closes), ALWAYS_IN, cfg(stop_atr_multiple=10.0, trailing_stop_atr=2.0)).trades[0]
    assert wide.reason == "trailing_stop"  # the trailing level has climbed far above the fixed one
    crash = replace_bar(climbing(), 23, 121.0, 121.5, 100.0, 105.0)
    early = run_backtest(crash, ALWAYS_IN, cfg(stop_atr_multiple=1.0, trailing_stop_atr=3.0)).trades[0]
    assert early.reason == "stop"  # entry 121: fixed 119, trailing 115 on the first bars


def test_a_time_stop_sells_at_the_next_open_after_the_limit_counting_the_entry_bar():
    bars = climbing()
    candles = candles_from_bars(bars)
    trade = run_backtest(bars, ALWAYS_IN, cfg(max_hold_bars=3)).trades[0]
    assert trade.reason == "time" and trade.entry_day == candles[21].day and trade.exit_day == candles[24].day  # closes 21, 22, 23 held
    assert trade.exit_price == candles[24].open


def test_a_signal_exit_before_the_time_limit_stays_a_signal():
    leave_now = Rule("leave", "enter, then leave at once", lambda p: True, lambda p: True)
    trade = run_backtest(climbing(), leave_now, cfg(max_hold_bars=30)).trades[0]
    assert trade.reason == "signal"


def test_a_position_size_below_a_hundred_percent_leaves_the_rest_in_cash():
    bars = climbing()
    result = run_backtest(bars, ALWAYS_IN, cfg(position_pct=50.0))
    candles = candles_from_bars(bars)
    expected = 50.0 + 50.0 * candles[-1].close / candles[21].open
    assert result.equity[-1] == pytest.approx(expected) and result.in_market[-1]
    full = run_backtest(bars, ALWAYS_IN, cfg())
    assert full.equity[-1] == pytest.approx(100.0 * candles[-1].close / candles[21].open)


def test_a_half_size_trade_reports_its_return_on_the_amount_invested():
    bars = climbing()
    half = run_backtest(bars, ALWAYS_IN, cfg(position_pct=50.0, take_profit_pct=5.0))
    full = run_backtest(bars, ALWAYS_IN, cfg(take_profit_pct=5.0))
    assert half.trades[0].net_return == pytest.approx(full.trades[0].net_return)
    day = half.days.index(half.trades[0].exit_day)  # the bar of the first exit: flat, before the always-in rule buys again
    assert half.equity[day] == pytest.approx(100.0 + (full.equity[day] - 100.0) / 2)


def test_a_strategy_carries_its_own_risk_options_into_the_run_replacing_the_defaults():
    rule = strategy_rule(parse_strategy({
        "name": "t", "entry": {"op": "gt", "left": {"price": "close"}, "right": {"const": 0}},
        "exit": {"op": "lt", "left": {"price": "close"}, "right": {"const": 0}},
        "take_profit_pct": 5, "max_hold_bars": 40, "position_pct": 50, "trailing_stop_atr": 3,
    }))
    result = run_backtest(climbing(), rule, cfg(stop_atr_multiple=2.0, position_pct=100.0))
    config = result.config
    assert (config.stop_atr_multiple, config.trailing_stop_atr, config.take_profit_pct, config.max_hold_bars, config.position_pct) == (None, 3.0, 5.0, 40, 50.0)
    assert result.trades[0].reason == "target"
