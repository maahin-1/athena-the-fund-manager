"""Checks the code runs on an offer document, with no model: relations that must hold between numbers the document prints.

Every check is a relation between printed numbers that is true of any set of accounts (a balance sheet balances, a subtotal is the sum of its parts, the same restated figure is the same wherever the document
prints it, the issue is its fresh part plus its sale part). The tolerance is the rounding the table itself carries: half a unit of the last printed digit for each number used. A difference larger than that is
a difference whatever its cause, and the cause (a typing error, a restatement note, a different basis) is for the text lane or the owner. A check that cannot be run says so and why (a statement not found, columns
not readable, two units, a row printed twice) and never counts as clean. Ratios and anomalies are not here: only relations that must hold can be proven by code.

Statements are read from the summary and financial sections only. What is not built yet is listed in `NOT_BUILT`, so a report never reads as if it had been looked at."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Any

from athena.forensic.checks.rows import ALIASES, Row, Table, classify, find, read_table

STATEMENT_SECTIONS = ("summary_financials", "financials")
EPS = 1e-9
UNIT = re.compile(r"(?:in|rs\.?|₹|inr)\s*\.?\s*(thousands?|lakhs?|lacs?|millions?|crores?)", re.IGNORECASE)
UNIT_FACTORS = (10.0, 100.0, 1000.0, 10000.0)  # a ratio near one of these between two prints of one figure is a unit, not a difference
NOT_BUILT = (
    "litigation category rows summing to their totals",
    "borrowings in the statements against the indebtedness section",
    "restated figures against the management discussion",
)
CROSS_KEYS: dict[str, tuple[str, ...]] = {
    "balance": ("total_assets", "total_equity", "non_current_assets", "current_assets", "non_current_liabilities", "current_liabilities", "cash"),
    "income": ("revenue", "other_income", "total_income", "total_expenses", "pbt", "pat"),
    "cashflow": ("cfo", "cfi", "cff", "cash_end"),
}
LABELS = {
    "total_assets": "total assets", "total_equity_liabilities": "total equity and liabilities", "total_equity": "total equity", "non_current_assets": "total non-current assets", "current_assets": "total current assets",
    "non_current_liabilities": "total non-current liabilities", "current_liabilities": "total current liabilities", "equity_capital": "equity share capital", "other_equity": "other equity", "cash": "cash and cash equivalents",
    "revenue": "revenue from operations", "other_income": "other income", "total_income": "total income", "total_expenses": "total expenses", "depreciation": "depreciation and amortisation", "pbt": "profit before tax",
    "pat": "profit for the year", "tax_current": "current tax", "tax_deferred": "deferred tax", "tax_earlier": "tax adjustment for earlier years", "cfo": "net cash from operating activities",
    "cfi": "net cash from investing activities", "cff": "net cash from financing activities", "net_change": "net increase or decrease in cash", "cash_begin": "cash at the beginning of the year", "cash_end": "cash at the end of the year",
}


@dataclass(frozen=True)
class Term:
    name: str
    value: float
    page: int
    printed: str
    half_unit: float  # the rounding of the printed figure: half of its last digit

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "page": self.page, "printed": self.printed}


@dataclass
class Check:
    id: str
    type: str  # arithmetic | document_vs_document
    title: str
    year: int | None
    relation: str = "equal"
    left: list[Term] = field(default_factory=list)
    right: list[Term] = field(default_factory=list)
    status: str = "not_run"  # ok | differs | not_run
    reason: str = ""

    @property
    def tolerance(self) -> float:
        return sum(t.half_unit for t in (*self.left, *self.right)) + EPS

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "title": self.title, "year": self.year, "relation": self.relation, "status": self.status, "reason": self.reason, "tolerance": round(self.tolerance, 6),
                "left": [t.as_dict() for t in self.left], "right": [t.as_dict() for t in self.right]}


def _term(name: str, row: Row, column: int) -> Term:
    return Term(name, row.values[column], row.page, row.printed[column], 0.5 * 10.0 ** -row.decimals[column])


def _unit(text: str) -> str | None:
    found = {m.group(1).lower().rstrip("s").replace("lac", "lakh") for m in UNIT.finditer(text[:2500])}
    return found.pop() if len(found) == 1 else None


def decide(check: Check) -> Check:
    """Set the status from the numbers: ok, differs, or not run when the values are within the table's own rounding of zero."""
    left, right = sum(t.value for t in check.left), sum(t.value for t in check.right)
    tol = check.tolerance
    if max(abs(left), abs(right)) < 4 * tol:
        check.status, check.reason = "not_run", "the figures are within the rounding of the table, so a difference means nothing"
        return check
    gap = left - right
    violated = abs(gap) > tol if check.relation == "equal" else gap > tol if check.relation == "at_most" else -gap > tol
    check.status = "differs" if violated else "ok"
    if violated:
        check.reason = f"left {left:,.2f} against right {right:,.2f}; rounding in the table allows {tol:,.2f}"
        big, small = max(abs(left), abs(right)), min(abs(left), abs(right))
        if check.type == DOC_VS_DOC and small and any(abs(big / small - f) / f < 0.01 for f in UNIT_FACTORS):
            check.status, check.reason = "not_run", "the two figures differ by about 10, 100, 1000 or 10000 times: more likely two units than two values"
    return check


