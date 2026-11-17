import json

import pytest

from athena.council import debate, evidence, judge
from test_council_evidence import sheet as make_sheet


class Script:
    """A model that replies from a list, one reply per call, and remembers what it was asked."""

    def __init__(self, *replies):
        self.replies, self.asked = list(replies), []

    def complete(self, system, user):
        self.asked.append(user)
        return self.replies.pop(0)


def side(case, evidence_ids=("F1",), weakest="The trend is up (T2)."):
    return json.dumps({"case": case, "evidence": list(evidence_ids), "weakest_point": weakest})


def test_no_debate_is_held_when_the_analysts_do_not_conflict():
    plain = {**make_sheet(), "conflict": None}
    assert debate.hold(plain, Script()) is None


def test_each_side_argues_once_from_the_sheet_and_cites_ids():
    llm = Script(side("P/E is 21 (F1), dear for this growth."), side("RSI is 61.2 (T2): momentum is intact.", ("T2",), "P/E is 21 (F1)."))
    got = debate.hold(make_sheet(), llm)
    assert got["bull"]["case"].startswith("P/E is 21") and got["bear"]["evidence"] == ["T2"] and "failed" not in got["bull"]
    assert "EVIDENCE SHEET for TCS" in llm.asked[0] and "strongest honest case that the stock will beat" in llm.asked[0] and "lag the market" in llm.asked[1]


def test_a_case_citing_an_id_or_a_figure_that_is_not_on_the_sheet_is_sent_back_once_then_recorded_as_failed():
    llm = Script(side("Margins are 45% (F9)."), side("Still wrong, 99.5 times (F9)."), side("Fine: P/E is 21 (F1)."), side("P/E is 21 (F1)."))
    got = debate.hold(make_sheet(), llm)
    assert "failed" in got["bull"] and "F9" in got["bull"]["failed"] and "rejected" in llm.asked[1]
    assert got["bear"]["case"] == "Fine: P/E is 21 (F1)." and len(llm.asked) == 3


def test_a_case_with_a_wrong_shape_or_too_long_is_rejected():
    assert debate._check("not json", make_sheet())[0] is None
    assert debate._check(json.dumps({"case": "x"}), make_sheet())[0] is None
    long = side(" ".join(["word"] * 200))
    assert "over 160 words" in debate._check(long, make_sheet())[1][0]


BAD_CITATIONS = [None, "F1", [], [None], [1], ["F1", None], ["F9"], ["F1, T2"], [["F1"]], {"id": "F1"}, ["f1"], [True]]


@pytest.mark.parametrize("citations", BAD_CITATIONS, ids=repr)
def test_a_debate_case_with_missing_malformed_or_unknown_citations_is_rejected_not_raised(citations):
    # A4: null used to raise TypeError out of the whole council; an empty list used to pass as "cited".
    reply = json.dumps({"case": "The trend is up.", "evidence": citations, "weakest_point": "The trend is up (T2)."})
    parsed, errors = debate._check(reply, make_sheet())
    assert parsed is None and errors and any("evidence" in e or "not on the sheet" in e for e in errors)


@pytest.mark.parametrize("field,value", [("case", None), ("case", ["x"]), ("case", ""), ("weakest_point", None), ("weakest_point", 5), ("weakest_point", "  ")])
def test_a_debate_case_with_non_text_or_empty_prose_is_rejected(field, value):
    reply = json.dumps({"case": "P/E is 21 (F1).", "evidence": ["F1"], "weakest_point": "P/E is 21 (F1).", field: value})
    parsed, errors = debate._check(reply, make_sheet())
    assert parsed is None and errors


def test_a_malformed_case_is_sent_back_once_and_a_repaired_reply_survives():
    bad = json.dumps({"case": "Up.", "evidence": None, "weakest_point": "Down (F1)."})
    llm = Script(bad, side("P/E is 21 (F1)."))
    got = debate.argue("bull", make_sheet(), llm)
    assert "failed" not in got and got["evidence"] == ["F1"] and "evidence" in llm.asked[1]  # the retry told the model what was wrong


def test_one_side_that_never_cites_is_recorded_failed_and_the_other_side_still_stands():
    bad = json.dumps({"case": "Up.", "evidence": [], "weakest_point": "Down (F1)."})
    got = debate.hold(make_sheet(), Script(bad, bad, side("RSI is 61.2 (T2).", ("T2",), "P/E is 21 (F1)."), ))
    assert "failed" in got["bull"] and "cite" in got["bull"]["failed"] and "failed" not in got["bear"]


def verdict(stance="Hold", conviction="same", days=20, why="The P/E is 21 (F1).", relied=("F1",)):
    return json.dumps({"stance": stance, "why": why, "relied_on": list(relied), "would_change_my_mind": "Earnings growth above the P/E (F1).", "conviction": conviction, "look_again_days": days})


