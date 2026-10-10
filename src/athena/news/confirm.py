from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.news.model import NewsItem, loose_names, mentions

DAYS_BEFORE, DAYS_AFTER = 14, 7  # announcements and record dates come before the ex-date; the aftermath is reported within days
SHOWN = 2  # headlines kept per event
CONFIRMED, NONE_FOUND = "confirmed", "no coverage found"

_ACTION_WORDS = ("split", "sub-division", "face value", "bonus", "demerger", "demerged", "scheme of arrangement", "record date", "rights", "buyback", "buy back")
KEYWORDS = {
    # Yahoo records a bonus as a split, so both kinds look for both words
    "split": ("split", "sub-division", "subdivision", "face value", "bonus"),
    "bonus": ("bonus", "split", "sub-division", "subdivision", "face value"),
    "demerger": ("demerger", "demerged", "scheme of arrangement", "record date", "spin"),
    "rights": ("rights",),
    "buyback": ("buyback", "buy back"),
    "capital_reduction": ("capital reduction",),
    "scheme": ("scheme", "arrangement", "record date"),
    "other": ("dividend", "special"),
    # a break nothing recorded: any corporate action, or the news that moves a stock by itself
    "unexplained": _ACTION_WORDS + ("merger", "amalgamation", "fraud", "probe", "raid", "results", "crash", "halt", "circuit", "default", "downgrade"),
}


@dataclass(frozen=True)
class EventRef:
    """An event the price checks found: an adjusted split, bonus or demerger, a noted row, or an unexplained break."""

    day: date
    kind: str  # a key of KEYWORDS
    label: str = ""  # the reader's words for it, e.g. "5-for-1 split"


@dataclass(frozen=True)
class Confirmation:
    event: EventRef
    status: str  # CONFIRMED or NONE_FOUND
    headlines: tuple[NewsItem, ...]  # the closest to the event date first, at most SHOWN


def confirm_events(events: Sequence[EventRef], items: Sequence[NewsItem], names: tuple[str, ...] = ()) -> list[Confirmation]:
    """Whether the news reported each event: a headline or filing from 14 days before to 7 days after its date that uses a
    word for that kind of event. With `names`, a headline must also name the company (an exchange filing is for the symbol,
    so it always counts): a generic piece about stock splits is not coverage of this one. This only confirms, it never decides anything: no price, ratio or adjustment comes from news."""
    out = []
    about = loose_names(names)
    for event in events:
        words = KEYWORDS.get(event.kind, KEYWORDS["other"])
        near = [
            item for item in items
            if -DAYS_BEFORE <= (item.published.date() - event.day).days <= DAYS_AFTER
            and any(word in f"{item.title} {item.snippet}".lower() for word in words)
            and (not names or item.source == "nse" or mentions(item, about))
        ]
        near.sort(key=lambda item: (abs((item.published.date() - event.day).days), item.published))
        out.append(Confirmation(event, CONFIRMED if near else NONE_FOUND, tuple(near[:SHOWN])))
    return out


def describe_confirmations(confirmations: Sequence[Confirmation]) -> tuple[str, ...]:
    """One sentence per event for the dashboard."""
    notes = []
    for confirmation in confirmations:
        event = confirmation.event
        name = event.label or event.kind.replace("_", " ")
        if event.kind == "unexplained":
            if confirmation.headlines:
                shown = "; ".join(f"'{h.title}' ({h.publisher}, {h.published.date()})" for h in confirmation.headlines)
                notes.append(f"News around the unexplained break on {event.day}, context only (nothing was adjusted): {shown}.")
            else:
                notes.append(f"No news found around the unexplained break on {event.day}; nothing was adjusted.")
        elif confirmation.headlines:
            shown = "; ".join(f"'{h.title}' ({h.publisher}, {h.published.date()})" for h in confirmation.headlines)
            notes.append(f"{name} on {event.day}: confirmed by {len(confirmation.headlines)} headline(s), e.g. {shown}.")
        else:
            notes.append(f"{name} on {event.day}: {NONE_FOUND} in the sources searched.")
    return tuple(notes)
