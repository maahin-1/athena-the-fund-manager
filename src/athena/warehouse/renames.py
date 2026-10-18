"""Which symbols are the same company's earlier name, from the exchange's own rename list checked against our bars.

    python -m athena.warehouse.renames            # refresh the table from the exchange's file
    python -m athena.warehouse.renames --status

A company that changes its symbol keeps its prices and statements under the symbol of the day, so a chart or a page of the new symbol begins
on the rename day. Every rename the exchange lists is kept in `symbol_renames` with two checks made against our own bars:

- `joinable`: the prices run on from the old symbol to the new one (the old bars end before the new begin, within `JOIN_GAP_DAYS`, and the
  close steps by no more than `JOIN_STEP`, as printed). A split before or after is no obstacle, since both are on the same footing as printed.
- `retired`: the old symbol stopped trading at the rename, so a symbol the exchange later gave to another company is never taken for a predecessor.

No symbol is named here: the table is the record, and everything that follows a succession reads it."""
from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from athena.contracts import AthenaError
from athena.symbol_changes import Rename, fetch_symbol_changes
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

JOIN_GAP_DAYS = 14
JOIN_STEP = 0.25
RETIRED_AFTER_DAYS = 7
EVENT_WINDOW_DAYS = 45
EVENT_WORDS = ("demerger", "arrangement", "amalgamation", "merger")
_EVENT = re.compile("|".join(EVENT_WORDS), re.IGNORECASE)


@dataclass(frozen=True)
class Span:
    """The bars a symbol has: first and last day with the close on each, as printed."""

    first_day: date
    first_close: float
    last_day: date
    last_close: float


def classify(rename: Rename, old: Span | None, new: Span | None) -> dict[str, Any]:
    """The checks for one rename against the two symbols' bars: whether their prices join (and if not, why) and whether the old symbol retired."""
    retired = old is None or old.last_day <= rename.day + timedelta(days=RETIRED_AFTER_DAYS)
    out: dict[str, Any] = {"gap_days": None, "step": None, "joinable": False, "retired": retired, "reason": ""}
    if old is None or new is None:
        out["reason"] = "no bars under " + ("either name" if old is None and new is None else "the old name" if old is None else "the new name")
        return out
    gap = (new.first_day - old.last_day).days
    step = new.first_close / old.last_close - 1 if old.last_close > 0 else None
    out["gap_days"], out["step"] = gap, step
    if gap <= 0:
        out["reason"] = f"overlap: both traded on the same days ({old.last_day} and {new.first_day})"
    elif gap > JOIN_GAP_DAYS:
        out["reason"] = f"gap of {gap} days between the last old bar ({old.last_day}) and the first new one ({new.first_day})"
    elif step is None or abs(step) > JOIN_STEP:
        out["reason"] = "step of " + ("an unreadable size" if step is None else f"{step:+.0%}") + " between the last old close and the first new one"
    else:
        out["joinable"] = True
    return out


def event_text(rename: Rename, actions: Sequence[tuple[str, date, str]]) -> str:
    """Wording of any demerger, arrangement, amalgamation or merger action of either symbol within `EVENT_WINDOW_DAYS` of the rename, with its date.
    Information for the page; never a reason to join or not to join."""
    near = [
        f"{subject.strip()} ({day})"
        for symbol, day, subject in sorted(actions, key=lambda a: a[1])
        if symbol in (rename.old, rename.new) and abs((day - rename.day).days) <= EVENT_WINDOW_DAYS and _EVENT.search(subject)
    ]
    return "; ".join(dict.fromkeys(near))


def build_rows(
    renames: Mapping[str, Sequence[Rename]], spans: Mapping[str, Span], actions: Sequence[tuple[str, date, str]], now: datetime
) -> list[dict[str, Any]]:
    rows = []
    for group in renames.values():
        for r in group:
            old, new = spans.get(r.old), spans.get(r.new)
            checks = classify(r, old, new)
            rows.append(
                {
                    "old": r.old, "new": r.new, "change_day": r.day, "company": r.company,
                    "last_old_day": old.last_day if old else None, "first_new_day": new.first_day if new else None,
                    "event": event_text(r, actions), **checks,
                }
            )
    return rows


def refresh_renames(warehouse: Warehouse, fetch: Callable[[], Mapping[str, Sequence[Rename]]], now: datetime) -> dict[str, int]:
    """Fetch the exchange's rename list and replace the table with it, checked against the bars held. A failed fetch raises and leaves the last table."""
    renames = fetch()
    spans = {symbol: Span(*values) for symbol, values in warehouse.spans().items()}
    rows = build_rows(renames, spans, warehouse.action_wordings(EVENT_WORDS), now)
    warehouse.replace_renames(rows, now)
    return {"renames": len(rows), "joinable": sum(r["joinable"] for r in rows), "retired": sum(r["retired"] for r in rows)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.warehouse.renames", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--status", action="store_true", help="only report what the table holds")
    args = parser.parse_args(argv)
    from athena.clock import utc_now

    with Warehouse(args.db, wait_seconds=300) as warehouse:
        if not args.status:
            try:
                print(refresh_renames(warehouse, fetch_symbol_changes, utc_now()))
            except AthenaError as exc:
                print(f"not refreshed: {exc}")
                return 1
        print(warehouse.renames_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
