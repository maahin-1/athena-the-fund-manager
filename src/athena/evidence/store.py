"""The evidence store: `.athena/evidence.duckdb`.

    items          one row per item: source, publisher, title, link, a short excerpt, when it was published, when we FIRST saw it, and the story it belongs to
    source_health  one row per source: when it was last tried, last worked, how it ended, how many failures in a row, the newest item it holds

Items are added once and never changed: a feed that edits a headline later gives a new row in the same story, and `first_seen_at` keeps meaning "what could have been known then". The file is opened
for a moment per call (the collector writes for milliseconds, the page reads for milliseconds), so a fetch never holds up a screen."""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.news.model import NewsItem

DEFAULT_PATH = Path(".athena") / "evidence.duckdb"
STRUCTURED_DDL = (
    "CREATE TABLE IF NOT EXISTS insider_trades (trade_id VARCHAR PRIMARY KEY, symbol VARCHAR NOT NULL, person VARCHAR, category VARCHAR, txn VARCHAR, mode VARCHAR, security VARCHAR, shares DOUBLE, value DOUBLE,"
    " before_shares DOUBLE, after_shares DOUBLE, before_pct DOUBLE, after_pct DOUBLE, trade_from DATE, trade_to DATE, filed_at TIMESTAMP NOT NULL, first_seen_at TIMESTAMP NOT NULL, link VARCHAR)",
    "CREATE INDEX IF NOT EXISTS insider_symbol ON insider_trades (symbol, filed_at)",
    "CREATE TABLE IF NOT EXISTS market_flows (day DATE NOT NULL, category VARCHAR NOT NULL, buy DOUBLE, sell DOUBLE, net DOUBLE, first_seen_at TIMESTAMP NOT NULL, PRIMARY KEY (day, category))",
    "CREATE TABLE IF NOT EXISTS board_meetings (meeting_id VARCHAR PRIMARY KEY, symbol VARCHAR NOT NULL, company VARCHAR, meeting_date DATE NOT NULL, purpose VARCHAR, description VARCHAR,"
    " announced_at TIMESTAMP NOT NULL, first_seen_at TIMESTAMP NOT NULL, link VARCHAR)",
    "CREATE INDEX IF NOT EXISTS board_symbol ON board_meetings (symbol, meeting_date)",
    "CREATE TABLE IF NOT EXISTS claims (claim_id VARCHAR PRIMARY KEY, item_id VARCHAR NOT NULL, source_id VARCHAR NOT NULL, story_id VARCHAR, symbol VARCHAR, kind VARCHAR NOT NULL, text VARCHAR,"
    " fields VARCHAR, published_at TIMESTAMP NOT NULL, extracted_at TIMESTAMP NOT NULL)",
    "CREATE INDEX IF NOT EXISTS claims_symbol ON claims (symbol, published_at)",
    "CREATE TABLE IF NOT EXISTS checks (claim_id VARCHAR PRIMARY KEY, outcome VARCHAR NOT NULL, final BOOLEAN NOT NULL, detail VARCHAR, ours VARCHAR, checked_at TIMESTAMP NOT NULL)",
    "CREATE TABLE IF NOT EXISTS claim_runs (item_id VARCHAR PRIMARY KEY, run_at TIMESTAMP NOT NULL)",
)
STORY_WINDOW = timedelta(hours=72)
STORY_SIMILARITY = 0.7  # Jaccard overlap of the headline words at or above which two items are one story
_WORDS = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset("the and for with from that this has have will says said after over into about amid its are was were not but new report reports live updates today".split())


