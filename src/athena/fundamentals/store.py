"""The fundamentals database: statements by company, basis, statement and period, the filings seen and the state of each company's ingest.

One DuckDB file. Python writes in short bursts (open, write one filing, close); the terminal's server opens it read-only. Times are naive UTC."""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import AthenaError
from athena.fundamentals import lines as spec
from athena.fundamentals.rows import Statements
from athena.fundamentals.shareholding import Holding

DEFAULT_PATH = Path(".athena") / "fundamentals.duckdb"
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS companies (symbol TEXT PRIMARY KEY, name TEXT, isin TEXT, industry TEXT, layout TEXT, shares DOUBLE, updated TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS filings (url TEXT PRIMARY KEY, symbol TEXT, basis TEXT, period_end DATE, annual BOOLEAN, filed TIMESTAMP,"
    " status TEXT, error TEXT, fetched TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS statement_lines (symbol TEXT, basis TEXT, statement TEXT, period_start DATE, period_end DATE, key TEXT,"
    " value DOUBLE, filing_url TEXT, filed TIMESTAMP, source TEXT)",
    "CREATE TABLE IF NOT EXISTS shareholding (symbol TEXT, period_end DATE, category TEXT, shares DOUBLE, holders DOUBLE, filing_url TEXT, filed TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS ingest_runs (symbol TEXT PRIMARY KEY, status TEXT, finished TIMESTAMP, filings INTEGER, error TEXT,"
    " blocked_until TIMESTAMP)",
)
KEY = "symbol = ? AND basis = ? AND statement = ? AND period_start = ? AND period_end = ? AND key = ?"


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def open_read(path: str | Path = DEFAULT_PATH, tries: int = 5, pause: float = 0.4, sleep: Callable[[float], None] = time.sleep) -> "FundamentalsStore":
    """The store opened read-only. A missing file is reported at once; a locked one is retried (see `FundamentalsStore`)."""
    if str(path) != ":memory:" and not Path(path).exists():
        raise AthenaError(f"no fundamentals store at {path}; run python -m athena.fundamentals.ingest first")
    return FundamentalsStore(path, read_only=True, tries=tries, pause=pause, sleep=sleep)


class FundamentalsStore:
    def __init__(
        self, path: str | Path = DEFAULT_PATH, read_only: bool = False, tries: int = 8, pause: float = 0.5, sleep: Callable[[float], None] = time.sleep
    ):
        """DuckDB lets one process hold the file, and the ingest (writing) and the terminal's page builder (reading) both open it for short moments,
        so a locked file is retried `tries` times, `pause` seconds apart, before it is reported as busy."""
        if str(path) != ":memory:" and not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(max(tries, 1)):
            try:
                self._con = duckdb.connect(str(path), read_only=read_only)
                break
            except duckdb.IOException as exc:
                if attempt >= tries - 1:
                    raise AthenaError(f"the fundamentals store is busy: {exc}") from exc
                sleep(pause)
        if not read_only:
            for statement in SCHEMA:
                self._con.execute(statement)

    def __enter__(self) -> "FundamentalsStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._con.close()

    # ---- writes
    def save_company(self, symbol: str, name: str, isin: str, industry: str, layout: str, shares: float | None) -> None:
        self._con.execute("INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?, ?, ?, ?)", [symbol, name, isin, industry, layout, shares, now()])

    def save_statements(self, st: Statements, url: str, filed: datetime, source: str = "nse-xbrl") -> int:
        """Store a filing's rows. A row already held from a filing made later is kept; one from an earlier filing is replaced. Returns the number of rows offered."""
        filed = _utc(filed)
        rows: list[tuple[Any, ...]] = []
        for statement, periods in ((spec.INCOME, st.income), (spec.CASHFLOW, st.cashflow), (spec.BANKING, st.banking)):
            for (start, end), got in periods.items():
                rows += [(st.symbol, st.basis, statement, start, end, key, value) for key, value in got.items()]
        for end, got in st.balance.items():
            rows += [(st.symbol, st.basis, spec.BALANCE, end, end, key, value) for key, value in got.items()]
        if not rows:
            return 0
        self._con.executemany(f"DELETE FROM statement_lines WHERE {KEY} AND filed < ?", [(*row[:6], filed) for row in rows])
        self._con.executemany(
            f"INSERT INTO statement_lines SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM statement_lines WHERE {KEY})",
            [(*row, url, filed, source, *row[:6]) for row in rows],
        )
        return len(rows)

    def forget_filing(self, url: str) -> None:
        """Drop every row a filing gave, so it can be read again with a changed mapping."""
        self._con.execute("DELETE FROM statement_lines WHERE filing_url = ?", [url])

    def save_holding(self, symbol: str, holding: Holding, url: str, filed: datetime) -> None:
        """Store one quarter's pattern, replacing what was held for that symbol and quarter."""
        self._con.execute("DELETE FROM shareholding WHERE symbol = ? AND period_end = ?", [symbol, holding.period_end])
        self._con.executemany(
            "INSERT INTO shareholding VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(symbol, holding.period_end, key, g.shares, g.holders, url, _utc(filed)) for key, g in holding.groups.items()],
        )

    def record_filing(
        self, url: str, symbol: str, basis: str, period_end: date, annual: bool, filed: datetime, status: str, error: str = ""
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO filings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [url, symbol, basis, period_end, annual, _utc(filed), status, error, now()],
        )

    def set_run(self, symbol: str, status: str, filings: int = 0, error: str = "", blocked_until: datetime | None = None) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO ingest_runs VALUES (?, ?, ?, ?, ?, ?)",
            [symbol, status, now(), filings, error, blocked_until and _utc(blocked_until)],
        )

    # ---- reads
    def filing_status(self, url: str) -> str | None:
        row = self._con.execute("SELECT status FROM filings WHERE url = ?", [url]).fetchone()
        return row[0] if row else None

    def lines(self, symbol: str, basis: str, statement: str) -> dict[tuple[date, date], dict[str, float]]:
        out: dict[tuple[date, date], dict[str, float]] = {}
        for start, end, key, value in self._con.execute(
            "SELECT period_start, period_end, key, value FROM statement_lines WHERE symbol = ? AND basis = ? AND statement = ?"
            " ORDER BY period_end, period_start",
            [symbol, basis, statement],
        ).fetchall():
            out.setdefault((start, end), {})[key] = value
        return out

    def holdings(self, symbol: str) -> dict[date, dict[str, tuple[float, float | None]]]:
        """Each stored quarter's holder groups as `(shares, holders)`, by quarter end."""
        out: dict[date, dict[str, tuple[float, float | None]]] = {}
        for end, key, shares, holders in self._con.execute(
            "SELECT period_end, category, shares, holders FROM shareholding WHERE symbol = ? ORDER BY period_end", [symbol]
        ).fetchall():
            out.setdefault(end, {})[key] = (shares, holders)
        return out

    def bases(self, symbol: str) -> list[str]:
        return [r[0] for r in self._con.execute("SELECT DISTINCT basis FROM statement_lines WHERE symbol = ? ORDER BY basis", [symbol]).fetchall()]

    def company(self, symbol: str) -> dict[str, Any] | None:
        row = self._con.execute("SELECT symbol, name, isin, industry, layout, shares, updated FROM companies WHERE symbol = ?", [symbol]).fetchone()
        return dict(zip(("symbol", "name", "isin", "industry", "layout", "shares", "updated"), row)) if row else None

    def run(self, symbol: str) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT symbol, status, finished, filings, error, blocked_until FROM ingest_runs WHERE symbol = ?", [symbol]
        ).fetchone()
        return dict(zip(("symbol", "status", "finished", "filings", "error", "blocked_until"), row)) if row else None

    def blocked_until(self) -> datetime | None:
        row = self._con.execute("SELECT max(blocked_until) FROM ingest_runs WHERE status = 'blocked'").fetchone()
        return row[0] if row else None

    def coverage(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT symbol, basis, count(DISTINCT (period_start, period_end)) AS periods, min(period_end), max(period_end) FROM statement_lines"
            " GROUP BY symbol, basis ORDER BY symbol, basis"
        ).fetchall()
        return [dict(zip(("symbol", "basis", "periods", "first", "last"), r)) for r in rows]
