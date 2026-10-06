from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Protocol

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, UnknownInstrument
from athena.freshness import check_fresh
from athena.isin import isin_checksum_ok, isin_type_hint, looks_like_isin
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.routing import route
from athena.store import DataStore
from athena.trading_calendar import TradingCalendar

logger = logging.getLogger("athena.resolver")

ACCEPT_SCORE = 0.90  # proposed defaults; tune on the labeled resolver set
ACCEPT_MARGIN = 0.05
CANDIDATE_FLOOR = 0.55
MODEL_THRESHOLD = 0.85
MAX_CANDIDATES = 5  # what the model classifier and `Resolution.candidates` see
MAX_LISTED = 50  # the most candidates a person is shown when a query is ambiguous
CLI_SHOWN = 10  # how many a text report prints before saying how many more there are
MIN_PREFIX_LENGTH = 3
PREFIX_BASE = 0.70  # a prefix match scores 0.70-0.89: it ranks first but can never reach ACCEPT_SCORE on its own
PREFIX_SPAN = 0.19
_NAME_STOPWORDS = {"limited", "ltd"}


def more_candidates_line(total: int, shown: int) -> str | None:
    """The closing line of a text list that was cut short, or None when everything was shown."""
    return f"  ...and {total - shown} more; type the exact symbol." if total > shown else None


