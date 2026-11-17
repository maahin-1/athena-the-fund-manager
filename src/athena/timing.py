"""One timing vocabulary for evidence, decisions and execution.

Four moments are kept apart everywhere they matter, because mixing them is how a result ends up measured on information or prices that did not exist yet:

    reporting period      what the figures describe (a quarter end)            -- never when they were known
    publication instant   when the source first made them public               -- may be a date only, or unknown
    observation instant   when this system first saw them (`fetched_at`, `built_at`)
    decision / execution  when a verdict was completed, and the first session it could be traded

Rules written here, in code, and named so a result can say which it used:

* `ENTRY_POLICY` (`next_open_after_decision_v1`): a decision completed on exchange-local date D is entered at the OPEN of the first session strictly after D. It does not claim a fill at the
  close of the data date, nor at the close of D. Another policy needs another name.
* Availability. A case date means "what could be known by the end of that day, India time". A publication given as a date only is treated as available at the END of that date (the latest
  possible moment, so the rule is conservative on the evidence's side); a publication given as an instant is compared as an instant. In strict historical mode an unknown publication is not
  available; in a current case it is admitted and labelled, because the store holding it now shows it is public now.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from athena.trading_calendar import IST, ist_date

ENTRY_POLICY = "next_open_after_decision_v1"
LEGACY_ASSOCIATION = "data_date_close_association_v0"  # the old statistic: return from the close of the data date; a historical association, not an executable result
STRICT_POLICY = "strict_end_of_day_v1"
LIVE_POLICY = "live_end_of_day_v1"
POLICY_NOTE = "a case date means the end of that day in India; a publication given only as a date counts as available at the end of that date; an unknown publication is not available in a strict historical case"
END_OF_DAY = time(23, 59, 59, 999999)


def decision_date(moment: datetime) -> date:
    """The exchange-local (IST) date of a timezone-aware moment: the date a decision completed on, which is not always the UTC date."""
    return ist_date(moment)


def next_session(after: date, sessions: Sequence[date]) -> date | None:
    """The first session date strictly after `after`, from the exchange's session dates (not a calendar guess), or None when none is held yet."""
    return next((day for day in sorted(sessions) if day > after), None)


@dataclass(frozen=True)
class SessionCalendar:
    """The exchange's session dates, established independently of any price download.

    A session is a weekday that is not a recorded holiday, from the start of time up to `through`, the last day the store has examined. Beyond `through` nothing is known, so nothing is assumed:
    a window that needs a later session is not established (pending), it is never shortened or moved. A price row that is missing on a session does not remove the session; the price lookup
    reports it as a gap. A weekday that has no rows and is not recorded as a holiday therefore stays a session (with a gap) rather than quietly vanishing."""

    holidays: frozenset[date]
    through: date

    def is_session(self, day: date) -> bool:
        return day <= self.through and day.weekday() < 5 and day not in self.holidays

    def first_after(self, day: date) -> date | None:
        """The first established session strictly after `day`, or None when the calendar has not reached one."""
        sessions = self.sessions_from(day + timedelta(days=1), 1)
        return sessions[0] if sessions else None

    def sessions_from(self, start: date, count: int) -> list[date] | None:
        """`count` consecutive established sessions on or after `start`, or None when fewer than `count` are established."""
        out: list[date] = []
        day = start
        while len(out) < count and day <= self.through:
            if self.is_session(day):
                out.append(day)
            day += timedelta(days=1)
        return out if len(out) == count else None


def is_historical(as_of: date, now: datetime) -> bool:
    """Whether a case dated `as_of` is in the past relative to the exchange's today at `now`."""
    return as_of < ist_date(now)


@dataclass(frozen=True)
class Admission:
    ok: bool
    reason: str
    precision: str  # "instant", "date" or "unknown"


def admit(published: date | datetime | None, as_of: date, *, strict: bool) -> Admission:
    """Whether evidence published at `published` may be used by a case dated `as_of`. A publication after the case date is never admitted, in either mode."""
    cutoff = datetime.combine(as_of, END_OF_DAY, tzinfo=IST)
    if published is None:
        if strict:
            return Admission(False, "its publication date is unknown, and a strict historical case admits only evidence demonstrably available by the case date", "unknown")
        return Admission(True, "publication date unknown; admitted because this store holds it now, and labelled", "unknown")
    if isinstance(published, datetime):
        if published.tzinfo is None:
            raise ValueError("a publication instant must be timezone-aware; give a date if only the date is known")
        if published > cutoff:
            return Admission(False, f"published {published.astimezone(IST).isoformat(timespec='minutes')}, after the case date {as_of.isoformat()}", "instant")
        return Admission(True, "published by the case date", "instant")
    available_at = datetime.combine(published, END_OF_DAY, tzinfo=IST)  # a date-only publication: the latest moment it could have appeared
    if available_at > cutoff:
        return Admission(False, f"published {published.isoformat()}, after the case date {as_of.isoformat()}", "date")
    return Admission(True, "published by the case date", "date")
