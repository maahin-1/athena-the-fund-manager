"""Which underlying source an evidence row read, so a second reading of one source is not mistaken for a second source (audit A2, WP05).

Two evidence ids can be one document: `doc.open` and `doc.search` read the same file, and so do the `checks` lane's `doc.checks` and the text lane's `doc.map`. Two lanes can read one stored set of
statements. So independence is decided on the *source*, not the evidence id, the tool or the lane: a source is a kind and an identifier (`document:<doc id>`, `statements:<symbol>`, `prices:<symbol>`...),
and a digest where the result carries one. The rules below are the code's own list of what each tool reads; a tool that is not on it has an unknown source, and an unknown source is never called independent.

This establishes that two rows did not read the same stored source. It does not establish that two stored sources are not copies of one filing (a peer's statements and a draft's printed table of that
peer may restate the same figure); that remains for a person to judge."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Source:
    kind: str
    ident: str
    digest: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.ident}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ident": self.ident, "digest": self.digest}


def _digest(result: Any) -> str | None:
    """A content hash the result itself carries (`sha256`, `source_hash`), if it is a non-empty string."""
    if isinstance(result, Mapping):
        for key in ("sha256", "source_hash"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def sources_of(target: str, tool: str, args: Mapping[str, Any], result: Any) -> list[Source]:
    """The sources a call of `tool` on a case about `target` reads; empty when the tool is not known (the source is then unknown)."""
    digest = _digest(result)
    prefix = tool.split(".", 1)[0]
    symbol = str(args.get("symbol")) if isinstance(args, Mapping) and args.get("symbol") else None
    if prefix == "doc":
        return [Source("document", target, digest)]
    if prefix == "fin":
        return [Source("statements", target, digest)]
    if tool == "own.holdings":
        return [Source("shareholding", target, digest)]
    if tool == "own.insider":
        return [Source("insider", target, digest)]
    if tool == "own.board":
        return [Source("board", target, digest)]
    if tool == "own.reconcile":
        return [Source("shareholding", target), Source("insider", target)]
    if tool == "mkt.claims":
        return [Source("headlines", target), Source("prices", target)]
    if prefix == "mkt":
        return [Source("prices", target, digest)]
    if tool == "peers.pages":
        return [Source("document", target, digest)]
    if tool == "peers.filed" and symbol:
        return [Source("statements", symbol, digest)]
    if tool == "peers.compare" and symbol:
        return [Source("document", target), Source("statements", symbol)]
    return []


def of_row(target: str, row: Mapping[str, Any]) -> list[Source]:
    """The sources of a stored evidence row: the lineage it was written with, or, for a row written without one, what its tool reads by the rules above."""
    stored = row.get("lineage")
    if isinstance(stored, list):
        return [Source(str(s["kind"]), str(s["ident"]), s.get("digest")) for s in stored if isinstance(s, Mapping) and s.get("kind") and s.get("ident")]
    return sources_of(target, str(row.get("tool", "")), row.get("args") or {}, row.get("result"))


def keys(sources: list[Source]) -> set[str]:
    return {s.key for s in sources}
