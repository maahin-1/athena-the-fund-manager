"""Run pytest into its own uniquely named run directory and keep machine-readable evidence (WP07 T5 review corrections).

    python run_pytest.py <label> [pytest args...]        e.g.  python run_pytest.py py-red tests/test_snapshot_integration.py

Writes `runs/<yyyymmdd-HHMMSS>-<label>/` beside this script's parent: command.json, git-head.txt, git-status.txt, toolchain.json, collected-ids.txt, junit.xml, pytest.log and results.json (one record per
test: id, outcome, seconds, message). Nothing from an earlier run is overwritten: the directory name is unique and the script refuses to reuse one. The exit code is pytest's, so a red run is kept and marked,
never summarised as a pass. Synthetic data only; no environment file is read."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
RUNS = Path(__file__).resolve().parents[1] / "runs"


def sh(*cmd: str, cwd: Path = REPO) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False).stdout


def results_from_junit(xml: Path) -> list[dict]:
    out = []
    for case in ET.parse(xml).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome, message = ("skipped" if tag == "skipped" else tag + "ed" if tag == "error" else "failed"), (node.get("message") or "")[:300]
                break
        out.append({"id": f"{case.get('classname')}::{case.get('name')}", "outcome": outcome, "seconds": float(case.get("time") or 0), "message": message})
    return out


def main(argv: list[str]) -> int:
    label, pytest_args = argv[0], argv[1:]
    run = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    run.mkdir(parents=True, exist_ok=False)
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--junitxml={run / 'junit.xml'}", "-q", *pytest_args]
    (run / "command.json").write_text(json.dumps({"cwd": str(REPO), "argv": cmd}, indent=1), encoding="utf-8")
    (run / "git-head.txt").write_text(sh("git", "rev-parse", "HEAD"), encoding="utf-8")
    (run / "git-status.txt").write_text(sh("git", "status", "--short"), encoding="utf-8")
    versions = {"python": sys.version.split()[0], "pytest": sh(sys.executable, "-m", "pytest", "--version") or ""}
    (run / "toolchain.json").write_text(json.dumps(versions, indent=1), encoding="utf-8")
    collect = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", *pytest_args]
    ids = [line for line in subprocess.run(collect, cwd=REPO, capture_output=True, text=True, check=False).stdout.splitlines() if "::" in line]
    (run / "collected-ids.txt").write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
    started = time.time()
    with open(run / "pytest.log", "w", encoding="utf-8") as log:
        code = subprocess.run(cmd, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
    results = results_from_junit(run / "junit.xml") if (run / "junit.xml").exists() else []
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in ("passed", "failed", "errored", "skipped")}
    (run / "results.json").write_text(json.dumps({"label": label, "exit_code": code, "seconds": round(time.time() - started, 1), "collected": len(ids), "counts": counts, "tests": results}, indent=1), encoding="utf-8")
    print(f"{run}\nexit={code} collected={len(ids)} {counts}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
