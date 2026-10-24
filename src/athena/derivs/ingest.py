"""Load the derivatives history, day by day, into `.athena/derivs.duckdb`.

    python -m athena.derivs.ingest --since 2020-01-01        # resumable: days already loaded are skipped
    python -m athena.derivs.ingest --status

One request every `MIN_GAP` seconds to the exchange's archive host; two refusals in a row stop the run (the days loaded stay). A weekday with no file is recorded as a holiday, except in the
last `LATE_DAYS` days, where the file may only be late. Strike-level option data is kept for the last `STRIKE_DAYS` calendar days only; older days keep futures and option totals."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from athena.contracts import AthenaError
from athena.derivs import bhav
from athena.derivs.store import DEFAULT_PATH, DerivsStore

STRIKE_DAYS = 800
LATE_DAYS = 3
STOP_AFTER_REFUSALS = 2


def load_day(day: date, fetch: Callable[[str], bytes | None], unzip: Callable[[bytes], str], strikes: bool) -> dict[str, list[tuple]] | None:
    """The day's reduced rows, or None when neither address has a file."""
    for url in bhav.urls_for(day):
        content = fetch(url)
        if content is not None:
            return bhav.reduce(bhav.parse(unzip(content)), day, strikes)
    return None


def run(
    store: DerivsStore, since: date, today: date, fetch: Callable[[str], bytes | None], unzip: Callable[[bytes], str], out=sys.stdout,
) -> dict[str, int]:
    done = store.days_done()
    summary = {"loaded": 0, "holidays": 0, "skipped": 0, "stopped": 0}
    refusals = 0
    day = since
    while day <= today:
        if day.weekday() >= 5 or day in done:
            summary["skipped"] += day.weekday() < 5
            day += timedelta(days=1)
            continue
        try:
            reduced = load_day(day, fetch, unzip, strikes=(today - day).days <= STRIKE_DAYS)
        except AthenaError as exc:
            refusals += 1
            print(f"{day}: {exc}", file=out)
            if refusals >= STOP_AFTER_REFUSALS:
                summary["stopped"] = 1
                break
            continue  # try the same day once more
        refusals = 0
        if reduced is None:
            if (today - day).days <= LATE_DAYS:
                day += timedelta(days=1)
                continue
            summary["holidays"] += 1
        else:
            summary["loaded"] += 1
        store.put_day(day, reduced)
        if (summary["loaded"] + summary["holidays"]) % 50 == 0:
            print(f"{day}: {summary['loaded']} loaded, {summary['holidays']} holidays", file=out, flush=True)
        day += timedelta(days=1)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    from athena.pit.bhavcopy import ArchiveClient, _unzip

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", default="2020-01-01")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    with DerivsStore(args.store) as store:
        if args.status:
            print(store.coverage())
            return 0
        summary = run(store, date.fromisoformat(args.since), date.today() - timedelta(days=1), ArchiveClient().get, _unzip)
    print(summary)
    return 3 if summary["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
