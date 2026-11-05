"""The document store: `.athena/documents.duckdb`.

    docs       one row per document read: where it came from, its content hash (a document is read once; the same bytes are never read twice), page count, the pages that gave no text
    pages      the text of every page, with the section the page falls in
    layout_pages  for pages that look like statement pages, the same page read again with the text placed by position on the page (`pages.text` is the reading order of the PDF, which on some
               drafts separates a label from its figures); the checks lane chooses between the two per page
    sections   the sections the finder saw, with their page ranges
    figures    the offer figures taken by program, as JSON, with the checks that ran on them

The file is opened for a moment per call, as the evidence and IPO files are, so a fetch never holds up a screen."""
from __future__ import annotations

import json
import time
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.documents.sections import Section

DEFAULT_PATH = Path(".athena") / "documents.duckdb"
DDL = (
    "CREATE TABLE IF NOT EXISTS docs (doc_id VARCHAR PRIMARY KEY, kind VARCHAR, company VARCHAR, url VARCHAR, sha256 VARCHAR NOT NULL UNIQUE, published DATE, n_pages INTEGER, unreadable VARCHAR,"
    " fetched_at TIMESTAMP NOT NULL)",
    "CREATE TABLE IF NOT EXISTS pages (doc_id VARCHAR NOT NULL, page INTEGER NOT NULL, section VARCHAR, text VARCHAR, PRIMARY KEY (doc_id, page))",
    "CREATE TABLE IF NOT EXISTS layout_pages (doc_id VARCHAR NOT NULL, page INTEGER NOT NULL, text VARCHAR, PRIMARY KEY (doc_id, page))",
    "CREATE TABLE IF NOT EXISTS sections (doc_id VARCHAR NOT NULL, key VARCHAR NOT NULL, title VARCHAR, start_page INTEGER, end_page INTEGER, PRIMARY KEY (doc_id, key))",
    "CREATE TABLE IF NOT EXISTS figures (doc_id VARCHAR PRIMARY KEY, body VARCHAR NOT NULL, built_at TIMESTAMP NOT NULL)",
    "CREATE TABLE IF NOT EXISTS analysis (doc_id VARCHAR PRIMARY KEY, body VARCHAR NOT NULL, built_at TIMESTAMP NOT NULL)",
)


class DocStore:
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
    def add(self, doc_id: str, *, kind: str, company: str, url: str, sha256: str, published: date | None, pages: Sequence[str], unreadable: Sequence[int], sections: Sequence[Section],
            section_of: Any, now: datetime) -> None:
        """Keep a document: its pages (each with its section), its sections and its unreadable pages."""
        stamp = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
        with self._open() as con:
            con.execute("INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [doc_id, kind, company, url, sha256, published, len(pages), json.dumps(list(unreadable)), stamp])
            con.executemany("INSERT INTO pages VALUES (?, ?, ?, ?)", [(doc_id, n, section_of(sections, n), text) for n, text in enumerate(pages, start=1)])
            if sections:  # a document whose sections were not found is still kept
                con.executemany("INSERT INTO sections VALUES (?, ?, ?, ?, ?)", [(doc_id, s.key, s.title, s.start, s.end) for s in sections])

    def save_layout(self, doc_id: str, pages: dict[int, str]) -> None:
        """The layout-mode text of some pages of a document (reading again replaces what was kept)."""
        with self._open() as con:
            con.execute("DELETE FROM layout_pages WHERE doc_id = ?", [doc_id])
            if pages:
                con.executemany("INSERT INTO layout_pages VALUES (?, ?, ?)", [(doc_id, n, text) for n, text in sorted(pages.items())])

    def save_figures(self, doc_id: str, body: dict[str, Any], now: datetime) -> None:
        stamp = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
        with self._open() as con:
            con.execute("DELETE FROM figures WHERE doc_id = ?", [doc_id])
            con.execute("INSERT INTO figures VALUES (?, ?, ?)", [doc_id, json.dumps(body, ensure_ascii=False), stamp])

    def save_analysis(self, doc_id: str, answers: list[dict[str, Any]], now: datetime) -> None:
        """The answers to the standard questions (one list per document; asking again replaces it)."""
        stamp = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
        with self._open() as con:
            con.execute("DELETE FROM analysis WHERE doc_id = ?", [doc_id])
            con.execute("INSERT INTO analysis VALUES (?, ?, ?)", [doc_id, json.dumps(answers, ensure_ascii=False), stamp])

    # --- reading ---
    def analysis(self, doc_id: str) -> list[dict[str, Any]] | None:
        with self._open() as con:
            row = con.execute("SELECT body FROM analysis WHERE doc_id = ?", [doc_id]).fetchone()
        return json.loads(row[0]) if row else None

    def by_hash(self, sha256: str) -> str | None:
        with self._open() as con:
            row = con.execute("SELECT doc_id FROM docs WHERE sha256 = ?", [sha256]).fetchone()
        return row[0] if row else None

    def by_url(self, url: str) -> str | None:
        with self._open() as con:
            row = con.execute("SELECT doc_id FROM docs WHERE url = ? ORDER BY fetched_at DESC LIMIT 1", [url]).fetchone()
        return row[0] if row else None

    def document(self, doc_id: str) -> dict[str, Any] | None:
        with self._open() as con:
            row = con.execute("SELECT doc_id, kind, company, url, sha256, published, n_pages, unreadable, fetched_at FROM docs WHERE doc_id = ?", [doc_id]).fetchone()
        if not row:
            return None
        keys = ("doc_id", "kind", "company", "url", "sha256", "published", "n_pages", "unreadable", "fetched_at")
        out = dict(zip(keys, row))
        out["unreadable"] = json.loads(out["unreadable"] or "[]")
        return out

    def pages(self, doc_id: str, sections: Sequence[str] | None = None) -> list[tuple[int, str, str]]:
        """(page, section, text) in page order, limited to the named sections when given."""
        with self._open() as con:
            rows = con.execute("SELECT page, section, text FROM pages WHERE doc_id = ? ORDER BY page", [doc_id]).fetchall()
        return [r for r in rows if sections is None or r[1] in sections]

    def layout(self, doc_id: str) -> dict[int, str]:
        """The layout-mode text kept for some pages, by page number (empty when none was kept, or the store is older than this table)."""
        try:
            with self._open() as con:
                rows = con.execute("SELECT page, text FROM layout_pages WHERE doc_id = ?", [doc_id]).fetchall()
        except duckdb.CatalogException:
            return {}
        return {int(page): text or "" for page, text in rows}

    def sections(self, doc_id: str) -> list[Section]:
        with self._open() as con:
            rows = con.execute("SELECT key, title, start_page, end_page FROM sections WHERE doc_id = ? ORDER BY start_page", [doc_id]).fetchall()
        return [Section(*r) for r in rows]

    def figures(self, doc_id: str) -> dict[str, Any] | None:
        with self._open() as con:
            row = con.execute("SELECT body FROM figures WHERE doc_id = ?", [doc_id]).fetchone()
        return json.loads(row[0]) if row else None
