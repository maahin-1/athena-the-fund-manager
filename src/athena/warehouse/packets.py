"""Precomputing the analysts' packets for the whole market from the warehouse alone. A packet is computed in code from stored prices
and statements, so it is the same every time it is built from the same data; the model is not involved. Each carries the day of its
last price and flags for anything that makes it less reliable (a price break nothing explains, statements older than a month)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from athena.technicals.packet import build_technical_packet
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.trading_calendar import ist_date
from athena.warehouse.history import adjusted_history
from athena.warehouse.screen import screen_row
from athena.warehouse.statements import STALE_OK_DAYS
from athena.warehouse.warehouse import ETF, STOCK, Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
MIN_BARS = 60  # fewer closes than this and no technical packet is built
ACTIVE_WITHIN_DAYS = 10  # a symbol with no bar this close to the last loaded day has stopped trading
BENCHMARK = "Nifty 50"  # the name in the exchange's index file
TECHNICAL, FUNDAMENTALS = "technical", "fundamentals"


def _close(day: date) -> datetime:
    return datetime.combine(day, time(16, 0), tzinfo=IST)


def build_packets(
    warehouse: Warehouse, now: datetime, on_progress: Callable[[str], None] = lambda text: None, symbols: list[str] | None = None
) -> dict[str, Any]:
    """Technical packets for every stock and ETF with enough recent prices; fundamentals packets for every stock whose statements
    are held. Returns what was built and why anything was left out."""
    last = warehouse.last_loaded_day()
    summary: dict[str, Any] = {"technical": 0, "fundamentals": 0, "no_prices": 0, "inactive": 0, "no_statements": 0, "failed": []}
    if last is None:
        return summary
    history = adjusted_history(warehouse)
    index_pe = warehouse.index_valuation(BENCHMARK)
    chosen = set(symbols) if symbols is not None else None
    rows: list[tuple[str, str, date, list[str], dict[str, Any]]] = []
    screen: list[dict[str, Any]] = []
    for entry in warehouse.universe():
        symbol, kind = entry["symbol"], entry["kind"]
        if kind not in (STOCK, ETF) or (chosen is not None and symbol not in chosen):
            continue
        bars = history(symbol)
        if len(bars) < MIN_BARS:
            summary["no_prices"] += 1
            continue
        day = ist_date(bars[-1].timestamp)
        if (last - day).days > ACTIVE_WITHIN_DAYS:
            summary["inactive"] += 1
            continue
        flags = [f"price break with no recorded action: {flag}" for flag in history.price_flags(symbol)]
        try:
            technical = build_technical_packet(symbol, _close(day), bars)
            rows.append((symbol, TECHNICAL, day, flags, technical))
            summary["technical"] += 1
            fundamentals, age, notes = None, None, list(flags)
            if kind == STOCK:
                held = warehouse.statement(symbol)
                if held is None:
                    summary["no_statements"] += 1
                else:
                    payload, fetched = held
                    age = (now - fetched).days
                    if age > STALE_OK_DAYS:
                        notes.append(f"statements fetched {age} days ago")
                    fundamentals = build_fundamentals_packet(symbol, _close(day), payload, bars[-1].close, _close(day), index_pe)
                    rows.append((symbol, FUNDAMENTALS, day, notes, fundamentals))
                    summary["fundamentals"] += 1
            screen.append(screen_row(symbol, kind, entry["name"] or symbol, day, bars, technical, fundamentals, len(notes), age))
        except Exception as exc:  # one stock's odd data must not stop the market's build
            summary["failed"].append(f"{symbol}: {type(exc).__name__}: {str(exc)[:80]}")
            on_progress(f"{symbol}: packet failed ({type(exc).__name__})")
    warehouse.put_packets(rows, now)
    summary["screen"] = warehouse.put_screen(screen, now, replace_all=chosen is None)
    return summary
