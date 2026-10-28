"""A company's documents from NSE: annual reports, investor-call notices, transcripts and recordings, and its other announcements, as titles and links.
The files themselves are not downloaded here; the page links to the exchange's own copy.

    python -m athena.fundamentals.documents --universe nifty50 --resume

Two requests per company (`annual-reports`, `corporate-announcements`), through the same throttled, resumable client as the statement ingest. Started
by hand, never scheduled."""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from athena.contracts import AthenaError
from athena.fundamentals.history import PREDECESSORS
from athena.fundamentals.ingest import Blocked, Report, with_predecessors
from athena.fundamentals.integrated import FundamentalsResults, is_block
from athena.fundamentals.runner import run_each
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore
from athena.fundamentals.universe import Member, universe

ANNUAL = "https://www.nseindia.com/api/annual-reports"
ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements"
IST = timezone(timedelta(hours=5, minutes=30))
FROM = date(2018, 1, 1)
KEEP_OTHER = 200  # the newest announcements that are neither investor-call items nor routine notices
ARCHIVE_HOST = "https://nsearchives.nseindia.com/"
ROUTINE = ("loss of share certificate", "copy of newspaper", "newspaper advertisement", "record date", "date of payment of dividend")
CALL_WORDS = ("Analysts/Institutional Investor Meet", "Con. Call")
ANNUAL_KIND, CONCALL, ANNOUNCEMENT = "annual_report", "concall", "announcement"


@dataclass(frozen=True)
class Doc:
    kind: str
    title: str
    category: str
    published: datetime
    url: str
    size: str = ""


def _when(text: str, formats: tuple[str, ...]) -> datetime | None:
    for form in formats:
        try:
            return datetime.strptime(text.strip().title(), form).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _url_ok(url: str) -> bool:
    """Only links on the exchange's archive host are kept: the page opens them, so nothing else may get in."""
    return url.startswith(ARCHIVE_HOST) and re.fullmatch(r"[\w./:%+~@=&-]+", url) is not None


def parse_annual(body: Any) -> list[Doc]:
    """Annual reports, newest first. The title is the financial year, for example `FY2025-26`."""
    rows = body.get("data") if isinstance(body, dict) else body
    out = []
    for row in rows if isinstance(rows, list) else []:
        url = str(row.get("fileName") or "")
        when = _when(str(row.get("broadcast_dttm") or ""), ("%d-%b-%Y %H:%M:%S",))
        if not _url_ok(url) or not str(row.get("toYr") or "").isdigit() or not str(row.get("fromYr") or "").isdigit():
            continue
        # older reports carry no broadcast time ("-"): the financial year's end stands in for it, so the list still sorts by year
        when = when or datetime(int(row["toYr"]), 3, 31, tzinfo=IST)
        out.append(Doc(ANNUAL_KIND, f"FY{row['fromYr']}-{str(row['toYr'])[-2:]}", "Annual report", when, url, str(row.get("attFileSize") or "")))
    return sorted(out, key=lambda d: d.title, reverse=True)


def _about(text: str) -> str:
    """`X has informed the Exchange about Transcript` becomes `Transcript`."""
    return re.sub(r"^.*?informed the Exchange about\s*", "", text.strip(), flags=re.S).strip() or text.strip()


def parse_announcements(rows: Any) -> list[Doc]:
    """Investor-call items (all since 2018) and the newest of the other announcements, newest first. Routine notices (lost share certificates,
    newspaper copies, record dates) are left out."""
    calls: list[Doc] = []
    others: list[Doc] = []
    for row in rows if isinstance(rows, list) else []:
        url = str(row.get("attchmntFile") or "")
        when = _when(str(row.get("an_dt") or ""), ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"))
        category = str(row.get("desc") or "")
        if not _url_ok(url) or when is None or when.date() < FROM or any(word in category.lower() for word in ROUTINE):
            continue
        text = str(row.get("attchmntText") or "")
        if any(word in category for word in CALL_WORDS):
            calls.append(Doc(CONCALL, _about(text), category, when, url, str(row.get("attFileSize") or "")))
        else:
            others.append(Doc(ANNOUNCEMENT, _about(text)[:200], category, when, url, str(row.get("attFileSize") or "")))
    newest = lambda docs: sorted(docs, key=lambda d: d.published, reverse=True)  # noqa: E731
    return [*newest(calls), *newest(others)[:KEEP_OTHER]]


def _fetch(results: Any, url: str, symbol: str) -> Any:
    try:
        return results._get(url, {"index": "equities", "symbol": symbol}).json()
    except Exception as exc:
        raise AthenaError(f"NSE documents unavailable for {symbol}: {exc}") from exc


def ingest_symbol(member: Member, results: Any, store_path: str | Path, **_: Any) -> Report:
    """Fetch and store one company's documents. Raises `Blocked` when the exchange refuses; one list failing is counted and the other is kept."""
    report = Report(member.symbol)
    for name, url, parse, kinds in (
        ("annual reports", ANNUAL, parse_annual, (ANNUAL_KIND,)), ("announcements", ANNOUNCEMENTS, parse_announcements, (CONCALL, ANNOUNCEMENT)),
    ):
        try:
            docs = parse(_fetch(results, url, member.symbol))
        except AthenaError as exc:
            if is_block(exc):
                raise Blocked(str(exc)) from exc
            report.failed += 1
            report.notes.append(f"{name}: {exc}")
            continue
        with FundamentalsStore(store_path) as store:
            store.save_documents(member.symbol, kinds, docs)
        report.fetched += len(docs)
    return report


def run(
    members: Sequence[Member], results: Any, store_path: str | Path, *, resume: bool, max_age_days: int, limit: int | None, refresh: bool = False,
    out: TextIO = sys.stdout, job: str | None = None,
) -> int:
    return run_each(
        members, lambda m: ingest_symbol(m, results, store_path), store_path, key=lambda m: f"{m.symbol}#docs", resume=resume,
        max_age_days=max_age_days, limit=limit, refresh=refresh, out=out, job=job,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50", help="nifty50, nifty500, all, or SYMBOL,SYMBOL")
    parser.add_argument("--resume", action="store_true", help="skip companies finished within --max-age-days")
    parser.add_argument("--max-age-days", type=int, default=7, help="how old a finished company may be before it is fetched again (default 7: announcements change daily)")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many companies")
    parser.add_argument("--no-history", action="store_true", help="do not also fetch the earlier symbols of renamed companies")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    members = universe(args.universe)
    if not args.no_history:
        members = with_predecessors(members, PREDECESSORS)
    print(f"{len(members)} companies in {args.universe}", flush=True)
    return run(members, FundamentalsResults(), args.store, resume=args.resume, max_age_days=args.max_age_days, limit=args.limit, job="documents")


if __name__ == "__main__":
    raise SystemExit(main())
