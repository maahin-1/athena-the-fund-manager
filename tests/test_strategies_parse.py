import copy

import pytest
from bar_factory import make_bars

from athena.strategies.model import LINE_NAMES, MAX_DEPTH, MAX_NODES, AllOf, Compare, Const, IndicatorRef, Not, Price, Rolling
from athena.strategies.parse import StrategyError, parse_strategy, to_dict
from athena.strategies.presets import PRESET_DATA, PRESETS
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import REGISTRY, Selected, compute, default_params

CLOSE = {"price": "close"}
BASIC = {"name": "Basic", "entry": {"op": "gt", "left": CLOSE, "right": {"const": 100}}, "exit": {"op": "lt", "left": CLOSE, "right": {"const": 90}}}


def broken(path, value):
    """BASIC with one part replaced; `path` is a list of keys from the top."""
    data = copy.deepcopy(BASIC)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


def test_a_minimal_strategy_parses_with_a_default_name_no_stop_and_the_version_accepted():
    data = {"version": 1, "entry": BASIC["entry"], "exit": BASIC["exit"]}
    strategy = parse_strategy(data)
    assert strategy.name == "Custom strategy" and strategy.stop_atr is None
    assert strategy.entry == Compare("gt", Price("close"), Const(100.0))
    assert parse_strategy(BASIC).name == "Basic" and parse_strategy({**BASIC, "stop_atr": 2}).stop_atr == 2.0


def test_an_indicator_gets_its_missing_settings_filled_in_and_a_line_by_name_or_number():
    strategy = parse_strategy({"entry": {"op": "gt", "left": {"ind": "macd", "line": "signal", "params": {"fast": 8}}, "right": {"const": 0}}, "exit": BASIC["exit"]})
    ref = strategy.entry.left
    assert isinstance(ref, IndicatorRef) and ref.line == 1
    assert dict(ref.params) == {"fast": 8, "slow": 26, "signal": 9} and ref.params == tuple(sorted(ref.params))
    assert parse_strategy({"entry": {"op": "gt", "left": {"ind": "macd", "line": 2}, "right": {"const": 0}}, "exit": BASIC["exit"]}).entry.left.line == 2


def test_every_preset_parses_and_turns_back_into_equal_data():
    assert set(PRESETS) == set(PRESET_DATA) and len(PRESETS) == 4
    for key, strategy in PRESETS.items():
        assert parse_strategy(to_dict(strategy)) == strategy, key
    names = [strategy.name for strategy in PRESETS.values()]
    assert len(set(names)) == len(names)


def test_a_complex_strategy_round_trips_through_plain_data():
    data = {
        "name": "Complex", "stop_atr": 1.5,
        "entry": {"all": [
            {"op": "crosses_above", "left": {"ind": "sma", "params": {"length": 20}, "shift": 1}, "right": {"ind": "ema", "params": {"length": 50}}},
            {"any": [{"op": "lt", "left": {"ind": "rsi"}, "right": {"const": 40}}, {"not": {"op": "gt", "left": {"arith": "div", "left": CLOSE, "right": {"rolling": "mean", "of": CLOSE, "window": 30}}, "right": {"const": 1.2}}}]},
        ]},
        "exit": {"op": "le", "left": CLOSE, "right": {"rolling": "min", "of": {"price": "low"}, "window": 10, "shift": 2}},
    }
    strategy = parse_strategy(data)
    assert isinstance(strategy.entry, AllOf) and isinstance(strategy.entry.items[1].items[1], Not)
    assert parse_strategy(to_dict(strategy)) == strategy
    assert to_dict(parse_strategy(to_dict(strategy))) == to_dict(strategy)


