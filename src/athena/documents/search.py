"""Find the few pages of a stored document that answer a question, with the page and section of every passage.

It reuses the keyword ranking and the quote check of `athena.retrieval.index` (the same checks the annual-report review relies on) over passages cut from the stored pages. A question names the
sections it should read (the risks of a company are in `risk_factors`, what the money is for in `objects`), so a search over a 500-page document looks only at the pages that can hold the answer."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.documents.store import DocStore
from athena.retrieval.index import Hit, Index, Passage, chunk, verify_quotes

CHUNK_WORDS = 200
CHUNK_OVERLAP = 40


@dataclass(frozen=True)
class Found:
    key: str  # the passage key a quote must cite: doc_id#chunk
    page: int
    section: str
    text: str
    score: float


class Finder:
    def __init__(self, store: DocStore, doc_id: str, as_of: date | None = None):
        doc = store.document(doc_id)
        if doc is None:
            raise ValueError(f"no document {doc_id}")
        self._doc_id = doc_id
        self._published = as_of or doc["published"] or doc["fetched_at"].date()
        self._pages = store.pages(doc_id)
        self._cache: dict[tuple[str, ...], tuple[Index, dict[int, tuple[int, str]]]] = {}

    def _index(self, sections: Sequence[str] | None) -> tuple[Index, dict[int, tuple[int, str]]]:
        wanted = tuple(sorted(sections)) if sections else ()
        if wanted not in self._cache:
            passages: list[Passage] = []
            where: dict[int, tuple[int, str]] = {}
            for page, section, text in self._pages:
                if wanted and section not in wanted:
                    continue
                for piece in chunk(text, CHUNK_WORDS, CHUNK_OVERLAP):
                    where[len(passages)] = (page, section)
                    passages.append(Passage(self._doc_id, f"{section}, page {page}", self._published, len(passages), piece))
            self._cache[wanted] = (Index(passages), where)
        return self._cache[wanted]

    def search(self, query: str, sections: Sequence[str] | None = None, k: int = 5) -> list[Found]:
        index, where = self._index(sections)
        return [self._found(h, where) for h in index.search(query, self._published, k)]

    def hits(self, query: str, sections: Sequence[str] | None = None, k: int = 5) -> list[Hit]:
        index, _ = self._index(sections)
        return index.search(query, self._published, k)

    def opening(self, sections: Sequence[str], k: int = 5) -> list[Found]:
        """The first `k` passages of the named sections, in document order: for a question about how a section begins (a document lists its most important risks first)."""
        index, where = self._index(sections)
        return [self._found(Hit(p, 0.0, None, None), where) for p in index.first(k)]

    def _found(self, hit: Hit, where: dict[int, tuple[int, str]]) -> Found:
        page, section = where[hit.passage.chunk]
        return Found(f"{hit.passage.doc_id}#{hit.passage.chunk}", page, section, hit.passage.text, hit.score)


__all__ = ["Finder", "Found", "verify_quotes"]
