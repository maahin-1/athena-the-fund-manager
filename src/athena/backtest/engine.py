from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from athena.backtest.adjust import require_positive_prices
from athena.backtest.rules import ENTRY, EXIT, Rule, SeriesRule
from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import candles_from_bars
from athena.technicals.packet import build_technical_packet

WINDOW_BARS = 520  # about 760 calendar days: the same history the live specialist sees (orchestrator HISTORY_DAYS)
WARMUP_BARS = 330  # roughly 15 months, the least the monthly trend needs


@dataclass(frozen=True)
class Config:
    cost_bps_per_side: float = 15.0  # an approximation of delivery-trade charges; not exact
    stop_atr_multiple: float | None = 2.0  # protective stop below the entry fill, in ATR14; None switches it off
    trailing_stop_atr: float | None = None  # a stop this many ATRs below the highest high since entry; it only rises
    take_profit_pct: float | None = None  # sell at this percentage above the entry fill
    max_hold_bars: int | None = None  # sell at the next open after this many closes held
    position_pct: float = 100.0  # the share of cash put into each entry
    warmup_bars: int = WARMUP_BARS
    window_bars: int = WINDOW_BARS
    initial_cash: float = 100.0


@dataclass(frozen=True)
class Trade:
    entry_day: date
    exit_day: date
    entry_price: float
    exit_price: float
    net_return: float  # after costs on both sides
    reason: str  # "signal", "stop", "trailing_stop", "target" or "time"


@dataclass(frozen=True)
class BacktestResult:
    rule: str
    config: Config
    days: tuple[date, ...]  # the evaluation window, one entry per bar
    equity: tuple[float, ...]  # marked at each close, cash until the first fill
    in_market: tuple[bool, ...]
    trades: tuple[Trade, ...]
    benchmark: tuple[float, ...]  # buy and hold from the same first open, same costs, same window


def decide(bars: Sequence[Bar], index: int, rule: Rule, holding: bool, window: int) -> tuple[str | None, float | None]:
    """The rule's wish after the close of bar `index`, from the bars up to and including it and nothing later."""
    visible = bars[max(0, index + 1 - window) : index + 1]
    packet = build_technical_packet(bars[index].symbol, bars[index].timestamp, visible)
    wants = rule.leave(packet) if holding else rule.enter(packet)
    atr = packet["metrics"].get("atr_14")
    return (EXIT if holding else ENTRY) if wants else None, (atr["value"] if atr else None)


