"""The brokers Athena works with, and how far each one has been verified.

Athena speaks one order shape (`athena.brokers.model`) and a broker is an adapter behind it (`athena.brokers.live`). The five largest by NSE active clients (September 2026 reports, which differ in small
ways: Groww about 29%, Zerodha 14.7%, Angel One 14.65%, ICICI Securities about 21 lakh clients, Upstox about 19 lakh) have adapters, written against each broker's published API documentation.

  docs_checked   the endpoints, headers and fields were read from the broker's published documentation or official SDK (see docs/brokers/<key>.md for what could not be confirmed)
  live_tested    an order has been sent through the adapter to a real account by its owner; NOBODY has done this yet for any broker, and until someone has, treat every adapter as unproven with real money

Nothing in this file is read to decide an order."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrokerInfo:
    key: str
    name: str
    settings: tuple[str, ...]  # the settings-file names the adapter needs
    market_data: str
    built: str
    docs_checked: bool = False
    live_tested: bool = False
    notes: str = ""


BROKERS: tuple[BrokerInfo, ...] = (
    BrokerInfo("paper", "Paper trading", (), "the terminal's live quotes", "orders, funds, holdings, settle, cancel (built, tested)", True, True, "the default; no account needed"),
    BrokerInfo("groww", "Groww", ("GROWW_ACCESS_TOKEN",), "a paid plan for live quotes; orders need the Trading API subscription", "adapter (athena.brokers.live.groww)", True, False, "largest by active clients; the token expires every day at 6 AM"),
    BrokerInfo("zerodha", "Zerodha (Kite Connect)", ("KITE_API_KEY", "KITE_ACCESS_TOKEN"), "live data and history need a paid Connect plan", "adapter (athena.brokers.live.zerodha) and the daily login helper (athena.brokers.kite_login)", True, False, "second by active clients"),
    BrokerInfo("angelone", "Angel One (SmartAPI)", ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"), "free; the terminal already streams prices through it", "adapter (athena.brokers.live.angelone), login and the live stream", True, False, "third by active clients; the full status list is not published, unknown statuses are refused"),
    BrokerInfo("icici", "ICICI Direct (Breeze)", ("ICICI_APP_KEY", "ICICI_SECRET_KEY", "ICICI_SESSION_TOKEN"), "to be checked", "adapter (athena.brokers.live.icici)", True, False, "orders must come from the registered static IP"),
    BrokerInfo("upstox", "Upstox", ("UPSTOX_ACCESS_TOKEN",), "to be checked", "adapter (athena.brokers.live.upstox)", True, False, "places and cancels through API v3; reads use v2"),
    BrokerInfo("dhan", "Dhan", ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"), "to be checked", "nothing", False, False, "next in line; add an adapter by writing one subclass (docs/brokers/README.md)"),
)


def describe() -> list[str]:
    def state(b: BrokerInfo) -> str:
        return "live-tested" if b.live_tested and b.key != "paper" else "docs-checked" if b.docs_checked else "not built"

    return [f"{b.name:<24} {state(b):<13} built: {b.built}" for b in BROKERS]
