"""The durable budget ledger: reservations made in one transaction, so two processes cannot both take the last slot (audit A5, WP06).

The earlier meter counted lines of a JSONL file, then called the model, then appended a line. Between the count and the append another process could count the same free slot, and one unreadable line
made the whole file read as empty. Here a call is a *logical request* that is **reserved** before the model is asked: inside a `BEGIN IMMEDIATE` transaction the ledger counts this month's units for the
tier and either inserts the reservation or refuses. SQLite (standard library, already used by the reply cache) serialises writers across processes; nothing else is needed.

Units are kept apart:

- a *logical request* is one reservation: one `complete()` call by a caller, whatever the fallback chain does. This is what the owner's `calls_per_month` ceiling counts. It is **not** a limit on
  provider HTTP attempts, tokens or money, and says nothing about any of them;
- a *provider attempt* is one HTTP request to one model, recorded under its reservation with its outcome; a *retry* is an attempt after the first on the same model. Neither uses a unit;
- *token usage* is what the answering provider reported, or NULL when it reported nothing: an unreported figure is never turned into zero.

A reservation is in one of these states. `reserved` (made, not finished: counts, and keeps counting if the process dies, until a person releases it), `settled` (a model answered: counts), `unknown`
(no model answered, but some attempt may have been processed by a provider, such as a timeout or a broken reply: counts, because unknown provider work is not free), `refused` (every attempt was
definitely declined by a provider, or none was made: does not count) and `released` (a person closed an unresolved reservation and said why: does not count).

An unreadable ledger is never read as an empty one: opening it raises `LedgerUnavailable`, and the meter then refuses metered work."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError

LEDGER_PATH = Path(".athena") / "llm_ledger.sqlite"
SCHEMA = 1
BUSY_SECONDS = 10.0
COUNTED = ("reserved", "settled", "unknown")
RESERVED, SETTLED, UNKNOWN, REFUSED, RELEASED = "reserved", "settled", "unknown", "refused", "released"
DEFINITE_REFUSAL_BELOW = 500  # an HTTP answer under this with a status of 400 or more means the provider declined to process the request

DDL = (
    "CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, logical_id TEXT NOT NULL, month TEXT NOT NULL, tier TEXT NOT NULL, units INTEGER NOT NULL DEFAULT 1, "
    "state TEXT NOT NULL, reserved_at TEXT NOT NULL, settled_at TEXT, pid INTEGER, origin TEXT NOT NULL DEFAULT 'live', model TEXT, chars_in INTEGER, chars_out INTEGER, "
    "prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER, usage_reported INTEGER NOT NULL DEFAULT 0, error TEXT, note TEXT)",
    "CREATE INDEX IF NOT EXISTS reservations_month_tier ON reservations (month, tier)",
    "CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, reservation_id INTEGER NOT NULL, n INTEGER NOT NULL, model TEXT, outcome TEXT NOT NULL, status INTEGER, retry INTEGER NOT NULL DEFAULT 0, detail TEXT)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
)


class LedgerUnavailable(AthenaError):
    """The ledger cannot be opened or read, so nothing can be reserved against it."""


class LedgerFull(AthenaError):
    """The tier's units for the month are all taken. `used` is how many are counted."""

    def __init__(self, used: int, limit: int):
        super().__init__(f"{used} of {limit} units are counted")
        self.used, self.limit = used, limit


def classify_failure(attempts: list[Mapping[str, Any]] | None) -> str:
    """The state a reservation takes when no model answered. `refused` only when it is known that no provider processed anything: attempts are known (a list, possibly empty because every model was
    skipped) and each one was an HTTP answer declining the request. No information, a timeout, a network error, a server error or a reply that could not be read is `unknown`, which counts."""
    if attempts is None:
        return UNKNOWN
    for a in attempts:
        status = a.get("status")
        if a.get("outcome") != "http_error" or not isinstance(status, int) or not 400 <= status < DEFINITE_REFUSAL_BELOW or status == 408:
            return UNKNOWN
    return REFUSED