def _naive(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when


def item_id(source_id: str, item: NewsItem) -> str:
    return hashlib.sha1(f"{source_id}|{item.link or item.title}".encode()).hexdigest()


def headline_words(title: str) -> frozenset[str]:
    return frozenset(w for w in _WORDS.findall(title.lower()) if w not in _STOP)


def same_story(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two headlines are one story when most of their words are shared (an agency story printed by two papers with a different first line is still one voice)."""
    if len(a) < 3 or len(b) < 3:
        return a == b and bool(a)
    return len(a & b) / len(a | b) >= STORY_SIMILARITY


@dataclass(frozen=True)
class StoredItem:
    item_id: str
    source_id: str
    publisher: str
    title: str
    link: str
    snippet: str
    published_at: datetime  # UTC, timezone-aware
    first_seen_at: datetime
    story_id: str


class EvidenceStore:
    def __init__(self, path: Path | str = DEFAULT_PATH, wait_seconds: float = 30.0, read_only: bool = False):
        self._path, self._wait, self._read_only = Path(path), wait_seconds, read_only
        if not read_only:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._open() as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS items (item_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, publisher VARCHAR NOT NULL, title VARCHAR NOT NULL, link VARCHAR, snippet VARCHAR,"
                    " published_at TIMESTAMP NOT NULL, first_seen_at TIMESTAMP NOT NULL, story_id VARCHAR NOT NULL)"
                )
                con.execute("CREATE INDEX IF NOT EXISTS items_published ON items (published_at)")
                con.execute(
                    "CREATE TABLE IF NOT EXISTS source_health (source_id VARCHAR PRIMARY KEY, last_try TIMESTAMP, last_ok TIMESTAMP, last_status VARCHAR, last_error VARCHAR,"
                    " items_last INTEGER, fail_streak INTEGER, newest_item TIMESTAMP)"
                )
                for ddl in STRUCTURED_DDL:
                    con.execute(ddl)

    @contextmanager
    def _open(self):  # noqa: ANN202
        """The file for one operation. Another process holding it for a write is waited for, up to `wait_seconds`."""
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
    def add_items(self, source_id: str, items: Sequence[NewsItem], now: datetime) -> int:
        """Store the items not seen before and return how many were new. An item joins an earlier story from the last 72 hours when its headline is close to that story's."""
        if not items:
            return 0
        stamp = _naive(now)
        with self._open() as con:
            recent = con.execute("SELECT title, story_id FROM items WHERE published_at >= ?", [stamp - STORY_WINDOW - timedelta(days=1)]).fetchall()
            known = [(headline_words(t), s) for t, s in recent]
            held = {r[0] for r in con.execute("SELECT item_id FROM items WHERE item_id IN (" + ",".join("?" * len(items)) + ")", [item_id(source_id, i) for i in items]).fetchall()}
            new = 0
            con.execute("BEGIN")
            try:
                for i in items:
                    iid = item_id(source_id, i)
                    if iid in held:
                        continue
                    words = headline_words(i.title)
                    story = next((s for w, s in known if same_story(words, w)), iid[:16])
                    con.execute("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [iid, source_id, i.publisher, i.title, i.link, i.snippet, _naive(i.published), stamp, story])
                    known.append((words, story))
                    held.add(iid)
                    new += 1
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return new

    def record_health(self, source_id: str, now: datetime, ok: bool, status: str, error: str = "", count: int = 0, newest: datetime | None = None) -> None:
        stamp = _naive(now)
        with self._open() as con:
            row = con.execute("SELECT last_ok, fail_streak, newest_item FROM source_health WHERE source_id = ?", [source_id]).fetchone()
            last_ok, streak, held_newest = (row if row else (None, 0, None))
            if ok:
                last_ok, streak = stamp, 0
                if newest is not None:
                    held_newest = _naive(newest)
            else:
                streak = (streak or 0) + 1
            con.execute("INSERT OR REPLACE INTO source_health VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [source_id, stamp, last_ok, status, error[:300], count, streak, held_newest])

    def _insert_new(self, table: str, key_col: str, keys: Sequence[Any], rows: Sequence[Sequence[Any]]) -> int:
        """Insert the rows whose key is not held yet, in one transaction, and return how many were new. A row already held is never rewritten (its `first_seen_at` stays)."""
        if not rows:
            return 0
        with self._open() as con:
            held = {r[0] for r in con.execute(f"SELECT {key_col} FROM {table} WHERE {key_col} IN (" + ",".join("?" * len(keys)) + ")", list(keys)).fetchall()}
            added: set[Any] = set()
            con.execute("BEGIN")
            try:
                for key, row in zip(keys, rows):
                    if key in held or key in added:
                        continue
                    added.add(key)
                    con.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})", list(row))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return len(added)

    def add_insider(self, trades: Sequence[Any], now: datetime) -> int:
        stamp = _naive(now)
        rows = [(t.trade_id, t.symbol, t.person, t.category, t.txn, t.mode, t.security, t.shares, t.value, t.before_shares, t.after_shares, t.before_pct, t.after_pct, t.trade_from, t.trade_to, _naive(t.filed_at), stamp, t.link) for t in trades]
        return self._insert_new("insider_trades", "trade_id", [t.trade_id for t in trades], rows)

    def add_flows(self, flows: Sequence[Any], now: datetime) -> int:
        stamp = _naive(now)
        if not flows:
            return 0
        with self._open() as con:
            held = set(con.execute("SELECT day, category FROM market_flows WHERE day >= ?", [min(f.day for f in flows)]).fetchall())
            new = 0
            for f in flows:
                if (f.day, f.category) in held:
                    continue
                con.execute("INSERT INTO market_flows VALUES (?, ?, ?, ?, ?, ?)", [f.day, f.category, f.buy, f.sell, f.net, stamp])
                held.add((f.day, f.category))
                new += 1
        return new

    def add_board(self, meetings: Sequence[Any], now: datetime) -> int:
        stamp = _naive(now)
        rows = [(m.meeting_id, m.symbol, m.company, m.meeting_date, m.purpose, m.description, _naive(m.announced_at), stamp, m.link) for m in meetings]
        return self._insert_new("board_meetings", "meeting_id", [m.meeting_id for m in meetings], rows)

    # --- claims and their checks ---
    def items_without_claims(self, since: datetime, limit: int = 20000) -> list[StoredItem]:
        """Items published since `since` that have not been read for claims yet, oldest first."""
        sql = ("SELECT i.item_id, i.source_id, i.publisher, i.title, i.link, i.snippet, i.published_at, i.first_seen_at, i.story_id FROM items i LEFT JOIN claim_runs r ON r.item_id = i.item_id"
               " WHERE r.item_id IS NULL AND i.published_at >= ? ORDER BY i.published_at LIMIT ?")
        return self._rows(sql, [_naive(since), limit])

    def add_claims(self, batch: Sequence[tuple[StoredItem, Sequence[Any]]], now: datetime) -> int:
        """Record that each item was read, and keep its claims. An item is read once; a claim is never rewritten. Returns how many claims were kept."""
        import json

        stamp, kept = _naive(now), 0
        with self._open() as con:
            con.execute("BEGIN")
            try:
                for item, claims in batch:
                    for c in claims:
                        cid = hashlib.sha1(f"{item.item_id}|{c.kind}|{c.text}".encode()).hexdigest()
                        con.execute("INSERT OR IGNORE INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    [cid, item.item_id, item.source_id, item.story_id, c.symbol, c.kind, c.text, json.dumps(c.fields, sort_keys=True), _naive(item.published_at), stamp])
                        kept += 1
                    con.execute("INSERT OR IGNORE INTO claim_runs VALUES (?, ?)", [item.item_id, stamp])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return kept

    def claims_to_check(self, since: datetime) -> list[dict[str, Any]]:
        """Claims published since `since` with no final check yet (never checked, or still pending), oldest first."""
        import json

        sql = ("SELECT c.claim_id, c.item_id, c.source_id, c.symbol, c.kind, c.text, c.fields, c.published_at FROM claims c LEFT JOIN checks k ON k.claim_id = c.claim_id"
               " WHERE (k.claim_id IS NULL OR NOT k.final) AND c.published_at >= ? ORDER BY c.published_at")
        with self._open() as con:
            rows = con.execute(sql, [_naive(since)]).fetchall()
        keys = ("claim_id", "item_id", "source_id", "symbol", "kind", "text", "fields", "published_at")
        out = [dict(zip(keys, r)) for r in rows]
        for r in out:
            r["fields"] = json.loads(r["fields"] or "{}")
            r["published_at"] = r["published_at"].replace(tzinfo=timezone.utc)
        return out

    def save_checks(self, batch: Sequence[tuple[str, str, bool, str, dict[str, Any]]], now: datetime) -> None:
        """Each `(claim_id, outcome, final, detail, ours)`. A check that is not final is replaced by the next one; a final one is kept as it was."""
        import json

        stamp = _naive(now)
        with self._open() as con:
            con.execute("BEGIN")
            try:
                for claim_id, outcome, final, detail, ours in batch:
                    held = con.execute("SELECT final FROM checks WHERE claim_id = ?", [claim_id]).fetchone()
                    if held and held[0]:
                        continue
                    con.execute("INSERT OR REPLACE INTO checks VALUES (?, ?, ?, ?, ?, ?)", [claim_id, outcome, final, detail[:600], json.dumps(ours, default=str, sort_keys=True), stamp])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def claims_for(self, symbol: str, since: datetime, until: datetime) -> list[dict[str, Any]]:
        """A company's claims in the window with what the check said (outcome `unchecked` when none yet), newest first."""
        import json

        sql = ("SELECT c.claim_id, c.item_id, c.source_id, c.story_id, c.kind, c.text, c.fields, c.published_at, i.publisher, i.link, i.title, i.first_seen_at,"
               " coalesce(k.outcome, 'unchecked'), coalesce(k.detail, ''), coalesce(k.ours, '{}'), k.checked_at"
               " FROM claims c JOIN items i ON i.item_id = c.item_id LEFT JOIN checks k ON k.claim_id = c.claim_id"
               " WHERE c.symbol = ? AND c.published_at >= ? AND c.published_at <= ? ORDER BY c.published_at DESC")
        keys = ("claim_id", "item_id", "source_id", "story_id", "kind", "text", "fields", "published_at", "publisher", "link", "title", "first_seen_at", "outcome", "detail", "ours", "checked_at")
        with self._open() as con:
            rows = con.execute(sql, [symbol, _naive(since), _naive(until)]).fetchall()
        out = [dict(zip(keys, r)) for r in rows]
        for r in out:
            r["fields"], r["ours"] = json.loads(r["fields"] or "{}"), json.loads(r["ours"] or "{}")
            r["published_at"] = r["published_at"].replace(tzinfo=timezone.utc)
            r["first_seen_at"] = r["first_seen_at"].replace(tzinfo=timezone.utc)
        return out

    def market_claims(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        """Market-wide claims (institutional flows) with their checks."""
        import json

        sql = ("SELECT c.claim_id, c.source_id, c.story_id, c.kind, c.text, c.fields, c.published_at, i.publisher, i.link, coalesce(k.outcome, 'unchecked'), coalesce(k.detail, '')"
               " FROM claims c JOIN items i ON i.item_id = c.item_id LEFT JOIN checks k ON k.claim_id = c.claim_id"
               " WHERE c.symbol = '' AND c.published_at >= ? AND c.published_at <= ? ORDER BY c.published_at DESC LIMIT 50")
        keys = ("claim_id", "source_id", "story_id", "kind", "text", "fields", "published_at", "publisher", "link", "outcome", "detail")
        with self._open() as con:
            rows = con.execute(sql, [_naive(since), _naive(until)]).fetchall()
        out = [dict(zip(keys, r)) for r in rows]
        for r in out:
            r["fields"] = json.loads(r["fields"] or "{}")
            r["published_at"] = r["published_at"].replace(tzinfo=timezone.utc)
        return out

    def source_record(self) -> dict[str, dict[str, int]]:
        """Per source, how its settled claims ended: the record a source is judged on (only confirmed, partly and contradicted count; the rest say nothing about the source)."""
        sql = ("SELECT c.source_id, k.outcome, count(*) FROM claims c JOIN checks k ON k.claim_id = c.claim_id WHERE k.final AND k.outcome IN ('confirmed', 'partly', 'contradicted') GROUP BY 1, 2")
        out: dict[str, dict[str, int]] = {}
        with self._open() as con:
            for sid, outcome, n in con.execute(sql).fetchall():
                out.setdefault(sid, {"confirmed": 0, "partly": 0, "contradicted": 0})[outcome] = n
        return out

    def reset_claims(self) -> None:
        """Forget every claim and check. They are derived from the items (which are never touched), so they can be rebuilt when the patterns change."""
        with self._open() as con:
            for table in ("checks", "claims", "claim_runs"):
                con.execute(f"DELETE FROM {table}")

    def claim_counts(self) -> dict[str, int]:
        with self._open() as con:
            claims = con.execute("SELECT count(*) FROM claims").fetchone()[0]
            read = con.execute("SELECT count(*) FROM claim_runs").fetchone()[0]
            by = dict(con.execute("SELECT outcome, count(*) FROM checks GROUP BY 1").fetchall())
        return {"claims": claims, "items_read": read, **by}

    # --- reading ---
    def insider_between(self, since: date, until: date, symbol: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """Insider trades FILED in the window (by the exchange's stamp), newest first."""
        sql = "SELECT symbol, person, category, txn, mode, security, shares, value, before_pct, after_pct, trade_from, trade_to, filed_at, first_seen_at, link FROM insider_trades WHERE filed_at >= ? AND filed_at < ?"
        params: list[Any] = [datetime.combine(since, datetime.min.time()), datetime.combine(until + timedelta(days=1), datetime.min.time())]
        if symbol:
            sql, params = sql + " AND symbol = ?", [*params, symbol]
        keys = ("symbol", "person", "category", "txn", "mode", "security", "shares", "value", "before_pct", "after_pct", "trade_from", "trade_to", "filed_at", "first_seen_at", "link")
        with self._open() as con:
            rows = con.execute(sql + " ORDER BY filed_at DESC LIMIT ?", [*params, limit]).fetchall()
        return [dict(zip(keys, r)) for r in rows]

    def insider_newest(self) -> datetime | None:
        """When the newest insider filing held was filed: how far this data reaches."""
        with self._open() as con:
            row = con.execute("SELECT max(filed_at) FROM insider_trades").fetchone()
        return row[0].replace(tzinfo=timezone.utc) if row and row[0] else None

    def flows_first(self) -> date | None:
        with self._open() as con:
            row = con.execute("SELECT min(day) FROM market_flows").fetchone()
        return row[0] if row else None

    def flows_between(self, since: date, until: date) -> list[dict[str, Any]]:
        with self._open() as con:
            rows = con.execute("SELECT day, category, buy, sell, net, first_seen_at FROM market_flows WHERE day >= ? AND day <= ? ORDER BY day DESC, category", [since, until]).fetchall()
        return [dict(zip(("day", "category", "buy", "sell", "net", "first_seen_at"), r)) for r in rows]

    def board_for(self, symbol: str, since: date, until: date) -> list[dict[str, Any]]:
        sql = ("SELECT symbol, meeting_date, purpose, description, announced_at, first_seen_at, link FROM board_meetings"
               " WHERE symbol = ? AND meeting_date >= ? AND meeting_date <= ? ORDER BY meeting_date, announced_at")
        with self._open() as con:
            rows = con.execute(sql, [symbol, since, until]).fetchall()
        return [dict(zip(("symbol", "meeting_date", "purpose", "description", "announced_at", "first_seen_at", "link"), r)) for r in rows]

    def _rows(self, sql: str, params: Sequence[Any]) -> list[StoredItem]:
        with self._open() as con:
            rows = con.execute(sql, list(params)).fetchall()
        utc = lambda d: d.replace(tzinfo=timezone.utc)  # noqa: E731
        return [StoredItem(r[0], r[1], r[2], r[3], r[4] or "", r[5] or "", utc(r[6]), utc(r[7]), r[8]) for r in rows]

    def items_between(self, since: datetime, until: datetime, source_ids: Iterable[str] | None = None, limit: int = 5000) -> list[StoredItem]:
        """Items published in the window, newest first, optionally only from these sources."""
        sql = "SELECT item_id, source_id, publisher, title, link, snippet, published_at, first_seen_at, story_id FROM items WHERE published_at >= ? AND published_at <= ?"
        params: list[Any] = [_naive(since), _naive(until)]
        if source_ids is not None:
            ids = list(source_ids)
            if not ids:
                return []
            sql += " AND source_id IN (" + ",".join("?" * len(ids)) + ")"
            params += ids
        return self._rows(sql + " ORDER BY published_at DESC LIMIT ?", [*params, limit])

    def health(self) -> dict[str, dict[str, Any]]:
        with self._open() as con:
            rows = con.execute("SELECT source_id, last_try, last_ok, last_status, last_error, items_last, fail_streak, newest_item FROM source_health").fetchall()
            counts = dict(con.execute("SELECT source_id, count(*) FROM items GROUP BY 1").fetchall())
        keys = ("last_try", "last_ok", "status", "error", "items_last", "fail_streak", "newest_item")
        out = {r[0]: dict(zip(keys, r[1:])) for r in rows}
        for sid, row in out.items():
            row["items_held"] = counts.get(sid, 0)
        return out
