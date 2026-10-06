import math
import re
from dataclasses import replace

import numpy as np
import pytest
import talib
from bar_factory import make_bars

from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import (
    MAX_INDICATORS,
    OVERLAY,
    PANE,
    REGISTRY,
    Selected,
    compute,
    default_params,
    default_selection,
    next_id,
    problem,
    short_history,
    validate,
)
from athena.technicals.packet import build_technical_packet

CLOSES = [100.0 + i * 0.5 + (3 if i % 7 == 0 else 0) - (2 if i % 11 == 0 else 0) for i in range(400)]
BARS = [
    replace(bar, open=bar.close - ((i % 3) - 1) * 0.4, volume=1000.0 + (i % 5) * 100)
    for i, bar in enumerate(make_bars(CLOSES, symbol="SBIN"))
]  # an open that differs from the close, and a volume that moves
CANDLES = candles_from_bars(BARS)
CLOSE = np.array([c.close for c in CANDLES])
HIGH = np.array([c.high for c in CANDLES])
LOW = np.array([c.low for c in CANDLES])


def chosen(key, **params):
    return Selected(f"{key}-1", key, {**default_params(key), **params})


def values(key, line=0, **params):
    return compute(CANDLES, chosen(key, **params))[line].values


def test_every_registry_entry_is_well_formed_and_its_defaults_are_valid():
    assert len(REGISTRY) >= 16
    for key, spec in REGISTRY.items():
        assert spec.key == key and spec.label and spec.placement in (OVERLAY, PANE)
        for param in spec.params:
            assert param.minimum <= param.default <= param.maximum, (key, param.name)
        assert problem(chosen(key)) is None, key


def test_every_indicator_gives_lines_aligned_with_the_candles_with_a_warm_up_of_none():
    for key in REGISTRY:
        lines = compute(CANDLES, chosen(key))
        assert lines and all(len(line.values) == len(CANDLES) for line in lines), key
        for line in lines:
            assert all(v is None or (isinstance(v, float) and math.isfinite(v)) for v in line.values), (key, line.name)
            assert any(v is not None for v in line.values), (key, line.name)
        if key not in ("obv", "sar"):
            assert lines[0].values[0] is None, key  # a moving window has no value on the first bar


def test_moving_averages_match_ta_lib_and_use_the_chosen_length():
    assert values("sma", length=30)[-1] == pytest.approx(talib.SMA(CLOSE, 30)[-1])
    assert values("ema", length=21)[-1] == pytest.approx(talib.EMA(CLOSE, 21)[-1])
    assert values("wma", length=10)[-1] == pytest.approx(talib.WMA(CLOSE, 10)[-1])
    assert compute(CANDLES, chosen("sma", length=30))[0].name == "SMA 30"
    assert values("sma", length=30)[28] is None and values("sma", length=30)[29] is not None


def test_bollinger_bands_use_both_settings_and_name_them():
    upper, lower = compute(CANDLES, chosen("bbands", length=15, width=2.5))
    ta_upper, _, ta_lower = talib.BBANDS(CLOSE, 15, 2.5, 2.5)
    assert upper.values[-1] == pytest.approx(ta_upper[-1]) and lower.values[-1] == pytest.approx(ta_lower[-1])
    assert upper.name == "Bollinger upper (15, 2.5)" and upper.style == "dot"


def test_the_high_low_channel_is_the_highest_high_and_lowest_low_of_the_last_n_bars_including_today():
    high, low = compute(CANDLES, chosen("channel", length=252))
    assert high.values[250] is None and high.values[251] == max(HIGH[:252])
    assert high.values[-1] == max(HIGH[-252:]) and low.values[-1] == min(LOW[-252:])
    assert high.name == "High 252" and low.name == "Low 252"


