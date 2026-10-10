import pytest

from athena.evaluation.pit_score import Call, calls_from_rows, format_score, modal, score


def call(symbol, signal, label, confidence=60, run=0, as_of="2026-04-30", horizon=63, specialist="quant_technical"):
    return Call(symbol, as_of, horizon, specialist, run, signal, confidence, label)


CALLS = [
    call("A", "bullish", "outperform", 80),
    call("B", "bullish", "underperform", 75),
    call("C", "bearish", "underperform", 55),
    call("D", "neutral", "inline", 50),
    call("E", "neutral", "outperform", 40),
    call("F", None, "inline"),  # abstained
]


def test_accuracy_counts_an_inline_outcome_as_a_neutral_call_and_the_abstention_is_counted_apart():
    result = score(CALLS, "quant_technical", 63)
    assert (result.cases, result.abstained) == (5, 1) and result.accuracy == pytest.approx(3 / 5)
    assert result.directional == pytest.approx(2 / 3) and result.directional_calls == 3  # A right, B wrong, C right; D and E are neutral
    assert result.high_confidence_accuracy == pytest.approx(1 / 2) and result.high_confidence_calls == 2


def test_the_fixed_answers_it_must_beat_come_from_the_same_cases():
    result = score(CALLS, "quant_technical", 63)
    assert result.outcome_mix == {"bullish": pytest.approx(2 / 5), "neutral": pytest.approx(1 / 5), "bearish": pytest.approx(2 / 5)}
    assert result.mix == {"bullish": pytest.approx(2 / 5), "neutral": pytest.approx(2 / 5), "bearish": pytest.approx(1 / 5)}
    assert result.confusion[("bullish", "bearish")] == 1 and result.confusion[("neutral", "neutral")] == 1


def test_repeated_runs_of_a_case_count_once_by_their_most_common_signal_and_their_agreement_is_reported():
    runs = [call("A", "bullish", "outperform", run=0), call("A", "bullish", "outperform", run=1), call("A", "neutral", "outperform", run=2),
            call("B", "bearish", "underperform", run=0), call("B", "bearish", "underperform", run=1), call("B", "bearish", "underperform", run=2)]
    result = score(runs, "quant_technical", 63)
    assert result.cases == 2 and result.accuracy == 1.0 and result.agreement == pytest.approx(1 / 2)


def test_a_tie_goes_to_the_call_that_commits_to_least():
    assert modal(["bullish", "bearish"]) == "neutral" and modal(["bullish", "neutral"]) == "neutral" and modal(["bullish", "bullish", "bearish"]) == "bullish"
    assert modal(["bearish", "bullish"]) == "neutral"


def test_other_specialists_and_horizons_are_not_mixed_in_and_nothing_gives_none():
    mixed = CALLS + [call("A", "bearish", "outperform", specialist="valuation"), call("A", "bearish", "outperform", horizon=126)]
    assert score(mixed, "quant_technical", 63).cases == 5
    assert score(mixed, "news_sentiment", 63) is None


def test_a_summary_names_the_best_fixed_answer_so_a_model_that_only_says_it_cannot_look_good():
    text = format_score(score(CALLS, "quant_technical", 63))
    assert "quant_technical, 63-day outcome: 5 cases (1 abstained)" in text and "accuracy 60%" in text and "best fixed answer (always bullish) 40%" in text


def test_log_rows_without_an_outcome_label_are_left_out():
    rows = [{"symbol": "A", "as_of": "2026-04-30", "horizon": 63, "specialist": "x", "signal": "bullish", "confidence": 70, "label": "outperform"},
            {"symbol": "B", "as_of": "2026-04-30", "horizon": 63, "specialist": "x", "signal": "bullish", "confidence": 70, "label": None}]
    assert len(calls_from_rows(rows)) == 1
