"""The loop every manual fetch command shares: go through the companies one at a time, skip those finished recently when asked, stop cleanly when
the exchange refuses (recording when to try again), and print one line per company. What is fetched for one company is the caller's `fetch`.

What it does when something goes wrong, so a run that takes hours does not need watching:
  * one company failing with an unforeseen error is recorded as failed, with the reason, and the run goes on;
  * a store that is busy or unreadable is waited for and tried once more; five such failures in a row stop the run (something is wrong with the file, not the company);
  * five companies in a row that fail outright against the exchange stop the run and pause the exchange jobs for a few minutes;
  * five in a row that fail only because the language model did not answer stop THIS run alone, with its own message, and pause nothing else;
  * with a `job` name the run keeps `.athena/status/<job>.json` current and holds the `nse` and writer locks, so a second job cannot start beside it (see `athena.ops.jobs`)."""
from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TextIO

import duckdb

from athena.contracts import AthenaError
from athena.fundamentals.ingest import BLOCK_PAUSE, Blocked, Report, Stall, stop_stalled
from athena.fundamentals.store import FundamentalsStore, now
from athena.fundamentals.universe import Member
from athena.ops.jobs import Job, JobLocked

STORE_RETRY_WAIT = 20.0  # seconds before a busy or unreadable store is tried once more
STOP_AFTER_STORE_ERRORS = 5
STOP_AFTER_ERRORS = 10  # unforeseen errors in a row
STOP_AFTER_MODEL_FAILURES = 5
EXIT_LOCKED, EXIT_MODEL, EXIT_STORE = 6, 4, 5


def is_store_error(exc: BaseException) -> bool:
    return isinstance(exc, duckdb.IOException) or (isinstance(exc, AthenaError) and "store is busy" in str(exc))


def run_each(
    members: Sequence[Member], fetch: Callable[[Member], Report], store_path: str | Path, *, key: Callable[[Member], str], resume: bool,
    max_age_days: int, limit: int | None, refresh: bool = False, out: TextIO = sys.stdout, job: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """`fetch` each member in turn. Returns 0 when done, 3 when the exchange blocked it (or a block is still running), 4 when the language model stopped answering, 5 when the store or too
    many unforeseen errors stopped it, 6 when another job holds a lock it needs. The state of a member is kept under `key(member)`: `ok`, `partial` (some items could not be read, so
    `--resume` tries it again), `failed` or `blocked`."""
    if job is None:
        return _loop(members, fetch, store_path, key, resume, max_age_days, limit, refresh, out, None, sleep)
    try:
        with Job(job, locks=("nse", f"writer:{Path(store_path).name}")) as running:
            code = _loop(members, fetch, store_path, key, resume, max_age_days, limit, refresh, out, running, sleep)
            if not running._ended:
                state = {0: "done", 3: "blocked", 4: "stopped", 5: "stopped"}.get(code, "stopped")
                running.finish(state, code, running._data.get("last_error", "") if code else "")
            return code
    except JobLocked as exc:
        print(f"not starting: {exc}; one job at a time talks to the exchange and writes this file", file=out)
        return EXIT_LOCKED


def _set_run(store_path: str | Path, name: str, status: str, **kw: object) -> None:
    try:
        with FundamentalsStore(store_path) as store:
            store.set_run(name, status, **kw)  # type: ignore[arg-type]
    except Exception:  # recording the state must not end the run that is trying to report it
        pass


def _loop(members, fetch, store_path, key, resume, max_age_days, limit, refresh, out, running, sleep) -> int:  # noqa: ANN001
    with FundamentalsStore(store_path) as store:
        until = store.blocked_until()
    if until and until > now():
        print(f"blocked by the exchange until {until:%H:%M} UTC; not starting", file=out)
        return 3
    done = 0
    stall = Stall()
    model_failures = store_errors = errors = 0
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
        report: Report | None = None
        for attempt in (1, 2):
            try:
                report = fetch(member)
                break
            except Blocked as exc:
                _set_run(store_path, name, "blocked", error=str(exc)[:200], blocked_until=now() + BLOCK_PAUSE)
                print(f"{member.symbol:<12} BLOCKED ({exc}); stopping, try again after {BLOCK_PAUSE.seconds // 60} minutes", file=out)
                if running:
                    running.finish("blocked", 3, str(exc))
                return 3
            except Exception as exc:  # noqa: BLE001 - one company must not end a run that takes hours
                if is_store_error(exc) and attempt == 1:
                    print(f"{member.symbol:<12} the store did not answer ({type(exc).__name__}); waiting {STORE_RETRY_WAIT:.0f}s and trying once more", file=out)
                    sleep(STORE_RETRY_WAIT)
                    continue
                store_errors = store_errors + 1 if is_store_error(exc) else 0
                errors += 1
                text = f"{type(exc).__name__}: {str(exc)[:160]}"
                print(f"{member.symbol:<12} ERROR {text}", file=out)
                _set_run(store_path, name, "failed", error=text[:200])
                if running:
                    running.note_error(f"{member.symbol}: {text}")
                break
        if report is None:
            if store_errors >= STOP_AFTER_STORE_ERRORS or errors >= STOP_AFTER_ERRORS:
                why = "the store failed" if store_errors >= STOP_AFTER_STORE_ERRORS else "unforeseen errors"
                print(f"{member.symbol:<12} stopping: {why} {max(store_errors, errors)} times in a row; the file or the code needs a look, not the company", file=out)
                if running:
                    running.finish("stopped", EXIT_STORE, why)
                return EXIT_STORE
            continue
        errors = store_errors = 0
        status = "ok" if report.failed == 0 else "partial" if report.fetched + report.skipped > 0 else "failed"
        _set_run(store_path, name, status, filings=report.fetched + report.skipped)
        print(f"{member.symbol:<12} fetched {report.fetched:>3} skipped {report.skipped:>3} failed {report.failed:>3}  {(now() - started).total_seconds():5.0f}s", file=out)
        for note in report.notes[:3]:
            print(f"{'':<12} note: {note}", file=out)
        done += 1
        if running:
            running.beat(done=done, last=member.symbol, last_status=status)
        model_failures = model_failures + 1 if report.model_failed > 0 and report.fetched + report.skipped == 0 else 0
        if model_failures >= STOP_AFTER_MODEL_FAILURES:
            reason = f"the language model did not answer for {STOP_AFTER_MODEL_FAILURES} companies in a row"
            print(f"{member.symbol:<12} stopping: {reason}. Nothing was recorded against the exchange; fix the model (python -m athena.llm.probe) and run again with --resume.", file=out)
            if running:
                running.finish("stopped", EXIT_MODEL, reason)
            return EXIT_MODEL
        if stall.saw(report):
            code = stop_stalled(store_path, name, member.symbol, out)
            if running:
                running.finish("blocked", code, "no reply from the exchange")
            return code
    return 0