@pytest.mark.parametrize(
    "data, expected",
    [
        ("not an object", "strategy: must be an object"),
        ({**BASIC, "extra": 1}, "strategy: unexpected key 'extra'"),
        ({"name": "x", "exit": BASIC["exit"]}, "strategy: missing 'entry'"),
        ({"name": "x", "entry": BASIC["entry"]}, "strategy: missing 'exit'"),
        ({**BASIC, "version": 2}, "only version 1 is supported"),
        ({**BASIC, "name": ""}, "strategy.name: must be text of 1 to 60 characters"),
        ({**BASIC, "name": "x" * 61}, "strategy.name: must be text of 1 to 60 characters"),
        ({**BASIC, "name": 5}, "strategy.name: must be text"),
        ({**BASIC, "stop_atr": 0}, "strategy.stop_atr: must be above 0 and at most 10"),
        ({**BASIC, "stop_atr": 11}, "strategy.stop_atr: must be above 0 and at most 10"),
        ({**BASIC, "stop_atr": "2"}, "strategy.stop_atr: must be a number"),
        ({**BASIC, "stop_atr": True}, "strategy.stop_atr: must be a number"),
        (broken(["entry"], {"op": "gt", "all": []}), "entry: must have exactly one of"),
        (broken(["entry"], {}), "entry: must have exactly one of"),
        (broken(["entry"], {"op": "gt", "left": CLOSE, "right": CLOSE, "zzz": 1}), "entry: unexpected key 'zzz'"),
        (broken(["entry", "op"], "equals"), "entry.op: must be one of gt, ge, lt, le, crosses_above, crosses_below"),
        (broken(["entry"], {"op": "gt", "left": CLOSE}), "entry: missing 'right'"),
        (broken(["entry"], {"all": []}), "entry.all: must be a list of 1 to 8 conditions"),
        (broken(["entry"], {"all": [BASIC["entry"]] * 9}), "entry.all: must be a list of 1 to 8 conditions"),
        (broken(["entry"], {"not": 5}), "entry.not: must be an object"),
        (broken(["entry", "left"], {"price": "vwap"}), "entry.left.price: must be one of open, high, low, close, volume"),
        (broken(["entry", "left"], {"price": "close", "shift": -1}), "entry.left.shift: must be between 0 and 500"),
        (broken(["entry", "left"], {"price": "close", "shift": 501}), "entry.left.shift: must be between 0 and 500"),
        (broken(["entry", "left"], {"price": "close", "shift": 1.5}), "entry.left.shift: must be a whole number"),
        (broken(["entry", "left"], {"price": "close", "colour": "red"}), "entry.left: unexpected key 'colour'"),
        (broken(["entry", "left"], {"price": "close", "const": 1}), "entry.left: must have exactly one of"),
        (broken(["entry", "left"], {"const": "5"}), "entry.left.const: must be a number"),
        (broken(["entry", "left"], {"const": float("inf")}), "entry.left.const: must be a number"),
        (broken(["entry", "left"], {"const": True}), "entry.left.const: must be a number"),
        (broken(["entry", "left"], {"ind": "nope"}), "entry.left.ind: unknown indicator 'nope'"),
        (broken(["entry", "left"], {"ind": "sma", "params": {"length": 1}}), "entry.left.params: Length must be between 2 and 400"),
        (broken(["entry", "left"], {"ind": "sma", "params": {"size": 5}}), "entry.left.params: Simple moving average has no setting called 'size'"),
        (broken(["entry", "left"], {"ind": "macd", "params": {"fast": 30, "slow": 20}}), "the fast length must be shorter than the slow length"),
        (broken(["entry", "left"], {"ind": "sma", "params": [1]}), "entry.left.params: must be an object"),
        (broken(["entry", "left"], {"ind": "sma", "line": 1}), "entry.left.line: must be between 0 and 0"),
        (broken(["entry", "left"], {"ind": "macd", "line": "histo"}), "entry.left.line: macd has the lines macd, signal, histogram"),
        (broken(["entry", "left"], {"rolling": "median", "of": CLOSE, "window": 5}), "entry.left.rolling: must be one of max, min, mean"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE}), "a rolling value needs 'of' and 'window'"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE, "window": 1}), "entry.left.window: must be between 2 and 1000"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE, "window": 1001}), "entry.left.window: must be between 2 and 1000"),
        (broken(["entry", "left"], {"arith": "pow", "left": CLOSE, "right": CLOSE}), "entry.left.arith: must be one of add, sub, mul, div"),
        (broken(["entry", "left"], {"arith": "add", "left": CLOSE}), "entry.left: missing 'right'"),
        (broken(["entry", "left"], {"arith": "div", "left": CLOSE, "right": {"const": 0}}), "entry.left.right: cannot divide by zero"),
    ],
)
def test_a_malformed_strategy_is_refused_with_the_path_of_the_wrong_part(data, expected):
    with pytest.raises(StrategyError) as caught:
        parse_strategy(data)
    assert expected in str(caught.value)


