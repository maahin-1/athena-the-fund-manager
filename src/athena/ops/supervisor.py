"""Keeps the data fresh while the software is on: runs each data job when it is due, one at a time, and stops when the software does.

    python -m athena.ops.supervisor [--parent-pid PID] [--once]     (the terminal starts this itself; running it by hand does the same)
    python -m athena.ops.supervisor --list                           the jobs, when each last ran and when it is next due
    python -m athena.ops.supervisor --clear-unresolved LOCK          the operator's decision that the process behind an unresolved lock is gone (see below)

Nothing here is scheduled with the operating system: the person starts the software, the software starts this, and this ends with it (`--parent-pid` is the process to follow). Every job it runs is
one of the resumable jobs in this repository, started as a child process with the same command a person would type, so a job that dies, is refused by the exchange, or finds the language model down
costs only its own turn. The jobs run sequentially, in the order below, which is the order they matter in; when one is not due the next is looked at.

How a job's end is read (its exit code):
    0   done; next run after `every_hours`
    3   the exchange stalled or refused; try again in 2 hours
    4   the language model did not answer; try again in 6 hours (only the jobs that need a model end this way)
    5   a store would not answer; try again in 30 minutes
    6   another job holds the lock (the person started one by hand); try again in 10 minutes
    other   a failure; backs off 30 minutes, doubling per failure in a row, up to 6 hours
State is kept in `.athena/supervisor.json` so a restart of the software carries on where it was. `.athena/jobs.json` (written by hand) may hold
    {"disabled": ["annual_reports"], "every_hours": {"derivs": 24}}
to switch a job off or change its gap. The jobs that read the whole market go through the exchange, which is asked politely (the jobs keep their own spacing and share one lock)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.ops import jobs
from athena.ops.jobs import LOCK_DIR, STATUS_DIR, HandOver, Job, JobLocked, pid_alive

STATE_PATH = Path(".athena") / "supervisor.json"
CONFIG_PATH = Path(".athena") / "jobs.json"
LOG_DIR = Path("logs") / "jobs"
MAX_LOG_BYTES = 5_000_000
LOOP_SECONDS = 20.0
WAIT_FOR_OLD_ATTEMPTS = 13  # about a minute
BACKOFF_HOURS = {3: 2.0, 4: 6.0, 5: 0.5, 6: 10 / 60}
FAIL_BACKOFF_HOURS = 0.5
FAIL_BACKOFF_MAX_HOURS = 6.0
TERMINATE_WAIT = 20.0  # seconds a child is given to leave after terminate() before it is killed
KILL_WAIT = 10.0  # seconds it is given to be gone after kill() before its exit is called unconfirmed
TIMED_OUT, STOPPED = 124, 130  # exit codes this supervisor gives a job it ended itself (the job's own codes 3-6 are the exchange, model, store and lock)


@dataclass(frozen=True)
class Task:
    name: str
    argv: tuple[str, ...]  # what follows `python -u -m`
    every_hours: float
    locks: tuple[str, ...] = ()  # taken by the supervisor for a job that does not take its own (the fundamentals jobs do)
    max_hours: float = 8.0  # a run longer than this is ended and counted as a failure


TASKS: tuple[Task, ...] = (
    Task("prices", ("athena.warehouse.build",), 6, ("nse", "writer:warehouse.duckdb"), 3),
    Task("derivs", ("athena.derivs.ingest", "--since", "2020-01-01"), 6, ("nse", "writer:derivs.duckdb"), 3),
    Task("events", ("athena.warehouse.events",), 12, ("nse", "writer:warehouse.duckdb"), 1),
    Task("evidence", ("athena.evidence.collect",), 1, (), 0.3),  # takes its own writer lock
    Task("evidence_nse", ("athena.evidence.structured",), 1, (), 0.3),  # insider trades, FII/DII, board meetings; each only when due
    Task("ipo", ("athena.ipo.collect",), 2, (), 0.2),  # the IPO calendar, subscription, listings and SEBI drafts
    Task("evidence_claims", ("athena.evidence.verify",), 1, (), 0.3),  # read new items for claims and check them against our own data
    Task("index_members", ("athena.warehouse.members",), 24 * 7, ("nse", "writer:warehouse.duckdb"), 1),
    Task("fundamentals_ingest", ("athena.fundamentals.ingest", "--universe", "all", "--resume"), 24, (), 12),
    Task("holdings", ("athena.fundamentals.holdings", "--universe", "all", "--resume"), 24, (), 12),
    Task("documents", ("athena.fundamentals.documents", "--universe", "all", "--resume"), 24, (), 12),
    Task("annual_reports", ("athena.fundamentals.annual_reports", "--universe", "nifty500", "--resume", "--back-to", "2016"), 24, (), 12),
    Task("valuation_snapshot", ("athena.valuation", "snapshot"), 24, ("writer:valuation_ledger.duckdb",), 1),
    Task("checks", ("athena.ops", "check"), 6, (), 0.2),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tasks": {}}


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """The person's own file; a bad file is ignored (the jobs run with their defaults) rather than stopping the data from updating."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # utf-8-sig: Windows tools write a byte-order mark, which plain utf-8 reads as bad JSON
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def gap_hours(task: Task, config: dict[str, Any]) -> float:
    override = (config.get("every_hours") or {}).get(task.name)
    return float(override) if isinstance(override, (int, float)) and not isinstance(override, bool) and override > 0 else task.every_hours