DOC_VS_DOC = "document_vs_document"
ARITH = "arithmetic"


@dataclass(frozen=True)
class Located:
    section: str
    table: Table
    kind: str
    unit: str | None


def locate(pages: Sequence[tuple[int, str, str]], unreadable: Sequence[int] = ()) -> tuple[list[Located], list[str]]:
    """The statement tables found in the summary and financial sections, and why any page in them was not read."""
    found: list[Located] = []
    notes: list[str] = []
    last: Located | None = None  # the statement the previous page belonged to, to which a continuation page may add its rows
    sectioned = any(section in STATEMENT_SECTIONS for _, section, _ in pages)  # when the section finder did not find the financial sections at all, every page is looked at: a page is a statement by what it holds
    if not sectioned:
        notes.append("the financial sections were not found in the document, so every page was looked at for statements")
    for page, section, text in pages:
        if sectioned and section not in STATEMENT_SECTIONS:
            last = None
            continue
        if page in unreadable:
            notes.append(f"page {page} has no readable text")
            last = None
            continue
        table = read_table(page, text)
        kind = classify(table) if table else None
        if table and last is not None and _continues(last, table, kind, page, section):
            merged = Located(last.section, Table(last.table.page, last.table.years, last.table.basis, last.table.rows + table.rows), last.kind, last.unit)
            found[found.index(last)] = merged
            last = merged
            continue
        if table and kind:
            last = Located(section, table, kind, _unit(text))
            found.append(last)
        else:
            last = None
    return found, notes


MAX_CONTINUATION_ROWS = 8


def _continues(prev: Located, table: Table, kind: str | None, page: int, section: str) -> bool:
    """A statement often runs onto the next page (assets on one, equity and liabilities on the next; or a closing total alone on a short page). The next page continues it when it follows directly, in the
    same section, with the same year columns and either the same kind of statement or no kind at all and only a few rows, and a basis that is the same or not stated."""
    last_page = max(r.page for r in prev.table.rows)
    if page != last_page + 1 or section != prev.section or table.years != prev.table.years:
        return False
    if table.basis not in (prev.table.basis, "unspecified"):
        return False
    return kind == prev.kind or (kind is None and len(table.rows) <= MAX_CONTINUATION_ROWS)


def _row(loc: Located, key: str) -> Row | None:
    if key in ALIASES[loc.kind]:
        return find(loc.table, loc.kind, key)
    return None


