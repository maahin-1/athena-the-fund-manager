"""The golden sets against live data and the real models. Run with `pytest --live tests/live/test_live_golden.py -s`.

- Live evidence: every labelled figure and missing-metric is checked on today's data. These labels do not depend on the
  market (a sane P/E, a bank has no free-cash-flow yield, a USD reporter has no P/E), so a failure is a data or code fault.
- Frozen signals: the specialists read the frozen packets; a signal outside its label fails, a specialist that gives no
  answer is reported but does not fail (a free-tier model can fail validation twice).
- Live signals and trend labels: reported for the owner to read, never asserted, because the market moves under them."""
import pytest

from athena.agents.base import Specialist
from athena.cli import FUNDAMENTAL_SPECIALISTS, build_orchestrator, live_sources
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.contracts import AthenaError
from athena.evaluation.golden import (
    FAIL, FUNDAMENTALS, NO_ANSWER, TECHNICAL, GoldenCase, GoldenReport, build_packets, check_evidence, check_signals, load_cases,
    load_index_history, load_snapshot,
)

pytestmark = pytest.mark.live

CASES = load_cases()
SPECIALIST_PACKET = {"quant_technical": TECHNICAL, **{name: FUNDAMENTALS for name in FUNDAMENTAL_SPECIALISTS}}
SPECIALIST_SPECS = {"quant_technical": QUANT_TECHNICAL, **FUNDAMENTAL_SPECIALISTS}


@pytest.fixture(scope="module")
def sources():
    try:
        return live_sources()
    except AthenaError:
        pytest.skip("live sources are not available (no LLM provider key is set)")


@pytest.fixture(scope="module")
def orchestrator(sources):
    return build_orchestrator(sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals)


def live_packets(sources, orchestrator, symbol):
    resolution = sources.resolver.resolve(symbol)
    packets = {TECHNICAL: orchestrator._packet_builders["quant_technical"](resolution)}
    if resolution.asset_class == "equity":
        packets[FUNDAMENTALS] = sources.fundamentals(resolution)
    return packets


def run_specialists(case: GoldenCase, packets, client):
    outputs = {}
    for name in case.signals:
        packet = packets.get(SPECIALIST_PACKET[name])
        try:
            outputs[name] = Specialist(SPECIALIST_SPECS[name], client).analyze(packet) if packet is not None else None
        except AthenaError:
            outputs[name] = None
    return outputs


def test_live_data_meets_the_evidence_labels_that_do_not_depend_on_the_market(sources, orchestrator):
    report, drift = GoldenReport(), []
    for case in CASES:
        try:
            packets = live_packets(sources, orchestrator, case.symbol)
        except AthenaError as exc:
            pytest.fail(f"{case.symbol}: live data could not be fetched: {exc}")
        findings = check_evidence(case, packets)
        report.add([f for f in findings if not f.check.startswith("values.")])
        drift += [f"{case.symbol} {f.check}: {f.detail}" for f in findings if f.check.startswith("values.") and f.outcome == FAIL]
    print("\nlive evidence:", dict(report.by_component()))
    print("trend labels that have moved since the snapshot (informational):", drift or "none")
    assert not report.failures(), [f"{f.symbol} {f.check}: {f.detail}" for f in report.failures()]


def test_the_specialists_stay_inside_the_labelled_signal_ranges_on_the_frozen_packets(sources):
    client = sources.llm_router.client_for("specialist")
    index, report = load_index_history(), GoldenReport()
    for case in (c for c in CASES if c.signals):
        report.add(check_signals(case, run_specialists(case, build_packets(load_snapshot(case.symbol), index), client)))
    for finding in report.findings:
        print(f"{finding.outcome:10} {finding.symbol:11} {finding.check:28} {finding.detail}")
    assert not report.failures(), [f"{f.symbol} {f.check}: {f.detail}" for f in report.failures()]
    assert report.count(NO_ANSWER) <= len(report.findings) // 2, "more than half the specialists gave no answer"


def test_the_specialists_on_live_packets_are_reported_against_the_same_labels(sources, orchestrator):
    client = sources.llm_router.client_for("specialist")
    report = GoldenReport()
    for case in (c for c in CASES if c.signals):
        report.add(check_signals(case, run_specialists(case, live_packets(sources, orchestrator, case.symbol), client)))
    for finding in report.findings:
        print(f"{finding.outcome:10} {finding.symbol:11} {finding.check:28} {finding.detail}")
    print("live signal outcomes:", {k: dict(v) for k, v in report.by_component().items()})
    assert report.findings  # reported, not judged: the market moves under these labels
