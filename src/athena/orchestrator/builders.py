from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from athena.clock import utc_now
from athena.contracts import Bar
from athena.fallback import FallbackChain
from athena.resolver import Resolution
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

HISTORY_DAYS = 760  # about 25 months: enough for the monthly trend


def history_fetcher(
    chain: FallbackChain, days: int = HISTORY_DAYS, clock: Callable[[], datetime] = utc_now
) -> Callable[[str], list[Bar]]:
    """Daily bars for a symbol over the last `days`, through an OHLCV fallback chain."""

    def fetch(symbol: str) -> list[Bar]:
        since = ist_date(clock()) - timedelta(days=days)
        return chain.run(symbol, since=since).value

    return fetch


def technical_packet_builder(
    fetch_bars: Callable[[str], Sequence[Bar]], clock: Callable[[], datetime] = utc_now
) -> Callable[[Resolution], dict]:
    """Packet builder for the Quant/Technical specialist: the resolved symbol's bars -> technical packet."""

    def build(resolution: Resolution) -> dict:
        return build_technical_packet(resolution.identifier, clock(), fetch_bars(resolution.identifier))

    return build
