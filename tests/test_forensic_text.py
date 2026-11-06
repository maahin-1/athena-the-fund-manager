from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from athena.documents.sections import find_sections, section_of
from athena.documents.store import DocStore
from athena.forensic.model import Case
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.text.lane import TEXT_LANE, DocData, document_tools
from athena.forensic.tools import ToolContext, ToolError, base_registry
from athena.contracts import AthenaError

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
AS_OF = date(2026, 11, 4)
DOC = "doc1"

PAGES = [
    "DRAFT RED HERRING PROSPECTUS\nDated: September 25, 2026\nBOOK RUNNING LEAD MANAGERS\nICICI Securities Limited",
    "SECTION II – RISK FACTORS\nWe depend on a single customer for most of our revenue and the loss of that customer would hurt us a great deal.",
    "Our promoters have pledged shares with a lender and a default on the loan could affect control of the company over time.",
    "OBJECTS OF THE OFFER\nThe fresh issue proceeds of 650 crore will be used to repay borrowings of the company and the rest for general corporate purposes.",
    "OUR BUSINESS\nWe sell steel products to more than one thousand customers across India through traders and dealers in many states.",
]


class Script:
    def __init__(self, replies):  # noqa: ANN001
        self.replies, self.seen = list(replies), []

    def complete(self, system: str, user: str) -> str:
        self.seen.append(user)
        if not self.replies:
            raise AthenaError("script used up")
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


@pytest.fixture()
def docs(tmp_path):  # noqa: ANN001, ANN201
    path = tmp_path / "documents.duckdb"
    store = DocStore(path)
    store.add(DOC, kind="DRHP", company="Demo Steel", url="https://example.test/d.pdf", sha256="abc", published=date(2026, 9, 25), pages=PAGES, unreadable=[], sections=find_sections(PAGES), section_of=section_of, now=NOW)
    store.save_figures(DOC, {"offer_total": {"printed": "1,000 crore", "crore": 1000.0, "page": 4}, "checks": [{"check": "fresh issue plus offer for sale equals the offer", "ok": True, "detail": "800 + 200 vs 1000"}]}, NOW)
    store.save_analysis(DOC, [{"id": "business", "label": "What the company does", "status": "answered", "summary": "sells steel", "points": [{"page": 5, "section": "business", "quote": "We sell steel products"}]}], NOW)
    return path


def ctx(tmp_path, doc: str = DOC) -> ToolContext:  # noqa: ANN001
    return ToolContext(Case("c", "drhp", doc, AS_OF), ForensicStore(tmp_path / "f.duckdb"), "text")


def registry(path):  # noqa: ANN001, ANN201
    return base_registry(document_tools(DocData(path)))


def test_the_map_lists_sections_with_pages_and_the_figures_held(docs, tmp_path) -> None:  # noqa: ANN001
    result, ev = registry(docs).call("doc.map", {}, ctx(tmp_path))
    keys = {s["key"]: (s["start"], s["end"]) for s in result["sections"]}
    assert keys["risk_factors"] == (2, 3) and keys["objects"] == (4, 4)
    assert result["figures_held"] == ["checks", "offer_total"] and ev is not None


def test_open_reads_a_section_from_its_first_page_or_pages_from_a_number(docs, tmp_path) -> None:  # noqa: ANN001
    reg, c = registry(docs), ctx(tmp_path)
    by_section, _ = reg.call("doc.open", {"section": "objects"}, c)
    assert [p["page"] for p in by_section["pages"]] == [4] and "650 crore" in by_section["pages"][0]["text"]
    by_page, _ = reg.call("doc.open", {"from_page": 2, "pages": 9}, c)
    assert [p["page"] for p in by_page["pages"]] == [2, 3, 4]  # capped at three pages
    with pytest.raises(ToolError):
        reg.call("doc.open", {"from_page": 99}, c)
    with pytest.raises(ToolError):
        reg.call("doc.open", {"section": "nonsense"}, c)


