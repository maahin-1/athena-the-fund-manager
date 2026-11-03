"""Fetch the public-issue data once and keep it.

    python -m athena.ipo.collect              one pass: the calendar, the subscription of every open issue, issues that have listed, SEBI's drafts
    python -m athena.ipo.collect --status     each source and what is held

The supervisor runs this every two hours. The exchange's endpoints need a cookie from its home page (`athena.evidence.structured.NseJson`); SEBI's listing is a plain page, read with the platform's honest
user agent. A source that fails is recorded as failed and the pass goes on. For each draft SEBI lists that we have not seen, its own page is read once (a second apart, at most 30 a pass) for the PDF it shows.
Exit code 0 when anything worked, 3 when nothing did, 6 when another pass holds the lock."""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from athena.contracts import AthenaError
from athena.ipo import sources as src
from athena.ipo.store import DEFAULT_PATH, IpoStore
from athena.ops.jobs import Job, JobLocked

SPACING_SECONDS = 1.5
PAGES_PER_PASS = 30
PAST_DAYS = 90


def _record(store: IpoStore, sid: str, now: datetime, work: Callable[[], tuple[str, int]]) -> tuple[str, int]:
    try:
        status, n = work()
    except AthenaError as exc:
        store.record_health(sid, now, False, "failed", str(exc))
        return "failed", 0
    except Exception as exc:  # noqa: BLE001 - one source's surprise must not end the pass
        store.record_health(sid, now, False, "failed", f"{type(exc).__name__}: {exc}")
        return "failed", 0
    store.record_health(sid, now, status == "ok", status, "" if status == "ok" else "the source answered with nothing readable", n)
    return status, n


def collect(store: IpoStore, nse: Callable[..., Any], page: Callable[[str], bytes], now: datetime | None = None, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> dict[str, tuple[str, int]]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, tuple[str, int]] = {}

    def upcoming() -> tuple[str, int]:
        issues = src.parse_issues(nse(src.NSE_UPCOMING, {"category": "ipo"}))
        store.upsert_issues(issues, now)
        return ("ok" if issues else "empty"), len(issues)

    def subscription() -> tuple[str, int]:
        current = src.parse_issues(nse(src.NSE_CURRENT))
        store.upsert_issues(current, now)
        kept = 0
        for issue in (i for i in current if i.status == "Active"):
            sleep(SPACING_SECONDS)
            kept += store.add_subscription(issue.symbol, src.parse_subscription(nse(src.NSE_DETAIL, {"symbol": issue.symbol, "series": issue.series})), now)
        return "ok", kept

    def past() -> tuple[str, int]:
        rows = src.parse_past(nse(src.NSE_PAST, {"from_date": (now - timedelta(days=PAST_DAYS)).strftime("%d-%m-%Y"), "to_date": now.strftime("%d-%m-%Y")}))
        store.upsert_listed(rows, now)
        return ("ok" if rows else "empty"), len(rows)

    def drafts() -> tuple[str, int]:
        filings = src.parse_sebi(page(src.SEBI_LISTING))
        fresh = store.add_filings(filings, now)
        for url in store.filings_without_pdf(PAGES_PER_PASS):
            sleep(SPACING_SECONDS)
            try:
                store.set_pdf(url, src.pdf_of_filing_page(page(url)))
            except AthenaError:
                continue  # tried again on the next pass
        return "ok", len(fresh)

    for sid, work in (("nse_upcoming", upcoming), ("nse_subscription", subscription), ("nse_listed", past), ("sebi_drafts", drafts)):
        if out:
            sleep(SPACING_SECONDS)
        out[sid] = _record(store, sid, now, work)
        log(f"{sid:<18} {out[sid][0]:<7} {out[sid][1]:>4}")
    return out


def http_page(url: str) -> bytes:
    from athena.news.publisher_feeds import http_get

    return http_get(url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    store = IpoStore(args.store)
    if args.status:
        for sid, h in sorted(store.health().items()):
            print(f"{sid:<18} {h['status']:<7} items {h['items']:>4}  last ok {h['last_ok']}  {h['error']}")
        return 0
    try:
        with Job("ipo") as job:
            from athena.evidence.structured import NseJson

            results = collect(store, NseJson(), http_page)
            job.beat(done=len(results))
            if not any(status == "ok" for status, _ in results.values()):
                job.finish("stopped", 3, "no IPO source worked")
                return 3
    except JobLocked as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
