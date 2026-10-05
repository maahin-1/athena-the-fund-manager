import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import Bar
from athena.technicals.packet import build_technical_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def weekdays(count: int, end: date = date(2026, 10, 2)) -> list[date]:
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def make_bars(closes, volume=1_000.0, spread=1.0):
    bars = []
    for day, close in zip(weekdays(len(closes)), closes):
        stamp = datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1)
        bars.append(Bar("X", stamp, close, close + spread, close - spread, close, volume, NOW, "t"))
    return bars


def packet_for(closes, **kwargs):
    return build_technical_packet("X", NOW, make_bars(closes, **kwargs))


def value(packet, name):
    return packet["metrics"][name]["value"]


UP = [100.0 + i * 0.5 for i in range(400)]  # steady climb
DOWN = [300.0 - i * 0.5 for i in range(400)]


def test_a_steady_uptrend_is_aligned_up_with_every_metric_present():
    packet = packet_for(UP)
    assert packet["missing"] == []
    for frame in ("trend_daily", "trend_weekly", "trend_monthly"):
        assert value(packet, frame) == "up"
    assert value(packet, "trend_alignment") == "aligned_up"
    assert value(packet, "last_close") == UP[-1]
    assert value(packet, "sma_50") == pytest.approx(sum(UP[-50:]) / 50, abs=1e-3)
    assert value(packet, "rsi_14") > 90  # relentless climb
    assert value(packet, "bollinger_pct_b") > 0.5
    assert value(packet, "high_60d") == UP[-1] + 1.0
    assert value(packet, "reward_risk") == pytest.approx(1.0 / (2 * value(packet, "atr_14")), abs=1e-3)


def test_a_steady_downtrend_is_aligned_down():
    packet = packet_for(DOWN)
    assert value(packet, "trend_alignment") == "aligned_down"
    assert value(packet, "rsi_14") < 10
    assert value(packet, "reward_risk") > 2  # far below the 60-day high


def test_mixed_timeframes_are_not_aligned():
    rally_then_drop = UP[:380] + [UP[379] - i * 2.0 for i in range(1, 21)]
    packet = packet_for(rally_then_drop)
    assert value(packet, "trend_daily") != "up"
    assert value(packet, "trend_monthly") != "down"  # the long view has not turned
    assert value(packet, "trend_alignment") == "not_aligned"


def test_short_history_lists_what_cannot_be_computed_with_reasons():
    packet = packet_for(UP[:60])
    assert "trend_monthly" in packet["missing"] and "trend_alignment" in packet["missing"]
    assert "sma_200" in packet["missing"]
    assert "needs" in packet["missing_reasons"]["sma_200"]
    assert value(packet, "trend_daily") == "up"  # what can be computed still is


def test_empty_input_is_all_missing_not_an_error():
    packet = build_technical_packet("X", NOW, [])
    assert packet["metrics"] == {}
    assert "last_close" in packet["missing"] and "reward_risk" in packet["missing"]


def test_zero_volume_marks_volume_ratio_missing():
    packet = packet_for(UP, volume=0.0)
    assert "volume_ratio_20_50" in packet["missing"]
    assert packet["missing_reasons"]["volume_ratio_20_50"] == "no volume data"


def test_volume_ratio_reflects_recent_participation():
    bars = make_bars(UP, volume=100.0)
    bars = bars[:-20] + [Bar(b.symbol, b.timestamp, b.open, b.high, b.low, b.close, 400.0, NOW, "t") for b in bars[-20:]]
    ratio = value(build_technical_packet("X", NOW, bars), "volume_ratio_20_50")
    assert ratio == pytest.approx(400 / ((400 * 20 + 100 * 30) / 50), abs=1e-3)


def test_flat_prices_have_no_percent_b_and_no_reward_risk():
    packet = packet_for([100.0] * 80, spread=0.0)
    assert "bollinger_pct_b" in packet["missing"]
    assert "reward_risk" in packet["missing"]


def test_packet_is_json_ready_and_every_number_is_finite():
    packet = packet_for(UP)
    json.dumps(packet)
    for metric in packet["metrics"].values():
        if not isinstance(metric["value"], str):
            assert math.isfinite(metric["value"])
    assert packet["instrument"] == "X" and packet["as_of"] == NOW.isoformat()
