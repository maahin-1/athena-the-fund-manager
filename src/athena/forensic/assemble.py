"""Puts the lanes together for one kind of case, so a run needs one call and the lane lists do not drift apart.

    python -m athena.forensic.assemble drhp DOC_ID [--agent] [--calls 24]         # an offer document: checks (code), text, peers
    python -m athena.forensic.assemble listed SYMBOL [--as-of 2026-11-09] [--agent] [--calls 24]   # a listed company: statements, ownership, market

Without `--agent` no model is called: the code lanes run (for an offer document, the document checks) and the section or the tools' plain outputs are printed. With `--agent` the model lanes run within the
call cap, the cheap tier first and the mid tier on failure (`reader_retry`), and the report is printed as JSON. Nothing here decides anything: the concern level is computed by `athena.forensic.scoring` from the
flags that the code proved or a second lane supported."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from athena.forensic.model import Case, LaneSpec
from athena.forensic.supervisor import CodeLane
from athena.forensic.tools import Registry, Tool, base_registry


@dataclass
class Assembly:
    registry: Registry
    lanes: Mapping[str, LaneSpec]
    code_lanes: Mapping[str, CodeLane]


def for_document(docs_path: Path | str | None = None, statements: Any = None) -> Assembly:
    """An offer document: the document checks as a code lane, the text lane and the peers lane."""
    from athena.documents.store import DEFAULT_PATH
    from athena.forensic.checks.lane import checks_tool, run_code_lane
    from athena.forensic.peers.lane import PEERS_LANE, peers_tools
    from athena.forensic.statements.lane import StatementsData
    from athena.forensic.text.lane import TEXT_LANE, DocData, document_tools

    data = DocData(docs_path or DEFAULT_PATH)
    tools: list[Tool] = [*document_tools(data), checks_tool(data), *peers_tools(data, statements or StatementsData())]
    return Assembly(base_registry(tools), {"text": TEXT_LANE, "peers": PEERS_LANE}, {"checks": run_code_lane(data)})


def for_listed(statements: Any = None, ownership: Any = None, market: Any = None) -> Assembly:
    """A listed company: the statements lane, the ownership lane and the market lane."""
    from athena.forensic.market.lane import MARKET_LANE, MarketData, market_tools
    from athena.forensic.ownership.lane import OWNERSHIP_LANE, OwnershipData, ownership_tools
    from athena.forensic.statements.lane import STATEMENTS_LANE, StatementsData, statement_tools

    tools: list[Tool] = [*statement_tools(statements or StatementsData()), *ownership_tools(ownership or OwnershipData()), *market_tools(market or MarketData())]
    return Assembly(base_registry(tools), {"statements": STATEMENTS_LANE, "ownership": OWNERSHIP_LANE, "market": MARKET_LANE}, {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the forensic lanes for one case")
    parser.add_argument("kind", choices=["drhp", "listed"])
    parser.add_argument("target", help="a document id (drhp) or a symbol (listed)")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--agent", action="store_true", help="let the model lanes run (model calls)")
    parser.add_argument("--calls", type=int, default=24, help="the cap on model calls for the whole case")
    parser.add_argument("--db", default=None, help="a document store to read (drhp only; default: the platform's)")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of)
    target = args.target if args.kind == "drhp" else args.target.upper()
    try:
        from athena.forensic.store import ForensicStore
        from athena.forensic.supervisor import Supervisor

        assembly = for_document(args.db) if args.kind == "drhp" else for_listed()
        store = ForensicStore(Path(tempfile.gettempdir()) / "forensic_cli.duckdb")
        case = Case(f"{target}-{as_of.isoformat()}-{datetime.now():%Y%m%d%H%M%S}", args.kind, target, as_of)  # one id per run: evidence and the ledger are kept by case id
        if args.agent:
            from athena.llm.budget import Meter
            from athena.llm.router import build_router

            llm = build_router(meter=Meter(), max_tokens=6000).client_for("reader_retry")
        else:
            llm, args.calls = None, 0
        sup = Supervisor(store, assembly.registry, assembly.lanes if args.agent else {}, llm, total_calls=args.calls, max_rounds=2, code_lanes=assembly.code_lanes)  # type: ignore[arg-type]
        print(json.dumps(sup.investigate(case), indent=1, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
