from dataclasses import replace

import numpy as np
import pytest
from bar_factory import NOW, make_bars

from athena.backtest.cli import assumptions_for, format_runs, run_rules
from athena.backtest.engine import Config, run_backtest
from athena.backtest.rules import RULES
from athena.contracts import Bar
from athena.strategies.compile import strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.technicals.candles import candles_from_bars
from athena.trading_calendar import ist_date

CLOSE = {"price": "close"}
SMALL = Config(warmup_bars=20, window_bars=60, stop_atr_multiple=None, cost_bps_per_side=0.0)


def strategy(entry, exit_, **extra):
    return strategy_rule(parse_strategy({"name": "t", "entry": entry, "exit": exit_, **extra}))


def above(level):
    return {"op": "gt", "left": CLOSE, "right": {"const": level}}


def below(level):
    return {"op": "lt", "left": CLOSE, "right": {"const": level}}


def with_opens(bars, offset):
    return [Bar(b.symbol, b.timestamp, b.close - offset, b.high, b.low, b.close, b.volume, NOW, "t") for b in bars]


def walk(count, seed=11, start=100.0, drift=0.0006):
    rng = np.random.default_rng(seed)
    return list(start * np.exp(np.cumsum(rng.normal(drift, 0.012, count))))


def market(bars):
    days = [ist_date(bar.timestamp) for bar in bars]
    return {day: 100.0 * 1.0002**i for i, day in enumerate(days)}, {day: 1000.0 + i for i, day in enumerate(days)}


def test_a_strategy_decides_at_a_close_and_is_filled_at_the_next_open():
    closes = [100.0 + i * 0.5 for i in range(60)] + [130.0 - i for i in range(60)]
    bars = with_opens(make_bars(closes), 0.5)
    candles = candles_from_bars(bars)
    entry_bar = next(i for i in range(20, 120) if closes[i] > 110)
    exit_bar = next(i for i in range(entry_bar + 1, 120) if closes[i] < 110)
    result = run_backtest(bars, strategy(above(110), below(110)), SMALL)
    trade = result.trades[0]
    assert trade.entry_price == candles[entry_bar + 1].open and trade.exit_price == candles[exit_bar + 1].open
    assert trade.reason == "signal" and candles[entry_bar + 1].open != candles[entry_bar + 1].close


def test_a_strategy_chooses_its_own_stop_not_the_runs_default():
    closes = [100.0] * 60
    bars = make_bars(closes)
    crash = list(bars)
    day = crash[40]
    crash[40] = Bar(day.symbol, day.timestamp, 100.0, 100.5, 80.0, 99.0, 1000.0, NOW, "t")  # trades down through a 2 x ATR stop
    with_stop = run_backtest(crash, strategy(above(50), below(50), stop_atr=2.0), replace(SMALL, stop_atr_multiple=None))
    without = run_backtest(crash, strategy(above(50), below(50)), replace(SMALL, stop_atr_multiple=2.0))
    assert with_stop.config.stop_atr_multiple == 2.0 and [t.reason for t in with_stop.trades] == ["stop"]
    assert without.config.stop_atr_multiple is None and without.trades == ()  # still holding: the run's default stop was not applied


def test_the_packet_rules_still_use_the_runs_stop_setting():
    bars = make_bars(walk(400))
    assert run_backtest(bars, RULES["trend"], Config(stop_atr_multiple=1.5)).config.stop_atr_multiple == 1.5


def test_run_rules_runs_names_and_strategies_together_each_with_its_own_description():
    bars = make_bars(walk(700), symbol="SBIN")
    rate, index = market(bars)
    runs = run_rules(bars, ["trend", strategy_rule(PRESETS["golden_cross"])], rate, index)
    assert [run.summary.rule for run in runs] == ["trend", "Golden cross"]
    assert runs[0].description == RULES["trend"].description and runs[1].description.startswith("Buy when the simple moving average")
    assert all(isinstance(run.blinded_identical, bool) for run in runs)


def test_an_unknown_rule_name_is_still_refused_before_any_run():
    bars = make_bars(walk(400))
    rate, index = market(bars)
    with pytest.raises(ValueError, match="unknown rule 'magic'"):
        run_rules(bars, [strategy_rule(PRESETS["golden_cross"]), "magic"], rate, index)


def test_the_blinding_check_flags_a_strategy_that_depends_on_the_price_level_but_not_one_that_uses_ratios():
    bars = make_bars([120.0 * 1.003**i for i in range(700)], symbol="SBIN")  # starts at 120, so blinding really rescales it
    rate, index = market(bars)
    level = strategy(above(300), below(250))
    ratio = strategy({"op": "gt", "left": CLOSE, "right": {"arith": "mul", "left": {"ind": "sma", "params": {"length": 50}}, "right": {"const": 1.02}}}, below(1))
    runs = run_rules(bars, [replace(level, name="level"), replace(ratio, name="ratio")], rate, index)
    assert [run.blinded_identical for run in runs] == [False, True]


def test_assumptions_name_each_stop_only_when_the_rules_use_different_ones():
    bars = make_bars(walk(700), symbol="SBIN")
    rate, index = market(bars)
    same = run_rules(bars, [strategy(above(100), below(100), stop_atr=2.0)], rate, index)
    assert "no protective stop" not in assumptions_for(same) and "depends on the rule" not in assumptions_for(same)
    mixed = run_rules(bars, ["trend", strategy(above(100), below(100))], rate, index)
    text = assumptions_for(mixed)
    assert "a stop that depends on the rule (trend: a stop 2 x ATR below the entry fill; t: no protective stop)" in text
    assert "Assumed costs 15 bps per side" in text and assumptions_for([]).startswith("Assumed costs")


def test_the_report_prints_each_strategy_description_and_a_blinding_verdict_per_rule():
    bars = make_bars(walk(700), symbol="SBIN")
    rate, index = market(bars)
    runs = run_rules(bars, [strategy_rule(PRESETS["rsi_reversion"]), strategy_rule(PRESETS["golden_cross"])], rate, index)
    text = format_runs("SBIN", runs)
    assert "Backtest SBIN  rule: RSI mean reversion" in text and "Backtest SBIN  rule: Golden cross" in text
    assert "Buy when the RSI (length 14) is below 30." in text and "RSI mean reversion: " in text and "Golden cross: " in text
    assert "a stop that depends on the rule" in text
