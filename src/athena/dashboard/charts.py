from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from athena.backtest.engine import BacktestResult
from athena.technicals.candles import Candle
from athena.technicals.indicators import OVERLAY, PANE, REGISTRY, Line, Selected, compute

UP, DOWN = "#2e9e6b", "#d8574b"
OVERLAY_COLORS = ("#e0a030", "#7a5ccf", "#2aa7a1", "#d16ba5", "#5b8def", "#b5651d")
PANE_COLORS = ("#3b7dd8", "#e0a030", "#7a5ccf")
GREY = "#8a8f98"
PRICE_WEIGHT, VOLUME_WEIGHT, PANE_WEIGHT = 3.0, 1.0, 1.4
ROW_HEIGHT = 120


def _trace(line: Line, color: str, days: Sequence) -> go.BaseTraceType:
    if line.style == "bars":
        return go.Bar(x=days, y=list(line.values), name=line.name, marker_color=color, opacity=0.5)
    return go.Scatter(
        x=days, y=list(line.values), name=line.name, mode="markers" if line.style == "dots" else "lines",
        line=dict(color=color, width=1, dash="dot" if line.style == "dot" else "solid"), marker=dict(color=color, size=3),
    )


def build_chart(candles: Sequence[Candle], selection: Sequence[Selected], title: str, show_volume: bool = True) -> go.Figure:
    """One figure with a shared date axis: candles and the chosen overlays on top, volume under them, then one
    sub-chart per chosen pane indicator. Every item of `selection` must be valid (see `indicators.validate`)."""
    if not candles:
        raise ValueError("no candles to chart")
    days = [c.day for c in candles]
    computed = [(item, REGISTRY[item.key], compute(candles, item)) for item in selection]
    panes = [entry for entry in computed if entry[1].placement == PANE]
    weights = [PRICE_WEIGHT] + ([VOLUME_WEIGHT] if show_volume else []) + [PANE_WEIGHT] * len(panes)
    rows = len(weights)
    figure = make_subplots(rows=rows, cols=1, shared_xaxes=True, row_heights=[w / sum(weights) for w in weights], vertical_spacing=0.03)
    figure.add_trace(
        go.Candlestick(
            x=days, open=[c.open for c in candles], high=[c.high for c in candles],
            low=[c.low for c in candles], close=[c.close for c in candles], name="Price",
            increasing_line_color=UP, decreasing_line_color=DOWN,
        ),
        row=1, col=1,
    )

    overlay_index = 0
    for item, spec, lines in computed:
        if spec.placement != OVERLAY:
            continue
        color = GREY if item.key == "bbands" else OVERLAY_COLORS[overlay_index % len(OVERLAY_COLORS)]
        overlay_index += 0 if item.key == "bbands" else 1
        for line in lines:
            figure.add_trace(_trace(line, color, days), row=1, col=1)
    next_row = 2
    if show_volume:
        figure.add_trace(
            go.Bar(x=days, y=[c.volume for c in candles], name="Volume", marker_color=[UP if c.close >= c.open else DOWN for c in candles]),
            row=next_row, col=1,
        )
        next_row += 1
    for item, spec, lines in panes:
        for position, line in enumerate(lines):
            figure.add_trace(_trace(line, PANE_COLORS[position % len(PANE_COLORS)], days), row=next_row, col=1)
        for level in spec.levels:
            figure.add_hline(y=level, row=next_row, col=1, line=dict(color=GREY, width=1, dash="dot"))
        if spec.y_range:
            figure.update_yaxes(range=list(spec.y_range), row=next_row, col=1)
        figure.update_yaxes(title_text=spec.label, title_font=dict(size=10), row=next_row, col=1)
        next_row += 1
    figure.update_layout(
        title=title, xaxis_rangeslider_visible=False, height=260 + ROW_HEIGHT * rows, margin=dict(l=40, r=20, t=60, b=30),
        legend=dict(orientation="h"),
    )
    return figure


def equity_chart(result: BacktestResult, title: str) -> go.Figure:
    """The strategy's equity curve against buy and hold (both start at the same cash), with each entry and exit marked."""
    if not result.days:
        raise ValueError("no days to chart")
    days = list(result.days)
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=days, y=list(result.equity), name="Strategy", mode="lines", line=dict(color="#3b7dd8", width=2)))
    figure.add_trace(go.Scatter(x=days, y=list(result.benchmark), name="Buy and hold", mode="lines", line=dict(color="#8a8f98", width=1.5)))
    level = dict(zip(days, result.equity))
    entries = [(t.entry_day, level.get(t.entry_day)) for t in result.trades if t.entry_day in level]
    exits = [(t.exit_day, level.get(t.exit_day)) for t in result.trades if t.exit_day in level]
    figure.add_trace(go.Scatter(x=[d for d, _ in entries], y=[v for _, v in entries], name="Entry", mode="markers", marker=dict(symbol="triangle-up", color=UP, size=8)))
    figure.add_trace(go.Scatter(x=[d for d, _ in exits], y=[v for _, v in exits], name="Exit", mode="markers", marker=dict(symbol="triangle-down", color=DOWN, size=8)))
    figure.update_layout(title=title, height=320, margin=dict(l=40, r=20, t=50, b=30), legend=dict(orientation="h"), yaxis_title="value of 100 invested")
    return figure
