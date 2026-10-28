"""Fetch each company's shareholding patterns from NSE and keep the share of every holder group, quarter by quarter.

    python -m athena.fundamentals.holdings --universe nifty50 --resume
    python -m athena.fundamentals.holdings --universe TCS,SBIN --quarters 12

The listing (`corporate-share-holdings-master`) gives the latest 20 quarters with a link to each quarter's XBRL; the files are 0.3 to 1.3 MB, so they
are read and parsed in memory and only the parsed figures are kept. Throttled and resumable like the statement ingest (same client, same back-off,
same recorded block), started by hand, never scheduled. A company's progress is recorded under `SYMBOL#shp` in the ingest state."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from athena.contracts import AthenaError
from athena.fundamentals.history import PREDECESSORS
from athena.fundamentals.ingest import Blocked, Report, with_predecessors
from athena.fundamentals.integrated import FundamentalsResults, is_block
from athena.fundamentals.runner import run_each
from athena.fundamentals.shareholding import parse_shareholding
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore
from athena.fundamentals.universe import Member, universe

LISTING = "https://www.nseindia.com/api/corporate-share-holdings-master"
ARCHIVE = "https://nsearchives.nseindia.com"
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class Ref:
    period_end: datetime
    filed: datetime
    url: str


def _stamp(text: str, formats: tuple[str, ...]) -> datetime | None:
    for form in formats:
        try:
            return datetime.strptime(text.strip().title(), form).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def refs_from(rows: Sequence[dict[str, Any]]) -> list[Ref]:
    """The usable listing rows, newest first: those with a period, a broadcast time and an XBRL link. A revision of a period replaces its original."""
    best: dict[Any, Ref] = {}
    for row in rows:
        end = _stamp(str(row.get("date", "")), ("%d-%b-%Y",))
        filed = _stamp(str(row.get("broadcastDate", "")), ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M")) or _stamp(str(row.get("submissionDate", "")), ("%d-%b-%Y",))
        link = str(row.get("xbrl") or "")
        if end is None or filed is None or not link.endswith(".xml"):
            continue
        url = link if link.startswith("http") else ARCHIVE + link
        ref = Ref(end, filed, url)
        if end.date() not in best or filed > best[end.date()].filed:
            best[end.date()] = ref
    return sorted(best.values(), key=lambda r: r.period_end, reverse=True)


def listing(results: Any, symbol: str) -> list[Ref]:
    try:
        body = results._get(LISTING, {"index": "equities", "symbol": symbol}).json()
    except Exception as exc:
        raise AthenaError(f"NSE shareholding list unavailable for {symbol}: {exc}") from exc
    if not isinstance(body, list):
        raise AthenaError(f"NSE shareholding list for {symbol} has an unexpected shape")
    return refs_from(body)


def ingest_symbol(member: Member, results: Any, store_path: str | Path, *, quarters: int = 12, refresh: bool = False) -> Report:
    """Fetch and store one company's latest `quarters` patterns. Raises `Blocked` when the exchange refuses."""
    report = Report(member.symbol)
    try:
        refs = listing(results, member.symbol)[:quarters]
    except AthenaError as exc:
        if is_block(exc):
            raise Blocked(str(exc)) from exc
        report.failed += 1
        report.notes.append(str(exc))
        return report
    for ref in refs:
        with FundamentalsStore(store_path) as store:
            if store.filing_status(ref.url) == "ok" and not refresh:
                report.skipped += 1
                continue
        try:
            holding = parse_shareholding(results._get(ref.url).text)
        except AthenaError as exc:
            if is_block(exc):
                raise Blocked(str(exc)) from exc
            report.failed += 1
            report.notes.append(f"{ref.period_end:%Y-%m-%d}: {exc}")
            continue
        except ValueError as exc:
            with FundamentalsStore(store_path) as store:
                store.record_filing(ref.url, member.symbol, "shp", ref.period_end.date(), False, ref.filed, "failed", str(exc)[:200])
            report.failed += 1
            report.notes.append(f"{ref.period_end:%Y-%m-%d}: {exc}")
            continue
        with FundamentalsStore(store_path) as store:
            store.save_holding(member.symbol, holding, ref.url, ref.filed)
            store.record_filing(ref.url, member.symbol, "shp", holding.period_end, False, ref.filed, "ok")
        report.fetched += 1
    return report


def run(
    members: Sequence[Member], results: Any, store_path: str | Path, *, resume: bool, max_age_days: int, limit: int | None, quarters: int,
    refresh: bool = False, out: TextIO = sys.stdout, job: str | None = None,
) -> int:
    """Ingest each member in turn (see `athena.fundamentals.runner`). Returns 0 when done, 3 when the exchange blocked it."""
    return run_each(
        members, lambda m: ingest_symbol(m, results, store_path, quarters=quarters, refresh=refresh), store_path, key=lambda m: f"{m.symbol}#shp",
        resume=resume, max_age_days=max_age_days, limit=limit, refresh=refresh, out=out, job=job,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50", help="nifty50, nifty500, all, or SYMBOL,SYMBOL")
    parser.add_argument("--resume", action="store_true", help="skip companies finished within --max-age-days")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many companies")
    parser.add_argument("--quarters", type=int, default=12, help="how many of the latest quarters to keep (the exchange lists 20)")
    parser.add_argument("--no-history", action="store_true", help="do not also fetch the earlier symbols of renamed companies")
    parser.add_argument("--refresh", action="store_true", help="fetch again what is already stored")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    members = universe(args.universe)
    if not args.no_history:
        members = with_predecessors(members, PREDECESSORS)
    print(f"{len(members)} companies in {args.universe}", flush=True)
    return run(members, FundamentalsResults(), args.store, resume=args.resume, max_age_days=args.max_age_days, limit=args.limit, quarters=args.quarters, refresh=args.refresh, job="holdings")


if __name__ == "__main__":
    raise SystemExit(main())
