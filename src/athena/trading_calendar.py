from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def ist_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    return moment.astimezone(IST).date()


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset[date]

    @classmethod
    def from_dates(cls, dates: Iterable[date]) -> TradingCalendar:
        return cls(frozenset(dates))

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def trading_days_between(self, start: date, end: date) -> int:
        count = 0
        day = start
        while day < end:
            day += timedelta(days=1)
            if self.is_trading_day(day):
                count += 1
        return count
