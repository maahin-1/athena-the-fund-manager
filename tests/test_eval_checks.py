from athena.evaluation.checks import check_abstention, check_order_invariance

FULL = {"nav": [1.0, 2.0], "ter": 0.9, "manager_tenure": 5}


def honest(inputs):
    missing = [name for name in ("nav", "ter") if name not in inputs]
    optional_missing = [name for name in ("manager_tenure",) if name not in inputs]
    if missing:
        return {"signal": "neutral", "confidence": 0, "reasoning": "Too little data.", "data_coverage": "insufficient", "missing": missing + optional_missing}
    if optional_missing:
        return {"signal": "bullish", "confidence": 60, "reasoning": "Cost looks low.", "data_coverage": "partial", "missing": optional_missing}
    return {"signal": "bullish", "confidence": 70, "reasoning": "Cost looks low.", "data_coverage": "full", "missing": []}


def bluffer(inputs):
    return {"signal": "bullish", "confidence": 90, "reasoning": "Looks great.", "data_coverage": "full", "missing": []}


def test_an_honest_specialist_passes_abstention_checks():
    assert check_abstention(honest, FULL, critical=["nav", "ter"], optional=["manager_tenure"]) == []


def test_a_specialist_that_bluffs_is_caught_for_every_removed_input():
    failures = check_abstention(bluffer, FULL, critical=["nav", "ter"], optional=["manager_tenure"])
    assert len(failures) == 3 * 2  # wrong coverage + not named in missing, for each of 3 inputs
    assert any("without 'nav'" in f and "expected coverage 'insufficient'" in f for f in failures)
    assert any("without 'manager_tenure'" in f and "does not name it" in f for f in failures)


def test_invalid_output_is_reported_not_raised():
    failures = check_abstention(lambda inputs: {"signal": "buy"}, FULL, critical=["nav"])
    assert len(failures) == 1 and "invalid output" in failures[0]


def test_a_stable_judge_passes_order_invariance():
    judge = lambda parts: {"verdict": "Buy" if "bull" in parts else "Hold"}  # noqa: E731
    assert check_order_invariance(judge, ["bull", "bear", "risk"]) == []


def test_a_judge_swayed_by_who_speaks_first_is_caught():
    judge = lambda parts: {"verdict": "Buy" if parts[0] == "bull" else "Sell"}  # noqa: E731
    failures = check_order_invariance(judge, ["bull", "bear"])
    assert len(failures) == 1 and "flipped from 'Buy' to 'Sell'" in failures[0]


def test_orderings_are_capped():
    calls = []
    judge = lambda parts: calls.append(1) or {"verdict": "Hold"}  # noqa: E731
    check_order_invariance(judge, list("abcdef"), max_orderings=4)
    assert len(calls) == 4
