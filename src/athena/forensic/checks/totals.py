"""Checks on the tables of an offer document that print a count and a second figure for each row, then a total, with no model.

Two kinds are read. A table of holders (capital structure, promoters, management) prints a share count and a percentage; a table of litigation (outstanding dues to creditors, tax proceedings) prints a number of
cases or creditors and an amount. Relations that must hold: the rows above a `Total` add up to it (the count exactly, the second figure within the rounding of the table); lettered totals add up to the total that names
them (`Total (A+B)`, `Total (C=A+B)`); and a holding printed as 100% reaches the share count the draft states for the capital before the offer. Only a table of one shape is read: a name (wrapped over lines or not), a
count, a second figure, then any number of `[●]` blanks for the columns that come after the offer. A table with other columns (a build-up by date, a wide pattern by category) is not read, and a `Total` that cannot be
tied to the rows above it is counted in the notes, never as clean. A group that fails is only called a difference when no other reading of its rows (the rows since the last repeated heading) explains it and no line of
it carried a number without reading as a row."""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from athena.forensic.checks.document import ARITH, Check, Term, decide
from athena.forensic.checks.rows import NUMBER, parse_number

BLANK = "[●]"
MARKS = re.compile(r"^[*#^$@]+|[*#^$@]+$")
NIL = {"nil", "-", "—", "–"}
NOT_GIVEN = {"negligible", "na", "n.a", "n.a."}  # a figure the table does not print as a number
COUNT = re.compile(r"^\d[\d,]*$")
PCT = re.compile(r"^(\d{1,3})\.(\d{1,4})%?$")
AMOUNT = re.compile(r"^(\d{1,3}(?:,\d{2,3})*|\d+)\.(\d{1,4})$")
BIG_FIGURE = re.compile(r"^(?:\d{1,3}(?:,\d{2,3})+|\d+\.\d{2})$")  # a number that can only be a count or a second figure, not a note number or a face value
HEADING = re.compile(r"^(?:sr\.?\s*$|s\.\s*no|sr\.\s*no|name\b|particulars|category|description|date of|types? of|nature of)", re.IGNORECASE)
SERIAL = re.compile(r"^\s*(?:\(?\d{1,2}\)|\d{1,2}\.|\(?[a-z]\)|[ivx]+\.)\s+\S", re.IGNORECASE)  # `2. Dues to`, `(a) Criminal`
TAG = re.compile(r"\(\s*([a-z](?:\s*[=+]\s*[a-z])*)\s*\)\s*$")
FULL = 99.995  # a percentage that prints as 100.00
NO_ROUNDING = 12  # decimals of a figure that is exactly nothing
MAX_LABEL_LINES = 4
MAX_FRAGMENT = 60


@dataclass(frozen=True)
class Kind:
    prefix: str  # of the check ids
    sections: tuple[str, ...]
    count_id: str
    second_id: str
    count_name: str
    second_name: str
    rows_name: str
    table_pattern: re.Pattern[str]
    plausible: Callable[[float, int], bool]  # whether a value can be in the second column at all (a percentage is at most 100)


HOLDINGS = Kind("holding", ("capital_structure", "promoters", "management"), "shares", "pct", "shares", "percentage", "holders", PCT, lambda v, d: v <= 100.0 + 0.5 * 10.0 ** -d)
LITIGATION = Kind("litigation", ("litigation",), "count", "amount", "number", "amount", "categories", AMOUNT, lambda v, d: v >= 0)


@dataclass(frozen=True)
class Entry:
    label: str
    count: int
    count_printed: str
    second: float | None
    second_printed: str
    second_decimals: int
    page: int
    child: bool = False  # a part of a numbered heading that has no figures of its own, so it may already be inside another row's figures


@dataclass
class Group:
    rows: list[Entry] = field(default_factory=list)
    since_heading: list[Entry] = field(default_factory=list)
    orphans: int = 0  # lines with a count or a second figure in them that did not read as a row
    subtotals: dict[str, Entry] = field(default_factory=dict)  # lettered totals, for `Total (A+B)`
    chain: list[Entry] = field(default_factory=list)  # every total since the table broke, for a total of subtotals


def _clean(token: str) -> str:
    return MARKS.sub("", token) or token


def _count(token: str) -> tuple[int, str] | None:
    t = _clean(token)
    if t.lower() in NIL:
        return 0, token
    if not COUNT.match(t) or t.endswith(","):
        return None
    parsed = parse_number(t)
    return (int(parsed[0]), t) if parsed else None


