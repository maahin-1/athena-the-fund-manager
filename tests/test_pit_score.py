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


def _only_case(runs):
    result = score(runs, "quant_technical", 63)
    return result


def test_opposing_votes_with_no_neutral_run_do_not_crash_and_the_synthetic_neutral_has_zero_confidence():
    # Q6: bullish and bearish tie -> neutral, but no run gave neutral, so there is no confidence row to average.
    for n in (1, 2):  # two opposing votes, then four evenly opposing votes
        runs = [call("A", "bullish", "inline", 90, run=i) for i in range(n)] + [call("A", "bearish", "inline", 90, run=n + i) for i in range(n)]
        result = _only_case(runs)
        assert result.cases == 1 and result.abstained == 0
        assert result.mix["neutral"] == 1.0 and result.confusion == {("neutral", "neutral"): 1}
        # a synthetic neutral committed to nothing: it must not count as a high-confidence call
        assert result.high_confidence_calls == 0 and result.high_confidence_accuracy is None
        assert result.agreement == 0.0  # the runs disagreed: one disagreeing case out of one repeated case


def test_an_observed_neutral_majority_keeps_its_own_average_confidence():
    runs = [call("A", "neutral", "inline", 80, run=0), call("A", "neutral", "inline", 60, run=1), call("A", "bullish", "inline", 95, run=2)]
    result = _only_case(runs)
    assert result.cases == 1 and result.high_confidence_calls == 1  # neutral confidence (80+60)/2 = 70 meets the bar


def test_a_tie_that_includes_an_observed_neutral_uses_the_neutral_runs_confidence_not_zero():
    runs = [call("A", "bullish", "inline", 90, run=0), call("A", "neutral", "inline", 75, run=1)]
    result = _only_case(runs)
    assert result.mix["neutral"] == 1.0 and result.high_confidence_calls == 1


def test_a_three_way_tie_is_a_neutral_call_with_the_observed_neutral_confidence():
    runs = [call("A", "bullish", "inline", 90, run=0), call("A", "neutral", "inline", 72, run=1), call("A", "bearish", "inline", 90, run=2)]
    result = _only_case(runs)
    assert result.mix["neutral"] == 1.0 and result.high_confidence_calls == 1 and result.agreement == 0.0


def test_all_abstained_and_a_single_run_keep_their_counts():
    result = score([call("A", None, "inline"), call("A", None, "inline", run=1)], "quant_technical", 63)
    assert (result.cases, result.abstained, result.agreement) == (0, 1, None)
    single = _only_case([call("A", "bearish", "underperform", 55)])
    assert (single.cases, single.abstained, single.agreement) == (1, 0, None) and single.accuracy == 1.0


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
