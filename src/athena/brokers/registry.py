"""The brokers Athena is meant to work with, and what is known about each one's API.

Athena speaks one order shape (`athena.brokers.model`) and a broker is an adapter behind it. Paper trading is built. The live adapters are NOT: each one has to be checked against the broker's current
documentation and the owner's own account before it may send an order, and none has been. The order below is the share of NSE active clients (September 2026 reports, which differ in small ways):
Groww about 29%, Zerodha 14.7%, Angel One 14.65%, then Upstox, then Dhan.

`checked` says whether the API facts here were confirmed against the broker's own documentation; every row is False until that is done. Nothing in this file is read to decide an order."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrokerInfo:
    key: str
    name: str
    sdk: str  # the official Python package, as the search for this note found it
    market_data: str  # what the broker's API gives for prices, as far as known
    built: str  # what is built in Athena
    checked: bool = False
    notes: str = ""


BROKERS: tuple[BrokerInfo, ...] = (
    BrokerInfo("paper", "Paper trading", "none", "the terminal's live quotes", "orders, funds, holdings, settle, cancel (built, tested)", True, "the only broker that can be confirmed today"),
    BrokerInfo("groww", "Groww", "growwapi", "live quotes, LTP and history are on a paid plan; orders are in the free trial", "nothing", False, "largest by active clients"),
    BrokerInfo("zerodha", "Zerodha (Kite Connect)", "kiteconnect", "live data and history need a paid Connect plan; the plan for orders alone has to be confirmed", "login helper only (athena.brokers.kite_login)", False, "second by active clients"),
    BrokerInfo("angelone", "Angel One (SmartAPI)", "smartapi-python", "free; the terminal already streams prices through it", "login and the live stream (read only)", False, "third by active clients; the first live adapter to build"),
    BrokerInfo("upstox", "Upstox", "upstox-python-sdk", "to be checked", "nothing", False, "fourth; the SDK names an order API v2 and a v3"),
    BrokerInfo("dhan", "Dhan", "dhanhq", "to be checked", "nothing", False, "fifth, fast growing"),
)


def describe() -> list[str]:
    return [f"{b.name:<24} {'checked' if b.checked else 'NOT checked':<12} built: {b.built}" for b in BROKERS]
