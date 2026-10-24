import json
from datetime import timedelta

from test_council_debate_judge import Script
from test_verdicts_runner import NOW, Analyst, database

from athena.council.run import ALWAYS, Council
from athena.verdicts.calibration import Calibration
from athena.verdicts.runner import VerdictRunner


def people(valuation_signal="bearish"):
    out = {name: Analyst("bullish", 80) for name in ("quant_technical", "moat_quality", "earnings_intelligence")}
    out["valuation"] = Analyst(valuation_signal, 80)
    return out


def case(text):
    return json.dumps({"case": text, "evidence": ["T1"], "weakest_point": "The last close is 100 (T1)."})


JUDGE = json.dumps({"stance": "Hold", "why": "The last close is 100 (T1).", "relied_on": ["T1"], "would_change_my_mind": "A close above 100 (T1).", "conviction": "same", "look_again_days": 20})


def runner(path, council, specialists=None):
    return VerdictRunner(path, specialists or people(), Calibration(), lambda: NOW + timedelta(minutes=1), model="fake", council=council)


def test_on_a_conflict_the_council_debates_and_judges_beside_the_blend_which_stays_the_verdict(tmp_path):
    path = database(tmp_path)
    debaters, judge_llm = Script(case("Up from 100 (T1)."), case("Dear at 100 (T1).")), Script(JUDGE)
    stored = runner(path, Council(debaters, judge_llm, "judge-model")).make("TCS")
    payload = stored["payload"]
    council = payload["council"]
    assert payload["decision"]["type"] == "REVIEW" and council["judge_model"] == "judge-model"
    assert council["debate"]["bull"]["case"] == "Up from 100 (T1)." and council["judge"]["stance"] == "Hold"
    assert "T1 last_close = 100" in judge_llm.asked[0] and "BULL CASE: Up from 100 (T1)." in judge_llm.asked[0]
    assert payload["verdict"]["verdict"] in ("Overweight", "Hold", "Buy")  # the blend's own rating, untouched by the council


def test_without_a_conflict_the_council_is_not_called_unless_it_is_asked_for_every_time(tmp_path):
    path = database(tmp_path)
    agree = {name: Analyst("bullish", 80) for name in ("quant_technical", "valuation", "moat_quality", "earnings_intelligence")}
    quiet_judge = Script()
    assert runner(path, Council(Script(), quiet_judge), agree).make("TCS")["payload"]["council"] is None
    assert quiet_judge.asked == []
    asked = runner(path, Council(Script(), Script(JUDGE), "j", ALWAYS), agree).make("TCS")["payload"]["council"]
    assert asked["debate"] is None and asked["judge"]["blend_rating"] == "Buy"
    assert asked["judge"]["stance"] == "Overweight" and "held to Overweight" in asked["judge"]["limited"]  # Hold is two steps from Buy: pulled back to one


def test_a_council_that_fails_is_recorded_and_the_blend_still_stands(tmp_path):
    class Broken:
        def complete(self, system, user):
            raise RuntimeError("provider down")

    payload = runner(database(tmp_path), Council(Broken(), Broken())).make("TCS")["payload"]
    assert "provider down" in payload["council"]["failed"] and payload["verdict"]["verdict"]
