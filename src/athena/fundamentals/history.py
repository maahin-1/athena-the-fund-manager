"""Symbols that changed: a company renamed, or one whose listing continues under a new symbol, keeps one history on its page.

The exchange files results under the symbol of the day, so Zomato's statements to 2024 sit under ZOMATO and Eternal's from 2025 under ETERNAL. Which
symbol is whose earlier name is not kept by hand: it is the exchange's own rename list as the warehouse holds it (`athena.warehouse.renames`, the
`symbol_renames` table, rows whose old symbol stopped trading at the rename), read through `SuccessionTable`. Where the old and the new symbol both
hold a period, the new one wins."""
from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from athena.fundamentals.store import FundamentalsStore


@dataclass(frozen=True)
class Predecessor:
    symbol: str
    note: str


def _note(old: str, company: str, day: date, event: str) -> str:
    text = f"{old} was this company's symbol until {day}" + (f" ({company})" if company else "") + f"; the periods before it are as filed under {old}."
    if event:
        text += f" The exchange also records {event}, so periods before it may include business that is now outside the company."
    return text


class SuccessionTable(Mapping[str, tuple[Predecessor, ...]]):
    """The earlier symbols of each symbol (nearest first), read from the warehouse's rename table and kept for `keep` seconds. A warehouse that is
    missing, busy or built before the table existed gives an empty table, retried after `retry` seconds: a page never fails for want of it."""

    def __init__(self, path: str | Path | None = None, keep: float = 600.0, retry: float = 60.0):
        self._path, self._keep, self._retry = path, keep, retry
        self._table: dict[str, tuple[Predecessor, ...]] = {}
        self._until = 0.0

    def _load(self) -> dict[str, tuple[Predecessor, ...]]:
        from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

        path = Path(self._path) if self._path is not None else DEFAULT_PATH
        found: dict[str, list[Predecessor]] = {}
        with Warehouse(path, read_only=True) as warehouse:
            for old, new, day, company, event in warehouse._con.execute(
                "SELECT old, new, change_day, COALESCE(company, ''), COALESCE(event, '') FROM symbol_renames WHERE retired ORDER BY change_day DESC"
            ).fetchall():
                found.setdefault(new, []).append(Predecessor(old, _note(old, company, day, event)))
        return {new: tuple(items) for new, items in found.items()}

    def _get(self) -> dict[str, tuple[Predecessor, ...]]:
        now = time.monotonic()
        if now >= self._until:
            try:
                self._table, self._until = self._load(), now + self._keep
            except Exception:
                self._table, self._until = {}, now + self._retry
        return self._table

    def __getitem__(self, symbol: str) -> tuple[Predecessor, ...]:
        return self._get()[symbol]

    def __iter__(self) -> Iterator[str]:
        return iter(self._get())

    def __len__(self) -> int:
        return len(self._get())


PREDECESSORS: Mapping[str, tuple[Predecessor, ...]] = SuccessionTable()


def chain(symbol: str, table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS, _seen: set[str] | None = None) -> list[Predecessor]:
    """The earlier symbols of `symbol`, oldest first (a predecessor's own predecessors come before it)."""
    seen = {symbol} if _seen is None else _seen
    out: list[Predecessor] = []
    for p in table.get(symbol, ()):
        if p.symbol in seen:
            continue
        seen.add(p.symbol)
        out = [*chain(p.symbol, table, seen), p, *out]
    return out


def lines(
    store: FundamentalsStore, symbol: str, basis: str, statement: str, table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS
) -> dict[tuple[date, date], dict[str, float]]:
    """The statement's rows by period for the symbol and its predecessors. A period the symbol itself holds replaces the same period of an earlier one."""
    merged: dict[tuple[date, date], dict[str, float]] = {}
    for earlier in chain(symbol, table):
        merged.update(store.lines(earlier.symbol, basis, statement))
    merged.update(store.lines(symbol, basis, statement))
    return merged


def used(store: FundamentalsStore, symbol: str, basis: str, table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS) -> list[dict[str, str]]:
    """The predecessors that actually add periods under this basis, each with its note and the span it covers, for the page to show."""
    out = []
    for earlier in chain(symbol, table):
        got = store.lines(earlier.symbol, basis, "income") or store.lines(earlier.symbol, basis, "balance")
        if got:
            ends = sorted(end for _, end in got)
            out.append({"symbol": earlier.symbol, "note": earlier.note, "first": ends[0].isoformat(), "last": ends[-1].isoformat()})
    return out


def holdings(
    store: FundamentalsStore, symbol: str, table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS
) -> dict[date, dict[str, tuple[float, float | None]]]:
    """The shareholding patterns of the symbol and its predecessors by quarter end; the symbol's own quarter wins."""
    merged: dict[date, dict[str, tuple[float, float | None]]] = {}
    for earlier in chain(symbol, table):
        merged.update(store.holdings(earlier.symbol))
    merged.update(store.holdings(symbol))
    return merged


def documents(
    store: FundamentalsStore, symbol: str, kind: str, limit: int, table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS
) -> list[dict[str, Any]]:
    """The documents of one kind for the symbol and its predecessors, newest first, each link once."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for name in (symbol, *(p.symbol for p in reversed(chain(symbol, table)))):
        for doc in store.documents(name, kind, limit):
            if doc["url"] not in seen:
                seen.add(doc["url"])
                out.append(doc)
    return sorted(out, key=lambda d: d["published"], reverse=True)[:limit]


def bases(store: FundamentalsStore, symbol: str, table: Mapping[str, tuple[Predecessor, ...]] = PREDECESSORS) -> list[str]:
    found = set(store.bases(symbol))
    for earlier in chain(symbol, table):
        found |= set(store.bases(earlier.symbol))
    return sorted(found)
