"""Stopped-trading companies that have no statements in the store: the survivor bias of every test that starts at an earlier date.

    python -m athena.fundamentals.delisted [--since 2019-01-01] [--before 2026-10-01] [--list]      the count and the first few, or with --list the symbols as one comma-separated line

The statements store holds the companies listed today (plus those fetched by hand). A test that starts in 2023 would see only the survivors of 2023 to 2026, and the worst outcomes (a company that collapsed and was
delisted) would be missing, which biases it toward finding nothing. This lists, from the price warehouse, the main-board companies that stopped trading between two dates, traded long enough to matter, have no
statement lines, and are not the old symbol of a company that carries on (those join to their successor and are already covered). The line it prints goes to the ordinary ingest:

    python -m athena.fundamentals.ingest --universe "$(python -m athena.fundamentals.delisted --list)" --no-history --resume

Statements before late 2022 are limited to income lines; a company that stopped before about 2018 has no XBRL results to fetch, so the default starts at 2019. It does not fetch anything itself."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

MIN_BARS = 200
MAIN_BOARD_SERIES = ("EQ", "BE", "BZ")  # SME series (SM, ST) have no XBRL results of the kind the ingest reads
DEFAULT_SINCE = date(2019, 1, 1)
DEFAULT_BEFORE = date(2026, 10, 1)


def candidates(warehouse: Path | str, fundamentals: Path | str, since: date = DEFAULT_SINCE, before: date = DEFAULT_BEFORE, min_bars: int = MIN_BARS) -> list[dict[str, Any]]:
    """The symbols whose last bar is on or after `since` and before `before`, with more than `min_bars` bars, last traded in a main-board series, with no statement lines, not the old symbol of a rename, and not
    known to be an ETF. Newest stop first."""
    wh = duckdb.connect(str(warehouse), read_only=True)
    fu = duckdb.connect(str(fundamentals), read_only=True)
    try:
        have = {r[0] for r in fu.execute("SELECT DISTINCT symbol FROM statement_lines").fetchall()}
        rows = wh.execute(
            "SELECT symbol, count(*), min(day), max(day), arg_max(series, day) FROM bars GROUP BY symbol HAVING max(day) >= ? AND max(day) < ? AND count(*) > ?", [since, before, min_bars]
        ).fetchall()
        try:
            olds = {r[0] for r in wh.execute("SELECT old FROM symbol_renames").fetchall()}
        except duckdb.CatalogException:
            olds = set()
        etfs = {r[0] for r in wh.execute("SELECT symbol FROM universe WHERE kind IN ('etf', 'sme')").fetchall()}
    finally:
        wh.close()
        fu.close()
    out = [
        {"symbol": s, "bars": n, "first": first, "last": last, "last_series": series}
        for s, n, first, last, series in rows
        if s not in have and s not in olds and s not in etfs and series in MAIN_BOARD_SERIES
    ]
    return sorted(out, key=lambda r: (r["last"], r["symbol"]), reverse=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default=DEFAULT_SINCE.isoformat())
    parser.add_argument("--before", default=DEFAULT_BEFORE.isoformat())
    parser.add_argument("--list", action="store_true", help="print the symbols as one comma-separated line, for the ingest")
    parser.add_argument("--warehouse", default=str(Path(".athena") / "warehouse.duckdb"))
    parser.add_argument("--fundamentals", default=str(Path(".athena") / "fundamentals.duckdb"))
    args = parser.parse_args(argv)
    found = candidates(args.warehouse, args.fundamentals, date.fromisoformat(args.since), date.fromisoformat(args.before))
    if args.list:
        print(",".join(r["symbol"] for r in found))
        return 0
    by_year: dict[int, int] = {}
    for r in found:
        by_year[r["last"].year] = by_year.get(r["last"].year, 0) + 1
    print(f"{len(found)} main-board symbols stopped trading between {args.since} and {args.before} with more than {MIN_BARS} bars and no statements; by year of the last bar: {dict(sorted(by_year.items()))}")
    for r in found[:10]:
        print(f"  {r['symbol']:>14}  {r['first']} to {r['last']}  {r['bars']} bars  last series {r['last_series']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
