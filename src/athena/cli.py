from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.fallback import FallbackChain
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.llm.router import Router, build_router
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.orchestrator.builders import history_fetcher, technical_packet_builder
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, Orchestrator
from athena.orchestrator.report import format_result
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar


def build_orchestrator(
    resolver: InstrumentResolver, llm_router: Router, chain: FallbackChain, clock: Callable[[], datetime] = utc_now
) -> Orchestrator:
    """Wire the specialists that exist (today only Quant/Technical) to their data and models."""
    specialists = {"quant_technical": Specialist(QUANT_TECHNICAL, llm_router.client_for("specialist"))}
    builders = {"quant_technical": technical_packet_builder(history_fetcher(chain, clock=clock), clock)}
    return Orchestrator(resolver, specialists, builders)


def live_orchestrator(env_file: Path | str = DEFAULT_ENV_FILE) -> Orchestrator:
    """Everything wired to real sources: NSE master lists and holidays, jugaad-data then Yahoo for prices, and
    whichever LLM providers have keys. Loads into a fresh in-memory store on every call."""
    llm_router = build_router(env_file=env_file)
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    NseHolidayLoader(store).refresh()
    years = [int(record.key) for record in store.latest_records(HOLIDAY_DATASET)]
    calendar = load_calendar(store, years) if years else TradingCalendar(frozenset())
    resolver = InstrumentResolver(InstrumentIndex.from_store(store, calendar=calendar))
    chain = ohlcv_chain([JugaadPriceAdapter(), YahooPriceAdapter()], calendar)
    return build_orchestrator(resolver, llm_router, chain)


def main(argv: list[str] | None = None, factory: Callable[..., Orchestrator] = live_orchestrator) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.cli", description="Analyze one instrument.")
    parser.add_argument("query", nargs="+", help="ticker, ISIN or name, for example SBIN")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args(argv)
    try:
        result = factory(env_file=args.env_file).analyze(" ".join(args.query))
    except (AthenaError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(format_result(result))
    return 2 if result.status == NEEDS_CLARIFICATION else 0


if __name__ == "__main__":
    raise SystemExit(main())
