"""The fundamentals database: statements by company, basis, statement and period, the filings seen and the state of each company's ingest.

One DuckDB file. Python writes in short bursts (open, write one filing, close); the terminal's server opens it read-only. Times are naive UTC."""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
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
    "CREATE TABLE IF NOT EXISTS documents (symbol TEXT, kind TEXT, title TEXT, category TEXT, published TIMESTAMP, url TEXT, size TEXT)",
    "CREATE TABLE IF NOT EXISTS ingest_runs (symbol TEXT PRIMARY KEY, status TEXT, finished TIMESTAMP, filings INTEGER, error TEXT,"
    " blocked_until TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS segments (url TEXT, symbol TEXT, basis TEXT, period_end DATE, filed TIMESTAMP, idx INTEGER, name TEXT, revenue DOUBLE, profit DOUBLE,"
    " assets DOUBLE, liabilities DOUBLE)",
    "CREATE TABLE IF NOT EXISTS share_counts (url TEXT PRIMARY KEY, symbol TEXT, basis TEXT, period_end DATE, filed TIMESTAMP, declared DOUBLE, from_profit DOUBLE)",
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
        self, path: str | Path = DEFAULT_PATH, read_only: bool = False, tries: int | None = None, pause: float = 0.5, sleep: Callable[[float], None] = time.sleep
    ):
        """DuckDB lets one process hold the file, and the ingest (writing) and the terminal's page builder (reading) both open it for short moments,
        so a locked file is retried `tries` times, `pause` seconds apart, before it is reported as busy."""
        if tries is None:  # a page waits a few seconds; a long job waits two minutes for another writer to finish (a four-second wait is what crashed the documents job)
            tries = 8 if read_only else 240
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
        """Add or refresh a company. `shares` is only the first guess for a company not held yet: afterwards the count is chosen from the share counts of its filings
        (`athena.fundamentals.shares`), never overwritten by whichever filing happened to be read last."""
        self._con.execute(
            "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (symbol) DO UPDATE SET name = excluded.name, isin = excluded.isin, industry = excluded.industry,"
            " layout = excluded.layout, updated = excluded.updated, shares = COALESCE(companies.shares, excluded.shares)",
            [symbol, name, isin, industry, layout, shares, now()],
        )

    def save_share_count(self, url: str, symbol: str, basis: str, period_end: date, filed: datetime, declared: float | None, from_profit: float | None) -> None:
        self._con.execute("INSERT OR REPLACE INTO share_counts VALUES (?, ?, ?, ?, ?, ?, ?)", [url, symbol, basis, period_end, _utc(filed), declared, from_profit])

    def share_counts(self, symbol: str) -> list[dict[str, Any]]:
        """The share counts each of the company's filings states, newest period first."""
        cols = ("url", "basis", "period_end", "filed", "declared", "from_profit")
        rows = self._con.execute(
            "SELECT url, basis, period_end, filed, declared, from_profit FROM share_counts WHERE symbol = ? ORDER BY period_end DESC, filed DESC", [symbol]
        ).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def save_segments(self, url: str, symbol: str, basis: str, period_end: date, filed: datetime, segments: Sequence[Any]) -> None:
        """A filing's reportable segments (replacing any held for the same filing). `segments` are `athena.fundamentals.segments.Segment`."""
        self._con.execute("DELETE FROM segments WHERE url = ?", [url])
        self._con.executemany(
            "INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(url, symbol, basis, period_end, _utc(filed), s.index, s.name, s.revenue, s.profit, s.assets, s.liabilities) for s in segments],
        )

    def segments(self, symbol: str, basis: str) -> tuple[date | None, list[dict[str, Any]]]:
        """The segments of the company's newest annual filing that reports several, as `(period end, rows)`; `(None, [])` when none does."""
        got = self._con.execute(
            "SELECT url, period_end FROM segments WHERE symbol = ? AND basis = ? ORDER BY period_end DESC, filed DESC LIMIT 1", [symbol, basis]
        ).fetchone()
        if got is None:
            return None, []
        cols = ("idx", "name", "revenue", "profit", "assets", "liabilities")
        rows = self._con.execute("SELECT idx, name, revenue, profit, assets, liabilities FROM segments WHERE url = ? ORDER BY idx", [got[0]]).fetchall()
        return got[1], [dict(zip(cols, r)) for r in rows]

    def set_shares(self, symbol: str, shares: float | None) -> None:
        self._con.execute("UPDATE companies SET shares = ? WHERE symbol = ?", [shares, symbol])

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
        rows = [(symbol, holding.period_end, key, g.shares, g.holders, url, _utc(filed)) for key, g in holding.groups.items()]
        if rows:
            self._con.executemany("INSERT INTO shareholding VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def save_documents(self, symbol: str, kinds: Sequence[str], docs: Sequence[Any]) -> None:
        """Replace the symbol's documents of these kinds with `docs` (what the exchange lists now is what is kept)."""
        for kind in kinds:
            self._con.execute("DELETE FROM documents WHERE symbol = ? AND kind = ?", [symbol, kind])
        rows = [(symbol, d.kind, d.title, d.category, _utc(d.published), d.url, d.size) for d in docs if d.kind in kinds]
        if rows:
            self._con.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

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

    def filing_state(self, url: str) -> tuple[str | None, datetime | None]:
        """`(status, when it was recorded)` for a filing; `(None, None)` when it was never tried."""
        row = self._con.execute("SELECT status, fetched FROM filings WHERE url = ?", [url]).fetchone()
        return (row[0], row[1]) if row else (None, None)

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

    def holding_filed(self, symbol: str) -> dict[date, datetime]:
        """When each stored quarter's shareholding pattern was filed, by quarter end."""
        return {end: when for end, when in self._con.execute("SELECT period_end, min(filed) FROM shareholding WHERE symbol = ? GROUP BY period_end", [symbol]).fetchall()}

    def documents(self, symbol: str, kind: str, limit: int = 100) -> list[dict[str, Any]]:
        """The symbol's documents of one kind, newest first."""
        rows = self._con.execute(
            "SELECT title, category, published, url, size FROM documents WHERE symbol = ? AND kind = ? ORDER BY published DESC LIMIT ?", [symbol, kind, limit]
        ).fetchall()
        return [dict(zip(("title", "category", "published", "url", "size"), r)) for r in rows]

    def bases(self, symbol: str) -> list[str]:
        return [r[0] for r in self._con.execute("SELECT DISTINCT basis FROM statement_lines WHERE symbol = ? ORDER BY basis", [symbol]).fetchall()]

    def last_filed(self, symbol: str, basis: str) -> datetime | None:
        """When the newest filing behind the symbol's lines under this basis was made."""
        row = self._con.execute("SELECT max(filed) FROM statement_lines WHERE symbol = ? AND basis = ?", [symbol, basis]).fetchone()
        return row[0] if row else None

    def filing_dates(self, symbol: str) -> dict[date, date]:
        """The day each reporting period's results were first filed (shareholding patterns are not results and are left out)."""
        rows = self._con.execute("SELECT period_end, min(filed) FROM filings WHERE symbol = ? AND basis <> 'shp' AND filed IS NOT NULL GROUP BY period_end", [symbol]).fetchall()
        return {period: filed.date() for period, filed in rows}

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
