"""Check on the table of outstanding dues to creditors, with no model: the draft prints the dues it owes to micro and small enterprises, to material creditors and to others, with a total, and prints trade payables
in the restated balance sheet as two lines (dues to micro and small enterprises, dues to the other creditors). Both are as at the balance sheet date, so the total of the table must be the two lines of the
balance sheet added, within the rounding of the figures. A draft may define the table more widely than trade payables (capital creditors, say), so a mismatch is never called a difference: it is not run, with the reason."""
from __future__ import annotations

import re
from collections.abc import Sequence

from athena.forensic.checks.document import DOC_VS_DOC, Check, Located, Term, _unit, decide
from athena.forensic.checks.rows import find
from athena.forensic.checks.totals import LITIGATION, read_line

SECTION = "litigation"
HEADING = re.compile(r"outstanding\s+dues\s+to\s+creditors|types?\s+of\s+creditors?|dues\s+to\s+(?:micro|material)", re.IGNORECASE)
STATED_DATE = re.compile(r"(?:as\s+(?:at|on|of)|,)\s*(?:the\s+)?([A-Za-z]+)\s+(\d{1,2})\s*,?\s*(20\d\d)", re.IGNORECASE)
SPLIT_YEAR = re.compile(r"\b(20\d)\s(\d)\b")
LOOKBACK = 2500
MAX_LINES_TO_TOTAL = 25


def creditors_table(pages: Sequence[tuple[int, str, str]]):
    """(total row, text before it) of the first dues-to-creditors table that ends in a total, else None."""
    ordered = sorted(p for p in pages if p[1] == SECTION)
    text = ""
    for page, _, body in ordered:
        text = SPLIT_YEAR.sub(r"\1\2", f"{text}\n{body}")[-6000:]
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if not HEADING.search(line):
                continue
            for later in lines[i + 1 : i + 1 + MAX_LINES_TO_TOTAL]:
                kind, got = read_line(later.strip(), page, LITIGATION)
                if kind == "row" and got.label.lower().startswith("total") and got.second is not None:
                    before = SPLIT_YEAR.sub(r"\1\2", "\n".join(lines[max(0, i - 12) : i + 1 + MAX_LINES_TO_TOTAL]))
                    return got, before
    return None


def creditors_checks(pages: Sequence[tuple[int, str, str]], found: Sequence[Located]) -> list[Check]:
    table = creditors_table(pages)
    if table is None:
        return []
    total, near = table
    cid = "creditors_equal_trade_payables"
    c = Check(cid, DOC_VS_DOC, f"outstanding dues to creditors (page {total.page}) and trade payables in the restated balance sheet", None)
    dates = [m for m in STATED_DATE.finditer(near) if m.group(1).lower() == "march" and m.group(2) == "31"]
    if not dates:
        c.reason = "the table is not stated as at a March 31 year end, so it is not the balance sheet's date"
        return [c]
    year = int(dates[0].group(3))
    c.year, c.title = year, f"outstanding dues to creditors as at March 31, {year} (page {total.page}) and trade payables in the restated balance sheet"
    unit = _unit(near)
    attempts: list[Check] = []
    for loc in (x for x in found if x.kind == "balance" and year in x.table.years):
        msme, other = find(loc.table, "balance", "payables_msme"), find(loc.table, "balance", "payables_other")
        if msme is None or other is None:
            continue
        t = Check(cid, DOC_VS_DOC, c.title, year)
        if unit and loc.unit and unit != loc.unit:
            t.reason = f"the creditors table is in {unit} and the balance sheet on page {loc.table.page} in {loc.unit}"
        else:
            j = loc.table.years.index(year)
            t.left = [Term("dues to creditors (table total)", total.second or 0.0, total.page, total.second_printed, 0.5 * 10.0 ** -total.second_decimals)]
            t.right = [Term(f"{name} (balance sheet, {loc.table.basis}, page {loc.table.page})", r.values[j], r.page, r.printed[j], 0.5 * 10.0 ** -r.decimals[j]) for name, r in (("micro and small enterprises", msme), ("other creditors", other))]
            decide(t)
        if t.status == "ok":
            return [t]
        attempts.append(t)
    if not attempts:
        c.reason = f"the balance sheet's two trade payables lines for {year} were not both read"
        return [c]
    t = attempts[0]
    if t.status == "differs":
        t.status, t.reason = "not_run", f"{t.reason}; the table of dues may be wider or narrower than trade payables (capital creditors, a different basis), so it is not judged"
    return [t]
