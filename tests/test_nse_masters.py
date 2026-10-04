from datetime import datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.nse_masters import (
    EQUITY_DATASET,
    EQUITY_URL,
    ETF_DATASET,
    ETF_URL,
    NseMasterLoader,
)
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)

EQUITY_CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "SBIN,State Bank of India,EQ,01-MAR-1995,1,1,INE062A01020,1\n"
    "20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n"
)
ETF_CSV = (
    "Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue,ETF Underlying,Underlying Key\r\n"
    "NIFTYBEES,Nifty 50,NIPINDETFNIFTYBEES,08-Jan-02,1,INF204KB14I2,1,EQUITY,Nifty 50\r\n"
)


def make_loader(store, text, urls=None):
    def fetch_text(url):
        if urls is not None:
            urls.append(url)
        return text

    return NseMasterLoader(store, fetch_text=fetch_text, clock=lambda: NOW)


def test_equity_refresh_writes_a_record_per_symbol_and_tolerates_header_spaces():
    store = DataStore()
    result = make_loader(store, EQUITY_CSV).refresh(EQUITY_DATASET)
    assert result.rows_written == 2
    assert store.latest(EQUITY_DATASET, "SBIN").payload == {
        "name": "State Bank of India",
        "series": "EQ",
        "isin": "INE062A01020",
        "listing_date": "01-MAR-1995",
    }
    assert store.latest(EQUITY_DATASET, "SBIN").as_of == NOW


def test_etf_refresh_keeps_underlying_index():
    store = DataStore()
    make_loader(store, ETF_CSV).refresh(ETF_DATASET)
    assert store.latest(ETF_DATASET, "NIFTYBEES").payload == {
        "name": "NIPINDETFNIFTYBEES",
        "isin": "INF204KB14I2",
        "underlying": "Nifty 50",
        "underlying_class": "EQUITY",
        "underlying_key": "Nifty 50",
    }


def test_each_dataset_uses_its_own_url():
    urls = []
    make_loader(DataStore(), EQUITY_CSV, urls).refresh(EQUITY_DATASET)
    make_loader(DataStore(), ETF_CSV, urls).refresh(ETF_DATASET)
    assert urls == [EQUITY_URL, ETF_URL]


def test_missing_column_is_schema_change():
    bad = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\nSBIN,State Bank of India,EQ,01-MAR-1995\n"
    with pytest.raises(SchemaChangedError, match="ISIN NUMBER"):
        make_loader(DataStore(), bad).refresh(EQUITY_DATASET)


def test_header_only_and_blank_files_are_empty_refresh():
    header = EQUITY_CSV.splitlines()[0] + "\n"
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), header).refresh(EQUITY_DATASET)
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), "").refresh(EQUITY_DATASET)


def test_unknown_dataset_is_rejected():
    with pytest.raises(ValueError, match="master.nse_equity"):
        make_loader(DataStore(), EQUITY_CSV).refresh("master.other")


def test_read_missing_raises_and_present_returns():
    store = DataStore()
    loader = make_loader(store, EQUITY_CSV)
    with pytest.raises(EmptyRefreshError):
        loader.read(EQUITY_DATASET, "SBIN")
    loader.refresh(EQUITY_DATASET)
    assert loader.read(EQUITY_DATASET, "SBIN").payload["isin"] == "INE062A01020"


def test_describe_lists_both_datasets_and_protocol_holds():
    loader = make_loader(DataStore(), EQUITY_CSV)
    assert set(loader.describe()) == {EQUITY_DATASET, ETF_DATASET}
    assert isinstance(loader, BatchLoader)
