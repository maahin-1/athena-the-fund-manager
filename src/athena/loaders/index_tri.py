from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import IST, ist_date

DATASET = "index.tri"
SOURCE = "jugaad.index_tri_raw"
DEFAULT_INDEX = "NIFTY 50"
_DEFAULT_WINDOW_DAYS = 30


def default_fetch(index_name: str, start: date, end: date) -> list[dict]:
    from jugaad_data.nse import NSEIndexHistory

    return NSEIndexHistory().index_tri_raw(index_name, index_name, start, end)


def parse_tri_rows(rows: list[dict]) -> list[tuple[date, float, float]]:
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(row["Date"], "%d %b %Y").date()
            parsed.append((day, float(row["TotalReturnsIndex"]), float(row["NTR_Value"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise SchemaChangedError(f"unexpected TRI row shape: {exc!r}") from exc
    return sorted(parsed)


def _ist_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)


class IndexTriLoader:
    def __init__(
        self,
        store: DataStore,
        fetch: Callable[[str, date, date], list[dict]] = default_fetch,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._clock = clock

    def describe(self) -> dict:
        return {DATASET: {"cadence": "daily", "source": SOURCE, "series": ["tri", "ntr"]}}

    def refresh(self, dataset: str = DATASET, since: Any = None, **params: Any) -> RefreshResult:
        if dataset != DATASET:
            raise ValueError(f"this loader only provides {DATASET!r}, got {dataset!r}")
        index_name = params.get("index_name", DEFAULT_INDEX)
        now = self._clock()
        today = ist_date(now)
        last = self._store.latest(DATASET, index_name)
        last_day = ist_date(last.as_of) if last else None
        start = since or last_day or today - timedelta(days=_DEFAULT_WINDOW_DAYS)

        raw = self._fetch(index_name, start, today)
        if not raw:
            raise EmptyRefreshError(f"no TRI rows returned for {index_name!r} from {start} to {today}")
        written = 0
        for day, tri, ntr in parse_tri_rows(raw):
            if last_day is not None and day <= last_day:
                continue
            self._store.put(
                Record(DATASET, index_name, _ist_midnight_utc(day), SOURCE, {"tri": tri, "ntr": ntr})
            )
            written += 1
        return RefreshResult(DATASET, written, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
