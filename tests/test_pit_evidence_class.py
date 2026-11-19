"""Strict archival filings and approximate deadline-filtered statements are different evidence (WP02, Q2); they are never pooled into one unqualified score."""
from datetime import date

import pytest

from athena.evaluation import pit_cases, scorecard
from athena.evaluation.pit_score import Call, calls_from_rows, score
from athena.pit import yahoo_asof
from athena.verdicts.calibration import Calibration


def row(source, signal, label, symbol="A", confidence=70, horizon=63, specialist="valuation", as_of="2026-04-30"):
    return {"symbol": symbol, "as_of": as_of, "horizon": horizon, "specialist": specialist, "signal": signal, "confidence": confidence, "label": label, "excess": 0.05 if label == "outperform" else -0.05, "source": source}


ARCHIVAL, APPROX = pit_cases.SOURCE_NSE, pit_cases.SOURCE_YAHOO


def test_each_source_has_a_named_evidence_class():
    assert pit_cases.evidence_class(ARCHIVAL) == "archival" and pit_cases.evidence_class(APPROX) == "approximate_deadline"
    assert pit_cases.evidence_class(pit_cases.SOURCE_NONE) == "none" and pit_cases.evidence_class("something else") == "unclassified"
    assert pit_cases.evidence_class(None) == "unclassified"  # a row from a log that never recorded its source


def test_the_yahoo_deadline_filter_keeps_a_late_filers_period_which_a_strict_study_could_not_know():
    # a March 31 annual period actually filed on June 20 is still kept for June 1, because only its legal deadline (60 days) had passed: this is why the class is approximate
    payload = {"annual": {"income": {"Total Revenue": {"2025-03-31": 100.0}}, "balance": {}}, "quarterly": {"income": {}}, "info": {}, "quality_flags": []}
    kept = yahoo_asof.yahoo_statements_as_of(payload, date(2025, 6, 1))
    assert kept["annual"]["income"]["Total Revenue"] == {"2025-03-31": 100.0}  # the leak, kept visible in a regression and labelled in the note
    assert "cannot tell a late filer" in yahoo_asof.NOTE and "safe" not in yahoo_asof.NOTE.lower().replace("not safe", "")
    assert yahoo_asof.EVIDENCE_CLASS == "approximate_deadline"


def test_calls_carry_their_evidence_class_and_can_be_filtered_by_it():
    rows = [row(ARCHIVAL, "bullish", "outperform"), row(APPROX, "bullish", "underperform", symbol="B"), row(pit_cases.SOURCE_NONE, "neutral", "inline", symbol="C")]
    everything = calls_from_rows(rows)
    assert {c.evidence for c in everything} == {"archival", "approximate_deadline", "none"}
    only = calls_from_rows(rows, evidence_classes={"archival"})
    assert [c.symbol for c in only] == ["A"]
    assert score(calls_from_rows(rows, evidence_classes={"approximate_deadline"}), "valuation", 63).accuracy == 0.0  # the approximate rows alone, reported alone


def test_the_scorecard_is_built_per_evidence_class_and_a_pooled_card_is_labelled_unqualified():
    rows = [row(ARCHIVAL, "bullish", "outperform", symbol=s) for s in "ABCDEFGH"] + [row(APPROX, "bullish", "underperform", symbol=s) for s in "IJKLMNOP"]
    cards = scorecard.build_by_evidence(rows)
    assert set(cards["classes"]) == {"archival", "approximate_deadline"} and cards["schema"] == "scorecard/2"
    assert cards["classes"]["archival"]["valuation"]["63"]["cases"] == 8 and cards["classes"]["approximate_deadline"]["valuation"]["63"]["cases"] == 8
    assert "pooled" not in cards
    pooled = scorecard.build_by_evidence(rows, pooled=True)
    assert pooled["pooled"]["label"].startswith("UNQUALIFIED") and pooled["pooled"]["card"]["valuation"]["63"]["cases"] == 16


def test_old_rows_without_a_recorded_source_are_reported_as_unclassified_not_as_archival():
    rows = [{k: v for k, v in row(ARCHIVAL, "bullish", "outperform", symbol=s).items() if k != "source"} for s in "ABCDEFGH"]
    cards = scorecard.build_by_evidence(rows)
    assert set(cards["classes"]) == {"unclassified"}


def test_calibration_can_use_archival_evidence_only_and_says_what_it_left_out_and_pooling_is_labelled():
    from athena.verdicts.calibration import DEFAULT_EVIDENCE

    rows = [row(ARCHIVAL, "bullish", "outperform", symbol=f"A{i}") for i in range(4)] + [row(APPROX, "bullish", "underperform", symbol=f"B{i}") for i in range(6)]
    assert DEFAULT_EVIDENCE == {"archival"}  # what the --calibrate command uses unless told --evidence all
    default = Calibration.from_log_rows(rows, evidence_classes=DEFAULT_EVIDENCE)
    assert default.measures["valuation"][63].cases == 4 and default.evidence == {"used": ["archival"], "left_out": {"approximate_deadline": 6}}
    pooled = Calibration.from_log_rows(rows)
    assert pooled.measures["valuation"][63].cases == 10 and pooled.evidence["used"] == ["all (pooled, unqualified)"]
    again = Calibration.from_json(default.to_json())  # the saved file keeps its old shape: it is only a derived summary
    assert again.measures == default.measures


def test_a_call_dataclass_still_builds_the_old_way():
    assert Call("A", "2026-04-30", 63, "valuation", 0, "bullish", 70, "outperform").evidence == "unclassified"