def _arith(loc: Located, spec: tuple[str, str, Sequence[str], Sequence[str], str]) -> list[Check]:
    """One check per year column: id, title, left keys, right keys, relation."""
    cid, title, left, right, relation = spec
    out = []
    for j, year in enumerate(loc.table.years):
        c = Check(f"{cid}@{loc.table.page}:{year}", ARITH, f"{title}, page {loc.table.page}, {loc.table.basis} FY{year}", year, relation)
        rows = {k: _row(loc, k) for k in (*left, *right)}
        missing = [LABELS.get(k, k) for k, r in rows.items() if r is None]
        if missing:
            c.reason = f"not found on the page (or printed more than once): {', '.join(missing)}"
        else:
            c.left = [_term(LABELS.get(k, k), rows[k], j) for k in left]  # type: ignore[arg-type]
            c.right = [_term(LABELS.get(k, k), rows[k], j) for k in right]  # type: ignore[arg-type]
            decide(c)
            if c.status == "differs" and cid in OPEN_RELATIONS:
                c = _with_unnamed_lines(loc, c, [r for r in rows.values() if r is not None], j)
        out.append(c)
    return out


# Relations whose statement can carry lines the relation does not name (instruments entirely equity in nature or non-controlling interest between share capital and total equity; exceptional items or a share of an
# associate's profit between expenses and profit; an exchange effect between the net change and the closing cash). A difference there is believed only when no such line sits between the terms.
OPEN_RELATIONS = frozenset({"equity_adds_up", "profit_before_tax", "profit_after_tax", "cash_rolls_forward"})
MAX_UNNAMED_ROWS = 4
MAX_UNNAMED_USED = 3


def _with_unnamed_lines(loc: Located, check: Check, used: Sequence[Row], column: int) -> Check:
    """The check as it stands when no unnamed line sits between its terms; otherwise the lines are tried (each in or out, either sign) and the check holds if some set reconciles it, and is not run when none does:
    the relation as stated is then incomplete, so a difference would be a claim about numbers that are not all in it."""
    rows = loc.table.rows
    where = sorted(i for r in used for i, x in enumerate(rows) if x is r)
    named = {id(r) for r in used}
    extras = [x for x in rows[where[0] + 1 : where[-1]] if id(x) not in named and x.values[column] != 0] if len(where) >= 2 else []
    if not extras:
        return check
    names = "; ".join(f"{x.label[:50]} ({x.printed[column]})" for x in extras[:MAX_UNNAMED_ROWS])
    if len(extras) > MAX_UNNAMED_ROWS:
        check.status, check.reason = "not_run", f"{len(extras)} lines the relation does not name sit between its terms ({names}, ...); not judged"
        return check
    for size in range(1, min(len(extras), MAX_UNNAMED_USED) + 1):
        for combo in combinations(extras, size):
            for signs in product((1, -1), repeat=size):
                trial = Check(check.id, check.type, check.title, check.year, check.relation, list(check.left), list(check.right) + [
                    Term(f"{x.label[:40]} (a line the relation does not name)", s * x.values[column], x.page, x.printed[column], 0.5 * 10.0 ** -x.decimals[column]) for x, s in zip(combo, signs)
                ])
                decide(trial)
                if trial.status == "ok":
                    trial.reason = f"holds with {', '.join(x.label[:40] for x in combo)} included"
                    return trial
    check.status, check.reason = "not_run", f"lines the relation does not name sit between its terms ({names}) and no way of including them reconciles the figures; not judged"
    return check


