"""Checks on the shareholding tables of an offer document, with no model: a table of holders prints a share count and a percentage for each, then a total.

Relations that must hold: the holders listed above a `Total` add up to it (the share count exactly, the percentage within the rounding of the table); lettered totals add up to the total that names them (`Total (A+B)`,
`Total (C=A+B)`); and a holding printed as 100% reaches the share count the draft states for the capital before the offer. Only a table of one shape is read: a name (wrapped over lines or not), a share count, a
percentage, then any number of `[●]` blanks for the columns that come after the offer. A table with other columns (a build-up by date, a wide pattern by category) is not read, and a `Total` that cannot be tied to
the rows above it is counted in the notes, never as clean. A group that fails is only called a difference when no other reading of its rows (the rows since the last repeated heading) explains it and no line of it
carried a number without reading as a row."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from athena.forensic.checks.document import ARITH, Check, Term, decide
from athena.forensic.checks.rows import NUMBER, parse_number

HOLDING_SECTIONS = ("capital_structure", "promoters", "management")
BLANK = "[●]"
MARKS = re.compile(r"^[*#^$@]+|[*#^$@]+$")
NIL = {"nil", "-", "—", "–"}
NOT_GIVEN = {"negligible", "na", "n.a", "n.a."}  # a percentage the table does not print as a number
SHARES = re.compile(r"^\d[\d,]*$")
PCT = re.compile(r"^(\d{1,3})\.(\d{1,4})%?$")
BIG_FIGURE = re.compile(r"^(?:\d{1,3}(?:,\d{2,3})+|\d+\.\d{2})$")  # a number that can only be a count or a percentage, not a note number or a face value
HEADING = re.compile(r"^(?:sr\.?\s*$|s\.\s*no|sr\.\s*no|name\b|particulars|category|description|date of)", re.IGNORECASE)
TAG = re.compile(r"\(\s*([a-z](?:\s*[=+]\s*[a-z])*)\s*\)\s*$")
FULL = 99.995  # a percentage that prints as 100.00
MAX_LABEL_LINES = 4
MAX_FRAGMENT = 60


@dataclass(frozen=True)
class Holding:
    label: str
    shares: int
    shares_printed: str
    pct: float | None
    pct_printed: str
    pct_decimals: int
    page: int


@dataclass
class Group:
    rows: list[Holding] = field(default_factory=list)
    since_heading: list[Holding] = field(default_factory=list)
    orphans: int = 0  # lines with a count or percentage in them that did not read as a row
    subtotals: dict[str, Holding] = field(default_factory=dict)  # lettered totals, for `Total (A+B)`
    chain: list[Holding] = field(default_factory=list)  # every total since the table broke, for a total of subtotals


def _clean(token: str) -> str:
    return MARKS.sub("", token) or token


def _shares(token: str) -> tuple[int, str] | None:
    t = _clean(token)
    if t.lower() in NIL:
        return 0, token
    if not SHARES.match(t) or t.endswith(","):
        return None
    parsed = parse_number(t)
    return (int(parsed[0]), t) if parsed else None


def _pct(token: str) -> tuple[float | None, str, int] | None:
    t = _clean(token)
    if t.lower() in NIL or t.lower() in NOT_GIVEN:
        return None, t, 0
    m = PCT.match(t)
    return (float(f"{m.group(1)}.{m.group(2)}"), t.rstrip("%"), len(m.group(2))) if m else None


def read_line(line: str, page: int) -> tuple[str, Any]:
    """('row', Holding) when the line ends in a share count and a percentage (then blanks), ('mixed', None) when it carries two or more numbers in any other shape, else ('label', text)."""
    tokens = line.split()
    end = len(tokens)
    while end and tokens[end - 1] == BLANK:
        end -= 1
    if end >= 2:
        pct, shares = _pct(tokens[end - 1]), _shares(tokens[end - 2])
        if pct is not None and shares is not None:
            if end >= 3 and NUMBER.match(_clean(tokens[end - 3])):  # a third number (a serial, a date, another column) is another shape of table
                return "mixed", None
            if any(BIG_FIGURE.match(_clean(t)) for t in tokens[: end - 2]):  # a count or percentage in front of the name's end: a wide table whose last two columns happen to look like ours
                return "mixed", None
            return "row", Holding(" ".join(tokens[: end - 2]), shares[0], shares[1], pct[0], pct[1], pct[2], page)
    numbers = sum(1 for t in tokens if t != BLANK and NUMBER.match(_clean(t)))
    return ("mixed", None) if numbers >= 2 else ("label", line)


def _term(name: str, h: Holding, pct: bool) -> Term:
    if pct:
        return Term(f"{name[:40]} (percentage)", h.pct or 0.0, h.page, h.pct_printed, 0.5 * 10.0 ** -h.pct_decimals)
    return Term(f"{name[:40]} (shares)", float(h.shares), h.page, h.shares_printed, 0.0)  # a count is exact: nothing is rounded


def _sum_check(cid: str, title: str, parts: Sequence[Holding], total: Holding, pct: bool) -> Check:
    c = Check(cid, ARITH, title, None)
    c.left = [_term(h.label or "holder", h, pct) for h in parts]
    c.right = [_term(total.label or "total", total, pct)]
    return decide(c)


def _percentages(h: Holding) -> bool:
    return h.pct is not None and h.pct <= 100.0 + 0.5 * 10.0 ** -h.pct_decimals


def _label(total: Holding) -> str:
    return re.sub(r"\s+", " ", total.label).strip()


def _tag(label: str) -> tuple[str | None, list[str]]:
    """(name, letters of the formula) of a total's label: `Total (A)` is ('A', []), `Total (C=A+B)` is ('C', ['A', 'B']), `Total (A+B)` is ('A+B', ['A', 'B'])."""
    m = TAG.search(label.lower())
    if not m:
        return None, []
    body = re.sub(r"\s+", "", m.group(1)).upper()
    if "=" in body:
        name, formula = body.split("=", 1)
        return name, formula.split("+")
    return (body, body.split("+")) if "+" in body else (body, [])


