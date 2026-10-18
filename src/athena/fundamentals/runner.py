"""The loop every manual fetch command shares: go through the companies one at a time, skip those finished recently when asked, stop cleanly when
the exchange refuses (recording when to try again), and print one line per company. What is fetched for one company is the caller's `fetch`."""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TextIO

from athena.fundamentals.ingest import BLOCK_PAUSE, Blocked, Report, Stall, stop_stalled
from athena.fundamentals.store import FundamentalsStore, now
from athena.fundamentals.universe import Member


def run_each(
    members: Sequence[Member], fetch: Callable[[Member], Report], store_path: str | Path, *, key: Callable[[Member], str], resume: bool,
    max_age_days: int, limit: int | None, refresh: bool = False, out: TextIO = sys.stdout,
) -> int:
    """`fetch` each member in turn. Returns 0 when done, 3 when the exchange blocked it (or a block is still running). The state of a member is kept
    under `key(member)`: `ok`, `partial` (some items could not be read, so `--resume` tries it again), `failed` or `blocked`."""
    with FundamentalsStore(store_path) as store:
        until = store.blocked_until()
    if until and until > now():
        print(f"blocked by the exchange until {until:%H:%M} UTC; not starting", file=out)
        return 3
    done = 0
    stall = Stall()
    for member in members:
        if limit is not None and done >= limit:
            break
        name = key(member)
        with FundamentalsStore(store_path) as store:
            state = store.run(name)
        if resume and not refresh and state and state["status"] == "ok" and state["finished"] > now() - timedelta(days=max_age_days):
            print(f"{member.symbol:<12} skipped (finished {state['finished']:%Y-%m-%d})", file=out)
            continue
        started = now()
        try:
            report = fetch(member)
        except Blocked as exc:
            with FundamentalsStore(store_path) as store:
                store.set_run(name, "blocked", error=str(exc)[:200], blocked_until=now() + BLOCK_PAUSE)
            print(f"{member.symbol:<12} BLOCKED ({exc}); stopping, try again after {BLOCK_PAUSE.seconds // 60} minutes", file=out)
            return 3
        with FundamentalsStore(store_path) as store:
            status = "ok" if report.failed == 0 else "partial" if report.fetched + report.skipped > 0 else "failed"
            store.set_run(name, status, filings=report.fetched + report.skipped)
        print(f"{member.symbol:<12} fetched {report.fetched:>3} skipped {report.skipped:>3} failed {report.failed:>3}  {(now() - started).total_seconds():5.0f}s", file=out)
        for note in report.notes[:3]:
            print(f"{'':<12} note: {note}", file=out)
        if stall.saw(report):
            return stop_stalled(store_path, name, member.symbol, out)
        done += 1
    return 0
