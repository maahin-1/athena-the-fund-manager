"""The market lane: its tools (plain code over the warehouse's price bars and the evidence store's checked claims) and its lane specification.

    python -m athena.forensic.market.lane SYMBOL [--as-of 2026-11-09]             # the price summary, the largest moves and the checked claims for one company, no model
    python -m athena.forensic.market.lane SYMBOL --agent                          # the lane investigates, within 12 model calls

What the price did and what outsiders said about the company are laid beside each other here. All arithmetic (returns, drawdown, volatility, volume against its usual, the move against the index) is done
in code over bars dated on or before the as-of day. Prices are the closes as printed, which a split, bonus or demerger breaks: the day such an action goes ex is never counted as a move, and a return is
taken only from the close of the last one in the window. A claim counts only once it was first seen on or before the as-of day, and what the check said is shown with it (confirmed, contradicted, not yet
checkable), so a headline is never read as a fact."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.forensic.model import Case, LaneSpec
from athena.forensic.tools import Tool, ToolContext, ToolError

IST_SHIFT = timedelta(hours=5, minutes=30)
BENCHMARK = "NIFTY 50"
BREAKS = ("bonus", "split", "demerger", "capital_reduction", "scheme")  # actions that change the printed price without a change in value
MIN_BARS = 20
MAX_MOVES = 10
MAX_CLAIMS = 30
LIMITS = (
    "Closes are as the exchange printed them; a split, bonus or demerger breaks the series, so a return is taken only from the close of the last such ex-date in the window and the ex-date itself is never a move. "
    "A trading halt or a thin market shows as a small volume, not as a missing day. The index is the NIFTY 50, which is not the company's sector. A claim is a headline read by patterns, not by a person: "
    "its check compares it with our own data only where that is possible, and `unchecked` says nothing either way."
)


class MarketData:
    """Opens the warehouse and the evidence store when first needed. Each is a factory so a test can hand in a stand-in with the same methods."""

    def __init__(self, warehouse: Callable[[], Any] | None = None, evidence: Callable[[], Any] | None = None):
        self._wh_factory, self._ev_factory = warehouse, evidence
        self._wh: Any = None
        self._ev: Any = None
        self._index: dict[date, float] | None = None

    @property
    def warehouse(self) -> Any:
        if self._wh is None:
            if self._wh_factory is None:
                from athena.warehouse.warehouse import Warehouse

                self._wh = Warehouse(Path(".athena") / "warehouse.duckdb", read_only=True, wait_seconds=10)
            else:
                self._wh = self._wh_factory()
        return self._wh

    @property
    def evidence(self) -> Any:
        if self._ev is None:
            if self._ev_factory is None:
                from athena.evidence.store import EvidenceStore

                self._ev = EvidenceStore(read_only=True)
            else:
                self._ev = self._ev_factory()
        return self._ev

    def bars(self, ctx: ToolContext, days: int) -> list[tuple[date, float, float, float, float, float]]:
        """(day, open, high, low, close, volume) from the window's start to the as-of day, oldest first; bars with no positive close are left out."""
        since = ctx.case.as_of - timedelta(days=days)
        rows = [((b.timestamp + IST_SHIFT).date(), b.open, b.high, b.low, b.close, b.volume or 0.0) for b in self.warehouse.bars(ctx.case.target, since, ctx.case.as_of)]
        rows = [r for r in rows if r[4] and r[4] > 0]
        if len(rows) < MIN_BARS:
            raise ToolError(f"{ctx.case.target} has {len(rows)} price bars between {since.isoformat()} and {ctx.case.as_of.isoformat()}; at least {MIN_BARS} are needed")
        return rows

    def index(self) -> dict[date, float]:
        if self._index is None:
            try:
                held = {n.lower(): n for n in self.warehouse.index_names()}  # the exchange's file holds "NIFTY 50" as "Nifty 50"
                self._index = self.warehouse.index_close(held[BENCHMARK.lower()]) if BENCHMARK.lower() in held else {}
            except Exception:  # noqa: BLE001 - a missing index only removes the comparison with it
                self._index = {}
        return self._index

    def breaks(self, ctx: ToolContext, first: date) -> list[Any]:
        """The corporate actions in the window that change the printed price without changing value."""
        try:
            actions = self.warehouse.actions(ctx.case.target)
        except Exception:  # noqa: BLE001
            return []
        return [a for a in actions if first <= a.day <= ctx.case.as_of and a.kind in BREAKS]


def _pct(a: float, b: float) -> float:
    return round((b / a - 1.0) * 100.0, 3)


def _clean(rows: list[tuple[date, float, float, float, float, float]], breaks: Sequence[Any]) -> list[tuple[date, float, float, float, float, float]]:
    """The bars from the last break's ex-date on: the ex-date's own bar is the first of the new series, whose close is compared only with later closes."""
    if not breaks:
        return rows
    cut = max(a.day for a in breaks)
    return [r for r in rows if r[0] >= cut]


