"""The vocabulary of an investigation: cases, lanes, and the typed entries agents write to the shared ledger."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

OBSERVATION, FLAG, QUESTION, ANSWER, NOTE = "OBSERVATION", "FLAG", "QUESTION", "ANSWER", "NOTE"
KINDS = (OBSERVATION, FLAG, QUESTION, ANSWER, NOTE)

# what a flag is a difference between
ARITHMETIC = "arithmetic"  # two verified numbers that cannot both be right
DOC_VS_DOC = "document_vs_document"
TEXT_VS_DATA = "text_vs_data"
CLAIM_VS_OUTSIDE = "claim_vs_outside"
ANOMALY_VS_PEERS = "anomaly_vs_peers"
FLAG_TYPES = (ARITHMETIC, DOC_VS_DOC, TEXT_VS_DATA, CLAIM_VS_OUTSIDE, ANOMALY_VS_PEERS)

# status of a ledger entry
OPEN, ANSWERED, ESTABLISHED, SINGLE_SOURCE, DUPLICATE, REJECTED, UNROUTABLE = "open", "answered", "established", "single_source", "duplicate", "rejected", "unroutable"


@dataclass(frozen=True)
class Case:
    """One target at one date. Everything an agent sees is dated on or before `as_of`."""

    case_id: str
    kind: str  # "drhp", "listed" or "news"
    target: str  # a symbol or a document id
    as_of: date
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LaneSpec:
    """An agent's lane: what it is for, which tools it may call and how many steps it gets in a round."""

    name: str
    purpose: str
    tools: tuple[str, ...]
    step_cap: int = 6


@dataclass
class Entry:
    id: str
    case_id: str
    kind: str
    author: str
    round: int
    status: str
    body: dict[str, Any]
    reason: str = ""

    @property
    def text(self) -> str:
        return str(self.body.get("text", ""))

    @property
    def evidence(self) -> list[str]:
        return [str(e) for e in self.body.get("evidence") or []]
