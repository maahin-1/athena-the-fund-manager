from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from athena.contracts import StaleDataError


@dataclass(frozen=True)
class Limit:
    amount: int
    unit: str  # "minutes" | "business_days" | "days"


# Proposed defaults from TRD section 5; tune from canary data.
DEFAULT_LIMITS: dict[str, Limit | None] = {
    "quote.intraday": Limit(15, "minutes"),
    "option_chain": Limit(15, "minutes"),
    "price.eod": Limit(1, "business_days"),
    "mf.nav": Limit(1, "business_days"),
    "index.tri": Limit(1, "business_days"),
    "mf.ter": Limit(7, "days"),
    "mf.holdings": Limit(45, "days"),
    "equity.fundamentals": Limit(136, "days"),  # one quarter (91) + 45 days
    "bond.price": None,  # illiquid: show the last-trade date instead
}


def age_in_business_days(as_of: datetime, now: datetime) -> int:
    """Weekdays in the half-open interval (as_of date, now date]. Holidays are ignored."""
    day = as_of.date()
    end = now.date()
    count = 0
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def check_fresh(
    dataset: str,
    as_of: datetime,
    now: datetime,
    limits: dict[str, Limit | None] = DEFAULT_LIMITS,
) -> None:
    if as_of.tzinfo is None or now.tzinfo is None:
        raise ValueError("as_of and now must be timezone-aware")
    if dataset not in limits:
        raise ValueError(f"no staleness limit declared for dataset {dataset!r}")
    limit = limits[dataset]
    if limit is None:
        return

    if limit.unit == "minutes":
        age = int((now - as_of).total_seconds() // 60)
        age_text, limit_text = f"{age} minutes", f"{limit.amount} minutes"
    elif limit.unit == "business_days":
        age = age_in_business_days(as_of, now)
        age_text, limit_text = f"{age} business days", f"{limit.amount} business days"
    elif limit.unit == "days":
        age = (now - as_of).days
        age_text, limit_text = f"{age} days", f"{limit.amount} days"
    else:
        raise ValueError(f"unknown limit unit {limit.unit!r}")

    if age > limit.amount:
        raise StaleDataError(f"{dataset} data is {age_text} old, limit is {limit_text}")
