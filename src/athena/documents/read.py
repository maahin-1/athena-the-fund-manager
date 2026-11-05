"""Read a long PDF once: fetch it, take its text page by page, cut it into sections, take the offer figures by program, and keep all of it in the document store.

    python -m athena.documents.read --url URL --company "Name Limited" --kind DRHP
    python -m athena.documents.read --coverage DOC_ID

A document is identified by the hash of its bytes, so the same file is never read twice, and a changed file (a later addendum at the same address) is read again as a new document. `coverage` says what
was and was not read: the sections found, the core sections not found, and the pages that gave no text, so nothing is silently missed."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from typing import Any

from athena.contracts import AthenaError
from athena.documents.fetch import fetch_pdf
from athena.documents.figures import offer_figures
from athena.documents.sections import find_sections, missing, section_of
from athena.documents.store import DEFAULT_PATH, DocStore

MIN_PAGE_CHARS = 40  # a page with less text than this is a cover, a divider or a scan


def read_pages(data: bytes) -> list[str]:
    """The text of each page. A page that cannot be read is empty rather than an error."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # a damaged file
        raise AthenaError(f"the PDF could not be opened: {exc}") from exc
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # a damaged page
            out.append("")
    return out


STATEMENT_WORDS = re.compile(r"total assets|total income|total expenses|profit before|cash and cash equivalents|net cash|total equity|revenue from operations", re.IGNORECASE)


def layout_candidates(pages: Sequence[str]) -> list[int]:
    """The pages (1-based) whose text names lines of a financial statement. Only these are read a second time in layout mode."""
    return [n for n, text in enumerate(pages, start=1) if STATEMENT_WORDS.search(text)]


def read_layout(data: bytes, wanted: Sequence[int]) -> dict[int, str]:
    """The text of the `wanted` pages (1-based) placed by position on the page, so a label stays on the line of its figures. A page that cannot be read is left out."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # a damaged file
        raise AthenaError(f"the PDF could not be opened: {exc}") from exc
    out: dict[int, str] = {}
    for n in wanted:
        try:
            out[n] = reader.pages[n - 1].extract_text(extraction_mode="layout") or ""
        except Exception:  # a damaged page, or a page number past the end
            continue
    return out


def unreadable(pages: Sequence[str]) -> list[int]:
    return [n for n, text in enumerate(pages, start=1) if len("".join(text.split())) < MIN_PAGE_CHARS]


def ingest(
    store: DocStore,
    url: str,
    *,
    company: str,
    kind: str,
    published: date | None = None,
    fetch: Callable[[str], bytes] = fetch_pdf,
    now: datetime | None = None,
) -> str:
    """The id of the document at `url`, reading and keeping it first if these exact bytes are not already held."""
    data = fetch(url)
    sha = hashlib.sha256(data).hexdigest()
    held = store.by_hash(sha)
    if held:
        return held
    when = now or datetime.now(timezone.utc)
    pages = read_pages(data)
    if not any(pages):
        raise AthenaError("the PDF has no text layer; it is a scan, which is not read")
    sections = find_sections(pages)
    doc_id = sha[:16]
    store.add(doc_id, kind=kind, company=company, url=url, sha256=sha, published=published, pages=pages, unreadable=unreadable(pages), sections=sections, section_of=section_of, now=when)
    store.save_figures(doc_id, offer_figures(pages, sections).as_dict(), when)
    try:  # the layout reading is an aid to the checks; a document is kept without it rather than refused
        store.save_layout(doc_id, read_layout(data, layout_candidates(pages)))
    except AthenaError:
        pass
    return doc_id


def add_layout(store: DocStore, doc_id: str, *, fetch: Callable[[str], bytes] = fetch_pdf) -> int:
    """Read the statement-looking pages of a document already held in layout mode too, from the same address; the bytes must be the ones that were read (same hash). The number of pages kept."""
    doc = store.document(doc_id)
    if doc is None:
        raise AthenaError(f"no document {doc_id}")
    data = fetch(doc["url"])
    if hashlib.sha256(data).hexdigest() != doc["sha256"]:
        raise AthenaError("the file at the address is no longer the one that was read (its hash changed); read it again as a new document")
    kept = read_layout(data, layout_candidates([text for _, _, text in store.pages(doc_id)]))
    store.save_layout(doc_id, kept)
    return len(kept)


def coverage(store: DocStore, doc_id: str) -> dict[str, Any]:
    """What was read: pages, the sections found with their sizes, the core sections not found, the pages with no text."""
    doc = store.document(doc_id)
    if doc is None:
        raise AthenaError(f"no document {doc_id}")
    sections = store.sections(doc_id)
    return {
        "doc_id": doc_id, "company": doc["company"], "kind": doc["kind"], "pages": doc["n_pages"],
        "sections": [{"key": s.key, "title": s.title, "start": s.start, "end": s.end, "pages": s.pages} for s in sections],
        "sections_not_found": missing(sections), "pages_without_text": doc["unreadable"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="read one long PDF into the document store")
    parser.add_argument("--url")
    parser.add_argument("--company", default="")
    parser.add_argument("--kind", default="DRHP")
    parser.add_argument("--coverage", metavar="DOC_ID")
    parser.add_argument("--figures", metavar="DOC_ID")
    parser.add_argument("--layout", metavar="DOC_ID", help="read the statement-looking pages of a document already held in layout mode too (fetches the file again, checks its hash)")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    store = DocStore(args.db)
    try:
        if args.layout:
            print(json.dumps({"doc_id": args.layout, "layout_pages_kept": add_layout(store, args.layout)}))
        elif args.coverage:
            print(json.dumps(coverage(store, args.coverage), indent=1, ensure_ascii=False))
        elif args.figures:
            print(json.dumps(store.figures(args.figures), indent=1, ensure_ascii=False))
        elif args.url:
            doc_id = ingest(store, args.url, company=args.company, kind=args.kind)
            print(json.dumps(coverage(store, doc_id), indent=1, ensure_ascii=False))
        else:
            parser.error("give --url, --coverage or --figures")
    except AthenaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