def test_a_strategy_error_is_a_value_error_so_callers_can_catch_either():
    assert issubclass(StrategyError, ValueError)


def test_nesting_and_size_are_bounded():
    nested = BASIC["entry"]
    for _ in range(MAX_DEPTH):
        nested = {"not": nested}
    with pytest.raises(StrategyError, match="nested more than 8 levels deep"):
        parse_strategy({**BASIC, "entry": nested})
    leaf = BASIC["entry"]
    wide = {"all": [{"any": [leaf] * 8}] * 8}  # 8 x 8 comparisons of 3 parts each: far over the limit
    with pytest.raises(StrategyError, match=f"more than {MAX_NODES} parts"):
        parse_strategy({**BASIC, "entry": wide})
    deep_expr = CLOSE
    for _ in range(MAX_DEPTH):
        deep_expr = {"arith": "add", "left": deep_expr, "right": {"const": 1}}
    with pytest.raises(StrategyError, match="nested more than 8 levels deep"):
        parse_strategy({**BASIC, "entry": {"op": "gt", "left": deep_expr, "right": {"const": 1}}})


def test_the_line_names_match_what_the_registry_computes_for_every_indicator():
    candles = candles_from_bars(make_bars([100.0 + i * 0.3 + (i % 5) for i in range(400)]))
    assert set(LINE_NAMES) == set(REGISTRY)
    for key in REGISTRY:
        lines = compute(candles, Selected("s-1", key, default_params(key)))
        assert len(lines) == len(LINE_NAMES[key]), key


def test_a_rolling_value_can_be_built_over_another_value_such_as_an_indicator():
    strategy = parse_strategy({**BASIC, "entry": {"op": "gt", "left": CLOSE, "right": {"rolling": "max", "of": {"ind": "rsi"}, "window": 5}}})
    assert isinstance(strategy.entry.right, Rolling) and isinstance(strategy.entry.right.of, IndicatorRef)


HUGE = 10**400  # an integer no float can hold


@pytest.mark.parametrize(
    "data, where",
    [
        (broken(["entry", "left"], {"ind": []}), "entry.left.ind"),
        (broken(["entry", "left"], {"ind": {}}), "entry.left.ind"),
        (broken(["entry", "right"], {"const": HUGE}), "entry.right.const"),
        (broken(["entry", "left"], {"price": "close", "shift": HUGE}), "entry.left.shift"),
        (broken(["entry", "left"], {"rolling": "max", "of": CLOSE, "window": HUGE}), "entry.left.window"),
        (broken(["entry", "left"], {"ind": "macd", "line": HUGE}), "entry.left.line"),
        (broken(["entry", "left"], {"ind": "sma", "params": {"length": HUGE}}), "entry.left.params"),
        ({**BASIC, "stop_atr": HUGE}, "strategy.stop_atr"),
    ],
)
def test_input_no_float_can_hold_or_no_name_can_be_is_refused_with_a_message_naming_the_part(data, where):
    with pytest.raises(StrategyError, match=where):
        parse_strategy(data)
