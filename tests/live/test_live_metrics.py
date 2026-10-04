from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.clock import utc_now
from athena.loaders.index_close import PRICE_DATASET, RATE_DATASET, RATE_INDEX, IndexCloseLoader
from athena.loaders.index_tri import DATASET as TRI_DATASET
from athena.loaders.index_tri import IndexTriLoader
from athena.metrics.packets import build_packet
from athena.metrics.series import series_from_bars, series_from_store
from athena.store import DataStore
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live

LOOKBACK_DAYS = 420


@pytest.fixture(scope="module")
def world():
    since = ist_date(utc_now()) - timedelta(days=LOOKBACK_DAYS)
    store = DataStore()
    IndexCloseLoader(store).refresh(since=since)
    IndexCloseLoader(store, dataset=RATE_DATASET, default_index=RATE_INDEX).refresh(since=since)
    IndexTriLoader(store).refresh(since=since)
    adapter = JugaadPriceAdapter()
    return {
        "since": since,
        "price_index": series_from_store(store, PRICE_DATASET, "NIFTY 50", "close"),
        "rate": series_from_store(store, RATE_DATASET, RATE_INDEX, "close"),
        "tri": series_from_store(store, TRI_DATASET, "NIFTY 50", "tri"),
        "sbin": series_from_bars(adapter.fetch_ohlcv("SBIN", "1d", since=since)),
        "niftybees": series_from_bars(adapter.fetch_ohlcv("NIFTYBEES", "1d", since=since)),
    }


def test_live_stock_packet_is_complete_and_sane(world):
    packet = build_packet(
        "SBIN", utc_now(), world["sbin"],
        benchmark=world["price_index"], benchmark_name="NIFTY 50",
        riskfree=world["rate"],
    )
    metrics = packet["metrics"]
    print("SBIN", {k: v["value"] for k, v in metrics.items()}, packet["missing_reasons"])
    assert {"volatility_annualized", "max_drawdown", "beta", "alpha_annualized", "sharpe"} <= set(metrics)
    assert 0.10 < metrics["volatility_annualized"]["value"] < 0.70
    assert -0.80 < metrics["max_drawdown"]["value"] < 0.0
    assert 0.3 < metrics["beta"]["value"] < 2.2
    assert metrics["beta"]["window"].startswith("2")  # roughly a year of returns


def test_live_riskfree_is_a_plausible_overnight_rate(world):
    days = sorted(world["rate"])[-253:]
    growth = world["rate"][days[-1]] / world["rate"][days[0]] - 1.0
    years = (days[-1] - days[0]).days / 365.0
    annualised = (1.0 + growth) ** (1.0 / years) - 1.0
    print("overnight rate, annualised over the window:", round(annualised, 4))
    assert 0.02 < annualised < 0.10


def test_live_etf_tracking_error_against_the_index_tri(world):
    packet = build_packet(
        "NIFTYBEES", utc_now(), world["niftybees"],
        tracking_index=world["tri"], tracking_index_name="NIFTY 50 TRI",
    )
    metrics = packet["metrics"]
    print("NIFTYBEES", {k: v["value"] for k, v in metrics.items()}, packet["missing_reasons"])
    assert {"tracking_error", "tracking_difference"} <= set(metrics)
    assert metrics["tracking_error"]["value"] < 0.04  # market-price basis, see the packet note
    assert abs(metrics["tracking_difference"]["value"]) < 0.10
