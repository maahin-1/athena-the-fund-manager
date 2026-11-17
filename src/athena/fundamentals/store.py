"""The fundamentals database: statements by company, basis, statement and period, the filings seen and the state of each company's ingest.

One DuckDB file. Python writes in short bursts (open, write one filing, close); the terminal's server opens it read-only. Times are naive UTC."""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
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
    # WP04 (Q3). `statement_lines` above is the LATEST-value table the current pages read: a newer filing replaces an older one's row there. `statement_versions` is the append-only record of every
    # filing's lines, so the value public at a past date can be read back. A version is identified by company, basis, statement, period, line AND the filing it came from; it carries when the
    # filing was made public (`filed`), when this system first stored it (`ingested`, NULL when unknown), the hash of the raw filing, and the parser/mapping version that read it. `provenance` is
    # `ingest` (written as the filing was read), `rebuilt_from_raw` (re-read from a kept raw filing) or `seeded_from_latest` (copied from the latest table when the versions were introduced:
    # whatever that row was, it is NOT known to be the original). No unique key: duplicates are prevented by the writers, as for `statement_lines`.
    "CREATE TABLE IF NOT EXISTS statement_versions (symbol TEXT, basis TEXT, statement TEXT, period_start DATE, period_end DATE, key TEXT, value DOUBLE, filing_url TEXT, filed TIMESTAMP,"
    " ingested TIMESTAMP, source TEXT, source_hash TEXT, parser_version TEXT, layout TEXT, provenance TEXT, migration_id TEXT)",
    "CREATE TABLE IF NOT EXISTS filing_withdrawals (filing_url TEXT, withdrawn_at TIMESTAMP, reason TEXT)",
    "CREATE TABLE IF NOT EXISTS migrations (migration_id TEXT PRIMARY KEY, applied_at TIMESTAMP, kind TEXT, counts TEXT)",
    "CREATE TABLE IF NOT EXISTS schema_meta (name TEXT PRIMARY KEY, value TEXT)",
)
KEY = "symbol = ? AND basis = ? AND statement = ? AND period_start = ? AND period_end = ? AND key = ?"
VERSIONS_SCHEMA = 1  # the `statement_versions` layout this code writes; a store with a newer one is refused rather than written to blindly
VERSION_COLUMNS = ("basis", "statement", "period_start", "period_end", "key", "value", "filing_url", "filed", "ingested", "source", "source_hash", "parser_version", "layout", "provenance", "migration_id")
SEEDED, REBUILT, INGESTED = "seeded_from_latest", "rebuilt_from_raw", "ingest"
_parser_version: str | None = None


def parser_version() -> str:
    """Which reading of a raw filing produced a row: a hash of the parser and the line mapping, so it changes by itself when either does."""
    global _parser_version
    if _parser_version is None:
        from athena.fundamentals import rows as rows_module

        digest = hashlib.sha256()
        for module in (rows_module, spec):
            digest.update(Path(module.__file__).read_bytes())
        _parser_version = "rows-" + digest.hexdigest()[:12]
    return _parser_version


