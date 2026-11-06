"""The number printed on a page against the page's position in the PDF.

A draft offer document cites pages by the number printed on them (`see "Risk Factors" on page 44`) while a PDF viewer, and everything this project stores, counts from the first sheet; the two differ by the
cover and the front matter, and the offset can change inside a document (a section divider, an inserted annexure). The printed number is a line of its own at the top or bottom of the page. It is taken
only where it agrees with the pages around it (the same offset on a neighbouring page), so a lone figure that happens to sit on a line (a year, a count) is not mistaken for a page number, and a page
without a printed number gets one only when the pages on both sides agree on the offset."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

EDGE_LINES = 2  # lines looked at from the top and from the bottom of a page
NEIGHBOURS = 3  # pages either side that may confirm an offset
GAP = 6  # most pages between two confirmed pages that a page without a number may be filled between
LINE = re.compile(r"^(?:page\s+)?(\d{1,4})(?:\s+of\s+\d{1,4})?$", re.IGNORECASE)


def raw_number(text: str) -> int | None:
    """The figure on a line of its own at the top or bottom of the page, or None."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in (*lines[:EDGE_LINES], *lines[-EDGE_LINES:]):
        m = LINE.match(line)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    return None


def printed_numbers(pages: Sequence[tuple[int, str]]) -> dict[int, int]:
    """{PDF page: printed number} for the pages where it is known with confidence: read from the page and confirmed by a neighbour with the same offset, or filled between two confirmed pages that share an offset."""
    raw = {page: n for page, text in pages if (n := raw_number(text)) is not None}
    offset = {page: page - n for page, n in raw.items()}
    confirmed = {page: off for page, off in offset.items() if any(offset.get(q) == off for q in range(page - NEIGHBOURS, page + NEIGHBOURS + 1) if q != page)}
    out = {page: page - off for page, off in confirmed.items()}
    ordered = sorted(confirmed)
    for before, after in zip(ordered, ordered[1:]):
        if 1 < after - before <= GAP and confirmed[before] == confirmed[after]:
            for page in range(before + 1, after):
                out.setdefault(page, page - confirmed[before])
    return out


def pdf_page_of(printed: int, numbers: Mapping[int, int]) -> int | None:
    """The PDF page that carries this printed number, when exactly one does (a number that two parts of a document both print is not guessed)."""
    hits = [page for page, n in numbers.items() if n == printed]
    return hits[0] if len(hits) == 1 else None