def test_open_gives_the_printed_number_of_a_page_and_opens_the_page_a_citation_names(tmp_path) -> None:  # noqa: ANN001
    pages = ["cover of the draft document with enough words to be a page", "contents of the draft document with enough words to be a page"] + [f"{p - 2}\nbody text of the page numbered {p - 2} on its own line, long enough to be a page" for p in range(3, 12)]
    path = tmp_path / "numbered.duckdb"
    DocStore(path).add("n1", kind="DRHP", company="Numbered", url="https://example.test/n.pdf", sha256="n1", published=date(2026, 9, 25), pages=pages, unreadable=[], sections=find_sections(pages), section_of=section_of, now=NOW)
    reg, c = registry(path), ToolContext(Case("cn", "drhp", "n1", AS_OF), ForensicStore(tmp_path / "fn.duckdb"), "text")
    opened, _ = reg.call("doc.open", {"from_page": 4, "pages": 2}, c)
    assert [(p["page"], p["printed_page"]) for p in opened["pages"]] == [(4, 2), (5, 3)]
    cited, _ = reg.call("doc.open", {"printed_page": 7}, c)  # "see page 7": the PDF page that prints 7
    assert cited["pages"][0]["page"] == 9 and cited["pages"][0]["printed_page"] == 7 and "numbered 7" in cited["pages"][0]["text"]
    front, _ = reg.call("doc.open", {"from_page": 1}, c)
    assert front["pages"][0]["printed_page"] is None  # the cover prints none
    with pytest.raises(ToolError, match="not known"):
        reg.call("doc.open", {"printed_page": 99}, c)


def test_search_is_limited_to_the_named_sections_and_cites_the_page(docs, tmp_path) -> None:  # noqa: ANN001
    reg, c = registry(docs), ctx(tmp_path)
    result, _ = reg.call("doc.search", {"query": "pledged shares lender default", "sections": ["risk_factors"]}, c)
    assert result["passages"] and result["passages"][0]["page"] == 3 and result["passages"][0]["section"] == "risk_factors"
    only_business, _ = reg.call("doc.search", {"query": "steel customers dealers", "sections": ["business"]}, c)
    assert {p["page"] for p in only_business["passages"]} == {5}
    with pytest.raises(ToolError):
        reg.call("doc.search", {"query": "x", "sections": ["nope"]}, c)
    with pytest.raises(ToolError):
        reg.call("doc.search", {"query": "  "}, c)


def test_figures_and_baseline_are_returned_and_a_missing_document_is_a_clear_error(docs, tmp_path) -> None:  # noqa: ANN001
    reg = registry(docs)
    figures, _ = reg.call("doc.figures", {}, ctx(tmp_path))
    assert figures["figures"]["offer_total"]["crore"] == 1000.0
    baseline, _ = reg.call("doc.baseline", {}, ctx(tmp_path))
    assert baseline[0]["id"] == "business" and baseline[0]["pages"] == [5]
    with pytest.raises(ToolError, match="no document"):
        reg.call("doc.map", {}, ctx(tmp_path, "missing"))


def test_a_supervised_run_quotes_a_page_and_a_made_up_quote_is_refused(docs, tmp_path) -> None:  # noqa: ANN001
    store = ForensicStore(tmp_path / "forensic.duckdb")
    good = {"action": "write", "kind": "OBSERVATION", "text": "a single customer accounts for most revenue (page 2)", "evidence": ["E1"],
            "quote": "We depend on a single customer for most of our revenue"}
    made_up = {"action": "write", "kind": "OBSERVATION", "text": "the promoters have sold all shares", "evidence": ["E1"], "quote": "The promoters have sold all of their shares in the company"}
    llm = Script([{"action": "tool", "tool": "doc.search", "args": {"query": "single customer revenue", "sections": ["risk_factors"]}}, good, made_up, {"action": "finish", "summary": "read the risk factors"}, "one observation, one refused"])
    report = Supervisor(store, registry(docs), {"text": TEXT_LANE}, llm, total_calls=8, max_rounds=1).investigate(Case("demo-1", "drhp", DOC, AS_OF))
    lane = report["lanes"]["text"][0]
    assert lane["rejected"] == 1
    kept = [e.text for e in store.entries("demo-1", kind="OBSERVATION") if e.status != "rejected"]
    assert any("single customer" in t for t in kept)
    assert not any("sold all shares" in t for t in kept)
