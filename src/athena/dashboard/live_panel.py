"""The live price panel: the exchange's quote, today's minute chart and the order book, refreshing by itself while the page is open.

Display only. The analysts and verdicts read the stored daily prices, never these."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from athena.contracts import AthenaError
from athena.live.quotes import Minute, Quote, market_is_open, staleness

REFRESH_CHOICES = (1, 2, 5, 10, 30, 60)
DEFAULT_REFRESH = 1
AUTO_KEY, EVERY_KEY = "live_auto", "live_every"
NOTE = (
    "Live figures come from the exchange's own quote page (unofficial, can change) and Yahoo's minute bars (can lag). They are shown for "
    "looking only: the specialists and the verdict read the stored daily prices."
)


def minutes_chart(minutes: Sequence[Minute], previous_close: float, ticks: Sequence[tuple[datetime, float]] = ()) -> go.Figure:
    """The minute bars, run on by every exchange answer seen since the last bar, so the line moves with the price."""
    xs, ys = [m.at for m in minutes], [m.close for m in minutes]
    after = [(at, price) for at, price in ticks if not xs or at > xs[-1]]
    xs += [at for at, _ in after]
    ys += [price for _, price in after]
    figure = go.Figure(go.Scatter(x=xs, y=ys, mode="lines", name="price", line={"width": 2}))
    if ys:
        figure.add_trace(go.Scatter(x=[xs[-1]], y=[ys[-1]], mode="markers", marker={"size": 8}, name="last"))
    figure.add_hline(y=previous_close, line_dash="dot", annotation_text="previous close", annotation_position="bottom right")
    figure.update_layout(height=280, margin={"l": 10, "r": 10, "t": 10, "b": 10}, showlegend=False, xaxis_title=None, yaxis_title=None)
    return figure


def depth_rows(quote: Quote) -> list[dict[str, Any]]:
    rows = []
    for i in range(max(len(quote.bids), len(quote.asks))):
        bid = quote.bids[i] if i < len(quote.bids) else None
        ask = quote.asks[i] if i < len(quote.asks) else None
        rows.append({
            "bid qty": f"{bid[1]:,.0f}" if bid else "", "bid": f"{bid[0]:,.2f}" if bid else "",
            "ask": f"{ask[0]:,.2f}" if ask else "", "ask qty": f"{ask[1]:,.0f}" if ask else "",
        })
    return rows


def _money(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def _crore(value: float | None) -> str:
    return "-" if value is None else f"{value / 1e7:,.0f} cr"


def _draw_price(quotes: Any, symbol: str) -> None:
    try:
        quote = quotes.quote(symbol)
    except AthenaError as exc:
        st.warning(f"No live quote: {exc}")
        return
    now = quotes.now()
    first = st.columns(6)
    first[0].metric("Last price", _money(quote.last), f"{quote.change:+,.2f} ({quote.change_pct:+.2f}%)")
    first[1].metric("Open", _money(quote.open))
    first[2].metric("Day high", _money(quote.high))
    first[3].metric("Day low", _money(quote.low))
    first[4].metric("Volume", "-" if quote.volume is None else f"{quote.volume:,.0f}")
    first[5].metric("Traded value", _crore(quote.value))
    state = "market open" if market_is_open(now) else "market closed"
    note = staleness(quote, now)
    updated = f"{quote.updated:%d %b %H:%M:%S} IST" if quote.updated else "time not given"
    status = quotes.stream_status() if hasattr(quotes, "stream_status") else ""
    st.caption(
        f"{quote.source} at {updated}  |  {state}" + (f"  |  {note}" if note else "")
        + (f"  |  {status}" if status and quote.source != "Angel One live stream" else "")
    )


def _draw_chart(quotes: Any, symbol: str) -> None:
    try:
        quote = quotes.quote(symbol)
    except AthenaError:
        return  # the price block above already says why
    chart, book = st.columns([3, 2])
    with chart:
        ticks = quotes.ticks(symbol)
        try:
            minutes = quotes.minutes(symbol)
        except AthenaError as exc:
            minutes = []
            if not ticks:
                st.caption(f"No minute chart: {exc}")
        if minutes or ticks:
            st.plotly_chart(minutes_chart(minutes, quote.previous_close, ticks), width="stretch")
    with book:
        if quote.bids or quote.asks:
            st.dataframe(depth_rows(quote), hide_index=True, width="stretch")
        else:
            st.caption("No order book is shown (the market is closed or the exchange gave none).")
        extras = [f"52-week {_money(quote.year_low)} to {_money(quote.year_high)}"]
        if quote.price_band:
            extras.append(f"price band {quote.price_band}")
        if quote.delivery_pct is not None:
            extras.append(f"delivery {quote.delivery_pct:.0f}% of traded quantity")
        st.caption("  |  ".join(extras))


CHART_EVERY = 2  # the chart and book are heavier to redraw than the price line, so they refresh no faster than this


def _fragment_for(name: str, draw: Any, seconds: int):
    def live(quotes: Any, symbol: str) -> None:
        draw(quotes, symbol)

    live.__qualname__ = f"live_{name}_every_{seconds}s"  # a fragment is told apart by its function, so each kind and interval gets its own
    return st.fragment(run_every=seconds)(live)


_FRAGMENTS: dict[tuple[str, int], Any] = {}


def _run(name: str, draw: Any, seconds: int, quotes: Any, symbol: str) -> None:
    if (name, seconds) not in _FRAGMENTS:
        _FRAGMENTS[(name, seconds)] = _fragment_for(name, draw, seconds)
    _FRAGMENTS[(name, seconds)](quotes, symbol)


def render_live(quotes: Any, symbol: str) -> None:
    """The panel for one symbol with its refresh controls. A changed control reruns the page; each refresh reruns only the panel."""
    st.markdown("**Live price**")
    left, right = st.columns([1, 3])
    auto = left.checkbox("Auto-refresh", value=True, key=AUTO_KEY)
    seconds = right.selectbox(
        "Every (seconds)", REFRESH_CHOICES, index=REFRESH_CHOICES.index(DEFAULT_REFRESH), key=EVERY_KEY, disabled=not auto, label_visibility="collapsed"
    )
    if auto:
        _run("price", _draw_price, seconds, quotes, symbol)
        _run("chart", _draw_chart, max(seconds, CHART_EVERY), quotes, symbol)
    else:
        _draw_price(quotes, symbol)
        _draw_chart(quotes, symbol)
        st.button("Refresh now", key="live_refresh_now")
    st.caption(NOTE)
