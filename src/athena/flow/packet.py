"""The flow analyst's input: what the derivatives detectors found over the last days, with the figures behind each event.

Everything here is counted or copied from the detectors (`athena.flow.detectors`); the model that reads it interprets and detects nothing. A stock with no derivatives history has no packet
figures and the analyst abstains."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from athena.flow.detectors import BUILD_UP, LONG_UNWINDING, SHORT_BUILD_UP, SHORT_COVERING, Event, describe

WINDOW_DAYS = 7  # calendar days of events shown
BULLISH = (BUILD_UP, SHORT_COVERING, "put_call_up", "max_pain_up")
BEARISH = (SHORT_BUILD_UP, LONG_UNWINDING, "put_call_down", "max_pain_down")
SOURCE = "NSE derivatives bhavcopy, detectors in code"


def _metric(value: float, unit: str, window: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "window": window, "source": SOURCE}


def build_flow_packet(symbol: str, today: date, events: Sequence[Event], futures_days_held: int, latest_put_call: float | None = None) -> dict[str, Any]:
    """The packet for one stock: counts of recent events by direction, the latest put-call ratio when there is one, and each event as a line."""
    since = today - timedelta(days=WINDOW_DAYS)
    recent = sorted((e for e in events if since <= e.day <= today), key=lambda e: e.day)
    window = f"last {WINDOW_DAYS} days to {today.isoformat()}"
    missing: list[str] = []
    reasons: dict[str, str] = {}
    metrics: dict[str, Any] = {}
    if futures_days_held > 0:
        metrics["futures_days_held"] = _metric(float(futures_days_held), "count", "days of near-month futures held")
        metrics["flow_events"] = _metric(float(len(recent)), "count", window)
        metrics["bullish_flow_events"] = _metric(float(sum(e.kind in BULLISH for e in recent)), "count", window)
        metrics["bearish_flow_events"] = _metric(float(sum(e.kind in BEARISH for e in recent)), "count", window)
    else:
        missing.append("futures_days_held")
        reasons["futures_days_held"] = "no derivatives history is held for this stock (it may not trade in futures and options)"
    if latest_put_call is not None:
        metrics["put_call_ratio"] = _metric(round(latest_put_call, 4), "ratio", f"nearest expiry on {today.isoformat()}")
    else:
        missing.append("put_call_ratio")
        reasons["put_call_ratio"] = "no option open interest is held for this stock"
    return {
        "instrument": symbol,
        "as_of": today.isoformat(),
        "metrics": metrics,
        "missing": missing,
        "missing_reasons": reasons,
        "events": [{"day": e.day.isoformat(), "kind": e.kind, "line": describe(e)} for e in recent],
    }
