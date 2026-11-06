"""Checks on the capitalisation table of an offer document, with no model: a table as at the last balance sheet date whose rows are lettered and whose totals and ratios are printed as formulas of the letters
(`Total Borrowings (C = A+B)`, `Total equity (F = D+E)`, `Ratio: Total borrowings / Total equity (C/F)`, `Total Capital (A+B)`).

Relations that must hold: a row whose label carries a formula of lettered rows equals it (a sum of the rows named, or a ratio of two, within the rounding of the printed figures and, for a ratio, of the figures it is
made of); and the total equity it prints is the total equity of the restated balance sheet at the same date. The section on indebtedness is not here: in most drafts it is as at a later date than the balance sheet,
so its figures are not the statements' figures and a difference would mean nothing."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from athena.forensic.checks.document import ARITH, DOC_VS_DOC, Check, Located, Term, _unit, decide
from athena.forensic.checks.rows import find, parse_number

SECTION = "capitalisation"
BLANK = "[●]"
MARKS = re.compile(r"[*^$#@]+")
TAIL = re.compile(r"(\(\s*[A-Z]\s*\)\s*/\s*\(\s*[A-Z]\s*\)|\(\s*[^()]{1,40}?\s*\))\s*$")
RATIO_PAIR = re.compile(r"\(\s*([A-Z])\s*\)\s*/\s*\(\s*([A-Z])\s*\)")
STATED_DATE = re.compile(r"as\s+(?:at|on|of)\s+([A-Za-z]+)\s+(\d{1,2})\s*,?\s*(20\d\d)", re.IGNORECASE)
SPLIT_YEAR = re.compile(r"\b(20\d)\s(\d)\b")  # `202 6`: a year the extraction broke
BASIS = re.compile(r"\b(consolidated|standalone)\b", re.IGNORECASE)
MAX_LABEL_LINES = 3
UNIT_NOTE = re.compile(r"\(\s*(?:in\s+)?(?:times|x|%|₹[^)]*)\s*\)\s*$", re.IGNORECASE)  # `(in times)` after a formula
TOTAL = re.compile(r"total\b", re.IGNORECASE)
FOOTNOTE = re.compile(r"^[*^$#@†‡]")
YEAR_TAIL = re.compile(r"^20\d\d$")


NO_ROUNDING = 12  # decimals of a figure that is exactly nothing


@dataclass(frozen=True)
class Line:
    label: str  # with the lines that wrapped into it
    value: float
    printed: str
    decimals: int
    page: int
    own: str = ""  # what is on the line of the figure itself

    def term(self, name: str | None = None) -> Term:
        return Term((name or self.label)[:60], self.value, self.page, self.printed, 0.5 * 10.0 ** -self.decimals)


def read_lines(pages: Sequence[tuple[int, str, str]]) -> list[Line]:
    """The rows of the capitalisation section that end in one figure (then `[●]` blanks for the column after the offer). A label that wraps over lines is joined."""
    out: list[Line] = []
    labels: list[str] = []
    for page, section, text in sorted(p for p in pages if p[1] == SECTION):
        for raw in text.splitlines():
            tokens = raw.split()
            blanked = bool(tokens) and tokens[-1] == BLANK
            while tokens and tokens[-1] == BLANK:
                tokens.pop()
            if not tokens:
                continue
            if FOOTNOTE.match(tokens[0]):  # a note under the table, which may end in a year or a figure
                labels = []
                continue
            parsed = parse_number(tokens[-1])
            if tokens[-1] == "-":  # a row of nothing; a lone dash is not a row
                parsed = (0.0, NO_ROUNDING) if len(tokens) >= 2 else None
            lone = len(tokens) == 1 and parsed is not None and bool(labels) and (blanked or "." in tokens[0])  # the figure of a label that wrapped: `49.79 [●]` under `Non-current borrowings (...) (B)`; a page number has neither
            if parsed is None or (len(tokens) == 1 and not lone):
                labels = [*labels, raw.strip()][-MAX_LABEL_LINES:] if parsed is None else []
                continue
            if len(tokens) >= 2 and parse_number(tokens[-2]) is not None and tokens[-2] != "-":  # two figures in a row: another shape of table
                labels = []
                continue
            if len(tokens) >= 2 and YEAR_TAIL.match(tokens[-1]) and tokens[-2].endswith(","):  # `March 31, 2026`
                labels = []
                continue
            own = " ".join(tokens[:-1]).strip()
            out.append(Line(" ".join([*labels, own]).strip(), parsed[0], tokens[-1], parsed[1], page, own or (labels[-1] if labels else "")))
            labels = []
    return out


def parse_tail(label: str) -> tuple[str | None, str | None, tuple[str, ...]]:
    """(letter the row defines, operator, letters it is made of) from the formula at the end of a label: `(C = A+B)` is ('C', '+', ('A', 'B')), `(A+B=C)` the same, `(B/F)` is (None, '/', ('B', 'F')),
    `(F)` is ('F', None, ()), and anything else is (None, None, ())."""
    m = TAIL.search(UNIT_NOTE.sub("", MARKS.sub("", label)).strip())
    if not m:
        return None, None, ()
    body = m.group(1)
    if pair := RATIO_PAIR.fullmatch(body):
        return None, "/", (pair.group(1), pair.group(2))
    inner = re.sub(r"\s+", "", body.strip("()"))
    if re.fullmatch(r"[A-Z]", inner):
        return inner, None, ()
    for pattern, defined, expr in ((r"([A-Z])=([A-Z](?:[+/][A-Z])+)", 1, 2), (r"([A-Z](?:[+/][A-Z])+)=([A-Z])", 2, 1), (r"([A-Z](?:[+/][A-Z])+)", 0, 1)):
        if m2 := re.fullmatch(pattern, inner):
            letters, op = m2.group(expr), "/" if "/" in m2.group(expr) else "+"
            parts = tuple(re.split(r"[+/]", letters))
            if op == "/" and (len(parts) != 2 or "+" in letters):
                return None, None, ()
            return (m2.group(defined) if defined else None), op, parts
    return None, None, ()


def _name(label: str) -> str:
    """What a row is called, to match a ratio's parts to rows: lower case, without notes, letters in brackets or the word ratio."""
    t = MARKS.sub("", label).lower()
    t = re.sub(r"\([^()]*\)", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"^ratio\s*:\s*", "", t.strip())).strip(" :-")