def is_due(task: Task, state: dict[str, Any], config: dict[str, Any], now: datetime) -> bool:
    if task.name in (config.get("disabled") or []):
        return False
    row = state.get("tasks", {}).get(task.name)
    if not row:
        return True
    try:
        until = datetime.fromisoformat(row.get("next_due", ""))
    except ValueError:
        return True
    return now >= until


def after_run(task: Task, row: dict[str, Any], code: int, now: datetime, config: dict[str, Any]) -> dict[str, Any]:
    """The task's row after a run that ended with `code`: when it may run next, and how many failures in a row."""
    row = dict(row)
    row.update({"last_end": now.isoformat(timespec="seconds"), "last_exit": code})
    if code == 0:
        row.update({"last_ok": row["last_end"], "fail_streak": 0, "next_due": (now + timedelta(hours=gap_hours(task, config))).isoformat(timespec="seconds")})
    elif code in BACKOFF_HOURS:
        row["next_due"] = (now + timedelta(hours=BACKOFF_HOURS[code])).isoformat(timespec="seconds")
    else:
        streak = int(row.get("fail_streak", 0)) + 1
        wait = min(FAIL_BACKOFF_MAX_HOURS, FAIL_BACKOFF_HOURS * 2 ** (streak - 1))
        row.update({"fail_streak": streak, "next_due": (now + timedelta(hours=wait)).isoformat(timespec="seconds")})
    return row


# --- unresolved children: lifecycle state, kept apart from the per-attempt result of a run
# A task's row keeps `unconfirmed_child`: the newest child that was ended but not confirmed gone (pid, locks, handover), with `previous` holding older ones. A run that is refused, retried,
# fails or succeeds says nothing about such a child, so no run removes it. It leaves only when its exit is confirmed (a known pid that is no longer running) or an operator resolves its
# locks durably (`resolve_unresolved`). An unknown pid is never confirmed.
def unpack(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The unresolved-child records on a task row, newest first."""
    head = row.get("unconfirmed_child")
    if not isinstance(head, dict):
        return []
    return [{k: v for k, v in head.items() if k != "previous"}, *(dict(p) for p in head.get("previous") or [] if isinstance(p, dict))]


def pack(records: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    head, *rest = records
    return {**head, "previous": list(rest)} if rest else dict(head)


def is_open(record: dict[str, Any]) -> bool:
    """Whether a child record is still unresolved: its pid is unknown, or the process is still running."""
    pid = int(record.get("pid", 0) or 0)
    return pid <= 0 or jobs.pid_alive(pid)


def open_locks(state: dict[str, Any]) -> set[str]:
    """Every lock the durable state still lists as held by an unresolved child."""
    return {lock for row in (state.get("tasks") or {}).values() for record in unpack(row) if is_open(record) for lock in record.get("locks") or []}


def resolve_unresolved(lock: str, state_path: Path = STATE_PATH, lock_dir: Path = LOCK_DIR, now: datetime | None = None) -> dict[str, Any]:
    """The operator's decision that the writer behind `lock` is gone, applied durably: the lock is dropped from every unresolved-child record in the state file (a record left with
    no lock to guard is removed), an audit entry is added under `operator_resolutions`, and only then the lock record and any in-memory block are removed, so startup recovery cannot put
    it back. If saving the state fails this raises and the lock is left blocked. A running supervisor drops its own in-memory block for the lock at its next step."""
    state = load_state(state_path)
    touched: list[str] = []
    for name, row in (state.get("tasks") or {}).items():
        records, changed = unpack(row), False
        for record in records:
            if lock in (record.get("locks") or []):
                record["locks"] = [item for item in record["locks"] if item != lock]
                record["operator_cleared"] = [*record.get("operator_cleared", []), lock]
                changed = True
        if not changed:
            continue
        packed = pack([r for r in records if r.get("locks") or "operator_cleared" not in r])  # a record whose only locks were cleared guards nothing
        if packed:
            row["unconfirmed_child"] = packed
        else:
            row.pop("unconfirmed_child", None)
        touched.append(name)
    state.setdefault("operator_resolutions", []).append({"lock": lock, "at": (now or _now()).isoformat(timespec="seconds"), "tasks": touched})
    save_state(state, state_path)
    removed = jobs.clear_unresolved(lock, lock_dir)
    return {"lock": lock, "tasks": touched, "lock_record_removed": removed}


def _exited(proc: subprocess.Popen, timeout: float) -> bool:
    """Whether `proc` has gone, waiting up to `timeout` seconds for it. Only a returned wait or a set return code counts; a timeout or an error is not confirmation."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return proc.poll() is not None
    except OSError:
        return proc.poll() is not None


