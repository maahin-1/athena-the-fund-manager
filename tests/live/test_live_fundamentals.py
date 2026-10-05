from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.dashboard.service import live_service
from athena.loaders.fundamentals import DATASET, FundamentalsLoader
from athena.loaders.index_valuation import IndexValuationLoader, valuation_history
from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet
from athena.store import DataStore
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def world():
    now = utc_now()
    store = DataStore()
    IndexValuationLoader(store).refresh(since=ist_date(now) - timedelta(days=365 * 8))
    return {"now": now, "store": store, "history": valuation_history(store), "loader": FundamentalsLoader(store), "prices": JugaadPriceAdapter()}


def packet_for(world, symbol):
    world["loader"].refresh(symbol=symbol)
    record = world["loader"].read(DATASET, symbol)
    bars = world["prices"].fetch_ohlcv(symbol, "1d", since=ist_date(world["now"]) - timedelta(days=10))
    return build_fundamentals_packet(symbol, record.as_of, record.payload, bars[-1].close, world["now"], world["history"])


def test_live_index_valuation_history_is_long_and_current(world):
    history = world["history"]
    latest = max(history)
    print("\nNIFTY 50 P/E rows:", len(history), "latest", latest, history[latest])
    assert len(history) > 1500 and (ist_date(world["now"]) - latest).days <= 5


def test_live_non_lender_stock_has_every_core_figure(world):
    packet = packet_for(world, "TCS")
    print("\nTCS missing:", packet["missing_reasons"], "| flags:", packet["data_quality_flags"])
    for name in ("market_cap", "pe_trailing", "pb", "fcf_yield", "owner_earnings_yield", "roe_latest", "operating_margin_latest",
                 "revenue_cagr", "earnings_cagr", "debt_to_equity", "accruals_ratio", "eps_surprise_last", "index_pe_percentile"):
        assert name in packet["metrics"], (name, packet["missing_reasons"].get(name))
    assert 3 < packet["metrics"]["pe_trailing"]["value"] < 100 and packet["is_lender"] is False


def test_live_bank_drops_the_ratios_that_do_not_apply(world):
    packet = packet_for(world, "SBIN")
    assert packet["is_lender"] is True
    for name in ("fcf_yield", "operating_margin_latest", "debt_to_equity", "accruals_ratio"):
        assert packet["missing_reasons"][name] == LENDER_REASON
    assert {"pe_trailing", "pb", "roe_latest", "earnings_cagr"} <= set(packet["metrics"])


def test_live_a_renamed_symbol_fails_loud_with_a_clear_message(world):
    with pytest.raises(AthenaError, match="ZOMATO"):
        world["loader"].refresh(symbol="ZOMATO")  # now ETERNAL on the NSE


@pytest.fixture(scope="module")
def service():
    try:
        return live_service()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


def titles(view):
    return [panel.title for panel in view.panels]


def test_live_dashboard_shows_fundamentals_for_a_stock_but_not_an_etf(service):
    stock = service.view("TCS")
    print("\nTCS panels:", titles(stock), [p.coverage for p in stock.panels], "| notes:", stock.notes)
    assert titles(stock)[2:] == ["Valuation", "Business quality", "Earnings"]
    assert not any(note.startswith("fundamentals unavailable") for note in stock.notes)
    etf = service.view("NIFTYBEES")
    assert len(etf.panels) == 2 and any("do not apply to ETFs" in note for note in etf.notes)
