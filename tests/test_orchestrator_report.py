from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW, OK, OrchestrationResult
from athena.orchestrator.report import DISCLAIMER, format_result
from athena.resolver import Ambiguity, Candidate, Resolution

RESOLUTION = Resolution("equity", "ticker", "SBIN", "State Bank of India", "INE062A01020", "exact", 1.0, (), ())
SPECIALIST = {"signal": "neutral", "confidence": 35, "reasoning": "Trend is mixed.", "data_coverage": "full", "missing": []}


def result(status=OK, verdict=None, **changes):
    base = dict(
        status=status, query="sbin", resolution=RESOLUTION, ambiguity=None, specialists={"quant_technical": SPECIALIST},
        skipped={"valuation": "not built yet"}, conflict=None, net=0.0,
        verdict=verdict or {"verdict": "Hold", "conviction": 35, "key_risks": ["a risk"], "resolution_path": "blend"},
        notes=("risk overlay missing",),
    )
    base.update(changes)
    return OrchestrationResult(**base)


def test_report_shows_instrument_verdict_specialists_skips_risks_notes_and_disclaimer():
    text = format_result(result())
    for expected in (
        "SBIN  State Bank of India  (equity, exact match)",
        "Verdict: Hold  conviction 35  (blend)",
        "quant_technical: neutral 35  coverage full",
        "Trend is mixed.",
        "Not run: valuation (not built yet)",
        "  - a risk",
        "Note: risk overlay missing",
        DISCLAIMER,
    ):
        assert expected in text, expected


def test_report_flags_a_no_view_result():
    text = format_result(result(NO_VIEW))
    assert "[no specialist had enough data]" in text


def test_report_lists_candidates_for_an_ambiguous_query():
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.81),), "several matches")
    text = format_result(OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ()))
    assert "could be more than one instrument (several matches)" in text
    assert "1. SBIN  State Bank of India  (equity, match 0.81)" in text and "Re-run with the exact symbol." in text


def test_the_report_shows_ten_candidates_and_says_how_many_more():
    candidates = tuple(Candidate("equity", f"SYM{n:02d}", f"Name {n}", 0.8) for n in range(25))
    ambiguity = Ambiguity("sy", candidates, "several matches")
    text = format_result(OrchestrationResult(NEEDS_CLARIFICATION, "sy", None, ambiguity, {}, {}, None, None, None, ()))
    assert "10. SYM09" in text and "11. SYM10" not in text
    assert "  ...and 15 more; type the exact symbol." in text and "Re-run with the exact symbol." in text


def test_the_report_has_no_more_line_when_everything_fits():
    candidates = tuple(Candidate("equity", f"SYM{n:02d}", f"Name {n}", 0.8) for n in range(10))
    ambiguity = Ambiguity("sy", candidates, "several matches")
    text = format_result(OrchestrationResult(NEEDS_CLARIFICATION, "sy", None, ambiguity, {}, {}, None, None, None, ()))
    assert "10. SYM09" in text and "...and" not in text


def test_report_lists_the_risk_overlay_and_says_when_a_verdict_was_held_back():
    findings = [
        {"check": "volatility", "status": "breach", "value": 0.8, "warn": 0.3, "hard": 0.6, "message": "Volatility 80.0% is at or above your hard limit of 60.0%."},
        {"check": "position", "status": "unchecked", "value": None, "warn": 0.1, "hard": 0.2, "message": "Position size not checked: no amount to invest was given."},
        {"check": "drawdown", "status": "ok", "value": 0.1, "warn": 0.3, "hard": 0.4, "message": "Worst drawdown 10.0% is within your limits (warning at 30.0%)."},
        {"check": "var_95", "status": "warn", "value": 0.04, "warn": 0.03, "hard": 0.06, "message": "1-day VaR (95%) 4.0% is at or above your warning level of 3.0% (hard limit 6.0%)."},
    ]
    verdict = {
        "verdict": "Hold", "conviction": 35, "key_risks": ["a risk"], "resolution_path": "blend",
        "pre_overlay_verdict": "Buy", "risk_findings": findings,
    }
    text = format_result(result(verdict=verdict))
    assert "Verdict: Hold  conviction 35  (blend)  [held back from Buy by your risk limits]" in text
    assert "Risk overlay:" in text
    for expected in (
        "  [BREACH] Volatility 80.0% is at or above your hard limit of 60.0%.",
        "  [not checked] Position size not checked: no amount to invest was given.",
        "  [ok] Worst drawdown 10.0% is within your limits (warning at 30.0%).",
        "  [WARN] 1-day VaR (95%) 4.0% is at or above your warning level of 3.0% (hard limit 6.0%).",
    ):
        assert expected in text, expected
    assert text.index("Risk overlay:") < text.index("Key risks:")


def test_report_without_an_overlay_has_no_overlay_block_or_held_back_mark():
    text = format_result(result())
    assert "Risk overlay:" not in text and "held back" not in text
