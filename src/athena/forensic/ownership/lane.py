"""The ownership lane: its tools (plain code over the shareholding patterns, the insider-trade filings and the board-meeting notices) and its lane specification.

    python -m athena.forensic.ownership.lane SYMBOL [--as-of 2026-11-09]          # the holdings, insider summary and reconciliation for one company, no model
    python -m athena.forensic.ownership.lane SYMBOL --agent                       # the lane investigates, within 12 model calls

Who holds the company changes slowly and is filed late, so everything here is dated: a shareholding pattern counts only once it was filed on or before the as-of day, an insider trade only once it was filed.
All arithmetic (changes in points, net shares, the reconciliation of the two sources) is done here, and an agent reads the results. The names of individuals are not given to the agent: the filings are read by
the category of the person (promoter, promoter group, director, employee), so a flag never names a person."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from athena.forensic.model import Case, LaneSpec
from athena.forensic.tools import Tool, ToolContext, ToolError
from athena.ownership.packet import GROUPS, Quarter, is_quarter_end

MAX_QUARTERS = 8
MAX_TRADES = 25
PROMOTER_CATEGORIES = ("promoters", "promoter group")
LIMITS = (
    "A shareholding pattern is filed once a quarter and shows the quarter end, not what happened between. Pledged shares are not in the patterns; they appear only as pledge, revoke and invoke lines in the insider "
    "filings, which this store holds only up to the date the exchange's endpoint last served (`insider_reaches`). The insider filings and the patterns are different sources: shares allotted, transferred inside a "
    "promoter group, gifted or invoked from a pledge can change one and not the other, so a gap between them is a reason to ask, not a finding."
)


class OwnershipData:
    """Opens the fundamentals store and the evidence store when first needed. Each is a factory so a test can hand in a stand-in with the same methods."""

    def __init__(self, fundamentals: Callable[[], Any] | None = None, evidence: Callable[[], Any] | None = None):
        self._fund_factory, self._ev_factory = fundamentals, evidence
        self._fund: Any = None
        self._ev: Any = None

    @property
    def fundamentals(self) -> Any:
        if self._fund is None:
            if self._fund_factory is None:
                from athena.fundamentals.store import open_read

                self._fund = open_read()
            else:
                self._fund = self._fund_factory()
        return self._fund

    @property
    def evidence(self) -> Any:
        if self._ev is None:
            if self._ev_factory is None:
                from athena.evidence.store import EvidenceStore

                self._ev = EvidenceStore(read_only=True)
            else:
                self._ev = self._ev_factory()
        return self._ev

    def patterns(self, ctx: ToolContext) -> list[Quarter]:
        symbol = ctx.case.target
        rows = pattern_quarters(self.fundamentals.holdings(symbol), self.fundamentals.holding_filed(symbol), ctx.case.as_of)
        if not rows:
            raise ToolError(f"no shareholding pattern filed by {ctx.case.as_of.isoformat()} is held for {symbol}")
        return rows

    def trades(self, ctx: ToolContext, since: date) -> list[dict[str, Any]]:
        """The insider filings of the case's company filed in `since` to the as-of day (the store already filters on the filing stamp)."""
        return self.evidence.insider_between(since, ctx.case.as_of, ctx.case.target, limit=2000)


def pattern_quarters(holdings: Any, filed: Any, as_of: date) -> list[Quarter]:
    """The calendar quarter ends filed by `as_of`, oldest first, as percent of all shares. Unlike the ownership packet this does not drop a pattern whose promoter and public groups do not add up to the total: a company
    whose employee trust or depository-receipt holder sits in neither group has a gap that is real, so it is shown as `unclassified` (total less promoters less public) and the agent is told, not left with nothing."""
    out: list[Quarter] = []
    for end in sorted(holdings):
        if not is_quarter_end(end) or end > as_of:
            continue
        when = filed[end].date() if end in filed else None
        if when is not None and when > as_of:
            continue
        groups = holdings[end]
        total = groups.get("total", (0.0, None))[0]
        if total <= 0:
            continue
        pct = {name: groups[name][0] / total * 100.0 for name in GROUPS if name in groups}
        if "promoters" in groups and "public_total" in groups:
            pct["unclassified"] = (total - groups["promoters"][0] - groups["public_total"][0]) / total * 100.0
        out.append(Quarter(end, when, pct, groups["total"][1]))
    return out


