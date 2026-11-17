"""Mutation checks with machine-readable output (WP07 T5 review corrections).

    python mutate.py <spec.json> <label>

A spec lists mutations: {id, file, old, new, command (optional override), note}. For each one the script replaces `old` (which must occur exactly once) by `new` in `file`, runs the spec's command, records
whether the tests caught it (a non-zero exit with no build error), and ALWAYS restores the file afterwards (try/finally, verified by SHA-256; a failed restore is reported and stops the run). Results go to
`runs/<yyyymmdd-HHMMSS>-<label>/`: `mutations.jsonl` (one record per mutation), `summary.json`, `logs/<id>.log` and the spec itself. A mutation that breaks the build is `invalid` (a compile error is not a caught
mutation); a pattern that is missing or ambiguous is `pattern_error`. Nothing outside the files named in the spec is touched, and no environment file or store is opened.

Spec fields: `cwd`, `command` (argv list), `env` (extra environment), `timeout` (seconds per mutation), `kind` (`pytest` | `cargo` | `vitest`, only for extracting failing test names)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs"
FAIL_PATTERNS = {
    "pytest": re.compile(r"^FAILED (\S+)"),
    "cargo": re.compile(r"^test (\S+) \.\.\. FAILED"),
    "vitest": re.compile(r"^\s*(?:FAIL|×)\s+(.+?)\s*(?:\d+ms)?$"),
}
BUILD_ERRORS = {"cargo": ("could not compile", "error[E"), "pytest": ("SyntaxError", "ImportError", "ModuleNotFoundError"), "vitest": ("SyntaxError", "Transform failed", "Failed to resolve import")}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_one(spec: dict, m: dict, logs: Path) -> dict:
    target = Path(m["file"])
    original = target.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    rec = {"id": m["id"], "file": str(target), "note": m.get("note", ""), "outcome": None, "failing": [], "seconds": 0.0, "exit_code": None}
    count = text.count(m["old"])
    if count != 1:
        rec.update(outcome="pattern_error", detail=f"the pattern occurs {count} times, not once")
        return rec
    cmd = list(m.get("command") or spec["command"])
    if cmd[0] == "python":
        cmd[0] = sys.executable  # the interpreter running this script (the project's virtual environment), not whichever `python` a child process would find first
    env = {**os.environ, **spec.get("env", {})}
    started = time.time()
    try:
        target.write_bytes(text.replace(m["old"], m["new"], 1).encode("utf-8"))
        # (T3 review corrections: the output is decoded as UTF-8 with replacement. Before, `text=True` used the Windows code page, vitest's UTF-8 output made the reader thread fail, the log came out empty and the
        # "invalid" check (a mutation that broke the build) could not see the output; the exit code was always the real one.)
        proc = subprocess.run(cmd, cwd=spec["cwd"], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=spec.get("timeout", 900), check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        rec["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired as exc:
        out, rec["exit_code"] = f"TIMEOUT after {exc.timeout}s", -1
    finally:
        target.write_bytes(original)
        if sha(target) != before:
            raise SystemExit(f"RESTORE FAILED for {target}: stopping")
    rec["seconds"] = round(time.time() - started, 1)
    (logs / f"{m['id']}.log").write_text(out, encoding="utf-8", errors="replace")
    kind = spec.get("kind", "pytest")
    rec["failing"] = sorted({mt.group(1) for line in out.splitlines() if (mt := FAIL_PATTERNS[kind].match(line))})[:12]
    if rec["exit_code"] == -1:
        rec["outcome"] = "timeout"
    elif rec["exit_code"] != 0 and any(b in out for b in BUILD_ERRORS[kind]) and not rec["failing"]:
        rec["outcome"] = "invalid"
        rec["detail"] = "the mutation broke the build or the import: not a caught mutation"
    else:
        rec["outcome"] = "caught" if rec["exit_code"] != 0 else "SURVIVED"
    return rec


def main(argv: list[str]) -> int:
    spec_path, label = Path(argv[0]), argv[1]
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))  # (a spec written by Windows PowerShell 5.1 starts with a byte-order mark)
    run = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    logs = run / "logs"
    logs.mkdir(parents=True, exist_ok=False)
    (run / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    records = []
    # (T3 review corrections) A baseline first: the command on the UNMUTATED tree must exit 0, else no mutation result means anything. A harness that fails for its own reasons (the lower-case drive letter in a web
    # spec's `cwd` made vitest load two copies of itself and every test file fail at load) would otherwise report every mutation "caught".
    base_cmd = list(spec["command"])
    base_env = {**os.environ, **spec.get("env", {})}
    base = subprocess.run(base_cmd, cwd=spec["cwd"], env=base_env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=spec.get("timeout", 900), check=False)
    (logs / "baseline-unmutated.log").write_text((base.stdout or "") + (base.stderr or ""), encoding="utf-8", errors="replace")
    (run / "baseline.json").write_text(json.dumps({"exit_code": base.returncode, "command": base_cmd, "cwd": spec["cwd"]}, indent=1), encoding="utf-8")
    if base.returncode != 0:
        (run / "INVALID.txt").write_text("the command failed on the unmutated tree: no mutation result from this run means anything", encoding="utf-8")
        print(f"BASELINE FAILED (exit {base.returncode}): the run is invalid; see {logs / 'baseline-unmutated.log'}")
        return 2
    with open(run / "mutations.jsonl", "w", encoding="utf-8") as f:
        for m in spec["mutations"]:
            rec = run_one(spec, m, logs)
            records.append(rec)
            f.write(json.dumps(rec) + "\n")
            f.flush()
            print(f"{rec['outcome']:>13}  {rec['id']}  ({rec['seconds']}s) {rec['failing'][:1]}")
    counts = {k: sum(1 for r in records if r["outcome"] == k) for k in ("caught", "SURVIVED", "invalid", "pattern_error", "timeout")}
    (run / "summary.json").write_text(json.dumps({"label": label, "total": len(records), "counts": counts, "files_restored": True}, indent=1), encoding="utf-8")
    print(run, counts)
    return 0 if counts["SURVIVED"] == 0 and counts["invalid"] == 0 and counts["pattern_error"] == 0 and counts["timeout"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