def test_the_judge_keeps_the_blends_conviction_unless_it_asks_to_lower_it_and_cannot_raise_it():
    s = make_sheet()  # the blend: Hold, conviction 20
    same = judge.decide(s, None, Script(verdict("Overweight")))
    assert same["stance"] == "Overweight" and same["conviction"] == 20 and same["limited"] is None and same["blend_rating"] == "Hold"
    lowered = judge.decide(s, None, Script(verdict("Hold", "lower")))
    assert lowered["conviction"] == 15
    higher = json.dumps({**json.loads(verdict()), "conviction": "higher"})
    assert "conviction must be" in judge.decide(s, None, Script(higher, higher))["failed"]  # raising it is not an option; the reply is sent back once, then recorded as failed


def test_a_stance_more_than_one_step_from_the_blend_is_pulled_back_and_says_so():
    s = make_sheet()
    got = judge.decide(s, None, Script(verdict("Sell")))
    assert got["stance"] == "Underweight" and got["asked_for"] == "Sell" and "held to Underweight" in got["limited"]
    assert judge.limit("Buy", "Overweight") == ("Buy", None) and judge.limit("Sell", "Buy")[0] == "Overweight"


def test_the_judge_is_shown_the_debate_and_its_citations_are_checked():
    s = make_sheet()
    cases = {"bull": {"case": "RSI is 61.2 (T2).", "evidence": ["T2"], "weakest_point": "P/E is 21 (F1)."}, "bear": {"failed": "could not be made to cite the sheet"}}
    llm = Script(verdict(why="Used F9."), verdict())
    got = judge.decide(s, cases, llm)
    assert "BULL CASE: RSI is 61.2 (T2)." in llm.asked[0] and "BEAR CASE: could not be made" in llm.asked[0] and "F9" in llm.asked[1] and got["stance"] == "Hold"
    assert judge.decide(s, None, Script("nope", "still nope"))["failed"]


JUDGE_BAD_CITATIONS = [None, "F1", [None], [1], ["F9"], ["F1, T2"], [["F1"]], {"id": "F1"}]


@pytest.mark.parametrize("relied", JUDGE_BAD_CITATIONS, ids=repr)
def test_a_judge_reply_with_malformed_or_unknown_citations_is_rejected_not_raised(relied):
    reply = json.dumps({**json.loads(verdict("Overweight")), "relied_on": relied})
    parsed, errors = judge._check(reply, make_sheet())
    assert parsed is None and errors
    assert "failed" in judge.decide(make_sheet(), None, Script(reply, reply))  # the council records the failure; nothing is raised


@pytest.mark.parametrize("stance,conviction", [("Overweight", "same"), ("Hold", "lower")])
def test_a_changed_stance_or_lowered_conviction_needs_citations(stance, conviction):
    reply = json.dumps({**json.loads(verdict(stance, conviction)), "relied_on": []})
    assert judge._check(reply, make_sheet())[0] is None
    assert "failed" in judge.decide(make_sheet(), None, Script(reply, reply))


def test_no_change_and_no_citations_is_accepted_only_as_insufficient_support():
    s = make_sheet()  # the blend: Hold, conviction 20
    reply = json.dumps({**json.loads(verdict("Hold", "same")), "relied_on": []})
    got = judge.decide(s, None, Script(reply))
    assert "failed" not in got and got["support"] == "insufficient" and got["stance"] == "Hold" and got["conviction"] == 20
    cited = judge.decide(s, None, Script(verdict("Overweight")))
    assert cited["support"] == "cited"  # presence of a valid citation, not a check that the cited fact supports the argument


@pytest.mark.parametrize("field,value", [("stance", None), ("stance", ["Hold"]), ("why", None), ("why", 7), ("would_change_my_mind", None), ("look_again_days", True), ("look_again_days", None), ("look_again_days", 20.5), ("conviction", None)])
def test_a_judge_reply_with_wrong_types_is_rejected_not_raised(field, value):
    reply = json.dumps({**json.loads(verdict()), field: value})
    parsed, errors = judge._check(reply, make_sheet())
    assert parsed is None and errors


def test_a_judge_that_writes_far_more_than_the_word_limit_is_rejected():
    reply = json.dumps({**json.loads(verdict()), "why": " ".join(["word"] * 200)})
    assert "over 120 words" in " ".join(judge._check(reply, make_sheet())[1])


def test_the_ids_in_the_test_sheet_are_the_ones_used_here():
    assert evidence.fact_ids(make_sheet()) == {"T1", "T2", "F1", "N1"}


def test_the_debaters_and_the_judge_are_told_how_to_read_the_valuation_models_ratio():
    from athena.council import debate, judge

    for system in (debate.SYSTEM, judge.SYSTEM):
        assert "price_to_model_base_vs_market" in system and "typical company" in system
