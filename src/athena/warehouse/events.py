"""Upcoming board meetings (results, dividends, fund raising) from the exchange's event calendar.

    python -m athena.warehouse.events            # refresh `events`
    python -m athena.warehouse.events --status

The exchange lists the meetings of the next few weeks for every company (437 on 2026-10-18, 19 Oct to 13 Nov; it needs no cookies). Each refresh replaces what is
held for the dates the listing covers and leaves earlier dates alone, so the table keeps the past it has seen and a meeting the exchange withdrew or moved
does not linger. A refresh that gets nothing keeps the table. Nothing here names a company."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from athena.contracts import AthenaError
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

API = "https://www.nseindia.com/api/event-calendar?index=equities"


def parse_events(content: bytes) -> list[dict[str, Any]]:
    """The listing's rows as (symbol, day, purpose, description, company). A row with no symbol or no readable date is skipped."""
    try:
        rows = json.loads(content)
    except ValueError as exc:
        raise AthenaError(f"NSE event calendar: the reply is not JSON ({exc})") from exc
    if not isinstance(rows, list):
        raise AthenaError("NSE event calendar: unexpected reply shape")
    out = []
    for r in rows:
        try:
            day = datetime.strptime(str(r["date"]).strip(), "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        symbol = str(r.get("symbol") or "").strip().upper()
        if symbol:
            out.append({"symbol": symbol, "day": day, "purpose": str(r.get("purpose") or "").strip(), "description": str(r.get("bm_desc") or "").strip(), "company": str(r.get("company") or "").strip()})
    return out


def refresh_events(warehouse: Warehouse, get: Callable[[str], bytes | None], now: datetime) -> dict[str, Any]:
    """Fetch the listing and store it. Raises when nothing comes back, leaving the table as it was."""
    content = get(API)
    if content is None:
        raise AthenaError("NSE event calendar: no reply (HTTP 404)")
    events = parse_events(content)
    if not events:
        raise AthenaError("NSE event calendar: no events in the reply; the table is as it was")
    first = min(e["day"] for e in events)
    warehouse.replace_events(events, first, now)
    return {"events": len(events), "first": first.isoformat(), "last": max(e["day"] for e in events).isoformat()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.events", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--status", action="store_true", help="only report what the table holds")
    args = parser.parse_args(argv)
    from athena.clock import utc_now
    from athena.pit.bhavcopy import ArchiveClient

    with Warehouse(args.db, wait_seconds=300) as warehouse:
        if not args.status:
            try:
                print(refresh_events(warehouse, ArchiveClient().get, utc_now()))
            except AthenaError as exc:
                print(f"not refreshed: {exc}")
                return 1
        print(warehouse.events_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
