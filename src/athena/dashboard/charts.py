from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from athena.backtest.engine import BacktestResult
from athena.technicals.candles import Candle
from athena.technicals.series import IndicatorSeries

UP, DOWN = "#2e9e6b", "#d8574b"
RSI_OVERBOUGHT, RSI_OVERSOLD = 70, 30


def _require(candles: Sequence[Candle], series: IndicatorSeries) -> None:
    if not candles:
        raise ValueError("no candles to chart")
    if len(series.days) != len(candles):
        raise ValueError("indicator series and candles are not aligned")


def price_chart(candles: Sequence[Candle], series: IndicatorSeries, title: str) -> go.Figure:
    """Candles with the 50- and 200-day averages and Bollinger Bands, volume underneath (PRD FR-6)."""
    _require(candles, series)
    days = [c.day for c in candles]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    figure.add_trace(
        go.Candlestick(
            x=days, open=[c.open for c in candles], high=[c.high for c in candles],
            low=[c.low for c in candles], close=[c.close for c in candles], name="Price",
            increasing_line_color=UP, decreasing_line_color=DOWN,
        ),
        row=1, col=1,
    )
    lines = (
        ("SMA 50", series.sma_50, "#e0a030", "solid"),
        ("SMA 200", series.sma_200, "#7a5ccf", "solid"),
        ("Bollinger upper", series.bb_upper, "#8a8f98", "dot"),
        ("Bollinger lower", series.bb_lower, "#8a8f98", "dot"),
    )
    for name, values, color, dash in lines:
        figure.add_trace(go.Scatter(x=days, y=list(values), name=name, mode="lines", line=dict(color=color, width=1, dash=dash)), row=1, col=1)
    figure.add_trace(
        go.Bar(x=days, y=[c.volume for c in candles], name="Volume", marker_color=[UP if c.close >= c.open else DOWN for c in candles]),
        row=2, col=1,
    )
    figure.update_layout(title=title, xaxis_rangeslider_visible=False, height=620, margin=dict(l=40, r=20, t=60, b=30), legend=dict(orientation="h"))
    return figure


def rsi_chart(candles: Sequence[Candle], series: IndicatorSeries, title: str = "RSI 14") -> go.Figure:
    """RSI with the conventional 30 and 70 reference lines."""
    _require(candles, series)
    figure = go.Figure(go.Scatter(x=list(series.days), y=list(series.rsi_14), name="RSI 14", mode="lines", line=dict(color="#3b7dd8", width=1.5)))
    figure.add_hline(y=RSI_OVERBOUGHT, line=dict(color=DOWN, width=1, dash="dot"))
    figure.add_hline(y=RSI_OVERSOLD, line=dict(color=UP, width=1, dash="dot"))
    figure.update_layout(title=title, yaxis=dict(range=[0, 100]), height=240, margin=dict(l=40, r=20, t=50, b=30), showlegend=False)
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
