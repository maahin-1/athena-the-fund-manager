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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.news.model import NewsItem

DEFAULT_PATH = Path(".athena") / "evidence.duckdb"
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

    # --- reading ---
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
