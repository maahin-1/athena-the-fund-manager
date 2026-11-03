"""The numbers of an offer document, taken by program as they print, never by a model.

A draft offer document prints its offer structure in a fixed table ("The Offer" or "The Issue"): the total, the fresh issue, the offer for sale, the shares outstanding before the offer, the pre-IPO
placement the company may make. In a DRHP the price is not set, so most share counts print as `[●]`; those are reported as blank, not zero. Amounts are printed in lakhs, millions or crores; they are
converted to crores here, once, and the printed text is kept beside the figure so it can be checked against the page.

Cross-checks run in code: fresh issue plus offer for sale should equal the total; the amounts shown against the objects should not exceed the fresh issue. A check that fails is reported, not hidden."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from athena.documents.sections import Section

BLANK = "[●]"
_CRORE_PER = {"crore": 1.0, "crores": 1.0, "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01, "million": 0.1, "millions": 0.1, "billion": 100.0}
_UNIT = r"(?:crores?|lakhs?|lacs?|millions?|billion)"
_NUM = r"[\d,]+(?:\.\d+)?"
_ROW = re.compile(
    r"(?<!Net )(?P<label>Offer for Sale|Fresh Issue|Issue of Equity Shares|Offer|Issue)(?:\s*\(\d+\))*\s+(?:Up to|Upto)\s+(?P<shares>\[●\]|[\d,]+)\s+Equity Shares\b.{0,160}?"
    rf"aggregating\s+(?:up\s+)?to\s+(?P<printed>₹\s*(?P<amount>\[●\]|{_NUM})\s*(?P<unit>{_UNIT})?)",
    re.IGNORECASE | re.DOTALL,
)
_PRE_OFFER = re.compile(r"Equity Shares outstanding prior to the (?:Offer|Issue)\b[^0-9\[]{0,120}?(?P<shares>[\d,]{4,})\s+Equity Shares", re.IGNORECASE | re.DOTALL)
_PRE_IPO = re.compile(rf"Pre\s*-?\s*IPO Placement[^.₹]{{0,140}}?aggregating up to\s+₹\s*(?P<amount>{_NUM})\s*(?P<unit>{_UNIT})", re.IGNORECASE)
_FACE_VALUE = re.compile(r"face value of\s+(?:₹|Rs\.?)\s*(?P<value>\d+(?:\.\d+)?)\s+each", re.IGNORECASE)
_DATE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
_DRAFT_DATE = re.compile(rf"Dated:?\s+(?P<d>{_DATE})")
_BOARD = re.compile(rf"(?:Board[^.]{{0,80}}?resolution dated|resolution of (?:our )?Board[^.]{{0,40}}?dated)\s+(?P<d>{_DATE})", re.IGNORECASE)
_FIRM = re.compile(r"^[A-Z][A-Za-z&.,’' -]{4,80}(?:Limited|Private Limited|LLP|Ltd\.?)$")
_OBJECTS_HEADING = re.compile(r"Requirement of funds and utili[sz]ation of Net Proceeds", re.IGNORECASE)
_AMOUNT_END = re.compile(rf"(?P<amount>{_NUM}|\[●\])\s*$")
_TABLE_UNIT = re.compile(rf"in\s+₹\s*(?P<unit>{_UNIT})", re.IGNORECASE)
_FOOTNOTES = re.compile(r"(?:\s*\(\d+\))+")
_TABLE_HEADER = re.compile(r"Particulars|Sr\.\s*No|Estimated [Aa]mount|^\(in\s+₹")
_LEADING_NUMBER = re.compile(r"^\s*\d+\.\s*")
MAX_OBJECT_LINES = 40


@dataclass
class Amount:
    """One printed amount: `crore` is None when the page prints `[●]` (not yet set)."""

    printed: str
    crore: float | None
    page: int


@dataclass
class OfferFigures:
    draft_date: str | None = None
    face_value: float | None = None
    offer_total: Amount | None = None
    fresh_issue: Amount | None = None
    offer_for_sale: Amount | None = None
    pre_offer_shares: int | None = None
    pre_ipo_placement: Amount | None = None
    board_resolution: str | None = None
    managers: list[str] = field(default_factory=list)
    exchanges: list[str] = field(default_factory=list)
    objects: list[dict[str, Any]] = field(default_factory=list)
    objects_unit_crore: float | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    # worked out in code from the figures above
    offer_for_sale_share: float | None = None
    objects_total_crore: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def to_crore(amount: str, unit: str | None) -> float | None:
    """`amount` as printed ('44,000.00', '1,12,800') in `unit` converted to crores; None for a blank or an unknown unit."""
    if not unit or amount == BLANK:
        return None
    try:
        return float(amount.replace(",", "")) * _CRORE_PER[unit.lower()]
    except (ValueError, KeyError):
        return None


def _pages_of(sections: Sequence[Section], keys: Sequence[str], count: int) -> list[int]:
    return [p for s in sections if s.key in keys for p in range(s.start, min(s.end, count) + 1)]


def _rows(text: str, page: int) -> dict[str, Amount]:
    out: dict[str, Amount] = {}
    for m in _ROW.finditer(_flat(text)):
        label = m.group("label").lower()
        label = "offer_for_sale" if label == "offer for sale" else "fresh_issue" if label == "fresh issue" else "offer_total"
        # the whole-offer row is the first "Offer" or "Issue" row; later rows with the same label are sub-rows
        out.setdefault(label, Amount(m.group("printed").strip(), to_crore(m.group("amount"), m.group("unit")), page))
    return out


def _objects(lines: list[str], unit: float | None) -> list[dict[str, Any]]:
    """The rows of the 'requirement of funds' table: an object's name runs over lines until one ends with its amount."""
    rows: list[dict[str, Any]] = []
    buffer: list[str] = []
    for raw in lines[:MAX_OBJECT_LINES]:
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(Net Proceeds|Total)\b", line, re.IGNORECASE):
            break
        if _TABLE_HEADER.search(line) or line.endswith(":"):
            buffer = []  # the sentence that introduces the table, or its column heads, is not part of a row's name
            continue
        m = _AMOUNT_END.search(line)
        if m and (re.search(r"[A-Za-z]", line[: m.start()]) or buffer):
            name = _LEADING_NUMBER.sub("", _FOOTNOTES.sub("", " ".join([*buffer, line[: m.start()]]))).strip(" :-")
            if name:
                amount = m.group("amount")
                rows.append({"object": name[:240], "printed": amount, "crore": None if amount == BLANK or unit is None else round(float(amount.replace(",", "")) * unit, 4)})
            buffer = []
        else:
            buffer.append(line)
    return rows


