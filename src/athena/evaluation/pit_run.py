"""Run the analysts on past dates and score them against what happened next.

    python -m athena.evaluation.pit_run --symbols TCS,KOTAKBANK --from 2026-03-31 --to 2026-07-31 --repeats 3 --out clean
    python -m athena.evaluation.pit_run --report clean

News is left out: there is no record of what was known on a past day. Each run appends to .athena/pit_runs/<name>.jsonl
and skips what is already there, so an interrupted run resumes."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.evaluation.pit_cases import PitCase, PitCaseBuilder, month_ends
from athena.evaluation.pit_score import calls_from_rows, format_score, score

RUN_DIR = Path(".athena") / "pit_runs"
PACKET_FOR = {"quant_technical": "quant_technical", "valuation": "fundamentals", "moat_quality": "fundamentals", "earnings_intelligence": "fundamentals"}
INSUFFICIENT = "insufficient"

Analyst = Callable[[dict[str, Any]], Mapping[str, Any]]


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut off by an interrupted run
    return rows


def rows_for(case: PitCase, specialist: str, run: int, output: Mapping[str, Any] | None, error: str = "") -> list[dict[str, Any]]:
    """One log row per horizon whose outcome is known: the call, and the label it is scored against."""
    usable = output is not None and output.get("data_coverage") != INSUFFICIENT
    base = {
        "symbol": case.symbol, "as_of": case.as_of.isoformat(), "specialist": specialist, "run": run, "blinded": case.blinded,
        "source": case.statements_source, "signal": output["signal"] if usable else None,
        "confidence": output.get("confidence", 0) if usable else 0, "coverage": output.get("data_coverage") if output else None, "error": error,
        "notes": list(case.notes),
    }
    return [
        {**base, "horizon": horizon, "label": outcome.label, "excess": round(outcome.excess, 4)}
        for horizon, outcome in sorted(case.outcomes.items()) if outcome is not None
    ]


def run_cases(
    builder: PitCaseBuilder,
    analysts: Mapping[str, Analyst],
    symbols: Sequence[str],
    days: Iterable[date],
    out: Path,
    repeats: int = 1,
    blind: bool = False,
    on_progress: Callable[[str], None] = lambda text: None,
    packet_for: Mapping[str, str] = PACKET_FOR,
) -> int:
    """Build each stock-and-date case and run every analyst on its packet `repeats` times, appending the rows to `out`.
    Returns the number of analyst runs made. A case that cannot be built, or a run that fails, is logged and skipped."""
    done = {(r["symbol"], r["as_of"], r["specialist"], r["run"]) for r in read_rows(out)}
    out.parent.mkdir(parents=True, exist_ok=True)
    made = 0
    for symbol in symbols:
        for day in days:
            wanted = [(name, run) for name in analysts for run in range(repeats) if (symbol, day.isoformat(), name, run) not in done]
            if not wanted:
                continue
            try:
                case = builder.build(symbol, day, blind)
            except AthenaError as exc:
                on_progress(f"{symbol} {day}: skipped ({exc})")
                continue
            if all(outcome is None for outcome in case.outcomes.values()):
                on_progress(f"{symbol} {day}: no outcome is known yet")
                continue
            with out.open("a", encoding="utf-8") as handle:
                for name, run in wanted:
                    packet = case.packets.get(packet_for[name])
                    if packet is None:
                        rows = rows_for(case, name, run, None, "no packet for this case")
                    else:
                        try:
                            rows = rows_for(case, name, run, analysts[name](packet))
                        except AthenaError as exc:
                            rows = rows_for(case, name, run, None, f"{type(exc).__name__}: {exc}"[:200])
                    for row in rows:
                        handle.write(json.dumps(row) + "\n")
                    made += 1
            on_progress(f"{symbol} {day}: {len(wanted)} runs")
    return made


def report(rows: Iterable[Mapping[str, Any]], specialists: Iterable[str] = PACKET_FOR) -> str:
    calls = calls_from_rows(rows)
    horizons = sorted({call.horizon for call in calls})
    results = [score(calls, name, h) for name in specialists for h in horizons]
    blocks = [format_score(result) for result in results if result is not None and result.cases]
    silent = sorted({result.specialist for result in results if result is not None and not result.cases})
    if silent and blocks:
        blocks.append("No call at all (no packet, or every run abstained): " + ", ".join(silent))
    return "\n\n".join(blocks) if blocks else "No scored runs yet."


LOOKBACK_DAYS = 400  # prices needed before the first as-of date (253 closes for the technical packet) ...
LOOKAHEAD_DAYS = 400  # ... and after the last, for the 252-trading-day outcome


def live_builder(first: date, last: date) -> tuple[PitCaseBuilder, dict[str, Analyst]]:
    """The real sources: the exchange's daily price files (then jugaad, then Yahoo), NSE filings, Yahoo statements, the NIFTY 50,
    and the specialist model. Prices are fetched for the window the dates need, not nine years."""
    from datetime import timedelta
    from functools import lru_cache

    from athena.agents.base import Specialist
    from athena.agents.earnings_intelligence import EARNINGS_INTELLIGENCE
    from athena.agents.moat_quality import MOAT_QUALITY
    from athena.agents.quant_technical import QUANT_TECHNICAL
    from athena.agents.valuation import VALUATION
    from athena.cli import live_sources
    from athena.clock import utc_now
    from athena.dashboard.risk import refresh_risk_data, risk_world_from_store
    from athena.loaders.fundamentals import DATASET, FundamentalsLoader
    from athena.loaders.index_valuation import valuation_history
    from athena.pit.bhavcopy import BhavcopyPriceAdapter
    from athena.pit.nse_results import CachedFilings, FilingStore, NseResults
    from athena.price_integrity.history import CheckedHistory
    from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
    from athena.trading_calendar import TradingCalendar, ist_date

    sources = live_sources()
    today = ist_date(utc_now())
    window = (first - timedelta(days=LOOKBACK_DAYS), min(last + timedelta(days=LOOKAHEAD_DAYS), today))
    refresh_risk_data(sources.store, since=window[0])
    chain = ohlcv_chain([BhavcopyPriceAdapter(), JugaadPriceAdapter(), YahooPriceAdapter()], TradingCalendar(frozenset()))
    raw = lru_cache(maxsize=64)(lambda symbol: chain.run(symbol, since=window[0], until=window[1]).value)
    adjusted = lru_cache(maxsize=64)(CheckedHistory(raw, sources.events, sources.actions))
    loader = FundamentalsLoader(sources.store)
    results = NseResults()

    @lru_cache(maxsize=64)
    def yahoo(symbol: str) -> dict[str, Any]:
        loader.refresh(symbol=symbol)
        return loader.read(DATASET, symbol).payload

    client = sources.llm_router.client_for("specialist")
    specs = {"quant_technical": QUANT_TECHNICAL, "valuation": VALUATION, "moat_quality": MOAT_QUALITY, "earnings_intelligence": EARNINGS_INTELLIGENCE}
    analysts = {name: Specialist(spec, client).analyze for name, spec in specs.items()}
    builder = PitCaseBuilder(
        raw, adjusted, risk_world_from_store(sources.store).price_index, valuation_history(sources.store),
        lru_cache(maxsize=64)(CachedFilings(results).filings), FilingStore(results).filing, yahoo,
    )
    return builder, analysts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.evaluation.pit_run", description=__doc__.split("\n")[0])
    parser.add_argument("--symbols", help="comma-separated NSE symbols")
    parser.add_argument("--from", dest="start", help="first as-of date, YYYY-MM-DD (month ends are used)")
    parser.add_argument("--to", dest="end", help="last as-of date")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--blind", action="store_true", help="remove the symbol and move every date back 28 years in the packets")
    parser.add_argument("--specialists", default=",".join(PACKET_FOR))
    parser.add_argument("--out", default="run", help="log name under .athena/pit_runs")
    parser.add_argument("--report", metavar="NAME", help="score a log instead of running")
    args = parser.parse_args(argv)
    if args.report:
        print(report(read_rows(RUN_DIR / f"{args.report}.jsonl")))
        return 0
    if not (args.symbols and args.start and args.end):
        parser.error("--symbols, --from and --to are needed to run")
    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    builder, analysts = live_builder(first, last)
    chosen = {name: analysts[name] for name in args.specialists.split(",")}
    days = month_ends(first, last)
    made = run_cases(builder, chosen, args.symbols.split(","), days, RUN_DIR / f"{args.out}.jsonl", args.repeats, args.blind, print)
    print(f"{made} analyst runs made")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