def _second(token: str, kind: Kind) -> tuple[float | None, str, int] | None:
    t = _clean(token)
    if t.lower() in NIL or t.lower() in NOT_GIVEN:
        return None, t, 0
    m = kind.table_pattern.match(t)
    if not m:
        return None
    return float(f"{m.group(1).replace(',', '')}.{m.group(2)}"), t.rstrip("%"), len(m.group(2))


def read_line(line: str, page: int, kind: Kind = HOLDINGS) -> tuple[str, Any]:
    """('row', Entry) when the line ends in a count and a second figure (then blanks), ('mixed', None) when it carries two or more numbers in any other shape, else ('label', text)."""
    tokens = line.split()
    end = len(tokens)
    while end and tokens[end - 1] == BLANK:
        end -= 1
    if end >= 2:
        second, count = _second(tokens[end - 1], kind), _count(tokens[end - 2])
        if second is not None and count is not None:
            if end >= 3 and NUMBER.match(_clean(tokens[end - 3])):  # a third number (a serial, a date, another column) is another shape of table
                return "mixed", None
            if any(BIG_FIGURE.match(_clean(t)) for t in tokens[: end - 2]):  # a count or figure in front of the name's end: a wide table whose last two columns happen to look like ours
                return "mixed", None
            value, printed, decimals = second
            if value is None and count[0] == 0:  # nothing counted, so nothing to add: `Nil NA`
                value, decimals = 0.0, NO_ROUNDING
            return "row", Entry(" ".join(tokens[: end - 2]), count[0], count[1], value, printed, decimals, page)
    numbers = sum(1 for t in tokens if t != BLANK and NUMBER.match(_clean(t)))
    return ("mixed", None) if numbers >= 2 else ("label", line)


def _term(name: str, e: Entry, second: bool, kind: Kind) -> Term:
    if second:
        return Term(f"{name[:40]} ({kind.second_name})", e.second or 0.0, e.page, e.second_printed, 0.5 * 10.0 ** -e.second_decimals)
    return Term(f"{name[:40]} ({kind.count_name})", float(e.count), e.page, e.count_printed, 0.0)  # a count is exact: nothing is rounded


def _sum_check(cid: str, title: str, parts: Sequence[Entry], total: Entry, second: bool, kind: Kind) -> Check:
    c = Check(cid, ARITH, title, None)
    c.left = [_term(e.label or "row", e, second, kind) for e in parts]
    c.right = [_term(total.label or "total", total, second, kind)]
    return decide(c)