def _index_return(index: dict[date, float], first: date, last: date) -> float | None:
    days = sorted(d for d in index if first <= d <= last)
    return _pct(index[days[0]], index[days[-1]]) if len(days) >= 2 else None


def market_tools(data: MarketData) -> list[Tool]:
    def summary(ctx: ToolContext, args: dict[str, Any]) -> Any:
        days = max(60, min(int(args.get("days", 365)), 1500))
        rows = data.bars(ctx, days)
        breaks = data.breaks(ctx, rows[0][0])
        clean = _clean(rows, breaks)
        if len(clean) < MIN_BARS:
            raise ToolError(f"after the last corporate action that breaks the price series ({max(a.day for a in breaks).isoformat()}) only {len(clean)} bars remain")
        closes = [r[4] for r in clean]
        daily = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        peak, worst, worst_from, worst_to = closes[0], 0.0, clean[0][0], clean[0][0]
        peak_day = clean[0][0]
        for r in clean:
            if r[4] > peak:
                peak, peak_day = r[4], r[0]
            drop = r[4] / peak - 1.0
            if drop < worst:
                worst, worst_from, worst_to = drop, peak_day, r[0]
        volumes = [r[5] for r in clean if r[5] > 0]
        median_volume = statistics.median(volumes) if volumes else None
        index_ret = _index_return(data.index(), clean[0][0], clean[-1][0])
        stock_ret = _pct(closes[0], closes[-1])
        high, low = max(clean, key=lambda r: r[2]), min(clean, key=lambda r: r[3])
        return {
            "from": clean[0][0].isoformat(), "to": clean[-1][0].isoformat(), "trading_days": len(clean), "first_close": closes[0], "last_close": closes[-1], "return_percent": stock_ret,
            "index": BENCHMARK, "index_return_percent": index_ret, "relative_to_index_points": None if index_ret is None else round(stock_ret - index_ret, 3),
            "high": {"day": high[0].isoformat(), "price": high[2]}, "low": {"day": low[0].isoformat(), "price": low[3]},
            "max_drawdown_percent": round(worst * 100.0, 3), "drawdown_peak_day": worst_from.isoformat(), "drawdown_trough_day": worst_to.isoformat(),
            "volatility_annualised_percent": round(statistics.pstdev(daily) * math.sqrt(252) * 100.0, 3) if len(daily) >= 2 else None,
            "median_daily_volume": median_volume, "last_volume_vs_median": round(clean[-1][5] / median_volume, 3) if median_volume else None,
            "price_breaks_in_window": [{"day": a.day.isoformat(), "kind": a.kind, "text": a.text[:120]} for a in breaks], "returns_taken_from_after_break": bool(breaks), "limits": LIMITS,
        }

    def moves(ctx: ToolContext, args: dict[str, Any]) -> Any:
        days = max(60, min(int(args.get("days", 365)), 1500))
        k = max(1, min(int(args.get("k", 5)), MAX_MOVES))
        rows = data.bars(ctx, days)
        break_days = {a.day for a in data.breaks(ctx, rows[0][0])}
        index = data.index()
        found = []
        for i in range(1, len(rows)):
            day, close, prev = rows[i][0], rows[i][4], rows[i - 1][4]
            if day in break_days:
                continue
            before = [r[5] for r in rows[max(0, i - 20): i] if r[5] > 0]
            usual = statistics.median(before) if before else None
            idx_ret = _pct(index[rows[i - 1][0]], index[day]) if day in index and rows[i - 1][0] in index else None
            move = _pct(prev, close)
            found.append({"day": day.isoformat(), "close_to_close_percent": move, "volume_vs_prior_20_day_median": round(rows[i][5] / usual, 2) if usual else None, "index_percent": idx_ret,
                          "relative_to_index_points": None if idx_ret is None else round(move - idx_ret, 3), "_abs": abs(move)})
        found.sort(key=lambda m: -m["_abs"])
        for m in found:
            m.pop("_abs")
        return {"window_days": days, "largest": found[:k], "ex_dates_skipped": sorted(d.isoformat() for d in break_days)}

    def around(ctx: ToolContext, args: dict[str, Any]) -> Any:
        try:
            day = date.fromisoformat(str(args["day"]))
        except (KeyError, ValueError) as exc:
            raise ToolError("give day as YYYY-MM-DD") from exc
        before, after = max(1, min(int(args.get("before", 5)), 60)), max(1, min(int(args.get("after", 5)), 60))
        if day > ctx.case.as_of:
            raise ToolError(f"{day.isoformat()} is after the as-of day {ctx.case.as_of.isoformat()}")
        rows = data.bars(ctx, max(365, (ctx.case.as_of - day).days + 120))
        pos = next((i for i, r in enumerate(rows) if r[0] >= day), None)
        if pos is None or pos - before < 0:
            raise ToolError("not enough bars before that day")
        if pos + after >= len(rows):
            raise ToolError("not enough bars after that day (nothing later than the as-of day is used)")
        start, end = rows[pos - before], rows[pos + after]
        breaks = [a for a in data.breaks(ctx, start[0]) if start[0] < a.day <= end[0]]
        if breaks:
            raise ToolError(f"a {breaks[0].kind} went ex on {breaks[0].day.isoformat()} inside this window, so closes before and after it are not comparable")
        index = data.index()
        idx = _pct(index[start[0]], index[end[0]]) if start[0] in index and end[0] in index else None
        stock = _pct(start[4], end[4])
        return {"event_day": rows[pos][0].isoformat(), "from": start[0].isoformat(), "to": end[0].isoformat(), "trading_days_before": before, "trading_days_after": after, "return_percent": stock,
                "index_percent": idx, "relative_to_index_points": None if idx is None else round(stock - idx, 3)}

    def claims(ctx: ToolContext, args: dict[str, Any]) -> Any:
        days = max(7, min(int(args.get("days", 90)), 400))
        end = datetime.combine(ctx.case.as_of, time.max, tzinfo=timezone.utc)
        rows = [r for r in data.evidence.claims_for(ctx.case.target, end - timedelta(days=days), end) if r["first_seen_at"] <= end]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        seen: set[str] = set()
        shown = []
        for r in rows:
            if r["story_id"] in seen:  # copies of one story are one voice
                continue
            seen.add(r["story_id"])
            shown.append({"published": r["published_at"].date().isoformat(), "publisher": r["publisher"], "kind": r["kind"], "title": r["title"][:160], "outcome": r["outcome"], "detail": (r["detail"] or "")[:200]})
        return {"window_days": days, "claims_read": len(rows), "independent_stories": len(seen), "by_outcome": counts, "latest": shown[:MAX_CLAIMS], "limits": LIMITS}

    return [
        Tool("mkt.summary", "the price over a window: return, return against the NIFTY 50, high and low, largest drawdown, volatility, volume against its median, and the splits or bonuses that break the series", "days?: 60 to 1500 (default 365)", summary),
        Tool("mkt.moves", "the largest single-day moves in the window with the volume against the prior 20-day median and the index move that day (ex-dates of price-breaking actions are skipped)", "days?: 60 to 1500 (default 365), k?: 1 to 10 (default 5)", moves),
        Tool("mkt.around", "the return from some trading days before a date to some after it, against the index (nothing after the as-of day is used)", "day: YYYY-MM-DD, before?: trading days (default 5), after?: trading days (default 5)", around),
        Tool("mkt.claims", "headlines read for claims about the company, each with what our own data said (confirmed, contradicted, not yet checkable), copies of one story counted once", "days?: 7 to 400 (default 90)", claims),
    ]


