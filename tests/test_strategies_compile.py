import numpy as np
import pytest
import talib
from bar_factory import make_bars

from athena.backtest.rules import ENTRY, EXIT
from athena.strategies.compile import compile_strategy, strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import Selected, compute

CLOSE = {"price": "close"}
EXIT_NEVER = {"op": "gt", "left": CLOSE, "right": {"const": 1e9}}


def candles_of(closes):
    return candles_from_bars(make_bars(closes))


def entries(entry, closes, exit_=EXIT_NEVER):
    """Which bars the entry condition holds on, as a list of indexes."""
    compiled = compile_strategy(parse_strategy({"name": "t", "entry": entry, "exit": exit_}), candles_of(closes))
    return [i for i in range(len(closes)) if compiled.decide(i, False)[0] == ENTRY]


def compare(op, left, right):
    return {"op": op, "left": left, "right": right}


def test_a_comparison_with_a_number_holds_exactly_where_it_is_true():
    assert entries(compare("gt", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [3, 4]
    assert entries(compare("ge", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [2, 3, 4]
    assert entries(compare("lt", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [0, 1]
    assert entries(compare("le", CLOSE, {"const": 3}), [1, 2, 3, 4, 5]) == [0, 1, 2]


def test_a_value_from_some_bars_back_reads_the_past_and_nothing_is_known_before_it_exists():
    closes = [1, 2, 3, 2, 5]
    assert entries(compare("gt", CLOSE, {"price": "close", "shift": 1}), closes) == [1, 2, 4]
    assert entries(compare("gt", CLOSE, {"price": "close", "shift": 3}), closes) == [3, 4]  # nothing to compare with before bar 3


def test_a_rolling_high_includes_today_unless_it_is_shifted_so_a_breakout_needs_the_shift():
    closes = [1, 2, 3, 2, 5]
    window = {"rolling": "max", "of": CLOSE, "window": 3}
    assert entries(compare("ge", CLOSE, window), closes) == [2, 4]  # today is part of its own high
    assert entries(compare("gt", CLOSE, window), closes) == []  # so it can never be strictly above it
    assert entries(compare("gt", CLOSE, {**window, "shift": 1}), closes) == [4]  # the previous 3 bars: a real breakout


def test_a_rolling_low_and_average_are_computed_over_the_window():
    closes = [5, 4, 3, 6, 7, 2]
    assert entries(compare("le", CLOSE, {"rolling": "min", "of": CLOSE, "window": 3}), closes) == [2, 5]
    average = {"rolling": "mean", "of": CLOSE, "window": 2}
    assert entries(compare("gt", CLOSE, average), closes) == [3, 4]


def test_a_cross_happens_only_on_the_bar_it_crosses():
    closes = [1, 3, 5, 3, 5, 5]
    assert entries(compare("crosses_above", CLOSE, {"const": 4}), closes) == [2, 4]
    assert entries(compare("crosses_below", CLOSE, {"const": 4}), closes) == [3]
    assert entries(compare("crosses_above", CLOSE, {"const": 5}), closes) == []  # touching is not crossing


def test_a_moving_average_is_not_available_until_it_has_enough_bars_and_never_triggers_before():
    closes = [10.0] * 60
    assert entries(compare("ge", CLOSE, {"ind": "sma", "params": {"length": 50}}), closes) == list(range(49, 60))


def test_not_of_something_not_yet_computable_is_not_a_signal():
    closes = [10.0] * 60
    above = compare("gt", CLOSE, {"ind": "sma", "params": {"length": 50}})
    assert entries({"not": above}, closes) == list(range(49, 60))  # from the first bar the average exists, never before
    assert entries({"not": {"not": above}}, closes) == []


def test_all_and_any_follow_three_valued_logic():
    closes = [10.0] * 60
    known_true = compare("gt", CLOSE, {"const": 1})
    known_false = compare("lt", CLOSE, {"const": 1})
    unknown = compare("gt", CLOSE, {"ind": "sma", "params": {"length": 50}, "shift": 1})
    assert entries({"all": [known_true, unknown]}, closes) == []  # equal to the average: not above it
    assert entries({"any": [known_true, unknown]}, closes) == list(range(60))  # one true is enough, even if the other is unknown
    assert entries({"any": [known_false, unknown]}, closes) == []
    assert entries({"not": {"all": [known_false, unknown]}}, closes) == list(range(60))  # one known false makes the whole known false
    assert entries({"not": {"any": [known_true, unknown]}}, closes) == []
    assert entries({"not": {"any": [known_false, known_false]}}, closes) == list(range(60))  # an any is known false when every part is
    assert entries({"not": {"any": [known_false, unknown]}}, closes) == list(range(50, 60))  # not before the average exists
    assert entries({"not": {"all": [known_true, unknown]}}, closes) == list(range(50, 60))  # known only once the average exists


def test_arithmetic_works_and_a_division_by_a_zero_value_is_unknown_not_infinite():
    closes = [2, 4, 6, 8]
    assert entries(compare("gt", {"arith": "mul", "left": CLOSE, "right": {"const": 2}}, {"const": 9}), closes) == [2, 3]
    assert entries(compare("gt", {"arith": "add", "left": CLOSE, "right": {"price": "close", "shift": 1}}, {"const": 9}), closes) == [2, 3]
    assert entries(compare("lt", {"arith": "sub", "left": CLOSE, "right": {"const": 3}}, {"const": 2}), closes) == [0, 1]
    zero = {"arith": "sub", "left": CLOSE, "right": CLOSE}
    assert entries(compare("gt", {"arith": "div", "left": CLOSE, "right": zero}, {"const": -1e9}), closes) == []
    # a division by zero must not be a number that later steps can quietly step over: the lowest of a window holding it is unknown
    step = {"arith": "div", "left": CLOSE, "right": {"arith": "sub", "left": CLOSE, "right": {"price": "close", "shift": 1}}}
    assert entries(compare("gt", {"rolling": "min", "of": step, "window": 2}, {"const": 0}), [2, 2, 4, 8]) == [3]


def test_two_averages_of_different_lengths_are_not_confused_with_each_other():
    closes = [1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 5, 6]
    fast, slow = talib.SMA(np.array(closes, dtype=float), 2), talib.SMA(np.array(closes, dtype=float), 3)
    expected = [i for i in range(1, len(closes)) if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]]
    cross = compare("crosses_above", {"ind": "sma", "params": {"length": 2}}, {"ind": "sma", "params": {"length": 3}})
    assert expected and entries(cross, closes) == expected


def test_an_indicator_line_chosen_by_name_is_that_line_of_the_registry():
    closes = [100.0 + i * 0.4 + (i % 7) for i in range(120)]
    candles = candles_of(closes)
    signal = [v for v in compute(candles, Selected("s-1", "macd", {"fast": 12, "slow": 26, "signal": 9}))[1].values]
    macd = parse_strategy({"name": "t", "entry": compare("gt", {"ind": "macd", "line": "signal"}, {"const": 0}), "exit": EXIT_NEVER})
    compiled = compile_strategy(macd, candles)
    got = [i for i in range(len(closes)) if compiled.decide(i, False)[0] == ENTRY]
    assert got == [i for i, v in enumerate(signal) if v is not None and v > 0]


def test_every_line_of_a_multi_line_indicator_is_reachable_by_name():
    closes = [100.0 + (i % 4) for i in range(60)]
    closes[40] = 70.0  # a drop below the lower band
    candles = candles_of(closes)
    lower = compute(candles, Selected("s-1", "bbands", {"length": 20, "width": 2.0}))[1].values
    expected = [i for i, v in enumerate(lower) if v is not None and closes[i] < v]
    assert expected
    assert entries(compare("lt", CLOSE, {"ind": "bbands", "line": "lower"}), closes) == expected


def test_a_decision_is_entry_or_exit_with_the_atr_for_the_stop():
    closes = [100.0 + i * 0.4 + (i % 5) for i in range(60)]
    candles = candles_of(closes)
    strategy = parse_strategy({"name": "t", "entry": compare("gt", CLOSE, {"const": 110}), "exit": compare("lt", CLOSE, {"const": 110})})
    compiled = compile_strategy(strategy, candles)
    atr = talib.ATR(np.array([c.high for c in candles]), np.array([c.low for c in candles]), np.array([c.close for c in candles]), 14)
    action, value = compiled.decide(40, False)
    assert action == (ENTRY if candles[40].close > 110 else None) and value == pytest.approx(atr[40])
    assert compiled.decide(40, True)[0] == (EXIT if candles[40].close < 110 else None)
    assert compiled.decide(5, False)[1] is None  # ATR is not available on the first bars


def test_the_strategy_rule_carries_its_name_description_and_stop():
    rule = strategy_rule(PRESETS["rsi_reversion"])
    assert rule.name == "RSI mean reversion" and rule.stop_atr == 2.0 and rule.description.startswith("Buy when the RSI")
    assert strategy_rule(PRESETS["breakout_52w"]).stop_atr is None


def walk(count, seed=7):
    rng = np.random.default_rng(seed)
    return list(100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, count))))


COMPLEX = parse_strategy({
    "name": "complex",
    "entry": {"all": [
        {"op": "gt", "left": CLOSE, "right": {"rolling": "max", "of": CLOSE, "window": 40, "shift": 1}},
        {"any": [{"op": "lt", "left": {"ind": "rsi"}, "right": {"const": 70}}, {"not": {"op": "gt", "left": {"ind": "macd", "line": "histogram"}, "right": {"const": 0}}}]},
    ]},
    "exit": {"op": "crosses_below", "left": CLOSE, "right": {"ind": "ema", "params": {"length": 20}, "shift": 1}},
})


@pytest.mark.parametrize("strategy", [*PRESETS.values(), COMPLEX], ids=lambda s: s.name)
def test_a_decision_depends_only_on_the_bars_up_to_that_close(strategy):
    candles = candles_of(walk(500))
    full = compile_strategy(strategy, candles)
    for cut in (260, 333, 480):
        part = compile_strategy(strategy, candles[:cut])
        for holding in (False, True):
            assert [part.decide(i, holding) for i in range(cut)] == [full.decide(i, holding) for i in range(cut)], (cut, holding)


@pytest.mark.parametrize("strategy", [*PRESETS.values(), COMPLEX], ids=lambda s: s.name)
def test_wild_later_bars_cannot_change_an_earlier_decision(strategy):
    closes = walk(500)
    candles = candles_of(closes)
    garbage = candles_of(closes[:300] + [c * 7 + (i % 5) * 40 for i, c in enumerate(closes[300:])])
    full, other = compile_strategy(strategy, candles), compile_strategy(strategy, garbage)
    for holding in (False, True):
        assert [other.decide(i, holding) for i in range(300)] == [full.decide(i, holding) for i in range(300)]
