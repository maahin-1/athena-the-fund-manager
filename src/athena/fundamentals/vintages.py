"""Seed the statement-versions table of a fundamentals store, safely.

    python -m athena.fundamentals.vintages --store COPY.duckdb                       dry run: counts only, the file is opened read-only and not changed
    python -m athena.fundamentals.vintages --store SRC.duckdb --copy-to COPY.duckdb --apply
                                                                                      copy the store (never over an existing file), then migrate the COPY
    python -m athena.fundamentals.vintages --store COPY.duckdb --apply --in-place     migrate this file (a copy you made yourself)
    python -m athena.fundamentals.vintages --store COPY.duckdb --rollback ID --in-place   remove exactly what migration ID added

What it does. `statement_lines` is the latest-value table the current pages read; a newer filing replaced an older one's rows there, so the originals are gone from it. The migration adds the
append-only `statement_versions` table (see `FundamentalsStore`) and fills it, from three honest sources and nothing else:

  * rows now in `statement_lines`, copied as `seeded_from_latest`: they are whatever filing last supplied the value, NOT known to be the original, and carry no ingest time or hash;
  * filings recorded `ok` whose RAW copy is still kept (the ingest's cache) and that have no version yet, re-read with the current parser as `rebuilt_from_raw`, with the raw file's hash;
  * nothing else. A filing with no raw copy and no surviving row is reported `originals unavailable` and stays missing. Today's restatement is never relabelled as the historical original.

It never writes to `statement_lines`, so the current pages read exactly what they read before. It is idempotent (a second run adds nothing), runs as one transaction (a failure leaves nothing),
and each run is recorded in `migrations` so `--rollback ID` removes exactly its rows. This tool will not operate on the default store (`.athena/fundamentals.duckdb`): migrating the real
store needs the owner's separate authorisation, a backup first, and a validated copy swapped in."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import AthenaError
from athena.fundamentals.ingest import raw_path
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import DEFAULT_PATH, REBUILT, SEEDED, VERSION_COLUMNS, VERSIONS_SCHEMA, FundamentalsStore, parser_version, statement_rows
from athena.fundamentals.store import now as utc_now
from athena.pit.nse_results import CACHE_DIR

UNKNOWN_READING = "unknown (before versions were kept)"
KEY_COLUMNS = ("symbol", "basis", "statement", "period_start", "period_end", "key")


@dataclass
class Report:
    """What a migration did, or (for a dry run) would do."""

    lines_rows: int = 0
    already_versioned: int = 0
    to_seed: int = 0
    rebuilt: int = 0
    originals_unavailable: list[dict[str, Any]] = field(default_factory=list)
    unreadable_raw: list[str] = field(default_factory=list)
    applied: bool = False
    migration_id: str = ""

    @property
    def seeded(self) -> int:
        return self.to_seed


def _tables(con: Any) -> set[str]:
    return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def _compute(con: Any, cache_dir: Path) -> tuple[Report, list[tuple[Any, ...]]]:
    """What the migration would write, from the file as it is: the report and the version rows. Nothing is written."""
    tables = _tables(con)
    report = Report()
    have: set[tuple[Any, ...]] = set()
    versioned_urls: set[str] = set()
    if "statement_versions" in tables:
        for row in con.execute("SELECT symbol, basis, statement, period_start, period_end, key, filing_url FROM statement_versions").fetchall():
            have.add(tuple(row))
            versioned_urls.add(row[6])
    lines = con.execute("SELECT symbol, basis, statement, period_start, period_end, key, value, filing_url, filed, source FROM statement_lines").fetchall() if "statement_lines" in tables else []
    report.lines_rows = len(lines)
    report.already_versioned = sum(1 for r in lines if (*r[:6], r[7]) in have)
    filings = con.execute("SELECT url, symbol, basis, period_end, annual, filed, status FROM filings WHERE status IN ('ok', 'gone') AND basis <> 'shp' ORDER BY filed, url").fetchall() if "filings" in tables else []
    out: list[tuple[Any, ...]] = []
    reading = parser_version()
    for url, symbol, basis, period_end, _annual, filed, status in filings:
        if url in versioned_urls:
            continue
        path = raw_path(cache_dir, url)
        why = "the exchange no longer serves this filing and no raw copy of it is kept" if status == "gone" else "no raw copy of this filing is kept, and none of its rows survive in the latest table"
        if path.exists() and status == "ok":
            text = path.read_text(encoding="utf-8")
            try:
                st = parse_rows(text)
            except ValueError as exc:
                report.unreadable_raw.append(f"{url}: {exc}")
                why = f"its raw copy cannot be read ({exc})"
            else:
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                for row in statement_rows(st):
                    if (*row[:6], url) not in have:
                        have.add((*row[:6], url))
                        out.append((*row, url, filed, None, "nse-xbrl", digest, reading, st.layout, REBUILT, None))
                        report.rebuilt += 1
                versioned_urls.add(url)
                continue
        if not any(r[7] == url for r in lines):  # no row of it survives either: its version is missing, and said to be
            report.originals_unavailable.append({"url": url, "symbol": symbol, "basis": basis, "period_end": period_end, "filed": filed, "reason": why})
    for symbol, basis, statement, start, end, key, value, url, filed, source in lines:
        if (symbol, basis, statement, start, end, key, url) not in have:
            out.append((symbol, basis, statement, start, end, key, value, url, filed, None, source, None, UNKNOWN_READING, None, SEEDED, None))
            have.add((symbol, basis, statement, start, end, key, url))
            report.to_seed += 1
    return report, out


def plan(path: str | Path, cache_dir: Path = CACHE_DIR) -> Report:
    """A dry run: the counts the migration would produce, from a read-only look at the file. Nothing is created or changed."""
    if not Path(path).exists():
        raise AthenaError(f"no fundamentals store at {path}")
    con = duckdb.connect(str(path), read_only=True)
    try:
        _check_schema(con)
        report, _ = _compute(con, cache_dir)
    finally:
        con.close()
    return report


def _check_schema(con: Any) -> None:
    if "schema_meta" in _tables(con):
        held = con.execute("SELECT value FROM schema_meta WHERE name = 'statement_versions'").fetchone()
        if held is not None and int(held[0]) > VERSIONS_SCHEMA:
            raise AthenaError(f"the store has statement_versions schema {held[0]}, newer than this tool understands ({VERSIONS_SCHEMA})")


def apply(path: str | Path, cache_dir: Path = CACHE_DIR, now: datetime | None = None) -> Report:
    """Migrate the file: create the versions tables, write the seeded and rebuilt rows in one transaction and record the migration. Idempotent: a second run finds nothing to add."""
    if not Path(path).exists():
        raise AthenaError(f"no fundamentals store at {path}")
    with FundamentalsStore(path) as store:  # opening for writing creates the additive tables and refuses a newer schema
        report, rows = _compute(store._con, cache_dir)
        if not rows:
            report.applied = True
            return report
        stamp = now or utc_now()
        migration_id = f"vintages-{stamp:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        counts = {"seeded": report.to_seed, "rebuilt": report.rebuilt, "parser_version": parser_version(), "originals_unavailable": len(report.originals_unavailable)}
        rows = [(*r[:-1], migration_id) for r in rows]
        with store._atomic():
            store._con.executemany(f"INSERT INTO statement_versions VALUES ({', '.join('?' for _ in range(len(VERSION_COLUMNS) + 1))})", rows)
            store._con.execute("INSERT INTO migrations VALUES (?, ?, ?, ?)", [migration_id, stamp, "statement_versions", json.dumps(counts)])
        report.applied, report.migration_id = True, migration_id
    return report


def rollback(path: str | Path, migration_id: str) -> int:
    """Remove exactly the rows migration `migration_id` added (and its record). Versions written by ingests after it, and by other migrations, stay. The latest-value table was never touched."""
    with FundamentalsStore(path) as store:
        if store._con.execute("SELECT count(*) FROM migrations WHERE migration_id = ?", [migration_id]).fetchone()[0] == 0:
            raise AthenaError(f"no migration {migration_id} is recorded in this store")
        count = store._con.execute("SELECT count(*) FROM statement_versions WHERE migration_id = ?", [migration_id]).fetchone()[0]
        with store._atomic():
            store._con.execute("DELETE FROM statement_versions WHERE migration_id = ?", [migration_id])
            store._con.execute("DELETE FROM migrations WHERE migration_id = ?", [migration_id])
    return int(count)


def copy_store(source: str | Path, dest: str | Path) -> Path:
    """A byte copy of the store (and its write-ahead log if there is one). Never over an existing file."""
    source, dest = Path(source), Path(dest)
    if not source.exists():
        raise AthenaError(f"no fundamentals store at {source}")
    if dest.exists():
        raise AthenaError(f"{dest} already exists: a copy is never written over a file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    wal = source.with_name(source.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, dest.with_name(dest.name + ".wal"))
    return dest


def _is_default_store(path: str | Path) -> bool:
    try:
        return Path(path).resolve() == DEFAULT_PATH.resolve()
    except OSError:
        return False


def _describe(report: Report, dry: bool) -> list[str]:
    lines = [
        f"{'dry run: nothing was changed' if dry else 'applied'}" + (f" (migration {report.migration_id})" if report.migration_id else ""),
        f"  latest-value rows read: {report.lines_rows} ({report.already_versioned} already have a version)",
        f"  rows to seed from the latest table: {report.to_seed}  (labelled seeded_from_latest: not known to be the original)",
        f"  rows rebuilt from kept raw filings: {report.rebuilt}  (labelled rebuilt_from_raw, with the raw file's hash)",
        f"  originals unavailable: {len(report.originals_unavailable)}  (no raw copy and no surviving row: they stay missing)",
    ]
    lines += [f"    {u['url']}  {u['symbol']} {u['basis']} period {u['period_end']} filed {u['filed']}: {u['reason']}" for u in report.originals_unavailable[:50]]
    if len(report.originals_unavailable) > 50:
        lines.append(f"    ... and {len(report.originals_unavailable) - 50} more")
    lines += [f"  unreadable raw copy: {u}" for u in report.unreadable_raw[:20]]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="a fundamentals store you copied; the default store is refused")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR), help="where the raw filings are kept")
    parser.add_argument("--apply", action="store_true", help="write the versions (the default is a dry run)")
    parser.add_argument("--copy-to", default=None, help="copy --store here first and work on the copy")
    parser.add_argument("--in-place", action="store_true", help="work on --store itself (it must be a copy you made)")
    parser.add_argument("--rollback", default=None, metavar="MIGRATION_ID", help="remove what that migration added")
    args = parser.parse_args(argv)
    cache = Path(args.cache_dir)
    try:
        for candidate in (args.store, args.copy_to):
            if candidate is not None and _is_default_store(candidate):
                print(f"refused: {candidate} is the default store. Migrating the real store needs your separate authorisation; work on a copy.", file=sys.stderr)
                return 2
        target = args.store
        if args.copy_to:
            target = str(copy_store(args.store, args.copy_to))
            print(f"copied {args.store} to {target}")
        if args.rollback:
            if not (args.in_place or args.copy_to):
                print("refused: --rollback changes the file; say --in-place to confirm --store is a copy", file=sys.stderr)
                return 2
            print(f"rolled back {rollback(target, args.rollback)} rows of {args.rollback}")
            return 0
        if args.apply:
            if not (args.in_place or args.copy_to):
                print("refused: --apply changes the file; use --copy-to to work on a copy, or --in-place to say --store already is one", file=sys.stderr)
                return 2
            report = apply(target, cache)
            print("\n".join(_describe(report, dry=False)))
            if report.migration_id:
                print(f"to undo: python -m athena.fundamentals.vintages --store {target} --rollback {report.migration_id} --in-place   (or discard the copy)")
            return 0
        print("\n".join(_describe(plan(target, cache), dry=True)))
        return 0
    except AthenaError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
