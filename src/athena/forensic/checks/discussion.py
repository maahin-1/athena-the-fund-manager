"""Checks on the management's discussion of a draft, with no model: the sentences that state a change in a line of the income statement, and the figures they quote, against the restated statements.

A sentence such as `our total income increased by 5.39% to ₹ 32,128.69 million in Fiscal 2026 from ₹ 30,486.39 million in Fiscal 2025` states three things the code can test: the percentage follows from the two figures
(within the rounding of the three numbers), the direction word agrees with the sign of the change, and each figure is the restated statement's figure for that line and year. A growth rate stated as a compound annual
rate (`grew at a CAGR of 32.68% to ... in Fiscal 2026 from ... in Fiscal 2024`) is tested the same way over the years between. Only sentences of these forms are read; a sentence in another form is not guessed at.

The figures are compared with the restated income statements the checks already read. The discussion is usually on the consolidated figures and a draft may print several statements, so a figure is judged against every
statement that has the year, and a difference is only called one when none of them agrees and the units are the same."""
from __future__ import annotations

import re
from collections.abc import Sequence

from athena.forensic.checks.document import ARITH, DOC_VS_DOC, Check, Located, Term, _unit, decide
from athena.forensic.checks.rows import find

SECTION = "mdna"
NUM = r"\d[\d,]*(?:\.\d+)?"
UNIT = r"(?:million|millions|lakhs?|lacs?|crores?|thousands?)"
SUBJECTS = {
    "total income": "total_income",
    "revenue from operations": "revenue",
    "total expenses": "total_expenses",
    "other income": "other_income",
    "profit before tax": "pbt",
    "profit for the year": "pat",
    "profit for the period": "pat",
    "profit after tax": "pat",
}
SUBJECT_ALTERNATIVES = "|".join(sorted((re.escape(s) for s in SUBJECTS), key=len, reverse=True))
SUBJECT = r"(?P<subj>" + SUBJECT_ALTERNATIVES + r")"
VERB = r"(?P<verb>increased|decreased|grew|declined|reduced|rose|fell)"
DOWN = {"decreased", "declined", "reduced", "fell"}
PCT_HEAD = VERB + r"\s+by\s+(?:approximately\s+|about\s+)?(?P<pct>" + NUM + r")\s?%"
CAGR_HEAD = VERB + r"\s+at\s+(?:a\s+)?(?:CAGR|compound\s+annual\s+growth\s+rate)\s+of\s+(?:approximately\s+|about\s+)?(?P<pct>" + NUM + r")\s?%"


def _fig(v: str, u: str, y: str) -> str:
    return rf"(?:₹|Rs\.?|INR)\s?(?P<{v}>{NUM})\s*(?P<{u}>{UNIT})?\s+(?:for|in|during|as\s+of)\s+(?:the\s+)?(?:Fiscal|Financial\s+Year|Fiscal\s+Year|FY)\s*(?P<{y}>20\d\d)"


def _sentence(head: str, order: str) -> re.Pattern[str]:
    to_from = rf"\s+to\s+{_fig('b', 'ub', 'y1')}\s*,?\s+from\s+{_fig('a', 'ua', 'y0')}"
    from_to = rf"\s+from\s+{_fig('a', 'ua', 'y0')}\s*,?\s+to\s+{_fig('b', 'ub', 'y1')}"
    return re.compile(SUBJECT + r"(?P<gap>(?:(?!" + SUBJECT_ALTERNATIVES + r")[^.]){0,60}?)\s" + head + (to_from if order == "to_from" else from_to), re.IGNORECASE)  # the gap holds no other line's name: the subject is the nearest one to the verb


# What may sit between the line's name and the verb and still mean the line of the whole company: `profit after tax for the year increased`. Anything else (`revenue from operations from our QSR vertical`) names a part.
WHOLE = re.compile(r"^(?:\s*(?:\(\d\))?\s*(?:for|during|in)\s+(?:the\s+)?(?:year|period|(?:Fiscal|Financial\s+Year|FY)\s*20\d\d)(?:\s+ended\s+March\s+31,?\s*20\d\d)?)?\s*(?:\(\d\))?\s*$", re.IGNORECASE)


