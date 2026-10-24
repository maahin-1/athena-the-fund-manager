"""Search over a company's documents (annual reports, concall transcripts, announcements) that cannot see the future and cannot misquote.

Three rules make it safe to give to a model:
  1. Point in time. Every passage carries the date its document was published; a search "as of" a day never returns a passage published after it.
  2. Hybrid. Keyword ranking (BM25, which finds the exact term: "pledge", "going concern") and, when an embedder is given, meaning ranking (cosine over embeddings, which finds the same idea in other words)
     are fused by reciprocal rank, so a passage both agree on rises.
  3. Quoted. A hit is the passage text exactly as stored, with its document and date; a model that cites one must quote it, and `verify_quotes` checks that each quote really is in the passage
     it cites, so a paraphrase or an invention is caught.

The index holds one company's passages at a time (a company has hundreds, not millions), built from the stored chunks; the embedder is optional and pluggable (`embed(texts) -> vectors`)."""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

CHUNK_WORDS = 180
CHUNK_OVERLAP = 30
RRF_K = 60
BM25_K1, BM25_B = 1.5, 0.75
_WORD = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")
STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were will with".split())

Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


@dataclass(frozen=True)
class Passage:
    doc_id: str  # which document (its url or an id the caller keeps)
    title: str
    published: date
    chunk: int
    text: str


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float
    keyword_rank: int | None
    meaning_rank: int | None


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in STOP]


def chunk(text: str, words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """The text cut into pieces of about `words` words, each starting `overlap` words before the last one ended, so a sentence on a cut is whole in one of them. The words are kept as written."""
    parts = text.split()
    if not parts:
        return []
    step = max(1, words - overlap)
    return [" ".join(parts[i : i + words]) for i in range(0, max(1, len(parts) - overlap), step)]


def passages(doc_id: str, title: str, published: date, text: str) -> list[Passage]:
    return [Passage(doc_id, title, published, n, piece) for n, piece in enumerate(chunk(text))]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class Index:
    def __init__(self, found: Sequence[Passage], embed: Embedder | None = None):
        self._passages = list(found)
        self._tokens = [tokens(p.text) for p in self._passages]
        self._df: Counter[str] = Counter(t for toks in self._tokens for t in set(toks))
        self._avg = sum(len(t) for t in self._tokens) / len(self._tokens) if self._tokens else 0.0
        self._embed = embed
        self._vectors = list(embed([p.text for p in self._passages])) if embed and self._passages else []

    def _bm25(self, query: Sequence[str], i: int) -> float:
        counts, length, n = Counter(self._tokens[i]), len(self._tokens[i]), len(self._passages)
        score = 0.0
        for term in set(query):
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
            score += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length / (self._avg or 1.0)))
        return score

    def search(self, query: str, as_of: date, k: int = 5) -> list[Hit]:
        """The `k` best passages published on or before `as_of`."""
        allowed = [i for i, p in enumerate(self._passages) if p.published <= as_of]
        if not allowed:
            return []
        q = tokens(query)
        keyword = sorted(((self._bm25(q, i), i) for i in allowed), reverse=True)
        keyword_rank = {i: r for r, (s, i) in enumerate(keyword, start=1) if s > 0}
        meaning_rank: dict[int, int] = {}
        if self._embed is not None and self._vectors:
            qv = list(self._embed([query]))[0]
            ranked = sorted(((_cosine(qv, self._vectors[i]), i) for i in allowed), reverse=True)
            meaning_rank = {i: r for r, (_, i) in enumerate(ranked, start=1)}
        fused = {i: sum(1.0 / (RRF_K + r[i]) for r in (keyword_rank, meaning_rank) if i in r) for i in set(keyword_rank) | set(meaning_rank)}
        order = sorted(fused, key=lambda i: (-fused[i], i))[:k]
        return [Hit(self._passages[i], fused[i], keyword_rank.get(i), meaning_rank.get(i)) for i in order]


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()


def verify_quotes(quotes: Mapping[str, str], hits: Sequence[Hit]) -> list[str]:
    """For `quotes` as {passage key: quoted text}, where the key is `doc_id#chunk`, the keys whose quote is not found in that passage (whitespace and case aside), or whose passage was not among the hits."""
    held = {f"{h.passage.doc_id}#{h.passage.chunk}": _squash(h.passage.text) for h in hits}
    return [key for key, quote in quotes.items() if key not in held or not _squash(quote) or _squash(quote) not in held[key]]
