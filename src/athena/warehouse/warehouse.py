"""The market database: every listed stock's and ETF's daily bars, which days have been loaded, and what each symbol is. One
DuckDB file, built each evening from the exchange's price files and read by charts, screens and analysts alike."""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import Bar
from athena.pit.bhavcopy import SOURCE, Row
from athena.price_integrity.actions import CorporateAction, parse_subject
from athena.warehouse.screen import SCREEN_COLUMNS

DEFAULT_PATH = Path(".athena") / "warehouse.duckdb"
LOADED, NO_SESSION, PENDING = "loaded", "no_session", "pending"
STATEMENT_OK, STATEMENT_EMPTY, STATEMENT_ERROR = "ok", "empty", "error"
STOCK, ETF, SME, OTHER = "stock", "etf", "sme", "other"
_UTC = timezone.utc
MAX_HOPS = 5  # a symbol renamed more than this many times in a row is read as a loop


@dataclass(frozen=True)
class Succession:
    """One exchange rename as the warehouse has checked it against its own bars (see `athena.warehouse.renames`)."""

    old: str
    new: str
    change_day: date
    company: str
    event: str  # wording of a demerger or scheme near the rename, or ""
    joinable: bool  # the prices join: adjacent bars, a small step
    retired: bool  # the old symbol stopped trading at the rename
    reason: str  # why the prices do not join, or ""
    gap_days: int | None
    step: float | None  # first new close over last old close, minus one, as printed
    last_old_day: date | None
    first_new_day: date | None


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(_UTC).replace(tzinfo=None)


