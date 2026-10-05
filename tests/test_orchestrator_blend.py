import pytest

from athena.evaluation.schema import validate_judge_verdict
from athena.orchestrator.blend import (
    CONFLICT_CONVICTION_CAP,
    NO_VIEW_RISK,
    Conflict,
    _verdict_for,
    blend_signals,
    detect_conflict,
)


def out(signal, confidence, coverage="full", missing=()):
    return {"signal": signal, "confidence": confidence, "reasoning": "x", "data_coverage": coverage, "missing": list(missing)}


def blend(outputs, **kwargs):
    result = blend_signals(outputs, **kwargs)
    assert validate_judge_verdict(result.verdict) == [], result.verdict
    assert result.verdict["resolution_path"] == "blend"
    return result


@pytest.mark.parametrize(
    "net, expected",
    [(1.0, "Buy"), (0.6, "Buy"), (0.59, "Overweight"), (0.2, "Overweight"), (0.19, "Hold"), (0.0, "Hold"),
     (-0.19, "Hold"), (-0.2, "Underweight"), (-0.59, "Underweight"), (-0.6, "Sell"), (-1.0, "Sell")],
)
def test_verdict_bands(net, expected):
    assert _verdict_for(net) == expected


def test_one_confident_bullish_specialist_is_a_buy_at_its_own_confidence():
    result = blend({"a": out("bullish", 80)})
    assert (result.verdict["verdict"], result.verdict["conviction"], result.net) == ("Buy", 80, 1.0)
    assert result.verdict["key_risks"] == [] and result.participants == ("a",)


def test_one_neutral_specialist_is_a_hold_at_its_own_confidence():
    result = blend({"a": out("neutral", 35)})
    assert (result.verdict["verdict"], result.verdict["conviction"], result.net) == ("Hold", 35, 0.0)


def test_one_bearish_specialist_is_a_sell():
    assert blend({"a": out("bearish", 70)}).verdict["verdict"] == "Sell"


def test_a_neutral_specialist_dilutes_a_bullish_one():
    result = blend({"a": out("bullish", 100), "b": out("neutral", 100)})
    assert result.net == 0.5 and result.verdict["verdict"] == "Overweight"
    assert result.verdict["conviction"] == 50 and result.conflict is None


def test_partial_coverage_caps_conviction_and_is_named_as_a_risk():
    result = blend({"a": out("bullish", 90, "partial", ["trend_monthly", "sma_200"])})
    assert result.verdict["verdict"] == "Buy" and result.verdict["conviction"] == 70
    assert result.verdict["key_risks"] == ["a: partial data, missing trend_monthly, sma_200"]


def test_partial_coverage_counts_for_less_than_full_when_specialists_disagree():
    result = blend({"a": out("bullish", 100, "full"), "b": out("bearish", 100, "partial", ["x"])})
    assert result.net == pytest.approx(0.1765, abs=1e-4)


def test_an_insufficient_specialist_does_not_take_part_but_is_reported():
    result = blend({"a": out("bullish", 80), "b": out("neutral", 0, "insufficient", ["nav"])})
    assert result.participants == ("a",) and result.verdict["verdict"] == "Buy"
    assert result.verdict["key_risks"] == ["b: not enough data to form a view (missing nav)"]


def test_nobody_with_enough_data_is_a_zero_conviction_hold():
    result = blend({"a": out("neutral", 0, "insufficient", ["nav"])})
    assert result.verdict["verdict"] == "Hold" and result.verdict["conviction"] == 0 and result.participants == ()
    assert result.verdict["key_risks"][0] == NO_VIEW_RISK
    assert blend({}).verdict["key_risks"] == [NO_VIEW_RISK]


def test_opposite_confident_signals_are_a_conflict_and_cap_conviction():
    result = blend({"a": out("bullish", 80), "b": out("bearish", 80)})
    assert result.conflict == Conflict(("a",), ("b",))
    assert result.verdict["verdict"] == "Hold" and result.verdict["conviction"] == CONFLICT_CONVICTION_CAP
    assert "specialists disagree" in result.verdict["key_risks"][0]


def test_a_conflict_needs_both_sides_at_or_above_the_confidence_threshold():
    assert detect_conflict({"a": out("bullish", 80), "b": out("bearish", 59)}) is None
    assert detect_conflict({"a": out("bullish", 80), "b": out("bearish", 60)}) is not None
    assert detect_conflict({"a": out("bullish", 80), "b": out("bearish", 59)}, min_confidence=50) is not None


def test_neutral_never_conflicts_and_insufficient_never_takes_part():
    assert detect_conflict({"a": out("bullish", 90), "b": out("neutral", 90)}) is None
    assert detect_conflict({"a": out("bullish", 90), "b": out("bearish", 90, "insufficient", ["x"])}) is None


def test_conviction_stays_within_zero_and_one_hundred():
    for confidence in (0, 1, 50, 100):
        for signal in ("bullish", "bearish", "neutral"):
            conviction = blend({"a": out(signal, confidence)}).verdict["conviction"]
            assert 0 <= conviction <= 100


def test_a_zero_confidence_participant_gives_a_hold_without_dividing_by_zero():
    result = blend({"a": out("bullish", 0)})
    assert result.net == 0.0 and result.verdict["verdict"] == "Hold"
