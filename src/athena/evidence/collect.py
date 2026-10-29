"""Fetch every active source once and keep what is new.

    python -m athena.evidence.collect              one pass over every feed in the registry
    python -m athena.evidence.collect --status     each source: working or not, its newest item, how many it holds

The supervisor runs this about once an hour while the software is open. One request to each feed, a second apart, through the plain feed reader; a feed that fails is recorded as failed and the pass goes on.
A feed that answers but whose newest item is more than `STALE_DAYS` old is recorded as `stale`, not `ok`: that is how a feed that has quietly stopped (Moneycontrol's, in 2024) is noticed.
Exit code 0 when at least one source worked, 3 when none did (the network or the whole set is down)."""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from athena.contracts import AthenaError
from athena.evidence.sources import FEED_SOURCES, FeedSource
from athena.evidence.store import DEFAULT_PATH, EvidenceStore
from athena.news.publisher_feeds import http_get
from athena.news.rss import parse_rss
from athena.ops.jobs import Job, JobLocked

STALE_DAYS = 14
SPACING_SECONDS = 1.0
EXIT_NONE_WORKED = 3
EXIT_LOCKED = 6


def collect_one(spec: FeedSource, store: EvidenceStore, get: Callable[..., bytes], now: datetime) -> tuple[str, int]:
    """Fetch and store one feed. Returns `(status, new items)`; the health row says how it ended."""
    try:
        items = parse_rss(get(spec.url), spec.name, spec.id, default_tz=spec.tz)  # type: ignore[arg-type]
    except AthenaError as exc:
        store.record_health(spec.id, now, False, "failed", str(exc))
        return "failed", 0
    except Exception as exc:  # noqa: BLE001 - one feed's surprise must not end the pass
        store.record_health(spec.id, now, False, "failed", f"{type(exc).__name__}: {exc}")
        return "failed", 0
    if not items:
        store.record_health(spec.id, now, False, "empty", "the feed answered with no readable item")
        return "empty", 0
    # nothing dated in the future is kept as published later than we saw it
    items = [i for i in items if i.published <= now + timedelta(hours=1)]
    new = store.add_items(spec.id, items, now)
    newest = max(i.published for i in items) if items else None
    if newest is not None and now - newest > timedelta(days=STALE_DAYS):
        store.record_health(spec.id, now, False, "stale", f"newest item is from {newest.date()}", len(items), newest)
        return "stale", new
    store.record_health(spec.id, now, True, "ok", "", len(items), newest)
    return "ok", new


def collect(store: EvidenceStore, specs: Sequence[FeedSource] = FEED_SOURCES, get: Callable[..., bytes] = http_get, now: datetime | None = None, sleep: Callable[[float], None] = time.sleep, log=print) -> dict[str, tuple[str, int]]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, tuple[str, int]] = {}
    active = [s for s in specs if s.active]
    for n, spec in enumerate(active):
        if n:
            sleep(SPACING_SECONDS)
        out[spec.id] = collect_one(spec, store, get, now)
        log(f"{spec.id:<24} {out[spec.id][0]:<7} {out[spec.id][1]:>3} new")
    for spec in specs:
        if not spec.active:
            store.record_health(spec.id, now, False, "off", spec.off_reason)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show each source and stop")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    store = EvidenceStore(args.store)
    if args.status:
        health = store.health()
        for spec in FEED_SOURCES:
            row = health.get(spec.id, {})
            print(f"{spec.id:<24} tier {spec.tier} {row.get('status', 'never tried'):<9} held {row.get('items_held', 0):>5}  newest {row.get('newest_item')}  {row.get('error') or spec.off_reason}")
        return 0
    try:
        with Job("evidence", locks=("writer:evidence.duckdb",)) as job:
            results = collect(store)
            job.beat(done=len(results), last=list(results)[-1] if results else "")
            worked = sum(1 for status, _ in results.values() if status == "ok")
            if results and not worked:
                job.finish("stopped", EXIT_NONE_WORKED, "no feed worked")
                return EXIT_NONE_WORKED
    except JobLocked as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