def _ratio_parts(label: str) -> tuple[str, str] | None:
    """The two names of a ratio printed without letters (`Ratio: Total borrowings/ Total equity (in times)`), when the label has exactly one slash."""
    t = _name(UNIT_NOTE.sub("", label))
    parts = [x.strip() for x in t.split("/")]
    return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else None


def _check_ratio(cid: str, title: str, line: Line, x: Line, y: Line) -> Check:
    c = Check(cid, ARITH, title, None)
    if y.value == 0:
        c.reason = "the divisor is nil"
        return c
    half = 0.5 * 10.0 ** -x.decimals / abs(y.value) + abs(x.value) * 0.5 * 10.0 ** -y.decimals / y.value**2  # what the rounding of the two figures does to their ratio
    c.left = [line.term()]
    c.right = [Term(f"{x.label[:25]} / {y.label[:25]} (worked out)", x.value / y.value, line.page, f"{x.printed} / {y.printed}", half)]
    return decide(c)


def formula_checks(lines: Sequence[Line]) -> list[Check]:
    """A row with a formula of letters equals it; a total with no formula equals the rows above it since the last total; a ratio with two names and no letters equals the rows those names are."""
    out: list[Check] = []
    defs: dict[str, Line] = {}
    named: dict[str, Line] = {}
    since: list[Line] = []  # the rows after the last total
    for n, line in enumerate(lines):
        defined, op, letters = parse_tail(line.label)
        clean = re.sub(r"[*^$#@]+", "", line.label)
        if op:
            title = f"{clean[:70]} is {op.join(letters)} (page {line.page})"
            missing = [x for x in letters if x not in defs]
            c = Check(f"capitalisation_{'ratio' if op == '/' else 'sum'}:{line.page}:{n}", ARITH, title, None)
            if missing:
                c.reason = f"the row(s) {', '.join(missing)} named in the formula were not found above it"
            elif op == "+":
                c.left, c.right = [line.term()], [defs[x].term() for x in letters]
                decide(c)
            else:
                c = _check_ratio(c.id, title, line, defs[letters[0]], defs[letters[1]])
            out.append(c)
        elif TOTAL.match(line.own) and len(since) >= 2:
            c = Check(f"capitalisation_rows:{line.page}:{n}", ARITH, f"{clean[:70]} is the sum of the rows above it (page {line.page})", None)
            c.left, c.right = [line.term()], [x.term() for x in since]
            out.append(decide(c))
        elif not defined and (pair := _ratio_parts(line.label)) and pair[0] in named and pair[1] in named:
            out.append(_check_ratio(f"capitalisation_ratio:{line.page}:{n}", f"{clean[:70]} is {pair[0]} over {pair[1]} (page {line.page})", line, named[pair[0]], named[pair[1]]))
        if defined:
            defs[defined] = line
        if _name(line.own):
            named[_name(line.own)] = line
        if TOTAL.match(line.own):
            since = []
        elif not re.match(r"ratio\b", line.own, re.IGNORECASE):
            since.append(line)
    return out


