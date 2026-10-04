from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import IST, ist_date

PRICE_DATASET = "index.price"
RATE_DATASET = "rate.overnight"
PRICE_INDEX = "NIFTY 50"
RATE_INDEX = "NIFTY 1D RATE INDEX"
SOURCE = "jugaad.index_raw"
_DEFAULT_WINDOW_DAYS = 30


def default_fetch(index_name: str, start: date, end: date) -> list[dict]:
    from jugaad_data.nse import NSEIndexHistory

    return NSEIndexHistory().index_raw(index_name, start, end)


def parse_close_rows(rows: list[dict]) -> list[tuple[date, float]]:
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(row["HistoricalDate"], "%d %b %Y").date()
            parsed.append((day, float(row["CLOSE"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise SchemaChangedError(f"unexpected index row shape: {exc!r}") from exc
    return sorted(parsed)


def _ist_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)


class IndexCloseLoader:
    """Daily closing levels of an NSE index. Two instances are used: the Nifty 50 price index
    (`index.price`) and the Nifty 1D Rate Index (`rate.overnight`), whose level ratio between two
    dates is the overnight risk-free return over that period."""

    def __init__(
        self,
        store: DataStore,
        dataset: str = PRICE_DATASET,
        default_index: str = PRICE_INDEX,
        fetch: Callable[[str, date, date], list[dict]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._dataset = dataset
        self._default_index = default_index
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {self._dataset: {"cadence": "daily", "source": SOURCE, "series": ["close"]}}

    def refresh(self, dataset: str | None = None, since: Any = None, **params: Any) -> RefreshResult:
        dataset = dataset or self._dataset
        if dataset != self._dataset:
            raise ValueError(f"this loader only provides {self._dataset!r}, got {dataset!r}")
        index_name = params.get("index_name", self._default_index)
        now = self._clock()
        today = ist_date(now)
        last = self._store.latest(self._dataset, index_name)
        last_day = ist_date(last.as_of) if last else None
        start = since or last_day or today - timedelta(days=_DEFAULT_WINDOW_DAYS)

        raw = self._fetch(index_name, start, today)
        if not raw:
            raise EmptyRefreshError(f"no rows returned for {index_name!r} from {start} to {today}")
        written = 0
        for day, close in parse_close_rows(raw):
            if last_day is not None and day <= last_day:
                continue
            self._store.put(
                Record(self._dataset, index_name, _ist_midnight_utc(day), SOURCE, {"close": close})
            )
            written += 1
        return RefreshResult(self._dataset, written, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