def end_child(proc: subprocess.Popen, terminate_wait: float = TERMINATE_WAIT, kill_wait: float = KILL_WAIT) -> bool:
    """End a child job: terminate, wait a bounded time, kill if it is still there, wait again. True only when the child's exit is confirmed (never merely because a signal was sent).

    Used for both a job past its time and a job running when the software closes, so the two cannot drift apart. It ends the child process only: grandchildren it started
    are not tracked (no process group or job object is used), a known limit; every job here runs its own work in one process."""
    for send, wait in ((proc.terminate, terminate_wait), (proc.kill, kill_wait)):
        try:
            send()
        except OSError:  # already gone (or not ours to signal): the wait below decides whether it really has exited
            pass
        if _exited(proc, wait):
            return True
    return False


def _log_file(name: str):  # noqa: ANN202
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.log"
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_suffix(".log.old"))
    fh = path.open("ab")
    fh.write(f"\n=== {_now().isoformat(timespec='seconds')} start ===\n".encode())
    fh.flush()
    return fh


class Supervisor:
    def __init__(
        self, tasks: Sequence[Task] = TASKS, parent_pid: int = 0, state_path: Path = STATE_PATH, config_path: Path = CONFIG_PATH,
        spawn: Callable[[Task], subprocess.Popen] | None = None, clock: Callable[[], datetime] = _now, sleep: Callable[[float], None] = time.sleep,
        alive: Callable[[int], bool] = pid_alive, status_dir: Path = STATUS_DIR, lock_dir: Path = LOCK_DIR,
    ):
        self.tasks, self.parent_pid, self._state_path, self._config_path = tuple(tasks), parent_pid, Path(state_path), Path(config_path)
        self._status_dir, self._lock_dir = Path(status_dir), Path(lock_dir)
        self._spawn, self._clock, self._sleep, self._alive = spawn or self._start, clock, sleep, alive
        self._stop = False
        self.unconfirmed: dict[str, Any] | None = None  # set by `_run` when the last job's child could not be confirmed to have exited
        self.recovery_errors: list[str] = []  # locks `recover` could not put back (the block then holds in memory only)

    # --- one child job ---
    @staticmethod
    def _start(task: Task) -> subprocess.Popen:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashes up for a background job
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.Popen([sys.executable, "-u", "-m", *task.argv], stdout=_log_file(task.name), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags, env=env)

    def _snapshot(self) -> None:
        """Publish a snapshot generation of the warehouse as it is before a job that writes it begins, so charts and pages read a validated copy while the job holds the real file (see
        athena.ops.snapshot). The job about to hold the file is named in the manifest. A failed publication is recorded in the snapshot status and never stops the job."""
        from athena.ops import snapshot

        snapshot.refresh(snapshot.WAREHOUSE, getattr(self, "_snapshot_job", None))

    def parent_gone(self) -> bool:
        return self.parent_pid > 0 and not self._alive(self.parent_pid)

    def _run(self, task: Task) -> int:
        """Run one job to its end and return its exit code. Ends the job (code 130) when the software that started this has gone, and (code 124) when it runs past `max_hours`.

        Ending a job means terminate, wait, kill if needed, wait again (`end_child`), and the job's writer lock is released only after its exit is confirmed. If it cannot be confirmed
        the locks are handed over as `unresolved` records (`Job.hand_over`): in the child's pid when it is known, or quarantined until an operator clears them when it is not. A lock
        that cannot be written is still refused in this process, and the outcome is recorded in `self.unconfirmed`, the state file (so `recover` can reinstate it after a restart)
        and the job's status error. The exit code is still 124 or 130."""
        self.unconfirmed = None
        locks = Job(task.name, locks=task.locks, status_dir=self._status_dir, lock_dir=self._lock_dir) if task.locks else None
        try:
            if locks:
                locks.__enter__()
        except JobLocked:
            return 6
        code = 1
        error = ""
        if "writer:warehouse.duckdb" in task.locks:
            self._snapshot_job = task.name
            self._snapshot()
        try:
            proc = self._spawn(task)
            started = time.monotonic()
            while True:
                code = proc.poll()
                if code is not None:
                    return code
                stopping = self._stop or self.parent_gone()
                if stopping or time.monotonic() - started > task.max_hours * 3600:
                    code = STOPPED if stopping else TIMED_OUT
                    # The writer lock stays held until the child's exit is confirmed (the `finally` below runs after this returns).
                    if not end_child(proc):
                        pid = int(getattr(proc, "pid", 0) or 0)
                        handover = locks.hand_over(pid) if locks else HandOver(pid)
                        self.unconfirmed = {
                            "pid": pid, "since": self._clock().isoformat(timespec="seconds"), "ended_because": "timeout" if code == TIMED_OUT else "software closed",
                            "locks": list(task.locks), "handover": handover.as_dict(),
                        }
                        error = f"child pid {pid or 'unknown'} was terminated and killed but its exit was not confirmed"
                        error += "; its locks are held in its name" if handover.ok else f"; lock handover incomplete: {handover.describe()}"
                    return code
                self._sleep(2.0)
        finally:
            if locks:
                locks.finish("done" if code == 0 else "stopped" if code == STOPPED and not error else "failed", code, error)
                locks.__exit__(None, None, None)

    def recover(self) -> None:
        """At start, after a restart: a child recorded as unconfirmed in the state file may still be running, and the old supervisor ending says nothing about it. While its pid is alive
        (or unknown) the locks it was holding are put back as unresolved records, so no conflicting job is scheduled; once the pid is confirmed gone the record is dropped. Locks that
        cannot be written are listed in `recovery_errors` and in the state file, and are still refused in this process's memory."""
        state = load_state(self._state_path)
        warnings: list[str] = []
        changed = False
        for task in self.tasks:
            row = (state.get("tasks") or {}).get(task.name) or {}
            records = unpack(row)
            if not records:
                continue
            still_open = [record for record in records if is_open(record)]
            if len(still_open) != len(records):  # confirmed gone; the lock record, if any, frees itself on the next take
                packed = pack(still_open)
                if packed:
                    row["unconfirmed_child"] = packed
                else:
                    row.pop("unconfirmed_child", None)
                changed = True
            for record in still_open:
                pid = int(record.get("pid", 0) or 0)
                held = record["locks"] if "locks" in record else list(task.locks)  # an emptied list (operator-cleared) reinstates nothing; only a record with no list at all falls back
                outcome = jobs.reinstate(held, pid, task.name, self._lock_dir)
                for name, error in outcome.failed:
                    warnings.append(f"{task.name}: lock {name} could not be reinstated ({error})")
        self.recovery_errors = warnings
        if warnings or changed:
            state["recovery_warnings"] = warnings
            save_state(state, self._state_path)

    # --- the loop ---
    def step(self) -> str | None:
        """Run the first due job, if any. Returns its name, or None when nothing was due."""
        state, config, now = load_state(self._state_path), load_config(self._config_path), self._clock()
        jobs.sync_quarantine(open_locks(state), self._lock_dir)  # an in-memory block follows the durable state, e.g. an operator's clear made by another process
        for task in self.tasks:
            if not is_due(task, state, config, now):
                continue
            row = dict(state.setdefault("tasks", {}).get(task.name) or {})
            row["last_start"] = now.isoformat(timespec="seconds")
            state["tasks"][task.name] = row
            state.update({"pid": os.getpid(), "heartbeat": now.isoformat(timespec="seconds"), "running": task.name})
            save_state(state, self._state_path)
            code = self._run(task)
            state = load_state(self._state_path)
            end = self._clock()
            if code == STOPPED:  # the software was closed: nothing about the job's health is learned from that
                state["tasks"][task.name] = {**state["tasks"].get(task.name, {}), "last_exit": STOPPED, "next_due": end.isoformat(timespec="seconds")}
            else:
                state["tasks"][task.name] = after_run(task, state["tasks"].get(task.name, {}), code, end, config)
                if code == TIMED_OUT:  # ended by this supervisor for running past max_hours: the job's fault, not an exchange (3), model (4), store (5) or lock (6) answer
                    state["tasks"][task.name]["last_reason"] = "timeout"
            # Children still unresolved from earlier attempts stay on the row whatever this attempt did: a refused retry (6) launched nothing, so it proves nothing about them.
            # Only a confirmed exit (or an operator's durable resolution, `resolve_unresolved`) removes one. This attempt's own unresolved child, if any, goes in front.
            older = [r for r in unpack(state["tasks"][task.name]) if is_open(r) and r.get("pid") != (self.unconfirmed or {}).get("pid")]
            packed = pack([*([self.unconfirmed] if self.unconfirmed else []), *older])
            if packed:
                state["tasks"][task.name]["unconfirmed_child"] = packed
            else:
                state["tasks"][task.name].pop("unconfirmed_child", None)
            state.update({"running": "", "heartbeat": end.isoformat(timespec="seconds")})
            save_state(state, self._state_path)
            return task.name
        state["heartbeat"] = now.isoformat(timespec="seconds")
        state.update({"pid": os.getpid(), "running": ""})
        save_state(state, self._state_path)
        return None

    def run(self, once: bool = False) -> None:
        self.recover()  # before anything is scheduled
        while not self._stop and not self.parent_gone():
            ran = self.step()
            if once and ran is None:
                return
            if ran is None:
                for _ in range(int(LOOP_SECONDS)):
                    if self._stop or self.parent_gone():
                        return
                    self._sleep(1.0)

    def stop(self) -> None:
        self._stop = True


