"""The forward log (design spec 6g): freeze the statement measures of every industrial company on a date, then grade them against prices a year or more later.

    python -m athena.forensic.statements.forward snapshot [--as-of 2026-11-06]
    python -m athena.forensic.statements.forward verify FILE
    python -m athena.forensic.statements.forward grade FILE --end 2027-11-06

The frozen file is the only record of what the measures said on that date: it holds each company's measures with their peer ranks, the revision of the code that made them, and a hash of the content. A snapshot is
never re-taken for a date (a second try would be a chance to improve it). Grading refuses to run before twelve months have passed, checks the hash, recomputes the measures with today's code and reports any
company whose elevated measures differ from the frozen ones (drift is shown, not hidden), then runs the same outcome code and the same primary reading as the cross-sectional test. Nothing here changes a weight."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.forensic.statements.annuals import connect
from athena.forensic.statements.detect import Universe
from athena.forensic.statements.metrics import MEASURES
from athena.fundamentals.history import SuccessionTable
from athena.fundamentals.store import DEFAULT_PATH as FUNDAMENTALS
from athena.warehouse.warehouse import DEFAULT_PATH as WAREHOUSE

DEFAULT_DIR = Path(".athena") / "forensic_forward"
MONTHS_BEFORE_GRADING_DAYS = 365


def _revision() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def digest(rows: Sequence[dict[str, Any]]) -> str:
    """A hash of the rows in a fixed order, so any later change to a frozen figure is found."""
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda r: r["symbol"]), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def frozen_rows(universe: Universe) -> list[dict[str, Any]]:
    """Per company: the measures computed, each with its value and the share of peers it is more concerning than, and which are elevated."""
    rows = []
    for symbol in sorted(universe.annuals):
        findings = universe.findings(symbol)
        rows.append({
            "symbol": symbol, "industry": universe.annuals[symbol].industry, "computed": sorted(universe.values.get(symbol, {})),
            "elevated": sorted(f.key for f in findings if f.elevated),
            "ranked": {f.key: {"value": round(f.value, 4), "worse_than": round(f.worse_than, 4), "peers": f.peers} for f in findings},
        })
    return rows


def snapshot(as_of: date, fundamentals: Path | str = FUNDAMENTALS, warehouse: Path | str = WAREHOUSE, root: Path = DEFAULT_DIR, universe: Universe | None = None, now: datetime | None = None) -> Path:
    """Write the frozen measures for `as_of`. Refuses to overwrite: one record per date."""
    target = root / f"{as_of.isoformat()}.json"
    if target.exists():
        raise AthenaError(f"a snapshot for {as_of} already exists at {target}; it is never re-taken")
    built = universe or Universe.build(connect(fundamentals), as_of, SuccessionTable(warehouse))
    rows = frozen_rows(built)
    record = {
        "as_of": as_of.isoformat(), "taken_at": (now or datetime.now(timezone.utc)).isoformat(), "code_revision": _revision(),
        "measures": [m.key for m in MEASURES], "companies": len(rows), "sha256": digest(rows), "rows": rows,
    }
    root.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return target


def load(path: Path | str) -> dict[str, Any]:
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if digest(record["rows"]) != record["sha256"]:
        raise AthenaError(f"{path} does not match its recorded hash: the frozen measures were changed")
    return record


def verify(path: Path | str) -> dict[str, Any]:
    record = load(path)
    return {"as_of": record["as_of"], "companies": record["companies"], "code_revision": record["code_revision"], "hash_ok": True}


def drift(record: dict[str, Any], universe: Universe) -> dict[str, Any]:
    """Companies whose elevated measures today's code gives differently from the frozen ones, and companies now present that were not frozen."""
    frozen = {r["symbol"]: set(r["elevated"]) for r in record["rows"]}
    now = {s: {f.key for f in universe.findings(s) if f.elevated} for s in universe.annuals}
    changed = sorted(s for s in frozen if s in now and frozen[s] != now[s])
    return {"frozen": len(frozen), "recomputed": len(now), "changed_elevated_set": len(changed), "examples": changed[:10], "missing_now": sorted(set(frozen) - set(now))[:10], "new_since": len(set(now) - set(frozen))}


def grade(path: Path | str, end: date, fundamentals: Path | str = FUNDAMENTALS, warehouse: Path | str = WAREHOUSE, today: date | None = None) -> dict[str, Any]:
    """The cross-sectional test, with the cash-flow composite, on the frozen date. Refused before twelve months have passed."""
    from athena.forensic.statements import backtest

    record = load(path)
    as_of = date.fromisoformat(record["as_of"])
    earliest = as_of + timedelta(days=MONTHS_BEFORE_GRADING_DAYS)
    if (today or date.today()) < earliest or end < earliest:
        raise AthenaError(f"a snapshot taken on {as_of} is graded no earlier than {earliest} (the end of the window and today must both be on or after it)")
    universe = Universe.build(connect(fundamentals), as_of, SuccessionTable(warehouse))
    result = backtest.run(as_of, end, fundamentals, warehouse)
    result["frozen_snapshot"] = {"file": str(path), "code_revision_then": record["code_revision"], "drift": drift(record, universe)}
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("--as-of", default=None, help="default: today")
    p.add_argument("--fundamentals", default=str(FUNDAMENTALS))
    p.add_argument("--warehouse", default=str(WAREHOUSE))
    p.add_argument("--dir", default=str(DEFAULT_DIR))
    v = sub.add_parser("verify")
    v.add_argument("file")
    g = sub.add_parser("grade")
    g.add_argument("file")
    g.add_argument("--end", required=True)
    g.add_argument("--fundamentals", default=str(FUNDAMENTALS))
    g.add_argument("--warehouse", default=str(WAREHOUSE))
    g.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            when = date.fromisoformat(args.as_of) if args.as_of else date.today()
            target = snapshot(when, args.fundamentals, args.warehouse, Path(args.dir))
            print(json.dumps(verify(target), indent=1))
            print(f"written: {target}")
        elif args.command == "verify":
            print(json.dumps(verify(args.file), indent=1))
        else:
            from athena.forensic.statements.backtest import render

            result = grade(args.file, date.fromisoformat(args.end), args.fundamentals, args.warehouse)
            out = Path(args.out) if args.out else Path(args.file).with_name(Path(args.file).stem + f"-graded-{args.end}.json")
            out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
            print(render(result))
            print(json.dumps(result["frozen_snapshot"], indent=1))
            print(f"written: {out}")
    except AthenaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
