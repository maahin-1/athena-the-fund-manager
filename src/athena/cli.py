from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.agents.base import Specialist
from athena.agents.earnings_intelligence import EARNINGS_INTELLIGENCE
from athena.agents.moat_quality import MOAT_QUALITY
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.agents.valuation import VALUATION
from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.fallback import FallbackChain
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.llm.router import Router, build_router
from athena.loaders.fundamentals import FundamentalsLoader
from athena.loaders.index_valuation import IndexValuationLoader, valuation_history
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.orchestrator.builders import RequestCache, history_fetcher, technical_packet_builder
from athena.orchestrator.fundamentals_source import LiveFundamentals
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.orchestrator.report import format_result
from athena.resolver import InstrumentIndex, InstrumentResolver, Resolution
from athena.risk_overlay.parse import build_overlay
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar, ist_date

INDEX_VALUATION_DAYS = 365 * 8  # NSE has published index P/E since 2015; eight years is plenty for a percentile
FUNDAMENTAL_SPECIALISTS = {
    "valuation": VALUATION,
    "moat_quality": MOAT_QUALITY,
    "earnings_intelligence": EARNINGS_INTELLIGENCE,
}


def build_orchestrator(
    resolver: InstrumentResolver,
    llm_router: Router,
    chain: FallbackChain,
    clock: Callable[[], datetime] = utc_now,
    fetch_bars: Callable[[str], list[Bar]] | None = None,
    fundamentals: Callable[[Resolution], dict] | None = None,
) -> Orchestrator:
    """Wire the specialists to their data and models: Quant/Technical always, and Valuation, Moat & Quality and
    Earnings Intelligence when a `fundamentals` source is given (all three read the same packet).
    `fetch_bars` and `fundamentals` let a caller share downloads with other consumers (the dashboard does)."""
    fetch = fetch_bars or history_fetcher(chain, clock=clock)
    client = llm_router.client_for("specialist")
    specialists = {"quant_technical": Specialist(QUANT_TECHNICAL, client)}
    builders = {"quant_technical": technical_packet_builder(fetch, clock)}
    if fundamentals is not None:
        for name, spec in FUNDAMENTAL_SPECIALISTS.items():
            specialists[name] = Specialist(spec, client)
            builders[name] = fundamentals
    return Orchestrator(resolver, specialists, builders, bars=fetch)


@dataclass(frozen=True)
class LiveSources:
    resolver: InstrumentResolver
    llm_router: Router
    chain: FallbackChain
    store: DataStore
    bars: RequestCache
    fundamentals: LiveFundamentals


def live_sources(env_file: Path | str = DEFAULT_ENV_FILE) -> LiveSources:
    """Everything wired to real sources: NSE master lists, holidays and index valuation, jugaad-data then Yahoo for
    prices, Yahoo for statements, and whichever LLM providers have keys. Loads into a fresh in-memory store on every
    call."""
    llm_router = build_router(env_file=env_file)
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    NseHolidayLoader(store).refresh()
    years = [int(record.key) for record in store.latest_records(HOLIDAY_DATASET)]
    calendar = load_calendar(store, years) if years else TradingCalendar(frozenset())
    resolver = InstrumentResolver(InstrumentIndex.from_store(store, calendar=calendar), confirm_related=True)
    chain = ohlcv_chain([JugaadPriceAdapter(), YahooPriceAdapter()], calendar)
    IndexValuationLoader(store).refresh(since=ist_date(utc_now()) - timedelta(days=INDEX_VALUATION_DAYS))
    bars = RequestCache(history_fetcher(chain))
    fundamentals = LiveFundamentals(FundamentalsLoader(store), valuation_history(store), bars)
    return LiveSources(resolver, llm_router, chain, store, bars, fundamentals)


def live_orchestrator(env_file: Path | str = DEFAULT_ENV_FILE) -> Orchestrator:
    sources = live_sources(env_file)
    return build_orchestrator(
        sources.resolver, sources.llm_router, sources.chain, fetch_bars=sources.bars, fundamentals=sources.fundamentals
    )


def main(argv: list[str] | None = None, factory: Callable[..., Orchestrator] = live_orchestrator) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.cli", description="Analyze one instrument.")
    parser.add_argument("query", nargs="+", help="ticker, ISIN or name, for example SBIN")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--profile", help="your risk limits: conservative, moderate, aggressive, or a JSON file")
    parser.add_argument("--holdings", metavar="FILE.csv", help="what you own now: a CSV with the columns symbol,value (rupees)")
    parser.add_argument("--amount", type=float, help="rupees you are thinking of putting into this instrument")
    args = parser.parse_args(argv)
    try:
        overlay = build_overlay(args.profile, args.holdings, args.amount)
        orchestrator = factory(env_file=args.env_file)
        query = " ".join(args.query)
        result = orchestrator.analyze(query) if overlay is None else orchestrator.analyze(query, overlay)
    except (AthenaError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(format_result(result))
    return 2 if result.status == NEEDS_CLARIFICATION else 0


if __name__ == "__main__":
    raise SystemExit(main())
