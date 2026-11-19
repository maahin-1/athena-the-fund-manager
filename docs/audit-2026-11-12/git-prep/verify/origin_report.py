"""Summarises the import-origin log written by origin_guard/sitecustomize.py.

Exit 1 when any `athena` module was loaded from outside the candidate, when fewer than --min-athena-processes processes that imported `athena` were observed, when fewer than --min-processes
Python processes were observed at all, or (with --executable) when any observed process ran under a different interpreter than the intended one. An empty log proves nothing and never passes
a gate that asks for at least one process.

Usage: python origin_report.py <log> [--min-athena-processes N] [--min-processes N] [--executable <path of the intended interpreter>] [--out report.json]"""
import argparse
import json
import os
import sys
from pathlib import Path


def norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def merge(rows: list) -> list:
    """One entry per process: its `start` record (always present, even for a process that is later killed) merged with its `exit` record (only a process that ended normally). Rows from the earlier
    recorder (exit records only, no `proc`) count as one process each."""
    procs: dict = {}
    for i, r in enumerate(rows):
        key = r.get("proc") or f"legacy-{i}"
        d = procs.setdefault(key, {"pid": r.get("pid"), "argv": r.get("argv", []), "executable": r.get("executable", ""), "athena_modules": 0, "violations": [], "exited": False})
        if r.get("executable"):
            d["executable"] = r["executable"]
        if r.get("event", "exit") == "exit":
            d["athena_modules"] = r.get("athena_modules", 0)
            d["violations"] = r.get("violations", [])
            d["exited"] = True
    return list(procs.values())


def summarise(rows: list, min_athena: int = 1, min_processes: int = 0, executable: str | None = None) -> dict:
    rows = merge(rows)
    with_athena = [r for r in rows if r["athena_modules"]]
    violations = [(r["pid"], v) for r in rows for v in r["violations"]]
    report = {
        "processes_observed": len(rows),
        "processes_ended_normally": sum(1 for r in rows if r["exited"]),
        "processes_that_imported_athena": len(with_athena),
        "athena_modules_in_largest_process": max((r["athena_modules"] for r in rows), default=0),
        "violations": len(violations),
        "violation_examples": violations[:5],
    }
    ok = (not violations) and len(with_athena) >= min_athena and len(rows) >= min_processes
    if executable:
        want = norm(executable)
        on_intended = [r for r in rows if norm(r.get("executable", "")) == want]
        report["intended_executable"] = executable
        report["processes_on_intended_executable"] = len(on_intended)
        report["processes_on_other_executable"] = len(rows) - len(on_intended)
        report["other_executable_examples"] = sorted({r.get("executable", "") for r in rows if norm(r.get("executable", "")) != want})[:3]
        ok = ok and report["processes_on_other_executable"] == 0
    report["ok"] = bool(ok)
    return report


def read_rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--min-athena-processes", type=int, default=1)
    ap.add_argument("--min-processes", type=int, default=0)
    ap.add_argument("--executable")
    ap.add_argument("--out")
    args = ap.parse_args()
    report = summarise(read_rows(Path(args.log)), args.min_athena_processes, args.min_processes, args.executable)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
