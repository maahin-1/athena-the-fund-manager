"""One look at everything: the jobs and the data.

    python -m athena.ops check          jobs (running, died, failed, stopped) and the data gates; exit code 0 all fine, 1 something to look at, 2 something is wrong
    python -m athena.ops status         the jobs only

A job counts as DIED when its status file still says running but its process is gone, and as STALLED when it says running and has not written for `STALL_MINUTES`. A job that ended on its
own with a reason (`blocked`, `stopped`, `failed`) is shown with that reason. Nothing here starts, stops or schedules anything; the data gates open each store read-only for a moment."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone

from athena.ops import gates
from athena.ops.jobs import all_status, pid_alive

STALL_MINUTES = 30
STALE_DONE_HOURS = 24 * 8  # a job that last finished over a week ago is mentioned, not an alarm


def _age(text: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(text)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def assess_job(row: dict, now: datetime, alive=pid_alive) -> tuple[str, str]:  # noqa: ANN001
    """`(level, one line)` for a job's status file."""
    name, state = row.get("name", "?"), row.get("state", "?")
    progress = row.get("progress") or {}
    where = f" after {progress['done']} companies (last {progress.get('last', '?')})" if "done" in progress else ""
    if state == "running":
        if not alive(int(row.get("pid", 0) or 0)):
            return gates.BAD, f"{name}: DIED (process {row.get('pid')} is gone) while running{where}; last error: {row.get('last_error', 'none recorded')}"
        age = _age(row.get("heartbeat", ""), now)
        if age is not None and age > STALL_MINUTES:
            return gates.BAD, f"{name}: STALLED, no heartbeat for {age:.0f} minutes{where}"
        return gates.OK, f"{name}: running{where}"
    if state == "done":
        return gates.OK, f"{name}: finished{where}"
    if state == "blocked":
        return gates.WARN, f"{name}: stopped, the exchange refused or went quiet{where}; try again later. {row.get('last_error', '')}"
    if state == "stopped":
        return gates.WARN, f"{name}: stopped itself{where}: {row.get('last_error', 'no reason recorded')}"
    return gates.BAD, f"{name}: FAILED{where}: {row.get('last_error', 'no reason recorded')}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=("check", "status"))
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    results: list[gates.Gate] = []
    rows = all_status()
    print("jobs:")
    if not rows:
        print("  none have run under the status wrapper yet")
    for row in rows:
        level, line = assess_job(row, now)
        results.append(gates.Gate(row.get("name", "?"), level, line))
        print(f"  {level.upper():<5} {line}")
    if args.what == "check":
        print("data:")
        for gate in gates.all_gates(now):
            results.append(gate)
            print(f"  {gate.level.upper():<5} {gate.name}: {gate.detail}")
    verdict = gates.worst(results)
    print(f"verdict: {verdict}")
    return {gates.OK: 0, gates.WARN: 1, gates.BAD: 2}[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
