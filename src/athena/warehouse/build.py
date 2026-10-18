"""Bring the market database up to date. Run it each evening after the close:

    python -m athena.warehouse.build              # prices since two years back (only what is missing), actions, indices, universe, packets
    python -m athena.warehouse.build --status     # what the database holds, without fetching anything

Each run writes `.athena/build_status.json` and a report under `.athena/reports/`, which `python -m athena.warehouse.monitor` reads.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from athena.clock import utc_now
from athena.warehouse.actions import ingest_actions
from athena.warehouse.renames import refresh_renames
from athena.warehouse.indices import ingest_indices
from athena.warehouse.ingest import ingest_prices, ingest_universe, ready_through
from athena.warehouse.packets import build_packets
from athena.warehouse.statements import refresh_statements
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

BACKFILL_DAYS = 730
BUILD_STATUS_PATH = Path(".athena") / "build_status.json"
REPORT_DIR = Path(".athena") / "reports"


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
        "universe: " + (", ".join(f"{count:,} {kind}" for kind, count in sorted(status["universe"].items())) or "empty"),
        "packets: " + (", ".join(f"{count:,} {kind}" for kind, count in sorted(status["packets"].items())) or "none built yet"),
        f"index files: {status['index_days']} days (benchmark and index P/E)",
        "statements: " + (", ".join(f"{count:,} {name}" for name, count in sorted(status["statements"].items())) or "none fetched yet"),
        f"actions: {status['actions']['rows']:,} corporate actions for {status['actions']['symbols']:,} symbols, "
        f"ex-dates {status['actions']['first_ex']} to {status['actions']['last_ex']}",
        f"gaps since {first}: " + (f"{len(gaps)} weekdays not settled, e.g. {', '.join(d.isoformat() for d in gaps[:5])}" if gaps else "none"),
    ]
    return "\n".join(lines)


def run_build(
    warehouse: Warehouse,
    now: datetime,
    first: date,
    get: Callable[[str], bytes | None],
    price_store: Any,
    lists: Callable[[], tuple[dict, dict]],
    statements_fetch: Callable[[str], dict[str, Any]] | None,
    statements_budget: int,
    on_progress: Callable[[str], None] = lambda text: None,
    full_actions: bool = False,
    renames_fetch: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run every step of the nightly build, one failing never stopping the next, and say what each did. The price step is the one the
    others lean on: if it stopped early the build is not ok even when the later steps ran."""
    steps: list[dict[str, Any]] = []

    def step(name: str, work: Callable[[], Any], is_ok: Callable[[Any], bool] = lambda result: True) -> Any:
        try:
            result = work()
        except Exception as exc:  # a step's failure is recorded; the data it did not refresh keeps its last good value
            steps.append({"name": name, "ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"})
            on_progress(f"{name}: FAILED ({type(exc).__name__}: {str(exc)[:100]})")
            return None
        steps.append({"name": name, "ok": bool(is_ok(result)), "detail": result})
        on_progress(f"{name}: {result}")
        return result

    step("prices", lambda: ingest_prices(warehouse, price_store, first, now), lambda r: not r["stopped"] and not r["failed"])
    step("actions", lambda: ingest_actions(warehouse, get, ready_through(now), now, since=first, full=full_actions))
    if renames_fetch is not None:  # after prices and actions: the checks read both
        step("renames", lambda: refresh_renames(warehouse, renames_fetch, now))
    step("indices", lambda: ingest_indices(warehouse, get, first, now), lambda r: not r["stopped"] and not r["failed"])
    step("universe", lambda: ingest_universe(warehouse, *lists(), now))
    if statements_budget and statements_fetch is not None:
        step("statements", lambda: refresh_statements(warehouse, statements_fetch, now, statements_budget), lambda r: not r["stopped"])
    step("packets", lambda: build_packets(warehouse, now), lambda r: not r["failed"])
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "last_loaded_day": str(warehouse.last_loaded_day())}


def write_build_status(status: dict[str, Any], started: datetime, finished: datetime, path: Path = BUILD_STATUS_PATH, report_dir: Path = REPORT_DIR) -> Path:
    """Save the run as JSON for the monitor and as a short report for people."""
    record = {**status, "started_at": started.isoformat(), "finished_at": finished.isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    lines = [
        f"# Nightly build, {started:%Y-%m-%d %H:%M} UTC",
        "",
        f"- Result: **{'ok' if status['ok'] else 'problems'}**; took {(finished - started).total_seconds() / 60:.1f} minutes; prices to {status['last_loaded_day']}.",
        "",
    ] + [f"- {'ok' if s['ok'] else 'FAILED'} {s['name']}: {s['detail']}" for s in status["steps"]]
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"build_{started:%Y%m%d_%H%M}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_dir / "build_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.build", description=__doc__.split("\n")[0])
    parser.add_argument("--since", help="first day to load, YYYY-MM-DD (default: two years back); an earlier day than the history held extends it backwards, actions included")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--status", action="store_true", help="only report what the database holds")
    parser.add_argument("--statements", type=int, default=300, help="how many stocks' statements to fetch this run (0: none)")
    args = parser.parse_args(argv)
    started = utc_now()
    first = date.fromisoformat(args.since) if args.since else ready_through(started) - timedelta(days=BACKFILL_DAYS)
    warehouse = Warehouse(Path(args.db), wait_seconds=300)  # a long fill may hold the file for a moment
    ok = True
    try:
        if not args.status:
            from athena.loaders.fundamentals import default_fetch
            from athena.symbol_changes import fetch_symbol_changes
            from athena.pit.bhavcopy import ArchiveClient, BhavcopyStore

            client = ArchiveClient()
            outcome = run_build(warehouse, started, first, client.get, BhavcopyStore(client), masters, default_fetch, args.statements, on_progress=print, full_actions=bool(args.since), renames_fetch=fetch_symbol_changes)
            ok = outcome["ok"]
            print("report:", write_build_status(outcome, started, utc_now()))
        print(format_status(warehouse.status(first, ready_through(started)), first))
    finally:
        warehouse.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
