from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

TITLE_LIMIT = 300
_SUFFIX = re.compile(r"\s+(?:limited|ltd\.?)$", re.IGNORECASE)


@dataclass(frozen=True)
class NewsItem:
    """One headline or exchange announcement. All of it is untrusted text from outside: it is shown and counted, never
    followed as an instruction."""

    title: str
    publisher: str
    published: datetime  # timezone-aware
    link: str
    source: str  # which feed it came from: nse, publisher feed, google news
    snippet: str = ""


def clip(text: str, limit: int = TITLE_LIMIT) -> str:
    """Whitespace collapsed and the text cut to `limit` characters, with an ellipsis when it was cut."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def company_names(name: str) -> tuple[str, ...]:
    """What a headline is likely to call a company: its name without Limited or Ltd, and as listed. A name that is only
    the suffix is left alone."""
    base = _SUFFIX.sub("", name.strip())
    names = [base] if base else []
    if name.strip() not in names:
        names.append(name.strip())
    return tuple(names)


@dataclass(frozen=True)
class SourceResult:
    """What one news source returned, with anything worth telling the reader (a feed that was down, a source that is off)."""

    items: list[NewsItem]
    notes: tuple[str, ...] = ()


GENERIC_FIRST_WORDS = frozenset({"state", "bank", "indian", "india", "national", "general", "the", "first", "new", "bharat", "hindustan"})


def loose_names(names: tuple[str, ...]) -> tuple[str, ...]:
    """The names plus, for a company with a name of several words, its first word when that word is distinctive (headlines
    say `Kotak stock split`, not `Kotak Mahindra Bank Limited stock split`). A generic first word (`State`) is not added."""
    out = list(names)
    words = names[0].split() if names else []
    if len(words) >= 2 and len(words[0]) >= 4 and words[0].lower() not in GENERIC_FIRST_WORDS and words[0] not in out:
        out.append(words[0])
    return tuple(out)


def mentions(item: NewsItem, names: tuple[str, ...]) -> bool:
    """Whether the title or snippet names the company as a whole word (so `Idea` does not match `ideal`)."""
    text = f"{item.title} {item.snippet}"
    return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) for name in names)
