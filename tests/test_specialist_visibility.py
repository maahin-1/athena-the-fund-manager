import json

import pytest

from athena.agents.base import Specialist, SpecialistError, SpecialistSpec

REPLY_A = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric a is 42.5."})
WIDE = {
    "instrument": "X",
    "sector": "Technology",
    "metrics": {"a": {"value": 42.5}, "b": {"value": 7.25}, "c": {"value": 9.5}},
    "missing": ["z"],
    "missing_reasons": {"z": "why"},
}


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def test_a_specialist_that_declares_shows_is_only_shown_those_figures_plus_the_packet_level_facts():
    spec = SpecialistSpec("Test", "You are a test analyst.", critical=("a",), optional=("b",), shows=("a",))
    llm = Scripted(REPLY_A)
    Specialist(spec, llm).analyze(WIDE)
    prompt = llm.calls[0][1]
    assert "42.5" in prompt and "7.25" not in prompt and "9.5" not in prompt
    assert '"sector": "Technology"' in prompt and '"z"' not in prompt  # unshown figures vanish from missing, too


def test_grounding_is_checked_against_what_the_model_was_shown():
    spec = SpecialistSpec("Test", "p", critical=("a",), shows=("a",))
    cites_a_hidden_figure = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "Metric b is 7.25."})
    with pytest.raises(SpecialistError) as caught:
        Specialist(spec, Scripted(cites_a_hidden_figure)).analyze(WIDE)
    assert "figures not found in the data" in str(caught.value)


def test_without_shows_the_whole_packet_is_shown_as_before():
    spec = SpecialistSpec("Test", "p", critical=("a",), optional=("b",))
    llm = Scripted(REPLY_A)
    Specialist(spec, llm).analyze(WIDE)
    assert "9.5" in llm.calls[0][1]


def test_figures_marked_not_applicable_are_neither_required_nor_missing_and_the_prompt_says_so():
    spec = SpecialistSpec("Test", "p", critical=("a", "z"), optional=("b",))
    packet = {"metrics": {"a": {"value": 42.5}}, "not_applicable": ["z", "b"]}
    llm = Scripted(REPLY_A)
    out = Specialist(spec, llm).analyze(packet)
    assert (out["data_coverage"], out["missing"]) == ("full", [])
    assert "not a data gap" in llm.calls[0][1] and "z, b" in llm.calls[0][1]


def test_a_missing_figure_that_is_not_marked_not_applicable_still_counts_against_coverage():
    spec = SpecialistSpec("Test", "p", critical=("a",), optional=("b",))
    out = Specialist(spec, Scripted(REPLY_A)).analyze({"metrics": {"a": {"value": 42.5}}, "not_applicable": []})
    assert (out["data_coverage"], out["missing"]) == ("partial", ["b"])


def test_the_system_prompt_explains_how_to_read_the_units():
    llm = Scripted(REPLY_A)
    Specialist(SpecialistSpec("Test", "You are a test analyst.", critical=("a",)), llm).analyze(WIDE)
    system = llm.calls[0][0]
    assert "0.25 means 25 percent" in system and "percentage points" in system and "plain multiple" in system
