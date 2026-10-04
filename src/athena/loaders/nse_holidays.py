from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar

DATASET = "calendar.nse_holidays"
SOURCE = "nse.holiday_list"


def default_fetch() -> dict[str, Any]:
    from jugaad_data.nse import NSELive

    return NSELive().holiday_list()


def parse_holidays(payload: dict[str, Any], segment: str = "CM") -> list[date]:
    if segment not in payload:
        raise SchemaChangedError(f"holiday payload has no segment {segment!r}")
    rows = payload[segment]
    if not rows:
        raise EmptyRefreshError(f"holiday payload segment {segment!r} is empty")
    try:
        return sorted({datetime.strptime(row["tradingDate"], "%d-%b-%Y").date() for row in rows})
    except (KeyError, ValueError, TypeError) as exc:
        raise SchemaChangedError(f"unexpected holiday row shape: {exc!r}") from exc


class NseHolidayLoader:
    def __init__(
        self,
        store: DataStore,
        fetch: Callable[[], dict[str, Any]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {DATASET: {"cadence": "yearly", "source": SOURCE}}

    def refresh(self, dataset: str = DATASET, since: Any = None) -> RefreshResult:
        if dataset != DATASET:
            raise ValueError(f"this loader only provides {DATASET!r}, got {dataset!r}")
        days = parse_holidays(self._fetch())
        now = self._clock()
        by_year: dict[int, list[date]] = {}
        for day in days:
            by_year.setdefault(day.year, []).append(day)
        for year, dates in by_year.items():
            self._store.put(
                Record(DATASET, str(year), now, SOURCE, {"dates": [d.isoformat() for d in dates]})
            )
        return RefreshResult(DATASET, len(by_year), now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record


def load_calendar(store: DataStore, years: Iterable[int]) -> TradingCalendar:
    dates: set[date] = set()
    for year in years:
        record = store.latest(DATASET, str(year))
        if record is None:
            raise EmptyRefreshError(f"no {DATASET} data for {year}; run the holiday loader first")
        dates.update(date.fromisoformat(text) for text in record.payload["dates"])
    return TradingCalendar(frozenset(dates))
