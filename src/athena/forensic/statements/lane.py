"""The statements lane: its tools (plain code over the annual series and the peer universe) and its lane specification.

    python -m athena.forensic.statements.lane SYMBOL [--as-of 2026-11-03]            # the detectors for one company, no model
    python -m athena.forensic.statements.lane SYMBOL --agent                         # the lane investigates, within 12 model calls

An agent reads the same tables a person would: the company's profile, its lines by year, the detector findings with the peer ranking, the distribution of any measure across peers and its recent quarters.
Every result is kept whole as evidence, so a finding can cite it and code can check the citation. Amounts are in crore."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from athena.forensic.model import Case, LaneSpec
from athena.forensic.statements.annuals import Annuals, connect
from athena.forensic.statements.detect import Universe
from athena.forensic.statements.metrics import BY_KEY, MEASURES
from athena.forensic.tools import Tool, ToolContext, ToolError

LINES = {
    "income": ("sales", "other_income", "total_expenses", "interest", "depreciation", "pbt", "tax", "net_profit", "operating_profit"),
    "balance": ("equity_capital", "reserves", "borrowings", "total_assets", "fixed_assets", "cwip", "investments", "receivables", "inventory", "payables", "cash", "bank_balances", "current_assets", "current_liabilities"),
    "cashflow": ("cfo", "cfi", "cff", "capex", "dividends_paid"),
}
LIMITS = (
    "Statements as last stored: a later filing that restated an old year replaced the first figure, which cannot be undone here. No cost-of-goods or selling-expense lines are held, so the full Beneish M-score is not computed. "
    "Peers are industrial companies (banks, lenders and insurers have other statements and are not examined). A high ranking is a reason to look, not a finding."
)


class StatementsData:
    """Opens the fundamentals store when first needed and keeps one `Universe` per date for the process."""

    def __init__(self, connection: Callable[[], duckdb.DuckDBPyConnection] = connect):
        self._connection = connection
        self._universes: dict[date, Universe] = {}

    def universe(self, as_of: date) -> Universe:
        if as_of not in self._universes:
            con = self._connection()
            try:
                self._universes[as_of] = Universe.build(con, as_of)
            finally:
                con.close()
        return self._universes[as_of]

    def company(self, ctx: ToolContext) -> tuple[Universe, Annuals]:
        universe = self.universe(ctx.case.as_of)
        annuals = universe.annuals.get(ctx.case.target)
        if annuals is None:
            raise ToolError(f"no annual statements for {ctx.case.target} as of {ctx.case.as_of.isoformat()}: it is not an industrial company in the store (banks, lenders and insurers have other statements and are not examined) or none were filed by then")
        return universe, annuals


def _cr(value: float | None) -> float | None:
    return None if value is None else round(value / 1e7, 2)


def statement_tools(data: StatementsData) -> list[Tool]:
    def profile(ctx: ToolContext, args: dict[str, Any]) -> Any:
        universe, a = data.company(ctx)
        ends = [y.fy_end for y in a.years]
        gaps = [f"{x.isoformat()} to {y.isoformat()}" for x, y in zip(ends, ends[1:]) if not 330 <= (y - x).days <= 400]
        peers_in_industry = sum(1 for o in universe.annuals.values() if a.industry and o.industry == a.industry)
        return {"symbol": a.symbol, "name": a.name, "industry": a.industry or "(not recorded)", "basis": a.basis, "years": [e.isoformat() for e in ends], "years_missing_between": gaps,
                "quarters_held": len(a.quarters), "companies_in_universe": len(universe.annuals), "peers_in_same_industry": peers_in_industry, "as_of": ctx.case.as_of.isoformat(), "limits": LIMITS}

    def table(ctx: ToolContext, args: dict[str, Any]) -> Any:
        _, a = data.company(ctx)
        wanted = args.get("lines") or ["sales", "net_profit", "cfo"]
        known = {k: s for s, keys in LINES.items() for k in keys}
        bad = [w for w in wanted if w not in known]
        if bad:
            raise ToolError(f"unknown lines {bad}; choose from {sorted(known)}")
        out: dict[str, Any] = {"unit": "crore", "years": [y.fy_end.isoformat() for y in a.years]}
        for line in wanted:
            source = known[line]
            out[line] = {y.fy_end.isoformat(): _cr(getattr(y, source).get(line)) for y in a.years}
        return out

    def detect(ctx: ToolContext, args: dict[str, Any]) -> Any:
        universe, a = data.company(ctx)
        found = universe.findings(a.symbol)
        computed = {f.key for f in found}
        return {
            "symbol": a.symbol, "as_of": ctx.case.as_of.isoformat(), "convergence": universe.convergence(a.symbol), "elevated": [f.as_dict() for f in found if f.elevated], "others": [f.as_dict() for f in found if not f.elevated],
            "not_computable": [{"measure": m.key, "label": m.label, "needs": f"{m.years_needed} consecutive years with the lines it uses"} for m in MEASURES if m.key not in computed], "limits": LIMITS,
        }

    def peers(ctx: ToolContext, args: dict[str, Any]) -> Any:
        universe, a = data.company(ctx)
        measure = str(args.get("measure", ""))
        if measure not in BY_KEY:
            raise ToolError(f"unknown measure {measure!r}; choose from {sorted(BY_KEY)}")
        ranked = universe.worse_than(measure, a.symbol)
        values, basis = universe._peer_values(measure, a.industry)  # noqa: SLF001
        if not values:
            raise ToolError("no peer has this measure")

        def q(p: float) -> float:
            return round(values[min(len(values) - 1, int(p * len(values)))], 2)

        own = universe.values.get(a.symbol, {}).get(measure)
        return {"measure": measure, "peer_basis": basis, "peers": len(values), "p10": q(0.10), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "company_value": own.value if own else None,
                "more_concerning_than_share": round(ranked[0], 3) if ranked else None, "concerning_side": BY_KEY[measure].bad}

    def quarters(ctx: ToolContext, args: dict[str, Any]) -> Any:
        _, a = data.company(ctx)
        recent = list(a.quarters.items())[-int(args.get("last", 8)) :]
        return {"unit": "crore", "quarters": {d.isoformat(): {"sales": _cr(v.get("sales")), "net_profit": _cr(v.get("net_profit"))} for d, v in recent}}

    return [
        Tool("fin.profile", "the company, its statements basis, the years held, gaps, the size of its peer set and the limits of the data", "", profile),
        Tool("fin.table", "annual lines in crore by year", f'lines?: list from {sorted({k for keys in LINES.values() for k in keys})}', table),
        Tool("fin.detect", "every detector for the company with its ranking against peers (elevated first), figures, and innocent explanations", "", detect),
        Tool("fin.peers", "how one measure is distributed across peers (10th to 90th percentile) and where the company stands", f"measure: one of {sorted(BY_KEY)}", peers),
        Tool("fin.quarters", "the last quarters' sales and net profit in crore", "last?: number of quarters (default 8)", quarters),
    ]


STATEMENTS_LANE = LaneSpec(
    "statements",
    "reads a listed industrial company's annual statements for signs that profit is not backed by cash, that working capital or assets are growing out of line with sales, or that the tax, other income or "
    "debt figures are out of line with peers; it explains every difference with the figures and the innocent explanations, and asks the text lane when a document might explain it",
    ("fin.profile", "fin.table", "fin.detect", "fin.peers", "fin.quarters", "ledger.read", "notes.read"),
    step_cap=8,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="forensic detectors for one listed company")
    parser.add_argument("symbol")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--agent", action="store_true", help="let the statements lane investigate (model calls)")
    parser.add_argument("--db", default=None, help="a fundamentals file to read (default: the platform's)")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of)
    data = StatementsData((lambda: connect(args.db)) if args.db else connect)
    symbol = args.symbol.upper()
    try:
        if not args.agent:
            universe = data.universe(as_of)
            if symbol not in universe.annuals:
                print(f"no annual statements for {symbol} as of {as_of}", file=sys.stderr)
                return 1
            for f in universe.findings(symbol):
                print(("* " if f.elevated else "  ") + f.reading)
            return 0
        from athena.forensic.store import ForensicStore
        from athena.forensic.supervisor import Supervisor
        from athena.forensic.tools import base_registry
        from athena.llm.budget import Meter
        from athena.llm.router import build_router

        router = build_router(meter=Meter(), max_tokens=6000)
        store = ForensicStore(Path(tempfile.gettempdir()) / "forensic_cli.duckdb")
        case = Case(f"{symbol}-{as_of.isoformat()}-{datetime.now():%Y%m%d%H%M%S}", "listed", symbol, as_of)  # one id per run: evidence and the ledger are kept by case id
        sup = Supervisor(store, base_registry(statement_tools(data)), {"statements": STATEMENTS_LANE}, router.client_for("reader_retry"), total_calls=12, max_rounds=1)
        print(json.dumps(sup.investigate(case), indent=1, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
