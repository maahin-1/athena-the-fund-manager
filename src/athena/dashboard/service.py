from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from athena.clock import utc_now
from athena.cli import build_orchestrator, live_sources
from athena.contracts import AthenaError, Bar
from athena.dashboard.risk import RiskWorld, refresh_risk_data, risk_packet, risk_world_from_store
from athena.dashboard.view import DashboardView, build_view
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.loaders.fundamentals import DATASET as FUNDAMENTALS_DATASET
from athena.loaders.fundamentals import FundamentalsLoader
from athena.loaders.index_valuation import IndexValuationLoader, valuation_history
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.orchestrator.builders import HISTORY_DAYS, history_fetcher
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.resolver import Resolution
from athena.store import DataStore
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date


INDEX_VALUATION_DAYS = 365 * 8  # NSE has published index P/E since 2015; eight years is plenty for a percentile


class RequestCache:
    """Remembers each symbol's bars for the duration of one request, so the specialists and the charts share one
    download. The service clears it at the start of every request."""

    def __init__(self, fetch: Callable[[str], list[Bar]]):
        self._fetch = fetch
        self._bars: dict[str, list[Bar]] = {}

    def __call__(self, symbol: str) -> list[Bar]:
        if symbol not in self._bars:
            self._bars[symbol] = self._fetch(symbol)
        return self._bars[symbol]

    def clear(self) -> None:
        self._bars.clear()


FundamentalsSource = Callable[[Resolution, list[Bar], datetime], dict]


class LiveFundamentals:
    """Fetches a stock's statements (Yahoo) into the store and builds its fundamentals packet against the last close
    and the NIFTY 50 valuation history. Raises an `AthenaError` when the source has nothing for the symbol."""

    def __init__(self, store: DataStore, loader: FundamentalsLoader, index_history: dict):
        self._store = store
        self._loader = loader
        self._index_history = index_history

    def __call__(self, resolution: Resolution, bars: list[Bar], now: datetime) -> dict:
        symbol = resolution.identifier
        self._loader.refresh(symbol=symbol)
        record = self._loader.read(FUNDAMENTALS_DATASET, symbol)
        price = bars[-1].close if bars else None
        return build_fundamentals_packet(symbol, record.as_of, record.payload, price, now, self._index_history)


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
                fundamentals = self._fundamentals(result.resolution, bars, now)
            except AthenaError as exc:
                note = f"fundamentals unavailable: {exc}"
        return build_view(result, bars, technical, risk, fundamentals, note)


def live_service(env_file: Path | str = DEFAULT_ENV_FILE) -> DashboardService:
    """The dashboard wired to real sources. The market series for the risk panel are loaded once, when the service
    is built, so a session left open across days should be restarted."""
    sources = live_sources(env_file)
    refresh_risk_data(sources.store, since=ist_date(utc_now()) - timedelta(days=HISTORY_DAYS))
    IndexValuationLoader(sources.store).refresh(since=ist_date(utc_now()) - timedelta(days=INDEX_VALUATION_DAYS))
    fundamentals = LiveFundamentals(sources.store, FundamentalsLoader(sources.store), valuation_history(sources.store))
    cache = RequestCache(history_fetcher(sources.chain))
    orchestrator = build_orchestrator(sources.resolver, sources.llm_router, sources.chain, fetch_bars=cache)
    return DashboardService(orchestrator, cache, risk_world_from_store(sources.store), fundamentals=fundamentals)
