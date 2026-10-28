"""A short exclusive lock for the steps of the order desk that must not run twice at once.

Redeeming a token, checking the limits, placing the order and writing the audit line are one unit: two `confirm` runs at the same moment must not both see an unused token, and two audit
lines must not take the same sequence number. The lock is a file created exclusively and holding the pid of its owner; a lock whose owner is gone is taken over, and a waiter gives up with
an error after `WAIT_SECONDS` rather than waiting for ever. It is held for milliseconds."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from athena.ops.jobs import pid_alive

WAIT_SECONDS = 15.0


class LockTimeout(Exception):
    pass


@contextmanager
def exclusive(path: Path | str, wait: float = WAIT_SECONDS, poll: float = 0.05) -> Iterator[None]:
    lock = Path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + wait
    last_look = 0.0
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"pid": os.getpid()}))
            break
        except (FileExistsError, PermissionError):  # on Windows a lock being deleted answers PermissionError for a moment
            # on Windows a file cannot be deleted while another handle has it open, so a waiter reads the owner at most once a second, not on every poll
            if time.monotonic() - last_look >= 1.0 or time.monotonic() > end:
                last_look = time.monotonic()
                try:
                    pid = int(json.loads(lock.read_text(encoding="utf-8")).get("pid", 0))
                except (OSError, ValueError):
                    pid = 0
                if pid != os.getpid() and not pid_alive(pid):
                    try:
                        lock.unlink()  # the owner is gone
                    except OSError:
                        pass
                    continue
                if time.monotonic() > end:
                    raise LockTimeout(f"{lock.name} is held by pid {pid}") from None
            time.sleep(poll)
    try:
        yield
    finally:
        for _ in range(200):  # another handle may have the file open for a moment: try again rather than leave the lock behind
            try:
                lock.unlink()
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.01)
