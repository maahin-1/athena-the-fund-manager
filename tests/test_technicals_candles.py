from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import Bar
from athena.technicals.candles import MONTH, WEEK, Candle, candles_from_bars, resample

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def bar(day: date, o, h, l, c, v=100.0):
    # jugaad-style: IST midnight stored as 18:30 UTC of the previous calendar day
    stamp = datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1)
    return Bar("X", stamp, o, h, l, c, v, NOW, "t")


def test_candles_are_sorted_and_deduplicated_by_ist_date():
    bars = [bar(date(2026, 9, 2), 1, 2, 0.5, 1.5), bar(date(2026, 9, 1), 1, 1, 1, 1), bar(date(2026, 9, 2), 1, 3, 0.4, 2.5)]
    candles = candles_from_bars(bars)
    assert [c.day for c in candles] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert candles[1].close == 2.5  # the later duplicate wins


def test_weekly_resample_merges_ohlcv():
    days = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 14)]  # Mon,Tue,Wed | next Mon
    candles = candles_from_bars(
        [bar(days[0], 10, 12, 9, 11, 100), bar(days[1], 11, 15, 10, 14, 200), bar(days[2], 14, 14, 8, 9, 300), bar(days[3], 9, 10, 9, 10, 50)]
    )
    weekly = resample(candles, WEEK)
    assert weekly == [Candle(days[2], 10, 15, 8, 9, 600), Candle(days[3], 9, 10, 9, 10, 50)]


def test_monthly_resample_and_unknown_period():
    candles = candles_from_bars([bar(date(2026, 8, 31), 1, 2, 1, 2), bar(date(2026, 9, 1), 2, 3, 2, 3)])
    assert [c.day for c in resample(candles, MONTH)] == [date(2026, 8, 31), date(2026, 9, 1)]
    with pytest.raises(ValueError):
        resample(candles, "year")
