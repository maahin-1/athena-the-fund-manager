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
from collections.abc import Sequence
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
        self._data: dict[str, Any] = {}
        self._last_write = 0.0

    # --- locks ---
    def _take(self, lock: str) -> None:
        path = _lock_path(lock, self._lock_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    holder = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    holder = {}
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
