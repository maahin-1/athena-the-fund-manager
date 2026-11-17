"""The council's figures are bound to the facts it cites (audit A4's semantic continuation, WP05).

Before: a figure had only to appear *somewhere* on the sheet, so a correct number attached to the wrong fact, or with the wrong sign, passed. Now a figure that is the value of a fact on the sheet must
belong to a fact the reply cites, and a figure written with a sign must agree with a cited fact's sign. A reply is still only bound to ids: that a cited fact supports the conclusion is for a person to judge."""
import json

from athena.council import debate, evidence, judge
from athena.evaluation.claims import bound_figure_errors, facts_from_sheet

TECH = {"metrics": {"rsi_14": {"value": 61.2, "unit": "index", "window": "daily to 2026-10-22", "source": "ta-lib"}}}
FUND = {
    "metrics": {
        "pe_trailing": {"value": 21.0, "unit": "ratio", "window": "last 4 quarters", "source": "NSE"},
        "roe": {"value": -0.25, "unit": "fraction", "window": "FY2026", "source": "annual report"},
        "growth": {"value": 0.12, "unit": "fraction", "window": "FY2026", "source": "annual report"},
    },
}
OUTPUTS = {"valuation": {"signal": "bearish", "confidence": 70, "data_coverage": "full", "reasoning": "P/E is 21.", "missing": []}}


def make(outputs=OUTPUTS):
    return evidence.build_sheet("TCS", "2026-10-22", {"technical": TECH, "fundamentals": FUND}, outputs, {"verdict": "Hold", "conviction": 20, "raw_conviction": 40, "calibration": "uncalibrated"},
                                {"type": "REVIEW", "reason": "analysts disagree"}, {"bullish": ["valuation"], "bearish": ["valuation"]}, 0.03)


SHEET = make()
IDS = {f["name"]: f["id"] for f in SHEET["facts"]}  # F1 growth, F2 pe_trailing, F3 roe, T1 rsi_14


def case(text, evidence_ids, weakest="The trend is mixed."):
    return json.dumps({"case": text, "evidence": list(evidence_ids), "weakest_point": weakest})


def test_a_correct_number_attached_to_the_wrong_fact_is_rejected_in_a_debate_case():
    parsed, errors = debate._check(case(f"P/E is 21 ({IDS['rsi_14']}).", [IDS["rsi_14"]]), SHEET)
    assert parsed is None and any(IDS["pe_trailing"] in e and "not cite" in e for e in errors)


def test_the_same_figure_with_its_own_fact_cited_passes_whether_the_id_is_listed_or_written_inline():
    assert debate._check(case(f"P/E is 21 ({IDS['pe_trailing']}).", [IDS["pe_trailing"]]), SHEET)[0] is not None
    assert debate._check(case("Momentum is intact.", [IDS["rsi_14"]], f"P/E is 21 ({IDS['pe_trailing']})."), SHEET)[0] is not None


def test_a_signed_figure_must_agree_with_the_sign_of_the_fact_it_cites():
    roe = IDS["roe"]
    assert debate._check(case(f"Return on equity is +25% ({roe}).", [roe]), SHEET)[0] is None
    errors = debate._check(case(f"Return on equity is +25% ({roe}).", [roe]), SHEET)[1]
    assert any("sign" in e for e in errors)
    assert debate._check(case(f"Return on equity is -25% ({roe}).", [roe]), SHEET)[0] is not None
    assert debate._check(case(f"A 25% shortfall in return on equity ({roe}).", [roe]), SHEET)[0] is not None  # a magnitude without a sign is still allowed, as before


def test_a_fraction_may_be_cited_as_a_percent_but_a_plain_number_is_not_scaled():
    growth = IDS["growth"]
    assert debate._check(case(f"Growth of 12% ({growth}).", [growth]), SHEET)[0] is not None
    # only a fraction converts: 2100 is not the ratio 21 read as a percent, so it matches no fact and the binder leaves it to the sheet-wide check
    assert bound_figure_errors("A 2100% move", facts_from_sheet(SHEET), [IDS["pe_trailing"]]) == []
    # and 12 is the fraction 0.12 read as a percent, a fact the reply must cite
    assert bound_figure_errors("A 12% move", facts_from_sheet(SHEET), [IDS["pe_trailing"]]) != []


def test_a_figure_that_is_on_no_fact_is_left_to_the_sheet_wide_check_as_before():
    assert bound_figure_errors("Conviction is 20 and net is 0.03.", facts_from_sheet(SHEET), []) == []
    parsed, errors = debate._check(case("Margins are 45% (F9).", ["F9"]), SHEET)
    assert parsed is None


def test_the_judge_is_bound_the_same_way_to_what_it_relied_on():
    def reply(why, relied):
        return json.dumps({"stance": "Underweight", "why": why, "relied_on": relied, "would_change_my_mind": "A better quarter.", "conviction": "same", "look_again_days": 20})

    wrong = judge._check(reply(f"P/E is 21 ({IDS['roe']}).", [IDS["roe"]]), SHEET)
    assert wrong[0] is None and any(IDS["pe_trailing"] in e for e in wrong[1])
    assert judge._check(reply(f"P/E is 21 ({IDS['pe_trailing']}).", [IDS["pe_trailing"]]), SHEET)[0] is not None
    signed = judge._check(reply(f"Return on equity is +25% ({IDS['roe']}).", [IDS["roe"]]), SHEET)
    assert signed[0] is None and any("sign" in e for e in signed[1])


def test_the_sheet_shows_the_typed_claims_an_analyst_made_and_the_label_of_what_was_checked():
    outputs = {"valuation": {**OUTPUTS["valuation"], "claims": [{"fact": "pe_trailing", "statement": "TCS pe_trailing (last 4 quarters) = 21 ratio [pe_trailing]"}],
                              "verification": {"scheme": "typed_claims/1", "claims_verified": 1, "interpretation": "model_unreviewed"}},
               "quality": {**OUTPUTS["valuation"], "verification": {"scheme": "numeric_consistency/1", "figures_checked": 1, "binds_claims": False}}}
    sheet = make(outputs)
    assert sheet["analysts"]["valuation"]["claims"] == ["TCS pe_trailing (last 4 quarters) = 21 ratio [pe_trailing]"]
    assert sheet["analysts"]["valuation"]["checked"] == "typed_claims/1" and sheet["analysts"]["quality"]["checked"] == "numeric_consistency/1"
    text = evidence.render(sheet)
    assert "TCS pe_trailing (last 4 quarters) = 21 ratio" in text and "checked: numeric_consistency/1" in text
    assert "claims" not in make()["analysts"]["valuation"] and "checked" not in make()["analysts"]["valuation"]  # an older output has neither
