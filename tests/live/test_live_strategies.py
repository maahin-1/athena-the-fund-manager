import pytest

from athena.backtest.cli import analyze, live_world
from athena.strategies.compile import strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS

pytestmark = pytest.mark.live

MINE = {
    "name": "Close above the 20-day average",
    "entry": {"op": "crosses_above", "left": {"price": "close"}, "right": {"ind": "sma", "params": {"length": 20}}},
    "exit": {"op": "crosses_below", "left": {"price": "close"}, "right": {"ind": "sma", "params": {"length": 20}}},
    "stop_atr": 2,
}


@pytest.fixture(scope="module")
def world():
    return live_world(years=8)


def test_live_every_preset_runs_on_a_real_stock_and_reads_only_the_price_pattern(world):
    rules = [strategy_rule(strategy) for strategy in PRESETS.values()]
    text, code = analyze(world, "SBIN", rules)
    print("\n" + text)
    assert code == 0
    for strategy in PRESETS.values():
        assert f"rule: {strategy.name}" in text
    assert "buy and hold" in text and "15 bps per side" in text
    assert "DIFFERENT" not in text  # the presets use ratios and indicators, never a price level


def test_live_a_person_written_strategy_runs_next_to_a_built_in_rule(world):
    text, code = analyze(world, "RELIANCE", ["trend", strategy_rule(parse_strategy(MINE))])
    print("\n" + text)
    assert code == 0 and "rule: trend" in text and "rule: Close above the 20-day average" in text
    assert "Buy when the close crosses above the simple moving average (length 20)." in text
    assert "Prices adjusted for 1 split or bonus event" in text  # the adjustment still applies to a strategy
