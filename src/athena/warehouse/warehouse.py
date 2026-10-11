"""The market database: every listed stock's and ETF's daily bars, which days have been loaded, and what each symbol is. One
DuckDB file, built each evening from the exchange's price files and read by charts, screens and analysts alike."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import Bar
from athena.pit.bhavcopy import SOURCE, Row

DEFAULT_PATH = Path(".athena") / "warehouse.duckdb"
LOADED, NO_SESSION, PENDING = "loaded", "no_session", "pending"
STOCK, ETF, SME, OTHER = "stock", "etf", "sme", "other"
_UTC = timezone.utc


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(_UTC).replace(tzinfo=None)


def weekdays(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class Warehouse:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS bars (symbol VARCHAR NOT NULL, day DATE NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, "
            "close DOUBLE, volume DOUBLE, series VARCHAR, source VARCHAR, loaded_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol, day))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS days (day DATE PRIMARY KEY, status VARCHAR NOT NULL, rows INTEGER NOT NULL, loaded_at TIMESTAMP NOT NULL)"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS universe (symbol VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, name VARCHAR, isin VARCHAR, updated_at TIMESTAMP NOT NULL)"
        )

    # ---- writing
    def upsert_day(self, day: date, rows: Mapping[str, Row], now: datetime) -> int:
        """Replace everything held for `day` with `rows`, and record the day as loaded. All or nothing."""
        stamp = _naive(now)
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM bars WHERE day = ?", [day])
            self._con.executemany(
                "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [[r.symbol, day, r.open, r.high, r.low, r.close, r.volume, r.series, SOURCE, stamp] for r in rows.values()],
            )
            self._con.execute("INSERT OR REPLACE INTO days VALUES (?, ?, ?, ?)", [day, LOADED, len(rows), stamp])
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(rows)

    def mark_day(self, day: date, status: str, now: datetime) -> None:
        """Record a day with no rows: `no_session` (a weekend or holiday) or `pending` (the file may only be late)."""
        if status not in (NO_SESSION, PENDING):
            raise ValueError(f"unknown status {status!r}")
        self._con.execute("INSERT OR REPLACE INTO days VALUES (?, ?, 0, ?)", [day, status, _naive(now)])

    def set_universe(self, rows: Iterable[tuple[str, str, str, str]], now: datetime) -> int:
        """Replace the universe with (symbol, kind, name, isin) rows."""
        stamp = _naive(now)
        data = [[s, k, n, i, stamp] for s, k, n, i in rows]
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM universe")
            self._con.executemany("INSERT INTO universe VALUES (?, ?, ?, ?, ?)", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    # ---- reading
    def day_status(self, first: date, last: date) -> dict[date, str]:
        rows = self._con.execute("SELECT day, status FROM days WHERE day BETWEEN ? AND ?", [first, last]).fetchall()
        return {day: status for day, status in rows}

    def gaps(self, first: date, last: date) -> list[date]:
        """Weekdays in the range that are not settled: never tried, or only pending."""
        known = self.day_status(first, last)
        return [day for day in weekdays(first, last) if known.get(day, PENDING) == PENDING]

    def bars(self, symbol: str, since: date | None = None, until: date | None = None) -> list[Bar]:
        """The symbol's bars as the exchange printed them, oldest first, stamped as the price adapters stamp them (IST midnight
        written as UTC)."""
        sql = "SELECT day, open, high, low, close, volume, source, loaded_at FROM bars WHERE symbol = ?"
        params: list[Any] = [symbol]
        if since is not None:
            sql += " AND day >= ?"
            params.append(since)
        if until is not None:
            sql += " AND day <= ?"
            params.append(until)
        out = []
        for day, o, h, low, c, v, source, loaded in self._con.execute(sql + " ORDER BY day", params).fetchall():
            stamp = datetime(day.year, day.month, day.day, tzinfo=_UTC) - timedelta(hours=5, minutes=30)
            out.append(Bar(symbol, stamp, o, h, low, c, v, loaded.replace(tzinfo=_UTC), source))
        return out

    def symbols_on(self, day: date) -> dict[str, str]:
        """Every symbol with a bar on `day`, with the series it traded in."""
        return {s: series for s, series in self._con.execute("SELECT symbol, series FROM bars WHERE day = ?", [day]).fetchall()}

    def last_loaded_day(self) -> date | None:
        row = self._con.execute("SELECT max(day) FROM days WHERE status = ?", [LOADED]).fetchone()
        return row[0] if row else None

    def universe(self, kind: str | None = None) -> list[dict[str, str]]:
        sql, params = "SELECT symbol, kind, name, isin FROM universe", []
        if kind:
            sql, params = sql + " WHERE kind = ?", [kind]
        return [{"symbol": s, "kind": k, "name": n, "isin": i} for s, k, n, i in self._con.execute(sql + " ORDER BY symbol", params).fetchall()]

    def status(self, first: date | None = None, last: date | None = None) -> dict[str, Any]:
        """A summary for the nightly job and the dashboard: how far the data goes, what is missing, what it covers."""
        counts = dict(self._con.execute("SELECT status, count(*) FROM days GROUP BY status").fetchall())
        latest = self.last_loaded_day()
        row = self._con.execute("SELECT min(day), max(day), count(*) FROM bars").fetchone()
        universe = dict(self._con.execute("SELECT kind, count(*) FROM universe GROUP BY kind").fetchall())
        loaded_at = self._con.execute("SELECT max(loaded_at) FROM days").fetchone()[0]
        out: dict[str, Any] = {
            "days": counts, "last_loaded_day": latest, "bars_from": row[0], "bars_to": row[1], "bar_rows": row[2],
            "symbols_on_last_day": len(self.symbols_on(latest)) if latest else 0, "universe": universe,
            "last_load": loaded_at.replace(tzinfo=_UTC) if loaded_at else None,
        }
        if first is not None and last is not None:
            out["gaps"] = self.gaps(first, last)
        return out

    def close(self) -> None:
        self._con.close()
