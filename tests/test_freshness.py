from datetime import date, datetime, timezone

import pytest

from athena.contracts import StaleDataError
from athena.freshness import DEFAULT_LIMITS, Limit, age_in_business_days, check_fresh
from athena.trading_calendar import TradingCalendar

UTC = timezone.utc


def dt(day, hour=0, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_default_limits_match_the_trd_table():
    assert DEFAULT_LIMITS["quote.intraday"] == Limit(15, "minutes")
    assert DEFAULT_LIMITS["option_chain"] == Limit(15, "minutes")
    assert DEFAULT_LIMITS["price.eod"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["mf.nav"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["index.tri"] == Limit(1, "business_days")
    assert DEFAULT_LIMITS["mf.ter"] == Limit(7, "days")
    assert DEFAULT_LIMITS["mf.holdings"] == Limit(45, "days")
    assert DEFAULT_LIMITS["equity.fundamentals"] == Limit(136, "days")
    assert DEFAULT_LIMITS["bond.price"] is None


def test_business_days_skip_weekends():
    assert age_in_business_days(dt(2, 18), dt(3, 10)) == 0  # Fri -> Sat
    assert age_in_business_days(dt(2, 18), dt(5, 9)) == 1  # Fri -> Mon
    assert age_in_business_days(dt(2, 18), dt(6, 9)) == 2  # Fri -> Tue


def test_intraday_quote_fresh_at_limit_and_stale_after():
    check_fresh("quote.intraday", dt(5, 10, 0), dt(5, 10, 15))
    with pytest.raises(StaleDataError, match="quote.intraday"):
        check_fresh("quote.intraday", dt(5, 10, 0), dt(5, 10, 16))


def test_nav_published_friday_is_fresh_on_monday_stale_on_tuesday():
    check_fresh("mf.nav", dt(2, 18), dt(5, 9))
    with pytest.raises(StaleDataError, match="2 business days"):
        check_fresh("mf.nav", dt(2, 18), dt(6, 9))


def test_day_based_limit():
    check_fresh("mf.ter", dt(1), dt(8))
    with pytest.raises(StaleDataError, match="8 days"):
        check_fresh("mf.ter", dt(1), dt(9))


def test_dataset_without_limit_is_never_stale():
    check_fresh("bond.price", datetime(2020, 1, 1, tzinfo=UTC), dt(5))


def test_unknown_dataset_is_an_error():
    with pytest.raises(ValueError, match="no staleness limit declared"):
        check_fresh("made.up", dt(1), dt(2))


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        check_fresh("mf.nav", datetime(2026, 10, 2), dt(3))


CAL = TradingCalendar.from_dates([date(2026, 10, 2)])


def test_holiday_calendar_prevents_false_stale_on_monday():
    nav_as_of = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)  # Thu 18:00 IST
    now = datetime(2026, 10, 5, 3, 30, tzinfo=UTC)  # Mon 09:00 IST
    with pytest.raises(StaleDataError):
        check_fresh("mf.nav", nav_as_of, now)  # weekday-only age = 2 (counts the Fri holiday)
    check_fresh("mf.nav", nav_as_of, now, calendar=CAL)  # trading-day age = 1


def test_calendar_still_flags_genuinely_stale_data():
    with pytest.raises(StaleDataError, match="2 business days"):
        check_fresh(
            "mf.nav",
            datetime(2026, 10, 1, 12, 30, tzinfo=UTC),
            datetime(2026, 10, 6, 3, 30, tzinfo=UTC),
            calendar=CAL,
        )


def test_new_dataset_limits():
    assert DEFAULT_LIMITS["calendar.nse_holidays"] == Limit(120, "days")
    assert DEFAULT_LIMITS["master.nse_equity"] == Limit(7, "days")
    assert DEFAULT_LIMITS["master.nse_etf"] == Limit(7, "days")
