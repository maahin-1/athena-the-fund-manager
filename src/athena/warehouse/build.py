"""Bring the market database up to date. Run it each evening after the close:

    python -m athena.warehouse.build              # prices since two years back (only what is missing) and the universe
    python -m athena.warehouse.build --status     # what the database holds, without fetching anything
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from athena.clock import utc_now
from athena.warehouse.actions import ingest_actions
from athena.warehouse.ingest import ingest_prices, ingest_universe, ready_through
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

BACKFILL_DAYS = 730


def masters() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """The exchange's current equity and ETF lists, by symbol."""
    from athena.loaders.nse_masters import ETF_DATASET, EQUITY_DATASET, NseMasterLoader
    from athena.store import DataStore

    scratch = DataStore()
    loader = NseMasterLoader(scratch)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    return (
        {r.key: r.payload for r in scratch.latest_records(EQUITY_DATASET)},
        {r.key: r.payload for r in scratch.latest_records(ETF_DATASET)},
    )


def format_status(status: dict, first: date) -> str:
    days = status["days"]
    gaps = status.get("gaps", [])
    lines = [
        f"prices: {status['bar_rows']:,} rows, {status['bars_from']} to {status['bars_to']}; last loaded day {status['last_loaded_day']} "
        f"({status['symbols_on_last_day']:,} symbols that day)",
        f"days: {days.get('loaded', 0)} loaded, {days.get('no_session', 0)} with no session, {days.get('pending', 0)} pending",
        f"universe: " + (", ".join(f"{count:,} {kind}" for kind, count in sorted(status["universe"].items())) or "empty"),
        f"actions: {status['actions']['rows']:,} corporate actions for {status['actions']['symbols']:,} symbols, ex-dates {status['actions']['first_ex']} to {status['actions']['last_ex']}",
        f"gaps since {first}: " + (f"{len(gaps)} weekdays not settled, e.g. {', '.join(d.isoformat() for d in gaps[:5])}" if gaps else "none"),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.build", description=__doc__.split("\n")[0])
    parser.add_argument("--since", help="first day to load, YYYY-MM-DD (default: two years back)")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--status", action="store_true", help="only report what the database holds")
    args = parser.parse_args(argv)
    now = utc_now()
    first = date.fromisoformat(args.since) if args.since else ready_through(now) - timedelta(days=BACKFILL_DAYS)
    warehouse = Warehouse(Path(args.db))
    try:
        if not args.status:
            from athena.pit.bhavcopy import ArchiveClient, BhavcopyStore

            client = ArchiveClient()
            summary = ingest_prices(warehouse, BhavcopyStore(client), first, now, on_progress=print)
            print({k: v for k, v in summary.items() if v})
            try:
                print("corporate actions:", ingest_actions(warehouse, client.get, ready_through(now), now, since=first))
            except Exception as exc:  # the price days already loaded stay good; the next run asks again
                print(f"corporate actions not refreshed: {type(exc).__name__}: {exc}")
            try:
                equity, etf = masters()
                print(f"universe: {ingest_universe(warehouse, equity, etf, now)} symbols")
            except Exception as exc:  # the lists are on the exchange's archive host; the prices already loaded are still good
                print(f"universe not refreshed: {type(exc).__name__}: {exc}")
        print(format_status(warehouse.status(first, ready_through(now)), first))
    finally:
        warehouse.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
