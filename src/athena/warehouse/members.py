"""Which index each stock belongs to, and the exchange's industry for it, from the exchange's own constituent files.

    python -m athena.warehouse.members            # refresh `index_members`
    python -m athena.warehouse.members --status

The exchange publishes a CSV of constituents for most of its equity indices at `content/indices/ind_<name without spaces or punctuation>list.csv`, with
an `Industry` column. The indices the warehouse already holds bars for are the list of names to try (tested on 2026-10-18: 38 of 179 have a file, every
broad, sector and thematic equity index among them; the rest are debt, futures and strategy indices, which have none under that name). A name with no
file is skipped and counted. No index and no stock is named here. Peers and screens read the table."""
from __future__ import annotations

import argparse
import csv
import io
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from athena.contracts import AthenaError
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

BASE = "https://nsearchives.nseindia.com/content/indices/"


@dataclass(frozen=True)
class Member:
    index_name: str  # upper case, as the page writes index names
    symbol: str
    industry: str


def candidate_file(index_name: str) -> str:
    """The file name the exchange gives an index's constituent list."""
    return "ind_" + re.sub(r"[^a-z0-9]", "", index_name.lower()) + "list.csv"


def parse_members(index_name: str, text: str) -> list[Member]:
    """The (symbol, industry) rows of a constituent file. Rows with no symbol are skipped; text that is not a constituent file gives nothing."""
    reader = csv.DictReader(io.StringIO(text))
    fields = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}
    if "symbol" not in fields:
        return []
    out = []
    for row in reader:
        symbol = (row.get(fields["symbol"]) or "").strip().upper()
        if symbol:
            out.append(Member(index_name.upper(), symbol, (row.get(fields.get("industry", ""), "") or "").strip()))
    return out


def refresh_members(warehouse: Warehouse, get: Callable[[str], bytes | None], now: datetime) -> dict[str, int]:
    """Fetch each index's constituent file and replace the table with what was read. `get` returns the file's bytes, or None when there is none.
    A file that fails is counted and the others go on; if no file answered at all, the table is kept as it was and the error says so."""
    files: dict[str, str] = {}
    for name in warehouse.index_names():  # one file for an index written in two cases
        files.setdefault(candidate_file(name), name.upper())
    rows: list[Member] = []
    found = skipped = failed = 0
    for file, name in files.items():
        try:
            content = get(BASE + file)
        except AthenaError:
            failed += 1
            continue
        members = parse_members(name, content.decode("utf-8", errors="replace")) if content else []
        if members:
            rows.extend(members)
            found += 1
        else:
            skipped += 1
    if not rows:
        raise AthenaError("no constituent file answered; the index membership table is as it was")
    warehouse.replace_members([(m.index_name, m.symbol, m.industry) for m in rows], now)
    return {"indices": len(files), "found": found, "skipped": skipped, "failed": failed, "members": len(rows)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.members", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--status", action="store_true", help="only report what the table holds")
    args = parser.parse_args(argv)
    from athena.clock import utc_now
    from athena.pit.bhavcopy import ArchiveClient

    with Warehouse(args.db, wait_seconds=300) as warehouse:
        if not args.status:
            try:
                print(refresh_members(warehouse, ArchiveClient().get, utc_now()))
            except AthenaError as exc:
                print(f"not refreshed: {exc}")
                return 1
        status: dict[str, Any] = warehouse.members_status()
        print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
