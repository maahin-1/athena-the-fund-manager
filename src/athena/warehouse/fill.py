"""The long statements fill: every stock's statements, fetched one at a time over a couple of hours, without holding the database.

    python -m athena.warehouse.fill            # everything due; safe to stop and start again
    python -m athena.warehouse.fill --limit 50

The job opens the database only for each short write, so charts, reports and other jobs can use it meanwhile. It writes a small
status file (`.athena/fill_status.json`) as it goes, which `python -m athena.warehouse.monitor` reads, and a report when it ends.
When Yahoo refuses five fetches in a row the job pauses ten minutes and retries those stocks; after three pauses with no success in
between it stops and says why."""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from athena.clock import utc_now
from athena.fundamentals.packet_source import packet_filings
from athena.valuation.industry import packet_lookup
from athena.warehouse.packets import build_packets
from athena.warehouse.statements import GAP_SECONDS, has_statements, queue
from athena.warehouse.warehouse import DEFAULT_PATH, STATEMENT_EMPTY, STATEMENT_ERROR, STATEMENT_OK, Warehouse

STATUS_PATH = Path(".athena") / "fill_status.json"
REPORT_DIR = Path(".athena") / "reports"
STREAK_TO_PAUSE = 5
PAUSE_SECONDS = 600.0
MAX_PAUSES = 3  # pauses in a row with no success between them, before the job gives up
DB_WAIT_SECONDS = 120  # how long a write waits for another process to let go of the file


@dataclass
class FillStatus:
    state: str = "starting"  # starting | running | paused | finished | stopped
    pid: int = 0
    started_at: str = ""
    updated_at: str = ""
    total: int = 0
    done: int = 0
    ok: int = 0
    empty: int = 0
    error: int = 0
    current: str = ""
    last_success_at: str = ""
    pauses: int = 0
    pause_seconds: float = PAUSE_SECONDS
    reason: str = ""
    errors: dict[str, int] = field(default_factory=dict)  # error text -> how many stocks ended on it
    report: str = ""


def _write(path: Path, status: FillStatus, now: datetime) -> None:
    status.updated_at = now.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(asdict(status), indent=1), encoding="utf-8")
    try:
        temporary.replace(path)
    except OSError:  # a reader has the file open for a moment; the next update lands
        pass


def read_status(path: Path = STATUS_PATH) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _message(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:100] if str(exc) else ''}".strip()


def run_fill(
    db_path: Path,
    fetch: Callable[[str], dict[str, Any]],
    status_path: Path = STATUS_PATH,
    report_dir: Path = REPORT_DIR,
    clock: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    limit: int | None = None,
    gap: float = GAP_SECONDS,
    pause_seconds: float = PAUSE_SECONDS,
    max_pauses: int = MAX_PAUSES,
    build_after: bool = True,
    on_progress: Callable[[str], None] = lambda text: None,
) -> FillStatus:
    started = clock()
    with Warehouse(db_path, read_only=True, wait_seconds=DB_WAIT_SECONDS, sleep=sleep) as warehouse:
        todo = queue(warehouse, started, warehouse.last_loaded_day())
    if limit:
        todo = todo[:limit]
    status = FillStatus(state="running", pid=os.getpid(), started_at=started.isoformat(), total=len(todo), pause_seconds=pause_seconds)
    outcome: dict[str, str] = {}  # symbol -> its latest result; a stock retried after a pause counts once
    errors: dict[str, str] = {}
    streak = idle_pauses = 0
    index = 0
    _write(status_path, status, started)

    def refresh_counts() -> None:
        counts = Counter(outcome.values())
        status.ok, status.empty, status.error = counts[STATEMENT_OK], counts[STATEMENT_EMPTY], counts[STATEMENT_ERROR]
        status.done = len(outcome)
        status.errors = dict(Counter(errors[s] for s, o in outcome.items() if o == STATEMENT_ERROR))

    while index < len(todo):
        symbol = todo[index]
        status.current = symbol
        now = clock()
        try:
            payload = fetch(symbol)
        except Exception as exc:  # the free source can fail in many ways (network, rate limit, format)
            result, held, message = STATEMENT_ERROR, None, _message(exc)
        else:
            held = payload if has_statements(payload) else None
            result, message = (STATEMENT_OK if held is not None else STATEMENT_EMPTY), ""
            if result == STATEMENT_EMPTY:
                message = "no financial statements from the source"
        try:
            with Warehouse(db_path, wait_seconds=DB_WAIT_SECONDS, sleep=sleep) as warehouse:
                warehouse.put_statement(symbol, result, held, message, now)
        except Exception as exc:  # the file stayed in use by another job past the wait: stop cleanly, the next run resumes
            status.state, status.reason = "stopped", f"could not write to the database: {_message(exc)}"
            on_progress(status.reason)
            break
        outcome[symbol], errors[symbol] = result, message
        index += 1
        if result == STATEMENT_ERROR:
            streak += 1
        else:
            streak = idle_pauses = 0
            status.last_success_at = now.isoformat()
        refresh_counts()
        if streak >= STREAK_TO_PAUSE:
            idle_pauses += 1
            status.pauses += 1
            if idle_pauses > max_pauses:
                status.state, status.reason = "stopped", f"{streak} failures in a row after {max_pauses} pauses; last error: {message}"
                on_progress(status.reason)
                break
            status.state = "paused"
            _write(status_path, status, clock())
            on_progress(f"{streak} failures in a row ({message}); pausing {pause_seconds:.0f} s, then retrying those stocks")
            sleep(pause_seconds)
            index -= streak  # the stocks that just failed are tried again after the pause
            streak, status.state = 0, "running"
        _write(status_path, status, clock())
        on_progress(f"{symbol}: {result} ({status.done}/{status.total})")
        sleep(gap)
    if status.state != "stopped":
        status.state = "finished"
    status.current = ""
    packets = {}
    if build_after:
        try:
            with Warehouse(db_path, wait_seconds=DB_WAIT_SECONDS, sleep=sleep) as warehouse:
                packets = build_packets(warehouse, clock(), industry=packet_lookup(), filings=packet_filings())
        except Exception as exc:  # the statements are saved; the nightly build makes the packets
            on_progress(f"packets not rebuilt: {_message(exc)}")
    ended = clock()
    status.report = str(write_report(report_dir, status, packets, db_path, started, ended, errors, outcome))
    _write(status_path, status, ended)
    return status


