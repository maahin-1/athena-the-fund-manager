"""Judges the evidence that the Python-backed Rust tests really ran, from facts the Rust gate collected (this file reads JSON and decides; it starts nothing).

Why it exists: the Rust tests that need Python start `terminal/../.venv/Scripts/python.exe` and, when that file is missing, many of them return early and still PASS. A green count therefore does not show that
any Python-backed test body ran, and "zero Python processes observed" must never count as verified coverage. The gate collects, for every test on the recorded list (rust-python-tests.json), an isolated run
with its own import-origin log, and for the whole run the same log; this module then requires:

  * the interpreter path the tests invoke (`..\\.venv\\Scripts\\python.exe` from `terminal`) runs, is the intended environment, and imports `athena` from the candidate;
  * the recorded list is not empty;
  * every listed test: exit 0, exactly one passed and none failed, outcome `ok`, at least `min_python_processes` (>= 1) Python processes observed on the INTENDED interpreter, none on another one, no `athena`
    module from outside the candidate;
  * no test outside the list was run as part of it, and none on the list is missing from the isolated results;
  * the whole run observed at least `full_run_min_processes` Python processes, none on another interpreter, no import violation.

A test that returns early (no Python process) fails the isolated check, which is the point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def judge(expected: dict, isolated: list, full: dict, interpreter: dict) -> dict:
    reasons: list[str] = []
    tests = expected.get("tests", [])
    if not tests:
        reasons.append("the recorded list of Python-backed tests is empty: coverage cannot be claimed")
    if not interpreter.get("ran"):
        reasons.append("the interpreter path the Rust tests invoke did not run")
    if not interpreter.get("is_candidate_venv_path"):
        reasons.append("the interpreter the tests invoke is not the candidate's .venv python")
    if not interpreter.get("same_environment_as_intended"):
        reasons.append("the interpreter the tests invoke is not the intended environment")
    if not interpreter.get("athena_from_candidate"):
        reasons.append("`athena` imported by the invoked interpreter does not come from the candidate")

    by_id = {r["id"]: r for r in isolated}
    want = {t["id"]: t for t in tests}
    for missing in sorted(set(want) - set(by_id)):
        reasons.append(f"{missing}: no isolated result")
    for extra in sorted(set(by_id) - set(want)):
        reasons.append(f"{extra}: isolated result for a test that is not on the recorded list")
    per_test = []
    for tid, t in want.items():
        r = by_id.get(tid)
        if r is None:
            continue
        need = max(1, int(t.get("min_python_processes", 1)))
        problems = []
        if r.get("exit") != 0:
            problems.append(f"exit {r.get('exit')}")
        if r.get("passed") != 1 or r.get("failed") != 0:
            problems.append(f"passed={r.get('passed')} failed={r.get('failed')} (exactly one pass expected)")
        if r.get("outcome") != "ok":
            problems.append(f"outcome {r.get('outcome')!r}")
        if r.get("processes_on_intended_executable", 0) < need:
            problems.append(f"{r.get('processes_on_intended_executable', 0)} Python process(es) on the intended interpreter, {need} required (an early return does not count)")
        if r.get("processes_on_other_executable", 0):
            problems.append("a process ran on another interpreter")
        if r.get("violations", 0):
            problems.append("`athena` imported from outside the candidate")
        per_test.append({"id": tid, "ok": not problems, "processes": r.get("processes_on_intended_executable", 0), "problems": problems})
        reasons += [f"{tid}: {p}" for p in problems]

    need_full = int(expected.get("full_run_min_processes", 1))
    if full.get("processes_observed", 0) < max(1, need_full):
        reasons.append(f"the full run observed {full.get('processes_observed', 0)} Python processes, at least {max(1, need_full)} required")
    if full.get("processes_on_other_executable", 0):
        reasons.append("the full run has processes on another interpreter")
    if full.get("violations", 0):
        reasons.append("the full run has `athena` imports from outside the candidate")
    return {"ok": not reasons, "reasons": reasons, "listed_tests": len(tests), "tests_verified": sum(1 for p in per_test if p["ok"]), "per_test": per_test,
            "full_run_processes": full.get("processes_observed", 0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", required=True)
    ap.add_argument("--isolated", required=True)
    ap.add_argument("--full-origin", required=True)
    ap.add_argument("--interpreter", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8-sig"))  # noqa: E731
    res = judge(load(a.expected), load(a.isolated), load(a.full_origin), load(a.interpreter))
    Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "per_test"}))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