ARITH_SPECS: dict[str, tuple[tuple[str, str, Sequence[str], Sequence[str], str], ...]] = {
    "balance": (
        ("balance_sheet_balances", "total assets equal total equity and liabilities", ("total_assets",), ("total_equity_liabilities",), "equal"),
        ("assets_add_up", "total assets are non-current plus current assets", ("total_assets",), ("non_current_assets", "current_assets"), "equal"),
        ("liabilities_add_up", "total equity and liabilities are equity plus non-current plus current liabilities", ("total_equity_liabilities",), ("total_equity", "non_current_liabilities", "current_liabilities"), "equal"),
        ("equity_adds_up", "total equity is share capital plus other equity", ("total_equity",), ("equity_capital", "other_equity"), "equal"),
    ),
    "income": (
        ("total_income_adds_up", "total income is revenue plus other income", ("total_income",), ("revenue", "other_income"), "equal"),
        ("profit_before_tax", "profit before tax is total income less total expenses", ("pbt", "total_expenses"), ("total_income",), "equal"),
        ("profit_after_tax", "profit for the year is profit before tax less the tax lines", ("pat", "tax_current", "tax_deferred", "tax_earlier"), ("pbt",), "equal"),
    ),
    "cashflow": (
        ("net_change_adds_up", "net change in cash is operating plus investing plus financing flows", ("net_change",), ("cfo", "cfi", "cff"), "equal"),
        ("cash_rolls_forward", "cash at the end is cash at the beginning plus the net change", ("cash_end",), ("cash_begin", "net_change"), "equal"),
    ),
}
def _cross(a: Located, b: Located, key: str, kind: str) -> list[Check]:
    out = []
    for ja, year in enumerate(a.table.years):
        if year not in b.table.years:
            continue
        jb = b.table.years.index(year)
        c = Check(f"{key}_same_in_{a.table.page}_and_{b.table.page}:{year}", DOC_VS_DOC, f"{LABELS.get(key, key)} FY{year} on page {a.table.page} and page {b.table.page} ({a.table.basis})", year)
        ra, rb = _row(a, key), _row(b, key)
        if ra is None or rb is None:
            continue  # a line only one of the two pages carries is not a comparison
        if a.unit and b.unit and a.unit != b.unit:
            c.reason = f"the two pages state different units ({a.unit} and {b.unit})"
        else:
            c.left, c.right = [_term(f"{LABELS.get(key, key)} (page {a.table.page})", ra, ja)], [_term(f"{LABELS.get(key, key)} (page {b.table.page})", rb, jb)]
            decide(c)
        out.append(c)
    return out


MAX_UNSTATED_BASIS_TABLES = 2
UNLIKE_REASON = "the two tables differ in most of their figures, so they are probably two different statements (another entity or basis), not one figure printed twice; not judged"


def _distrust_unlike_tables(pair: list[Check]) -> list[Check]:
    """Two tables that disagree on half or more of the figures they share (at least three) are two statements, not one statement with several typing errors."""
    decided = [c for c in pair if c.status in ("ok", "differs")]
    differing = [c for c in decided if c.status == "differs"]
    if len(differing) >= MISREAD_MIN and len(differing) >= MISREAD_SHARE * len(decided):
        for c in differing:
            c.status, c.reason = "not_run", UNLIKE_REASON
    return pair


MISREAD_REASON ="page probably misread (most of the arithmetic on this statement's pages fails at once, which points to a table read wrongly more than to many errors); not judged"
MISREAD_SHARE = 0.5
MISREAD_MIN = 3  # one wrong figure fails the two or three relations it sits in, which is a finding; three or more failing, and half of those decided, is a page that was read wrongly


def _distrust_misread_pages(checks: list[Check], arithmetic_by_loc: Sequence[list[Check]]) -> None:
    """A statement whose own arithmetic mostly fails (at least three checks, half or more of those decided) is probably misread, not wrong in many places at once: its differences, and any comparison that uses a
    figure from its pages, are turned into "not run" with that reason."""
    bad_pages: set[int] = set()
    for own in arithmetic_by_loc:
        decided = [c for c in own if c.status in ("ok", "differs")]
        differing = [c for c in decided if c.status == "differs"]
        if len(differing) >= MISREAD_MIN and len(differing) >= MISREAD_SHARE * len(decided):
            bad_pages |= {t.page for c in own for t in (*c.left, *c.right)}
    for c in checks:
        if c.status == "differs" and bad_pages & {t.page for t in (*c.left, *c.right)}:
            c.status, c.reason = "not_run", MISREAD_REASON


