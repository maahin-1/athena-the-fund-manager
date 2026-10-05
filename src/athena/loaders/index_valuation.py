from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore
from athena.trading_calendar import IST, ist_date

DATASET = "index.valuation"
SOURCE = "jugaad.index_pe_raw"
DEFAULT_INDEX = "NIFTY 50"
_DEFAULT_WINDOW_DAYS = 30


def default_fetch(index_name: str, start: date, end: date) -> list[dict]:
    from jugaad_data.nse import NSEIndexHistory

    return NSEIndexHistory().index_pe_raw(index_name, start, end)


def parse_valuation_rows(rows: list[dict]) -> list[tuple[date, float, float, float]]:
    parsed = []
    for row in rows:
        try:
            day = datetime.strptime(row["DATE"], "%d %b %Y").date()
            parsed.append((day, float(row["pe"]), float(row["pb"]), float(row["divYield"])))
        except (KeyError, ValueError, TypeError) as exc:
            raise SchemaChangedError(f"unexpected index valuation row shape: {exc!r}") from exc
    return sorted(parsed)


def _ist_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)


class IndexValuationLoader:
    """Daily price-to-earnings, price-to-book and dividend yield of an NSE index (NSE publishes these back to 2015)."""

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
        return {DATASET: {"cadence": "daily", "source": SOURCE, "series": ["pe", "pb", "div_yield"]}}

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
            raise EmptyRefreshError(f"no valuation rows returned for {index_name!r} from {start} to {today}")
        written = 0
        for day, pe, pb, div_yield in parse_valuation_rows(raw):
            if last_day is not None and day <= last_day:
                continue
            self._store.put(Record(DATASET, index_name, _ist_midnight_utc(day), SOURCE, {"pe": pe, "pb": pb, "div_yield": div_yield}))
            written += 1
        return RefreshResult(DATASET, written, now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record


def valuation_history(store: DataStore, index_name: str = DEFAULT_INDEX) -> dict[date, dict[str, float]]:
    """Stored valuation rows for an index, keyed by IST trading date."""
    return {ist_date(record.as_of): dict(record.payload) for record in store.history(DATASET, index_name)}
