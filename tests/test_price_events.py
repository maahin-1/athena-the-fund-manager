from datetime import date

import pandas as pd
import pytest

from athena.contracts import AthenaError
from athena.price_integrity.events import PriceEvent, yahoo_split_events


class FakeTicker:
    def __init__(self, splits=None, error=None):
        self._splits, self._error = splits, error

    @property
    def splits(self):
        if self._error:
            raise self._error
        return self._splits


def series(pairs):
    return pd.Series([v for _, v in pairs], index=pd.DatetimeIndex([d for d, _ in pairs], tz="Asia/Kolkata"), dtype=float)


def test_a_five_for_one_split_is_a_fifth_of_the_price():
    event = PriceEvent(date(2026, 1, 14), 5.0)
    assert event.factor == pytest.approx(0.2) and event.source == "yahoo"


def test_yahoo_splits_become_events_dated_in_ist():
    got = yahoo_split_events("KOTAKBANK", FakeTicker(series([("2026-01-14", 5.0), ("2024-10-28", 2.0)])))
    assert got == [PriceEvent(date(2024, 10, 28), 2.0), PriceEvent(date(2026, 1, 14), 5.0)]


def test_a_reverse_split_is_a_ratio_below_one_and_empty_or_unit_rows_are_ignored():
    got = yahoo_split_events("X", FakeTicker(series([("2025-03-03", 0.1), ("2025-04-01", 1.0), ("2025-05-01", 0.0)])))
    assert got == [PriceEvent(date(2025, 3, 3), 0.1)]


def test_a_stock_that_never_split_has_no_events_and_a_failed_lookup_says_so():
    assert yahoo_split_events("X", FakeTicker(series([]))) == []
    with pytest.raises(AthenaError, match="corporate actions unavailable for X"):
        yahoo_split_events("X", FakeTicker(error=RuntimeError("blocked")))
