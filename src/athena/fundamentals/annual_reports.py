"""Run the annual-report reading (`pdf_extract`) company by company, to reach back before the exchange's filings begin.

    python -m athena.fundamentals.annual_reports --universe nifty500 --limit 25 [--dry-run] [--back-to 2016]

The exchange's XBRL history for most companies starts around March 2022. For each company with the standard (Ind AS) layout this takes the report whose later year column the filings also hold,
reads it with `pdf_extract` and keeps the earlier year only if the overlap year agrees with the filings. The year it kept then stands as the reference for the report before, and so on back
to `--back-to`. A report that fails to prove itself ends that company's chain: nothing older is stored without a proof. Banks, finance companies and insurers are skipped (their lines differ).

Rows are stored with source `annual-report`, one annual period per year and the balance at the year end, under the report's link. A model reads the pages, so this costs tokens; `--dry-run` reads
and checks but stores nothing. Started by hand, never scheduled."""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.fundamentals import lines as spec
from athena.fundamentals.documents import ANNUAL_KIND
from athena.fundamentals.history import PREDECESSORS
from athena.fundamentals.ingest import Report, with_predecessors
from athena.fundamentals.pdf_extract import accept, extract, find_statements, normalise
from athena.fundamentals.rows import Statements
from athena.fundamentals.runner import run_each
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore
from athena.fundamentals.universe import Member, universe

SOURCE = "annual-report"
MAX_BYTES = 80_000_000
MIN_DAYS, MAX_DAYS = 340, 380  # a period this long is a year
Year = dict[str, dict[str, float]]


def pdf_bytes(blob: bytes) -> bytes:
    """The report as a PDF: a zip (the exchange packs older reports) gives its largest PDF. Anything else must already be one."""
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            pdfs = sorted((i for i in archive.infolist() if i.filename.lower().endswith(".pdf")), key=lambda i: i.file_size, reverse=True)
            if not pdfs:
                raise AthenaError("the archive holds no PDF")
            return archive.read(pdfs[0])
    if blob[:4] != b"%PDF":
        raise AthenaError("not a PDF")
    return blob


def pdf_pages(data: bytes) -> list[str]:
    """The text of each page. A page that cannot be read is empty rather than an error (scanned pages have no text)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # a damaged page
            out.append("")
    return out


def fiscal_end(title: str) -> int | None:
    """The calendar year a report's financial year ends in, from the exchange's title ('FY2021-22' is 2022)."""
    m = re.search(r"(\d{4})\s*-\s*(\d{2,4})", title)
    if not m:
        return None
    tail = m.group(2)
    return int(tail) if len(tail) == 4 else int(m.group(1)[:2] + tail)


def reference_from_store(store: FundamentalsStore, symbol: str) -> tuple[str, dict[date, Year]]:
    """The company's annual figures as the filings hold them (consolidated when it has any), by year end, in rupees. Empty when it has none."""
    for basis in ("consolidated", "standalone"):
        income, balance = store.lines(symbol, basis, spec.INCOME), store.lines(symbol, basis, spec.BALANCE)
        out: dict[date, Year] = {}
        for (start, end), got in income.items():
            if MIN_DAYS <= (end - start).days + 1 <= MAX_DAYS:
                out.setdefault(end, {"income": {}, "balance": {}})["income"] = dict(got)
        for (_, end), got in balance.items():  # a balance sheet is stored with the same date as its start and end
            if end in out:
                out[end]["balance"] = dict(got)
        if out:
            return basis, out
    return "", {}


def chain(
    reports: Sequence[Mapping[str, Any]], reference: Mapping[date, Year], read_pages: Callable[[str], list[str]], llm: Any, back_to: int = 2016,
) -> dict[str, Any]:
    """Go back through the company's reports, newest first, starting at the one whose financial year ends in the reference's earliest year. Returns `{"kept": {year end: year}, "steps": [...]}`.
    A step that does not prove itself ends the chain."""
    known: dict[date, Year] = {d: y for d, y in reference.items()}
    if not known:
        return {"kept": {}, "steps": [], "stop": "the filings hold no annual year to start from"}
    first = min(known).year
    eligible = sorted(
        ((fiscal_end(r["title"]), r) for r in reports if fiscal_end(r["title"]) is not None and back_to <= fiscal_end(r["title"]) <= first), key=lambda t: t[0], reverse=True
    )
    kept: dict[date, Year] = {}
    steps: list[dict[str, Any]] = []
    for end_year, report in eligible:
        step: dict[str, Any] = {"report": report["title"], "url": report["url"]}
        steps.append(step)
        try:
            pages = read_pages(report["url"])
            blocks = find_statements(pages)
            if not blocks:
                step["result"] = "no statements found in the pages"
                break
            years = normalise(extract(blocks, llm))
        except (AthenaError, ValueError, OSError) as exc:
            step["result"] = f"unreadable: {exc}"
            break
        verdict = accept(years, known)
        step["overlap"] = {str(d): {k: v for k, v in o.items() if k != "off"} | {"off": o["off"][:4]} for d, o in verdict["overlap"].items()}
        if not verdict["kept"]:
            step["result"] = verdict.get("why") or "no earlier year passed the checks"
            step["rejected"] = {str(d): p for d, p in verdict.get("rejected", {}).items()}
            break
        step["result"] = "kept " + ", ".join(sorted(str(d) for d in verdict["kept"]))
        step["rejected"] = {str(d): p for d, p in verdict.get("rejected", {}).items()}
        for day, year in verdict["kept"].items():
            kept[day] = year
            known[day] = year
    return {"kept": kept, "steps": steps}


