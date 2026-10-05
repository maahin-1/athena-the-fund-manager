from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import AthenaError
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.orchestrator.builders import HISTORY_DAYS, RequestCache
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.resolver import Resolution
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date


FundamentalsSource = Callable[[Resolution], dict]


class DashboardService:
    """Turns a typed instrument into a `DashboardView`: the orchestrator's verdict plus the charts and metric
    panels that sit around it."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        bars: RequestCache,
        risk_world: RiskWorld,
        clock: Callable[[], datetime] = utc_now,
        fundamentals: FundamentalsSource | None = None,
    ):
        self._orchestrator = orchestrator
        self._bars = bars
        self._risk_world = risk_world
        self._clock = clock
        self._fundamentals = fundamentals

    def view(self, query: str) -> DashboardView:
        self._bars.clear()
        if hasattr(self._fundamentals, "clear"):
            self._fundamentals.clear()
        result = self._orchestrator.analyze(query)
        if result.status == NEEDS_CLARIFICATION:
            return build_view(result)
        assert result.resolution is not None
        bars = self._bars(result.resolution.identifier)
        now = self._clock()
        technical = build_technical_packet(result.resolution.identifier, now, bars)
        risk = risk_packet(result.resolution, bars, self._risk_world, now)
        fundamentals, note = None, None
        if self._fundamentals is not None and result.resolution.asset_class == "equity":
            try:
                fundamentals = self._fundamentals(result.resolution)
            except AthenaError as exc:
                note = f"fundamentals unavailable: {exc}"
        return build_view(result, bars, technical, risk, fundamentals, note)


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel and the NIFTY 50 valuation history
    are loaded once, when the service is built, so a session left open across days should be restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=HISTORY_DAYS))
    orchestrator = build_orchestrator(
        sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals
    )
    return DashboardService(
        orchestrator, sources.bars, risk_world_from_store(sources.store), fundamentals=sources.fundamentals
    )
