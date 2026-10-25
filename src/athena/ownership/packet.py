"""The ownership analyst's input: who holds the stock and how that changed over the last quarters, worked out in code from the shareholding patterns the company filed.

A shareholding pattern is filed once a quarter and covers one quarter end, so everything here is slow-moving. Only calendar quarter ends are compared (the exchange also lists one-off
patterns on other dates, for a listing or an amalgamation), a quarter counts only once it was filed on or before the as-of day, and a pattern whose groups do not add up to the total shares
is left out rather than repaired. Every share is shares held over total shares, in percentage points; a change is a difference of two such shares. The model that reads the packet interprets
it and does no arithmetic. Not held: pledged shares, so a falling promoter stake cannot be told from a pledge being invoked."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

SOURCE = "NSE shareholding patterns"
STALE_AFTER_DAYS = 200  # a latest quarter older than this is not today's ownership
CHECK_TOLERANCE = 0.005  # promoters plus the public against the total shares
QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}
GROUPS = ("promoters", "fii", "dii", "mutual_funds", "insurance", "retail")
Holdings = Mapping[date, Mapping[str, tuple[float, float | None]]]


@dataclass(frozen=True)
class Quarter:
    end: date
    filed: date | None
    pct: Mapping[str, float]  # percentage points of the total shares, by holder group
    holders: float | None  # shareholders in all


def is_quarter_end(day: date) -> bool:
    return (day.month, day.day) in QUARTER_ENDS


def quarters(holdings: Holdings, filed: Mapping[date, datetime] | None = None, as_of: date | None = None) -> list[Quarter]:
    """The calendar quarter ends that can be used, oldest first: filed by `as_of`, with a total, and with promoters plus the public matching it (when both are given)."""
    out: list[Quarter] = []
    for end in sorted(holdings):
        if not is_quarter_end(end):
            continue
        when = filed.get(end).date() if filed and end in filed else None
        if as_of is not None and (end > as_of or (when is not None and when > as_of)):
            continue
        groups = holdings[end]
        total = groups.get("total", (0.0, None))[0]
        if total <= 0:
            continue
        if "promoters" in groups and "public_total" in groups and abs(groups["promoters"][0] + groups["public_total"][0] - total) > CHECK_TOLERANCE * total:
            continue
        pct = {name: groups[name][0] / total * 100.0 for name in GROUPS if name in groups}
        out.append(Quarter(end, when, pct, groups["total"][1]))
    return out


def _back(rows: Sequence[Quarter], n: int) -> Quarter | None:
    """The quarter `n` quarters before the last one, only when the quarters in between are all there."""
    last = rows[-1]
    for q in rows[:-1]:
        months = (last.end.year - q.end.year) * 12 + last.end.month - q.end.month
        if months == 3 * n:
            return q
    return None


def _metric(value: float, unit: str, window: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "window": window, "source": SOURCE}


NAMES = {"promoters": "promoter", "fii": "fii", "dii": "dii", "mutual_funds": "mutual_fund"}


def build_ownership_packet(symbol: str, as_of: date, holdings: Holdings, filed: Mapping[date, datetime] | None = None) -> dict[str, Any]:
    rows = quarters(holdings, filed, as_of)
    metrics: dict[str, Any] = {}
    missing: list[str] = []
    reasons: dict[str, str] = {}
    wanted = [f"{n}_holding" for n in NAMES.values()] + [f"{n}_change_{k}q" for n in NAMES.values() for k in (1, 4)] + ["shareholders_change_4q"]

    def none(why: str) -> dict[str, Any]:
        return {"instrument": symbol, "as_of": as_of.isoformat(), "metrics": {}, "missing": wanted, "missing_reasons": {name: why for name in wanted}}

    if not rows:
        return none("no shareholding pattern filed by this day is held for this stock")
    last = rows[-1]
    if (as_of - last.end).days > STALE_AFTER_DAYS:
        return none(f"the latest shareholding pattern held is for the quarter ending {last.end.isoformat()}, more than {STALE_AFTER_DAYS} days before this day")
    at = f"quarter ending {last.end.isoformat()}"
    for group, name in NAMES.items():
        if group in last.pct:
            metrics[f"{name}_holding"] = _metric(round(last.pct[group], 4), "percentage points", f"share of all shares, {at}")
        else:
            missing.append(f"{name}_holding")
            reasons[f"{name}_holding"] = f"this stock's {at} pattern has no {group.replace('_', ' ')} group"
        for k in (1, 4):
            before = _back(rows, k)
            key = f"{name}_change_{k}q"
            if group in last.pct and before is not None and group in before.pct:
                metrics[key] = _metric(round(last.pct[group] - before.pct[group], 4), "percentage points", f"{at} less the quarter ending {before.end.isoformat()}")
            else:
                missing.append(key)
                reasons[key] = f"no {k}-quarter comparison: the quarter ending {(before.end.isoformat() if before else 'a year earlier' if k == 4 else 'the quarter before')} is missing or lacks the group"
    year = _back(rows, 4)
    if last.holders and year is not None and year.holders:
        metrics["shareholders_change_4q"] = _metric(round(last.holders / year.holders - 1.0, 4), "fraction", f"number of shareholders, {at} against the quarter ending {year.end.isoformat()}")
    else:
        missing.append("shareholders_change_4q")
        reasons["shareholders_change_4q"] = "the number of shareholders a year earlier is not held"
    return {"instrument": symbol, "as_of": as_of.isoformat(), "metrics": metrics, "missing": missing, "missing_reasons": reasons, "quarters_held": len(rows), "latest_quarter": last.end.isoformat(),
            "latest_filed": last.filed.isoformat() if last.filed else None, "pledges": "not held"}


def window_start(day: date) -> date:
    """The first day a quarter-end pattern can be treated as known in a test (45 days after the quarter end, a safe margin on the 21-day deadline and later revisions)."""
    return day + timedelta(days=45)
