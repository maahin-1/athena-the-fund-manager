"""Detectors for what derivatives positioning says, written in code and stamped with the day they fire.

The flow analyst (a model) is given the events these functions find, with the figures behind each; it interprets them and detects nothing itself. A detector compares a day with the stock's own
recent history (z-scores over `LOOKBACK` days), so a threshold means the same for a quiet stock and a busy one. Inputs are plain rows (one near-month futures row per day, option rows by strike),
so the same functions run on the exchange's derivatives bhavcopy once it is loaded.

Four readings of futures, by what price and open interest did together:
  long build-up     price up, open interest up      new buyers
  short build-up    price down, open interest up    new sellers
  short covering    price up, open interest down    sellers leaving
  long unwinding    price down, open interest down  buyers leaving
A reading fires only when both moves are large for this stock (`PRICE_Z`, `OI_Z`). Also: a shift in the put-call open-interest ratio, a drift of the max-pain strike, and a spike in open interest."""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

LOOKBACK = 60  # days of the stock's own history a move is compared with
MIN_HISTORY = 30
PRICE_Z = 1.0
OI_Z = 2.0
SPIKE_Z = 3.5
PCR_Z = 2.0
MAX_PAIN_DRIFT = 0.03  # of the price, over `MAX_PAIN_DAYS` days
MAX_PAIN_DAYS = 5
BUILD_UP, SHORT_BUILD_UP, SHORT_COVERING, LONG_UNWINDING = "long_build_up", "short_build_up", "short_covering", "long_unwinding"
READING = {(True, True): BUILD_UP, (False, True): SHORT_BUILD_UP, (True, False): SHORT_COVERING, (False, False): LONG_UNWINDING}


@dataclass(frozen=True)
class FutDay:
    """One day of a stock's near-month futures."""

    day: date
    close: float
    oi: float
    volume: float = 0.0


@dataclass(frozen=True)
class OptRow:
    """One option contract on one day: kind is "CE" or "PE"."""

    day: date
    expiry: date
    strike: float
    kind: str
    oi: float


@dataclass(frozen=True)
class Event:
    symbol: str
    day: date
    kind: str
    facts: Mapping[str, float] = field(default_factory=dict)  # the figures behind it, computed here


def _changes(values: Sequence[float]) -> list[float]:
    return [b / a - 1.0 for a, b in zip(values, values[1:]) if a > 0]


def _z(value: float, history: Sequence[float]) -> float | None:
    if len(history) < MIN_HISTORY:
        return None
    sd = statistics.pstdev(history)
    return (value - statistics.fmean(history)) / sd if sd > 0 else None


def futures_events(symbol: str, rows: Sequence[FutDay]) -> list[Event]:
    """The futures events on the latest day only: a reading of price and open interest together, and an open-interest spike. `rows` are in date order, at least `MIN_HISTORY` + 2 days."""
    if len(rows) < MIN_HISTORY + 2:
        return []
    window = rows[-(LOOKBACK + 2) :]
    price_moves = _changes([r.close for r in window])
    oi_moves = _changes([r.oi for r in window])
    if len(price_moves) < MIN_HISTORY or len(oi_moves) < MIN_HISTORY:
        return []
    price_now, oi_now = price_moves[-1], oi_moves[-1]
    price_z, oi_z = _z(price_now, price_moves[:-1]), _z(oi_now, oi_moves[:-1])
    day, out = rows[-1].day, []
    if price_z is not None and oi_z is not None and abs(price_z) >= PRICE_Z and abs(oi_z) >= OI_Z:
        out.append(Event(symbol, day, READING[(price_now > 0, oi_now > 0)], {"price_change": price_now, "price_z": price_z, "oi_change": oi_now, "oi_z": oi_z, "oi": rows[-1].oi}))
    if oi_z is not None and oi_z >= SPIKE_Z:
        out.append(Event(symbol, day, "oi_spike", {"oi_change": oi_now, "oi_z": oi_z, "oi": rows[-1].oi}))
    return out


