"""Rows of a financial statement table, read from the text of a PDF page.

A restated statement in an offer document prints as lines of a label followed by one number per year, with a heading that names the years. This module turns a page into rows (label, one value per
year, the digits printed) and tells which statement the page holds, from what it contains rather than from a title that text extraction sometimes drops. It only reads: it never guesses a value it
cannot see. A page whose columns cannot be read yields no rows, and the checks built on it report themselves as not run.

Numbers: `1,354.39`, `(286.66)` (negative), `-` (nil), with a note-reference column such as `2` ahead of the amounts ignored by taking the last as many numbers as there are years."""
from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?$")
YEAR = re.compile(r"\b(20\d\d)\b")
YEAR_ONLY = re.compile(r"^20\d\d$")
HEADER_START = re.compile(r"as at|year ended|for the year|particulars|note no", re.IGNORECASE)
MIN_YEARS, MAX_YEARS = 2, 5


@dataclass(frozen=True)
class Row:
    label: str  # as printed, lower-cased with white space collapsed
    values: tuple[float, ...]  # one per year column, in the order printed
    decimals: tuple[int, ...]  # digits after the point as printed, per value: the rounding the table carries
    printed: tuple[str, ...]
    page: int


@dataclass(frozen=True)
class Table:
    page: int
    years: tuple[int, ...]  # the fiscal year of each column, as printed in the heading
    basis: str  # "consolidated", "standalone" or "unspecified"
    rows: tuple[Row, ...]


def parse_number(token: str) -> tuple[float, int] | None:
    """(value, decimals printed) of a number token, or None. `-` alone is nil."""
    if token == "-":
        return 0.0, 0
    if not NUMBER.match(token):
        return None
    if token.startswith("(") != token.endswith(")"):  # `(1` and `4,114.03)` are the two halves of one bracketed number broken across the line
        return None
    digits = token.strip("()").lstrip("-")
    if digits.endswith(",") or (len(digits.split(".")[0].replace(",", "")) > 1 and digits.startswith("0")):  # `12,` and `032.79` are pieces of a number broken across a line, not amounts
        return None
    negative = token.startswith("(") or token.lstrip("(").startswith("-")
    body = token.strip("()").lstrip("-").replace(",", "")
    if not body or not body[0].isdigit():
        return None
    value = float(body)
    return (-value if negative else value), (len(body.split(".")[1]) if "." in body else 0)


FISCAL = re.compile(r"\b(20\d\d)\s*[-–]\s*(\d{2})(?!\d)|\b(20\d\d)\b")  # `2025-26` is the year ended March 2026; `March 31, 2026` is 2026


def _fiscal_years(line: str) -> list[int]:
    """The fiscal years named on a line, each as the calendar year in which it ends. A range `2025-26` names 2026 and only when its second part is the next year (not `2019-20 and 2021`)."""
    out = []
    for m in FISCAL.finditer(line):
        if m.group(3):
            out.append(int(m.group(3)))
        elif (int(m.group(1)) + 1) % 100 == int(m.group(2)):
            out.append(int(m.group(1)) + 1)
    return out


def _years(lines: list[str]) -> tuple[int, ...]:
    """The fiscal years named in the column heading: the lines from the first heading word up to the first line of figures."""
    found: list[int] = []
    started = False
    for line in lines:
        if not started and HEADER_START.search(line):
            started = True
        if started:
            if sum(1 for t in line.split() if parse_number(t) is not None and "." in t) >= 2:  # a line of figures ends the heading
                break
            found += _fiscal_years(line)
    seen: list[int] = []
    for y in found:
        if y not in seen:
            seen.append(y)
    return tuple(seen)


def _basis(text: str) -> str:
    head = text[:1500].lower()
    c, s = "consolidated" in head, "standalone" in head
    return "consolidated" if c and not s else "standalone" if s and not c else "unspecified"


