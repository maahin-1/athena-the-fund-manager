"""Reclassify the SAVED mutation runs with the v2 rules, from their saved logs (T3-C2: "reclassify from saved reports/logs where possible and rerun only ambiguous cases").

    python reclassify.py <label> <run-dir-name> [<run-dir-name> ...]

For every mutation of a saved run the log is read with the text parsers of mutate.py and decided against the same run's saved UNMUTATED baseline log (`logs/baseline-unmutated.log`, kept by the runs made since
the baseline check was added). Nothing is executed and no source file is touched. Output: `runs/<yyyymmdd-HHMMSS>-<label>/reclassification.json` and `.md`: per mutation the old outcome, the new one, why, and
whether it needs a rerun.

A saved log is `confirmed` only when the text proves the v2 conditions: an assertion failure after a `FAIL file > test` line, no suite that failed to load, per-file test counts equal to the baseline's, and a
failing test in a file the baseline ran. A run with no saved baseline log (the first Rust runs) cannot show the counts or the attribution: its catches are `unconfirmed (no baseline log)` and are rerun."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import mutate

RUNS = Path(__file__).resolve().parents[1] / "runs"


def reclassify_run(run: Path) -> dict:
    records = [json.loads(line) for line in (run / "mutations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    spec = json.loads((run / "spec.json").read_text(encoding="utf-8"))
    kind = spec.get("kind", "pytest")
    parse = {"vitest": mutate.parse_vitest_text, "cargo": mutate.parse_libtest, "pytest": mutate.parse_pytest}[kind]
    base_log = run / "logs" / "baseline-unmutated.log"
    base = parse(base_log.read_text(encoding="utf-8", errors="replace")) if base_log.exists() else None
    base_problems = mutate.baseline_problems(base, 0) if base else ["no baseline log was saved"]
    out = {"run": run.name, "kind": kind, "baseline_saved": base is not None, "baseline_problems": base_problems, "mutations": []}
    for r in records:
        item = {"id": r["id"], "old_outcome": r["outcome"], "new_outcome": None, "detail": "", "status": None}
        log = run / "logs" / f"{r['id']}.log"
        if r["outcome"] in ("pattern_error",):
            item.update(new_outcome="pattern_error", status="confirmed", detail=r.get("detail", ""))
        elif r["outcome"] == "invalid":
            item.update(new_outcome="invalid", status="confirmed", detail="was already invalid")
        elif not log.exists() or not log.read_text(encoding="utf-8", errors="replace").strip():
            item.update(new_outcome="invalid", status="rerun", detail="the saved log is empty or missing: no evidence")
        elif base is None or base_problems:
            rep = parse(log.read_text(encoding="utf-8", errors="replace"))
            guess = mutate.decide(rep, {**rep, "total": rep["total"], "files": {}, "tests": {}}, r.get("exit_code") or 0)
            item.update(new_outcome=guess[0], status="unconfirmed (no usable baseline log): rerun", detail=guess[1])
        else:
            rep = parse(log.read_text(encoding="utf-8", errors="replace"))
            outcome, detail, _ = mutate.decide(rep, base, r.get("exit_code") if r.get("exit_code") is not None else 1)
            confirmed = outcome in ("caught", "SURVIVED")
            item.update(new_outcome=outcome, detail=detail, status="confirmed" if confirmed else "rerun")
        out["mutations"].append(item)
    return out


def main(argv: list[str]) -> int:
    label, names = argv[0], argv[1:]
    dest = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    dest.mkdir(parents=True, exist_ok=False)
    results = [reclassify_run(RUNS / n) for n in names]
    (dest / "reclassification.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = ["# Reclassification of saved mutation runs (mutate.py v2 rules, from the saved logs)", ""]
    for res in results:
        counts: dict[str, int] = {}
        for m in res["mutations"]:
            key = f"{m['new_outcome']} / {m['status'].split(' ')[0]}"
            counts[key] = counts.get(key, 0) + 1
        lines += [f"## {res['run']} ({res['kind']}; saved baseline: {res['baseline_saved']}{'' if not res['baseline_problems'] else '; problems: ' + '; '.join(res['baseline_problems'])})", "", "| new outcome / status | count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(counts.items())]
        lines += ["", "| mutation | old | new | status | detail |", "|---|---|---|---|---|"]
        lines += [f"| {m['id']} | {m['old_outcome']} | {m['new_outcome']} | {m['status']} | {m['detail']} |" for m in res["mutations"]]
        lines.append("")
    (dest / "reclassification.md").write_text("\n".join(lines), encoding="utf-8")
    print(dest)
    for res in results:
        print(res["run"], {f"{m['new_outcome']}/{m['status'].split(' ')[0]}": 1 for m in res["mutations"]}.keys())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
