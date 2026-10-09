import pytest

from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output

GOOD = {"signal": "bullish", "confidence": 72, "reasoning": "Beta 1.10.", "data_coverage": "full", "missing": []}
GOOD_VERDICT = {"verdict": "Overweight", "conviction": 64, "key_risks": ["rates"], "resolution_path": "blend"}


def variant(**changes):
    return {**GOOD, **changes}


def test_valid_outputs_have_no_errors():
    assert validate_specialist_output(GOOD) == []
    assert validate_specialist_output(variant(data_coverage="partial", missing=["manager_tenure"])) == []
    assert validate_specialist_output(variant(signal="neutral", confidence=0, data_coverage="insufficient", missing=["nav"])) == []


def test_non_objects_are_rejected():
    assert validate_specialist_output("bullish") == ["output must be an object"]
    assert validate_judge_verdict(None) == ["verdict must be an object"]


def test_missing_and_unknown_keys_are_reported():
    bad = {k: v for k, v in GOOD.items() if k != "missing"}
    assert "missing keys: ['missing']" in validate_specialist_output(bad)
    assert "unknown keys: ['target_price']" in validate_specialist_output(variant(target_price=10))


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"signal": "buy"}, "signal must be one of"),
        ({"confidence": 101}, "confidence must be an integer 0-100"),
        ({"confidence": 72.5}, "confidence must be an integer 0-100"),
        ({"confidence": True}, "confidence must be an integer 0-100"),
        ({"reasoning": "  "}, "reasoning must be a non-empty string"),
        ({"data_coverage": "mostly"}, "data_coverage must be one of"),
        ({"missing": "nav"}, "missing must be a list of strings"),
    ],
)
def test_field_level_errors(changes, fragment):
    errors = validate_specialist_output(variant(**changes))
    assert any(fragment in error for error in errors), errors


def test_coverage_rules_between_label_and_missing_list():
    assert any("empty missing list" in e for e in validate_specialist_output(variant(missing=["nav"])))
    assert any("must list what is missing" in e for e in validate_specialist_output(variant(data_coverage="partial")))


def test_insufficient_coverage_requires_an_abstention():
    errors = validate_specialist_output(variant(data_coverage="insufficient", missing=["nav"]))
    assert any("requires an abstention" in e for e in errors)


def test_valid_verdicts():
    assert validate_judge_verdict(GOOD_VERDICT) == []
    debate = {**GOOD_VERDICT, "resolution_path": "debate", "debate_transcript_ref": "d-17", "panel_agreement": 0.67, "contested": False}
    assert validate_judge_verdict(debate) == []


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"verdict": "Strong Buy"}, "verdict must be one of"),
        ({"conviction": -1}, "conviction must be an integer 0-100"),
        ({"key_risks": "rates"}, "key_risks must be a list of strings"),
        ({"resolution_path": "vote"}, "resolution_path must be one of"),
        ({"resolution_path": "debate"}, "needs a debate_transcript_ref"),
        ({"panel_agreement": 1.5}, "panel_agreement must be a number between 0 and 1"),
        ({"contested": "no"}, "contested must be a boolean"),
    ],
)
def test_verdict_errors(changes, fragment):
    errors = validate_judge_verdict({**GOOD_VERDICT, **changes})
    assert any(fragment in error for error in errors), errors


FINDING = {"check": "volatility", "status": "ok", "value": 0.1, "warn": 0.2, "hard": 0.3, "message": "fine"}


def test_a_held_back_verdict_with_findings_is_valid():
    held = {**GOOD_VERDICT, "verdict": "Hold", "pre_overlay_verdict": "Buy", "risk_findings": [{**FINDING, "status": "breach"}, FINDING]}
    assert validate_judge_verdict(held) == []
    assert validate_judge_verdict({**GOOD_VERDICT, "risk_findings": []}) == []


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"risk_findings": [{"check": "volatility", "status": "ok"}]}, "risk_findings[0] missing keys: ['message']"),
        ({"risk_findings": [FINDING, {**FINDING, "status": "fine"}]}, "risk_findings[1].status must be one of ['ok', 'warn', 'breach', 'unchecked'], got 'fine'"),
        ({"verdict": "Hold", "pre_overlay_verdict": "Sell"}, "pre_overlay_verdict must be Buy or Overweight, got 'Sell'"),
        ({"verdict": "Buy", "pre_overlay_verdict": "Buy"}, "a verdict held back from Buy must be Hold, got 'Buy'"),
    ],
)
def test_risk_overlay_errors(changes, fragment):
    errors = validate_judge_verdict({**GOOD_VERDICT, **changes})
    assert fragment in errors, errors


def test_verdict_missing_key():
    bad = {k: v for k, v in GOOD_VERDICT.items() if k != "conviction"}
    assert "missing keys: ['conviction']" in validate_judge_verdict(bad)
