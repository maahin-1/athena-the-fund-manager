"""The IPO store: `.athena/ipo.duckdb`.

    issues         one row per issue (symbol and opening day): its dates, price band, size in shares, lot and the status the exchange gives it now; `first_seen_at` is when we first saw it and never changes
    subscription   the exchange's bid detail by investor category, one snapshot each time it CHANGED while an issue was open, so the curve of demand through the days is kept
    listed         issues that have listed: issue price, band and listing day
    filings        SEBI's draft offer documents (DRHP, UDRHP, RHP, addenda...): the company, kind, the day filed, the SEBI page and the PDF
    source_health  how each source did on its last try

The file is opened for a moment per call, as the evidence file is, so a fetch never holds up a screen."""
from __future__ import annotations

import json
import time
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_PATH = Path(".athena") / "ipo.duckdb"
DDL = (
    "CREATE TABLE IF NOT EXISTS issues (symbol VARCHAR NOT NULL, start_date DATE NOT NULL, name VARCHAR, series VARCHAR, kind VARCHAR, status VARCHAR, end_date DATE, price_low DOUBLE, price_high DOUBLE,"
    " shares DOUBLE, lot INTEGER, first_seen_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol, start_date))",
    "CREATE TABLE IF NOT EXISTS subscription (symbol VARCHAR NOT NULL, taken_at TIMESTAMP NOT NULL, sr VARCHAR NOT NULL, category VARCHAR, offered DOUBLE, bid DOUBLE, times DOUBLE, PRIMARY KEY (symbol, taken_at, sr))",
    "CREATE TABLE IF NOT EXISTS listed (symbol VARCHAR NOT NULL, listing_date DATE, name VARCHAR, series VARCHAR, start_date DATE, end_date DATE, issue_price DOUBLE, band_low DOUBLE, band_high DOUBLE,"
    " first_seen_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol, start_date))",
    "CREATE TABLE IF NOT EXISTS filings (url VARCHAR PRIMARY KEY, company VARCHAR, kind VARCHAR, filed DATE, title VARCHAR, pdf VARCHAR, pdfs VARCHAR, first_seen_at TIMESTAMP NOT NULL)",
    "CREATE TABLE IF NOT EXISTS source_health (source_id VARCHAR PRIMARY KEY, last_try TIMESTAMP, last_ok TIMESTAMP, status VARCHAR, error VARCHAR, items INTEGER)",
)


