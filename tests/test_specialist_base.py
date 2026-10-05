import json

import pytest

from athena.agents.base import Specialist, SpecialistError, SpecialistSpec, parse_model_json

SPEC = SpecialistSpec("Test", "You are a test analyst.", critical=("a",), optional=("b",))
PACKET = {"instrument": "X", "as_of": "2026-10-05T04:00:00+00:00", "metrics": {"a": {"value": 42.5}, "b": {"value": 7.25}}, "missing": []}


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


GOOD = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5 and b is 7.25."})
GOOD_A_ONLY = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5."})


def test_parse_model_json_handles_fences_and_surrounding_text():
    assert parse_model_json('```json\n{"x": 1}\n```') == {"x": 1}
    assert parse_model_json('Here you go: {"x": 1} done') == {"x": 1}
    with pytest.raises(ValueError):
        parse_model_json("no braces here")
    with pytest.raises(ValueError):
        parse_model_json("{not json}")


def test_valid_reply_becomes_a_full_contract_output():
    out = Specialist(SPEC, Scripted(GOOD)).analyze(PACKET)
    assert out == {"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5 and b is 7.25.", "data_coverage": "full", "missing": []}


def test_system_prompt_carries_persona_and_contract_and_user_prompt_carries_data():
    llm = Scripted(GOOD)
    Specialist(SPEC, llm).analyze(PACKET)
    system, user = llm.calls[0]
    assert "You are a test analyst." in system and "one JSON object" in system
    assert "42.5" in user and "Specialist: Test" in user


def test_missing_critical_input_abstains_without_calling_the_model():
    llm = Scripted(GOOD)
    out = Specialist(SPEC, llm).analyze({"metrics": {"b": {"value": 1}}})
    assert llm.calls == []
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")
    assert out["missing"] == ["a"] and "(a)" in out["reasoning"]


def test_missing_optional_input_is_partial_and_the_prompt_says_so():
    llm = Scripted(GOOD_A_ONLY)
    out = Specialist(SPEC, llm).analyze({"metrics": {"a": {"value": 42.5}}})
    assert (out["data_coverage"], out["missing"]) == ("partial", ["b"])
    assert "PARTIAL" in llm.calls[0][1] and "Missing: b" in llm.calls[0][1]


def test_model_cannot_choose_its_own_coverage_label():
    sneaky = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "a is 42.5.", "data_coverage": "full", "missing": []})
    llm = Scripted(sneaky, GOOD_A_ONLY)
    out = Specialist(SPEC, llm).analyze({"metrics": {"a": {"value": 42.5}}})
    assert len(llm.calls) == 2 and "does not allow" in llm.calls[1][1]
    assert out["data_coverage"] == "partial"


def test_ungrounded_figure_is_rejected_with_feedback_then_accepted():
    invented = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 99.9."})
    llm = Scripted(invented, GOOD)
    out = Specialist(SPEC, llm).analyze(PACKET)
    assert out["reasoning"].startswith("Metric a is 42.5")
    assert "99.9" in llm.calls[1][1] and "rejected" in llm.calls[1][1]


def test_invalid_contract_values_are_rejected():
    bad = json.dumps({"signal": "buy", "confidence": 70, "reasoning": "a is 42.5."})
    with pytest.raises(SpecialistError) as caught:
        Specialist(SPEC, Scripted(bad)).analyze(PACKET)
    assert "signal must be one of" in str(caught.value)


def test_unparseable_reply_fails_loud_by_default_and_quiet_on_request():
    with pytest.raises(SpecialistError):
        Specialist(SPEC, Scripted("I refuse")).analyze(PACKET)
    out = Specialist(SPEC, Scripted("I refuse"), fail_quiet=True).analyze(PACKET)
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")
    assert out["missing"] == ["valid_model_output"]


def test_identical_requests_are_served_from_cache_and_copies_are_independent():
    llm = Scripted(GOOD)
    specialist = Specialist(SPEC, llm)
    first = specialist.analyze(PACKET)
    first["missing"].append("tampered")
    second = specialist.analyze(PACKET)
    assert specialist.model_calls == 1 and second["missing"] == []
    specialist.analyze({"metrics": {"a": {"value": 42.5}, "b": {"value": 7.25}}, "instrument": "Y"})
    assert specialist.model_calls == 2  # different data, different key