def statement_rows(st: Statements) -> list[tuple[Any, ...]]:
    """A parsed filing as `(symbol, basis, statement, period_start, period_end, key, value)` rows: what both the latest table and the versions table are written from."""
    rows: list[tuple[Any, ...]] = []
    for statement, periods in ((spec.INCOME, st.income), (spec.CASHFLOW, st.cashflow), (spec.BANKING, st.banking)):
        for (start, end), got in periods.items():
            rows += [(st.symbol, st.basis, statement, start, end, key, value) for key, value in got.items()]
    for end, got in st.balance.items():
        rows += [(st.symbol, st.basis, spec.BALANCE, end, end, key, value) for key, value in got.items()]
    return rows


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
            held = self._con.execute("SELECT value FROM schema_meta WHERE name = 'statement_versions'").fetchone()
            if held is None:
                self._con.execute("INSERT INTO schema_meta VALUES ('statement_versions', ?)", [str(VERSIONS_SCHEMA)])
            elif int(held[0]) > VERSIONS_SCHEMA:
                self._con.close()
                raise AthenaError(f"the fundamentals store has statement_versions schema {held[0]}, newer than this code understands ({VERSIONS_SCHEMA}): update Athena before writing to it")

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

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """All the statements inside happen or none do: a filing's old rows are deleted and its new ones inserted together, so a crash or a full disk between the two cannot leave a company without them."""
        self._con.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self._con.execute("ROLLBACK")
            raise
        self._con.execute("COMMIT")

    def save_statements(self, st: Statements, url: str, filed: datetime, source: str = "nse-xbrl", source_hash: str = "", version: str | None = None) -> int:
        """Store a filing's rows. In the latest-value table (what the current pages read) a row already held from a filing made later is kept and one from an earlier filing is replaced, as it
        always was. Every row is ALSO appended to `statement_versions` under this filing, whatever the latest table does with it, so an original replaced by a revision (or arriving after
        it) stays recoverable. `source_hash` is the hash of the raw filing; `version` the parser/mapping version (default: the current one). Returns the number of rows offered."""
        filed = _utc(filed)
        rows = statement_rows(st)
        if not rows:
            return 0
        stamp, reading = now(), version or parser_version()
        with self._atomic():
            self._con.executemany(
                f"INSERT INTO statement_versions SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL WHERE NOT EXISTS (SELECT 1 FROM statement_versions WHERE {KEY} AND filing_url = ?)",
                [(*row, url, filed, stamp, source, source_hash or None, reading, st.layout, INGESTED, *row[:6], url) for row in rows],
            )
            self._con.executemany(f"DELETE FROM statement_lines WHERE {KEY} AND filed < ?", [(*row[:6], filed) for row in rows])
            self._con.executemany(
                f"INSERT INTO statement_lines SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM statement_lines WHERE {KEY})",
                [(*row, url, filed, source, *row[:6]) for row in rows],
            )
        return len(rows)

    def forget_filing(self, url: str) -> None:
        """Drop every row a filing gave, so it can be read again with a changed mapping. Its versions go with it: they are this system's reading of that filing, and are written again from the new
        reading. (A version of the filer's number is never dropped by anything else.)"""
        self._con.execute("DELETE FROM statement_lines WHERE filing_url = ?", [url])
        self._con.execute("DELETE FROM statement_versions WHERE filing_url = ?", [url])

    # ---- versions (WP04)
    def has_versions(self) -> bool:
        """Whether the versions table exists and holds anything."""
        if not self._table_exists("statement_versions"):
            return False
        return self._con.execute("SELECT count(*) FROM statement_versions").fetchone()[0] > 0

    def _table_exists(self, name: str) -> bool:
        return self._con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0

    def versions(self, symbol: str, basis: str | None = None, statement: str | None = None) -> list[dict[str, Any]]:
        """Every stored version of the company's lines (optionally of one basis and statement), oldest filing first. Empty when the store has no versions table (a file from before WP04)."""
        if not self._table_exists("statement_versions"):
            return []
        sql, args = f"SELECT {', '.join(VERSION_COLUMNS)} FROM statement_versions WHERE symbol = ?", [symbol]
        for column, value in (("basis", basis), ("statement", statement)):
            if value is not None:
                sql, args = sql + f" AND {column} = ?", [*args, value]
        sql += " ORDER BY period_end, period_start, key, filed, filing_url"
        return [dict(zip(VERSION_COLUMNS, row)) for row in self._con.execute(sql, args).fetchall()]

    def _eligible(self, symbol: str, basis: str, statement: str, cutoff: datetime) -> list[tuple[Any, ...]]:
        """The version rows public at `cutoff`: filed by it and not withdrawn by it."""
        if not self._table_exists("statement_versions"):
            raise AthenaError("this store has no statement versions: run python -m athena.fundamentals.vintages on a copy first")
        cut = _utc(cutoff)
        withdrawn = "NOT EXISTS (SELECT 1 FROM filing_withdrawals w WHERE w.filing_url = v.filing_url AND w.withdrawn_at <= ?)" if self._table_exists("filing_withdrawals") else "? IS NOT NULL"
        return self._con.execute(
            "SELECT period_start, period_end, key, value, filing_url, filed FROM statement_versions v WHERE symbol = ? AND basis = ? AND statement = ? AND filed <= ? AND " + withdrawn,
            [symbol, basis, statement, cut, cut],
        ).fetchall()

    def lines_as_of(self, symbol: str, basis: str, statement: str, cutoff: datetime, fieldwise: bool = False) -> dict[tuple[date, date], dict[str, float]]:
        """The lines as they stood at `cutoff`: for each period, the version that was public then and not yet withdrawn. By default the whole latest eligible FILING supplies the period, so a
        partial amendment is exactly what it reported and inherits nothing from the filing it amended; `fieldwise=True` takes the newest eligible value of each line instead. Ties (two
        filings at the same instant) go to the greater filing identity, so the answer does not depend on insertion order. A period with no eligible version is absent: see `gaps_as_of`."""
        rows = self._eligible(symbol, basis, statement, cutoff)
        out: dict[tuple[date, date], dict[str, float]] = {}
        if fieldwise:
            best: dict[tuple[date, date, str], tuple[Any, ...]] = {}
            for start, end, key, value, url, filed in rows:
                held = best.get((start, end, key))
                if held is None or (filed, url) > (held[0], held[1]):
                    best[(start, end, key)] = (filed, url, value)
            for (start, end, key), (_, _, value) in sorted(best.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2])):
                out.setdefault((start, end), {})[key] = value
            return out
        winner: dict[tuple[date, date], tuple[Any, str]] = {}
        for start, end, _key, _value, url, filed in rows:
            if (start, end) not in winner or (filed, url) > winner[(start, end)]:
                winner[(start, end)] = (filed, url)
        for start, end, key, value, url, _filed in sorted(rows, key=lambda r: (r[1], r[0], r[2])):
            if winner[(start, end)][1] == url:
                out.setdefault((start, end), {})[key] = value
        return out

    def gaps_as_of(self, symbol: str, basis: str, statement: str, cutoff: datetime) -> list[dict[str, Any]]:
        """The periods this store holds a version of but none that was public at `cutoff`, each with the reason. This is how a missing original shows: the store only holds the revision, and says so."""
        have = self.lines_as_of(symbol, basis, statement, cutoff)
        gaps = []
        for start, end, first in self._con.execute(
            "SELECT period_start, period_end, min(filed) FROM statement_versions WHERE symbol = ? AND basis = ? AND statement = ? GROUP BY period_start, period_end ORDER BY period_end, period_start",
            [symbol, basis, statement],
        ).fetchall():
            if (start, end) in have:
                continue
            if first > _utc(cutoff):
                why = f"no version was public by {_utc(cutoff):%Y-%m-%d}: the first held version was filed {first:%Y-%m-%d}, and an earlier original, if there was one, is not held"
            else:
                why = f"every version public by {_utc(cutoff):%Y-%m-%d} has since been withdrawn"
            gaps.append({"period_start": start, "period_end": end, "reason": why})
        return gaps

    def withdraw_filing(self, url: str, when: datetime, reason: str) -> None:
        """Record that a filing was withdrawn from `when`: its versions stop counting from then on (a cutoff before `when` still sees them, because they were public), and the latest-value table is rebuilt
        for its company from the versions that remain. The versions themselves are kept."""
        symbols = [r[0] for r in self._con.execute("SELECT DISTINCT symbol FROM statement_versions WHERE filing_url = ?", [url]).fetchall()]
        if not symbols:
            raise AthenaError(f"no stored filing {url}: nothing to withdraw")
        self._con.execute("INSERT INTO filing_withdrawals VALUES (?, ?, ?)", [url, _utc(when), reason])
        self.rebuild_latest(symbols)

    def rebuild_latest(self, symbols: Sequence[str]) -> int:
        """Recompute the latest-value table for these companies from their versions (the newest filing of each line that has not been withdrawn). Refuses unless every row now in the latest table is
        also a version: otherwise rebuilding would drop a row that exists nowhere else. Returns the rows written."""
        written = 0
        for symbol in symbols:
            orphan = self._con.execute(
                f"SELECT count(*) FROM statement_lines l WHERE l.symbol = ? AND NOT EXISTS (SELECT 1 FROM statement_versions v WHERE v.symbol = l.symbol AND v.basis = l.basis AND v.statement = l.statement"
                " AND v.period_start = l.period_start AND v.period_end = l.period_end AND v.key = l.key AND v.filing_url = l.filing_url)",
                [symbol],
            ).fetchone()[0]
            if orphan:
                raise AthenaError(f"{symbol} has {orphan} latest-value rows that are not stored as versions yet: run the versions migration before changing what is current")
            best: dict[tuple[Any, ...], tuple[Any, ...]] = {}
            for basis, statement, start, end, key, value, url, filed, source in self._con.execute(
                "SELECT basis, statement, period_start, period_end, key, value, filing_url, filed, source FROM statement_versions v WHERE symbol = ? AND NOT EXISTS"
                " (SELECT 1 FROM filing_withdrawals w WHERE w.filing_url = v.filing_url AND w.withdrawn_at <= ?)",
                [symbol, now()],
            ).fetchall():
                k = (basis, statement, start, end, key)
                if k not in best or (filed, url) > (best[k][1], best[k][2]):
                    best[k] = (value, filed, url, source)
            with self._atomic():
                self._con.execute("DELETE FROM statement_lines WHERE symbol = ?", [symbol])
                rows = [(symbol, *k, value, url, filed, source) for k, (value, filed, url, source) in best.items()]
                if rows:
                    self._con.executemany("INSERT INTO statement_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                written += len(rows)
        return written

    def save_holding(self, symbol: str, holding: Holding, url: str, filed: datetime) -> None:
        """Store one quarter's pattern, replacing what was held for that symbol and quarter."""
        rows = [(symbol, holding.period_end, key, g.shares, g.holders, url, _utc(filed)) for key, g in holding.groups.items()]
        with self._atomic():
            self._con.execute("DELETE FROM shareholding WHERE symbol = ? AND period_end = ?", [symbol, holding.period_end])
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

    def quarter_figures(self, symbol: str) -> list[dict[str, Any]]:
        """Net profit and sales of each quarterly period (80 to 100 days) under each basis, in rupees, with when the filing that carried them was made. One row per basis and period end."""
        rows = self._con.execute(
            "SELECT basis, period_start, period_end, key, value, filed FROM statement_lines WHERE symbol = ? AND key IN ('net_profit', 'sales') AND (period_end - period_start) BETWEEN 80 AND 100"
            " ORDER BY period_end, basis",
            [symbol],
        ).fetchall()
        out: dict[tuple[str, date], dict[str, Any]] = {}
        for basis, start, end, key, value, filed in rows:
            row = out.setdefault((basis, end), {"basis": basis, "start": start, "end": end, "filed": filed})
            row[key] = value
            if filed is not None and (row["filed"] is None or filed < row["filed"]):
                row["filed"] = filed  # the first filing that carried the period
        return list(out.values())

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
