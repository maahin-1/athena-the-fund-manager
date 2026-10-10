from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from athena.news.model import NewsItem

# The exchange's own wording for a merger or scheme that has happened, not one that is only proposed. Precision over recall:
# an employee share-option "scheme" or a proposal must not match, and a completion this misses is simply not reported.
_COMPLETED = re.compile(
    r"effective date of (?:the )?(?:composite )?scheme of (?:amalgamation|arrangement|merger)"
    r"|sanctioning the (?:composite )?scheme of (?:amalgamation|arrangement|merger)"
    r"|allotment of [\d,]+ (?:equity )?(?:shares|securities)? ?pursuant to scheme of arrangement/amalgamation"
    r"|allotment of securities:.{0,200}pursuant to scheme of arrangement/amalgamation",
    re.IGNORECASE,
)
MAX_DATES = 4


@dataclass(frozen=True)
class SchemeFiling:
    day: date
    text: str


def scheme_completions(items: Sequence[NewsItem]) -> list[SchemeFiling]:
    """The exchange filings that say a merger, amalgamation or scheme of arrangement took effect, newest first, one per day."""
    seen: dict[date, SchemeFiling] = {}
    for item in sorted(items, key=lambda i: i.published, reverse=True):
        day = item.published.date()
        if day not in seen and item.source == "nse" and _COMPLETED.search(item.title):
            seen[day] = SchemeFiling(day, item.title)
    return list(seen.values())


def scheme_flag(filings: Sequence[SchemeFiling]) -> str | None:
    """One sentence for the data-quality flags, or None when no filing says a scheme took effect."""
    if not filings:
        return None
    shown = filings[:MAX_DATES]
    days = ", ".join(str(f.day) for f in shown) + (f" and {len(filings) - MAX_DATES} more" if len(filings) > MAX_DATES else "")
    return (
        f"NSE filings show a merger, amalgamation or scheme of arrangement taking effect ({days}); statements, share counts "
        f"and growth rates across those dates include the acquired business and are not organic. Latest filing: {shown[0].text}"
    )
