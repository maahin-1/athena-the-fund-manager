"""Daily index levels and valuation (close, P/E, P/B, dividend yield for about 140 indices) from the exchange's archive file, one
request per day. This is the benchmark the risk and outcome figures use and the index P/E the stock P/E is compared with."""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from athena.contracts import AthenaError, SchemaChangedError
from athena.warehouse.warehouse import LOADED, Warehouse

URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{day:%d%m%Y}.csv"
COLUMNS = {
    "name": "Index Name", "open": "Open Index Value", "high": "High Index Value", "low": "Low Index Value", "close": "Closing Index Value",
    "pe": "P/E", "pb": "P/B", "div_yield": "Div Yield",
}
MAX_CONSECUTIVE_FAILURES = 3


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # the exchange writes "-" where an index has no figure


def parse_index_file(text: str) -> list[tuple[str, float | None, float | None, float | None, float | None, float | None, float | None, float | None]]:
    """(name, open, high, low, close, P/E, P/B, dividend yield) for each index in a day's file. An unknown header is a schema change."""
    reader = csv.DictReader(io.StringIO(text))
    header = {name.strip() for name in reader.fieldnames or ()}
    missing = [column for column in COLUMNS.values() if column not in header]
    if missing:
        raise SchemaChangedError(f"index file is missing columns {missing}")
    out: dict[str, tuple] = {}
    for raw in reader:
        row = {key.strip(): (value or "").strip() for key, value in raw.items() if key}
        if not row.get(COLUMNS["name"]):
            continue
        entry = (row[COLUMNS["name"]], *(_number(row[COLUMNS[key]]) for key in ("open", "high", "low", "close", "pe", "pb", "div_yield")))
        held = out.get(entry[0])
        if held is None or entry[4] is not None or held[4] is None:  # an index listed twice keeps the row with a close, the later one on a tie
            out[entry[0]] = entry
    return list(out.values())


def ingest_indices(
    warehouse: Warehouse,
    get: Callable[[str], bytes | None],
    since: date,
    now: datetime,
    on_progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    """Load the index file for every trading day the price ledger has loaded and the index table has not. A day the exchange has
    no index file for is left to the next run."""
    last = warehouse.last_loaded_day()
    summary: dict[str, Any] = {"loaded": 0, "missing": [], "failed": [], "stopped": False}
    if last is None:
        return summary
    have = warehouse.index_days_loaded(since, last)
    days = [d for d, status in sorted(warehouse.day_status(since, last).items()) if status == LOADED and d not in have]
    streak = 0
    for day in days:
        try:
            content = get(URL.format(day=day))
        except AthenaError as exc:
            streak += 1
            summary["failed"].append(day.isoformat())
            on_progress(f"{day}: failed ({exc})")
            if streak >= MAX_CONSECUTIVE_FAILURES:
                summary["stopped"] = True
                break
            continue
        streak = 0
        if content is None:
            summary["missing"].append(day.isoformat())
            continue
        warehouse.put_index_day(day, parse_index_file(content.decode("utf-8", errors="replace")), now)
        summary["loaded"] += 1
        on_progress(f"{day}: index file")
    return summary
