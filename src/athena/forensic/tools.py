"""The tools an agent may call. A tool is plain code that returns a small JSON-able result; a model only chooses which to call and with what arguments. Results that are data are kept whole in the
store as evidence (E1, E2...), so a finding can cite them and code can check the citation. Later steps register the document, financial and market tools; this module holds the registry and the two
tools every lane has (reading the shared ledger and its own notebook)."""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from athena.contracts import AthenaError
from athena.forensic import lineage
from athena.forensic.model import Case
from athena.forensic.store import ForensicStore

MAX_SHOWN_CHARS = 2500


class ToolError(AthenaError):
    """A tool call that cannot be run (unknown tool, bad arguments, no data). The agent is shown the reason."""


@dataclass(frozen=True)
class ToolContext:
    case: Case
    store: ForensicStore
    lane: str


@dataclass(frozen=True)
class Tool:
    name: str
    doc: str  # one line: what it returns
    args: str  # one line: the arguments, as the agent should write them
    fn: Callable[[ToolContext, dict[str, Any]], Any]
    evidence: bool = True  # False for the tools that read the investigation itself (ledger, notebook): their results are not data to cite


class Registry:
    def __init__(self, tools: Sequence[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def describe(self, names: Sequence[str]) -> str:
        return "\n".join(f"- {n}({t.args}): {t.doc}" for n in names if (t := self._tools.get(n)))

    def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> tuple[Any, str | None]:
        """(result, evidence id or None). A tool that raises is a `ToolError` with the reason."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"no tool named {name!r}")
        try:
            result = tool.fn(ctx, dict(args))
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - arguments come from a model; whatever a tool does with them wrong is a refusal the agent is shown, never the end of the run
            raise ToolError(f"{name}: bad arguments or no result ({type(exc).__name__}: {exc})") from exc
        if not tool.evidence:
            return result, None
        found = ctx.store.find_evidence(ctx.case.case_id, name, args)
        if found:
            return result, found
        sources = [s.to_dict() for s in lineage.sources_of(ctx.case.target, name, args, result)]
        return result, ctx.store.add_evidence(ctx.case.case_id, ctx.lane, name, args, result, lineage=sources)


def full_text(result: Any) -> str:
    """The whole result as the store keeps it, as text: the string whose characters `shown` cuts and `evidence.read` reopens."""
    return json.dumps(result, ensure_ascii=False, default=str)


def shown(result: Any, ev_id: str | None) -> str:
    """The result as an agent sees it: JSON, cut to a size a prompt can carry, with the evidence id that holds the whole and how to reopen the rest. What was cut was not seen."""
    text = full_text(result)
    if len(text) <= MAX_SHOWN_CHARS:
        return text
    reopen = f"; {ev_id} holds all of it: reopen the rest with evidence.read, e.g. {{\"id\": \"{ev_id}\", \"offset\": {MAX_SHOWN_CHARS}}}" if ev_id else ""
    return text[:MAX_SHOWN_CHARS] + f" ... (cut, {len(text)} characters in all{reopen}; the cut part has not been shown to you)"


READ_LIMIT = 2000  # characters of a stored result one `evidence.read` returns: a slice, never more than a prompt can carry whatever is asked


def _node(data: Any, path: str) -> Any:
    node = data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.lstrip("-").isdigit() and -len(node) <= int(part) < len(node):
            node = node[int(part)]
        else:
            raise ToolError(f"no field {path!r} in that result (stopped at {part!r})")
    return node


def _evidence_read(ctx: ToolContext, args: dict[str, Any]) -> Any:
    """A bounded slice of a stored result: the whole payload's text from `offset`, or the text of the field at `path`. Reading it is not showing it: the tool records nothing about what a lane has seen, because a
    slice returned here may never reach a model. The agent loop credits a slice as shown only once a prompt that carries it has been delivered (`athena.forensic.agent`)."""
    ev_id = str(args["id"])
    row = ctx.store.evidence(ctx.case.case_id, ev_id)
    if row is None:
        raise ToolError(f"no evidence {ev_id!r} in this case")
    offset, limit = int(args.get("offset", 0)), min(int(args.get("limit", READ_LIMIT)), READ_LIMIT)
    if offset < 0 or limit < 1:
        raise ToolError("offset must be 0 or more and limit at least 1")
    path = str(args["path"]) if args.get("path") else None
    if path:
        node = _node(row["result"], path)
        text = node if isinstance(node, str) else full_text(node)
    else:
        text = full_text(row["result"])
    piece = text[offset : offset + limit]
    end = offset + len(piece)
    return {"id": ev_id, "path": path, "offset": offset, "total_chars": len(text), "text": piece, "more": end < len(text), "next_offset": end if end < len(text) else None}


def _ledger_read(ctx: ToolContext, args: dict[str, Any]) -> Any:
    entries = ctx.store.entries(ctx.case.case_id, kind=args.get("kind"), status=args.get("status"), author=args.get("author"))
    return [{"id": e.id, "kind": e.kind, "author": e.author, "round": e.round, "status": e.status, "text": e.text[:300], "evidence": e.evidence} for e in entries[-30:]]


def _notes_read(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return ctx.store.notes(ctx.case.case_id, ctx.lane)[-20:]


def base_tools() -> list[Tool]:
    return [
        Tool("ledger.read", "the shared findings so far (id, kind, author, status, text)", 'kind?: "FLAG"|"OBSERVATION"|"QUESTION"|"ANSWER", author?, status?', _ledger_read, evidence=False),
        Tool("notes.read", "your own notebook for this case", "", _notes_read, evidence=False),
        Tool("evidence.read", "a slice of a stored result that was cut when shown (up to 2000 characters), from an offset or from one field; what you have not been shown, you have not examined",
             'id: an evidence id like E3, offset?: characters to skip (the next_offset of the last slice), path?: a dotted field like pages.2.text', _evidence_read, evidence=False),
    ]


def base_registry(extra: Sequence[Tool] = ()) -> Registry:
    return Registry([*base_tools(), *extra])