def read_table(page: int, text: str) -> Table | None:
    """The table on a page, or None when its year columns cannot be read (fewer than two years named, or a different number of figures on the lines than years)."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    years = _years(lines)
    if not MIN_YEARS <= len(years) <= MAX_YEARS:
        return None
    n = len(years)
    rows: list[Row] = []
    pending: list[str] = []
    for line in lines:
        tokens = _join_one_split(_without_empty_columns(_rejoin_split_numbers(line.split()), n), n)
        trailing = 0
        for token in reversed(tokens):
            if parse_number(token) is None:
                break
            trailing += 1
        figures = tokens[-n:] if trailing >= n else []
        if figures and trailing > n and _figure_like(tokens[-n - 1]):  # one figure more than there are year columns, so a piece of a broken number is among them: which columns the figures belong to is not known
            pending = []
            continue
        if not figures or all(YEAR_ONLY.match(t) for t in figures):  # no figures, or only the years of a heading
            if trailing < len(tokens):  # a line with some words in it may be the start of a label wrapped over lines
                pending = (pending + [line])[-2:]
            continue
        parsed = [parse_number(t) for t in figures]
        own = " ".join(tokens[: len(tokens) - n])
        label = own
        if (not own or own[0].islower() or own[0] == "(") and pending:
            lead = pending[-2:] if len(pending) > 1 and pending[-1][0].islower() else pending[-1:]  # a label wrapped over three lines: its middle fragment starts in lower case too (`... of micro enterprises` / `and` / `small enterprises 6,918.74`)
            label = " ".join(lead + ([own] if own else []))
        rows.append(Row(" ".join(label.lower().split()), tuple(p[0] for p in parsed if p), tuple(p[1] for p in parsed if p), tuple(figures), page))
        pending = []
    return Table(page, years, _basis(text), tuple(rows)) if rows else None


SPLIT_HEAD = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})*,\)?$")  # `4,` : the front of a number whose thousands separator was followed by a space in the extracted text
SPLIT_TAIL = re.compile(r"^\d{3}(?:\.\d+)?\)?$")


SPLIT_POINT_HEAD = re.compile(r"^\(?-?\d[\d,]*\.$")  # `21,002.` : the front of a number broken after its decimal point
SPLIT_POINT_TAIL = re.compile(r"^\d{1,4}\)?$")
SPACED_HEAD = re.compile(r"^\(?-?\d{1,3}$")  # `10` of `10 ,000.00`, `(8` of `(8 .23)`: the front of a number with a space before its comma or point
SPACED_TAIL = re.compile(r"^[,.]\d[\d,]*(?:\.\d+)?\)?$")  # `,000.00` or `.23)`: nothing but the rest of a number starts with a comma or a point
BRACKET_HEAD = re.compile(r"^\(\d{1,3}$")  # `(1` of `(1 79.65)`
BRACKET_TAIL = re.compile(r"^\d[\d,]*(?:\.\d+)?\)$")  # `79.65)`: an open bracket and a closing one with no bracket of its own make one number


def _rejoin_split_numbers(tokens: list[str]) -> list[str]:
    """Text extraction sometimes breaks a number after its thousands comma (`4, 032.79`) or its decimal point (`21,002. 68`). The two pieces are one number when the first ends in the comma or the point and the
    second is three digits (with any decimals) or the decimals."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and SPLIT_HEAD.match(tokens[i]) and SPLIT_TAIL.match(tokens[i + 1]) and not tokens[i].endswith(")"):
            out.append(tokens[i] + tokens[i + 1])
            i += 2
        elif i + 1 < len(tokens) and SPLIT_POINT_HEAD.match(tokens[i]) and SPLIT_POINT_TAIL.match(tokens[i + 1]):
            out.append(tokens[i] + tokens[i + 1])
            i += 2
        elif i + 1 < len(tokens) and ((SPACED_HEAD.match(tokens[i]) and SPACED_TAIL.match(tokens[i + 1])) or (BRACKET_HEAD.match(tokens[i]) and BRACKET_TAIL.match(tokens[i + 1]))):
            out.append(tokens[i] + tokens[i + 1])
            i += 2
        else:
            out.append(tokens[i])
            i += 1
    return out


