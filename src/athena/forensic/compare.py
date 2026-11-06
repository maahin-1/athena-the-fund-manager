"""What changed between two versions of the same offer document (a draft and the red herring prospectus that followed it, or an addendum), worked out in code.

    python -m athena.forensic.compare --earlier DOC_ID --later DOC_ID

A change is not wrong: a later version is meant to set the blanks, add a year and answer the regulator. What the owner wants is the list, with the changes that need an explanation set apart: a restated
figure of a year that both versions print, an offer figure that moved, a risk or litigation sentence whose numbers changed, a risk that is no longer there, a check that holds in the earlier version and
differs in the later one. Nothing is judged and nothing is summarised by a model.

- **figures**: the offer figures taken by program (size of the offer, fresh issue, offer for sale, shares before the offer, each object's amount), before and after; a `[●]` that was set is *set*, not a change.
- **statements**: every line of the restated balance sheet, income statement and cash flow that both versions print for the same year, on the same basis and in the same unit, with the lines that changed
  beyond the rounding of the printed figures; years only one version prints are listed as added or dropped.
- **wording**: the sentences of the risk factors and litigation sections that one version has and the other does not, and the pairs that are the same sentence with different numbers.
- **checks**: the document checks (`athena.forensic.checks.document`) that differ in the later version and held or did not differ in the earlier one.

Output is one JSON document (`SCHEMA`)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from athena.contracts import AthenaError
from athena.documents.store import DEFAULT_PATH, DocStore
from athena.forensic.checks.document import locate, prefer_layout, run_checks
from athena.forensic.checks.rows import ALIASES, find

SCHEMA = "athena.forensic.compare/1"
WORDING_SECTIONS = ("risk_factors", "litigation")
MIN_SENTENCE = 70
MAX_LISTED = 40
SENTENCE_SPLIT = re.compile(r"(?<=[.;:])\s+(?=[A-Z“\"(])")
DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _key(sentence: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", sentence.lower())


def _skeleton(sentence: str) -> str:
    return re.sub(r"[^a-z#]+", "", DIGITS.sub("#", sentence.lower()))


def sentences(store: DocStore, doc_id: str, sections: Sequence[str] = WORDING_SECTIONS) -> dict[str, str]:
    """{normalised key: the sentence as printed} for the long sentences of the named sections (a table row or a heading is too short to be a claim)."""
    out: dict[str, str] = {}
    for _, section, text in store.pages(doc_id):
        if section not in sections:
            continue
        for raw in SENTENCE_SPLIT.split(" ".join(text.split())):
            if len(raw) >= MIN_SENTENCE and sum(c.isalpha() for c in raw) > 0.6 * len(raw):
                out.setdefault(_key(raw), raw)
    return out


def wording(store: DocStore, earlier: str, later: str) -> dict[str, Any]:
    a, b = sentences(store, earlier), sentences(store, later)
    only_a, only_b = {k: v for k, v in a.items() if k not in b}, {k: v for k, v in b.items() if k not in a}
    by_skeleton: dict[str, list[str]] = {}
    for sentence in only_a.values():
        by_skeleton.setdefault(_skeleton(sentence), []).append(sentence)
    changed, matched = [], set()
    for sentence in only_b.values():
        pool = by_skeleton.get(_skeleton(sentence), [])
        if pool and DIGITS.search(sentence):
            before = pool.pop(0)
            changed.append({"before": before[:400], "after": sentence[:400]})
            matched.add(_key(before)), matched.add(_key(sentence))
    removed = [v for k, v in only_a.items() if k not in matched]
    added = [v for k, v in only_b.items() if k not in matched]
    return {
        "sections": list(WORDING_SECTIONS), "same": len(a.keys() & b.keys()), "numbers_changed": changed[:MAX_LISTED], "numbers_changed_count": len(changed),
        "only_in_earlier": [s[:400] for s in removed[:MAX_LISTED]], "only_in_earlier_count": len(removed), "only_in_later": [s[:400] for s in added[:MAX_LISTED]], "only_in_later_count": len(added),
    }


def figures(store: DocStore, earlier: str, later: str) -> list[dict[str, Any]]:
    fa, fb = store.figures(earlier) or {}, store.figures(later) or {}
    rows: list[dict[str, Any]] = []

    def amount(f: Mapping[str, Any], name: str) -> tuple[float | None, str]:
        item = f.get(name)
        return (item.get("crore"), str(item.get("printed", ""))) if isinstance(item, Mapping) else (None, "")

    def row(label: str, a: tuple[Any, str], b: tuple[Any, str]) -> None:
        va, vb = a[0], b[0]
        if va is None and vb is None and not a[1] and not b[1]:
            return
        if va is None and vb is not None:
            status = "set"  # a blank [●] that the later version fills in
        elif va is not None and vb is None:
            status = "blank_again" if b[1] else "removed"
        elif va is None:
            status = "same"
        else:
            status = "same" if abs(va - vb) <= 0.005 + 1e-9 else "changed"
        rows.append({"item": label, "earlier": a[1] or va, "later": b[1] or vb, "status": status})

    for name, label in (("offer_total", "size of the offer (crore)"), ("fresh_issue", "fresh issue (crore)"), ("offer_for_sale", "offer for sale (crore)")):
        row(label, amount(fa, name), amount(fb, name))
    row("shares before the offer", (fa.get("pre_offer_shares"), str(fa.get("pre_offer_shares") or "")), (fb.get("pre_offer_shares"), str(fb.get("pre_offer_shares") or "")))
    objects_a = {str(o.get("object", ""))[:60]: o for o in fa.get("objects", []) if isinstance(o, Mapping)}
    objects_b = {str(o.get("object", ""))[:60]: o for o in fb.get("objects", []) if isinstance(o, Mapping)}
    for name in sorted(objects_a.keys() | objects_b.keys()):
        oa, ob = objects_a.get(name), objects_b.get(name)
        if oa is None or ob is None:
            rows.append({"item": f"object: {name}", "earlier": (oa or {}).get("printed", ""), "later": (ob or {}).get("printed", ""), "status": "added" if oa is None else "removed"})
        else:
            row(f"object: {name}", (oa.get("crore"), str(oa.get("printed", ""))), (ob.get("crore"), str(ob.get("printed", ""))))
    return rows


def statement_values(store: DocStore, doc_id: str) -> tuple[dict[tuple[str, str, str, int], tuple[float, str, float]], dict[tuple[str, str], str | None]]:
    """{(kind, basis, line, year): (value, printed, rounding)} and {(kind, basis): unit}, from the restated statements of a document (the financial section's table where there are several)."""
    doc = store.document(doc_id)
    pages, _ = prefer_layout(store.pages(doc_id), store.layout(doc_id))
    found, _ = locate(pages, doc["unreadable"] if doc else ())
    values: dict[tuple[str, str, str, int], tuple[float, str, float]] = {}
    units: dict[tuple[str, str], str | None] = {}
    for loc in sorted(found, key=lambda x: x.section != "financials"):  # the financial section's table is the anchor
        units.setdefault((loc.kind, loc.table.basis), loc.unit)
        for key in ALIASES[loc.kind]:
            row = find(loc.table, loc.kind, key)
            if row is None:
                continue
            for j, year in enumerate(loc.table.years):
                values.setdefault((loc.kind, loc.table.basis, key, year), (row.values[j], row.printed[j], 0.5 * 10.0 ** -row.decimals[j]))
    return values, units


def statements(store: DocStore, earlier: str, later: str) -> dict[str, Any]:
    va, ua = statement_values(store, earlier)
    vb, ub = statement_values(store, later)
    same = 0
    changed: list[dict[str, Any]] = []
    skipped: list[str] = []
    for ident in sorted(va.keys() & vb.keys()):
        kind, basis, key, year = ident
        if (ua.get((kind, basis)) or None) != (ub.get((kind, basis)) or None):
            note = f"{kind} {basis}: the two versions state different units ({ua.get((kind, basis))} and {ub.get((kind, basis))})"
            if note not in skipped:
                skipped.append(note)
            continue
        (x, px, hx), (y, py, hy) = va[ident], vb[ident]
        if abs(x - y) <= hx + hy + 1e-9:
            same += 1
        else:
            changed.append({"statement": kind, "basis": basis, "line": key, "year": year, "earlier": px, "later": py, "difference": round(y - x, 4)})
    years = lambda v: {(k[0], k[1], k[3]) for k in v}  # noqa: E731
    added = sorted({f"{k} {b} FY{y}" for k, b, y in years(vb) - years(va)})
    dropped = sorted({f"{k} {b} FY{y}" for k, b, y in years(va) - years(vb)})
    return {"same": same, "changed": changed[:MAX_LISTED * 2], "changed_count": len(changed), "years_added": added, "years_dropped": dropped, "not_compared": skipped}


PAGE_WORDS = re.compile(r"\(?\bpages?\s+\d+(?:\s*(?:and|,|-|to)\s*\d+)*\)?|\bin\s+(?:the\s+)?\w+\s+statement\s+\(\)", re.IGNORECASE)


def _identity(title: str) -> str:
    """A check's title without the pages it was found on: the pages move between versions, what is compared does not."""
    return " ".join(PAGE_WORDS.sub("", title).split()).lower()


def _checks(store: DocStore, doc_id: str) -> dict[str, tuple[str, str]]:
    doc = store.document(doc_id)
    result = run_checks(store.pages(doc_id), store.figures(doc_id), doc["unreadable"] if doc else (), store.layout(doc_id))
    out: dict[str, tuple[str, str]] = {}
    for c in result["checks"]:
        key = _identity(c["title"])
        if c["status"] != "not_run" and (key not in out or out[key][0] != "differs"):
            out[key] = (c["status"], c["title"])
    return out


def check_changes(store: DocStore, earlier: str, later: str) -> dict[str, Any]:
    """The checks that run in both versions, by what they compare: those that differ only in the later one, those that differed and now hold, and how many hold in both."""
    a, b = _checks(store, earlier), _checks(store, later)
    return {
        "new_differences": [{"title": t} for k, (st, t) in b.items() if st == "differs" and a.get(k, ("", ""))[0] != "differs"],
        "resolved_differences": [t for k, (st, t) in a.items() if st == "differs" and b.get(k, ("", ""))[0] == "ok"],
        "held_in_both": sum(1 for k, (st, _) in a.items() if st == "ok" and b.get(k, ("", ""))[0] == "ok"),
        "run_in_earlier_only": sum(1 for k in a if k not in b), "run_in_later_only": sum(1 for k in b if k not in a),
    }


def compare(store: DocStore, earlier: str, later: str) -> dict[str, Any]:
    a, b = store.document(earlier), store.document(later)
    if a is None or b is None:
        raise AthenaError(f"no document {earlier if a is None else later}")
    return {
        "schema": SCHEMA, "earlier": {"doc_id": earlier, "company": a["company"], "kind": a["kind"], "pages": a["n_pages"]}, "later": {"doc_id": later, "company": b["company"], "kind": b["kind"], "pages": b["n_pages"]},
        "figures": figures(store, earlier, later), "statements": statements(store, earlier, later), "wording": wording(store, earlier, later), "checks": check_changes(store, earlier, later),
        "note": "A change is not an error: a later version sets blanks, adds a year and answers the regulator. These are the places to read, found by code; none is judged. Not investment advice.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--earlier", required=True)
    parser.add_argument("--later", required=True)
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    try:
        out = compare(DocStore(args.db, read_only=True), args.earlier, args.later)
    except AthenaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(out, indent=1, ensure_ascii=False, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
