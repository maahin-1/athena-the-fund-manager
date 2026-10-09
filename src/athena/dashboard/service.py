from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path

from athena.backtest.cli import assumptions_for, run_rules
from athena.backtest.rules import RULES, Rule, SeriesRule
from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import AthenaError, Bar
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.backtest_view import BacktestView, build_backtest_view
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.orchestrator.builders import RequestCache, history_fetcher
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.resolver import Resolution
from athena.risk_overlay.model import Overlay
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

BACKTEST_YEARS = 8


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
        long_history: Callable[[str], list[Bar]] | None = None,
    ):
        self._orchestrator = orchestrator
        self._bars = bars
        self._risk_world = risk_world
        self._clock = clock
        self._fundamentals = fundamentals
        self._long_history = long_history

    def view(self, query: str, overlay: Overlay | None = None) -> DashboardView:
        self._bars.clear()
        if hasattr(self._fundamentals, "clear"):
            self._fundamentals.clear()
        result = self._orchestrator.analyze(query) if overlay is None else self._orchestrator.analyze(query, overlay)
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


    def backtest(self, identifier: str, rules: Sequence[str | Rule | SeriesRule] | None = None) -> BacktestView:
        """The given rules (the two built-in technical rules by default) over the long history, each against buy and hold,
        with the blinding check."""
        if self._long_history is None:
            raise AthenaError("backtests are not available in this session")
        runs = run_rules(
            self._long_history(identifier), list(RULES) if rules is None else list(rules), self._risk_world.rate, self._risk_world.price_index
        )
        return build_backtest_view(identifier, runs, assumptions_for(runs))


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel and the backtest, and the NIFTY 50
    valuation history, are loaded once when the service is built, so a session left open across days should be
    restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=365 * BACKTEST_YEARS))
    orchestrator = build_orchestrator(
        sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals
    )
    long_history = lru_cache(maxsize=8)(history_fetcher(sources.chain, days=365 * BACKTEST_YEARS))
    return DashboardService(
        orchestrator, sources.bars, risk_world_from_store(sources.store), fundamentals=sources.fundamentals,
        long_history=long_history,
    )
