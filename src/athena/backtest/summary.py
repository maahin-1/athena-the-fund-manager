from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from athena.backtest.engine import BacktestResult
from athena.contracts import InsufficientData
from athena.metrics import stats
from athena.metrics.series import Series, level_asof, returns_from_levels, riskfree_returns


@dataclass(frozen=True)
class Summary:
    """What a backtest run reports (PRD FR-11): Sharpe, drawdown and benchmark-relative return, plus trade statistics.
    Everything is over the evaluation window; `None` means it could not be computed."""

    rule: str
    start: date
    end: date
    bars: int
    total_return: float
    cagr: float | None
    volatility: float | None
    max_drawdown: float | None
    sharpe: float | None
    time_in_market: float
    trades: int
    stops: int
    win_rate: float | None
    average_trade: float | None
    worst_trade: float | None
    benchmark_total_return: float
    benchmark_cagr: float | None
    benchmark_max_drawdown: float | None
    benchmark_sharpe: float | None
    excess_return: float  # strategy total return minus buy-and-hold total return
    index_total_return: float | None  # the market (NIFTY 50) over the same window, if its series was given


def _try(compute) -> float | None:
    try:
        return compute()
    except InsufficientData:
        return None


def _cagr(first: float, last: float, start: date, end: date) -> float | None:
    years = (end - start).days / 365.25
    return (last / first) ** (1 / years) - 1 if years > 0 and first > 0 and last > 0 else None


def _sharpe(levels: tuple[float, ...], days: tuple[date, ...], riskfree: Series | None) -> float | None:
    returns = returns_from_levels(levels)
    if riskfree is None:
        free = [0.0] * len(returns)
    else:
        try:
            free = riskfree_returns(riskfree, days)
        except InsufficientData:
            return None
    return _try(lambda: stats.sharpe(returns, free))


def summarize(result: BacktestResult, riskfree: Series | None = None, index: Series | None = None) -> Summary:
    """Sharpe is measured against `riskfree` (an accrual index such as the Nifty 1D Rate Index) or against zero if none."""
    initial = result.config.initial_cash
    days, equity, held = result.days, result.equity, result.benchmark
    start, end = days[0], days[-1]
    trades = result.trades
    first_level, last_level = level_asof(index, start) if index else None, level_asof(index, end) if index else None
    return Summary(
        rule=result.rule,
        start=start,
        end=end,
        bars=len(days),
        total_return=equity[-1] / initial - 1,
        cagr=_cagr(initial, equity[-1], start, end),
        volatility=_try(lambda: stats.annualized_volatility(returns_from_levels(equity))),
        max_drawdown=_try(lambda: stats.max_drawdown([initial, *equity])),
        sharpe=_sharpe(equity, days, riskfree),
        time_in_market=sum(result.in_market) / len(result.in_market),
        trades=len(trades),
        stops=sum(1 for trade in trades if trade.reason == "stop"),
        win_rate=sum(1 for t in trades if t.net_return > 0) / len(trades) if trades else None,
        average_trade=sum(t.net_return for t in trades) / len(trades) if trades else None,
        worst_trade=min((t.net_return for t in trades), default=None),
        benchmark_total_return=held[-1] / initial - 1,
        benchmark_cagr=_cagr(initial, held[-1], start, end),
        benchmark_max_drawdown=_try(lambda: stats.max_drawdown([initial, *held])),
        benchmark_sharpe=_sharpe(held, days, riskfree),
        excess_return=equity[-1] / initial - held[-1] / initial,
        index_total_return=last_level / first_level - 1 if first_level and last_level else None,
    )


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_summary(summary: Summary, symbol: str = "", rule_description: str = "") -> str:
    head = f"Backtest {symbol}  rule: {summary.rule}  {summary.start} to {summary.end} ({summary.bars} trading days)"
    lines = [head]
    if rule_description:
        lines.append(f"  {rule_description}")
    lines += [
        "                      strategy    buy and hold",
        f"  total return        {pct(summary.total_return):>9}    {pct(summary.benchmark_total_return):>9}",
        f"  yearly return       {pct(summary.cagr):>9}    {pct(summary.benchmark_cagr):>9}",
        f"  worst drawdown      {pct(summary.max_drawdown):>9}    {pct(summary.benchmark_max_drawdown):>9}",
        f"  Sharpe              {num(summary.sharpe):>9}    {num(summary.benchmark_sharpe):>9}",
        f"  excess over buy and hold: {pct(summary.excess_return)}"
        + (f"   market (NIFTY 50): {pct(summary.index_total_return)}" if summary.index_total_return is not None else ""),
        f"  in the market {pct(summary.time_in_market)} of days; {summary.trades} trades ({summary.stops} stopped out), "
        f"win rate {pct(summary.win_rate)}, average trade {pct(summary.average_trade)}, worst {pct(summary.worst_trade)}",
        "  Cash earns nothing between trades. Past results do not predict future ones.",
    ]
    return "\n".join(lines)
