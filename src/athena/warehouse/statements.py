"""Keeping each stock's financial statements in the warehouse. Yahoo gives one stock per request, so the refresh is a queue worked
a few hundred at a time: stocks never fetched first, most traded first; a failed one is tried again the next day; one the
source has nothing for (renamed, delisted, no filings) is not asked again for three months."""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from athena.warehouse.warehouse import STATEMENT_EMPTY, STATEMENT_ERROR, STATEMENT_OK, Warehouse

STALE_OK_DAYS = 30  # a quarter's results land within 45 days; a month-old payload may miss the newest quarter
RETRY_EMPTY_DAYS = 90
RETRY_ERROR_DAYS = 1
GAP_SECONDS = 1.0  # between requests: Yahoo is unofficial and throttles
MAX_CONSECUTIVE_ERRORS = 5  # five failures in a row: the source is refusing us, stop and try again tomorrow
KINDS = ("stock",)  # ETFs have no statements and SME-platform names are thinly covered


def queue(warehouse: Warehouse, now: datetime, last_day, kinds: tuple[str, ...] = KINDS) -> list[str]:
    """Symbols due a fetch, most urgent first: never tried, then failed (a day later), then stale, then empty (three months later);
    within each, the most traded first."""
    state = warehouse.statement_state()
    value = warehouse.traded_value(last_day) if last_day else {}
    due: list[tuple[int, float, str]] = []
    for kind in kinds:
        for row in warehouse.universe(kind):
            symbol = row["symbol"]
            status, fetched, tried, _ = state.get(symbol, (None, None, None, 0))
            if status is None:
                rank = 0
            elif status == STATEMENT_ERROR and (now - tried).days >= RETRY_ERROR_DAYS:
                rank = 1
            elif status == STATEMENT_OK and fetched is not None and (now - fetched).days >= STALE_OK_DAYS:
                rank = 2
            elif status == STATEMENT_EMPTY and (now - tried).days >= RETRY_EMPTY_DAYS:
                rank = 3
            else:
                continue
            due.append((rank, -value.get(symbol, 0.0), symbol))
    return [symbol for _, _, symbol in sorted(due)]


def has_statements(payload: dict[str, Any]) -> bool:
    income = payload.get("annual", {}).get("income", {})
    return any(series for series in income.values())


def refresh_statements(
    warehouse: Warehouse,
    fetch: Callable[[str], dict[str, Any]],
    now: datetime,
    budget: int,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    """Fetch up to `budget` due stocks. Returns what happened. Never raises for one stock's failure."""
    last_day = warehouse.last_loaded_day()
    todo = queue(warehouse, now, last_day)
    summary: dict[str, Any] = {"due": len(todo), "ok": 0, "empty": 0, "error": 0, "stopped": False}
    streak = 0
    for symbol in todo[:budget]:
        try:
            payload = fetch(symbol)
        except Exception as exc:  # the free source can fail in many ways (network, rate limit, format)
            warehouse.put_statement(symbol, STATEMENT_ERROR, None, f"{type(exc).__name__}: {str(exc)[:120]}", now)
            summary["error"] += 1
            streak += 1
            on_progress(f"{symbol}: error ({type(exc).__name__})")
            if streak >= MAX_CONSECUTIVE_ERRORS:
                summary["stopped"] = True
                on_progress(f"stopping after {streak} failures in a row; the next run resumes here")
                break
        else:
            streak = 0
            if has_statements(payload):
                warehouse.put_statement(symbol, STATEMENT_OK, payload, "", now)
                summary["ok"] += 1
            else:
                warehouse.put_statement(symbol, STATEMENT_EMPTY, None, "no financial statements from the source", now)
                summary["empty"] += 1
        sleep(GAP_SECONDS)
    return summary
