"""Fetch each company's results filings from NSE and keep their statements.

    python -m athena.fundamentals.ingest --universe nifty50 --resume
    python -m athena.fundamentals.ingest --universe TCS,SBIN --standalone
    python -m athena.fundamentals.ingest --universe HDFCLIFE --refresh     (read the kept filings again with the current mapping; no network)

Resumable and gentle: it goes through `NseResults` (1.5 s between requests, back-off when the exchange refuses), keeps every downloaded filing on
disk, skips filings already stored and companies finished recently (`--resume`), and stops cleanly when the exchange blocks it, recording when it may
try again. It is started by hand; nothing schedules it. Default scope per company: consolidated filings (standalone only when there are none, or
with `--standalone`), every annual filing and the latest 13 quarterly ones, newest first."""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, TextIO

from athena.contracts import AthenaError
from athena.fundamentals.history import PREDECESSORS, Predecessor, chain
from athena.fundamentals.integrated import FundamentalsResults, is_block
from athena.fundamentals import shares as share_counts
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.segments import parse_segments
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, now
from athena.fundamentals.universe import Member, universe
from athena.pit.nse_results import CACHE_DIR, FilingRef

BLOCK_PAUSE = timedelta(minutes=30)
GONE_FOR = timedelta(days=90)  # a filing link the exchange answered 404 for is not asked again for this long (`--refresh` asks anyway)
STALL_AFTER = 5  # companies in a row that fail outright (nothing fetched, nothing already held) before a run stops by itself
STALL_PAUSE = timedelta(minutes=5)


class Blocked(Exception):
    """The exchange refused after the client's own retries: stop and let it cool down."""


@dataclass
class Report:
    symbol: str
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    model_failed: int = 0  # of `failed`, the ones caused by the language model not answering: they say nothing about the exchange
    notes: list[str] = field(default_factory=list)


_blocked = is_block


class Stall:
    """Counts the companies in a row that failed outright against the exchange. A company with nothing to fetch (delisted, no filings) is not a failure; one that got any
    filing through resets the count; a failure caused by the language model is not the exchange's and does not count. After `STALL_AFTER` the exchange has gone quiet and carrying on
    only fills the log with failures."""

    def __init__(self) -> None:
        self.count = 0

    def saw(self, report: Report) -> bool:
        self.count = self.count + 1 if report.failed - report.model_failed > 0 and report.fetched + report.skipped == 0 else 0
        return self.count >= STALL_AFTER


def stop_stalled(store_path: str | Path, name: str, symbol: str, out: TextIO) -> int:
    """Record the pause against the company that tripped it and say so; returns the exit code of a stopped run."""
    with FundamentalsStore(store_path) as store:
        store.set_run(name, "blocked", error=f"no reply from the exchange for {STALL_AFTER} companies in a row", blocked_until=now() + STALL_PAUSE)
    print(f"{symbol:<12} no reply from the exchange for {STALL_AFTER} companies in a row; stopping, try again after {STALL_PAUSE.seconds // 60} minutes", file=out)
    return 3


def with_predecessors(members: Sequence[Member], table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS) -> list[Member]:
    """The members, each followed by the earlier symbols of its company (see `athena.fundamentals.history`), so a renamed company's old statements are
    fetched under their old symbol. A symbol already in the list is not added twice."""
    out: list[Member] = []
    seen: set[str] = set()
    for m in members:
        for member in (m, *(Member(p.symbol, m.name, m.industry, m.isin) for p in reversed(chain(m.symbol, table)))):
            if member.symbol not in seen:
                seen.add(member.symbol)
                out.append(member)
    return out


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
    member: Member, results: Any, store_path: str | Path, *, quarters: int = 13, standalone: bool = False, refresh: bool = False,
    cache_dir: Path = CACHE_DIR,
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
            status, when = store.filing_state(ref.url)
            if status == "ok" and not refresh:
                report.skipped += 1
                continue
            if status == "gone" and not refresh and when is not None and now() - when.replace(tzinfo=now().tzinfo) < GONE_FOR:
                continue  # known dead: neither a skip nor a failure
        try:
            text = _text(results, ref, cache_dir)
        except AthenaError as exc:
            if _blocked(exc):
                raise Blocked(str(exc)) from exc
            if "HTTP 404" in str(exc):  # gone from the exchange: remember it, and do not count it as a failure that keeps the company on every resume list
                with FundamentalsStore(store_path) as store:
                    store.record_filing(ref.url, member.symbol, basis, ref.period_end, ref.annual, ref.filed, "gone", str(exc)[:200])
                report.notes.append(f"{ref.period_end}: {exc} (remembered as gone)")
                continue
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
            if refresh:
                store.forget_filing(ref.url)
            store.save_statements(statements, ref.url, ref.filed)
            store.record_filing(ref.url, member.symbol, basis, ref.period_end, ref.annual, ref.filed, "ok")
            store.save_company(member.symbol, member.name, member.isin, member.industry, statements.layout, statements.shares)
            store.save_share_count(ref.url, member.symbol, basis, ref.period_end, ref.filed, statements.shares_declared, statements.shares_from_profit)
            share_counts.refresh(store, member.symbol)
            if ref.annual:
                found = parse_segments(text)
                if found is not None and found.segments:
                    store.save_segments(ref.url, member.symbol, basis, ref.period_end, ref.filed, found.segments)
        report.fetched += 1
        report.notes.extend(statements.notes)
    return report


def run(
    members: Sequence[Member], results: Any, store_path: str | Path, *, resume: bool, max_age_days: int, limit: int | None,
    quarters: int, standalone: bool, refresh: bool = False, out: TextIO = sys.stdout, cache_dir: Path = CACHE_DIR, job: str | None = None,
) -> int:
    """Ingest each member in turn (the loop is `athena.fundamentals.runner.run_each`). Returns 0 when done, 3 when the exchange blocked it (or a block is still running)."""
    from athena.fundamentals.runner import run_each

    return run_each(
        members, lambda m: ingest_symbol(m, results, store_path, quarters=quarters, standalone=standalone, refresh=refresh, cache_dir=cache_dir), store_path,
        key=lambda m: m.symbol, resume=resume, max_age_days=max_age_days, limit=limit, refresh=refresh, out=out, job=job,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50", help="nifty50, nifty500, all, or SYMBOL,SYMBOL")
    parser.add_argument("--resume", action="store_true", help="skip companies finished within --max-age-days")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many companies")
    parser.add_argument("--quarters", type=int, default=13)
    parser.add_argument("--standalone", action="store_true", help="also fetch standalone filings")
    parser.add_argument("--no-history", action="store_true", help="do not also fetch the earlier symbols of renamed companies")
    parser.add_argument("--refresh", action="store_true", help="read filings already stored again with the current mapping (uses the kept copies)")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    members = universe(args.universe)
    if not args.no_history:
        members = with_predecessors(members)
    print(f"{len(members)} companies in {args.universe}", flush=True)
    return run(
        members, FundamentalsResults(), args.store, resume=args.resume, max_age_days=args.max_age_days, limit=args.limit, quarters=args.quarters,
        standalone=args.standalone, refresh=args.refresh, job="fundamentals_ingest",
    )


if __name__ == "__main__":
    raise SystemExit(main())
