"""Filling the warehouse: the exchange's daily price files into `bars`, and the master lists into `universe`."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from athena.contracts import AthenaError
from athena.pit.bhavcopy import Row
from athena.warehouse.warehouse import ETF, LOADED, NO_SESSION, OTHER, PENDING, SME, STOCK, Warehouse, weekdays

IST = timezone(timedelta(hours=5, minutes=30))
READY_HOUR = 19  # the exchange publishes the day's file in the evening; the current day is tried only after this hour (IST)
RECENT_DAYS = 3  # a missing file this recent may only be late, so it is retried; an older one is a holiday
SME_SERIES = frozenset({"SM", "ST"})  # the exchange's small-company platform: not in the main equity list, thinly traded, thinly disclosed
MAX_CONSECUTIVE_FAILURES = 3  # the exchange refusing three days in a row: stop and let the next run carry on


class DayStore(Protocol):
    def day(self, day: date, remember_missing: bool = True) -> Mapping[str, Row] | None: ...


def ready_through(now: datetime) -> date:
    """The latest day whose price file should be out by `now`."""
    local = now.astimezone(IST)
    return local.date() if local.hour >= READY_HOUR else local.date() - timedelta(days=1)


def ingest_prices(
    warehouse: Warehouse,
    store: DayStore,
    since: date,
    now: datetime,
    until: date | None = None,
    on_progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    """Load every weekday from `since` to `until` (default: the latest ready day) that is not already settled. A day already
    loaded or known to have no session is skipped; a pending day is asked for again. Returns what happened."""
    ready = ready_through(now)
    last = min(until, ready) if until else ready
    known = warehouse.day_status(since, last)
    summary: dict[str, Any] = {"loaded": 0, "no_session": 0, "pending": 0, "failed": [], "skipped": 0, "stopped": False}
    streak = 0
    for day in weekdays(since, last):
        if known.get(day) in (LOADED, NO_SESSION):
            summary["skipped"] += 1
            continue
        settled = (ready - day).days > RECENT_DAYS
        try:
            rows = store.day(day, remember_missing=settled)
        except AthenaError as exc:
            streak += 1
            summary["failed"].append(day.isoformat())
            on_progress(f"{day}: failed ({exc})")
            if streak >= MAX_CONSECUTIVE_FAILURES:
                summary["stopped"] = True
                on_progress(f"stopping after {streak} failures in a row; the next run resumes here")
                break
            continue
        streak = 0
        if rows:
            warehouse.upsert_day(day, rows, now)
            summary["loaded"] += 1
            on_progress(f"{day}: {len(rows)} symbols")
        else:
            status = NO_SESSION if settled else PENDING
            warehouse.mark_day(day, status, now)
            summary[status] += 1
            on_progress(f"{day}: no file ({status})")
    return summary


def universe_rows(
    equity: Mapping[str, Mapping[str, str]], etf: Mapping[str, Mapping[str, str]], traded: Mapping[str, str]
) -> list[tuple[str, str, str, str]]:
    """(symbol, kind, name, isin) for every symbol that is listed or traded (`traded` maps symbol to the series it traded in).
    In the ETF list it is an ETF; in the equity list a stock; traded in an SME series, `sme`; anything else `other`: shown as
    such, never guessed."""
    rows = []
    for symbol in sorted(set(equity) | set(etf) | set(traded)):
        if symbol in etf:
            kind, info = ETF, etf[symbol]
        elif symbol in equity:
            kind, info = STOCK, equity[symbol]
        elif traded.get(symbol) in SME_SERIES:
            kind, info = SME, {}
        else:
            kind, info = OTHER, {}
        rows.append((symbol, kind, info.get("name", ""), info.get("isin", "")))
    return rows


def ingest_universe(
    warehouse: Warehouse, equity: Mapping[str, Mapping[str, str]], etf: Mapping[str, Mapping[str, str]], now: datetime
) -> int:
    latest = warehouse.last_loaded_day()
    traded = warehouse.symbols_on(latest) if latest else {}
    return warehouse.set_universe(universe_rows(equity, etf, traded), now)
