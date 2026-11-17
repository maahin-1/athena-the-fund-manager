"""Turn a `cargo test` log into machine-readable results: one record per test (name, outcome) and the summary lines.

    python cargo_results.py <rust-test.log> <out.json>

libtest prints `test <name> ... ok|FAILED|ignored`. This reads those lines (and `test result:` summaries) and nothing else; it opens no store and no environment file."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LINE = re.compile(r"^test (?P<name>\S+) \.\.\. (?P<outcome>ok|FAILED|ignored)(?:, .*)?$")
SUMMARY = re.compile(r"^test result: (?P<verdict>\w+)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; (?P<ignored>\d+) ignored")


def parse(text: str) -> dict:
    tests, summaries = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if m := LINE.match(line):
            tests.append({"id": m["name"], "outcome": {"ok": "passed", "FAILED": "failed", "ignored": "ignored"}[m["outcome"]]})
        elif m := SUMMARY.match(line):
            summaries.append({k: (int(v) if v.isdigit() else v) for k, v in m.groupdict().items()})
    counts = {k: sum(1 for t in tests if t["outcome"] == k) for k in ("passed", "failed", "ignored")}
    return {"counts": counts, "summaries": summaries, "tests": sorted(tests, key=lambda t: t["id"])}


if __name__ == "__main__":
    log, out = Path(sys.argv[1]), Path(sys.argv[2])
    result = parse(log.read_text(encoding="utf-8", errors="replace"))
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(result["counts"], result["summaries"])
