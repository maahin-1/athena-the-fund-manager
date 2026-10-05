import random
from datetime import date, datetime, timedelta, timezone

import pytest
from bar_factory import make_bars

from athena.dashboard.risk import ETF_TRACKING_INDEX, refresh_risk_data, risk_packet, risk_world_from_store
from athena.resolver import Resolution
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
START = date(2025, 6, 2)


def weekdays(start, end):
    day, days = start, []
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = weekdays(START, date(2026, 10, 2))


def levels(seed, drift, vol, base=1000.0):
    rng, level, out = random.Random(seed), base, []
    for _ in DAYS:
        level *= 1 + rng.gauss(drift, vol)
        out.append(level)
    return out


NIFTY = levels(1, 0.0004, 0.008)
RATE = [1000 * (1 + 0.00014) ** i for i in range(len(DAYS))]
TRI = [v * 1.01 for v in NIFTY]


def close_rows(values):
    return [{"HistoricalDate": d.strftime("%d %b %Y"), "CLOSE": str(v)} for d, v in zip(DAYS, values)]


def tri_rows():
    return [{"Date": d.strftime("%d %b %Y"), "TotalReturnsIndex": str(v), "NTR_Value": str(v)} for d, v in zip(DAYS, TRI)]


def populated_store():
    store = DataStore()
    refresh_risk_data(
        store, START,
        price_fetch=lambda name, s, e: close_rows(NIFTY),
        rate_fetch=lambda name, s, e: close_rows(RATE),
        tri_fetch=lambda name, s, e: tri_rows(),
        clock=lambda: NOW,
    )
    return store


WORLD = risk_world_from_store(populated_store())


def resolution(symbol, asset_class):
    return Resolution(asset_class, "ticker", symbol, symbol, "", "exact", 1.0, (), ())


def asset_bars(symbol, values):
    # market-priced asset that follows NIFTY with some noise
    return make_bars([v / 10 for v in values], end=date(2026, 10, 2), symbol=symbol)


def test_refresh_then_read_gives_aligned_series_for_all_three_datasets():
    assert len(WORLD.price_index) == len(DAYS) and len(WORLD.rate) == len(DAYS) and len(WORLD.tri) == len(DAYS)
    assert WORLD.price_index[DAYS[10]] == pytest.approx(NIFTY[10])
    assert WORLD.tri[DAYS[10]] == pytest.approx(TRI[10])


def test_a_stock_gets_volatility_drawdown_beta_alpha_and_sharpe_but_no_tracking_metrics():
    rng = random.Random(7)
    asset = [n * (1 + rng.gauss(0, 0.004)) for n in NIFTY]
    packet = risk_packet(resolution("SBIN", "equity"), asset_bars("SBIN", asset), WORLD, NOW)
    assert {"volatility_annualized", "max_drawdown", "beta", "alpha_annualized", "sharpe"} <= set(packet["metrics"])
    assert {"tracking_error", "tracking_difference"} <= set(packet["missing"])
    assert packet["metrics"]["beta"]["inputs"] == ["asset", "NIFTY 50"]
    assert 0.7 < packet["metrics"]["beta"]["value"] < 1.3  # it follows the index


def test_a_mapped_etf_also_gets_tracking_error_and_difference_against_the_tri():
    assert "NIFTYBEES" in ETF_TRACKING_INDEX
    packet = risk_packet(resolution("NIFTYBEES", "etf"), asset_bars("NIFTYBEES", TRI), WORLD, NOW)
    assert {"tracking_error", "tracking_difference"} <= set(packet["metrics"])
    assert packet["metrics"]["tracking_error"]["inputs"] == ["asset", "NIFTY 50 TRI"]
    assert packet["metrics"]["tracking_error"]["value"] < 0.001  # the asset is the index


def test_an_etf_with_no_known_index_gets_no_tracking_metrics():
    packet = risk_packet(resolution("GOLDBEES", "etf"), asset_bars("GOLDBEES", NIFTY), WORLD, NOW)
    assert "tracking_error" in packet["missing"] and "tracking_difference" in packet["missing"]


def test_a_stock_with_a_symbol_in_the_etf_map_is_not_treated_as_tracking():
    packet = risk_packet(resolution("NIFTYBEES", "equity"), asset_bars("NIFTYBEES", NIFTY), WORLD, NOW)
    assert "tracking_error" in packet["missing"]


def test_an_empty_store_gives_a_packet_with_everything_missing_not_a_crash():
    empty = risk_world_from_store(DataStore())
    packet = risk_packet(resolution("SBIN", "equity"), asset_bars("SBIN", NIFTY), empty, NOW)
    assert packet["metrics"].keys() <= {"volatility_annualized", "max_drawdown"}
    assert {"beta", "alpha_annualized", "sharpe"} <= set(packet["missing"])
