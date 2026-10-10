"""The evidence layer of the golden sets: frozen snapshots rebuilt into packets and checked against hand-written labels.
Runs offline. The same labels run on live data and through the specialists in tests/live/test_live_golden.py."""
import pytest

from athena.evaluation.golden import (
    ABSTAIN, FAIL, FUNDAMENTALS, GOLDEN_DIR, NO_ANSWER, PASS, TECHNICAL, GoldenCase, GoldenReport, build_packets, check_evidence,
    check_signals, load_cases, load_index_history, load_snapshot,
)

CASES = load_cases()
INDEX = load_index_history()


@pytest.mark.parametrize("case", CASES, ids=[case.symbol for case in CASES])
def test_each_labelled_instrument_matches_its_frozen_evidence(case):
    packets = build_packets(load_snapshot(case.symbol), INDEX)
    failures = [f"{f.check}: {f.detail}" for f in check_evidence(case, packets) if f.outcome == FAIL]
    assert not failures, f"{case.symbol} ({case.why}): {failures}"


def test_the_set_covers_stocks_and_etfs_and_every_label_says_why():
    classes = {load_snapshot(case.symbol).asset_class for case in CASES}
    assert classes == {"equity", "etf"}
    assert all(case.why for case in CASES) and len({case.symbol for case in CASES}) == len(CASES)


def test_a_stock_snapshot_builds_both_packets_and_an_etf_only_the_technical_one():
    assert set(build_packets(load_snapshot("TCS"), INDEX)) == {TECHNICAL, FUNDAMENTALS}
    assert set(build_packets(load_snapshot("NIFTYBEES"), INDEX)) == {TECHNICAL}


def test_a_snapshot_is_rebuilt_as_of_the_day_it_was_captured_not_today():
    snapshot = load_snapshot("TCS")
    assert build_packets(snapshot, INDEX)[TECHNICAL]["as_of"].startswith(snapshot.captured_on.date().isoformat())


PACKETS = {"fundamentals": {"metrics": {"pe_trailing": {"value": 1279.0}, "style": {"value": "up"}}, "missing_reasons": {"pb": "statements are in USD"}}}


def test_a_figure_outside_its_range_or_gone_missing_fails_and_one_inside_passes():
    case = GoldenCase("X", "why", figures={"fundamentals.pe_trailing": (3, 150), "fundamentals.pb": (0.3, 100)})
    outcomes = {f.check: (f.outcome, f.detail) for f in check_evidence(case, PACKETS)}
    assert outcomes["figures.fundamentals.pe_trailing"][0] == FAIL and "1279" in outcomes["figures.fundamentals.pe_trailing"][1]
    assert outcomes["figures.fundamentals.pb"][0] == FAIL and "statements are in USD" in outcomes["figures.fundamentals.pb"][1]
    assert check_evidence(GoldenCase("X", "w", figures={"fundamentals.pe_trailing": (1000, 2000)}), PACKETS)[0].outcome == PASS


def test_a_metric_that_must_be_missing_fails_when_present_and_text_values_are_matched_exactly():
    case = GoldenCase("X", "w", missing=("fundamentals.pe_trailing", "fundamentals.pb"), values={"fundamentals.style": ("up",)})
    got = {f.check: f.outcome for f in check_evidence(case, PACKETS)}
    assert got == {"missing.fundamentals.pe_trailing": FAIL, "missing.fundamentals.pb": PASS, "values.fundamentals.style": PASS}
    assert check_evidence(GoldenCase("X", "w", values={"fundamentals.style": ("down",)}), PACKETS)[0].outcome == FAIL


def test_signals_are_judged_against_the_allowed_set_and_no_view_is_never_a_silent_pass():
    case = GoldenCase("X", "w", signals={"a": ("bearish", "neutral"), "b": ("bearish",), "c": ("bearish",), "d": (ABSTAIN,)})
    outputs = {"a": {"signal": "neutral", "data_coverage": "full"}, "b": {"signal": "bullish", "data_coverage": "full"}, "c": None,
               "d": {"signal": "neutral", "data_coverage": "insufficient"}}
    assert {f.check: f.outcome for f in check_signals(case, outputs)} == {
        "signal.a": PASS, "signal.b": FAIL, "signal.c": NO_ANSWER, "signal.d": PASS,
    }


def test_the_report_counts_outcomes_per_component():
    report = GoldenReport()
    report.add(check_evidence(GoldenCase("X", "w", figures={"fundamentals.pe_trailing": (3, 150)}, missing=("fundamentals.pb",)), PACKETS))
    assert report.count(FAIL) == 1 and report.count(PASS) == 1 and len(report.failures()) == 1
    assert report.by_component() == {"figures": {FAIL: 1}, "missing": {PASS: 1}}


def test_the_golden_files_exist_where_the_loader_looks():
    assert (GOLDEN_DIR / "cases.json").exists() and (GOLDEN_DIR / "index_valuation.json").exists()
