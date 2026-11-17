"""Mutation checks with machine-readable evidence (WP07; version 2, T3 review follow-up T3-C2).

    python mutate.py <spec.json> <label>

A spec lists mutations: {id, file, old, new, command (optional override), expect (optional), note}. For each one the script replaces `old` (which must occur exactly once) by `new` in `file`, runs the
command, reads the **result of that execution** and decides what it says. The file is ALWAYS restored afterwards (try/finally, verified by SHA-256; a failed restore stops the run). Results go to
`runs/<yyyymmdd-HHMMSS>-<label>/`: `mutations.jsonl` (one record per mutation), `summary.json`, `baseline.json`, `logs/<id>.log` (the raw bytes of the output, kept whatever happens afterwards),
`reports/<id>.*.json` (vitest's own JSON report, or the parsed libtest report) and the spec.

What counts as caught (v2): a mutant is `caught` only when the execution's report shows **at least one assertion failure** in a test that also passed on the unmutated tree, and nothing about the run is
broken. Everything else is classified apart, never as caught:

    caught         an executed assertion failure, attributable to a test of the baseline; no suite failed to load, the number of tests is the baseline's
    SURVIVED       every test passed, exit 0, the baseline's number of tests
    mixed          assertion failures, but also a suite that did not load or a different number of tests: not an unqualified kill
    error_only     tests failed, but none by an assertion (an exception, a timeout): the mutant broke the code, no test judged it
    unattributed   an assertion failed, but not in a test named by the mutation's `expect`
    invalid        the run says nothing: a build, transform, import or setup error; a suite that did not load; no tests; no report; a non-zero exit with no failing test; a failing test the baseline never ran
    timeout        the command did not finish
    pattern_error  `old` is missing or ambiguous
    harness_error  this script failed while reading the result (the log is kept)

The baseline is run first on the UNMUTATED tree with the same command (and once for each different `command` override): it must exit 0, report tests, none failing, no suite failing to load. A run whose
default baseline is not clean is INVALID and nothing is mutated. A mutation whose own override command has an unclean baseline is `invalid`.

Report sources: `vitest` reads vitest's JSON report (`--reporter=json --outputFile.json=...` is added to the command); `cargo` parses libtest's stable text output (`running N tests`, `test x ... ok|FAILED`,
`test result:`, the panic message of each failing test) into the same normalised report, because libtest's JSON format needs a nightly compiler; `pytest` parses its short summary (the weakest of the three).

Spec fields: `cwd`, `command` (argv list), `env` (extra environment), `timeout` (seconds), `kind` (`vitest` | `cargo` | `pytest`)."""
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
OUTCOMES = ("caught", "SURVIVED", "mixed", "error_only", "unattributed", "invalid", "timeout", "pattern_error", "harness_error")
ASSERTION = re.compile(r"(?m)^\s*AssertionError\b|Snapshot .+ mismatched")
RUST_ASSERTION = re.compile(r"assertion `|assertion failed|assert_eq!|assert_ne!")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_report(source: str) -> dict:
    return {"source": source, "total": 0, "passed": 0, "failed": 0, "skipped": 0, "tests": {}, "files": {}, "assertion_failures": [], "error_failures": [], "load_errors": [], "build_errors": [], "problems": []}


# ---------------------------------------------------------------------------------------------------------------- parsers

