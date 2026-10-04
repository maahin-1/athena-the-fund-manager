from datetime import date, datetime, timezone

import pytest

from athena.trading_calendar import IST, TradingCalendar, ist_date

UTC = timezone.utc
CAL = TradingCalendar.from_dates([date(2026, 10, 2), date(2026, 10, 20)])


def test_weekends_and_holidays_are_not_trading_days():
    assert not CAL.is_trading_day(date(2026, 10, 3))  # Saturday
    assert not CAL.is_trading_day(date(2026, 10, 2))  # Friday holiday
    assert CAL.is_trading_day(date(2026, 10, 1))
    assert CAL.is_trading_day(date(2026, 10, 5))


def test_trading_days_between_skips_holiday_and_weekend():
    # Thu 1 Oct -> Mon 5 Oct: Fri is a holiday, Sat and Sun are weekend, Mon counts.
    assert CAL.trading_days_between(date(2026, 10, 1), date(2026, 10, 5)) == 1
    assert CAL.trading_days_between(date(2026, 10, 1), date(2026, 10, 6)) == 2


def test_same_day_is_zero():
    assert CAL.trading_days_between(date(2026, 10, 5), date(2026, 10, 5)) == 0


def test_ist_date_uses_india_time():
    assert ist_date(datetime(2026, 9, 30, 18, 30, tzinfo=UTC)) == date(2026, 10, 1)
    assert ist_date(datetime(2026, 10, 1, 12, 0, tzinfo=UTC)) == date(2026, 10, 1)
    assert ist_date(datetime(2026, 10, 1, 0, 0, tzinfo=IST)) == date(2026, 10, 1)


def test_ist_date_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware"):
        ist_date(datetime(2026, 10, 1))
