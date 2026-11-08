"""The peers lane: its tools (plain code over a stored offer document and the annual statements of listed companies) and its lane specification.

    python -m athena.forensic.peers.lane DOC_ID [--db documents.duckdb]           # the pages that carry the document's table of listed peers, no model
    python -m athena.forensic.peers.lane DOC_ID --agent                           # the lane reads the peer table and checks it against the peers' own filings, within 12 model calls

An offer document justifies its price band by a table of listed peers (total income, profit after tax, earnings per share, return on net worth, net asset value). The peers filed their own statements. This
lane lays the two side by side: it finds the pages with the table, finds the peer in the store of filed statements, and works out in code how far the figure the document prints is from the figure the peer filed.
The figures come in a document's own unit (million, lakh, crore), are converted here, and a difference is shown with the basis the filed figure is on, because a document may use a peer's consolidated figure where
the store holds the standalone one (or the reverse). A model reads the table and asks for the comparison; it never does the sum."""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Sequence
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from athena.documents.store import DEFAULT_PATH
from athena.forensic.model import Case, LaneSpec
from athena.forensic.statements.lane import StatementsData
from athena.forensic.text.lane import DocData
from athena.forensic.tools import Tool, ToolContext, ToolError

MAX_PAGES = 3
MAX_PAGE_CHARS = 5000
MAX_MATCHES = 5
UNITS = {"crore": 1.0, "crores": 1.0, "cr": 1.0, "million": 0.1, "millions": 0.1, "mn": 0.1, "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01, "thousand": 0.0001, "thousands": 0.0001}
LINES = ("total_income", "sales", "net_profit", "net_worth")
FILLER = re.compile(r"\b(limited|ltd|private|pvt|the|of|and|india|formerly|known|as|\(.*?\))\b|[^a-z0-9 ]", re.IGNORECASE)
MARKERS = (re.compile(r"P\s*/\s*E", re.IGNORECASE), re.compile(r"\bpeers?\b", re.IGNORECASE), re.compile(r"Limited|Ltd\.?"), re.compile(r"\bRoNW\b|return on net worth", re.IGNORECASE), re.compile(r"total income|revenue from operations", re.IGNORECASE))
LIMITS = (
    "A document's peer table is taken from the peers' filings by the issuer's advisers, usually on a consolidated basis and for the latest year. The store holds one basis per company (the one with more years) and the "
    "figure as last filed, which a later restatement replaces. Total income here is revenue from operations plus other income; net worth is equity share capital plus reserves, which a peer may define differently "
    "(excluding revaluation reserve, attributable to owners only). A small gap is the rounding of the units; a large one is a reason to look at the basis and the definition first."
)


def _tokens(name: str) -> set[str]:
    return {t for t in FILLER.sub(" ", name.lower()).split() if len(t) > 1}


def _covered(small: set[str], big: set[str]) -> bool:
    """Every word of `small` is a word of `big`, or within a letter or two of one (a document may print `Gallant` for `Gallantt`)."""
    return all(t in big or any(len(t) >= 5 and len(b) >= 5 and SequenceMatcher(None, t, b).ratio() >= 0.88 for b in big) for t in small)


def _to_crore(value: float, unit: str) -> float:
    factor = UNITS.get(unit.strip().lower())
    if factor is None:
        raise ToolError(f"unit must be one of {sorted(set(UNITS))}")
    return value * factor


def peers_tools(docs: DocData, statements: StatementsData) -> list[Tool]:
    def pages(ctx: ToolContext, args: dict[str, Any]) -> Any:
        docs.document(ctx)
        numbers = docs.printed(ctx.case.target)
        scored = []
        for page, section, text in docs.store.pages(ctx.case.target):
            score = sum(1 for m in MARKERS if m.search(text)) + (2 if section == "basis_for_price" else 0)
            if len(re.findall(r"Limited|Ltd", text)) >= 3 and re.search(r"P\s*/\s*E", text, re.IGNORECASE):
                score += 3  # a table with several company names and a P/E column
            scored.append((score, page, section, text))
        best = sorted((s for s in scored if s[0] >= 5), key=lambda s: (-s[0], s[1]))[:MAX_PAGES]
        if not best:
            raise ToolError("no page of this document carries a table of listed peers (no P/E column with company names was found)")
        return {"pages": [{"page": p, "printed_page": numbers.get(p), "section": sec, "text": text[:MAX_PAGE_CHARS], "cut": len(text) > MAX_PAGE_CHARS} for _, p, sec, text in sorted(best, key=lambda s: s[1])], "limits": LIMITS}

    def find(ctx: ToolContext, args: dict[str, Any]) -> Any:
        want = _tokens(str(args.get("name", "")))
        if not want:
            raise ToolError("give name: the company's name as the document prints it")
        annuals = statements.universe(ctx.case.as_of).annuals
        hits = []
        for a in annuals.values():
            have = _tokens(a.name)
            if have and (_covered(want, have) or _covered(have, want)):
                hits.append((len(have ^ want), a))
        hits.sort(key=lambda h: (h[0], h[1].symbol))
        if not hits:
            raise ToolError(f"no listed company in the store of annual statements matches {args.get('name')!r} (banks, lenders and insurers are not held)")
        return {"matches": [{"symbol": a.symbol, "name": a.name, "industry": a.industry, "basis": a.basis, "financial_years": [y.fy_end.isoformat() for y in a.years]} for _, a in hits[:MAX_MATCHES]]}

    def annuals_of(ctx: ToolContext, symbol: str) -> Any:
        a = statements.universe(ctx.case.as_of).annuals.get(symbol.upper())
        if a is None:
            raise ToolError(f"no annual statements for {symbol} as of {ctx.case.as_of.isoformat()}")
        return a

    def value_of(a: Any, line: str, year: int) -> tuple[date, float | None]:
        y = next((y for y in a.years if y.fy_end.year == year), None)
        if y is None:
            raise ToolError(f"{a.symbol} has no financial year ending in {year}; it has {[x.fy_end.isoformat() for x in a.years]}")
        inc, bal = y.income, y.balance
        if line == "sales":
            v = inc.get("sales")
        elif line == "total_income":
            v = None if inc.get("sales") is None else inc["sales"] + (inc.get("other_income") or 0.0)
        elif line == "net_profit":
            v = inc.get("net_profit")
        elif line == "net_worth":
            v = None if bal.get("equity_capital") is None or bal.get("reserves") is None else bal["equity_capital"] + bal["reserves"]
        else:
            raise ToolError(f"line must be one of {LINES}")
        return y.fy_end, None if v is None else v / 1e7

    def filed(ctx: ToolContext, args: dict[str, Any]) -> Any:
        a = annuals_of(ctx, str(args.get("symbol", "")))
        n = max(1, min(int(args.get("years", 3)), 6))
        out = []
        for y in a.years[-n:]:
            row: dict[str, Any] = {"fy_end": y.fy_end.isoformat()}
            for line in LINES:
                _, v = value_of(a, line, y.fy_end.year)
                row[line] = None if v is None else round(v, 2)
            out.append(row)
        return {"symbol": a.symbol, "name": a.name, "basis": a.basis, "unit": "crore", "years": out, "limits": LIMITS}

    def compare(ctx: ToolContext, args: dict[str, Any]) -> Any:
        a = annuals_of(ctx, str(args.get("symbol", "")))
        line, unit = str(args.get("line", "")), str(args.get("unit", ""))
        try:
            year, stated = int(args["year"]), float(str(args["stated"]).replace(",", ""))
        except (KeyError, ValueError) as exc:
            raise ToolError("give year (the calendar year in which the financial year ends, 2026 for the year to March 31, 2026) and stated (the number the document prints)") from exc
        end, filed_cr = value_of(a, line, year)
        if filed_cr is None:
            raise ToolError(f"{a.symbol} has no {line} for the year ending {end.isoformat()}")
        stated_cr = _to_crore(stated, unit)
        diff = stated_cr - filed_cr
        out = {"symbol": a.symbol, "line": line, "fy_end": end.isoformat(), "stated_crore": round(stated_cr, 4), "filed_crore": round(filed_cr, 4), "difference_crore": round(diff, 4),
               "difference_percent": round(diff / abs(filed_cr) * 100.0, 3) if filed_cr else None, "basis_of_filed_figure": a.basis, "stated_in": f"{stated:,} {unit}",
               "note": "a unit's rounding explains a gap of well under one percent; a larger one has to be explained by the basis, the definition of the line, or a later restatement (see limits)", "limits": LIMITS}
        basis = str(args.get("basis", "")).strip().lower()
        if basis and basis != a.basis:
            out["basis_warning"] = f"the document states {basis} figures and the store holds {a.basis} ones for this company, so the two may not be the same measure"
        return out

    return [
        Tool("peers.pages", "the pages of the offer document that carry the table of listed peers (company names with total income, profit, P/E, RoNW, NAV), with page and printed page", "", pages),
        Tool("peers.find", "the listed companies in the store of annual statements whose name matches a name as the document prints it", "name: e.g. \"Gallant Ispat Limited\"", find),
        Tool("peers.filed", "what a peer filed: revenue, total income, net profit and net worth in crore for its last financial years, with the basis the store holds", "symbol: from peers.find, years?: 1 to 6 (default 3)", filed),
        Tool("peers.compare", "the figure the document prints for a peer against what the peer filed, worked out in code: both in crore, the difference and its percent", "symbol: from peers.find, line: total_income | sales | net_profit | net_worth, year: the calendar year the financial year ends in (2026), stated: the number printed, unit: crore | million | lakh | thousand, basis?: consolidated | standalone as the document states", compare),
    ]


PEERS_LANE = LaneSpec(
    "peers",
    "reads the table of listed peers in an offer document that justifies its price band, and checks each peer's printed total income, profit after tax and net worth against what that peer filed; it finds the pages "
    "with the table, finds each peer in the store, asks for the comparison (the code works out the difference, never the lane) and reports the differences with the basis and definition caveats; where a difference "
    "is large it states it as a contradiction between the printed figure and the filed one, with the innocent causes",
    ("peers.pages", "peers.find", "peers.filed", "peers.compare", "doc.open", "doc.search", "ledger.read", "notes.read"),
    step_cap=10,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="forensic peers view for one stored offer document")
    parser.add_argument("doc_id")
    parser.add_argument("--db", default=str(DEFAULT_PATH), help="a document store to read (default: the platform's)")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--agent", action="store_true", help="let the peers lane work (model calls)")
    args = parser.parse_args(argv)
    docs, statements = DocData(args.db), StatementsData()
    try:
        from athena.forensic.store import ForensicStore
        from athena.forensic.text.lane import document_tools
        from athena.forensic.tools import base_registry

        store = ForensicStore(Path(tempfile.gettempdir()) / "forensic_cli.duckdb")
        case = Case(f"{args.doc_id}-{datetime.now():%Y%m%d%H%M%S}", "drhp", args.doc_id, date.fromisoformat(args.as_of))
        registry = base_registry([*document_tools(docs), *peers_tools(docs, statements)])
        if not args.agent:
            print(json.dumps(registry.call("peers.pages", {}, ToolContext(case, store, "peers"))[0], indent=1, ensure_ascii=False))
            return 0
        from athena.forensic.supervisor import Supervisor
        from athena.llm.budget import Meter
        from athena.llm.router import build_router

        router = build_router(meter=Meter(), max_tokens=6000)
        sup = Supervisor(store, registry, {"peers": PEERS_LANE}, router.client_for("reader_retry"), total_calls=12, max_rounds=1)
        print(json.dumps(sup.investigate(case), indent=1, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
