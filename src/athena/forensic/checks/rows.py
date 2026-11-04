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
    negative = token.startswith("(") or token.lstrip("(").startswith("-")
    body = token.strip("()").lstrip("-").replace(",", "")
    if not body or not body[0].isdigit():
        return None
    value = float(body)
    return (-value if negative else value), (len(body.split(".")[1]) if "." in body else 0)


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
            found += [int(y) for y in YEAR.findall(line)]
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
        tokens = _without_empty_columns(line.split(), n)
        trailing = 0
        for token in reversed(tokens):
            if parse_number(token) is None:
                break
            trailing += 1
        figures = tokens[-n:] if trailing >= n else []
        if not figures or all(YEAR_ONLY.match(t) for t in figures):  # no figures, or only the years of a heading
            if trailing < len(tokens):  # a line with some words in it may be the start of a label wrapped over lines
                pending = (pending + [line])[-2:]
            continue
        parsed = [parse_number(t) for t in figures]
        own = " ".join(tokens[: len(tokens) - n])
        label = own
        if (not own or own[0].islower() or own[0] == "(") and pending:
            label = " ".join(pending[-1:] + ([own] if own else []))
        rows.append(Row(" ".join(label.lower().split()), tuple(p[0] for p in parsed if p), tuple(p[1] for p in parsed if p), tuple(figures), page))
        pending = []
    return Table(page, years, _basis(text), tuple(rows)) if rows else None


def _without_empty_columns(tokens: list[str], n: int) -> list[str]:
    """Some tables print a second block of nil columns (`-  -  -`) after the figures on subtotal rows. They are dropped when the figures come before them, so the last `n` numbers are the figures."""
    k = 0
    while k < len(tokens) and tokens[-1 - k] == "-":
        k += 1
    rest = tokens[: len(tokens) - k]
    if 0 < k <= n and len(rest) >= n and all(parse_number(t) is not None for t in rest[-n:]) and any(t != "-" for t in rest[-n:]):
        return rest
    return tokens


ALIASES: dict[str, dict[str, re.Pattern[str]]] = {
    "balance": {
        "total_assets": re.compile(r"\btotal assets\b"),
        "total_equity_liabilities": re.compile(r"\btotal equity (?:and|&) liabilities\b"),
        "total_equity": re.compile(r"\btotal equity\b(?!\s*(?:and|&))"),
        "non_current_assets": re.compile(r"\btotal non[- ]?current assets\b"),
        "current_assets": re.compile(r"\btotal current assets\b"),
        "non_current_liabilities": re.compile(r"\btotal non[- ]?current liabilities\b"),
        "current_liabilities": re.compile(r"\btotal current liabilities\b"),
        "equity_capital": re.compile(r"\bequity share capital\b"),
        "other_equity": re.compile(r"\bother equity\b"),
        "cash": re.compile(r"^cash and cash equivalents\b"),  # not "bank balances other than cash and cash equivalents"
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
        "tax_earlier": re.compile(r"tax adjustment for earlier years|tax (?:expense|adjustments?) (?:relating to|of) earlier"),
    },
    "cashflow": {
        "cfo": re.compile(r"\bnet cash (?:flow )?(?:from|generated from|\(?used in\)?)?.{0,30}operating activities\b"),
        "cfi": re.compile(r"\bnet cash (?:flow )?(?:from|generated from|\(?used in\)?)?.{0,30}investing activities\b"),
        "cff": re.compile(r"\bnet cash (?:flow )?(?:from|generated from|\(?used in\)?)?.{0,30}financing activities\b"),
        "net_change": re.compile(r"\bnet (?:increase|decrease).{0,20}in cash and cash equivalents\b"),
        "cash_begin": re.compile(r"\bcash and cash equivalents at the beginning\b"),
        "cash_end": re.compile(r"\bcash and cash equivalents at the end\b"),
        "pbt": re.compile(r"^(?:\(?[a-z]\)? )?profit before tax\b"),
        "depreciation": re.compile(r"^depreciation and amorti[sz]ation expense\b"),
    },
}


def classify(table: Table) -> str | None:
    """Which statement a table is, from the rows it holds: "balance", "income" or "cashflow"; None when it is none of them (or holds several kinds of rows)."""
    hits = {kind: sum(1 for row in table.rows for pat in pats.values() if pat.search(row.label)) for kind, pats in ALIASES.items()}
    if hits["cashflow"] >= 2 and any("operating activities" in r.label for r in table.rows):
        return "cashflow"
    if hits["balance"] >= 3:
        return "balance"
    if hits["income"] >= 3:
        return "income"
    return None


def find(table: Table, kind: str, key: str) -> Row | None:
    """The one row of a table that is `key` for a statement of `kind`; None when none or more than one matches (a label printed twice is ambiguous, and no value is guessed)."""
    pattern = ALIASES[kind][key]
    matches = [r for r in table.rows if pattern.search(r.label)]
    distinct = {r.values for r in matches}
    return matches[0] if len(distinct) == 1 else None
