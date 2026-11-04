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
from typing import Any

from athena.forensic.checks.rows import ALIASES, Row, Table, classify, find, read_table

STATEMENT_SECTIONS = ("summary_financials", "financials")
EPS = 1e-9
UNIT = re.compile(r"(?:in|rs\.?|₹|inr)\s*\.?\s*(thousands?|lakhs?|lacs?|millions?|crores?)", re.IGNORECASE)
UNIT_FACTORS = (10.0, 100.0, 1000.0, 10000.0)  # a ratio near one of these between two prints of one figure is a unit, not a difference
NOT_BUILT = (
    "shareholding rows summing to 100% and share counts summing to the shares before the offer",
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
    for page, section, text in pages:
        if section not in STATEMENT_SECTIONS:
            continue
        if page in unreadable:
            notes.append(f"page {page} has no readable text")
            continue
        table = read_table(page, text)
        kind = classify(table) if table else None
        if table and kind:
            found.append(Located(section, table, kind, _unit(text)))
    return found, notes


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
        out.append(c)
    return out


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


def statement_checks(found: Sequence[Located]) -> list[Check]:
    out: list[Check] = []
    for loc in found:
        for spec in ARITH_SPECS[loc.kind]:
            out += _arith(loc, spec)
    # the same restated figure wherever the document prints it: the first table of a kind and basis is the anchor, the others are compared to it
    for kind, keys in CROSS_KEYS.items():
        by_basis: dict[str, list[Located]] = {}
        for loc in found:
            if loc.kind == kind:
                by_basis.setdefault(loc.table.basis, []).append(loc)
        for group in by_basis.values():
            anchor = next((g for g in group if g.section == "financials"), group[0])
            for other in group:
                if other is not anchor and other.table.page != anchor.table.page:
                    for key in keys:
                        out += _cross(anchor, other, key, kind)
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


def run_checks(pages: Sequence[tuple[int, str, str]], figures: Mapping[str, Any] | None, unreadable: Sequence[int] = ()) -> dict[str, Any]:
    """Every check, the whole result as one JSON-able structure. `checks.N.left.M.value` is the path of a number in it."""
    found, notes = locate(pages, unreadable)
    checks = statement_checks(found) + offer_checks(figures)
    if not found:
        checks.insert(0, Check("statements_found", ARITH, "restated statements in the summary and financial sections", None, reason="no statement table could be read (the sections were not found, or the pages carry no readable year columns)"))
    counts = {s: sum(1 for c in checks if c.status == s) for s in ("ok", "differs", "not_run")}
    return {"checks": [c.as_dict() for c in checks], "summary": {"run": counts["ok"] + counts["differs"], **counts, "statement_tables_read": len(found)}, "notes": notes, "not_built": list(NOT_BUILT)}
