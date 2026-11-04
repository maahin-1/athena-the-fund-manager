"""The text lane: its tools (plain code over a stored offer document) and its lane specification.

    python -m athena.forensic.text.lane DOC_ID                 # the section map, the figures and the baseline answers, no model
    python -m athena.forensic.text.lane DOC_ID --agent         # the lane reads the document, within 12 model calls

A long document is never put in front of a model whole. The lane navigates it the way a person does: the section map first (what the document contains and on which pages), then a section or a few pages, or a
search limited to the sections that can hold the answer. Every result is kept whole as evidence with its page numbers, so a quotation can be checked against the page it cites. The stored baseline answers
(the fixed questions of `athena.documents.drhp`) are offered as leads, not as findings."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from athena.documents.read import coverage
from athena.documents.search import Finder
from athena.documents.store import DEFAULT_PATH, DocStore
from athena.forensic.model import Case, LaneSpec
from athena.forensic.tools import Tool, ToolContext, ToolError

MAX_PAGES_PER_OPEN = 3
MAX_PAGE_CHARS = 5000
MAX_SEARCH_HITS = 8
LIMITS = (
    "Text is read from the PDF page by page; tables can lose their layout, so a figure in a table is only as reliable as the page text shows. A figure printed as [●] is left blank in a draft and is not an omission. "
    "The page number is the position in the PDF, which can differ from the number printed on the page."
)


class DocData:
    """Opens the document store when first needed and keeps one `Finder` per document for the process."""

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._path = path
        self._store: DocStore | None = None
        self._finders: dict[str, Finder] = {}

    @property
    def store(self) -> DocStore:
        if self._store is None:
            self._store = DocStore(self._path, read_only=True)
        return self._store

    def finder(self, doc_id: str) -> Finder:
        if doc_id not in self._finders:
            self._finders[doc_id] = Finder(self.store, doc_id)
        return self._finders[doc_id]

    def document(self, ctx: ToolContext) -> dict[str, Any]:
        doc = self.store.document(ctx.case.target)
        if doc is None:
            raise ToolError(f"no document {ctx.case.target!r} in the document store")
        return doc


def _int(args: dict[str, Any], key: str, default: int | None = None) -> int | None:
    value = args.get(key, default)
    return None if value is None else int(value)


def document_tools(data: DocData) -> list[Tool]:
    def doc_map(ctx: ToolContext, args: dict[str, Any]) -> Any:
        data.document(ctx)
        found = coverage(data.store, ctx.case.target)
        figures = data.store.figures(ctx.case.target)
        return {**found, "figures_held": sorted(figures) if figures else [], "limits": LIMITS}

    def doc_open(ctx: ToolContext, args: dict[str, Any]) -> Any:
        doc = data.document(ctx)
        section, first, count = args.get("section"), _int(args, "from_page"), min(MAX_PAGES_PER_OPEN, max(1, _int(args, "pages", 1) or 1))
        sections = {s.key: s for s in data.store.sections(ctx.case.target)}
        if first is None:
            if section not in sections:
                raise ToolError(f"give a section from the map ({sorted(sections)}) or from_page")
            first = sections[section].start
        if not 1 <= first <= doc["n_pages"]:
            raise ToolError(f"page {first} is outside the document (1 to {doc['n_pages']})")
        wanted = range(first, min(doc["n_pages"], first + count - 1) + 1)
        pages = [p for p in data.store.pages(ctx.case.target) if p[0] in wanted]
        return {"pages": [{"page": n, "section": sec, "text": text[:MAX_PAGE_CHARS], "cut": len(text) > MAX_PAGE_CHARS} for n, sec, text in pages], "document_pages": doc["n_pages"]}

    def doc_search(ctx: ToolContext, args: dict[str, Any]) -> Any:
        data.document(ctx)
        query = str(args["query"]).strip()
        if not query:
            raise ToolError("give a query")
        sections = args.get("sections")
        if sections is not None:
            known = {s.key for s in data.store.sections(ctx.case.target)}
            bad = [s for s in sections if s not in known]
            if bad:
                raise ToolError(f"unknown sections {bad}; the map has {sorted(known)}")
        hits = data.finder(ctx.case.target).search(query, sections, k=min(MAX_SEARCH_HITS, int(args.get("k", 5))))
        return {"query": query, "sections_searched": sections or "all", "passages": [{"page": h.page, "section": h.section, "text": h.text} for h in hits]}

    def doc_figures(ctx: ToolContext, args: dict[str, Any]) -> Any:
        data.document(ctx)
        figures = data.store.figures(ctx.case.target)
        if figures is None:
            raise ToolError("no offer figures were taken from this document")
        return {"figures": figures, "note": "amounts were taken by program from the pages cited (crore unless printed otherwise); [●] is blank in a draft"}

    def doc_baseline(ctx: ToolContext, args: dict[str, Any]) -> Any:
        data.document(ctx)
        answers = data.store.analysis(ctx.case.target)
        if answers is None:
            raise ToolError("the fixed questions have not been asked of this document")
        return [
            {"id": a["id"], "label": a["label"], "status": a["status"], "summary": a.get("summary"), "pages": sorted({p["page"] for p in a.get("points", [])}), "quotes": [{"page": p["page"], "quote": p.get("quote")} for p in a.get("points", [])[:4]]}
            for a in answers
        ]

    return [
        Tool("doc.map", "the document's sections with start and end pages, sections not found, pages without text, and which offer figures are held", "", doc_map),
        Tool("doc.open", "whole pages of the document, with page numbers and section", "section?: a key from doc.map (starts at its first page), from_page?: PDF page number, pages?: 1 to 3", doc_open),
        Tool("doc.search", "the passages that best match a query, with page and section", "query: words to look for, sections?: list of keys from doc.map to search in, k?: up to 8", doc_search),
        Tool("doc.figures", "the offer figures taken by program (offer size, fresh issue, offer for sale, objects with amounts, shares before the offer) and the arithmetic checks on them", "", doc_figures),
        Tool("doc.baseline", "answers to the fixed questions already asked of this document, with the pages and quotes they rest on: leads to follow, not findings", "", doc_baseline),
    ]


TEXT_LANE = LaneSpec(
    "text",
    "reads an offer document by navigating its section map; it follows leads (what the money is for, who sells and at what price, how the company describes its business and customers, what litigation and related-party "
    "dealings are disclosed) and looks for places where two parts of the document, or the document and the statements, say different things; it quotes the page word for word, answers other lanes' questions from the text, "
    "and says plainly when the text has no answer",
    ("doc.map", "doc.open", "doc.search", "doc.figures", "doc.baseline", "doc.checks", "ledger.read", "notes.read"),
    step_cap=8,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="forensic text lane for one stored offer document")
    parser.add_argument("doc_id")
    parser.add_argument("--agent", action="store_true", help="let the text lane read the document (model calls)")
    parser.add_argument("--db", default=str(DEFAULT_PATH), help="a document store to read (default: the platform's)")
    args = parser.parse_args(argv)
    data = DocData(args.db)
    try:
        from athena.forensic.store import ForensicStore
        from athena.forensic.tools import base_registry

        store = ForensicStore(Path(tempfile.gettempdir()) / "forensic_cli.duckdb")
        case = Case(f"{args.doc_id}-{datetime.now():%Y%m%d%H%M%S}", "drhp", args.doc_id, date.today())  # one id per run: evidence and the ledger are kept by case id
        from athena.forensic.checks.lane import checks_tool, run_code_lane

        registry = base_registry([*document_tools(data), checks_tool(data)])
        if not args.agent:
            ctx = ToolContext(case, store, "text")
            print(json.dumps(registry.call("doc.map", {}, ctx)[0], indent=1, ensure_ascii=False))
            return 0
        from athena.forensic.supervisor import Supervisor
        from athena.llm.budget import Meter
        from athena.llm.router import build_router

        router = build_router(meter=Meter(), max_tokens=6000)
        sup = Supervisor(store, registry, {"text": TEXT_LANE}, router.client_for("reader_retry"), total_calls=12, max_rounds=1, code_lanes={"checks": run_code_lane(data)})
        print(json.dumps(sup.investigate(case), indent=1, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
