from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from athena.adapters.prices import JugaadPriceAdapter, YahooPriceAdapter, ohlcv_chain
from athena.backtest.adjust import Adjustment, adjust_for_splits, adjustment_note
from athena.backtest.blinding import blind_bars
from athena.backtest.engine import BacktestResult, Config, run_backtest, same_decisions
from athena.backtest.rules import RULES, Rule, SeriesRule
from athena.backtest.summary import Summary, format_summary, summarize
from athena.clock import utc_now
from athena.contracts import AthenaError, Bar
from athena.dashboard.risk import refresh_risk_data, risk_world_from_store
from athena.loaders.nse_holidays import DATASET as HOLIDAY_DATASET
from athena.loaders.nse_holidays import NseHolidayLoader, load_calendar
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.metrics.series import Series
from athena.orchestrator.builders import history_fetcher
from athena.resolver import CLI_SHOWN, Ambiguity, InstrumentIndex, InstrumentResolver, more_candidates_line
from athena.store import DataStore
from athena.strategies.compile import strategy_rule
from athena.strategies.parse import parse_strategy
from athena.strategies.presets import PRESETS
from athena.trading_calendar import TradingCalendar, ist_date

BACKTEST_YEARS = 8
DISCLAIMER = "A stylized analytical framework, not financial advice; not a registered investment adviser."


@dataclass(frozen=True)
class BacktestWorld:
    resolver: InstrumentResolver
    fetch_bars: Callable[[str], list[Bar]]  # symbol -> a long daily history
    riskfree: Series  # Nifty 1D Rate Index levels, for the Sharpe ratio
    index: Series  # NIFTY 50 closes, for the market comparison


def live_world(years: int = BACKTEST_YEARS) -> BacktestWorld:
    """Real sources: NSE master lists and holidays, jugaad-data then Yahoo prices, NIFTY 50 and overnight-rate series.
    No language model and no API key is involved."""
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    NseHolidayLoader(store).refresh()
    holiday_years = [int(record.key) for record in store.latest_records(HOLIDAY_DATASET)]
    calendar = load_calendar(store, holiday_years) if holiday_years else TradingCalendar(frozenset())
    resolver = InstrumentResolver(InstrumentIndex.from_store(store, calendar=calendar))
    chain = ohlcv_chain([JugaadPriceAdapter(), YahooPriceAdapter()], calendar)
    refresh_risk_data(store, since=ist_date(utc_now()) - timedelta(days=365 * years))
    market = risk_world_from_store(store)
    return BacktestWorld(resolver, history_fetcher(chain, days=365 * years), market.rate, market.price_index)


@dataclass(frozen=True)
class RuleRun:
    summary: Summary
    blinded_identical: bool
    result: BacktestResult
    adjustments: tuple[Adjustment, ...] = ()  # splits and bonuses found in the bars, the same on every run
    description: str = ""  # what the rule does, in words


def run_rules(
    bars: list[Bar], rules: Sequence[str | Rule | SeriesRule], riskfree: Series, index: Series, config: Config = Config()
) -> list[RuleRun]:
    """Each rule (a built-in name, a packet rule or a strategy rule) on the real bars and again on blinded bars (ticker
    removed, dates shifted, prices rescaled), both after adjusting the prices for the splits and bonus issues found in them."""
    unknown = [rule for rule in rules if isinstance(rule, str) and rule not in RULES]
    if unknown:
        raise ValueError(f"unknown rule {unknown[0]!r}; choose from {sorted(RULES)}")
    resolved = [RULES[rule] if isinstance(rule, str) else rule for rule in rules]
    names = [rule.name for rule in resolved]
    repeated = [name for name in names if names.count(name) > 1]
    if repeated:
        raise ValueError(f"two rules are named {repeated[0]!r}; give each a different name")
    adjusted, adjustments = adjust_for_splits(bars)
    blinded = blind_bars(adjusted)
    runs = []
    for rule in resolved:
        real = run_backtest(adjusted, rule, config)
        runs.append(
            RuleRun(
                summarize(real, riskfree, index),
                same_decisions(real, run_backtest(blinded, rule, config)),
                real,
                adjustments,
                rule.description,
            )
        )
    return runs


def stop_phrase(config: Config) -> str:
    return (
        f"a stop {config.stop_atr_multiple:g} x ATR below the entry fill" if config.stop_atr_multiple is not None
        else "no protective stop"
    )


