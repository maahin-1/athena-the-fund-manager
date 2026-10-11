"""Corporate actions for the whole market in one request: the exchange's own list (splits, bonuses, demergers, rights, buybacks,
dividends) with the ex-date and wording of each, kept in the warehouse so a price series can be adjusted without a request per stock."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from athena.contracts import AthenaError
from athena.warehouse.warehouse import Warehouse

API = "https://www.nseindia.com/api/corporates-corporateActions"
BACKFILL_DAYS = 730
RECHECK_DAYS = 45  # each run re-reads this far back, so a late amendment lands
LOOKAHEAD_DAYS = 365  # announced actions whose ex-date has not come yet


def _stamp(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def fetch_actions(get: Callable[[str], bytes | None], first: date, last: date) -> list[dict[str, Any]]:
    """Every equity corporate action with an ex-date from `first` to `last`, as the exchange lists them. One request, no symbol
    and no cookies (checked 2026-10-11: two years came back as the same 4,049 rows that five half-year requests did)."""
    url = f"{API}?{urlencode({'index': 'equities', 'from_date': _stamp(first), 'to_date': _stamp(last)})}"
    content = get(url)
    if content is None:
        raise AthenaError("NSE corporate actions: no reply (HTTP 404)")
    try:
        rows = json.loads(content)
    except ValueError as exc:
        raise AthenaError(f"NSE corporate actions: reply is not JSON ({exc})") from exc
    if not isinstance(rows, list):
        raise AthenaError("NSE corporate actions: unexpected reply shape")
    return rows


def _face_value(text: Any) -> float | None:
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def rows_to_actions(rows: list[Mapping[str, Any]]) -> tuple[list[tuple[str, date, str, float | None, str, str]], int]:
    """(symbol, ex-date, wording, face value, series, ISIN) for each usable row, and how many rows had no usable ex-date or text."""
    out, skipped = [], 0
    for row in rows:
        try:
            day = datetime.strptime(str(row["exDate"]).strip(), "%d-%b-%Y").date()
            symbol, subject = str(row["symbol"]).strip(), str(row["subject"]).strip()
        except (KeyError, ValueError):
            skipped += 1
            continue
        if not symbol or not subject:
            skipped += 1
            continue
        out.append((symbol, day, subject, _face_value(row.get("faceVal")), str(row.get("series") or ""), str(row.get("isin") or "")))
    return out, skipped


def ingest_actions(
    warehouse: Warehouse, get: Callable[[str], bytes | None], today: date, now: datetime, since: date | None = None
) -> dict[str, int]:
    """The first run reads from `since` (default two years back); later runs re-read the last 45 days and everything announced
    ahead. Actions in the window are replaced, so one the exchange withdrew or reworded is not kept."""
    have = warehouse.actions_status()["rows"]
    first = (since or today - timedelta(days=BACKFILL_DAYS)) if not have else today - timedelta(days=RECHECK_DAYS)
    last = today + timedelta(days=LOOKAHEAD_DAYS)
    rows, skipped = rows_to_actions(fetch_actions(get, first, last))
    return {"rows": warehouse.replace_actions(rows, first, last, now), "skipped": skipped}
