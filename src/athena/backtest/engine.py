from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.backtest.adjust import require_positive_prices
from athena.backtest.rules import Rule
from athena.contracts import Bar, InsufficientData
from athena.technicals.candles import candles_from_bars
from athena.technicals.packet import build_technical_packet

WINDOW_BARS = 520  # about 760 calendar days: the same history the live specialist sees (orchestrator HISTORY_DAYS)
WARMUP_BARS = 330  # roughly 15 months, the least the monthly trend needs
ENTRY, EXIT = "enter", "exit"


@dataclass(frozen=True)
class Config:
    cost_bps_per_side: float = 15.0  # an approximation of delivery-trade charges; not exact
    stop_atr_multiple: float | None = 2.0  # protective stop below the entry fill, in ATR14; None switches it off
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
    reason: str  # "signal" or "stop"


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


def run_backtest(bars: Sequence[Bar], rule: Rule, config: Config = Config()) -> BacktestResult:
    """Long-only, one position, all in. Decisions use only information up to a bar's close and are filled at the next
    bar's open, so nothing can see the future; a protective stop fills at the stop price, or at the open if the bar
    gaps through it."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    require_positive_prices(ordered)
    candles = candles_from_bars(ordered)
    if len(candles) != len(ordered):
        raise InsufficientData("the bars contain repeated trading days")
    if len(ordered) <= config.warmup_bars + 1:
        raise InsufficientData(f"a backtest needs more than {config.warmup_bars + 1} daily bars, got {len(ordered)}")

    cost = config.cost_bps_per_side / 10_000.0
    cash, shares, stop = config.initial_cash, 0.0, None
    entry_day = entry_price = entry_cost_base = None
    pending: str | None = None
    pending_atr: float | None = None
    equity: list[float] = []
    in_market: list[bool] = []
    trades: list[Trade] = []

    def close_position(price: float, day: date, reason: str) -> None:
        nonlocal cash, shares, stop, entry_day, entry_price, entry_cost_base
        proceeds = shares * price * (1 - cost)
        trades.append(Trade(entry_day, day, entry_price, price, proceeds / entry_cost_base - 1, reason))  # type: ignore[arg-type]
        cash, shares, stop = proceeds, 0.0, None
        entry_day = entry_price = entry_cost_base = None

    for i in range(config.warmup_bars, len(candles)):
        bar = candles[i]
        if pending == ENTRY and shares == 0:
            entry_cost_base = cash
            shares = cash / (bar.open * (1 + cost))
            cash, entry_day, entry_price = 0.0, bar.day, bar.open
            stop = bar.open - config.stop_atr_multiple * pending_atr if (config.stop_atr_multiple and pending_atr) else None
        elif pending == EXIT and shares > 0:
            close_position(bar.open, bar.day, "signal")
        pending = None

        if shares > 0 and stop is not None and bar.low <= stop:
            close_position(min(bar.open, stop), bar.day, "stop")

        equity.append(cash + shares * bar.close)
        in_market.append(shares > 0)
        if i < len(candles) - 1:  # no point deciding after the last bar: there is no next open to fill at
            pending, pending_atr = decide(ordered, i, rule, shares > 0, config.window_bars)

    first = candles[config.warmup_bars]
    bought = config.initial_cash / (first.open * (1 + cost))
    benchmark = [bought * c.close for c in candles[config.warmup_bars :]]
    benchmark[-1] = bought * candles[-1].close * (1 - cost)  # liquidated at the end, so costs match the strategy's
    if shares > 0:
        equity[-1] = shares * candles[-1].close * (1 - cost)
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
