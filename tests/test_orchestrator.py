import pytest

from athena.agents.base import SpecialistError
from athena.contracts import AllSourcesFailed, InsufficientData
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import (
    NEEDS_CLARIFICATION,
    NO_VIEW,
    NOT_BUILT,
    OK,
    Orchestrator,
)
from athena.resolver import Ambiguity, Candidate, Resolution


def resolution(asset_class="equity", routed=("quant_technical", "valuation")):
    return Resolution(asset_class, "ticker", "SBIN", "State Bank of India", "INE062A01020", "exact", 1.0, (), tuple(routed))


class FakeResolver:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def resolve(self, query):
        self.queries.append(query)
        return self.result


class FakeSpecialist:
    def __init__(self, output=None, error=None):
        self.output, self.error, self.packets = output, error, []

    def analyze(self, packet):
        self.packets.append(packet)
        if self.error:
            raise self.error
        return self.output


def out(signal="bullish", confidence=80, coverage="full", missing=()):
    return {"signal": signal, "confidence": confidence, "reasoning": "r", "data_coverage": coverage, "missing": list(missing)}


def build(specialists, builders=None, resolved=None):
    builders = builders if builders is not None else {name: (lambda res, n=name: {"built_for": n, "id": res.identifier}) for name in specialists}
    return Orchestrator(FakeResolver(resolved or resolution()), specialists, builders)


def test_a_resolved_query_runs_the_registered_specialists_and_blends():
    quant = FakeSpecialist(out("bullish", 80))
    result = build({"quant_technical": quant}).analyze("sbin")
    assert result.status == OK and result.query == "sbin"
    assert result.specialists == {"quant_technical": out("bullish", 80)}
    assert quant.packets == [{"built_for": "quant_technical", "id": "SBIN"}]
    assert result.verdict["verdict"] == "Buy" and validate_judge_verdict(result.verdict) == []
    assert validate_specialist_output(result.specialists["quant_technical"]) == []


def test_routed_specialists_that_are_not_built_are_listed_not_hidden():
    result = build({"quant_technical": FakeSpecialist(out())}).analyze("sbin")
    assert result.skipped == {"valuation": NOT_BUILT}


def test_a_specialist_with_no_packet_builder_counts_as_not_built():
    result = build({"quant_technical": FakeSpecialist(out())}, builders={}).analyze("sbin")
    assert result.skipped["quant_technical"] == NOT_BUILT and result.status == NO_VIEW


def test_a_data_failure_in_one_specialist_is_recorded_and_the_others_still_run():
    def failing_builder(res):
        raise InsufficientData("no price history")

    good = FakeSpecialist(out("bearish", 70))
    orchestrator = Orchestrator(
        FakeResolver(resolution(routed=("quant_technical", "valuation"))),
        {"quant_technical": FakeSpecialist(out()), "valuation": good},
        {"quant_technical": failing_builder, "valuation": lambda res: {"metrics": {}}},
    )
    result = orchestrator.analyze("sbin")
    assert result.skipped == {"quant_technical": "InsufficientData: no price history"}
    assert list(result.specialists) == ["valuation"] and result.verdict["verdict"] == "Sell"


def test_model_failures_are_recorded_as_skipped_specialists():
    for error in (SpecialistError("no valid output"), AllSourcesFailed("every model failed")):
        result = build({"quant_technical": FakeSpecialist(error=error)}).analyze("sbin")
        assert type(error).__name__ in result.skipped["quant_technical"]


def test_when_nothing_ran_the_result_is_a_zero_conviction_hold_marked_no_view():
    result = build({"quant_technical": FakeSpecialist(error=SpecialistError("x"))}).analyze("sbin")
    assert result.status == NO_VIEW
    assert (result.verdict["verdict"], result.verdict["conviction"]) == ("Hold", 0)


def test_an_insufficient_specialist_is_shown_but_gives_no_view():
    result = build({"quant_technical": FakeSpecialist(out("neutral", 0, "insufficient", ["rsi_14"]))}).analyze("sbin")
    assert result.status == NO_VIEW and "quant_technical" in result.specialists


def test_a_conflict_between_specialists_is_reported_and_conviction_is_capped():
    orchestrator = Orchestrator(
        FakeResolver(resolution(routed=("quant_technical", "valuation"))),
        {"quant_technical": FakeSpecialist(out("bullish", 85)), "valuation": FakeSpecialist(out("bearish", 85))},
        {"quant_technical": lambda r: {}, "valuation": lambda r: {}},
    )
    result = orchestrator.analyze("sbin")
    assert result.conflict is not None and result.verdict["conviction"] <= 40
    assert result.verdict["resolution_path"] == "blend"


def test_an_ambiguous_query_asks_for_clarification_and_runs_nothing():
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.8),), "several matches")
    quant = FakeSpecialist(out())
    orchestrator = Orchestrator(FakeResolver(ambiguity), {"quant_technical": quant}, {"quant_technical": lambda r: {}})
    result = orchestrator.analyze("sbi")
    assert result.status == NEEDS_CLARIFICATION and result.ambiguity is ambiguity
    assert result.verdict is None and result.specialists == {} and quant.packets == []


def test_a_bug_in_a_specialist_is_not_swallowed():
    orchestrator = build({"quant_technical": FakeSpecialist(error=RuntimeError("bug"))})
    with pytest.raises(RuntimeError):
        orchestrator.analyze("sbin")


def test_analyze_resolved_skips_the_resolver_and_always_notes_the_missing_risk_overlay():
    orchestrator = build({"quant_technical": FakeSpecialist(out())})
    result = orchestrator.analyze_resolved(resolution())
    assert orchestrator._resolver.queries == [] and result.query == "SBIN"
    assert any("risk overlay" in note for note in result.notes)


def test_the_result_says_how_many_of_the_routed_specialists_actually_ran():
    result = build({"quant_technical": FakeSpecialist(out())}).analyze("sbin")
    assert result.notes[0] == "only 1 of 2 routed specialists ran, so the verdict reflects just those"


def test_no_coverage_note_when_every_routed_specialist_ran():
    orchestrator = Orchestrator(
        FakeResolver(resolution(routed=("quant_technical",))), {"quant_technical": FakeSpecialist(out())}, {"quant_technical": lambda r: {}}
    )
    notes = orchestrator.analyze("sbin").notes
    assert not any("routed specialists ran" in note for note in notes) and any("risk overlay" in note for note in notes)
