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