PATTERNS = tuple((kind, _sentence(head, order)) for kind, head in (("change", PCT_HEAD), ("cagr", CAGR_HEAD)) for order in ("to_from", "from_to"))


def _decimals(printed: str) -> int:
    return len(printed.split(".")[1]) if "." in printed else 0


def _value(printed: str) -> float:
    return float(printed.replace(",", ""))


def _canonical_unit(word: str | None) -> str | None:
    if not word:
        return None
    w = word.lower().rstrip("s")
    return "lakh" if w == "lac" else w


def statement_values(found: Sequence[Located], key: str, year: int):
    """(table, value, printed, decimals, row page) for every restated income statement that has this line for this year."""
    out = []
    for loc in found:
        if loc.kind != "income" or year not in loc.table.years:
            continue
        row = find(loc.table, "income", key)
        if row is not None:
            j = loc.table.years.index(year)
            out.append((loc, row.values[j], row.printed[j], row.decimals[j], row.page))
    return out


def sentences(pages: Sequence[tuple[int, str, str]]):
    """Every sentence of the discussion in one of the forms read: (page, kind, match), each once."""
    seen: set[tuple[str, ...]] = set()
    for page, section, text in sorted(p for p in pages if p[1] == SECTION):
        flat = " ".join(text.split())
        for kind, rx in PATTERNS:
            for m in rx.finditer(flat):
                ident = (kind, m.group("subj").lower(), m.group("y1"), m.group("y0"), m.group("b"), m.group("a"))
                if ident not in seen:
                    seen.add(ident)
                    yield page, kind, m


def _change_check(cid: str, kind: str, m: re.Match[str], page: int) -> Check:
    subject = m.group("subj").lower()
    a, b, pct = m.group("a"), m.group("b"), m.group("pct")
    y0, y1 = int(m.group("y0")), int(m.group("y1"))
    down = m.group("verb").lower() in DOWN
    va, vb = _value(a), _value(b)
    what = "compound annual growth rate" if kind == "cagr" else "change"
    c = Check(cid, ARITH, f"the stated {what} in {subject} from Fiscal {y0} to Fiscal {y1} follows from the two figures (page {page})", y1)
    if va <= 0 or vb <= 0:
        c.reason = "a figure that is nil or negative has no percentage change"
        return c
    ha, hb = 0.5 * 10.0 ** -_decimals(a), 0.5 * 10.0 ** -_decimals(b)
    if kind == "cagr":
        years = y1 - y0
        if years < 1:
            c.reason = "the compound rate is stated over no years"
            return c
        ratio = vb / va
        worked = (ratio ** (1 / years) - 1) * 100
        half = 100 * (ratio ** (1 / years)) / years * (hb / vb + ha / va)  # what the rounding of the two figures does to the rate
    else:
        worked = (vb - va) / va * 100
        half = 100 * (hb / va + vb * ha / va**2)
    stated = -_value(pct) if down else _value(pct)
    c.left = [Term(f"{subject} {what} stated ({'down' if down else 'up'}, page {page})", stated, page, ("-" if down else "") + pct + "%", 0.5 * 10.0 ** -_decimals(pct))]
    c.right = [Term(f"{subject} {what} worked out from {a} and {b}", worked, page, f"{b} against {a}", half)]
    return decide(c)


