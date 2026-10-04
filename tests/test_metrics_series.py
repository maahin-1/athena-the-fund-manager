from datetime import date, datetime, timezone

import pytest

from athena.contracts import Bar, InsufficientData, Record
from athena.metrics.series import (
    common_dates,
    level_asof,
    levels_on,
    returns_from_levels,
    riskfree_returns,
    series_from_bars,
    series_from_store,
)
from athena.store import DataStore

UTC = timezone.utc
D = lambda day: date(2026, 10, day)  # noqa: E731


def test_returns_from_levels():
    assert returns_from_levels([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])
    assert returns_from_levels([100.0]) == []


def test_common_dates_intersects_and_sorts():
    a = {D(1): 1.0, D(5): 2.0, D(6): 3.0}
    b = {D(6): 9.0, D(1): 8.0, D(2): 7.0}
    assert common_dates(a, b) == [D(1), D(6)]
    assert common_dates(a) == [D(1), D(5), D(6)]
    assert common_dates() == []


def test_levels_on_follows_the_given_dates():
    assert levels_on({D(1): 1.0, D(5): 5.0}, [D(5), D(1)]) == [5.0, 1.0]


def test_level_asof_uses_the_latest_earlier_level():
    series = {D(1): 10.0, D(5): 50.0}
    assert level_asof(series, D(3)) == 10.0
    assert level_asof(series, D(5)) == 50.0
    assert level_asof(series, D(9)) == 50.0
    assert level_asof(series, date(2026, 9, 30)) is None


def test_riskfree_returns_use_index_level_ratios_between_dates():
    rate_index = {D(1): 2600.0, D(2): 2601.0, D(3): 2602.0, D(5): 2605.0}  # D(4) missing (weekend)
    got = riskfree_returns(rate_index, [D(1), D(3), D(5)])
    assert got == pytest.approx([2602.0 / 2600.0 - 1.0, 2605.0 / 2602.0 - 1.0])


def test_riskfree_returns_raise_when_index_starts_after_first_day():
    with pytest.raises(InsufficientData, match="does not cover"):
        riskfree_returns({D(3): 1.0}, [D(1), D(3)])


def test_series_from_bars_keys_by_ist_trading_date():
    bar = Bar("SBIN", datetime(2026, 9, 30, 18, 30, tzinfo=UTC), 1, 1, 1, 954.1, 10, datetime(2026, 10, 5, tzinfo=UTC), "jugaad")
    assert series_from_bars([bar]) == {D(1): 954.1}


def test_series_from_store_reads_history_by_ist_date():
    store = DataStore()
    for day, value in ((29, 1.0), (30, 2.0)):  # IST midnight of 30 Sep and 1 Oct, stored as UTC
        store.put(Record("index.price", "NIFTY 50", datetime(2026, 9, day, 18, 30, tzinfo=UTC), "x", {"close": value}))
    assert series_from_store(store, "index.price", "NIFTY 50", "close") == {date(2026, 9, 30): 1.0, D(1): 2.0}
    assert series_from_store(store, "index.price", "OTHER", "close") == {}