class Reader:
    def __init__(self) -> None:
        self.group = Group()
        self.labels: list[str] = []
        self.checks: list[Check] = []
        self.unchecked = 0
        self.skipped: list[str] = []
        self.full: list[Holding] = []  # totals printed as 100%
        self.n = 0

    def reset(self, keep_subtotals: bool = False) -> None:
        old = self.group
        self.group = Group(subtotals=old.subtotals, chain=old.chain) if keep_subtotals else Group()
        self.labels = []

    def line(self, text: str, page: int) -> None:
        text = text.strip()
        if not text or re.fullmatch(r"\d{1,4}", text):  # a blank line or a page number
            return
        kind, got = read_line(text, page)
        if kind == "mixed":
            if text.lower().startswith("total"):
                self.unchecked += 1
                self.skipped.append(f"p{page} mixed: {text[:90]}")
            self.reset()
        elif kind == "label":
            if len(text) > MAX_FRAGMENT and text.endswith((".", ":")):  # prose between tables
                self.reset()
                return
            if HEADING.match(text):
                self.group.since_heading = []
            if len(text) <= MAX_FRAGMENT and any(BIG_FIGURE.match(_clean(t)) for t in text.split()):  # the size of a holder's row, not of a sentence that quotes a count
                self.group.orphans += 1
            self.labels = [*self.labels, text][-MAX_LABEL_LINES:]
        else:
            is_total = got.label.lower().startswith("total")
            h = Holding((got.label if is_total else " ".join([*self.labels, got.label])).strip(), got.shares, got.shares_printed, got.pct, got.pct_printed, got.pct_decimals, page)
            self.labels = []
            if is_total:
                self.total(h)
            else:
                self.group.rows.append(h)
                self.group.since_heading.append(h)

    def total(self, t: Holding) -> None:
        name, letters = _tag(_label(t))
        g = self.group
        if t.pct is not None and t.pct >= FULL and t.shares > 0:
            self.full.append(t)
        if len(letters) >= 2:
            self.composite(t, letters)
            self.reset()
            return
        subtotals = [] if name else [g.chain[-k:] for k in range(2, len(g.chain) + 1)]  # an unlettered total may be the total of the totals above it; `Total (C)` is its own part, not theirs
        sets = [g.rows, g.since_heading, *subtotals]  # the holders, the holders under the last heading, or the last totals before it
        usable = [rows for i, rows in enumerate(sets) if len(rows) >= 2 and rows not in sets[:i]]
        if not usable:
            self.unchecked += 1
            self.skipped.append(f"p{t.page} no rows: {_label(t)[:50]} {t.shares_printed} {t.pct_printed}")
        else:
            self.sum(t, usable, g.orphans)
        letters_seen, chain = g.subtotals, g.chain
        self.reset(keep_subtotals=True)
        letters_seen[name or "_"] = t
        chain.append(t)

    def sum(self, t: Holding, candidates: list[list[Holding]], orphans: int) -> None:
        self.n += 1
        title = f"the holders listed above '{_label(t)[:50]}' add up to its total (page {t.page})"
        for pct in (False, True):
            usable = [rows for rows in candidates if not pct or (_percentages(t) and all(_percentages(h) for h in rows))]  # `Negligible` or `NA` cannot be summed; a column with a value over 100 is not a percentage
            runs = [_sum_check(f"holding_{'pct' if pct else 'shares'}:{t.page}:{self.n}", title + (" in percentage" if pct else " in shares"), rows, t, pct) for rows in usable]
            if not runs:
                continue
            best = next((c for c in runs if c.status == "ok"), runs[0])
            if best.status == "differs" and orphans:
                best.status, best.reason = "not_run", "a line of the table carried a count or a percentage but did not read as a row, so a holder may be missing from the sum"
            self.checks.append(best)

    def composite(self, t: Holding, letters: list[str]) -> None:
        self.n += 1
        have = self.group.subtotals
        title = f"the totals {', '.join(letters)} add up to '{_label(t)[:50]}' (page {t.page})"
        missing = [x for x in letters if x not in have]
        if missing:
            c = Check(f"holding_total:{t.page}:{self.n}", ARITH, title, None, reason=f"the total(s) {', '.join(missing)} were not found above it in the same table")
            self.checks.append(c)
            return
        parts = [have[x] for x in letters]
        self.checks.append(_sum_check(f"holding_total_shares:{t.page}:{self.n}", title + " in shares", parts, t, False))
        if _percentages(t) and all(_percentages(p) for p in parts):
            self.checks.append(_sum_check(f"holding_total_pct:{t.page}:{self.n}", title + " in percentage", parts, t, True))


