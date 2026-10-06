from athena.strategies.describe import INDICATOR_NAMES, describe
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.technicals.indicators import REGISTRY

CLOSE = {"price": "close"}


def strategy(entry, exit_=None, **extra):
    return parse_strategy({"name": "t", "entry": entry, "exit": exit_ or {"op": "lt", "left": CLOSE, "right": {"const": 1}}, **extra})


def test_the_fifty_two_week_example_reads_as_the_sentence_a_person_would_say():
    assert describe(PRESETS["breakout_52w"]) == (
        "Buy when the close is at or above the highest close over the last 252 bars, as of 1 bar ago. "
        "Sell when the close is at or below the lowest close over the last 252 bars, as of 1 bar ago. No protective stop."
    )


def test_a_crossover_and_a_stop_are_described():
    text = describe(PRESETS["golden_cross"])
    assert "Buy when the simple moving average (length 50) crosses above the simple moving average (length 200)." in text
    assert "Sell when the simple moving average (length 50) crosses below" in text
    assert describe(PRESETS["rsi_reversion"]).endswith("Stop out if the price falls 2 x ATR below the entry fill.")


def test_and_or_not_and_arithmetic_are_spelled_out_with_brackets_where_they_nest():
    entry = {"all": [
        {"op": "gt", "left": CLOSE, "right": {"ind": "ema", "params": {"length": 20}, "shift": 3}},
        {"any": [{"op": "lt", "left": {"ind": "rsi"}, "right": {"const": 40}}, {"not": {"op": "ge", "left": {"arith": "div", "left": CLOSE, "right": {"const": 2}}, "right": {"price": "open"}}}]},
    ]}
    assert describe(strategy(entry)).startswith(
        "Buy when the close is above the exponential moving average (length 20) 3 bars ago and "
        "(the RSI (length 14) is below 40 or it is not true that (the close / 2) is at or above the open)."
    )


def test_a_multi_line_indicator_names_the_line_and_a_single_condition_has_no_brackets():
    text = describe(strategy({"op": "gt", "left": {"ind": "macd", "line": "signal"}, "right": {"const": 0}}))
    assert text.startswith("Buy when the MACD (fast 12, slow 26, signal 9) signal line is above 0.")
    single = describe(strategy({"all": [{"op": "gt", "left": CLOSE, "right": {"const": 1}}]}))
    assert single.startswith("Buy when the close is above 1. Sell when")


def test_every_indicator_has_a_plain_name_for_the_sentence():
    assert set(INDICATOR_NAMES) == set(REGISTRY)