def put_call_ratio(options: Sequence[OptRow], day: date, expiry: date | None = None, by_day: Mapping[date, Sequence[OptRow]] | None = None) -> float | None:
    """Put open interest over call open interest on `day` for one expiry (the nearest on the day when none is given); None when there are no calls. `by_day` (the rows grouped by day)
    saves a scan of every row for each day when many days are asked."""
    rows = list(by_day.get(day, ())) if by_day is not None else [o for o in options if o.day == day]
    if not rows:
        return None
    if expiry is None:
        live = [o.expiry for o in rows if o.expiry >= day]
        if not live:
            return None
        expiry = min(live)
    puts = sum(o.oi for o in rows if o.expiry == expiry and o.kind == "PE")
    calls = sum(o.oi for o in rows if o.expiry == expiry and o.kind == "CE")
    return puts / calls if calls > 0 else None


def max_pain(options: Sequence[OptRow], day: date, expiry: date) -> float | None:
    """The strike at which the writers of the day's open contracts for `expiry` would pay out least if the stock closed there on expiry."""
    rows = [o for o in options if o.day == day and o.expiry == expiry and o.oi > 0]
    strikes = sorted({o.strike for o in rows})
    if not strikes:
        return None

    def payout(at: float) -> float:
        return sum(o.oi * (max(at - o.strike, 0.0) if o.kind == "CE" else max(o.strike - at, 0.0)) for o in rows)

    return min(strikes, key=lambda s: (payout(s), s))


def options_events(symbol: str, options: Sequence[OptRow], days: Sequence[date], price: float) -> list[Event]:
    """The options events on the latest of `days` (in date order): a put-call ratio far from its own recent range, and a max-pain strike that has moved by `MAX_PAIN_DRIFT` of the price
    over `MAX_PAIN_DAYS` days for the same expiry."""
    if len(days) < MIN_HISTORY + 1 or price <= 0:
        return []
    today = days[-1]
    grouped: dict[date, list[OptRow]] = {}
    for o in options:
        grouped.setdefault(o.day, []).append(o)
    ratios = [r for d in days[-(LOOKBACK + 1) : -1] if (r := put_call_ratio(options, d, by_day=grouped)) is not None]
    now = put_call_ratio(options, today, by_day=grouped)
    out: list[Event] = []
    z = _z(now, ratios) if now is not None else None
    if z is not None and abs(z) >= PCR_Z:
        out.append(Event(symbol, today, "put_call_up" if z > 0 else "put_call_down", {"put_call_ratio": now, "z": z, "usual": statistics.fmean(ratios)}))
    nearest = [o.expiry for o in options if o.day == today and o.expiry >= today]
    if nearest and len(days) > MAX_PAIN_DAYS:
        expiry, before = min(nearest), days[-1 - MAX_PAIN_DAYS]
        a, b = max_pain(options, before, expiry), max_pain(options, today, expiry)
        if a is not None and b is not None and abs(b - a) / price >= MAX_PAIN_DRIFT:
            out.append(Event(symbol, today, "max_pain_up" if b > a else "max_pain_down", {"max_pain": b, "max_pain_before": a, "drift_of_price": (b - a) / price}))
    return out


def describe(event: Event) -> str:
    """One plain line for an event, with its figures."""
    f: dict[str, Any] = dict(event.facts)
    if event.kind in READING.values():
        return f"{event.day}: {event.kind.replace('_', ' ')}: price {f['price_change']:+.1%} (z {f['price_z']:+.1f}) with open interest {f['oi_change']:+.1%} (z {f['oi_z']:+.1f})"
    if event.kind == "oi_spike":
        return f"{event.day}: open interest jumped {f['oi_change']:+.1%} (z {f['oi_z']:+.1f})"
    if event.kind.startswith("put_call"):
        return f"{event.day}: put-call open-interest ratio {f['put_call_ratio']:.2f} against a usual {f['usual']:.2f} (z {f['z']:+.1f})"
    return f"{event.day}: max-pain strike {f['max_pain']:g} against {f['max_pain_before']:g} {MAX_PAIN_DAYS} days earlier ({f['drift_of_price']:+.1%} of the price)"
