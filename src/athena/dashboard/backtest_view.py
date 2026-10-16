from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import plotly.graph_objects as go

from athena.backtest.cli import RuleRun
from athena.backtest.summary import num, pct
from athena.dashboard.charts import equity_chart


@dataclass(frozen=True)
class Curve:
    """The numbers behind the equity chart, for a page that draws its own: both curves start at 100, one value per day."""

    days: tuple[str, ...]  # ISO dates
    strategy: tuple[float, ...]
    buy_and_hold: tuple[float, ...]
    entries: tuple[str, ...]
    exits: tuple[str, ...]


@dataclass(frozen=True)
class BacktestPanel:
    rule: str
    description: str
    rows: tuple[tuple[str, str, str], ...]  # (measure, strategy, buy and hold)
    facts: tuple[str, ...]
    blinded_identical: bool
    figure: go.Figure
    curve: Curve | None = None


@dataclass(frozen=True)
class BacktestView:
    identifier: str
    panels: tuple[BacktestPanel, ...]
    assumptions: str
    notes: tuple[str, ...] = ()  # what was adjusted in the prices, or could not be explained


def build_backtest_view(identifier: str, runs: Sequence[RuleRun], assumptions: str, notes: Sequence[str] = ()) -> BacktestView:
    """Everything the page shows for a backtest: per rule a comparison table, the facts that do not fit it, and the curve."""
    panels = []
    for run in runs:
        s = run.summary
        rows = (
            ("Total return", pct(s.total_return), pct(s.benchmark_total_return)),
            ("Yearly return", pct(s.cagr), pct(s.benchmark_cagr)),
            ("Worst drawdown", pct(s.max_drawdown), pct(s.benchmark_max_drawdown)),
            ("Sharpe", num(s.sharpe), num(s.benchmark_sharpe)),
        )
        facts = [
            f"{s.start} to {s.end} ({s.bars} trading days); excess over buy and hold {pct(s.excess_return)}"
            + (f"; the market (NIFTY 50) returned {pct(s.index_total_return)}" if s.index_total_return is not None else ""),
            f"in the market {pct(s.time_in_market)} of days; {s.trades} trades ({s.stops} stopped out), win rate "
            f"{pct(s.win_rate)}, average trade {pct(s.average_trade)}, worst trade {pct(s.worst_trade)}",
        ]
        panels.append(
            BacktestPanel(
                s.rule, run.description, rows, tuple(facts), run.blinded_identical,
                equity_chart(run.result, f"{identifier}: {s.rule} rule against buy and hold"),
                Curve(
                    tuple(d.isoformat() for d in run.result.days), tuple(run.result.equity), tuple(run.result.benchmark),
                    tuple(t.entry_day.isoformat() for t in run.result.trades), tuple(t.exit_day.isoformat() for t in run.result.trades),
                ),
            )
        )
    return BacktestView(identifier, tuple(panels), assumptions, tuple(notes))