def statement_checks(found: Sequence[Located]) -> list[Check]:
    out: list[Check] = []
    own_by_loc: list[list[Check]] = []
    for loc in found:
        own: list[Check] = []
        for spec in ARITH_SPECS[loc.kind]:
            own += _arith(loc, spec)
        own_by_loc.append(own)
        out += own
    # the same restated figure wherever the document prints it: the first table of a kind and basis is the anchor, the others are compared to it
    for kind, keys in CROSS_KEYS.items():
        by_basis: dict[str, list[Located]] = {}
        for loc in found:
            if loc.kind == kind:
                by_basis.setdefault(loc.table.basis, []).append(loc)
        for basis, group in by_basis.items():
            if basis == "unspecified" and len(group) > MAX_UNSTATED_BASIS_TABLES:  # with several statements of no stated basis, two may be two entities; they are not compared
                continue
            anchor = next((g for g in group if g.section == "financials"), group[0])
            for other in group:
                if other is not anchor and other.table.page != anchor.table.page:
                    pair: list[Check] = []
                    for key in keys:
                        pair += _cross(anchor, other, key, kind)
                    out += _distrust_unlike_tables(pair)
    # the same figure in two statements: depreciation and profit before tax (income against cash flow), cash at the end (cash flow against balance sheet)
    anchors: dict[tuple[str, str], Located] = {}
    for loc in found:
        k = (loc.kind, loc.table.basis)
        if k not in anchors or (loc.section == "financials" and anchors[k].section != "financials"):
            anchors[k] = loc
    for basis in sorted({b for _, b in anchors}):
        inc, cash, bal = anchors.get(("income", basis)), anchors.get(("cashflow", basis)), anchors.get(("balance", basis))
        for pair, key_a, key_b in ((inc, "depreciation", "depreciation"), (inc, "pbt", "pbt")):
            if pair and cash:
                out += _across(pair, cash, key_a, key_b)
        if cash and bal:
            out += _across(cash, bal, "cash_end", "cash")
    _distrust_misread_pages(out, own_by_loc)
    return out


def _across(a: Located, b: Located, key_a: str, key_b: str) -> list[Check]:
    out = []
    for ja, year in enumerate(a.table.years):
        if year not in b.table.years:
            continue
        jb = b.table.years.index(year)
        ra, rb = _row(a, key_a), _row(b, key_b)
        if ra is None or rb is None:
            continue
        c = Check(f"{key_a}_{a.kind}_vs_{b.kind}:{a.table.page}-{b.table.page}:{year}", DOC_VS_DOC, f"{LABELS.get(key_a, key_a)} FY{year} in the {a.kind} statement (page {a.table.page}) and the {b.kind} statement (page {b.table.page})", year)
        c.left, c.right = [_term(f"{LABELS.get(key_a, key_a)} ({a.kind}, page {a.table.page})", ra, ja)], [_term(f"{LABELS.get(key_b, key_b)} ({b.kind}, page {b.table.page})", rb, jb)]
        out.append(decide(c))
    return out