def weekdays(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _open(path: str | Path, read_only: bool, wait_seconds: float, sleep: Callable[[float], None]) -> duckdb.DuckDBPyConnection:
    """DuckDB lets one process write, or many read, never both. A process that finds the file in use for the other purpose waits
    and tries again for up to `wait_seconds` (a long job holds the file only for its short writes), then raises."""
    waited = 0.0
    while True:
        try:
            return duckdb.connect(str(path), read_only=read_only)
        except duckdb.IOException:
            if waited >= wait_seconds:
                raise
            sleep(1.0)
            waited += 1.0


class Warehouse:
    def __init__(
        self, path: str | Path = DEFAULT_PATH, read_only: bool = False, wait_seconds: float = 0, sleep: Callable[[float], None] = time.sleep
    ):
        """`read_only` for charts, screens and reports, which may run beside a job that writes in short bursts: open, read, close."""
        if str(path) != ":memory:" and not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = _open(path, read_only, wait_seconds, sleep)
        if not read_only:
            self._create_tables()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _create_tables(self) -> None:
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
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS index_bars (index_name VARCHAR NOT NULL, day DATE NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, "
            "close DOUBLE, pe DOUBLE, pb DOUBLE, div_yield DOUBLE, PRIMARY KEY (index_name, day))"
        )
        self._con.execute("CREATE TABLE IF NOT EXISTS index_days (day DATE PRIMARY KEY, rows INTEGER NOT NULL, loaded_at TIMESTAMP NOT NULL)")
        columns = ", ".join(f"{name} {sql_type}" for name, sql_type in SCREEN_COLUMNS.items())
        self._con.execute(f"CREATE TABLE IF NOT EXISTS screen ({columns}, built_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol))")
        held = {r[0] for r in self._con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'screen'").fetchall()}
        for name, sql_type in SCREEN_COLUMNS.items():
            if name not in held:  # a table made before this column existed
                self._con.execute(f"ALTER TABLE screen ADD COLUMN {name} {sql_type}")
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS packets (symbol VARCHAR NOT NULL, kind VARCHAR NOT NULL, data_as_of DATE NOT NULL, built_at TIMESTAMP NOT NULL, "
            "flags VARCHAR NOT NULL, payload VARCHAR NOT NULL, PRIMARY KEY (symbol, kind))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS statements (symbol VARCHAR PRIMARY KEY, fetched_at TIMESTAMP, tried_at TIMESTAMP NOT NULL, "
            "status VARCHAR NOT NULL, error VARCHAR, attempts INTEGER NOT NULL, payload VARCHAR)"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS actions (symbol VARCHAR NOT NULL, ex_date DATE NOT NULL, subject VARCHAR NOT NULL, face_value DOUBLE, "
            "series VARCHAR, isin VARCHAR, fetched_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol, ex_date, subject))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS symbol_renames (old VARCHAR NOT NULL, new VARCHAR NOT NULL, change_day DATE NOT NULL, company VARCHAR, "
            "last_old_day DATE, first_new_day DATE, gap_days INTEGER, step DOUBLE, joinable BOOLEAN NOT NULL, retired BOOLEAN NOT NULL, "
            "event VARCHAR, reason VARCHAR, checked_at TIMESTAMP NOT NULL, PRIMARY KEY (old, new, change_day))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS index_members (index_name VARCHAR NOT NULL, symbol VARCHAR NOT NULL, industry VARCHAR, "
            "fetched_at TIMESTAMP NOT NULL, PRIMARY KEY (index_name, symbol))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS events (symbol VARCHAR NOT NULL, event_date DATE NOT NULL, purpose VARCHAR NOT NULL, description VARCHAR, "
            "company VARCHAR, fetched_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol, event_date, purpose))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS verdicts (symbol VARCHAR NOT NULL, made_at TIMESTAMP NOT NULL, data_as_of DATE NOT NULL, close DOUBLE, "
            "annual_period DATE, quarter DATE, payload VARCHAR NOT NULL, PRIMARY KEY (symbol, made_at))"
        )

    # ---- writing
    def upsert_day(self, day: date, rows: Mapping[str, Row], now: datetime) -> int:
        """Replace everything held for `day` with `rows`, and record the day as loaded. All or nothing."""
        stamp = _naive(now)
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM bars WHERE day = ?", [day])
            if rows:
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
            if data:
                self._con.executemany("INSERT INTO universe VALUES (?, ?, ?, ?, ?)", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def replace_actions(self, rows: Iterable[tuple[str, date, str, float | None, str, str]], first: date, last: date, now: datetime) -> int:
        """Replace every corporate action with an ex-date from `first` to `last` by `rows` (symbol, ex-date, the exchange's wording,
        face value, series, ISIN). An action the exchange has since withdrawn or reworded does not linger. All or nothing."""
        stamp = _naive(now)
        data = [[s, d, subject, fv, series, isin, stamp] for s, d, subject, fv, series, isin in rows]
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM actions WHERE ex_date BETWEEN ? AND ?", [first, last])
            if data:
                self._con.executemany("INSERT OR REPLACE INTO actions VALUES (?, ?, ?, ?, ?, ?, ?)", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def put_index_day(self, day: date, rows: Iterable[tuple[str, float | None, float | None, float | None, float | None, float | None, float | None, float | None]], now: datetime) -> int:
        """Replace the day's index rows (name, open, high, low, close, P/E, P/B, dividend yield) and record the day as loaded."""
        data = [[name, day, o, h, low, c, pe, pb, dy] for name, o, h, low, c, pe, pb, dy in rows]
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM index_bars WHERE day = ?", [day])
            if data:
                self._con.executemany("INSERT INTO index_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
            self._con.execute("INSERT OR REPLACE INTO index_days VALUES (?, ?, ?)", [day, len(data), _naive(now)])
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def put_screen(self, rows: Iterable[Mapping[str, Any]], now: datetime, replace_all: bool = False) -> int:
        """Store the flat screen rows. `replace_all` first drops every row held, so a symbol that has gone inactive does not linger."""
        names = list(SCREEN_COLUMNS)
        data = [[row[name] for name in names] + [_naive(now)] for row in rows]
        self._con.execute("BEGIN")
        try:
            if replace_all:
                self._con.execute("DELETE FROM screen")
            if data:
                marks = ", ".join("?" for _ in range(len(names) + 1))
                self._con.executemany(f"INSERT OR REPLACE INTO screen ({', '.join(names)}, built_at) VALUES ({marks})", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def put_packets(self, rows: Iterable[tuple[str, str, date, list[str], Mapping[str, Any]]], now: datetime) -> int:
        """Store (symbol, kind, the day of the last price, flags, packet) rows, replacing what was held for the same symbol and kind."""
        stamp = _naive(now)
        data = [[s, kind, day, stamp, json.dumps(flags), json.dumps(packet, sort_keys=True)] for s, kind, day, flags, packet in rows]
        if data:
            self._con.executemany("INSERT OR REPLACE INTO packets VALUES (?, ?, ?, ?, ?, ?)", data)
        return len(data)

    def put_verdict(
        self, symbol: str, now: datetime, data_as_of: date, close: float | None, annual_period: date | None, quarter: date | None,
        payload: Mapping[str, Any],
    ) -> None:
        """Keep one verdict with what it was made from (the day and close of the last price, the statements' latest annual period and
        quarter). Every verdict is kept; `latest_verdict` shows the newest."""
        self._con.execute(
            "INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [symbol, _naive(now), data_as_of, close, annual_period, quarter, json.dumps(payload, sort_keys=True)],
        )

    def put_statement(self, symbol: str, status: str, payload: Mapping[str, Any] | None, error: str, now: datetime) -> None:
        """Keep the result of one statements fetch: `ok` with the payload, `empty` (the source has none for this symbol), or `error`
        (the fetch failed; the attempt count grows until a fetch succeeds). A failed fetch keeps the payload and the time it was
        fetched, so old data never looks new."""
        if status not in (STATEMENT_OK, STATEMENT_EMPTY, STATEMENT_ERROR):
            raise ValueError(f"unknown status {status!r}")
        row = self._con.execute("SELECT attempts, payload, fetched_at FROM statements WHERE symbol = ?", [symbol]).fetchone()
        if status == STATEMENT_ERROR:
            attempts, body, fetched = (row[0] + 1 if row else 1), (row[1] if row else None), (row[2] if row else None)
        elif status == STATEMENT_OK:
            attempts, body, fetched = 0, json.dumps(payload, sort_keys=True), _naive(now)
        else:
            attempts, body, fetched = 0, None, None
        self._con.execute(
            "INSERT OR REPLACE INTO statements VALUES (?, ?, ?, ?, ?, ?, ?)", [symbol, fetched, _naive(now), status, error, attempts, body]
        )

    # ---- reading
    def screen_rows(self, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Every screen row (or those of the given kinds) as a dict, with `built_at` when it was computed."""
        sql = "SELECT * FROM screen"
        params: list[Any] = []
        if kinds is not None:
            wanted = list(kinds)
            sql += f" WHERE kind IN ({', '.join('?' for _ in wanted)})"
            params = wanted
        cursor = self._con.execute(sql + " ORDER BY symbol", params)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def packet(self, symbol: str, kind: str) -> dict[str, Any] | None:
        """A stored packet with where it came from: {"packet", "flags", "data_as_of", "built_at"}, or None."""
        row = self._con.execute("SELECT data_as_of, built_at, flags, payload FROM packets WHERE symbol = ? AND kind = ?", [symbol, kind]).fetchone()
        if row is None:
            return None
        return {"data_as_of": row[0], "built_at": row[1].replace(tzinfo=_UTC), "flags": json.loads(row[2]), "packet": json.loads(row[3])}

    def _verdict_rows(self, where: str, params: list[Any], limit: str = "") -> list[dict[str, Any]]:
        try:
            cursor = self._con.execute(
                f"SELECT symbol, made_at, data_as_of, close, annual_period, quarter, payload FROM verdicts {where} ORDER BY made_at DESC {limit}", params
            )
        except duckdb.CatalogException:  # a database built before verdicts existed, opened read-only
            return []
        return [
            {"symbol": s, "made_at": m.replace(tzinfo=_UTC), "data_as_of": d, "close": c, "annual_period": a, "quarter": q, "payload": json.loads(p)}
            for s, m, d, c, a, q, p in cursor.fetchall()
        ]

    def latest_verdict(self, symbol: str) -> dict[str, Any] | None:
        rows = self._verdict_rows("WHERE symbol = ?", [symbol], "LIMIT 1")
        return rows[0] if rows else None

    def verdict_history(self, symbol: str) -> list[dict[str, Any]]:
        """Every verdict kept for the symbol, newest first."""
        return self._verdict_rows("WHERE symbol = ?", [symbol])

    def verdict_symbols(self) -> list[str]:
        """The symbols that have at least one verdict, oldest-judged first (so a limited refresh serves the stalest first)."""
        try:
            rows = self._con.execute("SELECT symbol FROM verdicts GROUP BY symbol ORDER BY max(made_at)").fetchall()
        except duckdb.CatalogException:
            return []
        return [r[0] for r in rows]

    def packet_counts(self) -> dict[str, int]:
        return dict(self._con.execute("SELECT kind, count(*) FROM packets GROUP BY kind").fetchall())

    def index_days_loaded(self, first: date, last: date) -> set[date]:
        return {r[0] for r in self._con.execute("SELECT day FROM index_days WHERE day BETWEEN ? AND ?", [first, last]).fetchall()}

    def index_close(self, name: str) -> dict[date, float]:
        """The index's closing level by trading date: the benchmark series the risk and outcome figures read."""
        return {d: c for d, c in self._con.execute("SELECT day, close FROM index_bars WHERE index_name = ? AND close IS NOT NULL ORDER BY day", [name]).fetchall()}

    def index_valuation(self, name: str) -> dict[date, dict[str, float]]:
        """The index's P/E, P/B and dividend yield by trading date, in the shape the fundamentals packet reads. Days the exchange
        gives no figure for are left out."""
        rows = self._con.execute("SELECT day, pe, pb, div_yield FROM index_bars WHERE index_name = ? AND pe IS NOT NULL ORDER BY day", [name]).fetchall()
        return {d: {"pe": pe, "pb": pb, "div_yield": dy} for d, pe, pb, dy in rows}

    def statement(self, symbol: str) -> tuple[dict[str, Any], datetime] | None:
        """The symbol's statements payload and when it was fetched, or None when none is held."""
        row = self._con.execute("SELECT payload, fetched_at FROM statements WHERE symbol = ?", [symbol]).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return json.loads(row[0]), row[1].replace(tzinfo=_UTC)

    def statement_state(self) -> dict[str, tuple[str, datetime | None, datetime, int]]:
        """symbol -> (status, when the payload held was fetched or None, when it was last tried, failed attempts in a row)."""
        return {
            s: (status, fetched.replace(tzinfo=_UTC) if fetched else None, tried.replace(tzinfo=_UTC), n)
            for s, status, fetched, tried, n in self._con.execute(
                "SELECT symbol, status, fetched_at, tried_at, attempts FROM statements"
            ).fetchall()
        }

    def traded_value(self, day: date) -> dict[str, float]:
        """symbol -> close x volume on `day`: how liquid each was, for deciding what to refresh first."""
        return {s: v for s, v in self._con.execute("SELECT symbol, close * volume FROM bars WHERE day = ?", [day]).fetchall()}

    def actions(self, symbol: str) -> list[CorporateAction]:
        """The symbol's corporate actions read from the exchange's wording (splits, bonuses, demergers, rights, buybacks), oldest first."""
        out = []
        for day, subject, face_value in self._con.execute(
            "SELECT ex_date, subject, face_value FROM actions WHERE symbol = ? ORDER BY ex_date", [symbol]
        ).fetchall():
            action = parse_subject(day, subject)
            if action is not None:
                out.append(replace(action, face_value=face_value))
        return out

    def spans(self) -> dict[str, tuple[date, float, date, float]]:
        """symbol -> (first day, first close, last day, last close) of every symbol with bars, as printed."""
        return {
            s: (a, ac, b, bc)
            for s, a, ac, b, bc in self._con.execute("SELECT symbol, min(day), arg_min(close, day), max(day), arg_max(close, day) FROM bars GROUP BY symbol").fetchall()
        }

    def action_wordings(self, words: Iterable[str]) -> list[tuple[str, date, str]]:
        """(symbol, ex-date, wording) of every corporate action whose wording holds any of `words` (case does not matter)."""
        words = list(words)
        if not words:
            return []
        clause = " OR ".join("lower(subject) LIKE ?" for _ in words)
        return self._con.execute(f"SELECT symbol, ex_date, subject FROM actions WHERE {clause}", [f"%{w.lower()}%" for w in words]).fetchall()

    def replace_renames(self, rows: Iterable[Mapping[str, Any]], now: datetime) -> int:
        """Replace the whole rename table by `rows`. All or nothing, so a failed run keeps the last table."""
        stamp = _naive(now)
        data = [
            [r["old"], r["new"], r["change_day"], r["company"], r["last_old_day"], r["first_new_day"], r["gap_days"], r["step"], r["joinable"], r["retired"], r["event"], r["reason"], stamp]
            for r in rows
        ]
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM symbol_renames")
            if data:
                self._con.executemany("INSERT OR REPLACE INTO symbol_renames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def predecessors(self, symbol: str, joinable_only: bool = True) -> list[Succession]:
        """The renames that lead to `symbol`, oldest first: for prices (`joinable_only`) those whose bars join, for statements and search
        those whose old symbol is retired. A chain is followed back at most `MAX_HOPS` times and never through a symbol twice. Empty when
        the table does not exist or holds nothing for the symbol."""
        column = "joinable" if joinable_only else "retired"
        out: list[Succession] = []
        here, seen = symbol, {symbol}
        try:
            for _ in range(MAX_HOPS):
                row = self._con.execute(
                    "SELECT old, new, change_day, company, event, joinable, retired, reason, gap_days, step, last_old_day, first_new_day "
                    f"FROM symbol_renames WHERE new = ? AND {column} ORDER BY change_day DESC",
                    [here],
                ).fetchall()
                row = [r for r in row if r[0] not in seen]
                if not row:
                    break
                out.append(Succession(*row[0]))
                here = row[0][0]
                seen.add(here)
        except duckdb.CatalogException:
            return []  # a read-only open of a warehouse built before the table existed
        return out[::-1]

    def renames_status(self) -> dict[str, Any]:
        try:
            row = self._con.execute("SELECT count(*), count(*) FILTER (WHERE joinable), count(*) FILTER (WHERE retired), max(checked_at) FROM symbol_renames").fetchone()
        except duckdb.CatalogException:
            return {"renames": 0, "joinable": 0, "retired": 0, "checked_at": None}
        return {"renames": row[0], "joinable": row[1], "retired": row[2], "checked_at": row[3].replace(tzinfo=_UTC) if row[3] else None}

    def replace_events(self, events: Iterable[Mapping[str, Any]], first: date, now: datetime) -> int:
        """Replace every event dated `first` or later by `events` (dicts with symbol, day, purpose, description, company); earlier dates are kept. All or
        nothing."""
        stamp = _naive(now)
        data = [[e["symbol"], e["day"], e["purpose"], e["description"], e["company"], stamp] for e in events]
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM events WHERE event_date >= ?", [first])
            if data:
                self._con.executemany("INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?)", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def upcoming_results(self, symbol: str, today: date) -> list[date]:
        """Dates of the board meetings the exchange has announced to approve results, from today on, soonest first."""
        try:
            rows = self._con.execute(
                "SELECT DISTINCT event_date FROM events WHERE symbol = ? AND event_date >= ? AND purpose ILIKE '%result%' ORDER BY event_date", [symbol, today]
            ).fetchall()
        except duckdb.CatalogException:
            return []
        return [r[0] for r in rows]

    def events_status(self) -> dict[str, Any]:
        try:
            row = self._con.execute("SELECT count(*), min(event_date), max(event_date), max(fetched_at) FROM events").fetchone()
        except duckdb.CatalogException:
            return {"events": 0, "first": None, "last": None, "fetched_at": None}
        return {"events": row[0], "first": row[1], "last": row[2], "fetched_at": row[3].replace(tzinfo=_UTC) if row[3] else None}

    def index_names(self) -> list[str]:
        """Every index the warehouse holds bars for."""
        return [r[0] for r in self._con.execute("SELECT DISTINCT index_name FROM index_bars ORDER BY 1").fetchall()]

    def replace_members(self, rows: Iterable[tuple[str, str, str]], now: datetime) -> int:
        """Replace the index membership table by `rows` (index, symbol, industry). All or nothing, so a failed run keeps the last table."""
        stamp = _naive(now)
        data = [[i, s, ind, stamp] for i, s, ind in rows]
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM index_members")
            if data:
                self._con.executemany("INSERT OR REPLACE INTO index_members VALUES (?, ?, ?, ?)", data)
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(data)

    def symbol_industries(self) -> dict[str, str]:
        """symbol -> its industry as the largest index that holds it gives it (the broad lists carry the exchange's current classification, the
        thematic ones sometimes an older one)."""
        try:
            rows = self._con.execute(
                "WITH size AS (SELECT index_name, count(*) AS n FROM index_members GROUP BY 1) "
                "SELECT m.symbol, arg_max(m.industry, size.n) FROM index_members m JOIN size USING (index_name) WHERE m.industry <> '' GROUP BY m.symbol"
            ).fetchall()
        except duckdb.CatalogException:
            return {}
        return dict(rows)

    def members_status(self) -> dict[str, Any]:
        try:
            row = self._con.execute("SELECT count(*), count(DISTINCT index_name), count(DISTINCT symbol), max(fetched_at) FROM index_members").fetchone()
        except duckdb.CatalogException:
            return {"rows": 0, "indices": 0, "symbols": 0, "fetched_at": None}
        return {"rows": row[0], "indices": row[1], "symbols": row[2], "fetched_at": row[3].replace(tzinfo=_UTC) if row[3] else None}

    def actions_status(self) -> dict[str, Any]:
        row = self._con.execute("SELECT count(*), count(DISTINCT symbol), min(ex_date), max(ex_date), max(fetched_at) FROM actions").fetchone()
        return {"rows": row[0], "symbols": row[1], "first_ex": row[2], "last_ex": row[3], "fetched_at": row[4].replace(tzinfo=_UTC) if row[4] else None}

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

    def first_loaded_day(self) -> date | None:
        row = self._con.execute("SELECT min(day) FROM days WHERE status = ?", [LOADED]).fetchone()
        return row[0] if row else None

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
            "last_load": loaded_at.replace(tzinfo=_UTC) if loaded_at else None, "actions": self.actions_status(),
            "statements": dict(self._con.execute("SELECT status, count(*) FROM statements GROUP BY status").fetchall()),
            "index_days": self._con.execute("SELECT count(*) FROM index_days").fetchone()[0], "packets": self.packet_counts(),
        }
        if first is not None and last is not None:
            out["gaps"] = self.gaps(first, last)
        return out

    def close(self) -> None:
        self._con.close()
