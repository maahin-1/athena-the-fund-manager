"""Read stored items for claims and check each claim against our own data.

    python -m athena.evidence.verify              read new items, check open claims
    python -m athena.evidence.verify --status     what has been read and how the checks ended
    python -m athena.evidence.verify --show SYMBOL    that company's claims and what our data said

New items are read once (patterns only, title only; see `athena.evidence.claims`). Every claim that is not settled is checked again on each pass: a price bar or a results filing that was not in yet
is waited for, up to 21 days, and then the claim is closed as not checkable with the reason. Prices come from the warehouse, figures from the fundamentals store, insider trades, flows and
board meetings from the evidence store; a store that is busy or missing leaves its claims pending, never wrong. The warehouse is read through its snapshot when a long job holds it."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.evidence.checks import check_claim
from athena.evidence.claims import Claim, NameIndex, common_lowercase_words, extract_claims
from athena.evidence.store import DEFAULT_PATH, EvidenceStore
from athena.ops.jobs import Job, JobLocked

READ_BACK_DAYS = 45  # items older than this are not read for claims
CHECK_BACK_DAYS = 60
IST_SHIFT = timedelta(hours=5, minutes=30)
EXIT_LOCKED = 6


class LiveData:
    """The data a check asks about. Each store is opened the first time it is needed and a failure to open it is remembered, so the claims that need it stay pending instead of the pass stopping."""

    def __init__(self, store: EvidenceStore, warehouse: Path = Path(".athena") / "warehouse.duckdb", fundamentals: Path = Path(".athena") / "fundamentals.duckdb"):
        self._store, self._warehouse_path, self._fund_path = store, Path(warehouse), Path(fundamentals)
        self._wh: Any = None
        self._fund: Any = None
        self._wh_error = self._fund_error = ""
        self._last: dict[str, date | None] = {}

    def close(self) -> None:
        for held in (self._wh, self._fund):
            if held is not None:
                held.close()

    def _warehouse(self) -> Any:
        if self._wh is None and not self._wh_error:
            from athena.warehouse.warehouse import Warehouse

            try:
                self._wh = Warehouse(self._warehouse_path, read_only=True, wait_seconds=10)
            except Exception as exc:  # noqa: BLE001
                self._wh_error = f"{type(exc).__name__}: {exc}"
        if self._wh is None:
            raise AthenaError(f"the warehouse could not be opened ({self._wh_error})")
        return self._wh

    def _fundamentals(self) -> Any:
        if self._fund is None and not self._fund_error:
            from athena.fundamentals.store import open_read

            try:
                self._fund = open_read(self._fund_path)
            except Exception as exc:  # noqa: BLE001
                self._fund_error = f"{type(exc).__name__}: {exc}"
        if self._fund is None:
            raise AthenaError(f"the fundamentals store could not be opened ({self._fund_error})")
        return self._fund

    def bars(self, symbol: str, since: date, until: date) -> list[tuple[date, float, float, float, float]]:
        return [((b.timestamp + IST_SHIFT).date(), b.open, b.high, b.low, b.close) for b in self._warehouse().bars(symbol, since, until)]

    def last_bar_day(self, symbol: str) -> date | None:
        if symbol not in self._last:
            recent = self._warehouse().bars(symbol, date.today() - timedelta(days=60))
            self._last[symbol] = max(((b.timestamp + IST_SHIFT).date() for b in recent), default=None)
        return self._last[symbol]

    def quarters(self, symbol: str) -> list[dict[str, Any]]:
        return self._fundamentals().quarter_figures(symbol)

    def insider(self, symbol: str, since: date, until: date) -> list[dict[str, Any]]:
        return self._store.insider_between(since, until, symbol)

    def insider_newest(self) -> datetime | None:
        return self._store.insider_newest()

    def flows(self, since: date, until: date) -> list[dict[str, Any]]:
        return self._store.flows_between(since, until)

    def flows_first(self) -> date | None:
        return self._store.flows_first()

    def board(self, symbol: str, since: date, until: date) -> list[dict[str, Any]]:
        return self._store.board_for(symbol, since, until)


def load_names(warehouse: Path = Path(".athena") / "warehouse.duckdb") -> list[tuple[str, str]]:
    """Every listed company's `(symbol, name)` from the warehouse, main board only (SME names and ETF names would collide with them)."""
    from athena.warehouse.warehouse import Warehouse

    with Warehouse(warehouse, read_only=True, wait_seconds=10) as wh:
        return [(u["symbol"], u["name"]) for u in wh.universe("stock") if u["name"]]


