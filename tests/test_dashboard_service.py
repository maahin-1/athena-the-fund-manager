import pytest
from bar_factory import NOW, make_bars
from dash_fakes import FUNDAMENTALS, INDEX, RATE, TREND_BARS, ambiguous_result, resolution

from athena.contracts import AthenaError, EmptyRefreshError
from athena.dashboard.risk import RiskWorld
from athena.dashboard.service import DashboardService
from athena.strategies.compile import strategy_rule
from athena.strategies.presets import PRESETS
from athena.orchestrator.builders import RequestCache
from athena.orchestrator.orchestrator import OK, OrchestrationResult

BARS = make_bars([100.0 + i * 0.5 for i in range(320)], symbol="SBIN")
VERDICT = {"verdict": "Hold", "conviction": 20, "key_risks": [], "resolution_path": "blend"}


class CountingFetch:
    def __init__(self):
        self.symbols = []

    def __call__(self, symbol):
        self.symbols.append(symbol)
        return BARS


class FakeOrchestrator:
    """Like the real one, it pulls the bars through the shared cache while it works."""

    def __init__(self, cache, result=None):
        self.cache, self.result, self.queries = cache, result, []

    def analyze(self, query):
        self.queries.append(query)
        result = self.result or OrchestrationResult(OK, query, resolution(), None, {}, {}, None, 0.0, VERDICT, ())
        if result.resolution:
            self.cache(result.resolution.identifier)
        return result


def make_service(result=None):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    orchestrator = FakeOrchestrator(cache, result)
    service = DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW)
    return service, fetch, orchestrator


def test_a_view_shares_one_download_between_the_specialists_and_the_charts():
    service, fetch, orchestrator = make_service()
    view = service.view("sbin")
    assert orchestrator.queries == ["sbin"] and fetch.symbols == ["SBIN"]
    assert view.identifier == "SBIN" and view.candles
    assert [panel.title.split()[0] for panel in view.panels] == ["Technical", "Risk"]


def test_the_technical_panel_is_computed_and_the_risk_panel_reports_what_it_could_not():
    view = make_service()[0].view("sbin")
    technical, risk = view.panels
    assert technical.coverage == "full" and any(row.name == "rsi_14" for row in technical.rows)
    assert risk.coverage == "partial" and {"volatility_annualized", "max_drawdown"} <= {r.name for r in risk.rows}
    assert "beta" in risk.missing  # the (empty) market series were not available


def test_every_request_starts_with_a_fresh_download():
    service, fetch, _ = make_service()
    service.view("sbin")
    service.view("sbin")
    assert fetch.symbols == ["SBIN", "SBIN"]


def test_an_ambiguous_query_downloads_nothing_and_returns_candidates():
    service, fetch, _ = make_service(ambiguous_result())
    view = service.view("sbi")
    assert fetch.symbols == [] and view.candidates and view.candles == ()


# ---- fundamentals source
class FakeFundamentals:
    def __init__(self, packet=None, error=None):
        self.packet, self.error, self.calls, self.clears = packet, error, [], 0

    def __call__(self, resolution):
        self.calls.append(resolution.identifier)
        if self.error:
            raise self.error
        return self.packet

    def clear(self):
        self.clears += 1


def service_with(fundamentals, asset_class="equity"):
    fetch = CountingFetch()
    cache = RequestCache(fetch)
    result = OrchestrationResult(OK, "sbin", resolution(asset_class), None, {}, {}, None, 0.0, VERDICT, ())
    orchestrator = FakeOrchestrator(cache, result)
    return DashboardService(orchestrator, cache, RiskWorld({}, {}, {}), clock=lambda: NOW, fundamentals=fundamentals)


def test_a_stock_view_gets_the_fundamentals_panels_built_from_the_same_bars():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source).view("sbin")
    assert source.calls == ["SBIN"]
    assert [p.title for p in view.panels][2:] == ["Valuation", "Business quality", "Earnings"]


def test_each_request_starts_by_clearing_the_fundamentals_cache():
    source = FakeFundamentals(FUNDAMENTALS)
    service = service_with(source)
    service.view("sbin")
    service.view("sbin")
    assert source.clears == 2


def test_an_etf_never_asks_for_fundamentals():
    source = FakeFundamentals(FUNDAMENTALS)
    view = service_with(source, "etf").view("niftybees")
    assert source.calls == [] and len(view.panels) == 2


def test_a_fundamentals_failure_becomes_a_note_and_the_rest_of_the_page_survives():
    view = service_with(FakeFundamentals(error=EmptyRefreshError("no statements for 'SBIN'"))).view("sbin")
    assert len(view.panels) == 2 and view.candles
    assert "fundamentals unavailable: no statements for 'SBIN'" in view.notes


def test_without_a_fundamentals_source_nothing_changes():
    assert len(service_with(None).view("sbin").panels) == 2


def test_a_backtest_needs_a_long_history_source():
    service = make_service()[0]
    with pytest.raises(AthenaError, match="backtests are not available"):
        service.backtest("SBIN")


def test_a_backtest_replays_both_rules_on_the_long_history_of_the_identifier():
    asked = []

    def long_history(symbol):
        asked.append(symbol)
        return TREND_BARS

    cache = RequestCache(CountingFetch())
    service = DashboardService(
        FakeOrchestrator(cache), cache, RiskWorld(INDEX, RATE, {}), clock=lambda: NOW, long_history=long_history
    )
    view = service.backtest("SBIN")
    assert asked == ["SBIN"] and view.identifier == "SBIN"
    assert [panel.rule for panel in view.panels] == ["trend", "persona"]
    assert "15 bps per side" in view.assumptions and all(panel.blinded_identical for panel in view.panels)


def test_a_backtest_can_run_a_strategy_instead_of_the_built_in_rules_and_describes_it():
    cache = RequestCache(CountingFetch())
    service = DashboardService(
        FakeOrchestrator(cache), cache, RiskWorld(INDEX, RATE, {}), clock=lambda: NOW, long_history=lambda symbol: TREND_BARS
    )
    view = service.backtest("SBIN", [strategy_rule(PRESETS["rsi_reversion"]), "trend"])
    assert [panel.rule for panel in view.panels] == ["RSI mean reversion", "trend"]
    assert view.panels[0].description.startswith("Buy when the RSI (length 14) is below 30.")
    assert "a stop that depends on the rule" not in view.assumptions  # both use a 2 x ATR stop
    assert [panel.rule for panel in service.backtest("SBIN").panels] == ["trend", "persona"]  # the default is unchanged
