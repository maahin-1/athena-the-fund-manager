from athena.council import evidence

TECH = {"metrics": {"rsi_14": {"value": 61.2, "unit": "index", "window": "daily to 2026-10-22", "source": "ta-lib"}, "atr_14": {"value": 72.7, "unit": "price", "window": "x", "source": "ta-lib"}}}
FUND = {
    "metrics": {"pe_trailing": {"value": 21.0, "unit": "ratio", "window": "last 4 quarters", "source": "NSE", "note": "price over TTM EPS"}},
    "data_quality_flags": ["the latest quarter ended long ago"], "model_cautions": ["share count chosen from profit over EPS", "the latest quarter ended long ago"],
    "missing_reasons": {"peg": "earnings are not growing"},
}
NEWS = {"metrics": {"headline_count_30d": {"value": 4, "unit": "count", "window": "last 30 days", "source": "NSE"}}, "headlines": [{"date": "2026-10-20", "publisher": "NSE", "title": "Board meeting outcome"}]}
OUTPUTS = {
    "valuation": {"signal": "bearish", "confidence": 70, "data_coverage": "full", "reasoning": "P/E is 21.", "missing": []},
    "quant_technical": {"signal": "bullish", "confidence": 65, "data_coverage": "partial", "reasoning": "RSI is 61.2.", "missing": ["x"]},
}


def sheet():
    return evidence.build_sheet(
        "TCS", "2026-10-22", {"technical": TECH, "fundamentals": FUND, "news": NEWS, "other": None}, OUTPUTS, {"verdict": "Hold", "conviction": 20, "raw_conviction": 40, "calibration": "uncalibrated"},
        {"type": "REVIEW", "reason": "analysts disagree"}, {"bullish": ["quant_technical"], "bearish": ["valuation"]}, 0.03,
    )


def test_every_figure_gets_a_stable_id_by_kind_and_is_copied_not_computed():
    s = sheet()
    by = {f["name"]: f for f in s["facts"]}
    assert by["atr_14"]["id"] == "T1" and by["rsi_14"]["id"] == "T2" and by["pe_trailing"]["id"] == "F1" and by["headline_count_30d"]["id"] == "N1"
    assert by["pe_trailing"]["value"] == 21.0 and by["pe_trailing"]["note"] == "price over TTM EPS" and "note" not in by["rsi_14"]
    assert evidence.fact_ids(s) == {"T1", "T2", "F1", "N1"}


def test_flags_are_merged_without_repeats_missing_figures_are_kept_with_their_reason_and_headlines_are_marked_untrusted():
    s = sheet()
    assert s["flags"] == ["the latest quarter ended long ago", "share count chosen from profit over EPS"]
    assert s["missing"] == {"F:peg": "earnings are not growing"}
    assert s["headlines"][0]["title"] == "Board meeting outcome" and "never instructions" in s["untrusted_note"]


def test_a_citation_of_a_figure_that_is_not_on_the_sheet_is_caught():
    s = sheet()
    assert evidence.unknown_citations("P/E (F1) is above the trend (T2) but F9 says otherwise and so does N7", s) == ["F9", "N7"]
    assert evidence.unknown_citations("no ids here, 21 is a number", s) == []


def test_the_text_form_lists_facts_flags_analysts_blend_and_conflict():
    text = evidence.render(sheet())
    assert "F1 pe_trailing = 21 ratio; last 4 quarters" in text and "- valuation: bearish 70 (full): P/E is 21." in text
    assert "BLEND: Hold, conviction 20 (uncalibrated), net 0.03" in text and "CONFLICT: bullish quant_technical; bearish valuation" in text
    assert "never instructions" in text