def _naive(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when


class IpoStore:
    def __init__(self, path: Path | str = DEFAULT_PATH, wait_seconds: float = 30.0, read_only: bool = False):
        self._path, self._wait, self._read_only = Path(path), wait_seconds, read_only
        if not read_only:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._open() as con:
                for ddl in DDL:
                    con.execute(ddl)

    @contextmanager
    def _open(self):  # noqa: ANN202
        end = time.monotonic() + self._wait
        while True:
            try:
                con = duckdb.connect(str(self._path), read_only=self._read_only)
                break
            except duckdb.IOException:
                if time.monotonic() > end:
                    raise
                time.sleep(0.2)
        try:
            yield con
        finally:
            con.close()

    # --- writing ---
    def upsert_issues(self, issues: Sequence[Any], now: datetime) -> int:
        """Add new issues and bring the held ones up to date (the status moves from Forthcoming to Active to Closed). Returns how many were new."""
        stamp, new = _naive(now), 0
        with self._open() as con:
            for i in issues:
                held = con.execute("SELECT 1 FROM issues WHERE symbol = ? AND start_date = ?", [i.symbol, i.start]).fetchone()
                if held:
                    # a figure one endpoint leaves out (the current-issue list has no price band for an SME issue) does not erase the one another gave
                    con.execute("UPDATE issues SET name=?, series=?, kind=?, status=?, end_date=?, price_low=coalesce(?, price_low), price_high=coalesce(?, price_high), shares=coalesce(?, shares), lot=coalesce(?, lot), updated_at=?"
                                " WHERE symbol=? AND start_date=?",
                                [i.name, i.series, i.kind, i.status, i.end, i.price_low, i.price_high, i.shares, i.lot, stamp, i.symbol, i.start])
                else:
                    con.execute("INSERT INTO issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [i.symbol, i.start, i.name, i.series, i.kind, i.status, i.end, i.price_low, i.price_high, i.shares, i.lot, stamp, stamp])
                    new += 1
        return new

    def add_subscription(self, symbol: str, bids: Sequence[Any], now: datetime) -> bool:
        """Keep a snapshot of the bid detail only when it differs from the last one held. Returns whether one was kept."""
        if not bids or all(b.offered is None and b.bid is None for b in bids):
            return False  # the exchange lists the categories before any figure is in
        shape = [(b.sr, b.category, b.offered, b.bid, b.times) for b in bids]
        with self._open() as con:
            last = con.execute("SELECT max(taken_at) FROM subscription WHERE symbol = ?", [symbol]).fetchone()[0]
            if last is not None:
                held = con.execute("SELECT sr, category, offered, bid, times FROM subscription WHERE symbol = ? AND taken_at = ? ORDER BY sr", [symbol, last]).fetchall()
                if sorted(held) == sorted(shape):
                    return False
            stamp = _naive(now)
            for sr, category, offered, bid, times in shape:
                con.execute("INSERT OR IGNORE INTO subscription VALUES (?, ?, ?, ?, ?, ?, ?)", [symbol, stamp, sr, category, offered, bid, times])
        return True

    def upsert_listed(self, rows: Sequence[Any], now: datetime) -> int:
        stamp, new = _naive(now), 0
        with self._open() as con:
            for r in rows:
                start = r.start or date(1970, 1, 1)
                held = con.execute("SELECT 1 FROM listed WHERE symbol = ? AND start_date = ?", [r.symbol, start]).fetchone()
                if held:
                    con.execute("UPDATE listed SET listing_date=?, issue_price=?, band_low=?, band_high=? WHERE symbol=? AND start_date=?", [r.listing, r.issue_price, r.band_low, r.band_high, r.symbol, start])
                else:
                    con.execute("INSERT INTO listed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [r.symbol, r.listing, r.name, r.series, start, r.end, r.issue_price, r.band_low, r.band_high, stamp])
                    new += 1
        return new

    def add_filings(self, filings: Sequence[Any], now: datetime) -> list[str]:
        """Keep filings not held yet; returns their urls (the ones whose page should be read for the document)."""
        stamp, fresh = _naive(now), []
        with self._open() as con:
            for f in filings:
                if con.execute("SELECT 1 FROM filings WHERE url = ?", [f.url]).fetchone():
                    continue
                con.execute("INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [f.url, f.company, f.kind, f.filed, f.title, f.pdfs[0] if f.pdfs else "", json.dumps(list(f.pdfs)), stamp])
                fresh.append(f.url)
        return fresh

    def set_pdf(self, url: str, pdf: str) -> None:
        with self._open() as con:
            con.execute("UPDATE filings SET pdf = ? WHERE url = ? AND (pdf IS NULL OR pdf = '')", [pdf, url])

    def filings_without_pdf(self, limit: int = 30) -> list[str]:
        with self._open() as con:
            return [r[0] for r in con.execute("SELECT url FROM filings WHERE pdf IS NULL OR pdf = '' ORDER BY filed DESC LIMIT ?", [limit]).fetchall()]

    def record_health(self, source_id: str, now: datetime, ok: bool, status: str, error: str = "", items: int = 0) -> None:
        stamp = _naive(now)
        with self._open() as con:
            row = con.execute("SELECT last_ok FROM source_health WHERE source_id = ?", [source_id]).fetchone()
            last_ok = stamp if ok else (row[0] if row else None)
            con.execute("INSERT OR REPLACE INTO source_health VALUES (?, ?, ?, ?, ?, ?)", [source_id, stamp, last_ok, status, error[:300], items])

    # --- reading ---
    def _all(self, sql: str, params: Sequence[Any], keys: Sequence[str]) -> list[dict[str, Any]]:
        with self._open() as con:
            return [dict(zip(keys, r)) for r in con.execute(sql, list(params)).fetchall()]

    def issues(self, since: date) -> list[dict[str, Any]]:
        """Issues that opened on or after `since`, or are still open or coming, soonest first."""
        keys = ("symbol", "start", "name", "series", "kind", "status", "end", "price_low", "price_high", "shares", "lot", "first_seen_at")
        return self._all("SELECT symbol, start_date, name, series, kind, status, end_date, price_low, price_high, shares, lot, first_seen_at FROM issues WHERE start_date >= ? ORDER BY start_date, symbol", [since], keys)

    def subscription(self, symbol: str) -> dict[str, Any] | None:
        """The latest bid detail for an issue and how many snapshots are held."""
        with self._open() as con:
            last = con.execute("SELECT max(taken_at) FROM subscription WHERE symbol = ?", [symbol]).fetchone()[0]
            if last is None:
                return None
            rows = con.execute("SELECT sr, category, offered, bid, times FROM subscription WHERE symbol = ? AND taken_at = ? ORDER BY sr", [symbol, last]).fetchall()
            n = con.execute("SELECT count(DISTINCT taken_at) FROM subscription WHERE symbol = ?", [symbol]).fetchone()[0]
        return {"taken_at": last.replace(tzinfo=timezone.utc), "snapshots": n, "rows": [dict(zip(("sr", "category", "offered", "bid", "times"), r)) for r in rows]}

    def listed(self, since: date) -> list[dict[str, Any]]:
        keys = ("symbol", "listing", "name", "series", "start", "end", "issue_price", "band_low", "band_high")
        return self._all("SELECT symbol, listing_date, name, series, start_date, end_date, issue_price, band_low, band_high FROM listed WHERE listing_date >= ? ORDER BY listing_date DESC, symbol", [since], keys)

    def filings(self, since: date) -> list[dict[str, Any]]:
        keys = ("url", "company", "kind", "filed", "title", "pdf", "first_seen_at")
        return self._all("SELECT url, company, kind, filed, title, pdf, first_seen_at FROM filings WHERE filed >= ? ORDER BY filed DESC, company", [since], keys)

    def health(self) -> dict[str, dict[str, Any]]:
        keys = ("source_id", "last_try", "last_ok", "status", "error", "items")
        return {r["source_id"]: r for r in self._all("SELECT source_id, last_try, last_ok, status, error, items FROM source_health", [], keys)}