def _pp(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _txn_sign(txn: str) -> int:
    """+1 for a purchase, -1 for a sale, 0 for anything that does not move shares in or out of the group's ownership the same way (a pledge, a revoke, an invoke, no type)."""
    t = (txn or "").strip().lower()
    return 1 if t == "buy" else -1 if t == "sell" else 0


def _is_promoter(category: str) -> bool:
    return (category or "").strip().lower() in PROMOTER_CATEGORIES


def _day_of(row: dict[str, Any]) -> date | None:
    for key in ("trade_to", "trade_from"):
        value = row.get(key)
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    filed = row.get("filed_at")
    return filed.date() if isinstance(filed, datetime) else None


def ownership_tools(data: OwnershipData) -> list[Tool]:
    def holdings(ctx: ToolContext, args: dict[str, Any]) -> Any:
        rows = data.patterns(ctx)[-MAX_QUARTERS:]
        out = []
        for i, q in enumerate(rows):
            prev = rows[i - 1] if i else None
            out.append({"quarter_end": q.end.isoformat(), "filed": q.filed.isoformat() if q.filed else None, "percent": {g: round(v, 2) for g, v in q.pct.items()},
                        "change_points": {g: _pp(v - prev.pct[g]) for g, v in q.pct.items() if prev and g in prev.pct}, "shareholders": q.holders})
        return {"unit": "percent of all shares; change_points are points since the quarter before; mutual_funds and insurance are parts of dii, not additional to it; unclassified is the total less promoters less the public "
                         "group (an employee trust or a depository holder sits there)", "groups": [*GROUPS, "unclassified"], "quarters": out, "limits": LIMITS}

    def insider(ctx: ToolContext, args: dict[str, Any]) -> Any:
        days = max(30, min(int(args.get("days", 365)), 1100))
        rows = data.trades(ctx, ctx.case.as_of - timedelta(days=days))
        newest = data.evidence.insider_newest()
        by: dict[str, dict[str, Any]] = {}
        for r in rows:
            cat = (r["category"] or "-").strip()
            g = by.setdefault(cat, {"filings": 0, "bought_shares": 0.0, "sold_shares": 0.0, "bought_value": 0.0, "sold_value": 0.0, "pledge_lines": 0, "people": set()})
            g["filings"] += 1
            g["people"].add(r["person"])
            sign = _txn_sign(r["txn"])
            if sign > 0:
                g["bought_shares"] += r["shares"] or 0.0
                g["bought_value"] += r["value"] or 0.0
            elif sign < 0:
                g["sold_shares"] += r["shares"] or 0.0
                g["sold_value"] += r["value"] or 0.0
            if (r["txn"] or "").lower().startswith("pledge"):
                g["pledge_lines"] += 1
        summary = {cat: {**{k: (round(v) if isinstance(v, float) else v) for k, v in g.items() if k != "people"}, "net_shares": round(g["bought_shares"] - g["sold_shares"]), "distinct_people": len(g["people"])} for cat, g in sorted(by.items())}
        shown = [{"filed": r["filed_at"].date().isoformat() if isinstance(r["filed_at"], datetime) else None, "category": r["category"], "txn": r["txn"], "mode": r["mode"], "shares": r["shares"], "value_rupees": r["value"],
                  "holding_before_pct": r["before_pct"], "holding_after_pct": r["after_pct"], "traded_to": str(r["trade_to"]) if r["trade_to"] else None} for r in rows[:MAX_TRADES]]
        return {"window_days": days, "filings": len(rows), "by_category": summary, "latest": shown, "insider_reaches": newest.date().isoformat() if newest else None, "limits": LIMITS}

    def reconcile(ctx: ToolContext, args: dict[str, Any]) -> Any:
        rows = data.patterns(ctx)[-(MAX_QUARTERS + 1):]
        if len(rows) < 2:
            raise ToolError("two quarters are needed to compare")
        raw = data.fundamentals.holdings(ctx.case.target)
        trades = data.trades(ctx, rows[0].end)
        out = []
        for prev, cur in zip(rows, rows[1:]):
            before, after = raw.get(prev.end, {}).get("promoters"), raw.get(cur.end, {}).get("promoters")
            if not before or not after:
                out.append({"quarter_end": cur.end.isoformat(), "reason": "the pattern has no promoter group in one of the two quarters"})
                continue
            net = bought = sold = 0.0
            count = 0
            for r in trades:
                day = _day_of(r)
                if day is None or not (prev.end < day <= cur.end) or not _is_promoter(r["category"]):
                    continue
                sign = _txn_sign(r["txn"])
                if sign:
                    count += 1
                    shares = r["shares"] or 0.0
                    net += sign * shares
                    bought += shares if sign > 0 else 0.0
                    sold += shares if sign < 0 else 0.0
            total_before, total_after = raw[prev.end]["total"][0], raw[cur.end]["total"][0]
            change = after[0] - before[0]
            scaled = before[0] * total_after / total_before - before[0]  # what the promoters' share count does when the share count of the whole company changes (a bonus, a split, an issue) and their stake does not
            gap = change - scaled - net
            out.append({"quarter_end": cur.end.isoformat(), "total_shares_change_percent": round((total_after / total_before - 1.0) * 100.0, 3), "promoter_shares_change_in_patterns": round(change),
                        "change_if_stake_unchanged": round(scaled), "insider_net_shares_promoters": round(net), "insider_bought": round(bought), "insider_sold": round(sold), "insider_filings_counted": count,
                        "gap_shares": round(gap), "gap_points_of_all_shares": round(gap / total_after * 100.0, 4)})
        return {"quarters": out, "note": "gap = change in promoter shares less the change a constant stake would show when the total share count changed, less the insider net; the patterns and the insider filings are different sources, "
                                         "so a gap is for the agent to ask about, with the innocent causes in the limits", "limits": LIMITS}

    def board(ctx: ToolContext, args: dict[str, Any]) -> Any:
        days = max(30, min(int(args.get("days", 365)), 1100))
        rows = data.evidence.board_for(ctx.case.target, ctx.case.as_of - timedelta(days=days), ctx.case.as_of)
        return {"window_days": days, "meetings": [{"date": str(r["meeting_date"]), "purpose": r["purpose"], "description": (r["description"] or "")[:300]} for r in rows[-30:]]}

    return [
        Tool("own.holdings", "the last quarters' shareholding patterns (promoters, FII, DII, mutual funds, insurance, retail as percent of all shares), what was filed when, and the change in points", "", holdings),
        Tool("own.insider", "insider-trade filings by category of person (promoters, directors, employees): shares and value bought and sold, pledge lines, the latest filings, and how far the insider data reaches", "days?: 30 to 1100 (default 365)", insider),
        Tool("own.reconcile", "per quarter, the change in promoter shares between two patterns against the net shares promoters bought and sold in the insider filings, and the gap", "", reconcile),
        Tool("own.board", "the board-meeting notices of the company and their purposes", "days?: 30 to 1100 (default 365)", board),
    ]


OWNERSHIP_LANE = LaneSpec(
    "ownership",
    "reads who holds a company and how that changed: promoter and institutional holdings by quarter, insider buying, selling and pledges by category of person, and the board meetings called; it looks for a promoter "
    "stake falling while filings say little, for institutions leaving or arriving, for pledges, and for a gap between the patterns and the insider filings; it never names a person, explains every difference with "
    "the figures and the innocent causes, and asks the text lane when an offer document says something about the shareholders",
    ("own.holdings", "own.insider", "own.reconcile", "own.board", "ledger.read", "notes.read"),
    step_cap=8,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="forensic ownership view for one listed company")
    parser.add_argument("symbol")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--agent", action="store_true", help="let the ownership lane investigate (model calls)")
    args = parser.parse_args(argv)
    as_of, symbol = date.fromisoformat(args.as_of), args.symbol.upper()
    data = OwnershipData()
    try:
        from athena.forensic.store import ForensicStore
        from athena.forensic.tools import base_registry

        store = ForensicStore(Path(tempfile.gettempdir()) / "forensic_cli.duckdb")
        case = Case(f"{symbol}-{as_of.isoformat()}-{datetime.now():%Y%m%d%H%M%S}", "listed", symbol, as_of)
        registry = base_registry(ownership_tools(data))
        if not args.agent:
            ctx = ToolContext(case, store, "ownership")
            for name in ("own.holdings", "own.insider", "own.reconcile"):
                print(name, json.dumps(registry.call(name, {}, ctx)[0], indent=1, ensure_ascii=False, default=str))
            return 0
        from athena.forensic.supervisor import Supervisor
        from athena.llm.budget import Meter
        from athena.llm.router import build_router

        router = build_router(meter=Meter(), max_tokens=6000)
        sup = Supervisor(store, registry, {"ownership": OWNERSHIP_LANE}, router.client_for("reader_retry"), total_calls=12, max_rounds=1)
        print(json.dumps(sup.investigate(case), indent=1, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