def parse_vitest_json(path: Path) -> dict | None:
    """vitest's JSON report as the normalised report; None when it is missing or not a vitest report."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("testResults"), list) or "numTotalTests" not in data:
        return None
    rep = empty_report("vitest-json")
    for tr in data["testResults"]:
        suite = str(tr.get("name", "")).replace("\\", "/")
        suite = suite.split("/terminal/web/")[-1]
        asserts = tr.get("assertionResults") or []
        if tr.get("status") == "failed" and not asserts:
            rep["load_errors"].append({"suite": suite, "message": str(tr.get("message") or "")[:300]})
        rep["files"][suite] = len(asserts)
        for a in asserts:
            tid = f"{suite} :: {a.get('fullName') or a.get('title')}"
            status = a.get("status")
            rep["tests"][tid] = status
            if status == "failed":
                (rep["assertion_failures"] if ASSERTION.search("\n".join(a.get("failureMessages") or [])) else rep["error_failures"]).append(tid)
    rep["total"] = int(data.get("numTotalTests") or 0)
    rep["passed"] = int(data.get("numPassedTests") or 0)
    rep["failed"] = int(data.get("numFailedTests") or 0)
    rep["skipped"] = int(data.get("numPendingTests") or 0) + int(data.get("numTodoTests") or 0)
    if len(rep["tests"]) != rep["total"]:
        rep["problems"].append(f"the report lists {len(rep['tests'])} tests but counts {rep['total']}")
    if rep["passed"] + rep["failed"] + rep["skipped"] != rep["total"]:
        rep["problems"].append("the report's counts do not add up")
    return rep


def parse_vitest_text(text: str) -> dict:
    """vitest's default reporter, for logs that were saved before the JSON report was kept (reclassification only: the per-test list of passing tests is not in the text)."""
    rep = empty_report("vitest-text")
    for m in re.finditer(r"(?m)^ [✓❯×] (test/\S+) \((\d+) tests?(?: \| (\d+) failed)?\)", text):
        rep["files"][m.group(1)] = int(m.group(2))
    s = re.search(r"(?m)^\s+Tests\s+(.*)$", text)
    if s:
        line = s.group(1)
        failed = re.search(r"(\d+) failed", line)
        passed = re.search(r"(\d+) passed", line)
        total = re.search(r"\((\d+)\)\s*$", line)
        rep["failed"] = int(failed.group(1)) if failed else 0
        rep["passed"] = int(passed.group(1)) if passed else 0
        rep["total"] = int(total.group(1)) if total else 0
    blocks = re.split(r"(?m)^ FAIL +", text)[1:]
    for block in blocks:
        head, _, body = block.partition("\n")
        head = head.strip()
        if " > " not in head:
            rep["load_errors"].append({"suite": head, "message": body.strip().splitlines()[0][:300] if body.strip() else ""})
            continue
        (rep["assertion_failures"] if ASSERTION.search(body.lstrip("\n")[:400]) else rep["error_failures"]).append(head)
    for marker in ("Failed to resolve import", "Transform failed", "SyntaxError"):
        if marker in text and not rep["load_errors"] and rep["failed"] == 0:
            rep["build_errors"].append(marker)
    return rep


def parse_libtest(text: str) -> dict:
    """libtest's stable text output. `total` is the first test binary's `running N tests` (cargo stops at the first failing binary, so later ones are not comparable)."""
    rep = empty_report("libtest-text")
    # (`error: test failed, to rerun pass ...` ends every failing run and is not a build error)
    if "could not compile" in text or re.search(r"(?m)^error\[E\d+\]:", text) or re.search(r"(?m)^error: aborting due to", text):
        rep["build_errors"].append("could not compile")
    if "process didn't exit successfully" in text:
        rep["build_errors"].append("the test process crashed")
    runs = [int(n) for n in re.findall(r"(?m)^running (\d+) tests?$", text)]
    rep["total"] = runs[0] if runs else 0
    for m in re.finditer(r"(?m)^test (\S+) \.\.\. (ok|FAILED|ignored)\b", text):
        status = {"ok": "passed", "FAILED": "failed", "ignored": "skipped"}[m.group(2)]
        rep["tests"][m.group(1)] = status
    rep["passed"] = sum(1 for v in rep["tests"].values() if v == "passed")
    rep["failed"] = sum(1 for v in rep["tests"].values() if v == "failed")
    rep["skipped"] = sum(1 for v in rep["tests"].values() if v == "skipped")
    parts = re.split(r"(?m)^---- (\S+) stdout ----$", text)
    bodies = dict(zip(parts[1::2], parts[2::2]))
    for tid, status in rep["tests"].items():
        if status != "failed":
            continue
        body = bodies.get(tid, "")
        (rep["assertion_failures"] if "panicked at" in body and RUST_ASSERTION.search(body) else rep["error_failures"]).append(tid)
    if runs and not re.search(r"(?m)^test result:", text) and not rep["build_errors"]:
        rep["problems"].append("no `test result:` line")
    return rep


