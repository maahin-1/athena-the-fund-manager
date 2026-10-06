from dataclasses import replace

import pytest
from bar_factory import NOW, make_bars

from athena.backtest.engine import Config, decide, run_backtest, same_decisions
from athena.backtest.rules import RULES, Rule
from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import candles_from_bars

ALWAYS_IN = Rule("always", "enter at the first chance and never leave", lambda p: True, lambda p: False)
NEVER = Rule("never", "never trade", lambda p: False, lambda p: False)
IN_OUT = Rule("inout", "enter when flat, leave when long", lambda p: True, lambda p: True)
SMALL = Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=0.0)


def climbing(count=60, step=1.0, base=100.0):
    return make_bars([base + i * step for i in range(count)])


def with_opens(bars, offset):
    """The same bars with every open `offset` below its close, so that an open is never mistaken for a close."""
    return [Bar(b.symbol, b.timestamp, b.close - offset, b.high, b.low, b.close, b.volume, NOW, "t") for b in bars]


def test_a_decision_at_one_close_is_filled_at_the_next_open():
    bars = climbing()
    candles = candles_from_bars(bars)
    result = run_backtest(bars, IN_OUT, SMALL)
    assert result.trades[0].entry_price == candles[SMALL.warmup_bars + 1].open  # decided at the close of bar 20, filled at 21's open
    held = run_backtest(bars, ALWAYS_IN, SMALL)
    assert held.in_market[0] is False and held.in_market[1] is True  # the evaluation window's first bar is still flat


def test_a_rule_that_never_trades_leaves_cash_untouched_and_buy_and_hold_still_runs():
    result = run_backtest(climbing(), NEVER, SMALL)
    assert set(result.equity) == {100.0} and result.trades == () and not any(result.in_market)
    assert result.benchmark[-1] > 100.0 and len(result.benchmark) == len(result.equity) == len(result.days)


def test_buy_and_hold_starts_at_the_first_open_of_the_window_with_no_cost_when_costs_are_zero():
    bars = climbing()
    candles = candles_from_bars(bars)
    result = run_backtest(bars, NEVER, SMALL)
    assert result.benchmark[-1] == pytest.approx(100.0 * candles[-1].close / candles[SMALL.warmup_bars].open)


def test_costs_are_charged_on_both_sides_of_a_round_trip():
    flat = make_bars([100.0] * 60)
    free = run_backtest(flat, IN_OUT, SMALL)
    costly = run_backtest(flat, IN_OUT, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=100.0))
    assert free.equity[-1] == pytest.approx(100.0)
    assert costly.equity[-1] < 100.0 * (1 - 0.01) ** 2 * 1.0001 and costly.trades[0].net_return == pytest.approx(0.99 / 1.01 - 1, abs=1e-9)


def test_an_open_position_is_liquidated_at_the_end_with_the_exit_cost():
    result = run_backtest(climbing(), ALWAYS_IN, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=50.0))
    candles = candles_from_bars(climbing())
    shares = 100.0 / (candles[21].open * 1.005)
    assert result.equity[-1] == pytest.approx(shares * candles[-1].close * 0.995)
    assert result.trades == ()  # still open: only closed trades are listed


def test_a_protective_stop_fills_at_the_stop_price_when_the_day_trades_through_it():
    closes = [100.0] * 40 + [100.0, 100.0, 100.0, 100.0, 100.0]
    bars = make_bars(closes)  # every bar spans 2 points, so ATR is 2 and the stop sits 2 x 2 = 4 below the entry open
    crash = list(bars)
    day = crash[30]
    crash[30] = Bar(day.symbol, day.timestamp, 100.0, 100.5, 90.0, 95.0, 1000.0, NOW, "t")  # trades down through the stop
    result = run_backtest(crash, ALWAYS_IN, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=2.0, cost_bps_per_side=0.0))
    stopped = [t for t in result.trades if t.reason == "stop"][0]
    assert stopped.exit_price == pytest.approx(stopped.entry_price - 4.0, abs=0.01) and stopped.net_return < 0


def test_a_gap_down_through_the_stop_fills_at_the_open_not_the_stop():
    bars = make_bars([100.0] * 45)
    gap = list(bars)
    day = gap[30]
    gap[30] = Bar(day.symbol, day.timestamp, 80.0, 81.0, 79.0, 80.0, 1000.0, NOW, "t")
    result = run_backtest(gap, ALWAYS_IN, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=2.0, cost_bps_per_side=0.0))
    stopped = [t for t in result.trades if t.reason == "stop"][0]
    assert stopped.exit_price == 80.0


def test_a_signal_exit_is_filled_at_the_next_open_and_recorded_as_a_signal():
    result = run_backtest(climbing(), IN_OUT, SMALL)
    candles = candles_from_bars(climbing())
    first = result.trades[0]
    assert first.reason == "signal" and first.entry_price == candles[21].open and first.exit_price == candles[22].open


