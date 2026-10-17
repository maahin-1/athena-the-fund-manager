"""Symbols that changed: a company renamed, or one whose listing continues under a new symbol, keeps one history on its page.

The exchange files results under the symbol of the day, so Zomato's statements to 2024 sit under ZOMATO and Eternal's from 2025 under ETERNAL. Each entry
here names the earlier symbol and says in words what changed, which the page shows; where the old and the new symbol both hold a period, the new one
wins. The table is kept by hand: a succession is added when a company's statements are seen to split."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from athena.fundamentals.store import FundamentalsStore


@dataclass(frozen=True)
class Predecessor:
    symbol: str
    note: str


PREDECESSORS: dict[str, tuple[Predecessor, ...]] = {
    "ETERNAL": (Predecessor("ZOMATO", "Zomato was renamed Eternal in 2025."),),
    "TMPV": (
        Predecessor(
            "TATAMOTORS",
            "Tata Motors Passenger Vehicles is the listed company that was Tata Motors; its commercial-vehicle business was demerged in October 2025, "
            "so periods before that include it.",
        ),
    ),
    "TATACONSUM": (Predecessor("TATAGLOBAL", "Tata Global Beverages was renamed Tata Consumer Products in 2020."),),
    "SHRIRAMFIN": (
        Predecessor("SRTRANSFIN", "Shriram Transport Finance merged with Shriram City Union Finance in December 2022; earlier periods are Shriram Transport's alone."),
    ),
    "BAJAJ-AUTO": (Predecessor("BAJAJAUTO", "The same company under a spelling without the hyphen."),),
}


def chain(symbol: str, table: dict[str, tuple[Predecessor, ...]] = PREDECESSORS, _seen: set[str] | None = None) -> list[Predecessor]:
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
    store: FundamentalsStore, symbol: str, basis: str, statement: str, table: dict[str, tuple[Predecessor, ...]] = PREDECESSORS
) -> dict[tuple[date, date], dict[str, float]]:
    """The statement's rows by period for the symbol and its predecessors. A period the symbol itself holds replaces the same period of an earlier one."""
    merged: dict[tuple[date, date], dict[str, float]] = {}
    for earlier in chain(symbol, table):
        merged.update(store.lines(earlier.symbol, basis, statement))
    merged.update(store.lines(symbol, basis, statement))
    return merged


def used(store: FundamentalsStore, symbol: str, basis: str, table: dict[str, tuple[Predecessor, ...]] = PREDECESSORS) -> list[dict[str, str]]:
    """The predecessors that actually add periods under this basis, each with its note and the span it covers, for the page to show."""
    out = []
    for earlier in chain(symbol, table):
        got = store.lines(earlier.symbol, basis, "income") or store.lines(earlier.symbol, basis, "balance")
        if got:
            ends = sorted(end for _, end in got)
            out.append({"symbol": earlier.symbol, "note": earlier.note, "first": ends[0].isoformat(), "last": ends[-1].isoformat()})
    return out


def bases(store: FundamentalsStore, symbol: str, table: dict[str, tuple[Predecessor, ...]] = PREDECESSORS) -> list[str]:
    found = set(store.bases(symbol))
    for earlier in chain(symbol, table):
        found |= set(store.bases(earlier.symbol))
    return sorted(found)