def describe(state: dict[str, Any], config: dict[str, Any], now: datetime, tasks: Sequence[Task] = TASKS) -> list[str]:
    lines = []
    for task in tasks:
        row = (state.get("tasks") or {}).get(task.name) or {}
        if task.name in (config.get("disabled") or []):
            lines.append(f"{task.name:<22} switched off in {CONFIG_PATH}")
            continue
        due = "due now" if is_due(task, state, config, now) else f"next {row.get('next_due', '?')}"
        ok = row.get("last_ok", "never")
        extra = f", {row['fail_streak']} failure(s) in a row" if row.get("fail_streak") else ""
        lines.append(f"{task.name:<22} last ok {ok}; last exit {row.get('last_exit', '-')}{extra}; {due}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--parent-pid", type=int, default=0, help="end when this process ends (the terminal passes its own)")
    parser.add_argument("--once", action="store_true", help="run what is due now and stop")
    parser.add_argument("--list", action="store_true", help="show the jobs and when each is next due")
    parser.add_argument("--clear-unresolved", metavar="LOCK", help="the operator's decision that the process behind an unresolved lock (e.g. writer:warehouse.duckdb) is gone: clears the lock and the saved recovery record")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(describe(load_state(), load_config(), _now())))
        return 0
    if args.clear_unresolved:
        result = resolve_unresolved(args.clear_unresolved)
        print(f"{result['lock']}: lock record {'removed' if result['lock_record_removed'] else 'was not there'}; recovery record updated for {', '.join(result['tasks']) or 'no task'}")
        return 0
    for attempt in range(WAIT_FOR_OLD_ATTEMPTS):
        try:
            with Job("supervisor", locks=("supervisor",)):
                Supervisor(parent_pid=args.parent_pid).run(once=args.once)
            return 0
        except JobLocked as exc:
            # the software was restarted while the old supervisor was still ending its job: wait for it to go, then take over
            if attempt == WAIT_FOR_OLD_ATTEMPTS - 1:
                print(f"another supervisor is already running: {exc}", file=sys.stderr)
                return 6
            time.sleep(5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
