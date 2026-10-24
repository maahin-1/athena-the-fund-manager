"""A disk cache for model replies: the same prompt to the same tier within a week is answered from disk, costing no call.

The analysts and the judge run at temperature 0 on packets built in code, so an identical prompt (same system text, same user text, same tier) means identical evidence; a reply kept from the last
time is as good as a new one and costs nothing. The key includes the tier and the full text of both prompts, so any change in the evidence, the persona or the instructions is a different key.
Kept in `.athena/llm_cache.sqlite` (standard library). A reply older than `TTL_DAYS` is not used. A cache that cannot be read or written never fails a call: the model is simply asked."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

CACHE_PATH = Path(".athena") / "llm_cache.sqlite"
TTL_DAYS = 7


def key(tier: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{tier}\x00{system}\x00{user}".encode()).hexdigest()


class ReplyCache:
    def __init__(self, path: Path = CACHE_PATH, ttl_days: float = TTL_DAYS, clock: Callable[[], float] = time.time):
        self._path, self._ttl, self._clock = Path(path), ttl_days * 86400, clock
        self.hits = 0

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._path, timeout=5)
        con.execute("CREATE TABLE IF NOT EXISTS replies (key TEXT PRIMARY KEY, made REAL, source TEXT, reply TEXT)")
        return con

    def get(self, k: str) -> tuple[str, str] | None:
        """`(reply, the model that first made it)`, or None."""
        try:
            con = self._connect()
            try:
                row = con.execute("SELECT made, source, reply FROM replies WHERE key = ?", [k]).fetchone()
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            return None
        if row is None or self._clock() - row[0] > self._ttl:
            return None
        self.hits += 1
        return row[2], row[1]

    def put(self, k: str, source: str, reply: str) -> None:
        try:
            con = self._connect()
            try:
                with con:
                    con.execute("INSERT OR REPLACE INTO replies VALUES (?, ?, ?, ?)", [k, self._clock(), source, reply])
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            pass


class CachedLLM:
    """Answers from the cache when it can, else asks the client and keeps the reply. Passes `names`, `last_source` and `last_usage` through; after a hit `last_source` is
    "cache (<model>)" and `last_usage` is empty, so a hit adds nothing to the metered usage."""

    def __init__(self, inner: Any, tier: str, cache: ReplyCache):
        self._inner, self._tier, self._cache = inner, tier, cache
        self._source, self._usage = "", {}

    @property
    def names(self) -> list[str]:
        return list(getattr(self._inner, "names", []))

    @property
    def last_source(self) -> str:
        return self._source or str(getattr(self._inner, "last_source", ""))

    @property
    def last_usage(self) -> dict[str, int]:
        return dict(self._usage)

    def complete(self, system: str, user: str) -> str:
        k = key(self._tier, system, user)
        hit = self._cache.get(k)
        if hit is not None:
            self._source, self._usage = f"cache ({hit[1]})", {}
            return hit[0]
        reply = self._inner.complete(system, user)
        self._source, self._usage = "", dict(getattr(self._inner, "last_usage", {}) or {})
        self._cache.put(k, str(getattr(self._inner, "last_source", "")), reply)
        return reply
