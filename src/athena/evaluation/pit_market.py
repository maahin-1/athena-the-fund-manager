"""Test the technical analyst on past dates using only what the warehouse stores.

    python -m athena.evaluation.pit_market --from 2024-01-31 --to 2026-07-31 --out wh_tech        # run (resumes) and print the report
    python -m athena.evaluation.pit_market --report wh_tech

Stocks are chosen by a rule (liquidity rank, then a fixed seed), packets are built from bars up to each month end and blinded, and the
model is scored against the NIFTY 50 beside a plain trend rule read off the same packet. See
docs/superpowers/specs/2026-10-12-phase-2e-technical-test-design.md."""
from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError, Bar
from athena.evaluation.pit_cases import PitCaseBuilder, month_ends
from athena.evaluation.pit_run import RUN_DIR, read_rows, report, run_cases
from athena.price_integrity.history import CheckedHistory
from athena.trading_calendar import ist_date
from athena.warehouse.packets import BENCHMARK
from athena.warehouse.warehouse import DEFAULT_PATH, STOCK, Warehouse

NEEDED_BARS = 253  # closes behind the first date, for the packet's long frames
TOP_RANKS, MID_RANKS = 100, 600
SEED = 11
PACKET_FOR = {"quant_technical": "quant_technical", "rule_trend": "quant_technical"}
TREND_SIGNAL = {"aligned_up": "bullish", "aligned_down": "bearish"}


@dataclass(frozen=True)
class Market:
    """What the test reads from the warehouse, loaded once so the file is not held open for hours."""

    raw: Mapping[str, list[Bar]]
    actions: Mapping[str, list[Any]]
    index: Mapping[date, float]
    valuation: Mapping[date, Mapping[str, float]] = field(default_factory=dict)  # the NIFTY 50's P/E, P/B and yield by day
    statements: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # Yahoo payloads as stored today (a case after the filings end reads them)

    def builder(
        self, filings: Callable[[str], Any] | None = None, load_filing: Callable[[Any], Any] | None = None
    ) -> PitCaseBuilder:
        history = CheckedHistory(lambda symbol: list(self.raw[symbol]), lambda symbol: (), lambda symbol: self.actions.get(symbol, []))
        adjusted: dict[str, list[Bar]] = {}

        def adjusted_bars(symbol: str) -> list[Bar]:
            if symbol not in adjusted:
                adjusted[symbol] = history(symbol)
            return adjusted[symbol]

        def no_filing(_: Any) -> Any:
            raise AthenaError("the warehouse test has no filings")

        def yahoo(symbol: str) -> Mapping[str, Any]:
            if symbol not in self.statements:
                raise AthenaError(f"no statements stored for {symbol}")
            return self.statements[symbol]

        return PitCaseBuilder(
            lambda symbol: self.raw[symbol], adjusted_bars, dict(self.index), dict(self.valuation), filings or (lambda symbol: []),
            load_filing or no_filing, yahoo if self.statements else None, unadjusted_sources=None,
        )


def rule_trend(packet: Mapping[str, Any]) -> dict[str, Any]:
    """The plain baseline: the packet's own trend label as a call, with no confidence."""
    trend = packet.get("metrics", {}).get("trend_alignment", {}).get("value")
    return {"signal": TREND_SIGNAL.get(trend, "neutral"), "confidence": 0, "data_coverage": "full"}


def choose_stocks(
    warehouse: Warehouse, first: date, want_top: int, want_mid: int, seed: int = SEED
) -> tuple[list[str], dict[str, list[Bar]], dict[str, list[Any]]]:
    """Stocks by rule: of the most traded, the first `want_top` with a full history and no unexplained price break; of the next ranks,
    `want_mid` drawn with the seed under the same conditions. Returns the symbols with their raw bars and actions."""
    last = warehouse.last_loaded_day()
    if last is None:
        raise AthenaError("the warehouse has no prices")
    traded = warehouse.traded_value(last)
    stocks = {e["symbol"] for e in warehouse.universe(STOCK)}
    ranked = [s for s, _ in sorted(traded.items(), key=lambda kv: -(kv[1] or 0)) if s in stocks]
    raw: dict[str, list[Bar]] = {}
    actions: dict[str, list[Any]] = {}

    def usable(symbol: str) -> bool:
        bars = warehouse.bars(symbol)
        behind = sum(1 for bar in bars if ist_date(bar.timestamp) <= first)
        if behind < NEEDED_BARS or (last - ist_date(bars[-1].timestamp)).days > 10:
            return False
        acts = warehouse.actions(symbol)
        history = CheckedHistory(lambda s: list(bars), lambda s: (), lambda s: acts)
        history(symbol)
        if history.price_flags(symbol, len(bars)):
            return False
        raw[symbol], actions[symbol] = bars, acts
        return True

    top = [s for s in ranked[:TOP_RANKS] if usable(s)][:want_top]
    pool = ranked[TOP_RANKS:MID_RANKS]
    random.Random(seed).shuffle(pool)
    mid: list[str] = []
    for symbol in pool:
        if len(mid) >= want_mid:
            break
        if usable(symbol):
            mid.append(symbol)
    chosen = top + mid
    return chosen, {s: raw[s] for s in chosen}, {s: actions[s] for s in chosen}


