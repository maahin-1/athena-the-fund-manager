import pytest
from bar_factory import make_bars

from athena.agents.base import SpecialistError
from athena.contracts import AllSourcesFailed, AthenaError, InsufficientData
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import (
    NEEDS_CLARIFICATION,
    NO_VIEW,
    NOT_BUILT,
    OK,
    RISK_OVERLAY_NOTE,
    Orchestrator,
)
from athena.resolver import Ambiguity, Candidate, Resolution
from athena.risk_overlay.model import Holding, Limit, Overlay, RiskProfile


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


def wild_bars():
    closes, level = [], 100.0
    for i in range(300):
        level *= 1.05 if i % 2 == 0 else 0.95
        closes.append(level)
    return make_bars(closes, volume=1000.0, symbol="SBIN")


class Prices:
    def __init__(self, bars=None, error=None):
        self.bars, self.error, self.symbols = bars if bars is not None else wild_bars(), error, []

    def __call__(self, symbol):
        self.symbols.append(symbol)
        if self.error:
            raise self.error
        return self.bars


CAUTIOUS = Overlay(RiskProfile("cautious", {"volatility": Limit(0.3, 0.6)}))


def with_prices(prices, specialists=None):
    specialists = specialists or {"quant_technical": FakeSpecialist(out("bullish", 80))}
    builders = {name: (lambda res: {}) for name in specialists}
    return Orchestrator(FakeResolver(resolution()), specialists, builders, bars=prices)


def test_a_breached_limit_holds_a_buy_back_to_hold_and_keeps_the_specialists_call():
    prices = Prices()
    result = with_prices(prices).analyze("sbin", CAUTIOUS)
    assert result.verdict["verdict"] == "Hold" and result.verdict["pre_overlay_verdict"] == "Buy"
    assert [f["status"] for f in result.verdict["risk_findings"]] == ["breach"]
    assert result.verdict["key_risks"][0].startswith("Held back by your risk limits")
    assert validate_judge_verdict(result.verdict) == [] and prices.symbols == ["SBIN"]
    assert "risk profile 'cautious' applied" in " ".join(result.notes) and RISK_OVERLAY_NOTE not in result.notes
    assert result.specialists["quant_technical"]["signal"] == "bullish"  # the specialists are untouched


def test_without_an_overlay_the_verdict_has_no_overlay_keys_and_the_note_says_no_limits_were_applied():
    prices = Prices()
    result = with_prices(prices).analyze("sbin")
    assert result.verdict["verdict"] == "Buy" and set(result.verdict) == {"verdict", "conviction", "key_risks", "resolution_path"}
    assert RISK_OVERLAY_NOTE in result.notes and prices.symbols == []


def test_the_overlay_also_applies_when_the_instrument_is_already_resolved():
    result = with_prices(Prices()).analyze_resolved(resolution(), None, CAUTIOUS)
    assert result.verdict["verdict"] == "Hold" and result.verdict["pre_overlay_verdict"] == "Buy"


def test_a_calm_stock_within_its_limits_keeps_its_verdict_and_still_shows_every_check():
    calm = Prices(make_bars([100.0 + 0.1 * (i % 3) for i in range(300)], symbol="SBIN"))
    result = with_prices(calm).analyze("sbin", CAUTIOUS)
    assert result.verdict["verdict"] == "Buy" and "pre_overlay_verdict" not in result.verdict
    assert [f["status"] for f in result.verdict["risk_findings"]] == ["ok"]


def test_the_checks_that_need_holdings_and_an_amount_use_them():
    overlay = Overlay(RiskProfile("p", {"position": Limit(0.05, 0.10)}), (Holding("INFY", 90_000.0),), 30_000.0)
    result = with_prices(Prices()).analyze("sbin", overlay)
    (finding,) = result.verdict["risk_findings"]
    assert finding["status"] == "breach" and finding["value"] == 0.25 and result.verdict["verdict"] == "Hold"


def test_when_prices_cannot_be_fetched_the_verdict_stands_and_the_note_says_the_limits_were_not_checked():
    result = with_prices(Prices(error=AllSourcesFailed("no prices"))).analyze("sbin", CAUTIOUS)
    assert result.verdict["verdict"] == "Buy" and "risk_findings" not in result.verdict
    assert any(note.startswith("risk limits could not be checked") for note in result.notes)


def test_asking_for_an_overlay_with_no_price_source_is_an_error_not_a_silent_skip():
    orchestrator = Orchestrator(FakeResolver(resolution()), {"quant_technical": FakeSpecialist(out())}, {"quant_technical": lambda res: {}})
    with pytest.raises(AthenaError, match="needs a price source"):
        orchestrator.analyze("sbin", CAUTIOUS)


def test_an_ambiguous_query_asks_which_instrument_before_any_limit_is_checked():
    prices = Prices()
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.8),), "several")
    orchestrator = Orchestrator(FakeResolver(ambiguity), {}, {}, bars=prices)
    assert orchestrator.analyze("sbi", CAUTIOUS).status == NEEDS_CLARIFICATION and prices.symbols == []


def test_with_no_specialist_view_the_hold_stands_and_the_findings_are_still_listed():
    result = with_prices(Prices(), {"quant_technical": FakeSpecialist(out("neutral", 0, "insufficient", ["x"]))}).analyze("sbin", CAUTIOUS)
    assert result.status == NO_VIEW and result.verdict["verdict"] == "Hold" and "pre_overlay_verdict" not in result.verdict
    assert result.verdict["risk_findings"][0]["status"] == "breach"