def offer_figures(pages: Sequence[str], sections: Sequence[Section]) -> OfferFigures:
    """The offer structure, the pre-offer share count, the objects of the offer and the lead managers, from the pages of `pages` (1-based page numbers are kept for every figure)."""
    fig = OfferFigures()
    count = len(pages)
    front = [p for p in range(1, min(count, 8) + 1)]
    summary = _pages_of(sections, ("offer_summary",), count) or _pages_of(sections, ("introduction",), count)

    for p in front:
        text = _flat(pages[p - 1])
        if fig.draft_date is None and (m := _DRAFT_DATE.search(text)):
            fig.draft_date = m.group("d")
        if not fig.managers and "BOOK RUNNING LEAD MANAGER" in pages[p - 1].upper():
            block = pages[p - 1].split("REGISTRAR", 1)[0]
            names = (re.sub(r"^.*\bnamely,?\s*", "", ln.strip()) for ln in block.splitlines())  # "Book running lead manager to the Issue, namely, X Limited"
            fig.managers = [n for n in names if _FIRM.match(n)][:6]
        for name, key in (("BSE Limited", "BSE"), ("National Stock Exchange of India Limited", "NSE")):
            if name in text and key not in fig.exchanges:
                fig.exchanges.append(key)

    for p in summary:
        text = pages[p - 1]
        flat = _flat(text)
        for label, amount in _rows(text, p).items():
            if getattr(fig, label) is None:
                setattr(fig, label, amount)
        if fig.face_value is None and (m := _FACE_VALUE.search(flat)):
            fig.face_value = float(m.group("value"))
        if fig.pre_offer_shares is None and (m := _PRE_OFFER.search(flat)):
            fig.pre_offer_shares = int(m.group("shares").replace(",", ""))
        if fig.board_resolution is None and (m := _BOARD.search(flat)):
            fig.board_resolution = m.group("d")

    objects_pages = _pages_of(sections, ("objects",), count)
    for p in [*summary, *objects_pages]:  # the placement is a footnote of the offer table and of the objects table
        if fig.pre_ipo_placement is None and (m := _PRE_IPO.search(_flat(pages[p - 1]))):
            fig.pre_ipo_placement = Amount(f"₹ {m.group('amount')} {m.group('unit')}", to_crore(m.group("amount"), m.group("unit")), p)
    for p in objects_pages:
        lines = pages[p - 1].splitlines()
        for i, line in enumerate(lines):
            if _OBJECTS_HEADING.search(line):
                head = _flat(" ".join(lines[max(0, i - 2) : i + 6]))
                unit = _TABLE_UNIT.search(head) or _TABLE_UNIT.search(_flat(pages[p - 1]))
                fig.objects_unit_crore = _CRORE_PER.get(unit.group("unit").lower()) if unit else None
                fig.objects = _objects(lines[i + 1 :], fig.objects_unit_crore)
                break
        if fig.objects:
            break

    _derive(fig)
    return fig


def _derive(fig: OfferFigures) -> None:
    total, fresh, sale = (a.crore if a else None for a in (fig.offer_total, fig.fresh_issue, fig.offer_for_sale))
    if total and sale is not None:
        fig.offer_for_sale_share = round(sale / total, 4)
    elif total and fresh is not None and fig.offer_for_sale is None and fig.fresh_issue is not None:
        fig.offer_for_sale_share = 0.0  # the table has a fresh issue and no offer for sale row: the whole offer is fresh
    if total is not None and fresh is not None:
        sold = sale or 0.0
        ok = abs((fresh + sold) - total) <= max(0.01, 0.005 * total)
        fig.checks.append({"check": "fresh issue plus offer for sale equals the offer", "ok": ok, "detail": f"{fresh:.2f} + {sold:.2f} vs {total:.2f} crore"})
    amounts = [o["crore"] for o in fig.objects if o["crore"] is not None]
    if amounts:
        fig.objects_total_crore = round(sum(amounts), 4)
        if fresh is not None:
            fig.checks.append({"check": "amounts for the objects do not exceed the fresh issue", "ok": fig.objects_total_crore <= fresh * 1.005, "detail": f"{fig.objects_total_crore:.2f} vs {fresh:.2f} crore"})