class Ledger:
    """One SQLite file of reservations and attempts. Every operation opens its own short connection, so any number of processes and instances may use one file."""

    def __init__(self, path: Path | str = LEDGER_PATH, busy_seconds: float = BUSY_SECONDS):
        self._path, self._busy = Path(path), busy_seconds
        self._ready = False

    @contextmanager
    def _con(self):  # noqa: ANN202
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self._path, timeout=self._busy, isolation_level=None)
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailable(f"the budget ledger {self._path} cannot be opened ({type(exc).__name__}: {exc})") from exc
        try:
            con.row_factory = sqlite3.Row
            yield con
        except sqlite3.Error as exc:
            raise LedgerUnavailable(f"the budget ledger {self._path} cannot be read or written ({type(exc).__name__}: {exc})") from exc
        finally:
            con.close()

    def ensure(self, legacy: Callable[[], tuple[list[dict[str, Any]], int]] | None = None) -> None:
        """Create the tables and, once, import the earlier usage log (`legacy` returns its valid rows and how many lines could not be read). The import happens in the same transaction that marks it
        done, so two processes starting together import once, and a restart never imports again."""
        if self._ready:
            return
        with self._con() as con:
            for ddl in DDL:
                con.execute(ddl)
            con.execute("BEGIN IMMEDIATE")
            try:
                done = con.execute("SELECT value FROM meta WHERE key = 'legacy_imported'").fetchone()
                if done is None:
                    rows, unreadable = legacy() if legacy is not None else ([], 0)
                    for row in rows:
                        month = str(row.get("at", ""))[:7]
                        if not month or not row.get("tier") or row.get("failed"):
                            continue
                        usage = {k: row.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(row.get(k), int) and not isinstance(row.get(k), bool)}
                        con.execute(
                            "INSERT INTO reservations (logical_id, month, tier, units, state, reserved_at, settled_at, origin, model, chars_in, chars_out, prompt_tokens, completion_tokens, total_tokens, usage_reported) "
                            "VALUES (?, ?, ?, 1, 'settled', ?, ?, 'legacy', ?, ?, ?, ?, ?, ?, ?)",
                            [f"legacy-{os.urandom(4).hex()}", month, str(row["tier"]), str(row.get("at")), str(row.get("at")), row.get("model"), row.get("chars_in"), row.get("chars_out"),
                             usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"), 1 if usage else 0],
                        )
                    con.execute("INSERT INTO meta VALUES ('legacy_imported', ?)", [f"{len(rows)} valid rows, {unreadable} unreadable lines"])
                    con.execute("INSERT OR REPLACE INTO meta VALUES ('legacy_unreadable_lines', ?)", [str(unreadable)])
                    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", [str(SCHEMA)])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        self._ready = True

    def reserve(self, tier: str, month: str, limit: int | None, logical_id: str, now_iso: str) -> int:
        """Reserve one unit for `tier` in `month`: count the units already taken and insert the reservation in one transaction. Raises `LedgerFull` (nothing inserted) when `limit` is reached."""
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                used = con.execute(f"SELECT COALESCE(SUM(units), 0) FROM reservations WHERE tier = ? AND month = ? AND state IN ({','.join('?' * len(COUNTED))})", [tier, month, *COUNTED]).fetchone()[0]
                if limit is not None and used + 1 > limit:
                    con.execute("ROLLBACK")
                    raise LedgerFull(int(used), limit)
                cur = con.execute("INSERT INTO reservations (logical_id, month, tier, units, state, reserved_at, pid) VALUES (?, ?, ?, 1, 'reserved', ?, ?)", [logical_id, month, tier, now_iso, os.getpid()])
                con.execute("COMMIT")
            except LedgerFull:
                raise
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return int(cur.lastrowid)

    def _attempts(self, con: sqlite3.Connection, reservation_id: int, attempts: Iterable[Mapping[str, Any]]) -> None:
        for a in attempts:
            con.execute("INSERT INTO attempts (reservation_id, n, model, outcome, status, retry, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [reservation_id, int(a.get("n", 0)), a.get("model"), str(a.get("outcome", "unknown")), a.get("status"), 1 if a.get("retry") else 0, str(a.get("detail") or "")[:200] or None])

    def settle(self, reservation_id: int, now_iso: str, source: str, chars_in: int, chars_out: int, usage: Mapping[str, int] | None, attempts: Iterable[Mapping[str, Any]] = ()) -> None:
        """A model answered. `usage` is what the provider reported (empty or None when it reported nothing: stored as NULL, `usage_reported` 0)."""
        usage = {k: v for k, v in (usage or {}).items() if k in ("prompt_tokens", "completion_tokens", "total_tokens") and isinstance(v, int) and not isinstance(v, bool)}
        with self._con() as con:
            con.execute("UPDATE reservations SET state = 'settled', settled_at = ?, model = ?, chars_in = ?, chars_out = ?, prompt_tokens = ?, completion_tokens = ?, total_tokens = ?, usage_reported = ? WHERE id = ?",
                        [now_iso, source or None, chars_in, chars_out, usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"), 1 if usage else 0, reservation_id])
            self._attempts(con, reservation_id, attempts)

    def fail(self, reservation_id: int, now_iso: str, error: str, attempts: list[dict[str, Any]] | None) -> str:
        """No model answered. The state is `refused` (does not count) only when it is known nothing was processed, else `unknown` (counts). Returns the state."""
        state = classify_failure(attempts)
        with self._con() as con:
            con.execute("UPDATE reservations SET state = ?, settled_at = ?, error = ? WHERE id = ?", [state, now_iso, str(error)[:400], reservation_id])
            self._attempts(con, reservation_id, attempts or [])
        return state

    def release(self, reservation_id: int, reason: str, by: str, now_iso: str) -> None:
        """Close an unresolved reservation (a process died holding it) after someone checked the provider's side: it stops counting, and the reason and who gave it are kept."""
        if not reason.strip() or not by.strip():
            raise AthenaError("releasing a reservation needs who is releasing it and why")
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT state FROM reservations WHERE id = ?", [reservation_id]).fetchone()
                if row is None or row["state"] not in (RESERVED, UNKNOWN):
                    con.execute("ROLLBACK")
                    raise AthenaError(f"reservation {reservation_id} is not an unresolved reservation")
                con.execute("UPDATE reservations SET state = 'released', settled_at = ?, note = ? WHERE id = ?", [now_iso, f"released by {by.strip()}: {reason.strip()}"[:400], reservation_id])
                con.execute("COMMIT")
            except AthenaError:
                raise
            except BaseException:
                con.execute("ROLLBACK")
                raise

    # --- reading ---
    def counted(self, tier: str, month: str) -> int:
        with self._con() as con:
            return int(con.execute(f"SELECT COALESCE(SUM(units), 0) FROM reservations WHERE tier = ? AND month = ? AND state IN ({','.join('?' * len(COUNTED))})", [tier, month, *COUNTED]).fetchone()[0])

    def unresolved(self) -> list[dict[str, Any]]:
        """Reservations never finished (`reserved`) or finished with an unknown outcome (`unknown`): the ones a person may need to look at."""
        with self._con() as con:
            rows = con.execute("SELECT id, logical_id, month, tier, state, reserved_at, pid, error FROM reservations WHERE state IN ('reserved', 'unknown') ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def meta(self) -> dict[str, str]:
        with self._con() as con:
            return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta").fetchall()}

    def summary(self, month: str) -> dict[str, dict[str, Any]]:
        """Per tier for `month`: logical requests counted, answered, unknown-outcome and unresolved, provider attempts and retries, characters, tokens the providers reported (and how many answered calls
        reported none), and the models that answered."""
        out: dict[str, dict[str, Any]] = {}
        with self._con() as con:
            for r in con.execute("SELECT * FROM reservations WHERE month = ? ORDER BY id", [month]).fetchall():
                t = out.setdefault(r["tier"], {"logical_requests": 0, "calls": 0, "unknown_outcome": 0, "unresolved": 0, "refused": 0, "released": 0, "chars_in": 0, "chars_out": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                               "calls_without_usage": 0, "models": {}, "provider_attempts": 0, "retries": 0})
                state = r["state"]
                if state in COUNTED:
                    t["logical_requests"] += r["units"]
                if state == SETTLED:
                    t["calls"] += 1
                    t["chars_in"] += r["chars_in"] or 0
                    t["chars_out"] += r["chars_out"] or 0
                    t["prompt_tokens"] += r["prompt_tokens"] or 0
                    t["completion_tokens"] += r["completion_tokens"] or 0
                    t["calls_without_usage"] += 0 if r["usage_reported"] else 1
                    t["models"][r["model"] or ""] = t["models"].get(r["model"] or "", 0) + 1
                elif state == UNKNOWN:
                    t["unknown_outcome"] += 1
                elif state == RESERVED:
                    t["unresolved"] += 1
                elif state == REFUSED:
                    t["refused"] += 1
                elif state == RELEASED:
                    t["released"] += 1
            for tier, n, retries in con.execute("SELECT r.tier, COUNT(a.id), COALESCE(SUM(a.retry), 0) FROM attempts a JOIN reservations r ON r.id = a.reservation_id WHERE r.month = ? GROUP BY r.tier", [month]).fetchall():
                out.setdefault(tier, {"logical_requests": 0, "calls": 0})
                out[tier]["provider_attempts"], out[tier]["retries"] = int(n), int(retries)
        return out
