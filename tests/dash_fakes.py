"""Shared fixtures for the dashboard tests: a realistic result, view and service built from synthetic data."""
from datetime import datetime, timezone
from functools import cache

from bar_factory import make_bars

from athena.backtest.cli import assumptions, run_rules
from athena.contracts import AthenaError
from athena.dashboard.backtest_view import build_backtest_view
from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, Resolution
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date
from fund_fixtures import ACME, PRICE, index_history

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
CLOSES = [100.0 + i * 0.5 for i in range(320)]
BARS = make_bars(CLOSES, symbol="SBIN")
TECHNICAL = build_technical_packet("SBIN", NOW, BARS)
FUNDAMENTALS = build_fundamentals_packet("SBIN", NOW, ACME, PRICE, NOW, index_history())
RISK = {
    "instrument": "SBIN",
    "as_of": NOW.isoformat(),
    "metrics": {"beta": {"value": 1.1, "unit": "ratio", "inputs": ["asset", "NIFTY 50"], "window": "252 returns", "source": "x"}},
    "missing": ["tracking_error"],
    "missing_reasons": {"tracking_error": "required series not provided"},
}
SPECIALIST = {"signal": "bullish", "confidence": 72, "reasoning": "Daily trend is up.", "data_coverage": "full", "missing": []}
VERDICT = {"verdict": "Buy", "conviction": 72, "key_risks": ["a data gap"], "resolution_path": "blend"}


def resolution(asset_class="equity", symbol="SBIN"):
    return Resolution(asset_class, "ticker", symbol, "State Bank of India", "INE062A01020", "exact", 1.0, (), ())


def ok_result(asset_class="equity", status=OK, specialists=None, verdict=None):
    return OrchestrationResult(
        status, "sbin", resolution(asset_class), None,
        {"quant_technical": SPECIALIST} if specialists is None else specialists,
        {"valuation": "not built yet"}, None, 1.0, verdict or VERDICT, ("risk overlay missing",),
    )


def ambiguous_result(count=1):
    candidates = (Candidate("equity", "SBIN", "State Bank of India", 0.81),) + tuple(
        Candidate("equity", f"SBI{n:02d}", f"SBI Holding {n}", 0.7) for n in range(1, count)
    )
    ambiguity = Ambiguity("sbi", candidates, "several matches")
    return OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ())


def full_view(asset_class="equity", status=OK, risk=RISK, fundamentals=None, fundamentals_note=None):
    return build_view(ok_result(asset_class, status), BARS, TECHNICAL, risk, fundamentals, fundamentals_note)


# A long climb, a slide and a recovery: the trend rule makes exactly one trade on it, the persona rule none.
TREND_CLOSES = [100.0 + i * 0.4 for i in range(400)] + [260.0 - i * 1.2 for i in range(60)] + [190.0 + i * 0.8 for i in range(100)]
TREND_BARS = make_bars(TREND_CLOSES, symbol="SBIN")
TREND_DAYS = [ist_date(bar.timestamp) for bar in TREND_BARS]
RATE = {day: 100.0 * 1.0002**i for i, day in enumerate(TREND_DAYS)}
INDEX = {day: 1000.0 + i for i, day in enumerate(TREND_DAYS)}


@cache
def trend_runs():
    return tuple(run_rules(TREND_BARS, ["trend", "persona"], RATE, INDEX))


def sample_backtest_view():
    return build_backtest_view("SBIN", trend_runs(), assumptions())


class FakeService:
    """Stands in for DashboardService: returns canned views, raises canned errors, and records the queries."""

    def __init__(self, view=None, error=None, backtest=None, backtest_error=None):
        self.canned, self.error, self.queries = view, error, []
        self.canned_backtest, self.backtest_error, self.backtests = backtest, backtest_error, []

    def backtest(self, identifier):
        self.backtests.append(identifier)
        if self.backtest_error:
            raise self.backtest_error
        return self.canned_backtest or sample_backtest_view()

    def view(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.canned


class RoutingService(FakeService):
    """A FakeService with one canned view per query, so a page can be driven through several searches."""

    def __init__(self, views):
        super().__init__()
        self.views = views

    def view(self, query):
        self.queries.append(query)
        return self.views[query]


CURRENT = {"service": FakeService(full_view())}


def athena_error(message="no LLM provider key is set"):
    return AthenaError(message)
