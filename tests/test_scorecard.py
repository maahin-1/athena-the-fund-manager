import pytest

from athena.evaluation import scorecard


def row(symbol, day, signal, confidence, excess, analyst="valuation", horizon=63, run=0):
    return {"symbol": symbol, "as_of": day, "specialist": analyst, "signal": signal, "confidence": confidence, "excess": excess, "horizon": horizon, "run": run}


def market(days, skill):
    """Ten stocks a day; with skill the bullish ones beat the benchmark, without it the signal is unrelated to the return."""
    rows = []
    for d in range(days):
        for i in range(10):
            signal = "bullish" if i < 4 else "bearish" if i >= 7 else "neutral"
            noise = ((i * 37 + d * 11 + i * d * 7 + (i * d) ** 2) % 13 - 6) / 200
            excess = noise + (0.05 * scorecard.SIDE[signal] if skill else 0.0)
            rows.append(row(f"S{i}", f"2024-{d + 1:02d}-01", signal, 60 + i, excess))
    return rows


def test_repeated_runs_become_one_case_with_the_most_common_signal_and_a_tie_is_neutral():
    got = scorecard.cases([row("A", "d", "bullish", 80, 0.1), row("A", "d", "bullish", 60, 0.1, run=1), row("A", "d", "bearish", 90, 0.1, run=2), row("B", "d", "bullish", 70, 0.0), row("B", "d", "bearish", 70, 0.0, run=1)])
    by = {c["symbol"]: c for c in got}
    assert by["A"]["signal"] == "bullish" and by["A"]["confidence"] == 70 and by["B"]["signal"] == "neutral"
    assert scorecard.cases([row("A", "d", None, 0, 0.1), row("A", "d2", "bullish", 50, None)]) == []  # an abstention and an unscored call are not cases


def test_a_skilled_analyst_shows_a_spread_a_high_hit_rate_and_a_significant_rank_correlation():
    out = scorecard.build(market(12, skill=True))["valuation"]["63"]
    assert out["cases"] == 120 and out["dates"] == 12 and out["hit_rate"] > 0.9 and out["spread"] == pytest.approx(0.1, abs=0.03)
    assert out["mean_ic"] > 0.5 and out["ic_t"] > scorecard.T_EDGE and "positive" in out["verdict"]
    assert out["calls_by_rating"] == {"bullish": 48, "neutral": 36, "bearish": 36}


def test_an_analyst_whose_calls_mean_nothing_is_not_called_skilled():
    out = scorecard.build(market(12, skill=False))["valuation"]["63"]
    assert abs(out["spread"]) < 0.04 and abs(out["ic_t"]) < scorecard.T_EDGE
    assert out["verdict"].startswith("no evidence of skill")


def test_too_few_calls_or_dates_are_said_not_dressed_up():
    few = scorecard.build([row(f"S{i}", "2024-01-01", "bullish", 70, 0.1) for i in range(5)])["valuation"]["63"]
    assert "too few calls" in few["verdict"] and few["mean_ic"] is None
    one_day = scorecard.build([r for r in market(12, True) if r["as_of"] == "2024-01-01" or r["as_of"] == "2024-02-01"])["valuation"]["63"]
    assert one_day["ic_t"] is None


def test_the_card_is_written_in_one_line_per_analyst_and_horizon():
    text = scorecard.format_card(scorecard.build(market(12, True) + [{**r, "horizon": 126} for r in market(12, True)]))
    assert text.count("\n") == 1 and "valuation 63d" in text and "valuation 126d" in text and "spread +" in text


def test_a_significant_correlation_that_the_spread_contradicts_is_not_called_evidence():
    assert "disagree" in scorecard._verdict(100, 0.5, 0.5, 0.3, 3.0, -0.1)
    assert "positive" in scorecard._verdict(100, 0.5, 0.5, 0.3, 3.0, 0.1) and "no evidence" in scorecard._verdict(100, 0.5, 0.5, 0.3, 1.0, 0.1)