def parse_pytest(text: str) -> dict:
    rep = empty_report("pytest-text")
    for m in re.finditer(r"(?m)^FAILED (\S+)(?: - (.*))?$", text):
        rep["tests"][m.group(1)] = "failed"
        (rep["assertion_failures"] if (m.group(2) or "").lstrip().startswith(("AssertionError", "assert")) else rep["error_failures"]).append(m.group(1))
    for m in re.finditer(r"(?m)^ERROR (\S+)", text):
        rep["load_errors"].append({"suite": m.group(1), "message": ""})
    counts = {k: 0 for k in ("passed", "failed", "error", "skipped")}
    last = re.findall(r"(?m)^=+ (.*?) in [\d.]+s", text)
    if last:
        for n, k in re.findall(r"(\d+) (passed|failed|error|errors|skipped)", last[-1]):
            counts[k.rstrip("s") if k == "errors" else k] += int(n)
    rep["passed"], rep["failed"], rep["skipped"] = counts["passed"], counts["failed"], counts["skipped"]
    rep["total"] = counts["passed"] + counts["failed"] + counts["skipped"]
    if not last:
        rep["problems"].append("no pytest summary line")
    return rep


# ---------------------------------------------------------------------------------------------------------------- decisions

def baseline_problems(rep: dict | None, exit_code: int) -> list[str]:
    """Why a baseline run cannot be trusted (an empty list: it can)."""
    if rep is None:
        return ["no report"]
    out = []
    if exit_code != 0:
        out.append(f"exit code {exit_code}")
    if rep["build_errors"]:
        out.append("build errors: " + ", ".join(rep["build_errors"]))
    if rep["load_errors"]:
        out.append(f"{len(rep['load_errors'])} suites did not load")
    if rep["total"] == 0:
        out.append("no tests ran")
    if rep["failed"]:
        out.append(f"{rep['failed']} tests failed")
    out.extend(rep["problems"])
    return out


def decide(rep: dict | None, base: dict, exit_code: int, expect: list[str] | None = None) -> tuple[str, str, dict]:
    """The outcome of one mutated execution from its normalised report and the baseline's. Returns (outcome, detail, evidence)."""
    ev = {"report_source": rep["source"] if rep else None, "baseline_total": base["total"]}
    if rep is None:
        return "invalid", "no_report: the execution wrote no readable result", ev
    ev.update(total=rep["total"], failed=rep["failed"], assertion_failures=len(rep["assertion_failures"]), error_failures=len(rep["error_failures"]), load_errors=rep["load_errors"][:5], build_errors=rep["build_errors"])
    if rep["build_errors"]:
        return "invalid", "build_error: " + ", ".join(rep["build_errors"]), ev
    if rep["load_errors"]:
        if rep["assertion_failures"]:
            return "mixed", f"assertion failures beside {len(rep['load_errors'])} suites that did not load: not an unqualified kill", ev
        return "invalid", f"suite_load_error: {len(rep['load_errors'])} suites did not load", ev
    if rep["total"] == 0:
        return "invalid", "no_tests: the execution ran no test", ev
    base_files, files = base.get("files") or {}, rep.get("files") or {}
    drift = rep["total"] != base["total"] or any(f in base_files and files[f] != base_files[f] for f in files if files[f] or base_files.get(f))
    if drift:
        detail = f"test_count_drift: {rep['total']} tests, the baseline ran {base['total']}"
        return ("mixed", detail, ev) if rep["assertion_failures"] else ("invalid", detail, ev)
    if rep["problems"]:
        return "invalid", "inconsistent report: " + "; ".join(rep["problems"]), ev
    failing = rep["assertion_failures"] + rep["error_failures"]
    if failing:
        if exit_code == 0:
            return "invalid", "exit 0 although the report lists failing tests", ev
        known = {t for t, s in base["tests"].items() if s == "passed"}
        if known:
            stray = [t for t in failing if t not in known]
        else:  # a text report lists only the failing tests: the file must be one the baseline ran
            stray = [t for t in failing if t.split(" > ")[0] not in base_files]
        if stray:
            return "invalid", f"failing_test_not_in_baseline: {stray[0]}", ev
        if rep["assertion_failures"]:
            if expect and not any(e in t for t in rep["assertion_failures"] for e in expect):
                return "unattributed", "assertion failures, none in a test the mutation names (`expect`)", ev
            return "caught", f"{len(rep['assertion_failures'])} assertion failures" + (f" (and {len(rep['error_failures'])} tests failed with an error)" if rep["error_failures"] else ""), ev
        return "error_only", f"{len(rep['error_failures'])} tests failed with an error and none by an assertion", ev
    if exit_code != 0:
        return "invalid", f"unexplained_exit: exit code {exit_code} with no failing test", ev
    return "SURVIVED", "every test passed", ev


