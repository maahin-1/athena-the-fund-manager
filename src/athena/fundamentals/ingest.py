"""Fetch each company's results filings from NSE and keep their statements.

    python -m athena.fundamentals.ingest --universe nifty50 --resume
    python -m athena.fundamentals.ingest --universe TCS,SBIN --standalone

Resumable and gentle: it goes through `NseResults` (1.5 s between requests, back-off when the exchange refuses), keeps every downloaded filing on
disk, skips filings already stored and companies finished recently (`--resume`), and stops cleanly when the exchange blocks it, recording when it may
try again. It is started by hand; nothing schedules it. Default scope per company: consolidated filings (standalone only when there are none, or
with `--standalone`), every annual filing and the latest 13 quarterly ones, newest first."""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, TextIO

from athena.contracts import AthenaError
from athena.fundamentals.integrated import FundamentalsResults, is_block
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, now
from athena.fundamentals.universe import Member, universe
from athena.pit.nse_results import CACHE_DIR, FilingRef

BLOCK_PAUSE = timedelta(minutes=30)


class Blocked(Exception):
    """The exchange refused after the client's own retries: stop and let it cool down."""


@dataclass
class Report:
    symbol: str
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


_blocked = is_block


def wanted(refs: Sequence[FilingRef], quarters: int = 13, standalone: bool = False) -> list[FilingRef]:
    """The filings worth fetching, newest first: consolidated ones (every annual, the latest `quarters` quarterly), or standalone ones when the
    company has no consolidated filing or `standalone` is set. For one period and basis only the latest filing is kept (a revision replaces its original)."""
    latest: dict[tuple[Any, ...], FilingRef] = {}
    for r in refs:
        key = (r.period_end, r.annual, r.consolidated)
        if key not in latest or r.filed > latest[key].filed:
            latest[key] = r
    pool = list(latest.values())
    consolidated = [r for r in pool if r.consolidated]
    use = pool if standalone or not consolidated else consolidated
    annual = [r for r in use if r.annual]
    quarterly = sorted((r for r in use if not r.annual), key=lambda r: r.period_end, reverse=True)[:quarters]
    return sorted(annual + quarterly, key=lambda r: r.period_end, reverse=True)


def _text(results: Any, ref: FilingRef, cache_dir: Path) -> str:
    path = cache_dir / (hashlib.sha1(ref.url.encode()).hexdigest()[:20] + ".xml")  # the same cache the point-in-time code uses
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = results.text(ref)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def ingest_symbol(
    member: Member, results: Any, store_path: str | Path, *, quarters: int = 13, standalone: bool = False, cache_dir: Path = CACHE_DIR
) -> Report:
    """Fetch and store one company's filings. Raises `Blocked` when the exchange refuses; any other failure is counted and the run goes on."""
    report = Report(member.symbol)
    try:
        refs = results.filings(member.symbol)
    except AthenaError as exc:
        if _blocked(exc):
            raise Blocked(str(exc)) from exc
        report.notes.append(str(exc))
        report.failed += 1
        return report
    for ref in wanted(refs, quarters, standalone):
        basis = "consolidated" if ref.consolidated else "standalone"
        with FundamentalsStore(store_path) as store:
            if store.filing_status(ref.url) == "ok":
                report.skipped += 1
                continue
        try:
            text = _text(results, ref, cache_dir)
        except AthenaError as exc:
            if _blocked(exc):
                raise Blocked(str(exc)) from exc
            report.failed += 1
            report.notes.append(f"{ref.period_end}: {exc}")
            continue
        try:
            statements = parse_rows(text)
        except ValueError as exc:
            with FundamentalsStore(store_path) as store:
                store.record_filing(ref.url, member.symbol, basis, ref.period_end, ref.annual, ref.filed, "failed", str(exc)[:200])
            report.failed += 1
            report.notes.append(f"{ref.period_end}: {exc}")
            continue
        with FundamentalsStore(store_path) as store:
            store.save_statements(statements, ref.url, ref.filed)
            store.record_filing(ref.url, member.symbol, basis, ref.period_end, ref.annual, ref.filed, "ok")
            store.save_company(member.symbol, member.name, member.isin, member.industry, statements.layout, statements.shares)
        report.fetched += 1
        report.notes.extend(statements.notes)
    return report


def run(
    members: Sequence[Member], results: Any, store_path: str | Path, *, resume: bool, max_age_days: int, limit: int | None,
    quarters: int, standalone: bool, out: TextIO = sys.stdout,
) -> int:
    """Ingest each member in turn. Returns 0 when done, 3 when the exchange blocked it (or a block is still running)."""
    with FundamentalsStore(store_path) as store:
        until = store.blocked_until()
    if until and until > now():
        print(f"blocked by the exchange until {until:%H:%M} UTC; not starting", file=out)
        return 3
    done = 0
    for member in members:
        if limit is not None and done >= limit:
            break
        with FundamentalsStore(store_path) as store:
            state = store.run(member.symbol)
        if resume and state and state["status"] == "ok" and state["finished"] > now() - timedelta(days=max_age_days):
            print(f"{member.symbol:<12} skipped (finished {state['finished']:%Y-%m-%d})", file=out)
            continue
        started = now()
        try:
            report = ingest_symbol(member, results, store_path, quarters=quarters, standalone=standalone)
        except Blocked as exc:
            with FundamentalsStore(store_path) as store:
                store.set_run(member.symbol, "blocked", error=str(exc)[:200], blocked_until=now() + BLOCK_PAUSE)
            print(f"{member.symbol:<12} BLOCKED ({exc}); stopping, try again after {BLOCK_PAUSE.seconds // 60} minutes", file=out)
            return 3
        with FundamentalsStore(store_path) as store:
            ok = report.failed == 0 or report.fetched + report.skipped > 0
            store.set_run(member.symbol, "ok" if ok else "failed", filings=report.fetched + report.skipped)
        seconds = (now() - started).total_seconds()
        print(f"{member.symbol:<12} fetched {report.fetched:>3} skipped {report.skipped:>3} failed {report.failed:>3}  {seconds:5.0f}s", file=out)
        for note in report.notes[:3]:
            print(f"{'':<12} note: {note}", file=out)
        done += 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50", help="nifty50, nifty500, all, or SYMBOL,SYMBOL")
    parser.add_argument("--resume", action="store_true", help="skip companies finished within --max-age-days")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many companies")
    parser.add_argument("--quarters", type=int, default=13)
    parser.add_argument("--standalone", action="store_true", help="also fetch standalone filings")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    members = universe(args.universe)
    print(f"{len(members)} companies in {args.universe}", flush=True)
    return run(
        members, FundamentalsResults(), args.store, resume=args.resume, max_age_days=args.max_age_days, limit=args.limit, quarters=args.quarters,
        standalone=args.standalone,
    )


if __name__ == "__main__":
    raise SystemExit(main())
