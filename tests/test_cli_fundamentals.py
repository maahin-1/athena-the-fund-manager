import json
from types import SimpleNamespace

from bar_factory import make_bars
from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.cli import FUNDAMENTAL_SPECIALISTS, build_orchestrator
from athena.contracts import EmptyRefreshError, Record
from athena.evaluation.schema import validate_judge_verdict
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.orchestrator.orchestrator import OK
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

FULL = build_fundamentals_packet("SBIN", NOW, ACME, PRICE, NOW, index_history())


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


class GenericNarrator:
    """A fake model that cites the first two numeric figures of whichever packet it is shown."""

    def complete(self, system, user):
        metrics = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        cited = [(name, m["value"]) for name, m in metrics.items() if not isinstance(m["value"], str)][:2]
        text = " ".join(f"{name} is {value}." for name, value in cited)
        return json.dumps({"signal": "bullish", "confidence": 70, "reasoning": text})


class FakeLLMRouter:
    def client_for(self, role):
        return GenericNarrator()


class FakeChain:
    def run(self, symbol, **kwargs):
        return SimpleNamespace(value=make_bars([100.0 + i * 0.5 for i in range(400)], symbol=symbol), source="fake")


class FakeFundamentals:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, resolution):
        self.calls.append(resolution.identifier)
        if self.error:
            raise self.error
        return FULL


def orchestrator(fundamentals):
    return build_orchestrator(make_resolver(), FakeLLMRouter(), FakeChain(), clock=lambda: NOW, fundamentals=fundamentals)


def test_the_three_fundamentals_specialists_are_the_ones_the_routing_table_names():
    assert set(FUNDAMENTAL_SPECIALISTS) == {"valuation", "moat_quality", "earnings_intelligence"}


def test_with_a_fundamentals_source_a_stock_is_analysed_by_all_four_equity_specialists():
    source = FakeFundamentals()
    result = orchestrator(source).analyze("sbin")
    assert result.status == OK and result.skipped == {}
    assert set(result.specialists) == {"quant_technical", "valuation", "moat_quality", "earnings_intelligence"}
    assert all(out["data_coverage"] == "full" for out in result.specialists.values())
    assert validate_judge_verdict(result.verdict) == [] and result.verdict["verdict"] == "Buy"
    assert not any("routed specialists ran" in note for note in result.notes)
    assert source.calls == ["SBIN"] * 3  # one call per specialist; LiveFundamentals caches the work behind them


def test_an_etf_never_calls_the_fundamentals_source_and_keeps_its_own_routing():
    source = FakeFundamentals()
    result = orchestrator(source).analyze("niftybees")
    assert source.calls == [] and set(result.specialists) == {"quant_technical"}
    assert result.skipped == {"etf_analyst": "not built yet"}


def test_without_a_fundamentals_source_the_three_are_listed_as_not_built_as_before():
    result = orchestrator(None).analyze("sbin")
    assert set(result.specialists) == {"quant_technical"}
    assert result.skipped == {name: "not built yet" for name in FUNDAMENTAL_SPECIALISTS}


def test_a_fundamentals_failure_skips_the_three_but_the_technical_specialist_still_answers():
    result = orchestrator(FakeFundamentals(error=EmptyRefreshError("no statements for 'SBIN'"))).analyze("sbin")
    assert set(result.specialists) == {"quant_technical"} and result.status == OK
    assert set(result.skipped) == set(FUNDAMENTAL_SPECIALISTS)
    assert all("EmptyRefreshError" in why for why in result.skipped.values())
    assert any("only 1 of 4 routed specialists ran" in note for note in result.notes)
