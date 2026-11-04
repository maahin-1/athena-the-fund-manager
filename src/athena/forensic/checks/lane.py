"""The checks lane: a lane that is code. It runs the document checks, keeps the whole result as one piece of evidence, and writes a flag for each relation the document violates, with the contradiction
stated in the evidence's own numbers, so the verification gate evaluates it and the flag is established by code. It also puts one question to the text lane per flag (is there an explanation on the pages), and
writes an observation that says how many checks ran, held, differed and could not be run, so a report never reads as if what was not run had been clean.

A lane that is code has no model calls. Its relations are the registry's (`athena.forensic.checks.document`), not a model's choice.

    python -m athena.forensic.checks.lane DOC_ID        # run the checks on a stored document, print the result"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from typing import Any

from athena.forensic.agent import AgentResult, submit
from athena.forensic.checks.document import run_checks
from athena.forensic.model import FLAG, OBSERVATION, QUESTION, Case
from athena.forensic.store import ForensicStore
from athena.forensic.text.lane import DocData
from athena.forensic.tools import Registry, Tool, ToolContext, ToolError
from athena.forensic.verify import MAX_TOL

LANE = "checks"
MAX_FLAGS = 10
SUSPICIOUS_SHARE = 0.20  # more than this share of the checks that ran differing at once means a table was probably misread, not that a document is wrong in many places
SUSPICIOUS_MIN = 3
INNOCENT = {
    "arithmetic": ["a rounding or typing error in the draft that the final document corrects", "a line of the table missing from the extracted page text", "a subtotal that includes a line the extraction placed elsewhere"],
    "document_vs_document": ["the two places use different bases (consolidated or standalone), dates or units", "a later restatement updated one place and not the other", "a typing error in one place that the final document corrects"],
}


def checks_tool(data: DocData) -> Tool:
    def doc_checks(ctx: ToolContext, args: dict[str, Any]) -> Any:
        doc = data.document(ctx)
        return run_checks(data.store.pages(ctx.case.target), data.store.figures(ctx.case.target), doc["unreadable"])

    return Tool("doc.checks", "relations that must hold between printed numbers (balance sheet balances, subtotals add up, the same restated figure is the same in every place, the issue is its parts), each ok, differs or not run", "", doc_checks)


def _contradiction(ev: str, index: int, check: dict[str, Any]) -> dict[str, Any] | None:
    left = [{"ev": ev, "path": f"checks.{index}.left.{j}.value"} for j in range(len(check["left"]))]
    right = [{"ev": ev, "path": f"checks.{index}.right.{j}.value"} for j in range(len(check["right"]))]
    size = max(abs(sum(t["value"] for t in check["left"])), abs(sum(t["value"] for t in check["right"])), 1e-9)
    tol = check["tolerance"] / size
    return {"relation": check["relation"], "left": left, "right": right, "tol": min(MAX_TOL, tol)} if left and right and tol <= MAX_TOL else None


def _pages(check: dict[str, Any]) -> str:
    pages = sorted({t["page"] for t in (*check["left"], *check["right"]) if t["page"]})
    return ", ".join(str(p) for p in pages)


def run_code_lane(data: DocData):  # noqa: ANN201
    """The lane as the supervisor runs it: `(case, store, registry) -> AgentResult`."""

    def lane(case: Case, store: ForensicStore, registry: Registry) -> AgentResult:
        ctx = ToolContext(case, store, LANE)
        try:
            result, ev = registry.call("doc.checks", {}, ctx)
        except ToolError as exc:
            return AgentResult(LANE, "failed", note=str(exc)[:200])
        checks = result["checks"]
        differing = [(i, c) for i, c in enumerate(checks) if c["status"] == "differs"]
        summary = result["summary"]
        out = AgentResult(LANE, "finished", summary=f"{summary['run']} checks run, {summary['ok']} hold, {summary['differs']} differ, {summary['not_run']} not run")
        suspicious = len(differing) > max(SUSPICIOUS_MIN, SUSPICIOUS_SHARE * summary["run"])
        reasons = Counter(c["reason"].split(":")[0][:90] for c in checks if c["status"] == "not_run")
        text = f"Document checks: {summary['run']} run ({summary['ok']} hold, {summary['differs']} differ), {summary['not_run']} could not be run" + (
            f" ({'; '.join(r for r, _ in reasons.most_common(3))})" if reasons else "") + f"; {summary['statement_tables_read']} statement tables read. Not built: {len(result['not_built'])} further checks."
        if suspicious:
            text += (f" {len(differing)} of the {summary['run']} checks differ at once, which points to a table that was misread more than to that many errors in the document, so none is written as a flag; "
                     "the pages are " + ", ".join(sorted({_pages(c) for _, c in differing}))[:200] + ".")
        entry_id, reason = submit(store, case, LANE, 1, OBSERVATION, {"text": text, "evidence": [ev], "values": {"checks_run": summary["run"], "checks_not_run": summary["not_run"]}})
        out.written += 1 if reason is None else 0
        out.rejected += 1 if reason else 0
        if suspicious:
            out.note = "too many differences at once; no flags written"
            return out
        used: set[tuple[Any, ...]] = set()  # one wrong figure makes every relation it is in fail; it is one finding, so a check that shares a figure with a flag already written is not written again
        shared = 0
        for index, check in differing[:MAX_FLAGS]:
            contradiction = _contradiction(ev, index, check)
            if contradiction is None:
                continue
            figures = {(check["year"], t["name"], t["page"], t["printed"]) for t in (*check["left"], *check["right"])}
            if figures & used:
                shared += 1
                continue
            used |= figures
            body = {"text": f"{check['title']}: {check['reason']}", "flag_type": check["type"], "evidence": [ev], "innocent": INNOCENT[check["type"]], "contradiction": contradiction, "invariant": check["id"]}
            flag_id, reason = submit(store, case, LANE, 1, FLAG, body)
            if reason is not None:
                out.rejected += 1
                continue
            out.written += 1
            qid, qreason = submit(store, case, LANE, 1, QUESTION, {"to": "text", "about": flag_id, "text": f"The code found that {check['title']} differs (pages {_pages(check)}). Look in the document for an explanation (a note, a restatement adjustment, a different basis or unit) and answer with a quote, or answer cannot_determine."})
            out.written += 1 if qreason is None else 0
        notes = ([f"{len(differing) - MAX_FLAGS} further differences were not written as flags"] if len(differing) > MAX_FLAGS else []) + ([f"{shared} further check(s) failed on a figure already flagged"] if shared else [])
        out.note = "; ".join(notes)
        return out

    return lane


def main(argv: Sequence[str] | None = None) -> int:
    from athena.documents.store import DEFAULT_PATH

    parser = argparse.ArgumentParser(description="document checks for one stored offer document (no model)")
    parser.add_argument("doc_id")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--all", action="store_true", help="print every check, not only the ones that differ or could not be run")
    args = parser.parse_args(argv)
    data = DocData(args.db)
    try:
        doc = data.store.document(args.doc_id)
        if doc is None:
            print(f"no document {args.doc_id}", file=sys.stderr)
            return 1
        result = run_checks(data.store.pages(args.doc_id), data.store.figures(args.doc_id), doc["unreadable"])
        shown = result["checks"] if args.all else [c for c in result["checks"] if c["status"] != "ok"]
        print(json.dumps({"summary": result["summary"], "notes": result["notes"], "not_built": result["not_built"], "checks": shown}, indent=1, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