def assumptions(config: Config = Config(), stop: str | None = None) -> str:
    stop = stop or stop_phrase(config)
    return (
        f"Assumed costs {config.cost_bps_per_side:g} bps per side and {stop}; decisions at the close, fills at the next "
        "open; cash earns nothing, while the Sharpe ratio still subtracts the overnight rate, which penalises time in "
        "cash. Prices are adjusted only for splits and bonuses detected from large overnight breaks, never for "
        "dividends; the NIFTY 50 comparison is a price index; and a position still open at the end counts in the total "
        "return but not in the trade statistics."
    )


def assumptions_for(runs: Sequence[RuleRun], config: Config = Config()) -> str:
    """The assumptions of a set of runs. Strategies choose their own stop, so when the stops differ each one is named."""
    if not runs:
        return assumptions(config)
    phrases = {run.summary.rule: stop_phrase(run.result.config) for run in runs}
    if len(set(phrases.values())) == 1:
        return assumptions(runs[0].result.config)
    each = "; ".join(f"{rule}: {phrase}" for rule, phrase in phrases.items())
    return assumptions(runs[0].result.config, f"a stop that depends on the rule ({each})")


def format_runs(symbol: str, runs: list[RuleRun], config: Config = Config()) -> str:
    blocks = [format_summary(run.summary, symbol, run.description) for run in runs]
    checks = ", ".join(f"{run.summary.rule}: {'same trades' if run.blinded_identical else 'DIFFERENT'}" for run in runs)
    note = adjustment_note(runs[0].adjustments) if runs else None
    return "\n\n".join(blocks) + "\n\n" + (f"{note}\n" if note else "") + (
        f"Blinding check (ticker removed, dates shifted 28 years, prices rescaled to 100): {checks}."
        f"\n{assumptions_for(runs, config)}\n{DISCLAIMER}"
    )


def analyze(world: BacktestWorld, query: str, rules: Sequence[str | Rule | SeriesRule]) -> tuple[str, int]:
    """The report text and an exit code: 0 ok, 2 when the name is ambiguous."""
    resolved = world.resolver.resolve(query)
    if isinstance(resolved, Ambiguity):
        lines = [f"{query!r} could be more than one instrument ({resolved.reason}):"]
        shown = resolved.candidates[:CLI_SHOWN]
        lines += [f"  {c.identifier}  {c.name}  ({c.asset_class})" for c in shown]
        more = more_candidates_line(len(resolved.candidates), len(shown))
        if more:
            lines.append(more)
        return "\n".join(lines + ["Re-run with the exact symbol."]), 2
    if resolved.asset_class not in ("equity", "etf"):
        raise ValueError(f"backtests cover stocks and ETFs, not {resolved.asset_class}")
    runs = run_rules(world.fetch_bars(resolved.identifier), rules, world.riskfree, world.index)
    return format_runs(resolved.identifier, runs), 0


def load_strategy_rule(path: str) -> SeriesRule:
    """A strategy file (JSON) as a rule, or a ValueError that says what is wrong with it."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ValueError(f"{path} is not valid JSON: it is nested too deeply") from exc
    except ValueError as exc:  # for example an integer with more than 4300 digits
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    return strategy_rule(parse_strategy(data))


def main(argv: list[str] | None = None, factory: Callable[..., BacktestWorld] = live_world) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.backtest", description="Backtest rules and strategies on one instrument.")
    parser.add_argument("query", nargs="+", help="ticker, ISIN or name, for example SBIN")
    parser.add_argument("--rule", choices=[*RULES, "both"], default=None, help="the built-in rules (the default when no strategy is given)")
    parser.add_argument("--preset", action="append", default=[], choices=list(PRESETS), help="a ready-made strategy; may be repeated")
    parser.add_argument("--strategy", action="append", default=[], metavar="FILE", help="a strategy in a JSON file; may be repeated")
    parser.add_argument("--years", type=int, default=BACKTEST_YEARS)
    args = parser.parse_args(argv)
    try:
        rules: list[str | SeriesRule] = []
        if args.rule or not (args.preset or args.strategy):
            rules += [args.rule] if args.rule in RULES else list(RULES)
        rules += [strategy_rule(PRESETS[key]) for key in args.preset]
        rules += [load_strategy_rule(path) for path in args.strategy]
        text, code = analyze(factory(years=args.years), " ".join(args.query), rules)
    except (AthenaError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
