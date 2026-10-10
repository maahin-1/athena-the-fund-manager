from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from athena.backtest.blinding import BLIND_SYMBOL, BLIND_YEARS, blind_bars
from athena.contracts import AthenaError, Bar
from athena.evaluation.outcomes import HORIZONS, Outcome, forward_outcome
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.metrics.series import Series
from athena.pit.nse_results import FilingRef
from athena.pit.statements import statements_as_of
from athena.pit.xbrl import Filing
from athena.pit.yahoo_asof import yahoo_statements_as_of
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

IST = timezone(timedelta(hours=5, minutes=30))
NSE_FILINGS_UNTIL = date(2025, 3, 31)  # the exchange's results list is complete to about here; after it only a few late filers appear
SOURCE_NSE, SOURCE_YAHOO, SOURCE_NONE = "NSE results filings", "Yahoo, filtered by legal filing deadline", "none"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
UNADJUSTED_SOURCE = "jugaad"  # the exchange's own prices, as printed; Yahoo's are already adjusted for every later split
MIN_BARS = 60  # fewer closes than this before the date and no technical packet is built


@dataclass(frozen=True)
class PitCase:
    """One stock on one past date: the packets the analysts would have read then, and what happened afterwards."""

    symbol: str
    as_of: date
    packets: dict[str, dict[str, Any]]  # "quant_technical" and "fundamentals" (read by valuation, moat & quality, earnings)
    outcomes: dict[int, Outcome | None]  # horizon in trading days -> what followed; None while it is not yet known
    statements_source: str
    blinded: bool
    notes: tuple[str, ...] = ()


def shift_dates(value: Any, years: int = BLIND_YEARS) -> Any:
    """Every ISO date inside a packet moved back `years` calendar years (28 keeps weekdays and month lengths aligned)."""
    if isinstance(value, str):
        return _DATE.sub(lambda m: f"{int(m.group()[:4]) - years:04d}{m.group()[4:]}", value)
    if isinstance(value, Mapping):
        return {key: shift_dates(item, years) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [shift_dates(item, years) for item in value]
    return value


def blind_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """A packet with the company's identity removed: the symbol becomes ASSET and every date moves back 28 years. Magnitudes
    stay, so a very large or very famous company can still be recognised from its figures; the packet is only partly blind."""
    out = shift_dates(dict(packet))
    out["instrument"] = BLIND_SYMBOL
    out.pop("company", None)
    return out


def _at_close(day: date) -> datetime:
    return datetime.combine(day, time(16, 0), tzinfo=IST)


class PitCaseBuilder:
    """Builds `PitCase`s from the same sources the live app uses, cut off at the as-of date. Raw bars (as the exchange printed
    them) give the price the filings' per-share figures were reported against; adjusted bars give returns and the outcome."""

    def __init__(
        self,
        raw_bars: Callable[[str], Sequence[Bar]],
        adjusted_bars: Callable[[str], Sequence[Bar]],
        index: Series,
        index_valuation: Mapping[date, Mapping[str, float]],
        filings: Callable[[str], Sequence[FilingRef]],
        load_filing: Callable[[FilingRef], Filing],
        yahoo_payload: Callable[[str], Mapping[str, Any]] | None = None,
        unadjusted_source: str | None = UNADJUSTED_SOURCE,
    ):
        self._unadjusted_source = unadjusted_source  # None: the caller vouches that the raw bars are unadjusted
        self._raw, self._adjusted, self._index, self._valuation = raw_bars, adjusted_bars, index, index_valuation
        self._filings, self._load, self._yahoo = filings, load_filing, yahoo_payload

    def _payload(self, symbol: str, as_of: date) -> tuple[dict[str, Any] | None, str, list[str]]:
        notes: list[str] = []
        yahoo: Mapping[str, Any] | None = None
        if self._yahoo is not None:
            try:
                yahoo = self._yahoo(symbol)
            except AthenaError as exc:
                notes.append(f"Yahoo statements unavailable: {exc}")
        info = {key: yahoo["info"][key] for key in ("sector", "industry") if yahoo and key in yahoo.get("info", {})}
        if as_of <= NSE_FILINGS_UNTIL:
            try:
                payload = statements_as_of(self._filings(symbol), self._load, as_of, info)
            except AthenaError as exc:
                return None, SOURCE_NONE, [*notes, f"exchange filings unavailable: {exc}"]
            if payload["annual"]["income"] or payload["quarterly"]["income"]:
                return payload, SOURCE_NSE, notes
            return None, SOURCE_NONE, [*notes, "the exchange lists no filings for this company before that date"]
        if yahoo is not None:
            return yahoo_statements_as_of(yahoo, as_of), SOURCE_YAHOO, notes
        return None, SOURCE_NONE, notes

    def build(self, symbol: str, as_of: date, blind: bool = False) -> PitCase:
        adjusted = [bar for bar in self._adjusted(symbol) if ist_date(bar.timestamp) <= as_of]
        raw = [bar for bar in self._raw(symbol) if ist_date(bar.timestamp) <= as_of]
        notes: list[str] = []
        packets: dict[str, dict[str, Any]] = {}
        if len(adjusted) >= MIN_BARS:
            if blind:
                shifted = blind_bars(adjusted)
                packets["quant_technical"] = build_technical_packet(BLIND_SYMBOL, shifted[-1].timestamp + timedelta(hours=1), shifted)
            else:
                packets["quant_technical"] = build_technical_packet(symbol, _at_close(as_of), adjusted)
        else:
            notes.append(f"only {len(adjusted)} closes before {as_of}: no technical packet")
        payload, source, payload_notes = self._payload(symbol, as_of)
        notes += payload_notes
        if payload is not None and raw and self._unadjusted_source and any(bar.source != self._unadjusted_source for bar in raw):
            others = sorted({bar.source for bar in raw if bar.source != self._unadjusted_source})
            notes.append(
                f"the price history came from {', '.join(others)}, which is already adjusted for later splits, so the price would not be on "
                "the share count the filings report: no fundamentals packet"
            )
            payload = None
        if payload is not None and raw:
            history = {day: row for day, row in self._valuation.items() if day <= as_of}
            fundamentals = build_fundamentals_packet(symbol, _at_close(as_of), payload, raw[-1].close, _at_close(as_of), history)
            packets["fundamentals"] = blind_packet(fundamentals) if blind else fundamentals
        outcomes = {horizon: forward_outcome(self._adjusted(symbol), self._index, as_of, horizon) for horizon in HORIZONS}
        return PitCase(symbol, as_of, packets, outcomes, source, blind, tuple(notes))


def month_ends(first: date, last: date) -> list[date]:
    """The last calendar day of each month from `first` to `last`: a regular grid of as-of dates."""
    days, day = [], date(first.year, first.month, 1)
    while day <= last:
        nxt = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
        end = nxt - timedelta(days=1)
        if first <= end <= last:
            days.append(end)
        day = nxt
    return days


def last_trading_day(days: Sequence[date], on_or_before: date) -> date | None:
    i = bisect_right(days, on_or_before)
    return days[i - 1] if i else None
