from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent

SAME_EVENT_DAYS = 3  # calendar days apart that still count as one event: sources can be a weekend apart
RATIO_TOLERANCE = 0.02  # two ratios within 2% are the same ratio


@dataclass(frozen=True)
class Merged:
    events: tuple[PriceEvent, ...]  # what may be applied: Yahoo's events and NSE's readable ones, minus any that conflict
    conflicts: tuple[str, ...]  # events the two sources disagree about: neither is applied
    notes: tuple[str, ...]  # NSE rows that name a split or bonus but give no ratio to apply


def merge_events(yahoo: Sequence[PriceEvent], actions: Sequence[CorporateAction]) -> Merged:
    """Yahoo's split and bonus events checked against NSE's rows for the same stock. They agree: Yahoo's event stands. They
    disagree on the ratio: a conflict, and neither is applied. Only NSE has it and its text gives a ratio: NSE's is used.
    Only NSE has it and its text gives none: a note. Only Yahoo has it: it stands (NSE's list can lag or word it oddly)."""
    kept = list(yahoo)
    added: list[PriceEvent] = []
    conflicts: list[str] = []
    notes: list[str] = []
    for action in sorted((a for a in actions if a.kind in ("split", "bonus")), key=lambda a: a.day):
        match = next((event for event in kept if abs((event.day - action.day).days) <= SAME_EVENT_DAYS), None)
        if match is not None:
            if action.ratio is not None and abs(action.ratio / match.ratio - 1) > RATIO_TOLERANCE:
                kept.remove(match)
                conflicts.append(
                    f"NSE and Yahoo disagree about the {action.day} {action.kind}: NSE says x{action.ratio:.4g} ({action.text!r}), "
                    f"Yahoo x{match.ratio:.4g}; neither was applied."
                )
            continue
        if action.ratio is None:
            notes.append(f"NSE records a {action.kind} on {action.day} ({action.text!r}) with no readable ratio and Yahoo has none: not adjusted.")
        elif not any(abs((event.day - action.day).days) <= SAME_EVENT_DAYS for event in added):
            added.append(PriceEvent(action.day, action.ratio, "nse", action.kind))
    return Merged(tuple(sorted(kept + added, key=lambda event: event.day)), tuple(conflicts), tuple(notes))