# (what the row's own label starts with, the line of the restated balance sheet it must equal, whether a difference may be called one). Total equity is only ever confirmed: a draft's capitalisation may state
# equity attributable to the owners, or leave out non-controlling interest or instruments entirely equity in nature, where the balance sheet's total includes them. Share capital and other equity are the same lines.
EQUITY_LINES = (
    (re.compile(r"equity\s+share\s+capital\b", re.IGNORECASE), "equity_capital", "equity share capital", True),
    (re.compile(r"other\s+equity\b", re.IGNORECASE), "other_equity", "other equity", True),
    (re.compile(r"total\s+equity\b", re.IGNORECASE), "total_equity", "total equity", False),
)


def balance_checks(lines: Sequence[Line], found: Sequence[Located], pages: Sequence[tuple[int, str, str]]) -> list[Check]:
    """The equity lines of the capitalisation table against the restated balance sheet of the same date."""
    text = SPLIT_YEAR.sub(r"\1\2", " ".join(t for _, s, t in pages if s == SECTION))
    stated_date = STATED_DATE.search(text)  # the first date the section gives is the table's; a later column heading may repeat or differ
    year_found = stated_date if stated_date and stated_date.group(1).lower() == "march" and stated_date.group(2) == "31" else None
    stated = {m.group(1).lower() for m in BASIS.finditer(text)}
    unit = _unit(text)
    out: list[Check] = []
    for pattern, key, name, may_differ in EQUITY_LINES:
        row = next((x for x in lines if pattern.match(MARKS.sub("", x.own).strip())), None)
        if row is None:
            continue
        cid = f"capitalisation_{key}_in_balance_sheet"
        c = Check(cid, DOC_VS_DOC, f"{name} in the capitalisation table (page {row.page}) and in the restated balance sheet", None)
        if not year_found:
            c.reason = "the capitalisation table is not stated as at a March 31 year end, so it is not the balance sheet's date"
            out.append(c)
            continue
        year = int(year_found.group(3))
        c.year, c.title = year, f"{name} as at March 31, {year} in the capitalisation table (page {row.page}) and in the restated balance sheet"
        attempts: list[Check] = []
        for loc in (x for x in found if x.kind == "balance" and year in x.table.years and (not stated or x.table.basis in (*stated, "unspecified"))):
            b = find(loc.table, "balance", key)
            if b is None:
                continue
            t = Check(cid, DOC_VS_DOC, c.title, year)
            if unit and loc.unit and unit != loc.unit:
                t.reason = f"the capitalisation table is in {unit} and the balance sheet on page {loc.table.page} in {loc.unit}"
            else:
                j = loc.table.years.index(year)
                t.left = [row.term(f"{name} (capitalisation)")]
                t.right = [Term(f"{name} (balance sheet, {loc.table.basis}, page {loc.table.page})", b.values[j], b.page, b.printed[j], 0.5 * 10.0 ** -b.decimals[j])]
                decide(t)
            if t.status == "ok":
                attempts = [t]
                break
            attempts.append(t)
        if not attempts:
            c.reason = f"no restated balance sheet with a {name} line for {year} was read" + (f" on a {'/'.join(sorted(stated))} basis" if stated else "")
            out.append(c)
            continue
        t = attempts[0]
        if t.status == "differs" and not may_differ:
            t.status, t.reason = "not_run", f"{t.reason}; the capitalisation table may state {name} differently from the balance sheet (without non-controlling interest, or with instruments entirely equity in nature), so it is not judged"
        out.append(t)
    return out


def capitalisation_checks(pages: Sequence[tuple[int, str, str]], found: Sequence[Located]) -> list[Check]:
    lines = read_lines(pages)
    return formula_checks(lines) + balance_checks(lines, found, pages) if lines else []