def _figure_checks(cid: str, subject: str, key: str, year: int, value: float, printed: str, decimals: int, unit: str | None, page: int, found: Sequence[Located]) -> Check:
    c = Check(cid, DOC_VS_DOC, f"{subject} for Fiscal {year} in the discussion (page {page}) and in the restated statement of profit and loss", year)
    options = statement_values(found, key, year)
    if not options:
        c.reason = f"no restated income statement with {subject} for Fiscal {year} was read"
        return c
    attempts: list[Check] = []
    for loc, svalue, p, sdecimals, rpage in options:
        t = Check(cid, DOC_VS_DOC, c.title, year)
        if unit and loc.unit and unit != loc.unit:
            t.reason = f"the discussion is in {unit} and the statement on page {loc.table.page} in {loc.unit}"
        else:
            t.left = [Term(f"{subject} Fiscal {year} (discussion)", value, page, printed, 0.5 * 10.0 ** -decimals)]
            t.right = [Term(f"{subject} Fiscal {year} (statement, {loc.table.basis}, page {rpage})", svalue, rpage, p, 0.5 * 10.0 ** -sdecimals)]
            decide(t)
        if t.status == "ok":
            return t
        attempts.append(t)
    return attempts[0]


def discussion_checks(pages: Sequence[tuple[int, str, str]], found: Sequence[Located]) -> list[Check]:
    out: list[Check] = []
    for n, (page, kind, m) in enumerate(sentences(pages)):
        subject = m.group("subj").lower()
        key = SUBJECTS[subject]
        out.append(_change_check(f"discussion_{kind}:{page}:{n}", kind, m, page))
        if not WHOLE.match(m.group("gap")):  # a vertical, a segment, a customer: its figures are not a line of the statement
            continue
        for label, y, v, u in (("a", m.group("y0"), m.group("a"), m.group("ua")), ("b", m.group("y1"), m.group("b"), m.group("ub"))):
            out.append(_figure_checks(f"discussion_figure:{page}:{n}{label}", subject, key, int(y), _value(v), v, _decimals(v), _canonical_unit(u), page, found))
    return out + table_checks(pages, found)


UNIT_WORDS = {"gaap", "in", "₹", "rs", "inr", "million", "millions", "lakh", "lakhs", "lac", "lacs", "crore", "crores", "mn"}
NAMED = {"total income": "total_income", "revenue from operations": "revenue", "total expenses": "total_expenses", "profit before tax": "pbt", "profit after tax": "pat", "profit for the year": "pat"}


def table_line(label: str) -> str | None:
    """The key of a table row that is a whole line of the income statement by its name alone, with at most footnote letters and a unit after it (`revenue from operations (d)`, `total income (g)`, `revenue from
    operations (₹ million)`); a margin, a growth rate, a part of the business (`... from our QSR vertical`) or a line with a lead-in (`add: profit for the year`) is not."""
    for name, key in NAMED.items():
        for lead in ("", "total "):
            if label.startswith(lead + name) and (lead == "" or name not in ("total income", "total expenses")):
                rest = re.sub(r"[()\[\],.:;]", " ", label[len(lead + name):])
                tokens = rest.split()
                if all(t in UNIT_WORDS or re.fullmatch(r"\d|[a-z]", t) for t in tokens):
                    return key
    return None


def table_checks(pages: Sequence[tuple[int, str, str]], found: Sequence[Located]) -> list[Check]:
    """The key-figure tables of the discussion that the table reader can read, line by line and year by year against the restated income statements, each figure once."""
    from athena.forensic.checks.rows import read_table

    out: list[Check] = []
    seen: set[tuple[str, int, str]] = set()
    for page, section, text in sorted(p for p in pages if p[1] == SECTION):
        table = read_table(page, text)
        if table is None:
            continue
        unit = _unit(text)
        for row in table.rows:
            key = table_line(row.label)
            if key is None:
                continue
            subject = next(n for n, k in NAMED.items() if k == key)
            for j, year in enumerate(table.years):
                ident = (key, year, row.printed[j])
                if j >= len(row.values) or ident in seen:
                    continue
                seen.add(ident)
                out.append(_figure_checks(f"discussion_table:{page}:{key}:{year}", subject, key, year, row.values[j], row.printed[j], row.decimals[j], unit, page, found))
    return out
