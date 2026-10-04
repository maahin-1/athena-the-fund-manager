from datetime import datetime, timezone

import pytest

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter
from athena.loaders.index_tri import IndexTriLoader
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.store import DataStore

pytestmark = pytest.mark.live


def test_live_holidays():
    store = DataStore()
    result = NseHolidayLoader(store).refresh()
    assert result.rows_written >= 1
    calendar = load_calendar(store, [datetime.now(timezone.utc).year])
    assert len(calendar.holidays) >= 5


def test_live_equity_master():
    store = DataStore()
    result = NseMasterLoader(store).refresh(EQUITY_DATASET)
    assert result.rows_written > 1000
    assert store.latest(EQUITY_DATASET, "SBIN").payload["isin"].startswith("INE")


def test_live_etf_master():
    store = DataStore()
    NseMasterLoader(store).refresh(ETF_DATASET)
    etf = store.latest(ETF_DATASET, "NIFTYBEES")
    assert etf.payload["isin"].startswith("INF")
    assert etf.payload["underlying_key"]


def test_live_jugaad_prices():
    bars = JugaadPriceAdapter().fetch_ohlcv("SBIN", "1d", limit=3)
    assert bars and all(bar.close > 0 for bar in bars)


def test_live_yahoo_prices():
    bars = YahooPriceAdapter().fetch_ohlcv("SBIN", "1d", limit=3)
    assert bars and all(bar.close > 0 for bar in bars)


def test_live_index_tri():
    store = DataStore()
    result = IndexTriLoader(store).refresh()
    assert result.rows_written >= 3
    assert store.latest("index.tri", "NIFTY 50").payload["tri"] > 10000