def offer_checks(figures: Mapping[str, Any] | None) -> list[Check]:
    """The offer's own structure from the figures taken by program: fresh issue plus offer for sale is the offer; the objects with amounts do not exceed the fresh issue."""
    out: list[Check] = []

    def amount(name: str) -> Term | None:
        item = (figures or {}).get(name)
        if isinstance(item, Mapping) and isinstance(item.get("crore"), (int, float)):
            return Term(name.replace("_", " "), float(item["crore"]), int(item.get("page") or 0), str(item.get("printed", "")), 0.005)
        return None

    fresh, sale, total = amount("fresh_issue"), amount("offer_for_sale"), amount("offer_total")
    c = Check("offer_structure", ARITH, "the fresh issue plus the offer for sale is the size of the offer", None)
    if fresh and sale and total:
        c.left, c.right = [fresh, sale], [total]
        decide(c)
    else:
        c.reason = "one of the three amounts is blank ([●]) or was not found in the draft"
    out.append(c)
    objects = [o for o in (figures or {}).get("objects", []) if isinstance(o, Mapping)]
    known = [Term(str(o.get("object", ""))[:60], float(o["crore"]), int((figures or {}).get("offer_total", {}).get("page") or 0), str(o.get("printed", "")), 0.005) for o in objects if isinstance(o.get("crore"), (int, float))]
    c = Check("objects_within_fresh_issue", ARITH, "the objects with an amount do not exceed the fresh issue", None, "at_most")
    if fresh and known:
        c.left, c.right = known, [fresh]
        decide(c)
    else:
        c.reason = "no object has an amount yet, or the fresh issue is blank"
    out.append(c)
    return out


def _statement_score(page: int, text: str) -> int:
    """How many labelled lines a page gives as a statement: zero when it does not read as a balance sheet, income statement or cash flow."""
    table = read_table(page, text)
    return sum(1 for row in table.rows if row.label) if table and classify(table) else 0


def prefer_layout(pages: Sequence[tuple[int, str, str]], layout: Mapping[int, str]) -> tuple[list[tuple[int, str, str]], list[int]]:
    """The pages with, for each page that was also read in layout mode, whichever text gives more labelled lines of a statement (the default text on a tie). Some drafts put a label in one block of
    text and its figures in another, so the default reading order loses the rows; others place text so that layout mode loses the year headings. Neither is better in general, so the page decides."""
    out: list[tuple[int, str, str]] = []
    used: list[int] = []
    for page, section, text in pages:
        placed = layout.get(page)
        if placed and _statement_score(page, placed) > _statement_score(page, text):
            out.append((page, section, placed))
            used.append(page)
        else:
            out.append((page, section, text))
    return out, used


def run_checks(pages: Sequence[tuple[int, str, str]], figures: Mapping[str, Any] | None, unreadable: Sequence[int] = (), layout: Mapping[int, str] | None = None) -> dict[str, Any]:
    """Every check, the whole result as one JSON-able structure. `checks.N.left.M.value` is the path of a number in it."""
    pages, by_position = prefer_layout(pages, layout or {})
    found, notes = locate(pages, unreadable)
    if by_position:
        notes.append(f"{len(by_position)} page(s) were read by the position of the text on the page, which gave more of a statement's lines than the reading order: {', '.join(map(str, by_position[:12]))}{' ...' if len(by_position) > 12 else ''}")
    from athena.forensic.checks.holdings import holding_checks  # holdings builds on this module's Check and Term

    held, held_notes = holding_checks(pages, figures)
    notes.extend(held_notes)
    checks = statement_checks(found) + offer_checks(figures) + held
    if not found:
        checks.insert(0, Check("statements_found", ARITH, "restated statements in the summary and financial sections", None, reason="no statement table could be read (the sections were not found, or the pages carry no readable year columns)"))
    set_aside = [c for c in checks if c.reason == MISREAD_REASON]
    if set_aside:
        pages = sorted({t.page for c in set_aside for t in (*c.left, *c.right)})
        notes = [*notes, f"{len(set_aside)} difference(s) were set aside because the statement on page(s) {', '.join(map(str, pages))} was probably misread: most of its own arithmetic failed at once"]
    counts = {s: sum(1 for c in checks if c.status == s) for s in ("ok", "differs", "not_run")}
    return {"checks": [c.as_dict() for c in checks], "summary": {"run": counts["ok"] + counts["differs"], **counts, "statement_tables_read": len(found)}, "notes": notes, "not_built": list(NOT_BUILT)}