SPLIT_INT = re.compile(r"^\d{1,2}$")
SPLIT_REST = re.compile(r"^\d{1,2}\.\d{2}\)?$")


def _join_one_split(tokens: list[str], n: int) -> list[str]:
    """A row with exactly one figure too many for its `n` year columns, where one pair after the first figure is a one or two digit integer and then a figure of the form `d.dd` (`110.07 21 9.08`, the number 219.08 broken
    by a space): the pair is one number when it is the only such pair and the result has no more than three digits before the point. Anywhere else a lone small integer is a note number and is left alone."""
    run = 0
    while run < len(tokens) and parse_number(tokens[-1 - run]) is not None:
        run += 1
    if run != n + 1:
        return tokens
    start = len(tokens) - run
    candidates = [i for i in range(start + 1, len(tokens) - 1) if SPLIT_INT.match(tokens[i]) and SPLIT_REST.match(tokens[i + 1]) and len(tokens[i]) + len(tokens[i + 1].split(".")[0].lstrip("(")) <= 3]
    if len(candidates) != 1:
        return tokens
    i = candidates[0]
    if not all(_figure_like(t) for j, t in enumerate(tokens[start:], start) if j not in (i, i + 1)):  # amounts print with two decimals, so a bare integer elsewhere in the run is a note number and the row is not understood
        return tokens
    return [*tokens[:i], tokens[i] + tokens[i + 1], *tokens[i + 2 :]]


def _figure_like(token: str) -> bool:
    """A token printed like an amount (two decimals or more), as against a note number (`20`, `2.1`) that sits in front of the figures of a row."""
    parsed = parse_number(token)
    return parsed is not None and parsed[1] >= 2


def _without_empty_columns(tokens: list[str], n: int) -> list[str]:
    """Some tables print a second block of nil columns (`-  -  -`) after the figures on subtotal rows. The block is as wide as the figures, and is dropped when `n` figures come before it, so the last `n`
    numbers are the figures. A shorter run of dashes is a nil year (`20 240.10 0.10 -`: a note number, two figures and a nil), not a block, and stays."""
    k = 0
    while k < len(tokens) and tokens[-1 - k] == "-":
        k += 1
    rest = tokens[: len(tokens) - k]
    if k == n and len(rest) >= n and all(parse_number(t) is not None for t in rest[-n:]) and any(t != "-" for t in rest[-n:]):
        return rest
    return tokens


