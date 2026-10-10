from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_BONUS = re.compile(r"bonus\s*(\d+)\s*:\s*(\d+)")
_FROM = re.compile(r"from\s*(?:rs|re)\.?\s*([\d.]+)")
_TO = re.compile(r"to\s*(?:rs|re)\.?\s*([\d.]+)")
_RIGHTS = re.compile(r"rights\s*(\d+)\s*:\s*(\d+)(?:\s*@\s*premium(?:\s*of)?\s*(?:rs|re)\.?\s*([\d.]+))?")


@dataclass(frozen=True)
class CorporateAction:
    """One row of an exchange's corporate-action list, read as far as its text allows. `ratio` is what the text states
    and nothing more: shares after per share before for a bonus or split, new shares per held for rights, None when the
    text carries none (a demerger never does)."""

    day: date  # the ex-date, an IST trading date
    kind: str  # bonus | split | demerger | rights | buyback | capital_reduction | scheme | other
    ratio: float | None
    text: str
    premium: float | None = None  # the rights issue premium in rupees, when stated
    face_value: float | None = None  # the share's face value in rupees as the exchange lists it; with the premium it gives a rights issue price
    source: str = "nse"


def _number(match: re.Match | None) -> float | None:
    try:
        return float(match.group(1).rstrip(".")) if match else None
    except ValueError:
        return None


def parse_subject(day: date, subject: str) -> CorporateAction | None:
    """The action behind an exchange's `subject` text, or None for an ordinary dividend or meeting (neither changes the
    price series we adjust). Text it cannot read is kept as kind `other`, never guessed at."""
    text = subject.strip()
    low = text.lower()
    if "demerger" in low:
        return CorporateAction(day, "demerger", None, text)
    if "scheme" in low:
        return CorporateAction(day, "scheme", None, text)
    if "bonus" in low:
        bonus = _BONUS.search(low)
        ratio = (int(bonus.group(1)) + int(bonus.group(2))) / int(bonus.group(2)) if bonus and int(bonus.group(2)) else None
        return CorporateAction(day, "bonus", ratio, text)
    if "split" in low or "sub-division" in low or "subdivision" in low:
        old, new = _number(_FROM.search(low)), _number(_TO.search(low))
        return CorporateAction(day, "split", old / new if old and new else None, text)
    if "rights" in low:
        rights = _RIGHTS.search(low)
        if rights and int(rights.group(2)):
            premium = float(rights.group(3)) if rights.group(3) else None
            return CorporateAction(day, "rights", int(rights.group(1)) / int(rights.group(2)), text, premium)
        return CorporateAction(day, "rights", None, text)
    if re.search(r"buy\s*back", low):
        return CorporateAction(day, "buyback", None, text)
    if "capital reduction" in low:
        return CorporateAction(day, "capital_reduction", None, text)
    if "dividend" in low and "special" not in low:
        return None
    if low in ("annual general meeting", "agm") or low.startswith("annual general meeting"):
        return None
    return CorporateAction(day, "other", None, text)
