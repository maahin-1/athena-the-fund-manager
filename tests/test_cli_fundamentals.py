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


def test_with_a_fundamentals_source_a_stock_is_analysed_by_the_four_specialists_that_exist_without_news():
    source = FakeFundamentals()
    result = orchestrator(source).analyze("sbin")
    assert result.status == OK and result.skipped == {"news_sentiment": "not built yet"}  # no news hub was given
    assert set(result.specialists) == {"quant_technical", "valuation", "moat_quality", "earnings_intelligence"}
    assert all(out["data_coverage"] == "full" for out in result.specialists.values())
    assert validate_judge_verdict(result.verdict) == [] and result.verdict["verdict"] == "Buy"
    assert any("only 4 of 5 routed specialists ran" in note for note in result.notes)
    assert source.calls == ["SBIN"] * 3  # one call per specialist; LiveFundamentals caches the work behind them


def test_an_etf_never_calls_the_fundamentals_source_and_keeps_its_own_routing():
    source = FakeFundamentals()
    result = orchestrator(source).analyze("niftybees")
    assert source.calls == [] and set(result.specialists) == {"quant_technical"}
    assert result.skipped == {"etf_analyst": "not built yet"}


def test_without_a_fundamentals_source_the_three_are_listed_as_not_built_as_before():
    result = orchestrator(None).analyze("sbin")
    assert set(result.specialists) == {"quant_technical"}
    assert result.skipped == {name: "not built yet" for name in (*FUNDAMENTAL_SPECIALISTS, "news_sentiment")}


def test_a_fundamentals_failure_skips_the_three_but_the_technical_specialist_still_answers():
    result = orchestrator(FakeFundamentals(error=EmptyRefreshError("no statements for 'SBIN'"))).analyze("sbin")
    assert set(result.specialists) == {"quant_technical"} and result.status == OK
    assert set(result.skipped) == set(FUNDAMENTAL_SPECIALISTS) | {"news_sentiment"}
    assert all("EmptyRefreshError" in result.skipped[name] for name in FUNDAMENTAL_SPECIALISTS)
    assert any("only 1 of 5 routed specialists ran" in note for note in result.notes)


# ---- the news specialist, when a news hub is given

from datetime import date, datetime, timezone  # noqa: E402

from athena.contracts import AthenaError  # noqa: E402
from athena.news.hub import NewsHub  # noqa: E402
from athena.news.model import NewsItem, SourceResult  # noqa: E402

TODAY = date.today()


def headline(days_ago, title):
    day = date.fromordinal(TODAY.toordinal() - days_ago)
    return NewsItem(title, "ET", datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc), "https://x", "google news")


def with_news(hub, events=None):
    fetched = []
    router = FakeLLMRouter()
    built = build_orchestrator(
        make_resolver(), router, FakeChain(), fetch_bars=lambda s: fetched.append(s) or make_bars([100.0] * 60, symbol=s),
        fundamentals=FakeFundamentals(), news=hub, events=events,
    )
    return built, fetched


def test_with_a_news_hub_the_fifth_specialist_runs_after_the_bars_are_fetched():
    hub = NewsHub([("t", lambda symbol, names, since, until, extra="": SourceResult([headline(i + 1, f"SBIN headline {i}") for i in range(4)]))])
    built, fetched = with_news(hub)
    result = built.analyze("sbin")
    assert result.skipped == {} and "news_sentiment" in result.specialists and fetched.count("SBIN") >= 1
    assert result.specialists["news_sentiment"]["data_coverage"] == "full"


def test_a_news_hub_that_finds_nothing_makes_the_news_specialist_abstain_and_the_rest_still_answer():
    built, _ = with_news(NewsHub([("t", lambda symbol, names, since, until, extra="": SourceResult([]))]))
    result = built.analyze("sbin")
    assert result.specialists["news_sentiment"]["data_coverage"] == "insufficient" and "quant_technical" in result.specialists


def test_a_news_source_that_fails_is_a_note_in_the_packet_not_a_failed_analysis():
    def broken(symbol, names, since, until, extra=""):
        raise AthenaError("NSE announcements unavailable for SBIN: HTTP 403")

    built, _ = with_news(NewsHub([("t", broken)]))
    result = built.analyze("sbin")
    assert result.status == OK and result.specialists["news_sentiment"]["data_coverage"] == "insufficient"


def test_an_etf_is_not_routed_to_the_news_specialist():
    built, _ = with_news(NewsHub([("t", lambda symbol, names, since, until, extra="": SourceResult([]))]))
    assert "news_sentiment" not in built.analyze("niftybees").skipped
