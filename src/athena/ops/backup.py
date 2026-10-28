"""Copy the data stores somewhere else, as Parquet, so a failed disk or a bad migration does not take the only copy.

    python -m athena.ops.backup --to E:\\athena-backups [--keep 2] [--only warehouse,fundamentals]

Each store (`.athena/warehouse.duckdb`, `fundamentals.duckdb`, `derivs.duckdb`, `reference.duckdb`, `valuation_ledger.duckdb`) is opened read-only and written with DuckDB's
`EXPORT DATABASE ... (FORMAT PARQUET, COMPRESSION ZSTD)` into `<to>/<store>/<UTC timestamp>/`. Only the newest `--keep` exports of each store stay. A store a long job is writing is skipped and
said so (nothing is waited for or forced). The destination should be another drive; a destination on the same drive as `.athena` is refused unless `--allow-same-drive` is given, because
a copy beside the original protects against a bad migration but not against the disk. Started by hand; nothing schedules it. It never deletes anything but its own older exports."""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import duckdb

STORES = {
    "warehouse": Path(".athena") / "warehouse.duckdb",
    "fundamentals": Path(".athena") / "fundamentals.duckdb",
    "derivs": Path(".athena") / "derivs.duckdb",
    "reference": Path(".athena") / "reference.duckdb",
    "valuation_ledger": Path(".athena") / "valuation_ledger.duckdb",
}
STAMP = "%Y%m%dT%H%M%SZ"


def same_drive(a: Path, b: Path) -> bool:
    return Path(a).resolve().drive.lower() == Path(b).resolve().drive.lower()


def export_one(name: str, source: Path, destination: Path, keep: int, now: datetime | None = None) -> tuple[str, str]:
    """Export one store; returns `(state, text)` where state is `ok`, `skipped` or `failed`."""
    if not Path(source).exists():
        return "skipped", f"{name}: no file at {source}"
    stamp = (now or datetime.now(timezone.utc)).strftime(STAMP)
    target = Path(destination) / name / stamp
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(source), read_only=True)
    except (duckdb.IOException, duckdb.ConnectionException):
        return "skipped", f"{name}: busy (a job is writing it); not exported"
    try:
        started = time.time()
        con.execute(f"EXPORT DATABASE '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(target, ignore_errors=True)  # a half export is not a backup
        return "failed", f"{name}: {type(exc).__name__}: {str(exc)[:160]}"
    finally:
        con.close()
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    older = sorted(p for p in target.parent.iterdir() if p.is_dir() and p.name != stamp)
    for old in older[: max(0, len(older) + 1 - keep)]:
        shutil.rmtree(old, ignore_errors=True)  # only this tool's own older exports
    return "ok", f"{name}: exported {size / 1e6:,.0f} MB in {time.time() - started:.0f}s to {target}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", required=True, help="the folder to export into (another drive)")
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument("--only", default="", help="comma-separated store names")
    parser.add_argument("--allow-same-drive", action="store_true")
    args = parser.parse_args(argv)
    destination = Path(args.to)
    if same_drive(destination, Path(".athena")) and not args.allow_same_drive:
        print(f"{destination} is on the same drive as .athena: that protects against a bad migration, not a failed disk. Use another drive, or --allow-same-drive.", file=sys.stderr)
        return 2
    chosen = [n for n in args.only.split(",") if n] or list(STORES)
    unknown = [n for n in chosen if n not in STORES]
    if unknown:
        print("unknown store(s): " + ", ".join(unknown), file=sys.stderr)
        return 2
    states = []
    for name in chosen:
        state, text = export_one(name, STORES[name], destination, max(1, args.keep))
        print(f"{state.upper():<8}{text}")
        states.append(state)
    return 0 if all(s == "ok" for s in states) else 1 if "failed" not in states else 2


if __name__ == "__main__":
    raise SystemExit(main())
