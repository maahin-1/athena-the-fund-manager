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


def _with_calendar(filed: tuple[dict[str, Any], datetime] | None, yahoo: tuple[dict[str, Any], datetime] | None) -> tuple[dict[str, Any], datetime] | None:
    """The statements from the filings when there are some, else the warehouse's own. The filings carry no earnings calendar or dividend rate, so those two come from the
    warehouse's copy when it has them (the surprise figures against Yahoo's estimates say so in their notes)."""
    if filed is None:
        return yahoo
    payload, read = filed
    if yahoo is not None:
        theirs = yahoo[0]
        extra = {"earnings_dates": theirs.get("earnings_dates") or []}
        rate = (theirs.get("info") or {}).get("dividendRate")
        payload = {**payload, **extra, "info": {**payload["info"], **({"dividendRate": rate} if rate else {})}}
    return payload, read


def _quarter_after(day: date) -> date:
    """The last day of the quarter that follows the one ending on `day`."""
    month = day.month + 3
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    first_of_next = date(year + month // 12, month % 12 + 1, 1)
    return first_of_next - timedelta(days=1)


def with_next_results(payload: dict[str, Any], announced: list[date], today: date) -> dict[str, Any]:
    """Adds the next results date to the payload's earnings dates when it holds none after today. An announced board meeting is the date; with none announced it is
    last year's filing day for the same quarter plus a year (marked as an estimate, because companies move by a week or two). Nothing is added when neither exists."""
    held = payload.get("earnings_dates") or []
    if any(date.fromisoformat(row["date"]) > today for row in held):
        return payload
    if announced:
        row = {"date": announced[0].isoformat(), "reported": None, "surprise_pct": None, "source": "announced board meeting"}
    else:
        filed = {date.fromisoformat(k): date.fromisoformat(v) for k, v in (payload.get("filing_dates") or {}).items()}
        if not filed:
            return payload
        following = _quarter_after(max(filed))
        earlier = next((v for k, v in filed.items() if (k.year, k.month) == (following.year - 1, following.month)), None)
        if earlier is None:
            return payload
        expected = earlier + timedelta(days=364)  # the same weekday a year on
        if expected <= today:
            return payload
        row = {"date": expected.isoformat(), "reported": None, "surprise_pct": None, "source": "estimated from last year's filing date"}
    return {**payload, "earnings_dates": [*held, row]}


def build_packets(
    warehouse: Warehouse,
    now: datetime,
    on_progress: Callable[[str], None] = lambda text: None,
    symbols: list[str] | None = None,
    industry: Callable[[str, date], dict[str, Any] | None] | None = None,
    filings: Callable[[str], tuple[dict[str, Any], datetime] | None] | None = None,
) -> dict[str, Any]:
    """Technical packets for every stock and ETF with enough recent prices; fundamentals packets for every stock whose statements
    are held. `industry(symbol, day)` (optional) gives a stock's industry benchmarks for its fundamentals packet. `filings(symbol)` (optional) gives the statements
    from the exchange's filings as `(payload, filed)`; a stock it has none for falls back to the statements the warehouse holds. Returns what was built and why anything was left out."""
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
                held = _with_calendar(filings(symbol) if filings is not None else None, warehouse.statement(symbol))
                if held is None:
                    summary["no_statements"] += 1
                else:
                    payload, fetched = held
                    payload = with_next_results(payload, warehouse.upcoming_results(symbol, ist_date(now)), ist_date(now))
                    age = (now - fetched).days
                    if age > STALE_OK_DAYS:
                        notes.append(f"statements fetched {age} days ago")
                    fundamentals = build_fundamentals_packet(
                        symbol, _close(day), payload, bars[-1].close, _close(day), index_pe, industry=industry(symbol, day) if industry else None
                    )
                    rows.append((symbol, FUNDAMENTALS, day, notes, fundamentals))
                    summary["fundamentals"] += 1
            screen.append(screen_row(symbol, kind, entry["name"] or symbol, day, bars, technical, fundamentals, len(notes), age))
        except Exception as exc:  # one stock's odd data must not stop the market's build
            summary["failed"].append(f"{symbol}: {type(exc).__name__}: {str(exc)[:80]}")
            on_progress(f"{symbol}: packet failed ({type(exc).__name__})")
    warehouse.put_packets(rows, now)
    summary["screen"] = warehouse.put_screen(screen, now, replace_all=chosen is None)
    return summary
