"""Annual statement series from `.athena/fundamentals.duckdb`, as they were known on a date.

A company's annual figures are the income statement and cash flow for periods of about a year, and the balance sheet on the day each of those years ended. Only rows whose filing was made on or before
`as_of` are used, so a case dated in the past sees what existed then. The store keeps one value per line and period (the latest filing's); a later restatement of an old year therefore replaces the
first figure, which this loader cannot undo: the limitation is real and is said in the output that uses it.

Consolidated figures are used when they cover at least as many years as the standalone ones. Banks, lenders and insurers have different statements and are not read here."""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any

import duckdb

from athena.fundamentals.store import DEFAULT_PATH

LAYOUT = "indas"  # industrial and service companies: the statements the detectors are written for
MIN_YEAR_DAYS, MAX_YEAR_DAYS = 350, 380
MIN_QUARTER_DAYS, MAX_QUARTER_DAYS = 80, 100
CONSECUTIVE_DAYS = (330, 400)  # years that follow each other end 11 to 13 months apart


@dataclass(frozen=True)
class Year:
    fy_end: date
    income: Mapping[str, float]
    balance: Mapping[str, float]
    cashflow: Mapping[str, float]


@dataclass
class Annuals:
    symbol: str
    name: str
    industry: str
    layout: str
    basis: str
    years: list[Year] = field(default_factory=list)
    quarters: dict[date, dict[str, float]] = field(default_factory=dict)  # quarter end -> {"sales": ..., "net_profit": ...}; the quarter that ends on a year end is that year's last

    def run(self, n: int) -> list[Year] | None:
        """The last `n` years if they follow each other, else None."""
        if len(self.years) < n:
            return None
        tail = self.years[-n:]
        for a, b in zip(tail, tail[1:]):
            if not CONSECUTIVE_DAYS[0] <= (b.fy_end - a.fy_end).days <= CONSECUTIVE_DAYS[1]:
                return None
        return tail


def connect(path: Path | str = DEFAULT_PATH, tries: int = 10, pause: float = 1.0) -> duckdb.DuckDBPyConnection:
    """A read-only connection, tried again while the ingest job holds the file."""
    last: Exception | None = None
    for _ in range(tries):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException as exc:
            last = exc
            time.sleep(pause)
    raise last  # type: ignore[misc]


def _end_of(day: date) -> datetime:
    return datetime.combine(day, dtime.max)


def _merged(raw: Mapping[tuple[str, str], Any], names: Sequence[str], basis: str, depth: int) -> dict[Any, Any]:
    """The rows of `names` (earliest symbol first, the company's own last) under one basis; for a period both hold, the later symbol's rows win. `depth` is how many levels of a nested dict to merge."""
    out: dict[Any, Any] = {}
    for name in names:
        part = raw.get((name, basis)) or {}
        for k, v in part.items():
            if depth > 1:
                out.setdefault(k, {}).update(_merged({(name, basis): v}, [name], basis, depth - 1))
            else:
                out[k] = v
    return out


def load(
    con: duckdb.DuckDBPyConnection, as_of: date, symbols: Sequence[str] | None = None, layout: str = LAYOUT, predecessors: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Annuals]:
    """Annual series of every company (or the symbols given) of the layout, as known on `as_of`. A company renamed since its filings were made has them under its old symbol; they are joined to the
    current symbol through the exchange's rename list (`athena.fundamentals.history`), the current symbol's own periods winning where both hold one. The join uses an identity known now, not a return, so it
    brings no future information about the company's results."""
    from athena.fundamentals.history import PREDECESSORS, chain

    table = PREDECESSORS if predecessors is None else predecessors
    stamp = _end_of(as_of)
    companies = {r[0]: r[1:] for r in con.execute("SELECT symbol, name, industry, layout FROM companies WHERE layout = ?", [layout]).fetchall()}
    wanted = set(companies) if symbols is None else set(symbols) & set(companies)
    if not wanted:
        return {}
    names = {s: [p.symbol for p in chain(s, table)] + [s] for s in wanted}  # earliest symbol first
    sources = {n for ns in names.values() for n in ns}
    rows = con.execute(
        "SELECT symbol, basis, statement, period_start, period_end, key, value FROM statement_lines WHERE filed <= ? AND statement IN ('income', 'balance', 'cashflow')"
        " AND ((statement <> 'balance' AND period_end - period_start BETWEEN ? AND ?) OR (statement = 'balance' AND period_start = period_end))",
        [stamp, MIN_YEAR_DAYS, MAX_YEAR_DAYS],
    ).fetchall()
    # (symbol, basis) -> {statement: {period_end: {key: value}}}
    raw: dict[tuple[str, str], dict[str, dict[date, dict[str, float]]]] = {}
    for symbol, basis, statement, _start, end, key, value in rows:
        if symbol in sources and value is not None:
            raw.setdefault((symbol, basis), {}).setdefault(statement, {}).setdefault(end, {})[key] = float(value)
    quarters = _quarters(con, stamp, sources)
    out: dict[str, Annuals] = {}
    merged_away = {n for ns in names.values() for n in ns[:-1]}  # an old symbol is part of its successor, not a company of its own
    for symbol in wanted - merged_away:
        best: Annuals | None = None
        name, industry, lay = companies[symbol]
        for basis in ("consolidated", "standalone"):
            statements = _merged(raw, names[symbol], basis, 2)
            if not statements:
                continue
            years = _years(statements)
            if best is None or len(years) > len(best.years):
                best = Annuals(symbol, name or symbol, industry or "", lay, basis, years)
        if best is not None and best.years:
            joined: dict[date, dict[str, float]] = {}
            for earlier in names[symbol]:
                for end, v in quarters.get((earlier, best.basis), {}).items():
                    joined.setdefault(end, {}).update(v)
            best.quarters = dict(sorted(joined.items()))
            out[symbol] = best
    return out


def _years(statements: Mapping[str, Mapping[date, Mapping[str, float]]]) -> list[Year]:
    income, cash, balance = statements.get("income", {}), statements.get("cashflow", {}), statements.get("balance", {})
    # a year needs its income statement; the balance sheet of its last day and the cash flow are held for fewer years, and a measure that needs a missing statement is simply not computed
    return [Year(end, income[end], balance.get(end, {}), cash.get(end, {})) for end in sorted(income)]


def _quarters(con: duckdb.DuckDBPyConnection, stamp: datetime, wanted: set[str]) -> dict[tuple[str, str], dict[date, dict[str, float]]]:
    rows = con.execute(
        "SELECT symbol, basis, period_end, key, value FROM statement_lines WHERE filed <= ? AND statement = 'income' AND key IN ('sales', 'net_profit') AND period_end - period_start BETWEEN ? AND ?",
        [stamp, MIN_QUARTER_DAYS, MAX_QUARTER_DAYS],
    ).fetchall()
    out: dict[tuple[str, str], dict[date, dict[str, float]]] = {}
    for symbol, basis, end, key, value in rows:
        if symbol in wanted and value is not None:
            out.setdefault((symbol, basis), {}).setdefault(end, {})[key] = float(value)
    return out