def read_new_items(store: EvidenceStore, index: NameIndex, now: datetime) -> tuple[int, int]:
    """Read every item not read yet. Returns `(items read, claims kept)`."""
    items = store.items_without_claims(now - timedelta(days=READ_BACK_DAYS))
    batch = [(it, extract_claims(it.title, it.published_at, index.find(it.title))) for it in items]
    kept = store.add_claims(batch, now) if batch else 0
    return len(items), kept


def check_open(store: EvidenceStore, data: Any, now: datetime) -> dict[str, int]:
    """Check every claim that is not settled. A source of data that cannot be opened leaves the claims that need it as they were."""
    out: dict[str, int] = {}
    batch = []
    for row in store.claims_to_check(now - timedelta(days=CHECK_BACK_DAYS)):
        claim = Claim(row["kind"], row["symbol"], row["text"], row["fields"])
        try:
            result = check_claim(claim, row["published_at"], now, data)
        except AthenaError as exc:
            out["waiting"] = out.get("waiting", 0) + 1
            if out["waiting"] == 1:
                print(f"waiting on data: {exc}")
            continue
        out[result.outcome] = out.get(result.outcome, 0) + 1
        batch.append((row["claim_id"], result.outcome, result.final, result.detail, result.ours))
    if batch:
        store.save_checks(batch, now)
    return out


def run(store: EvidenceStore, names: Sequence[tuple[str, str]], data: Any, now: datetime | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    titles = [i.title for i in store.items_between(now - timedelta(days=READ_BACK_DAYS), now, limit=50000)]
    index = NameIndex(names, common_lowercase_words(titles))
    read, kept = read_new_items(store, index, now)
    log(f"read {read} new items, kept {kept} claims ({len(index)} company names known)")
    outcomes = check_open(store, data, now)
    log("checked: " + (", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())) or "nothing open"))
    return {"read": read, "claims": kept, "outcomes": outcomes}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--show", metavar="SYMBOL")
    parser.add_argument("--reread", action="store_true", help="forget every claim and check (they are derived from the items and can be rebuilt) and read the items again with the current patterns")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    store = EvidenceStore(args.store)
    if args.status:
        print(store.claim_counts())
        for sid, rec in sorted(store.source_record().items()):
            print(f"{sid:<24} {rec}")
        return 0
    if args.reread:
        store.reset_claims()
        print("claims and checks cleared; the next pass reads every item again")
        return 0
    if args.show:
        now = datetime.now(timezone.utc)
        for r in store.claims_for(args.show.upper(), now - timedelta(days=60), now):
            print(f"{r['published_at']:%Y-%m-%d} {r['publisher']:<18} {r['kind']:<13} {r['outcome']:<13} {r['text'][:70]} | {r['detail'][:110]}")
        return 0
    try:
        with Job("evidence_claims") as job:
            try:
                names = load_names()
            except Exception as exc:  # noqa: BLE001
                job.finish("stopped", 4, f"company names unavailable: {exc}")
                print(f"company names unavailable: {exc}", file=sys.stderr)
                return 4
            data = LiveData(store)
            try:
                result = run(store, names, data)
            finally:
                data.close()
            job.beat(read=result["read"], claims=result["claims"])
    except JobLocked as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
