"""Shared fixtures for the dashboard tests: a realistic result, view and service built from synthetic data."""
from datetime import datetime, timezone

from bar_factory import make_bars

from athena.contracts import AthenaError
from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, Resolution
from athena.technicals.packet import build_technical_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
CLOSES = [100.0 + i * 0.5 for i in range(320)]
BARS = make_bars(CLOSES, symbol="SBIN")
TECHNICAL = build_technical_packet("SBIN", NOW, BARS)
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


def ambiguous_result():
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.81),), "several matches")
    return OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ())


def full_view(asset_class="equity", status=OK, risk=RISK):
    return build_view(ok_result(asset_class, status), BARS, TECHNICAL, risk)


class FakeService:
    """Stands in for DashboardService: returns canned views, raises canned errors, and records the queries."""

    def __init__(self, view=None, error=None):
        self.canned, self.error, self.queries = view, error, []

    def view(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.canned


CURRENT = {"service": FakeService(full_view())}


def athena_error(message="no LLM provider key is set"):
    return AthenaError(message)

