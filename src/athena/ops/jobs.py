"""What a long job tells the owner, and the locks that keep two of them from colliding.

A job wraps its work in `Job(name, locks=(...))`. While it runs it keeps `.athena/status/<name>.json` up to date (pid, state, heartbeat, progress, the last error); when it ends the file says
how. `python -m athena.ops check` reads those files, so a job that died with a traceback shows as DIED without anyone tailing a log.

Locks are small files in `.athena/locks/` holding the pid of the holder. A second job that wants a held lock does not start: it says who holds it. A lock whose holder is no longer running
is taken over. Two locks matter: `nse` (one job at a time talks to the exchange, since the 1.5 s spacing is kept per process, so two jobs would double the request rate) and
`writer:<file>` (one long writer per DuckDB file). Nothing here schedules anything."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

STATUS_DIR = Path(".athena") / "status"
LOCK_DIR = Path(".athena") / "locks"
BEAT_EVERY = 5.0  # seconds between status writes while progress is reported


class JobLocked(Exception):
    def __init__(self, lock: str, holder_pid: int, holder_job: str):
        super().__init__(f"{lock} is held by pid {holder_pid} ({holder_job})")
        self.lock, self.holder_pid, self.holder_job = lock, holder_pid, holder_job


UNRESOLVED = "unresolved"  # a lock record's `state` for a writer whose process could not be confirmed to have exited
_QUARANTINE: dict[str, dict[str, Any]] = {}  # this process's memory of unresolved locks, by lock file path: holds even when the lock file itself could not be written


def _unresolved_blocker(record: Mapping[str, Any]) -> int | None:
    """Whether an unresolved lock record still blocks. None: it does not, because its child pid is known and the process has gone. Otherwise the pid to name in the refusal
    (0 when the child's pid is unknown, which is never assumed dead). The supervisor that wrote the record having ended, or being this very process, proves nothing about the child."""
    child = int(record.get("child_pid", 0) or 0)
    if child > 0 and not pid_alive(child):
        return None
    return child


@dataclass(frozen=True)
class HandOver:
    """What `Job.hand_over` did to each lock. `transferred`: now in the child's name. `quarantined`: marked unresolved with no child pid to follow (blocks until `clear_unresolved`).
    `failed`: (lock, error) pairs where the lock file could not be written; those are blocked in this process's memory only, so the caller must record them elsewhere too."""

    pid: int
    transferred: tuple[str, ...] = ()
    quarantined: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.quarantined and not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {"pid": self.pid, "transferred": list(self.transferred), "quarantined": list(self.quarantined), "failed": [list(item) for item in self.failed]}

    def describe(self) -> str:
        parts = []
        if self.quarantined:
            parts.append(f"{', '.join(self.quarantined)} quarantined without a child pid (cleared only by clear_unresolved)")
        if self.failed:
            parts.append("lock file not written for " + "; ".join(f"{name} ({error})" for name, error in self.failed) + " (blocked in memory only)")
        return "; ".join(parts) or "all locks handed to the child"


def _unresolved_record(owner: str, pid: int, note: str) -> dict[str, Any]:
    return {"pid": pid if pid > 0 else os.getpid(), "job": f"{owner} ({note})", "since": _now(), "state": UNRESOLVED, "child_pid": max(pid, 0), "supervisor_pid": os.getpid(), "note": note}


def reinstate(locks: Sequence[str], child_pid: int, owner: str, lock_dir: Path = LOCK_DIR, note: str = "child, exit unconfirmed (recovered after a restart)") -> HandOver:
    """Put unresolved records back for `locks` after a restart, from what the supervisor wrote down. A lock another running job holds is reported failed rather than overwritten."""
    transferred: list[str] = []
    quarantined: list[str] = []
    failed: list[tuple[str, str]] = []
    for lock in locks:
        path = _lock_path(lock, Path(lock_dir))
        record = _unresolved_record(owner, child_pid, note)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                holder = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # missing or unreadable: nothing live is recorded there
                holder = {}
            holder_pid = int(holder.get("pid", 0) or 0)
            if holder.get("state") != UNRESOLVED and holder_pid and pid_alive(holder_pid) and holder_pid != os.getpid():
                raise OSError(f"held by running pid {holder_pid} ({holder.get('job', '?')})")
            _write(path, record)
        except (OSError, ValueError) as exc:
            _QUARANTINE[str(path)] = record
            failed.append((lock, f"{type(exc).__name__}: {exc}"))
            continue
        (transferred if child_pid > 0 else quarantined).append(lock)
        if child_pid <= 0:
            _QUARANTINE[str(path)] = record
    return HandOver(child_pid, tuple(transferred), tuple(quarantined), tuple(failed))


def sync_quarantine(open_locks: Iterable[str], lock_dir: Path = LOCK_DIR) -> list[str]:
    """Drop this process's in-memory blocks, under `lock_dir`, for locks no longer in `open_locks` (the locks the durable state still lists as unresolved). This is how a block that exists
    only in memory (its lock file could not be written) follows an operator's clear made by another process. Returns the lock files released."""
    keep = {str(_lock_path(lock, Path(lock_dir))) for lock in open_locks}
    prefix = str(Path(lock_dir))
    dropped = [key for key in _QUARANTINE if key.startswith(prefix) and key not in keep]
    for key in dropped:
        _QUARANTINE.pop(key, None)
    return dropped


def clear_unresolved(lock: str, lock_dir: Path = LOCK_DIR) -> bool:
    """The operator's decision that the writer behind an unresolved lock is really gone: removes the lock record and the in-memory block. Returns whether there was one.
    This clears the lock only. The supervisor's durable recovery state must be updated too, or a restart would reinstate the lock: use `athena.ops.supervisor.resolve_unresolved`
    (or `python -m athena.ops.supervisor --clear-unresolved LOCK`), which does both. An unresolved lock with a known child pid frees itself once that process is confirmed gone."""
    path = _lock_path(lock, Path(lock_dir))
    found = _QUARANTINE.pop(str(path), None) is not None
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("state") == UNRESOLVED:
            path.unlink()
            found = True
    except (OSError, ValueError):
        pass
    return found


def pid_alive(pid: int) -> bool:
    """Whether a process with this id is running. Never signals it: on Windows `os.kill` would end the process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_status(name: str, directory: Path = STATUS_DIR) -> dict[str, Any] | None:
    try:
        return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def all_status(directory: Path = STATUS_DIR) -> list[dict[str, Any]]:
    rows = []
    for p in sorted(directory.glob("*.json")) if directory.exists() else []:
        row = read_status(p.stem, directory)
        if row:
            rows.append(row)
    return rows


def _lock_path(lock: str, directory: Path) -> Path:
    return directory / (lock.replace(":", "_").replace("\\", "_").replace("/", "_") + ".lock")


class Job:
    """`with Job("holdings", locks=("nse", "writer:fundamentals.duckdb")) as job: ... job.beat(done=3, last="TCS")`. Raises `JobLocked` on entry when a lock is held by a running job."""

    def __init__(self, name: str, locks: Sequence[str] = (), status_dir: Path = STATUS_DIR, lock_dir: Path = LOCK_DIR):
        self.name, self.locks, self._status_dir, self._lock_dir = name, tuple(locks), Path(status_dir), Path(lock_dir)
        self._held: list[Path] = []
        self._held_names: dict[str, Path] = {}
        self._data: dict[str, Any] = {}
        self._last_write = 0.0

    # --- locks ---
    def _take(self, lock: str) -> None:
        path = _lock_path(lock, self._lock_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        remembered = _QUARANTINE.get(str(path))  # an unresolved writer this process knows of, whether or not its lock file could be written
        if remembered is not None:
            blocker = _unresolved_blocker(remembered)
            if blocker is not None:
                raise JobLocked(lock, blocker, str(remembered.get("job", "unresolved")))
            _QUARANTINE.pop(str(path), None)  # its child is confirmed gone
        for _ in range(3):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    holder = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    holder = {}
                if holder.get("state") == UNRESOLVED:
                    # Never reclaimed because the supervisor that wrote it is this process or has ended: only a known child pid that is confirmed gone frees it.
                    blocker = _unresolved_blocker(holder)
                    if blocker is not None:
                        raise JobLocked(lock, blocker or int(holder.get("supervisor_pid", 0) or 0), str(holder.get("job", "unresolved"))) from None
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                pid = int(holder.get("pid", 0) or 0)
                if pid and pid != os.getpid() and pid_alive(pid):
                    raise JobLocked(lock, pid, str(holder.get("job", "?"))) from None
                try:
                    path.unlink()  # the holder is gone: take the lock over
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"pid": os.getpid(), "job": self.name, "since": _now()}))
            self._held.append(path)
            self._held_names[lock] = path
            return
        raise JobLocked(lock, 0, "unknown")

    def _release(self) -> None:
        for path in self._held:
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    path.unlink()
            except (OSError, ValueError):
                pass
        self._held.clear()
        self._held_names.clear()

    def hand_over(self, pid: int, note: str = "child, exit unconfirmed") -> HandOver:
        """Stop holding the locks without freeing them, for a child process that could not be confirmed to have exited. Returns what happened to each lock; the caller must record it.

        Each lock file is rewritten as an `unresolved` record in the name of `pid` (the child). While that process runs the lock refuses every other job, and once it is gone the lock is
        taken over like a dead holder's. With no usable pid (`pid <= 0`) the record is still written, but with `child_pid` 0, which is never assumed dead: it blocks until
        `clear_unresolved`. If the file cannot be rewritten the lock is remembered in this process (`_QUARANTINE`) and reported in `failed`; it is never simply let go, because a lock
        still naming this process would be reclaimed by the next job here, and one naming a supervisor that later ends would look stale."""
        transferred: list[str] = []
        quarantined: list[str] = []
        failed: list[tuple[str, str]] = []
        for lock, path in list(self._held_names.items()):
            record = _unresolved_record(self.name, pid, note if pid > 0 else "child pid unknown, exit unconfirmed")
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("pid") != os.getpid():
                    raise ValueError("the lock is no longer held by this process")
                _write(path, record)
            except (OSError, ValueError) as exc:
                _QUARANTINE[str(path)] = record
                failed.append((lock, f"{type(exc).__name__}: {exc}"))
                continue
            if pid > 0:
                transferred.append(lock)
            else:
                _QUARANTINE[str(path)] = record
                quarantined.append(lock)
        self._held.clear()
        self._held_names.clear()
        outcome = HandOver(pid, tuple(transferred), tuple(quarantined), tuple(failed))
        self._data["handover"] = outcome.as_dict()
        return outcome

    # --- status ---
    def _save(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._last_write < BEAT_EVERY:
            return
        self._data["heartbeat"] = _now()
        _write(self._status_dir / f"{self.name}.json", self._data)
        self._last_write = time.monotonic()

    def beat(self, **progress: Any) -> None:
        self._data.setdefault("progress", {}).update(progress)
        self._save()

    def note_error(self, text: str) -> None:
        self._data["last_error"] = str(text)[:300]
        self._save(force=True)

    def finish(self, state: str, exit_code: int = 0, error: str = "") -> None:
        """Say how the job ended: `done`, `blocked` (the exchange refused), `stopped` (it stopped itself with a reason) or `failed`."""
        self._data.update({"state": state, "exit_code": exit_code, "ended": _now()})
        if error:
            self._data["last_error"] = error[:300]
        self._save(force=True)
        self._ended = True

    def __enter__(self) -> "Job":
        try:
            for lock in self.locks:
                self._take(lock)
        except JobLocked:
            self._release()
            raise
        self._ended = False
        self._data = {"name": self.name, "pid": os.getpid(), "state": "running", "started": _now(), "progress": {}, "locks": list(self.locks)}
        self._save(force=True)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if not self._ended:
            if exc_type is None:
                self.finish("done")
            elif issubclass(exc_type, KeyboardInterrupt):
                self.finish("stopped", 130, "interrupted")
            else:
                self.finish("failed", 1, f"{exc_type.__name__}: {exc}")
        self._release()