def normalize_input(text: str) -> str:
    cleaned = " ".join(text.strip().upper().split())
    for prefix in ("NSE:", "BSE:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    for suffix in (".NS", ".BO"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned


def normalize_name(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    words = re.sub(r"[^a-z0-9 ]", " ", lowered).split()
    return " ".join(word for word in words if word not in _NAME_STOPWORDS)


@dataclass(frozen=True)
class Candidate:
    asset_class: str
    identifier: str
    name: str
    score: float


@dataclass(frozen=True)
class Resolution:
    asset_class: str
    identifier_type: str  # "ticker" | "isin" | "name"
    identifier: str
    name: str
    isin: str
    resolution_path: str  # "exact" | "fuzzy" | "model" | "user_confirmed"
    confidence: float
    candidates: tuple[Candidate, ...]
    routed_specialists: tuple[str, ...]


@dataclass(frozen=True)
class Ambiguity:
    query: str
    candidates: tuple[Candidate, ...]
    reason: str


class Classifier(Protocol):
    def choose(self, query: str, candidates: Sequence[Candidate]) -> tuple[int, float]:
        """Return (index into candidates, probability). Only ever chooses among the given candidates."""
        ...


@dataclass(frozen=True)
class _Entry:
    asset_class: str
    symbol: str
    name: str
    isin: str
    norm_name: str


class InstrumentIndex:
    def __init__(self, entries: Sequence[_Entry]) -> None:
        self.entries = list(entries)
        self.by_symbol: dict[str, _Entry] = {}
        self.by_isin: dict[str, _Entry] = {}
        self.by_name: dict[str, list[_Entry]] = {}
        for entry in self.entries:  # callers pass equities before ETFs so an ETF wins a symbol tie
            self.by_symbol[entry.symbol] = entry
            if entry.isin:
                self.by_isin[entry.isin] = entry
        for entry in self.by_symbol.values():
            self.by_name.setdefault(entry.norm_name, []).append(entry)

    @classmethod
    def from_store(
        cls,
        store: DataStore,
        now: datetime | None = None,
        calendar: TradingCalendar | None = None,
    ) -> InstrumentIndex:
        now = now or utc_now()
        entries: list[_Entry] = []
        for dataset, asset_class in ((EQUITY_DATASET, "equity"), (ETF_DATASET, "etf")):
            records = store.latest_records(dataset)
            if not records:
                raise EmptyRefreshError(f"no {dataset} data; run the NSE master loader first")
            check_fresh(dataset, max(record.as_of for record in records), now, calendar=calendar)
            for record in records:
                name = record.payload["name"]
                entries.append(
                    _Entry(asset_class, record.key, name, record.payload["isin"], normalize_name(name))
                )
        return cls(entries)


def _score(query: str, target: str) -> float:
    if not query or not target:
        return 0.0
    matcher = SequenceMatcher(None, query, target, autojunk=False)
    if matcher.real_quick_ratio() < CANDIDATE_FLOOR or matcher.quick_ratio() < CANDIDATE_FLOOR:
        return 0.0
    return matcher.ratio()


def _prefix_score(symbol_query: str, name_key: str, entry: _Entry) -> float:
    """0.0 unless the query starts the symbol or the name; shorter targets score higher."""
    best = 0.0
    if len(symbol_query) >= MIN_PREFIX_LENGTH and entry.symbol.lower().startswith(symbol_query):
        best = PREFIX_BASE + PREFIX_SPAN * len(symbol_query) / len(entry.symbol)
    if len(name_key) >= MIN_PREFIX_LENGTH and entry.norm_name.startswith(name_key):
        best = max(best, PREFIX_BASE + PREFIX_SPAN * len(name_key) / len(entry.norm_name))
    return best


class InstrumentResolver:
    """`confirm_related` is for a person at a screen: only a typed ticker or ISIN is taken as is. A name, a typo or a
    short prefix is never settled by a guess (a similarity score or a model): when other instruments are related to
    what was typed, they are all listed, the closest first, for the person to choose."""

    def __init__(self, index: InstrumentIndex, classifier: Classifier | None = None, confirm_related: bool = False) -> None:
        self._index = index
        self._classifier = classifier
        self._confirm_related = confirm_related

    def resolve(self, query: str) -> Resolution | Ambiguity:
        text = normalize_input(query)
        if not text:
            raise ValueError("empty query")

        entry = self._index.by_symbol.get(text)
        if entry:
            return self._resolved(entry, "ticker", "exact", 1.0)

        if looks_like_isin(text):
            return self._resolve_isin(text)

        name_key = normalize_name(query)
        matches = self._index.by_name.get(name_key, [])
        if len(matches) == 1 and self._confirm_related:
            related = self._related(text, name_key, exclude=matches[0])
            if related:  # the exact name first, then everything else related to it
                listed = (self._candidate(matches[0], 1.0), *related)[:MAX_LISTED]
                return Ambiguity(query, listed, "other instruments are related to this name")
        if len(matches) == 1:
            return self._resolved(matches[0], "name", "exact", 1.0)
        if len(matches) > 1:
            candidates = tuple(self._candidate(m, 1.0) for m in matches[:MAX_LISTED])
            return Ambiguity(query, candidates, "the name matches more than one instrument")

        return self._resolve_fuzzy(query, text, name_key)

    def confirm(self, ambiguity: Ambiguity, index: int) -> Resolution:
        chosen = ambiguity.candidates[index]
        entry = self._index.by_symbol[chosen.identifier]
        return self._resolved(entry, "ticker", "user_confirmed", 1.0, ambiguity.candidates)

    def _resolve_isin(self, text: str) -> Resolution:
        if not isin_checksum_ok(text):
            raise UnknownInstrument(f"{text} is not a valid ISIN (check digit does not match)")
        entry = self._index.by_isin.get(text)
        if entry:
            return self._resolved(entry, "isin", "exact", 1.0)
        note = ""
        if isin_type_hint(text) == "fund_or_etf":
            note = " It looks like a fund ISIN (INF); mutual-fund support is not available yet."
        raise UnknownInstrument(f"ISIN {text} is not in the NSE equity or ETF lists.{note}")

    def _scored(self, text: str, name_key: str) -> list[tuple[float, float, bool, str, _Entry]]:
        """Every instrument that looks related to the query: (edit score, shown score, is prefix, kind, entry)."""
        symbol_query = text.lower()
        scored: list[tuple[float, float, bool, str, _Entry]] = []
        for entry in self._index.by_symbol.values():
            name_score = _score(name_key, entry.norm_name)
            symbol_score = _score(symbol_query, entry.symbol.lower())
            edit = max(name_score, symbol_score)
            prefix = _prefix_score(symbol_query, name_key, entry)
            shown = max(edit, prefix)
            if shown >= CANDIDATE_FLOOR:
                kind = "name" if name_score >= symbol_score else "ticker"
                scored.append((edit, shown, prefix > 0.0, kind, entry))
        return scored

    def _related(self, text: str, name_key: str, exclude: _Entry) -> tuple[Candidate, ...]:
        scored = [item for item in self._scored(text, name_key) if item[4] is not exclude]
        ranked = sorted(scored, key=lambda i: (not i[2], -i[1], i[4].asset_class != "etf", i[4].symbol))
        return tuple(self._candidate(i[4], i[1]) for i in ranked)

    def _resolve_fuzzy(self, query: str, text: str, name_key: str) -> Resolution | Ambiguity:
        scored = self._scored(text, name_key)
        if not scored:
            raise UnknownInstrument(f"no instrument matches {query!r}")

        ranked = sorted(scored, key=lambda i: (not i[2], -i[1], i[4].asset_class != "etf", i[4].symbol))
        listed = tuple(self._candidate(i[4], i[1]) for i in ranked[:MAX_LISTED])
        candidates = listed[:MAX_CANDIDATES]

        by_edit = sorted(scored, key=lambda i: (-i[0], i[4].asset_class != "etf", i[4].symbol))
        top_edit, _, _, top_kind, top_entry = by_edit[0]
        runner_up = by_edit[1][0] if len(by_edit) > 1 else 0.0
        if self._confirm_related:  # a person chooses; nothing is settled by a score or a model, unless nothing else is related
            if len(listed) == 1 and top_edit >= ACCEPT_SCORE:
                return self._resolved(top_entry, top_kind, "fuzzy", round(top_edit, 4), candidates)
            return Ambiguity(query, listed, "other instruments are related to what you typed")
        if top_edit >= ACCEPT_SCORE and top_edit - runner_up >= ACCEPT_MARGIN:
            return self._resolved(top_entry, top_kind, "fuzzy", round(top_edit, 4), candidates)

        if self._classifier is not None:
            try:
                index, probability = self._classifier.choose(query, candidates)
            except Exception:
                logger.warning("classifier failed for %r; asking the user instead", query, exc_info=True)
            else:
                if probability >= MODEL_THRESHOLD and 0 <= index < len(candidates):
                    entry = self._index.by_symbol[candidates[index].identifier]
                    return self._resolved(entry, "name", "model", probability, candidates)
        return Ambiguity(query, listed, "no single instrument is a clear match")

    @staticmethod
    def _candidate(entry: _Entry, score: float) -> Candidate:
        return Candidate(entry.asset_class, entry.symbol, entry.name, round(score, 4))

    def _resolved(
        self,
        entry: _Entry,
        identifier_type: str,
        path: str,
        confidence: float,
        candidates: tuple[Candidate, ...] = (),
    ) -> Resolution:
        return Resolution(
            asset_class=entry.asset_class,
            identifier_type=identifier_type,
            identifier=entry.symbol,
            name=entry.name,
            isin=entry.isin,
            resolution_path=path,
            confidence=confidence,
            candidates=candidates,
            routed_specialists=route(entry.asset_class),
        )
