from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.contracts import Bar
from athena.loaders import index_close, index_tri
from athena.loaders.index_close import PRICE_DATASET, PRICE_INDEX, RATE_DATASET, RATE_INDEX, IndexCloseLoader
from athena.loaders.index_tri import DATASET as TRI_DATASET
from athena.loaders.index_tri import IndexTriLoader
from athena.metrics.packets import build_packet
from athena.metrics.series import Series, series_from_bars, series_from_store
from athena.resolver import Resolution
from athena.store import DataStore

# ETFs whose tracked index is known and has a total-return series loaded. Only NIFTYBEES has been checked
# against the NIFTY 50 TRI (5 Oct 2026); an ETF not listed gets no tracking metrics rather than a guess.
ETF_TRACKING_INDEX = {"NIFTYBEES": "NIFTY 50"}
BENCHMARK_NAME = PRICE_INDEX


@dataclass(frozen=True)
class RiskWorld:
    """The market series every risk metric is measured against."""

    price_index: Series  # NIFTY 50 close
    rate: Series  # Nifty 1D Rate Index level (the risk-free accrual)
    tri: Series  # NIFTY 50 total-return index


WAREHOUSE_SOURCE = "warehouse.index_bars"


def warehouse_closes(path: Any) -> Callable[[str, date, date], list[dict]] | None:
    """A fetch for `IndexCloseLoader` that reads the index's closes from the market warehouse (the exchange's own daily index file), in the rows the loader
    parses; None when there is no warehouse file. The name is matched without regard to case ("NIFTY 50" is held as "Nifty 50"). No network is asked."""
    from pathlib import Path

    if path is None or not Path(path).exists():
        return None

    def fetch(index_name: str, start: date, end: date) -> list[dict]:
        from athena.warehouse.warehouse import Warehouse

        with Warehouse(path, read_only=True, wait_seconds=30) as warehouse:
            names = {n.lower(): n for n in warehouse.index_names()}
            closes = warehouse.index_close(names.get(index_name.lower(), index_name))
        return [{"HistoricalDate": d.strftime("%d %b %Y"), "CLOSE": c} for d, c in closes.items() if start <= d <= end]

    return fetch


def refresh_risk_data_from(
    store: DataStore, since: date, warehouse: Any, clock: Callable[[], datetime] = utc_now, tri_fetch: Callable[[str, date, date], list[dict]] = index_tri.default_fetch,
) -> list[str]:
    """As `refresh_risk_data`, reading the NIFTY 50 and the overnight rate from the warehouse when it holds them (no network, the exchange's own figures) and
    from the index site otherwise. The total-return index comes only from the index site; when that fails the ETF tracking figures are left out and a note
    says so, rather than the service failing to start. Returns the notes."""
    notes: list[str] = []
    held = warehouse_closes(warehouse)
    for dataset, name in ((PRICE_DATASET, PRICE_INDEX), (RATE_DATASET, RATE_INDEX)):
        if held is not None:
            loader = IndexCloseLoader(store, dataset=dataset, default_index=name, fetch=held, clock=clock, source=WAREHOUSE_SOURCE)
            try:
                loader.refresh(since=since)
                continue
            except AthenaError:
                pass  # the warehouse has none in the window: ask the index site
        IndexCloseLoader(store, dataset=dataset, default_index=name, clock=clock).refresh(since=since)
    try:
        IndexTriLoader(store, fetch=tri_fetch, clock=clock).refresh(since=since)
    except Exception as exc:  # the index site answers with a page that is not data now and then
        notes.append(f"The NIFTY 50 total-return index could not be loaded ({type(exc).__name__}); ETF tracking figures are left out.")
    return notes


def refresh_risk_data(
    store: DataStore,
    since: date,
    price_fetch: Callable[[str, date, date], list[dict]] = index_close.default_fetch,
    rate_fetch: Callable[[str, date, date], list[dict]] = index_close.default_fetch,
    tri_fetch: Callable[[str, date, date], list[dict]] = index_tri.default_fetch,
    clock: Callable[[], datetime] = utc_now,
) -> None:
    """Pull the NIFTY 50 close, the overnight-rate index and the NIFTY 50 TRI into the store since `since`."""
    IndexCloseLoader(store, fetch=price_fetch, clock=clock).refresh(since=since)
    IndexCloseLoader(store, dataset=RATE_DATASET, default_index=RATE_INDEX, fetch=rate_fetch, clock=clock).refresh(since=since)
    IndexTriLoader(store, fetch=tri_fetch, clock=clock).refresh(since=since)


def risk_world_from_store(store: DataStore) -> RiskWorld:
    return RiskWorld(
        price_index=series_from_store(store, PRICE_DATASET, PRICE_INDEX, "close"),
        rate=series_from_store(store, RATE_DATASET, RATE_INDEX, "close"),
        tri=series_from_store(store, TRI_DATASET, "NIFTY 50", "tri"),
    )


def risk_packet(resolution: Resolution, bars: Sequence[Bar], world: RiskWorld, now: datetime) -> dict[str, Any]:
    """The Phase 0d metrics packet for one instrument: volatility, drawdown, beta, alpha and Sharpe against the
    NIFTY 50 and the overnight rate, plus tracking error and difference for an ETF with a known index."""
    tracked = resolution.asset_class == "etf" and resolution.identifier in ETF_TRACKING_INDEX
    return build_packet(
        resolution.identifier,
        now,
        series_from_bars(bars),
        benchmark=world.price_index,
        benchmark_name=BENCHMARK_NAME,
        riskfree=world.rate,
        tracking_index=world.tri if tracked else None,
        tracking_index_name=f"{ETF_TRACKING_INDEX[resolution.identifier]} TRI" if tracked else None,
    )