def to_statements(symbol: str, basis: str, kept: Mapping[date, Year]) -> Statements:
    income = {(d.replace(year=d.year - 1) + timedelta(days=1), d): dict(y["income"]) for d, y in kept.items() if y["income"]}
    balance = {d: dict(y["balance"]) for d, y in kept.items() if y["balance"]}
    return Statements(symbol, basis, spec.INDAS, None, None, income, balance, {}, {}, ("read from an annual report",))


class Reader:
    """Downloads a report through the exchange client and returns its page texts, keeping the last one."""

    def __init__(self, results: Any, cache_dir: Path | None = None):
        self._results = results
        self._cache = cache_dir

    def pages(self, url: str) -> list[str]:
        response = self._results._get(url)
        blob = response.content
        if len(blob) > MAX_BYTES:
            raise AthenaError("the report is too large")
        return pdf_pages(pdf_bytes(blob))


def ingest_symbol(member: Member, results: Any, store_path: str | Path, *, llm: Any, dry_run: bool, back_to: int, log: list[dict[str, Any]], **_: Any) -> Report:
    report = Report(member.symbol)
    with FundamentalsStore(store_path) as store:
        company = store.company(member.symbol)
        reports = store.documents(member.symbol, ANNUAL_KIND, 40)
        basis, reference = reference_from_store(store, member.symbol)
    if not company or company["layout"] != spec.INDAS:
        report.notes.append("not the standard layout: skipped")
        report.skipped += 1
        return report
    if not reference or not reports:
        report.notes.append("no annual figures or no reports to start from")
        report.skipped += 1
        return report
    result = chain(reports, reference, Reader(results).pages, llm, back_to)
    log.append({"symbol": member.symbol, "basis": basis, "kept": sorted(str(d) for d in result["kept"]), "steps": result["steps"]})
    for step in result["steps"]:
        if step["result"].startswith("unreadable"):
            report.failed += 1
            report.notes.append(f"{step['report']}: {step['result']}")
    if result["kept"] and not dry_run:
        newest = max(r["published"] for r in reports if isinstance(r.get("published"), datetime)) if reports else datetime.now()
        with FundamentalsStore(store_path) as store:
            by_year = {fiscal_end(r["title"]): r for r in reports}
            for day, year in result["kept"].items():
                source_report = by_year.get(day.year + 1) or by_year.get(day.year)
                url = source_report["url"] if source_report else f"annual-report:{member.symbol}:{day}"
                store.save_statements(to_statements(member.symbol, basis, {day: year}), url, source_report["published"] if source_report else newest, source=SOURCE)
    report.fetched += len(result["kept"])
    return report


def main(argv: Sequence[str] | None = None) -> int:
    import json

    from athena.fundamentals.integrated import FundamentalsResults
    from athena.llm.budget import Meter
    from athena.llm.router import build_router

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50", help="nifty50, nifty500, all, or SYMBOL,SYMBOL")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-age-days", type=int, default=3650)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--back-to", type=int, default=2016, help="the oldest financial-year end to read back to")
    parser.add_argument("--dry-run", action="store_true", help="read and check, store nothing")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--report", default=".athena/reports/annual-reports.json")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    members = universe(args.universe)
    if not args.no_history:
        members = with_predecessors(members, PREDECESSORS)
    llm = build_router(meter=Meter()).client_for("specialist")
    log: list[dict[str, Any]] = []
    results = FundamentalsResults()
    print(f"{len(members)} companies in {args.universe}", flush=True)
    try:
        code = run_each(
            members, lambda m: ingest_symbol(m, results, args.store, llm=llm, dry_run=args.dry_run, back_to=args.back_to, log=log), args.store,
            key=lambda m: f"{m.symbol}#ar" + ("-dry" if args.dry_run else ""), resume=args.resume, max_age_days=args.max_age_days, limit=args.limit, out=sys.stdout,
        )
    finally:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        path.write_text(json.dumps(old + log, indent=1, default=str), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