def test_the_engine_cannot_see_the_future_a_prefix_run_matches_the_longer_run():
    closes = [100 + i * 0.25 + (6 if i % 17 == 0 else 0) - (8 if i % 29 == 0 else 0) for i in range(400)]
    bars = make_bars(closes)
    config = Config(warmup_bars=330, window_bars=520)
    long_run = run_backtest(bars, RULES["trend"], config)
    cut = 380
    short_run = run_backtest(bars[:cut], RULES["trend"], config)
    keep = len(short_run.equity) - 1  # the last point of a shorter run includes its forced liquidation
    assert short_run.equity[:keep] == long_run.equity[:keep]
    closed_early = [t for t in short_run.trades if t.exit_day < short_run.days[-1]]
    assert closed_early == [t for t in long_run.trades if t.exit_day < short_run.days[-1]]


def test_runs_are_deterministic():
    bars = climbing(80)
    assert run_backtest(bars, IN_OUT, SMALL) == run_backtest(bars, IN_OUT, SMALL)


def test_too_few_bars_and_repeated_days_are_rejected():
    with pytest.raises(InsufficientData, match="needs more than"):
        run_backtest(climbing(21), ALWAYS_IN, SMALL)
    doubled = climbing(40) + climbing(40)
    with pytest.raises(InsufficientData, match="repeated trading days"):
        run_backtest(doubled, ALWAYS_IN, SMALL)


@pytest.mark.parametrize("index,field", [(SMALL.warmup_bars, "open"), (SMALL.warmup_bars + 5, "open"), (3, "close"), (40, "close")])
def test_a_non_positive_price_is_refused_instead_of_dividing_by_it(index, field):
    bars = climbing()
    bars[index] = replace(bars[index], **{field: 0.0})
    with pytest.raises(InsufficientData, match="non-positive prices"):
        run_backtest(bars, ALWAYS_IN, SMALL)


def test_the_result_carries_the_rule_name_config_and_window():
    bars = climbing()
    result = run_backtest(bars, NEVER, SMALL)
    assert result.rule == "never" and result.config is SMALL
    assert result.days[0] == candles_from_bars(bars)[20].day and result.days[-1] == candles_from_bars(bars)[-1].day


def test_a_decision_depends_only_on_the_bars_up_to_that_close():
    closes = [100 + i * 0.25 + (6 if i % 17 == 0 else 0) - (8 if i % 29 == 0 else 0) for i in range(420)]
    bars = make_bars(closes)
    for index in (340, 365, 390):
        garbage = bars[: index + 1] + [
            Bar(b.symbol, b.timestamp, b.open * 7, b.high * 7, b.low * 7, b.close * 7, b.volume * 3, NOW, "t")
            for b in bars[index + 1 :]
        ]  # every later bar is wildly different
        for holding in (False, True):
            assert decide(bars, index, RULES["trend"], holding, 520) == decide(garbage, index, RULES["trend"], holding, 520)


def test_fills_happen_at_the_open_of_the_next_bar_never_at_a_close():
    bars = with_opens(climbing(), 0.5)
    candles = candles_from_bars(bars)
    assert candles[21].open != candles[21].close
    held = run_backtest(bars, ALWAYS_IN, SMALL)
    assert held.equity[-1] == pytest.approx(100.0 / candles[21].open * candles[-1].close)
    first = run_backtest(bars, IN_OUT, SMALL).trades[0]
    assert first.entry_price == candles[21].open and first.exit_price == candles[22].open


def test_buy_and_hold_pays_the_entry_cost_at_the_start_and_the_exit_cost_only_at_the_end():
    bars = with_opens(climbing(), 0.5)
    candles = candles_from_bars(bars)
    result = run_backtest(bars, NEVER, Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=100.0))
    bought = 100.0 / (candles[20].open * 1.01)
    assert result.benchmark[-1] == pytest.approx(bought * candles[-1].close * 0.99)
    assert result.benchmark[-2] == pytest.approx(bought * candles[-2].close)


def test_two_runs_count_as_the_same_decisions_only_if_days_trades_reasons_and_returns_all_match():
    base = run_backtest(climbing(80), IN_OUT, SMALL)
    assert len(base.trades) >= 2 and same_decisions(base, base)
    nudged = replace(base, trades=tuple(replace(t, net_return=t.net_return + 5e-5) for t in base.trades))
    assert same_decisions(base, nudged)  # within the default tolerance of 1e-4
    off = replace(base, trades=tuple(replace(t, net_return=t.net_return + 1e-3) for t in base.trades))
    assert not same_decisions(base, off)
    assert not same_decisions(base, replace(base, trades=base.trades[:-1]))
    assert not same_decisions(base, replace(base, trades=(replace(base.trades[0], reason="stop"), *base.trades[1:])))
    assert not same_decisions(base, replace(base, in_market=tuple(not held for held in base.in_market)))
