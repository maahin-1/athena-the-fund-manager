"""Live broker adapters: one file per broker behind the shared `LiveBroker` base (`base.py`).

    build("zerodha")   a broker object, read from the settings file / environment

The five are the largest Indian brokers by NSE active clients (September 2026: Groww, Zerodha, Angel One, ICICI Securities, Upstox). A person can add another by writing one more subclass and one
line in `ADAPTERS`; the desk, the limits, the audit log and the confirm step do not change."""
from __future__ import annotations

from importlib import import_module

from athena.brokers.live.base import LiveBroker
from athena.contracts import AthenaError

ADAPTERS: dict[str, tuple[str, str]] = {
    "groww": ("athena.brokers.live.groww", "GrowwBroker"),
    "zerodha": ("athena.brokers.live.zerodha", "ZerodhaBroker"),
    "angelone": ("athena.brokers.live.angelone", "AngelOneBroker"),
    "icici": ("athena.brokers.live.icici", "IciciBroker"),
    "upstox": ("athena.brokers.live.upstox", "UpstoxBroker"),
}


def build(key: str, **kwargs) -> LiveBroker:
    """The adapter for `key` (as in `ADAPTERS`). Its required settings come from the settings file; a missing one is an `AthenaError` that names it."""
    if key not in ADAPTERS:
        raise AthenaError(f"no adapter for {key!r}; the known ones are: {', '.join(sorted(ADAPTERS))}")
    module, cls = ADAPTERS[key]
    return getattr(import_module(module), cls)(**kwargs)