ALIASES: dict[str, dict[str, re.Pattern[str]]] = {
    "balance": {
        "total_assets": re.compile(r"\btotal assets\b"),
        "total_equity_liabilities": re.compile(r"\btotal equity (?:and|&) liabilities\b"),
        "total_equity": re.compile(r"\btotal equity\b(?!\s*(?:and|&))"),
        "non_current_assets": re.compile(r"\btotal non(?:\s*-\s*|\s+)?current assets\b"),  # `non-current`, `non current`, `noncurrent`, `non - current`
        "current_assets": re.compile(r"\btotal current assets\b"),
        "non_current_liabilities": re.compile(r"\btotal non(?:\s*-\s*|\s+)?current liabilities\b"),
        "current_liabilities": re.compile(r"\btotal current liabilities\b"),
        "equity_capital": re.compile(r"\bequity share capital\b"),
        "other_equity": re.compile(r"\bother equity\b"),
        "cash": re.compile(r"^cash and cash equivalents\b"),  # not "bank balances other than cash and cash equivalents"
        "payables_msme": re.compile(r"\boutstanding dues of micro\b|\btrade payables\s*-\s*micro\b"),  # trade payables are printed as two lines, not a total
        "payables_other": re.compile(r"\boutstanding dues of creditors?\s+others?\b|\btrade payables\s*-\s*other than\b"),
    },
    "income": {
        "revenue": re.compile(r"\brevenue from operations\b"),
        "other_income": re.compile(r"\bother income\b(?! for the year)(?!.*comprehensive)"),
        "total_income": re.compile(r"\btotal income\b"),
        "total_expenses": re.compile(r"\btotal expenses\b"),
        "depreciation": re.compile(r"\bdepreciation and amorti[sz]ation expense\b"),
        "pbt": re.compile(r"\bprofit before (?:exceptional items and )?tax\b"),
        "pat": re.compile(r"\bprofit for the (?:year|period)\b|\bprofit after tax\b"),
        "tax_current": re.compile(r"^current tax\b"),
        "tax_deferred": re.compile(r"^deferred tax\b"),
        "tax_earlier": re.compile(r"tax(?:ation)?(?: expense| adjustments?)? (?:for|relating to|of|pertaining to|in respect of) (?:the )?earlier"),
    },
    "cashflow": {
        "cfo": re.compile(r"\bnet cash (?:flow )?(?:from|generated from|\(?used in\)?)?.{0,30}operating activities\b"),
        "cfi": re.compile(r"\bnet cash (?:flow )?(?:from|generated from|\(?used in\)?)?.{0,30}investing activities\b"),
        "cff": re.compile(r"\bnet cash (?:flow )?(?:from|generated from|\(?used in\)?)?.{0,30}financing activities\b"),
        "net_change": re.compile(r"\bnet (?:increase|decrease|change).{0,25}in cash (?:and|&) cash equivalents?\b"),
        "cash_begin": re.compile(r"\bcash (?:(?:and|&) cash )?equivalents?\s+(?:as at\s+|at\s+)?(?:the\s+)?(?:beginning|opening)\b"),  # `cash and cash equivalent(s) at the beginning`, `cash equivalents as at the opening`
        "cash_end": re.compile(r"\bcash (?:(?:and|&) cash )?equivalents?\s+(?:as at\s+|at\s+)?(?:the\s+)?(?:end|closing)\b"),
        "pbt": re.compile(r"^(?:\(?[a-z]\)? )?profit before tax\b"),
        "depreciation": re.compile(r"^depreciation and amorti[sz]ation expense\b"),
    },
}


CASHFLOW_MARKERS = ("adjustments for", "working capital", "operating activities", "investing activities", "financing activities")


def classify(table: Table) -> str | None:
    """Which statement a table is, from the rows it holds: "balance", "income" or "cashflow"; None when it is none of them (or holds several kinds of rows)."""
    hits = {kind: sum(1 for row in table.rows for pat in pats.values() if pat.search(row.label)) for kind, pats in ALIASES.items()}
    if hits["cashflow"] >= 2 and any("operating activities" in r.label for r in table.rows):
        return "cashflow"
    if any(marker in r.label for r in table.rows for marker in CASHFLOW_MARKERS):  # a page of adjustments to profit lists income and expense lines but is not an income statement
        return "cashflow" if hits["cashflow"] >= 2 else None
    if hits["balance"] >= 3:
        return "balance"
    structural = any(ALIASES["income"][k].search(r.label) for r in table.rows for k in ("revenue", "total_income", "total_expenses"))  # profit before tax, depreciation and other income also list in a cash flow statement's adjustments
    if hits["income"] >= 3 and structural:
        return "income"
    return None


def find(table: Table, kind: str, key: str) -> Row | None:
    """The one row of a table that is `key` for a statement of `kind`; None when none or more than one matches (a label printed twice is ambiguous, and no value is guessed)."""
    pattern = ALIASES[kind][key]
    matches = [r for r in table.rows if pattern.search(r.label)]
    distinct = {r.values for r in matches}
    return matches[0] if len(distinct) == 1 else None