def run_backtest(bars: Sequence[Bar], rule: Rule | SeriesRule, config: Config = Config()) -> BacktestResult:
    """Long-only, one position, all in. Decisions use only information up to a bar's close and are filled at the next
    bar's open, so nothing can see the future; a protective stop fills at the stop price, or at the open if the bar
    gaps through it."""
    if isinstance(rule, SeriesRule):
        config = replace(  # the strategy decides its own stops, target, time limit and size
            config, stop_atr_multiple=rule.stop_atr, trailing_stop_atr=rule.trailing_stop_atr, take_profit_pct=rule.take_profit_pct,
            max_hold_bars=rule.max_hold_bars, position_pct=rule.position_pct,
        )
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    require_positive_prices(ordered)
    candles = candles_from_bars(ordered)
    if len(candles) != len(ordered):
        raise InsufficientData("the bars contain repeated trading days")
    if len(ordered) <= config.warmup_bars + 1:
        raise InsufficientData(f"a backtest needs more than {config.warmup_bars + 1} daily bars, got {len(ordered)}")

    if isinstance(rule, SeriesRule):
        decider = rule.prepare(candles).decide
    else:
        def decider(index: int, holding: bool) -> tuple[str | None, float | None]:
            return decide(ordered, index, rule, holding, config.window_bars)

    cost = config.cost_bps_per_side / 10_000.0
    fraction = config.position_pct / 100.0
    cash, shares = config.initial_cash, 0.0
    stop = trail = target = trail_distance = high_water = None  # the fixed stop, the trailing level, the target, and what the trail follows
    held = 0
    entry_day = entry_price = entry_cost_base = None
    pending: str | None = None
    pending_reason = "signal"
    pending_atr: float | None = None
    equity: list[float] = []
    in_market: list[bool] = []
    trades: list[Trade] = []

    def close_position(price: float, day: date, reason: str) -> None:
        nonlocal cash, shares, stop, trail, target, trail_distance, high_water, held, entry_day, entry_price, entry_cost_base
        proceeds = shares * price * (1 - cost)
        trades.append(Trade(entry_day, day, entry_price, price, proceeds / entry_cost_base - 1, reason))  # type: ignore[arg-type]
        cash += proceeds
        shares, stop, trail, target, trail_distance, high_water, held = 0.0, None, None, None, None, None, 0
        entry_day = entry_price = entry_cost_base = None

    for i in range(config.warmup_bars, len(candles)):
        bar = candles[i]
        if pending == ENTRY and shares == 0:
            invested = cash * fraction
            entry_cost_base = invested
            shares = invested / (bar.open * (1 + cost))
            cash -= invested
            entry_day, entry_price = bar.day, bar.open
            stop = bar.open - config.stop_atr_multiple * pending_atr if (config.stop_atr_multiple and pending_atr) else None
            trail_distance = config.trailing_stop_atr * pending_atr if (config.trailing_stop_atr and pending_atr) else None
            trail = bar.open - trail_distance if trail_distance else None
            target = bar.open * (1 + config.take_profit_pct / 100.0) if config.take_profit_pct else None
            high_water, held = bar.open, 0
        elif pending == EXIT and shares > 0:
            close_position(bar.open, bar.day, pending_reason)
        pending, pending_reason = None, "signal"

        if shares > 0:
            level = max((x for x in (stop, trail) if x is not None), default=None)
            if level is not None and bar.low <= level:
                reason = "stop" if stop is not None and (trail is None or stop >= trail) else "trailing_stop"
                close_position(min(bar.open, level), bar.day, reason)
            elif target is not None and bar.high >= target:
                close_position(max(bar.open, target), bar.day, "target")
        if shares > 0:  # still held after this bar: move the trailing level, count the close, and apply the time limit
            held += 1
            if trail_distance:
                high_water = max(high_water, bar.high)
                trail = max(trail, high_water - trail_distance)

        equity.append(cash + shares * bar.close)
        in_market.append(shares > 0)
        if i < len(candles) - 1:  # no point deciding after the last bar: there is no next open to fill at
            pending, pending_atr = decider(i, shares > 0)
            if shares > 0 and config.max_hold_bars and held >= config.max_hold_bars and pending != EXIT:
                pending, pending_reason = EXIT, "time"

    first = candles[config.warmup_bars]
    bought = config.initial_cash / (first.open * (1 + cost))
    benchmark = [bought * c.close for c in candles[config.warmup_bars :]]
    benchmark[-1] = bought * candles[-1].close * (1 - cost)  # liquidated at the end, so costs match the strategy's
    if shares > 0:
        equity[-1] = cash + shares * candles[-1].close * (1 - cost)
    return BacktestResult(
        rule.name, config, tuple(c.day for c in candles[config.warmup_bars :]), tuple(equity), tuple(in_market),
        tuple(trades), tuple(benchmark),
    )


def same_decisions(first: BacktestResult, second: BacktestResult, tolerance: float = 1e-4) -> bool:
    """Whether two runs traded identically: the same days in the market, the same trades in the same order with the
    same exit reasons, and trade returns within `tolerance` (a rescaled price series differs only by rounding).
    Dates are not compared, so a run on blinded data can be checked against the run on the real data."""
    return (
        first.in_market == second.in_market
        and len(first.trades) == len(second.trades)
        and all(
            a.reason == b.reason and abs(a.net_return - b.net_return) <= tolerance
            for a, b in zip(first.trades, second.trades)
        )
    )