def write_report(
    report_dir: Path, status: FillStatus, packets: dict[str, Any], db_path: Path, started: datetime, ended: datetime,
    errors: dict[str, str], outcome: dict[str, str],
) -> Path:
    held, stocks, unread = {}, 0, ""
    try:
        with Warehouse(db_path, read_only=True, wait_seconds=DB_WAIT_SECONDS) as warehouse:
            held = warehouse.status()["statements"]
            stocks = len(warehouse.universe("stock"))
    except Exception as exc:  # the report is still worth writing without the totals
        unread = _message(exc)
    minutes = (ended - started).total_seconds() / 60
    failed = sorted(s for s, o in outcome.items() if o == STATEMENT_ERROR)
    lines = [
        f"# Statements fill, {started:%Y-%m-%d %H:%M} UTC",
        "",
        f"- Result: **{status.state}**" + (f" ({status.reason})" if status.reason else ""),
        f"- Took {minutes:.0f} minutes; {status.done} of {status.total} due stocks tried; {status.pauses} pause(s) for refusals.",
        f"- This run: {status.ok} with statements, {status.empty} with none at the source (new listings, renamed or delisted), {status.error} failed.",
        (f"- Now held: {held.get('ok', 0):,} of {stocks:,} stocks have statements; {held.get('empty', 0):,} empty, {held.get('error', 0):,} failed."
         if not unread else f"- Totals in the database could not be read ({unread}); run `python -m athena.warehouse.build --status`."),
    ]
    if packets:
        lines.append(f"- Packets rebuilt: {packets.get('technical', 0):,} technical, {packets.get('fundamentals', 0):,} fundamentals; "
                     f"{packets.get('no_statements', 0):,} stocks still without statements; {len(packets.get('failed', []))} packet failures.")
    if status.errors:
        lines += ["", "## Failures by cause", ""] + [f"- {count} x {text}" for text, count in sorted(status.errors.items(), key=lambda kv: -kv[1])]
        lines += ["", "Failed stocks (tried again tomorrow): " + ", ".join(failed[:60]) + (f", and {len(failed) - 60} more" if len(failed) > 60 else "")]
    if packets.get("failed"):
        lines += ["", "## Packet failures", ""] + [f"- {text}" for text in packets["failed"][:30]]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"statements_fill_{started:%Y%m%d_%H%M}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_dir / "statements_fill_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.fill", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--limit", type=int, help="fetch at most this many stocks")
    args = parser.parse_args(argv)
    from athena.loaders.fundamentals import default_fetch

    status = run_fill(Path(args.db), default_fetch, limit=args.limit, on_progress=print)
    print(f"{status.state}: {status.ok} ok, {status.empty} empty, {status.error} failed of {status.total}; report {status.report}")
    return 0 if status.state == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