# ---------------------------------------------------------------------------------------------------------------- execution

def with_report(cmd: list[str], kind: str, report: Path) -> list[str]:
    if kind == "vitest":
        return [*cmd, "--reporter=json", f"--outputFile.json={report}"]
    return cmd


def run_command(cmd: list[str], spec: dict, log_path: Path) -> tuple[int, str]:
    """Run, keep the output's raw bytes in `log_path` before anything reads them, return (exit code, decoded text). A child that outlives its timeout is killed with its process tree."""
    env = {**os.environ, **spec.get("env", {})}
    timeout = spec.get("timeout", 900)
    proc = subprocess.Popen(cmd, cwd=spec["cwd"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            proc.kill()
        out, err = proc.communicate()
        code = -1
        out += f"\nTIMEOUT after {timeout}s\n".encode()
    raw = out + (b"\n--- stderr ---\n" + err if err else b"")
    log_path.write_bytes(raw)
    return code, raw.decode("utf-8", errors="replace")


def read_report(kind: str, report: Path, text: str) -> dict | None:
    if kind == "vitest":
        return parse_vitest_json(report)
    if kind == "cargo":
        rep = parse_libtest(text)
        report.write_text(json.dumps(rep, indent=1), encoding="utf-8")
        return rep
    rep = parse_pytest(text)
    report.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep


def command_key(spec: dict, m: dict) -> str:
    return json.dumps([list(m.get("command") or spec["command"]), spec["cwd"], spec.get("env", {})], sort_keys=True)


def resolve_python(cmd: list[str]) -> list[str]:
    cmd = list(cmd)
    if cmd[0] == "python":
        cmd[0] = sys.executable  # the interpreter running this script, not whichever `python` a child process would find first
    return cmd


def run_baseline(spec: dict, cmd: list[str], name: str, run: Path) -> dict:
    kind = spec.get("kind", "pytest")
    report = run / "reports" / f"{name}.json"
    code, text = run_command(with_report(resolve_python(cmd), kind, report), spec, run / "logs" / f"{name}.log")
    rep = read_report(kind, report, text)
    problems = baseline_problems(rep, code)
    return {"name": name, "command": cmd, "exit_code": code, "problems": problems, "clean": not problems, "report": rep}


def run_one(spec: dict, m: dict, run: Path, base: dict) -> dict:
    target = Path(m["file"])
    original = target.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")
    rec = {"id": m["id"], "file": str(target), "note": m.get("note", ""), "outcome": None, "detail": "", "failing": [], "evidence": {}, "seconds": 0.0, "exit_code": None}
    count = text.count(m["old"])
    if count != 1:
        rec.update(outcome="pattern_error", detail=f"the pattern occurs {count} times, not once")
        return rec
    kind = spec.get("kind", "pytest")
    report = run / "reports" / f"{m['id']}.json"
    cmd = with_report(resolve_python(m.get("command") or spec["command"]), kind, report)
    started = time.time()
    out = ""
    try:
        target.write_bytes(text.replace(m["old"], m["new"], 1).encode("utf-8"))
        rec["exit_code"], out = run_command(cmd, spec, run / "logs" / f"{m['id']}.log")
    finally:
        target.write_bytes(original)
        if sha(target) != before:
            raise SystemExit(f"RESTORE FAILED for {target}: stopping")
    rec["seconds"] = round(time.time() - started, 1)
    if rec["exit_code"] == -1:
        rec.update(outcome="timeout", detail="the command did not finish in time")
        return rec
    try:
        rep = read_report(kind, report, out)
        outcome, detail, evidence = decide(rep, base["report"], rec["exit_code"], m.get("expect"))
        rec.update(outcome=outcome, detail=detail, evidence=evidence)
        if rep:
            rec["failing"] = sorted(rep["assertion_failures"] + rep["error_failures"])[:12]
    except Exception as exc:  # the log is already on disk
        rec.update(outcome="harness_error", detail=f"{type(exc).__name__}: {exc}")
    return rec


def main(argv: list[str], runs: Path | None = None) -> int:
    spec_path, label = Path(argv[0]), argv[1]
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))  # (a spec written by Windows PowerShell 5.1 starts with a byte-order mark)
    run = (runs or RUNS) / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    (run / "logs").mkdir(parents=True, exist_ok=False)
    (run / "reports").mkdir()
    (run / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    # every distinct command is run once on the UNMUTATED tree first: a harness that fails for its own reasons must not be able to report "caught"
    baselines: dict[str, dict] = {}
    for i, m in enumerate([{}] + spec["mutations"]):
        key = command_key(spec, m)
        if key not in baselines:
            baselines[key] = run_baseline(spec, list(m.get("command") or spec["command"]), "baseline-unmutated" if i == 0 else f"baseline-{len(baselines)}-unmutated", run)
    summary_of = {k: {x: v[x] for x in ("name", "command", "exit_code", "problems", "clean")} | {"total": (v["report"] or {}).get("total")} for k, v in baselines.items()}
    (run / "baseline.json").write_text(json.dumps(list(summary_of.values()), indent=1), encoding="utf-8")
    default = baselines[command_key(spec, {})]
    if not default["clean"]:
        (run / "INVALID.txt").write_text("the baseline on the unmutated tree is not clean, so no mutation result from this run means anything:\n" + "\n".join(default["problems"]), encoding="utf-8")
        print(f"BASELINE NOT CLEAN ({'; '.join(default['problems'])}): the run is invalid; see {run / 'logs'}")
        return 2
    records = []
    with open(run / "mutations.jsonl", "w", encoding="utf-8") as f:
        for m in spec["mutations"]:
            base = baselines[command_key(spec, m)]
            if not base["clean"]:
                rec = {"id": m["id"], "file": m["file"], "note": m.get("note", ""), "outcome": "invalid", "detail": "the baseline of this mutation's own command is not clean: " + "; ".join(base["problems"]), "failing": [], "evidence": {}, "seconds": 0.0, "exit_code": None}
            else:
                rec = run_one(spec, m, run, base)
            records.append(rec)
            f.write(json.dumps(rec) + "\n")
            f.flush()
            print(f"{rec['outcome']:>13}  {rec['id']}  ({rec['seconds']}s) {rec['failing'][:1]}  {rec['detail'][:90]}")
    counts = {k: sum(1 for r in records if r["outcome"] == k) for k in OUTCOMES}
    (run / "summary.json").write_text(json.dumps({"label": label, "harness": "mutate.py v2", "total": len(records), "counts": counts, "files_restored": True}, indent=1), encoding="utf-8")
    print(run, {k: v for k, v in counts.items() if v})
    return 0 if counts["caught"] == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
