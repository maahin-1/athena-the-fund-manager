from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import plotly.graph_objects as go

from athena.backtest.adjust import adjustment_note
from athena.backtest.cli import RuleRun
from athena.backtest.rules import RULES
from athena.backtest.summary import num, pct
from athena.dashboard.charts import equity_chart


@dataclass(frozen=True)
class BacktestPanel:
    rule: str
    description: str
    rows: tuple[tuple[str, str, str], ...]  # (measure, strategy, buy and hold)
    facts: tuple[str, ...]
    blinded_identical: bool
    figure: go.Figure


@dataclass(frozen=True)
class BacktestView:
    identifier: str
    panels: tuple[BacktestPanel, ...]
    assumptions: str
    notes: tuple[str, ...] = ()  # warnings about the data, such as prices adjusted for a split


def build_backtest_view(identifier: str, runs: Sequence[RuleRun], assumptions: str) -> BacktestView:
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
                s.rule, RULES[s.rule].description, rows, tuple(facts), run.blinded_identical,
                equity_chart(run.result, f"{identifier}: {s.rule} rule against buy and hold"),
            )
        )
    note = adjustment_note(runs[0].adjustments) if runs else None
    return BacktestView(identifier, tuple(panels), assumptions, (note,) if note else ())
