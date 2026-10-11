"""When has a stock changed enough that its stored verdict should be made again?

Three things, all read from what the verdict was made from and what the warehouse holds now: the company reported newer results,
the price moved a lot, or the price data has moved on a long way. Nothing else counts, so a quiet stock keeps its verdict."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

MOVE_LIMIT = 0.12  # the adjusted close moving this much, up or down, since the verdict
STALE_DAYS = 30  # the price data day this far past the verdict's


@dataclass(frozen=True)
class Facts:
    """What a verdict was made from."""

    data_as_of: date
    close: float | None
    annual_period: date | None
    quarter: date | None


def _day(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def facts_from(technical: Mapping[str, Any] | None, fundamentals: Mapping[str, Any] | None) -> Facts | None:
    """Facts from the stored packets (each as `Warehouse.packet` returns it). None when there is no technical packet: no price, no
    verdict."""
    if technical is None:
        return None
    metric = technical["packet"].get("metrics", {}).get("last_close")
    close = metric.get("value") if isinstance(metric, Mapping) else None
    body = fundamentals["packet"] if fundamentals else {}
    return Facts(
        technical["data_as_of"],
        float(close) if isinstance(close, (int, float)) and not isinstance(close, bool) else None,
        _day(body.get("latest_annual_period")),
        _day(body.get("latest_quarter")),
    )


def change_reasons(then: Facts, now: Facts) -> list[str]:
    """Why the verdict made from `then` is out of date given `now`, in plain words; empty when it still stands."""
    reasons: list[str] = []
    if now.annual_period and (then.annual_period is None or now.annual_period > then.annual_period):
        reasons.append(f"new annual results (year to {now.annual_period.isoformat()})")
    if now.quarter and (then.quarter is None or now.quarter > then.quarter):
        reasons.append(f"new quarterly results (quarter to {now.quarter.isoformat()})")
    if then.close and now.close:
        move = now.close / then.close - 1
        if abs(move) >= MOVE_LIMIT:
            reasons.append(f"the price has moved {move:+.0%} since")
    days = (now.data_as_of - then.data_as_of).days
    if days > STALE_DAYS:
        reasons.append(f"{days} days of price data since")
    return reasons