def market_of(warehouse: Warehouse, symbols: Sequence[str], raw: Mapping[str, list[Bar]] | None = None, actions: Mapping[str, list[Any]] | None = None) -> Market:
    held = {s: p for s in symbols if (statement := warehouse.statement(s)) is not None for p in [statement[0]]}
    return Market(
        raw or {s: warehouse.bars(s) for s in symbols}, actions or {s: warehouse.actions(s) for s in symbols},
        warehouse.index_close(BENCHMARK), warehouse.index_valuation(BENCHMARK), held,
    )


def load(path: Path, first: date, want_top: int, want_mid: int, pinned: Sequence[str] | None = None) -> tuple[list[str], Market]:
    """The stocks by rule, or the `pinned` list as it stands (the rule's draw moves with the latest day loaded, so a run that must be
    repeatable pins its list)."""
    with Warehouse(path, read_only=True, wait_seconds=300) as warehouse:
        if pinned is not None:
            return list(pinned), market_of(warehouse, pinned)
        symbols, raw, actions = choose_stocks(warehouse, first, want_top, want_mid)
        return symbols, market_of(warehouse, symbols, raw, actions)


FUNDAMENTALS = ("valuation", "moat_quality", "earnings_intelligence")
FUNDAMENTALS_PACKET_FOR = {name: "fundamentals" for name in FUNDAMENTALS}


def live_fundamentals_analysts() -> dict[str, Callable[[dict[str, Any]], Mapping[str, Any]]]:
    from athena.agents.base import Specialist
    from athena.agents.earnings_intelligence import EARNINGS_INTELLIGENCE
    from athena.agents.moat_quality import MOAT_QUALITY
    from athena.agents.valuation import VALUATION
    from athena.llm.router import build_router

    client = build_router().client_for("specialist")
    specs = {"valuation": VALUATION, "moat_quality": MOAT_QUALITY, "earnings_intelligence": EARNINGS_INTELLIGENCE}
    return {name: Specialist(spec, client).analyze for name, spec in specs.items()}


def exchange_filings() -> tuple[Callable[[str], Any], Callable[[Any], Any]]:
    """The exchange's results filings for a symbol and one filing's statements, both cached on disk."""
    from functools import lru_cache

    from athena.pit.nse_results import CachedFilings, FilingStore, NseResults

    results = NseResults()
    return lru_cache(maxsize=64)(CachedFilings(results).filings), FilingStore(results).filing


def live_analyst() -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    from athena.agents.base import Specialist
    from athena.agents.quant_technical import QUANT_TECHNICAL
    from athena.llm.router import build_router

    return Specialist(QUANT_TECHNICAL, build_router().client_for("specialist")).analyze


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.evaluation.pit_market", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--from", dest="start", default="2024-01-31", help="first as-of date (month ends are used)")
    parser.add_argument("--to", dest="end", default="2026-07-31")
    parser.add_argument("--top", type=int, default=20, help="stocks from the 100 most traded")
    parser.add_argument("--mid", type=int, default=20, help="stocks drawn from ranks 101 to 600")
    parser.add_argument("--out", default="wh_tech")
    parser.add_argument("--no-blind", action="store_true")
    parser.add_argument("--report", metavar="NAME")
    parser.add_argument("--fundamentals", action="store_true", help="test the valuation, moat and earnings analysts (exchange filings up to 2025-03-31, stored statements after)")
    parser.add_argument("--stocks", metavar="FILE", help="a JSON file {\"symbols\": [...]} to use as it stands instead of drawing stocks by rule")
    args = parser.parse_args(argv)
    fundamentals = args.fundamentals
    packet_for = FUNDAMENTALS_PACKET_FOR if fundamentals else PACKET_FOR
    if args.report:
        print(report(read_rows(RUN_DIR / f"{args.report}.jsonl"), packet_for))
        return 0
    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    pinned = json.loads(Path(args.stocks).read_text(encoding="utf-8"))["symbols"] if args.stocks else None
    symbols, market = load(Path(args.db), first, args.top, args.mid, pinned)
    print(f"{len(symbols)} stocks: {', '.join(symbols)}", flush=True)
    if fundamentals:
        filings, load_filing = exchange_filings()
        builder, analysts = market.builder(filings, load_filing), live_fundamentals_analysts()
    else:
        builder, analysts = market.builder(), {"quant_technical": live_analyst(), "rule_trend": rule_trend}
    made = run_cases(
        builder, analysts, symbols, month_ends(first, last), RUN_DIR / f"{args.out}.jsonl", 1, not args.no_blind,
        lambda text: print(text, flush=True), packet_for,
    )
    print(f"{made} analyst runs made")
    print(report(read_rows(RUN_DIR / f"{args.out}.jsonl"), packet_for))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