def _ties(full: Sequence[Holding], figures: Mapping[str, Any] | None) -> Check | None:
    """A holding printed as 100% reaches the share count the draft states before the offer, to the rounding of its percentage."""
    pre = (figures or {}).get("pre_offer_shares")
    title = "a holding printed as 100% reaches the number of shares before the offer"
    if not isinstance(pre, int) or pre <= 0:
        return Check("holding_reaches_capital", ARITH, title, None, reason="the number of shares before the offer was not found in the draft") if full else None
    if not full:
        return Check("holding_reaches_capital", ARITH, title, None, reason="no holding printed as 100% was read")
    base = Term("shares before the offer", float(pre), 0, f"{pre:,}", 0.0)
    attempts = []
    for t in full:
        c = Check("holding_reaches_capital", ARITH, title, None)
        c.left = [Term(f"{_label(t)[:40]} (shares)", float(t.shares), t.page, t.shares_printed, pre * 0.5 * 10.0 ** -t.pct_decimals / 100.0)]  # the printed percentage rounds to 100.00, so the count may be this far short
        c.right = [base]
        decide(c)
        if c.status == "ok":
            return c
        attempts.append(c)
    c = attempts[0]
    c.status, c.reason = "not_run", f"none of the {len(attempts)} holding(s) printed as 100% equals {pre:,}: the tables may be of another date or another class of shares, so a difference means nothing"
    return c


def read_pages(pages: Sequence[tuple[int, str, str]]) -> Reader:
    reader = Reader()
    last = None
    for page, _, text in sorted(p for p in pages if p[1] in HOLDING_SECTIONS):
        if last is not None and page != last + 1:  # a table does not run on across pages that are not next to each other
            reader.reset()
        last = page
        for line in text.splitlines():
            reader.line(line, page)
    return reader


def holding_checks(pages: Sequence[tuple[int, str, str]], figures: Mapping[str, Any] | None) -> tuple[list[Check], list[str]]:
    reader = read_pages(pages)
    checks = reader.checks
    tie = _ties(reader.full, figures)
    if tie is not None:
        checks.append(tie)
    notes = [f"{reader.unchecked} total row(s) of holdings were not checked: the lines above them did not read as holders of one table (a build-up by date, a table with other columns)"] if reader.unchecked else []
    return checks, notes