def test_momentum_indicators_match_ta_lib_and_carry_their_reference_levels():
    assert values("rsi", length=10)[-1] == pytest.approx(talib.RSI(CLOSE, 10)[-1])
    macd, signal, histogram = compute(CANDLES, chosen("macd", fast=8, slow=21, signal=5))
    ta_macd, ta_signal, ta_hist = talib.MACD(CLOSE, 8, 21, 5)
    assert macd.values[-1] == pytest.approx(ta_macd[-1]) and signal.values[-1] == pytest.approx(ta_signal[-1])
    assert histogram.values[-1] == pytest.approx(ta_hist[-1]) and histogram.style == "bars"
    slow_k, slow_d = compute(CANDLES, chosen("stoch", k=10, smooth=3, d=3))
    ta_k, ta_d = talib.STOCH(HIGH, LOW, CLOSE, fastk_period=10, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    assert slow_k.values[-1] == pytest.approx(ta_k[-1]) and slow_d.values[-1] == pytest.approx(ta_d[-1])
    assert REGISTRY["rsi"].levels == (30.0, 70.0) and REGISTRY["rsi"].y_range == (0.0, 100.0)


def test_the_default_selection_is_what_the_dashboard_always_drew_and_agrees_with_the_technical_packet():
    selection = default_selection()
    assert [(item.key, dict(item.params)) for item in selection] == [
        ("sma", {"length": 50}), ("sma", {"length": 200}), ("bbands", {"length": 20, "width": 2.0}), ("rsi", {"length": 14}),
    ]
    assert [item.id for item in selection] == ["sma-1", "sma-2", "bbands-1", "rsi-1"]
    metrics = build_technical_packet("SBIN", BARS[-1].as_of, BARS)["metrics"]
    sma_50, sma_200, bands, rsi = (compute(CANDLES, item) for item in selection)
    assert sma_50[0].values[-1] == pytest.approx(metrics["sma_50"]["value"], abs=1e-3)
    assert sma_200[0].values[-1] == pytest.approx(metrics["sma_200"]["value"], abs=1e-3)
    assert rsi[0].values[-1] == pytest.approx(metrics["rsi_14"]["value"], abs=1e-3)
    pct_b = (CLOSE[-1] - bands[1].values[-1]) / (bands[0].values[-1] - bands[1].values[-1])
    assert pct_b == pytest.approx(metrics["bollinger_pct_b"]["value"], abs=1e-3)


@pytest.mark.parametrize(
    "item, expected",
    [
        (Selected("x-1", "nope", {}), "unknown indicator 'nope'"),
        (Selected("x-1", "sma", {"size": 5}), "has no setting called 'size'"),
        (Selected("x-1", "sma", {"length": 1}), "Length must be between 2 and 400"),
        (Selected("x-1", "sma", {"length": 401}), "Length must be between 2 and 400"),
        (Selected("x-1", "sma", {"length": 50.5}), "Length must be a whole number"),
        (Selected("x-1", "sma", {"length": float("nan")}), "Length must be a number"),
        (Selected("x-1", "sma", {"length": True}), "Length must be a number"),
        (Selected("x-1", "sma", {"length": "50"}), "Length must be a number"),
        (Selected("x-1", "bbands", {"width": 9.0}), "Width (std dev) must be between 0.5 and 4"),
        (Selected("x-1", "macd", {"fast": 30, "slow": 20}), "the fast length must be shorter than the slow length"),
        (Selected("x-1", "macd", {"fast": 26, "slow": 26}), "the fast length must be shorter than the slow length"),
    ],
)
def test_a_choice_that_cannot_be_drawn_says_why(item, expected):
    assert expected in problem(item)
    with pytest.raises(ValueError, match=re.escape(expected)):
        compute(CANDLES, item)


def test_a_setting_left_out_falls_back_to_its_default_and_boundaries_are_allowed():
    assert problem(Selected("x-1", "sma", {})) is None
    assert problem(Selected("x-1", "sma", {"length": 2})) is None and problem(Selected("x-1", "sma", {"length": 400})) is None
    assert values("sma", length=50)[-1] == compute(CANDLES, Selected("x-1", "sma", {}))[0].values[-1]


def test_validate_keeps_the_drawable_choices_and_gives_a_reason_for_each_other_one():
    selection = [chosen("sma"), Selected("sma-2", "sma", {"length": 1}), Selected("zzz-1", "zzz", {}), chosen("rsi")]
    valid, problems = validate(selection)
    assert [item.id for item in valid] == ["sma-1", "rsi-1"]
    assert set(problems) == {"sma-2", "zzz-1"} and "between 2 and 400" in problems["sma-2"]


def test_validate_limits_the_number_of_indicators_and_repeated_ids():
    many = [Selected(f"sma-{n}", "sma", {"length": 5 + n}) for n in range(MAX_INDICATORS + 2)]
    valid, problems = validate(many)
    assert len(valid) == MAX_INDICATORS == 8 and set(problems) == {"sma-8", "sma-9"}
    assert "at most 8 indicators" in problems["sma-8"]
    valid, problems = validate([chosen("sma"), chosen("sma")])
    assert len(valid) == 1 and "already used" in problems["sma-1"]


def test_ids_are_the_smallest_unused_and_a_selection_round_trips_as_plain_data():
    selection = default_selection()
    assert next_id(selection, "sma") == "sma-3" and next_id(selection, "macd") == "macd-1"
    assert next_id([item for item in selection if item.id != "sma-1"], "sma") == "sma-1"
    item = chosen("macd", fast=10)
    assert Selected.from_dict(item.to_dict()) == item


def test_a_history_too_short_for_the_settings_is_named_not_hidden():
    short = candles_from_bars(make_bars(CLOSES[:150]))
    assert short_history(short, [chosen("sma", length=50), chosen("sma", length=200)]) == ["SMA 200"]
    assert short_history(short, [chosen("sma", length=50)]) == []