MARKET_LANE = LaneSpec(
    "market",
    "reads what the share price and the news did around a company's own events: the largest moves and whether they came with unusual volume or with a filing, a fall or rise against the index that the filings do not "
    "explain, and claims about the company that our own data contradicts; it reports the figures the tools worked out, never a view on the price, and asks the text lane or the ownership lane when a document or a holding might explain a move",
    ("mkt.summary", "mkt.moves", "mkt.around", "mkt.claims", "ledger.read", "notes.read"),
    step_cap=8,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="forensic market view for one listed company")
    parser.add_argument("symbol")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--agent", action="store_true", help="let the market lane investigate (model calls)")
    args = parser.parse_args(argv)
    as_of, symbol = date.fromisoformat(args.as_of), args.symbol.upper()
    data = MarketData()
    try:
        from athena.forensic.store import ForensicStore
        from athena.forensic.tools import base_registry

        store = ForensicStore(Path(tempfile.gettempdir()) / "forensic_cli.duckdb")
        case = Case(f"{symbol}-{as_of.isoformat()}-{datetime.now():%Y%m%d%H%M%S}", "listed", symbol, as_of)
        registry = base_registry(market_tools(data))
        if not args.agent:
            ctx = ToolContext(case, store, "market")
            for name in ("mkt.summary", "mkt.moves", "mkt.claims"):
                print(name, json.dumps(registry.call(name, {}, ctx)[0], indent=1, ensure_ascii=False, default=str))
            return 0
        from athena.forensic.supervisor import Supervisor
        from athena.llm.budget import Meter
        from athena.llm.router import build_router

        router = build_router(meter=Meter(), max_tokens=6000)
        sup = Supervisor(store, registry, {"market": MARKET_LANE}, router.client_for("reader_retry"), total_calls=12, max_rounds=1)
        print(json.dumps(sup.investigate(case), indent=1, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
