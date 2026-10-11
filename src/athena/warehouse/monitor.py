"""Watching the long statements fill from its status file, without touching the database.

    python -m athena.warehouse.monitor             # one look; exit code 0 fine, 1 paused, 2 stalled or stopped, 3 nothing found
    python -m athena.warehouse.monitor --events    # print a line at each 5% of progress and at any change worth knowing; ends when the job does
"""
from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from athena.warehouse.fill import STATUS_PATH, read_status

STALL_AFTER_SECONDS = 300  # a running job updates its status after every stock, a few seconds apart
OK, PAUSED, BAD, NONE = "ok", "paused", "bad", "none"


def _parse(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc) if text else None
    except ValueError:
        return None


def assess(status: dict[str, Any] | None, now: datetime) -> tuple[str, str]:
    """(level, one line) for the job's state: ok, paused (waiting out a refusal), bad (stalled or stopped), none (no status)."""
    if status is None:
        return NONE, "no fill status found: the job has not run"
    state = status.get("state", "")
    updated = _parse(status.get("updated_at", ""))
    age = (now - updated).total_seconds() if updated else float("inf")
    if state == "stopped":
        return BAD, f"stopped: {status.get('reason', 'no reason recorded')}"
    if state == "finished":
        return OK, f"finished: {status['ok']} ok, {status['empty']} empty, {status['error']} failed of {status['total']}"
    if state == "paused":
        limit = status.get("pause_seconds", 600) + STALL_AFTER_SECONDS
        if age > limit:
            return BAD, f"stalled: paused and silent for {age / 60:.0f} min (a pause lasts {status.get('pause_seconds', 600) / 60:.0f})"
        return PAUSED, f"paused after refusals ({status['error']} failed so far); retrying shortly"
    if age > STALL_AFTER_SECONDS:
        return BAD, f"stalled: no update for {age / 60:.0f} min while {state} (the process may have died)"
    return OK, f"{state}: {status['done']}/{status['total']} ({_percent(status)}%), now {status.get('current') or '-'}"


def _percent(status: dict[str, Any]) -> int:
    return int(100 * status["done"] / status["total"]) if status.get("total") else 100


def describe(status: dict[str, Any] | None, now: datetime) -> str:
    level, line = assess(status, now)
    if status is None:
        return line
    started = _parse(status.get("started_at", ""))
    minutes = (now - started).total_seconds() / 60 if started else 0
    lines = [line, f"  this run: {status['ok']} ok, {status['empty']} empty, {status['error']} failed; {status['pauses']} pause(s)"]
    if status["state"] in ("running", "paused") and status["done"]:
        rate = status["done"] / max(minutes, 0.1)
        left = (status["total"] - status["done"]) / rate if rate else 0
        lines.append(f"  {rate:.1f} stocks a minute, about {left:.0f} minutes to go; running for {minutes:.0f} min")
    for text, count in sorted(status.get("errors", {}).items(), key=lambda kv: -kv[1])[:3]:
        lines.append(f"  {count} x {text}")
    if status.get("report"):
        lines.append(f"  report: {status['report']}")
    return "\n".join(lines)


def watch(
    read: Callable[[], dict[str, Any] | None], now: Callable[[], datetime], sleep: Callable[[float], None], emit: Callable[[str], None],
    interval: float = 30.0,
) -> str:
    """Emit a line at each 5% of progress, at a change of state and at a stall; return when the job is over (finished, stopped or
    stalled), the level it ended on."""
    last_step, last_state = -1, None
    while True:
        status = read()
        level, line = assess(status, now())
        state = status.get("state") if status else None
        step = _percent(status) // 5 if status else -1
        if state != last_state or step > last_step or level == BAD:
            emit(line)
            last_step, last_state = max(step, last_step), state
        if level in (BAD, NONE) or state == "finished":
            return level
        sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.monitor", description=__doc__.split("\n")[0])
    parser.add_argument("--events", action="store_true")
    args = parser.parse_args(argv)
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    if args.events:
        level = watch(read_status, clock, time.sleep, lambda text: print(text, flush=True))
        return {OK: 0, PAUSED: 1}.get(level, 2)
    status = read_status(STATUS_PATH)
    print(describe(status, clock()))
    level, _ = assess(status, clock())
    return {OK: 0, PAUSED: 1, BAD: 2, NONE: 3}[level]


if __name__ == "__main__":
    raise SystemExit(main())
