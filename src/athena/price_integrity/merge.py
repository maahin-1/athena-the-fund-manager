from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent

SAME_EVENT_DAYS = 3  # calendar days apart that still count as one event: sources can be a weekend apart
RATIO_TOLERANCE = 0.02  # two ratios within 2% are the same ratio


@dataclass(frozen=True)
class Merged:
    events: tuple[PriceEvent, ...]  # what may be applied: Yahoo's events and NSE's readable ones, minus any that conflict
    conflicts: tuple[str, ...]  # events the two sources disagree about: neither is applied
    notes: tuple[str, ...]  # NSE rows that name a split or bonus but give no ratio to apply


def combine_same_day(actions: Sequence[CorporateAction]) -> list[CorporateAction]:
    """Splits and bonuses with the same ex-date act on the price together, so they are one event whose ratio is the product (a
    10-to-2 split with a 1:1 bonus is x10 in shares, a price of a tenth). Seen in the exchange's list for the whole market in
    October 2026: every such pair showed the combined fall, none the fall of either alone. If any row of the day has no
    readable ratio, the day's ratio is unknown and the combined row says so."""
    by_day: dict = {}
    for action in actions:
        if action.kind in ("split", "bonus"):
            by_day.setdefault(action.day, []).append(action)
    out: list[CorporateAction] = []
    for day, group in sorted(by_day.items()):
        if len(group) == 1:
            out.append(group[0])
            continue
        ratios = [a.ratio for a in group]
        ratio = None if any(r is None for r in ratios) else _product(ratios)
        kind = "+".join(sorted({a.kind for a in group}))
        out.append(replace(group[0], kind=kind, ratio=ratio, text=" + ".join(a.text for a in group)))
    return out


def _product(values: Sequence[float | None]) -> float:
    result = 1.0
    for value in values:
        result *= value  # type: ignore[operator]
    return result


def merge_events(yahoo: Sequence[PriceEvent], actions: Sequence[CorporateAction]) -> Merged:
    """Yahoo's split and bonus events checked against NSE's rows for the same stock. They agree: Yahoo's event stands. They
    disagree on the ratio: a conflict, and neither is applied. Only NSE has it and its text gives a ratio: NSE's is used.
    Only NSE has it and its text gives none: a note. Only Yahoo has it: it stands (NSE's list can lag or word it oddly)."""
    kept = list(yahoo)
    added: list[PriceEvent] = []
    conflicts: list[str] = []
    notes: list[str] = []
    for action in sorted(combine_same_day(actions), key=lambda a: a.day):
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
