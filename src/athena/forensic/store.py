"""The forensic store: `.athena/forensic.duckdb`.

    cases      one row per target and date
    evidence   every tool result an agent saw, whole, with an id (E1, E2...) that findings cite
    ledger     the shared findings: observations, flags, questions, answers (L1, L2...), each with author, round and status
    notes      each agent's private notebook for the case
    calls      one row per model call (lane, round, characters in and out), for the cost line of a report
    reports    the report of each investigation

Opened for a moment per call, like the evidence and IPO files."""
from __future__ import annotations

import json
import time
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import AthenaError
from athena.forensic.model import Case, Entry

DEFAULT_PATH = Path(".athena") / "forensic.duckdb"
DDL = (
    "CREATE TABLE IF NOT EXISTS cases (case_id VARCHAR PRIMARY KEY, kind VARCHAR, target VARCHAR, as_of DATE, meta VARCHAR, created_at TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS evidence (case_id VARCHAR NOT NULL, ev_id VARCHAR NOT NULL, lane VARCHAR, tool VARCHAR, args VARCHAR, result VARCHAR, created_at TIMESTAMP, PRIMARY KEY (case_id, ev_id))",
    "CREATE TABLE IF NOT EXISTS ledger (case_id VARCHAR NOT NULL, id VARCHAR NOT NULL, kind VARCHAR, author VARCHAR, round INTEGER, status VARCHAR, body VARCHAR, reason VARCHAR, created_at TIMESTAMP,"
    " PRIMARY KEY (case_id, id))",
    "CREATE TABLE IF NOT EXISTS notes (case_id VARCHAR NOT NULL, lane VARCHAR NOT NULL, round INTEGER, text VARCHAR, created_at TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS calls (case_id VARCHAR NOT NULL, lane VARCHAR, round INTEGER, chars_in INTEGER, chars_out INTEGER, ok BOOLEAN, created_at TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS reports (case_id VARCHAR NOT NULL, built_at TIMESTAMP, body VARCHAR)",
    # what the provider said the call used and which model answered (empty when it said nothing: the cost line then estimates from the characters)
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS model VARCHAR",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS completion_tokens INTEGER",
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ForensicStore:
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

    # --- cases ---
    def add_case(self, case: Case) -> None:
        """Evidence, ledger, notes and call counts are all keyed by the case id, so an id belongs to one target at one date; a second investigation needs its own id (the command lines add a run stamp)."""
        with self._open() as con:
            held = con.execute("SELECT kind, target, as_of FROM cases WHERE case_id = ?", [case.case_id]).fetchone()
            if held is None:
                con.execute("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?)", [case.case_id, case.kind, case.target, case.as_of, json.dumps(dict(case.meta), default=str), _now()])
            elif (held[0], held[1], held[2]) != (case.kind, case.target, case.as_of):
                raise AthenaError(f"case {case.case_id!r} already exists for {held[0]} {held[1]} as of {held[2]}; use a new case id for {case.kind} {case.target} as of {case.as_of}")

    # --- evidence ---
    def add_evidence(self, case_id: str, lane: str, tool: str, args: dict[str, Any], result: Any) -> str:
        with self._open() as con:
            n = con.execute("SELECT count(*) FROM evidence WHERE case_id = ?", [case_id]).fetchone()[0] + 1
            ev_id = f"E{n}"
            con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)", [case_id, ev_id, lane, tool, json.dumps(args, default=str), json.dumps(result, ensure_ascii=False, default=str), _now()])
        return ev_id

    def find_evidence(self, case_id: str, tool: str, args: dict[str, Any]) -> str | None:
        """The id of an earlier result of the same tool with the same arguments in this case: tools are deterministic, so asking again gives the same evidence, not a second copy."""
        with self._open() as con:
            row = con.execute("SELECT ev_id FROM evidence WHERE case_id = ? AND tool = ? AND args = ? ORDER BY created_at LIMIT 1", [case_id, tool, json.dumps(args, default=str)]).fetchone()
        return row[0] if row else None

    def evidence(self, case_id: str, ev_id: str) -> dict[str, Any] | None:
        with self._open() as con:
            row = con.execute("SELECT lane, tool, args, result FROM evidence WHERE case_id = ? AND ev_id = ?", [case_id, ev_id]).fetchone()
        return {"lane": row[0], "tool": row[1], "args": json.loads(row[2]), "result": json.loads(row[3])} if row else None

    def evidence_calls(self, case_id: str, lane: str) -> list[tuple[str, str, str]]:
        """(ev_id, tool, args as written) of what a lane has already looked at, so it is not asked for twice."""
        with self._open() as con:
            rows = con.execute("SELECT ev_id, tool, args FROM evidence WHERE case_id = ? AND lane = ? ORDER BY created_at, ev_id", [case_id, lane]).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- ledger ---
    def add_entry(self, case_id: str, kind: str, author: str, round_no: int, body: dict[str, Any], status: str = "open", reason: str = "") -> str:
        with self._open() as con:
            n = con.execute("SELECT count(*) FROM ledger WHERE case_id = ?", [case_id]).fetchone()[0] + 1
            entry_id = f"L{n}"
            con.execute("INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [case_id, entry_id, kind, author, round_no, status, json.dumps(body, ensure_ascii=False, default=str), reason, _now()])
        return entry_id

    def entries(self, case_id: str, kind: str | None = None, status: str | None = None, author: str | None = None) -> list[Entry]:
        with self._open() as con:
            rows = con.execute("SELECT id, kind, author, round, status, body, reason FROM ledger WHERE case_id = ? ORDER BY created_at, id", [case_id]).fetchall()
        out = [Entry(r[0], case_id, r[1], r[2], r[3], r[4], json.loads(r[5]), r[6] or "") for r in rows]
        out.sort(key=lambda e: int(e.id[1:]))
        return [e for e in out if (kind is None or e.kind == kind) and (status is None or e.status == status) and (author is None or e.author == author)]

    def entry(self, case_id: str, entry_id: str) -> Entry | None:
        return next((e for e in self.entries(case_id) if e.id == entry_id), None)

    def set_status(self, case_id: str, entry_id: str, status: str, reason: str = "") -> None:
        with self._open() as con:
            con.execute("UPDATE ledger SET status = ?, reason = ? WHERE case_id = ? AND id = ?", [status, reason, case_id, entry_id])

    # --- notebooks ---
    def add_note(self, case_id: str, lane: str, round_no: int, text: str) -> None:
        with self._open() as con:
            con.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", [case_id, lane, round_no, text, _now()])

    def notes(self, case_id: str, lane: str) -> list[str]:
        with self._open() as con:
            rows = con.execute("SELECT text FROM notes WHERE case_id = ? AND lane = ? ORDER BY created_at", [case_id, lane]).fetchall()
        return [r[0] for r in rows]

    # --- cost and reports ---
    def add_call(self, case_id: str, lane: str, round_no: int, chars_in: int, chars_out: int, ok: bool, model: str = "", prompt_tokens: int | None = None, completion_tokens: int | None = None) -> None:
        with self._open() as con:
            con.execute(
                "INSERT INTO calls (case_id, lane, round, chars_in, chars_out, ok, created_at, model, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [case_id, lane, round_no, chars_in, chars_out, ok, _now(), model or None, prompt_tokens, completion_tokens],
            )

    def calls(self, case_id: str) -> list[dict[str, Any]]:
        with self._open() as con:
            rows = con.execute("SELECT lane, round, chars_in, chars_out, ok, model, prompt_tokens, completion_tokens FROM calls WHERE case_id = ? ORDER BY created_at", [case_id]).fetchall()
        return [{"lane": r[0], "round": r[1], "chars_in": r[2], "chars_out": r[3], "ok": r[4], "model": r[5] or "", "prompt_tokens": r[6], "completion_tokens": r[7]} for r in rows]

    def save_report(self, case_id: str, body: dict[str, Any]) -> None:
        with self._open() as con:
            con.execute("INSERT INTO reports VALUES (?, ?, ?)", [case_id, _now(), json.dumps(body, ensure_ascii=False, default=str)])

    def report(self, case_id: str) -> dict[str, Any] | None:
        with self._open() as con:
            row = con.execute("SELECT body FROM reports WHERE case_id = ? ORDER BY built_at DESC LIMIT 1", [case_id]).fetchone()
        return json.loads(row[0]) if row else None


__all__: Sequence[str] = ["ForensicStore", "DEFAULT_PATH"]