def _label(total: Entry) -> str:
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
    def __init__(self, kind: Kind) -> None:
        self.kind = kind
        self.group = Group()
        self.labels: list[str] = []
        self.checks: list[Check] = []
        self.unchecked = 0
        self.skipped: list[str] = []
        self.full: list[Entry] = []  # totals printed as 100%
        self.in_block = False  # after a numbered heading with no figures: the rows without a serial of their own are its parts
        self.n = 0

    def usable(self, e: Entry) -> bool:
        return e.second is not None and self.kind.plausible(e.second, e.second_decimals)

    def reset(self, keep_subtotals: bool = False) -> None:
        old = self.group
        self.group = Group(subtotals=old.subtotals, chain=old.chain) if keep_subtotals else Group()
        self.labels = []
        self.in_block = False

    def line(self, text: str, page: int) -> None:
        text = text.strip()
        if not text or re.fullmatch(r"\d{1,4}", text):  # a blank line or a page number
            return
        kind, got = read_line(text, page, self.kind)
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
            elif SERIAL.match(text):
                self.in_block = True
            if len(text) <= MAX_FRAGMENT and any(BIG_FIGURE.match(_clean(t)) for t in text.split()):  # the size of a row, not of a sentence that quotes a count
                self.group.orphans += 1
            self.labels = [*self.labels, text][-MAX_LABEL_LINES:]
        else:
            is_total = got.label.lower().startswith("total")
            serial = bool(SERIAL.match(got.label))
            e = Entry((got.label if is_total else " ".join([*self.labels, got.label])).strip(), got.count, got.count_printed, got.second, got.second_printed, got.second_decimals, page, child=not is_total and self.in_block and not serial)
            self.labels = []
            if is_total:
                self.total(e)
            else:
                if serial:
                    self.in_block = False
                self.group.rows.append(e)
                self.group.since_heading.append(e)

    def total(self, t: Entry) -> None:
        name, letters = _tag(_label(t))
        g = self.group
        if self.kind.second_id == "pct" and t.second is not None and t.second >= FULL and t.count > 0:
            self.full.append(t)
        if len(letters) >= 2:
            self.composite(t, letters)
            self.reset()
            return
        subtotals = [] if name else [g.chain[-k:] for k in range(2, len(g.chain) + 1)]  # an unlettered total may be the total of the totals above it; `Total (C)` is its own part, not theirs
        parents = [e for e in g.rows if not e.child] if any(e.child for e in g.rows) else []  # the rows that are not parts of another
        sets = [g.rows, g.since_heading, parents, *subtotals]  # the rows, the rows under the last heading, the rows that are not parts of another, or the last totals before it
        usable = [rows for i, rows in enumerate(sets) if len(rows) >= 2 and rows not in sets[:i]]
        if not usable:
            self.unchecked += 1
            self.skipped.append(f"p{t.page} no rows: {_label(t)[:50]} {t.count_printed} {t.second_printed}")
        else:
            self.sum(t, usable, g.orphans)
        letters_seen, chain = g.subtotals, g.chain
        self.reset(keep_subtotals=True)
        letters_seen[name or "_"] = t
        chain.append(t)

    def sum(self, t: Entry, candidates: list[list[Entry]], orphans: int) -> None:
        k = self.kind
        candidates = [rows for rows in candidates if t.count or t.second or any(e.count or e.second for e in rows)]  # a table of nothing but Nil adds up to nothing, which says nothing
        if not candidates:
            return
        self.n += 1
        title = f"the {k.rows_name} listed above '{_label(t)[:50]}' add up to its total (page {t.page})"
        for second in (False, True):
            usable = [rows for rows in candidates if not second or (self.usable(t) and all(self.usable(e) for e in rows))]  # `Negligible` or `NA` cannot be summed
            what = k.second_id if second else k.count_id
            runs = [_sum_check(f"{k.prefix}_{what}:{t.page}:{self.n}", f"{title} in {k.second_name if second else k.count_name}", rows, t, second, k) for rows in usable]
            if not runs:
                continue
            best = next((c for c in runs if c.status == "ok"), runs[0])
            if best.status == "differs" and orphans:
                best.status, best.reason = "not_run", f"a line of the table carried a count or a {k.second_name} but did not read as a row, so a row may be missing from the sum"
            self.checks.append(best)

    def composite(self, t: Entry, letters: list[str]) -> None:
        k = self.kind
        self.n += 1
        have = self.group.subtotals
        title = f"the totals {', '.join(letters)} add up to '{_label(t)[:50]}' (page {t.page})"
        missing = [x for x in letters if x not in have]
        if missing:
            self.checks.append(Check(f"{k.prefix}_total:{t.page}:{self.n}", ARITH, title, None, reason=f"the total(s) {', '.join(missing)} were not found above it in the same table"))
            return
        parts = [have[x] for x in letters]
        self.checks.append(_sum_check(f"{k.prefix}_total_{k.count_id}:{t.page}:{self.n}", f"{title} in {k.count_name}", parts, t, False, k))
        if self.usable(t) and all(self.usable(p) for p in parts):
            self.checks.append(_sum_check(f"{k.prefix}_total_{k.second_id}:{t.page}:{self.n}", f"{title} in {k.second_name}", parts, t, True, k))


def _ties(full: Sequence[Entry], figures: Mapping[str, Any] | None) -> Check | None:
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
        c.left = [Term(f"{_label(t)[:40]} (shares)", float(t.count), t.page, t.count_printed, pre * 0.5 * 10.0 ** -t.second_decimals / 100.0)]  # the printed percentage rounds to 100.00, so the count may be this far short
        c.right = [base]
        decide(c)
        if c.status == "ok":
            return c
        attempts.append(c)
    c = attempts[0]
    c.status, c.reason = "not_run", f"none of the {len(attempts)} holding(s) printed as 100% equals {pre:,}: the tables may be of another date or another class of shares, so a difference means nothing"
    return c


def read_pages(pages: Sequence[tuple[int, str, str]], kind: Kind = HOLDINGS) -> Reader:
    reader = Reader(kind)
    last = None
    for page, _, text in sorted(p for p in pages if p[1] in kind.sections):
        if last is not None and page != last + 1:  # a table does not run on across pages that are not next to each other
            reader.reset()
        last = page
        for line in text.splitlines():
            reader.line(line, page)
    return reader


def total_checks(pages: Sequence[tuple[int, str, str]], figures: Mapping[str, Any] | None) -> tuple[list[Check], list[str]]:
    checks: list[Check] = []
    notes: list[str] = []
    for kind, what in ((HOLDINGS, "holdings (a build-up by date, a table with other columns)"), (LITIGATION, "litigation and dues (a table with other columns)")):
        reader = read_pages(pages, kind)
        checks.extend(reader.checks)
        if kind is HOLDINGS and (tie := _ties(reader.full, figures)) is not None:
            checks.append(tie)
        if reader.unchecked:
            notes.append(f"{reader.unchecked} total row(s) of {what} were not checked: the lines above them did not read as the rows of one table")
    return checks, notes
