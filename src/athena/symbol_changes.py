from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from athena.contracts import AthenaError

URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
TIMEOUT = 30
MAX_HOPS = 5  # a symbol renamed more than this many times in a row is treated as a loop


@dataclass(frozen=True)
class Rename:
    company: str  # the company's name as the exchange lists it
    old: str
    new: str
    day: date


def parse_symbol_changes(text: str) -> dict[str, list[Rename]]:
    """The exchange's rename file (company, old symbol, new symbol, date; no header) as old symbol -> its renames, oldest
    first. A company name may itself hold commas, so a row is read from its right-hand end. Rows that do not parse are
    skipped."""
    renames: dict[str, list[Rename]] = defaultdict(list)
    for line in text.splitlines():
        parts = line.strip().rsplit(",", 3)
        if len(parts) != 4:
            continue
        company, old, new, stamp = (part.strip() for part in parts)
        try:
            day = datetime.strptime(stamp, "%d-%b-%Y").date()
        except ValueError:
            continue  # a header row or a blank date
        if old and new and old != new:
            renames[old.upper()].append(Rename(company, old.upper(), new.upper(), day))
    for rows in renames.values():
        rows.sort(key=lambda rename: rename.day)
    return dict(renames)


def fetch_symbol_changes() -> dict[str, list[Rename]]:
    """NSE's published rename list. Any failure is an `AthenaError`: a lookup that cannot be answered is not a guess."""
    import requests

    from athena.price_integrity.nse_actions import HEADERS

    try:
        response = requests.get(URL, headers=HEADERS, timeout=TIMEOUT)
        if response.status_code != 200:
            raise AthenaError(f"HTTP {response.status_code}")
        renames = parse_symbol_changes(response.text)
    except AthenaError:
        raise
    except Exception as exc:
        raise AthenaError(f"NSE symbol changes unavailable: {exc}") from exc
    if not renames:
        raise AthenaError("NSE symbol changes unavailable: the file had no readable rows")
    return renames


class SymbolChanges:
    """Where a retired ticker went. `follow(symbol, is_current)` walks the exchange's own rename list from an old symbol to
    the one that is listed now, so ZOMATO finds ETERNAL by the record, never by name similarity. The list is loaded once."""

    def __init__(self, fetch: Callable[[], dict[str, list[Rename]]] = fetch_symbol_changes):
        self._fetch = fetch
        self._renames: dict[str, list[Rename]] | None = None

    def follow(self, symbol: str, is_current: Callable[[str], bool], today: date | None = None) -> list[Rename]:
        """The renames from `symbol` to a symbol for which `is_current` is true, oldest first; empty when the symbol was never
        renamed, or its trail does not end at a current symbol (then nothing is claimed)."""
        if self._renames is None:
            self._renames = self._fetch()
        today = today or date.today()
        trail: list[Rename] = []
        here, seen = symbol.upper(), {symbol.upper()}
        for _ in range(MAX_HOPS):
            later = [r for r in self._renames.get(here, ()) if r.day <= today and (not trail or r.day >= trail[-1].day)]
            if not later:
                return []
            step = later[-1]  # a symbol can be reused: the latest rename away from it is the one that applies
            trail.append(step)
            if is_current(step.new):
                return trail
            if step.new in seen:
                return []
            here = step.new
            seen.add(here)
        return []


def describe_trail(trail: list[Rename]) -> str:
    first, last = trail[0], trail[-1]
    hops = " -> ".join([first.old, *(rename.new for rename in trail)])
    return f"{first.old} is a retired ticker: the exchange renamed it ({hops}, {last.company}, on {last.day}); showing {last.new}."
