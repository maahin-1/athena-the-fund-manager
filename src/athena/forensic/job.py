"""The forensic job: one draft offer document in, one report document out. It is what the terminal's Analyse button runs.

    python -m athena.forensic.job --url https://www.sebi.gov.in/.../x.pdf --company "Name Limited" --kind DRHP

The address must be a PDF on the regulator's site. The document is read once and kept (an address already held is not downloaded again), the checks lane runs through the supervisor with no model call, and
the answer is ONE JSON document on standard output, always the same shape (`SCHEMA`), whether it worked or not:

    {"schema": "athena.forensic.report/1", "ok": true, "mode": "checks", "generated_at": "...", "document": {...what was read and what was not...}, "concern": {...range...},
     "flags": [{...a difference, with where it is on the page and what innocent explanations there are...}], "checks": {"summary": {...}, "groups": [...], "differences": [...], "not_run": [...], "notes": [...]},
     "not_examined": [...], "limits": [...], "narrative": "...", "caveat": "...", "cost": {...}}
    {"schema": "athena.forensic.report/1", "ok": false, "error": "a short message for the owner"}

Nothing in it is a recommendation. A check that could not run is listed with its reason and is never counted as clean; the concern level is the range `athena.forensic.scoring` computes from the flags."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from athena.contracts import AthenaError
from athena.documents.fetch import fetch_pdf
from athena.documents.read import coverage, ingest
from athena.documents.store import DEFAULT_PATH as DOCS_PATH, DocStore
from athena.forensic.checks.document import NOT_BUILT, run_checks
from athena.forensic.checks.lane import checks_tool, run_code_lane
from athena.forensic.model import Case
from athena.forensic.store import DEFAULT_PATH as FORENSIC_PATH, ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.text.lane import DocData, document_tools
from athena.forensic.tools import base_registry

SCHEMA = "athena.forensic.report/1"
HOSTS = ("www.sebi.gov.in", "sebi.gov.in")
MAX_URL = 400
MAX_NAME = 120
MAX_DIFFERENCES = 40
MAX_NOT_RUN_REASONS = 12
KINDS = ("DRHP", "RHP", "Prospectus", "Addendum", "Document")
GROUPS = (
    ("holdings", ("holding_",)),
    ("litigation", ("litigation_",)),
    ("capitalisation", ("capitalisation_",)),
    ("creditors", ("creditors_",)),
    ("discussion", ("discussion_",)),
    ("offer", ("offer_structure", "objects_within_fresh_issue")),
)
GROUP_TITLES = {
    "statements": "Restated statements: balance sheet, income, cash flow, and the same figure in every place",
    "holdings": "Shareholding tables: holders add up to their totals",
    "litigation": "Litigation tables: numbers and amounts add up",
    "capitalisation": "Capitalisation table: formulas, ratios and equity against the balance sheet",
    "creditors": "Dues to creditors against trade payables",
    "discussion": "Management's discussion against the restated statements",
    "offer": "The offer's own structure",
}
LIMITS = (
    "The checks compare numbers the document prints with each other. They do not tell whether the numbers are true, and a figure wrong in every place it is printed is not seen.",
    "A check that could not be run is listed with the reason. It is not a clean result.",
    "A difference is a place to look, with the innocent explanations that are possible. It is not a finding of wrongdoing and not investment advice.",
    "Page numbers are the position in the PDF, which can differ from the number printed on the page.",
)
CAVEAT = "A stylized analytical framework for the owner. Differences are things to examine, not findings of wrongdoing, and not investment advice."


def error_document(message: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "ok": False, "error": message[:240]}


def validate_url(url: str) -> str:
    """The address, when it is an https PDF on the regulator's site; otherwise an `AthenaError`. The job downloads whatever this lets through, so it is narrow."""
    text = (url or "").strip()
    if not text or len(text) > MAX_URL or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise AthenaError("the address of the document is missing or malformed")
    parsed = urlparse(text)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise AthenaError("only a document on the regulator's site (https://www.sebi.gov.in) can be analysed")
    if not parsed.path.lower().endswith(".pdf"):
        raise AthenaError("the address is not a PDF")
    return text


def clean_name(value: str, default: str) -> str:
    text = " ".join((value or "").split())[:MAX_NAME]
    return text if text and re.fullmatch(r"[\w &.,'()\-/]+", text) else default


def group_of(check_id: str) -> str:
    for name, prefixes in GROUPS:
        if check_id.startswith(prefixes):
            return name
    return "statements"


def pages_of(check: Mapping[str, Any]) -> list[int]:
    return sorted({int(t["page"]) for t in (*check["left"], *check["right"]) if t.get("page")})


