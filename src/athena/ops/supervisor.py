"""Keeps the data fresh while the software is on: runs each data job when it is due, one at a time, and stops when the software does.

    python -m athena.ops.supervisor [--parent-pid PID] [--once]     (the terminal starts this itself; running it by hand does the same)
    python -m athena.ops.supervisor --list                           the jobs, when each last ran and when it is next due

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

from athena.ops.jobs import Job, JobLocked, pid_alive

STATE_PATH = Path(".athena") / "supervisor.json"
CONFIG_PATH = Path(".athena") / "jobs.json"
LOG_DIR = Path("logs") / "jobs"
MAX_LOG_BYTES = 5_000_000
LOOP_SECONDS = 20.0
WAIT_FOR_OLD_ATTEMPTS = 13  # about a minute
BACKOFF_HOURS = {3: 2.0, 4: 6.0, 5: 0.5, 6: 10 / 60}
FAIL_BACKOFF_HOURS = 0.5
FAIL_BACKOFF_MAX_HOURS = 6.0


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
        data = json.loads(path.read_text(encoding="utf-8"))
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
        alive: Callable[[int], bool] = pid_alive,
    ):
        self.tasks, self.parent_pid, self._state_path, self._config_path = tuple(tasks), parent_pid, Path(state_path), Path(config_path)
        self._spawn, self._clock, self._sleep, self._alive = spawn or self._start, clock, sleep, alive
        self._stop = False

    # --- one child job ---
    @staticmethod
    def _start(task: Task) -> subprocess.Popen:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashes up for a background job
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.Popen([sys.executable, "-u", "-m", *task.argv], stdout=_log_file(task.name), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags, env=env)

    def parent_gone(self) -> bool:
        return self.parent_pid > 0 and not self._alive(self.parent_pid)

    def _run(self, task: Task) -> int:
        """Run one job to its end and return its exit code. Ends the job (code 130) when the software that started this has gone, and (code 124) when it runs past `max_hours`."""
        locks = Job(task.name, locks=task.locks) if task.locks else None
        try:
            if locks:
                locks.__enter__()
        except JobLocked:
            return 6
        code = 1
        try:
            proc = self._spawn(task)
            started = time.monotonic()
            while True:
                code = proc.poll()
                if code is not None:
                    return code
                if self._stop or self.parent_gone():
                    proc.terminate()
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    code = 130
                    return code
                if time.monotonic() - started > task.max_hours * 3600:
                    proc.terminate()
                    code = 124
                    return code
                self._sleep(2.0)
        finally:
            if locks:
                locks.finish("done" if code == 0 else "stopped" if code == 130 else "failed", code)
                locks.__exit__(None, None, None)

    # --- the loop ---
    def step(self) -> str | None:
        """Run the first due job, if any. Returns its name, or None when nothing was due."""
        state, config, now = load_state(self._state_path), load_config(self._config_path), self._clock()
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
            if code == 130:  # the software was closed: nothing about the job's health is learned from that
                state["tasks"][task.name] = {**state["tasks"].get(task.name, {}), "last_exit": 130, "next_due": end.isoformat(timespec="seconds")}
            else:
                state["tasks"][task.name] = after_run(task, state["tasks"].get(task.name, {}), code, end, config)
            state.update({"running": "", "heartbeat": end.isoformat(timespec="seconds")})
            save_state(state, self._state_path)
            return task.name
        state["heartbeat"] = now.isoformat(timespec="seconds")
        state.update({"pid": os.getpid(), "running": ""})
        save_state(state, self._state_path)
        return None

    def run(self, once: bool = False) -> None:
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
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(describe(load_state(), load_config(), _now())))
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