def terms(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": str(t["name"])[:80], "printed": str(t["printed"])[:40], "page": int(t["page"]) if t.get("page") else None} for t in items]


def checks_view(result: Mapping[str, Any]) -> dict[str, Any]:
    """The checks as the owner reads them: counts per group, every difference with its figures and pages, and why the checks that did not run did not."""
    counts: dict[str, Counter[str]] = {}
    for c in result["checks"]:
        counts.setdefault(group_of(c["id"]), Counter())[c["status"]] += 1
    order = ["statements", *(name for name, _ in GROUPS)]
    groups = [{"id": g, "title": GROUP_TITLES[g], "held": counts[g]["ok"], "differ": counts[g]["differs"], "not_run": counts[g]["not_run"]} for g in order if g in counts]
    differences = [{"id": c["id"], "group": group_of(c["id"]), "title": c["title"], "reason": c["reason"], "pages": pages_of(c), "left": terms(c["left"]), "right": terms(c["right"]), "tolerance": c["tolerance"]}
                   for c in result["checks"] if c["status"] == "differs"][:MAX_DIFFERENCES]
    reasons = Counter(c["reason"].split(":")[0][:110] for c in result["checks"] if c["status"] == "not_run")
    return {
        "summary": {"held": result["summary"]["ok"], "differ": result["summary"]["differs"], "not_run": result["summary"]["not_run"], "statement_tables_read": result["summary"]["statement_tables_read"]},
        "groups": groups,
        "differences": differences,
        "not_run": [{"reason": r, "count": n} for r, n in reasons.most_common(MAX_NOT_RUN_REASONS)],
        "notes": [str(n)[:300] for n in result["notes"]],
        "not_built": [str(n)[:300] for n in NOT_BUILT],
    }


def flag_view(flag: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {"id": flag["id"], "status": status, "type": flag.get("type"), "text": str(flag["text"])[:400], "why": str(flag.get("why") or "")[:240], "innocent_explanations": [str(x)[:200] for x in (flag.get("innocent_explanations") or [])], "supported_by": len(flag.get("supported_by") or [])}


def document_view(store: DocStore, doc_id: str) -> dict[str, Any]:
    doc = store.document(doc_id)
    found = coverage(store, doc_id)
    return {
        "doc_id": doc_id, "company": doc["company"], "kind": doc["kind"], "url": doc["url"], "sha256": doc["sha256"], "pages": doc["n_pages"],
        "sections": [{"key": s["key"], "title": s["title"], "start": s["start"], "end": s["end"]} for s in found["sections"]],
        "sections_not_found": found["sections_not_found"], "pages_without_text": len(found["pages_without_text"]),
    }


def run_job(url: str, company: str = "", kind: str = "DRHP", *, docs_path: Path | str = DOCS_PATH, forensic_path: Path | str = FORENSIC_PATH, fetch: Callable[[str], bytes] = fetch_pdf, now: datetime | None = None) -> dict[str, Any]:
    """The report document for the draft at `url`, reading and keeping the draft first when it is not already held."""
    address = validate_url(url)
    when = now or datetime.now(timezone.utc)
    docs = DocStore(docs_path)
    doc_id = docs.by_url(address) or ingest(docs, address, company=clean_name(company, "Unnamed company"), kind=kind if kind in KINDS else "Document", fetch=fetch)
    data = DocData(docs_path)
    store = ForensicStore(forensic_path)
    case = Case(f"{doc_id}-{when:%Y%m%d%H%M%S}", "drhp", doc_id, date.today())  # one id per run: evidence and the ledger are kept by case id
    registry = base_registry([*document_tools(data), checks_tool(data)])
    supervisor = Supervisor(store, registry, {}, None, total_calls=0, max_rounds=1, code_lanes={"checks": run_code_lane(data)})
    report = supervisor.investigate(case)
    doc = docs.document(doc_id)
    result = run_checks(docs.pages(doc_id), docs.figures(doc_id), doc["unreadable"], docs.layout(doc_id))
    return {
        "schema": SCHEMA, "ok": True, "mode": "checks", "generated_at": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "document": document_view(docs, doc_id),
        "concern": report["concern"],
        "flags": [flag_view(f, "established") for f in report["established"]] + [flag_view(f, "single_source") for f in report["single_source"]],
        "checks": checks_view(result),
        "not_examined": [{"lane": n["lane"], "why": str(n["why"])[:200]} for n in report["not_examined"]],
        "limits": list(LIMITS), "narrative": report["narrative"], "caveat": CAVEAT, "cost": report["cost"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--company", default="")
    parser.add_argument("--kind", default="DRHP")
    parser.add_argument("--docs", default=str(DOCS_PATH))
    parser.add_argument("--forensic", default=str(FORENSIC_PATH))
    args = parser.parse_args(argv)
    try:
        doc = run_job(args.url, args.company, args.kind, docs_path=args.docs, forensic_path=args.forensic)
    except AthenaError as exc:
        doc = error_document(str(exc))
    except Exception as exc:  # noqa: BLE001 - a crash is reported in the same shape, with the type in the log and nothing internal in the answer
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        doc = error_document("the analysis failed; the document could not be read or checked")
    sys.stdout.write(json.dumps(doc, ensure_ascii=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
