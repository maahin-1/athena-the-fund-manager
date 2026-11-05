"""Long documents: the download, the section finder, the offer figures taken by program, the store and the search over stored pages."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from athena.contracts import AthenaError
from athena.documents import read as reader
from athena.documents.fetch import fetch_pdf
from athena.documents.figures import offer_figures, to_crore
from athena.documents.search import Finder, verify_quotes
from athena.documents.sections import find_sections, missing, section_of
from athena.documents.store import DocStore

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

CONTENTS = "TABLE OF CONTENTS\n" + "\n".join(f"ITEM {n} {'.' * 40} {n}" for n in range(8))
OFFER_PAGE = """68
SECTION III – INTRODUCTION
THE OFFER
The following table summarizes details of the Offer:
Offer (1)(2)(3) Up to [●] Equity Shares of face value of ₹ 10 each, aggregating up to ₹
10,000.00 million
Consists of:
Fresh Issue (1)(2) Up to [●] Equity Shares of face value of ₹ 10 each, aggregating up to
₹8,000.00 million
Offer for Sale(3) Up to [●] Equity Shares of face value of ₹ 10 each, aggregating up to
₹2,000.00 million
Net Offer Up to [●] Equity Shares of face value of ₹  10 each, aggregating up to
₹[●] million
Equity Shares outstanding prior to the Offer (as on the date of this
Draft Red Herring Prospectus)
211,239,450 Equity Shares of face value of ₹ 10 each
(1) The Offer has been authorized by a resolution of our Board dated September 16, 2026.
(2) Our Company may consider a Pre -IPO Placement aggregating up to ₹ 1,600.00 million, as may be permitted.
"""
ISSUE_PAGE_LAKHS = """71
SECTION III – INTRODUCTION
THE ISSUE
Issue of Equity Shares(1) Up to [●] Equity Shares of face value of ₹ 10 each,
aggregating to ₹ 44,000.00 lakhs
The Issue consists of:
Fresh Issue(1) Up to [●] Equity Shares of face value of ₹ 10 each
aggregating up to ₹ 44,000.00 lakhs
Equity Shares outstanding prior to the Issue (as on the date of
this Draft Red Herring Prospectus)
6,90,97,925 Equity Shares of face value of ₹ 10 each
"""
OBJECTS_PAGE = """113
OBJECTS OF THE ISSUE
Requirement of funds and utilization of Net Proceeds
Our Company proposes to utilize the Net Proceeds towards funding the following objects, as approved by our Board by way of their resolution dated September 25, 2026:
(in ₹ lakhs)
Sr. No. Particulars Estimated Amount^
1. Funding the working capital requirements of our Company  15,000.00
2. Repayment/prepayment, in full or in part, of certain debts availed by
our Company 12,000.00
3. General corporate purposes (1)(2)  [●]
 Total(1)(3) [●]
(1) To be finalised upon determination of the Issue Price.
"""


def body(*heads: str) -> list[str]:
    return [f"{h}\nsome text about {h.lower()} that runs on for a while to be a real page of the document" for h in heads]


def drhp_pages() -> list[str]:
    return [
        "DRAFT RED HERRING PROSPECTUS\nDated: September 25, 2026\nBOOK RUNNING LEAD MANAGERS\nICICI Securities Limited\nMotilal Oswal Investment Advisors Limited\nREGISTRAR TO THE OFFER\nproposed to be listed on BSE Limited and National Stock Exchange of India Limited",
        CONTENTS,
        *body("SECTION I – GENERAL", "SECTION II – RISK FACTORS", "more risks"),
        OFFER_PAGE,
        *body("CAPITAL STRUCTURE"),
        OBJECTS_PAGE,
        *body("BASIS FOR OFFER PRICE", "SECTION IV – ABOUT OUR COMPANY\nINDUSTRY OVERVIEW", "OUR BUSINESS", "OUR MANAGEMENT", "OUR PROMOTERS AND PROMOTER GROUP",
              "SECTION V – FINANCIAL INFORMATION", "MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION", "SECTION VI – LEGAL AND OTHER INFORMATION\nOUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS",
              "GOVERNMENT AND OTHER APPROVALS", "OFFER STRUCTURE"),
    ]


class TestSections:
    def test_each_heading_starts_its_section_and_the_contents_page_is_not_one(self) -> None:
        pages = drhp_pages()
        secs = {s.key: s for s in find_sections(pages)}
        assert secs["risk_factors"].start == 4 and secs["risk_factors"].end == 5  # "SECTION II" page, then a run-on page
        assert secs["offer_summary"].start == 6
        assert secs["objects"].start == 8
        assert "contents" not in secs  # the contents page lists every heading with a dotted leader; none of them counts
        assert secs["front"].start == 1 and secs["front"].end == 2

    def test_a_divider_sharing_its_page_with_the_first_chapter_holds_no_pages_of_its_own(self) -> None:
        secs = {s.key: s for s in find_sections(drhp_pages())}
        assert "about_company" not in secs and secs["industry"].pages == 1
        assert "legal" not in secs and secs["litigation"].pages == 1

    def test_the_sections_cover_every_page_once(self) -> None:
        pages = drhp_pages()
        secs = find_sections(pages)
        covered = [p for s in secs for p in range(s.start, s.end + 1)]
        assert covered == list(range(1, len(pages) + 1))
        assert section_of(secs, 8) == "objects"

    def test_what_is_not_found_is_listed(self) -> None:
        secs = find_sections(body("SECTION II – RISK FACTORS", "OUR BUSINESS"))
        assert "Objects of the offer" in missing(secs) and "Risk factors" not in missing(secs)

    def test_a_heading_inside_a_sentence_is_not_a_heading(self) -> None:
        pages = ["Cover page of the draft document with enough text to be a page", "The risk factors and our business are described later; objects of the offer too.", "Closing page text goes on and on"]
        assert [s.key for s in find_sections(pages)] == []


class TestFigures:
    def test_offer_table_in_millions(self) -> None:
        pages = drhp_pages()
        fig = offer_figures(pages, find_sections(pages))
        assert fig.offer_total.crore == pytest.approx(1000.0) and fig.fresh_issue.crore == pytest.approx(800.0) and fig.offer_for_sale.crore == pytest.approx(200.0)
        assert fig.offer_for_sale_share == pytest.approx(0.2)
        assert fig.pre_offer_shares == 211239450 and fig.face_value == 10.0
        assert fig.pre_ipo_placement.crore == pytest.approx(160.0)
        assert fig.board_resolution == "September 16, 2026" and fig.draft_date == "September 25, 2026"
        assert fig.managers == ["ICICI Securities Limited", "Motilal Oswal Investment Advisors Limited"]
        assert fig.exchanges == ["BSE", "NSE"]
        assert all(c["ok"] for c in fig.checks)

    def test_the_net_offer_row_is_not_taken_for_the_whole_offer(self) -> None:
        pages = drhp_pages()
        fig = offer_figures(pages, find_sections(pages))
        assert fig.offer_total.printed.startswith("₹ 10,000.00")  # "Net Offer ... ₹[●] million" comes after and must not replace it

    def test_lakhs_and_indian_grouping(self) -> None:
        pages = ["cover page text long enough to count as a page of the document", ISSUE_PAGE_LAKHS]
        fig = offer_figures(pages, find_sections(pages))
        assert fig.offer_total.crore == pytest.approx(440.0) and fig.fresh_issue.crore == pytest.approx(440.0)
        assert fig.offer_for_sale is None and fig.offer_for_sale_share == 0.0  # a fresh issue only
        assert fig.pre_offer_shares == 69097925

    def test_objects_table_rows_amounts_and_blank(self) -> None:
        pages = drhp_pages()
        fig = offer_figures(pages, find_sections(pages))
        names = [o["object"] for o in fig.objects]
        assert names == ["Funding the working capital requirements of our Company", "Repayment/prepayment, in full or in part, of certain debts availed by our Company", "General corporate purposes"]
        assert [o["crore"] for o in fig.objects] == [pytest.approx(150.0), pytest.approx(120.0), None]  # the blank stays blank, it is not zero
        assert fig.objects_unit_crore == 0.01 and fig.objects_total_crore == pytest.approx(270.0)

    def test_a_failed_check_is_reported_not_hidden(self) -> None:
        text = OFFER_PAGE.replace("₹8,000.00 million", "₹5,000.00 million")
        pages = ["cover page text long enough to count as a page of the document", text]
        fig = offer_figures(pages, find_sections(pages))
        failed = [c for c in fig.checks if not c["ok"]]
        assert failed and "fresh issue plus offer for sale" in failed[0]["check"]

    def test_units(self) -> None:
        assert to_crore("1,12,800", "lakhs") == pytest.approx(1128.0)
        assert to_crore("250", "crore") == 250.0 and to_crore("10", "billion") == 1000.0
        assert to_crore("[●]", "million") is None and to_crore("5", None) is None


class TestFetch:
    @staticmethod
    def response(body: bytes, status: int = 200, announced: int | None = None):  # noqa: ANN205
        headers = {"content-length": str(len(body) if announced is None else announced)}
        return SimpleNamespace(status_code=status, content=body, headers=headers)

    def test_a_short_transfer_is_tried_again(self) -> None:
        good = b"%PDF-1.7 " + b"x" * 50
        answers = [self.response(good[:20], announced=len(good)), self.response(good)]
        sleeps: list[float] = []
        assert fetch_pdf("https://x/y.pdf", get=lambda *a, **k: answers.pop(0), sleep=sleeps.append) == good
        assert sleeps == [3.0]

    def test_a_dropped_connection_is_tried_again_then_reported(self) -> None:
        calls = []

        def get(*a, **k):  # noqa: ANN202
            calls.append(1)
            raise ConnectionError("reset")

        with pytest.raises(AthenaError, match="could not download"):
            fetch_pdf("https://x/y.pdf", get=get, sleep=lambda s: None)
        assert len(calls) == 3

    def test_a_page_that_is_not_a_pdf_is_not_asked_for_twice(self) -> None:
        calls = []

        def get(*a, **k):  # noqa: ANN202
            calls.append(1)
            return self.response(b"<html>blocked</html>")

        with pytest.raises(AthenaError, match="did not give a PDF"):
            fetch_pdf("https://x/y.pdf", get=get, sleep=lambda s: None)
        assert len(calls) == 1

    def test_a_server_error_is_retried(self) -> None:
        answers = [self.response(b"", status=503), self.response(b"%PDF ok")]
        assert fetch_pdf("https://x/y.pdf", get=lambda *a, **k: answers.pop(0), sleep=lambda s: None) == b"%PDF ok"

    def test_the_size_limit(self) -> None:
        with pytest.raises(AthenaError, match="over the"):
            fetch_pdf("https://x/y.pdf", get=lambda *a, **k: self.response(b"%PDF" + b"x" * 5000), max_bytes=1000, sleep=lambda s: None)


@pytest.fixture
def store(tmp_path):  # noqa: ANN001, ANN201
    return DocStore(tmp_path / "documents.duckdb")


@pytest.fixture
def stored(store, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(reader, "read_pages", lambda data: drhp_pages())
    doc_id = reader.ingest(store, "https://x/a.pdf", company="Example Limited", kind="DRHP", published=date(2026, 10, 5), fetch=lambda url: b"%PDF-1.7 a", now=NOW)
    return store, doc_id


class TestIngest:
    def test_a_document_is_kept_with_sections_figures_and_coverage(self, stored) -> None:  # noqa: ANN001
        store, doc_id = stored
        cov = reader.coverage(store, doc_id)
        assert cov["pages"] == len(drhp_pages()) and cov["company"] == "Example Limited"
        assert {"risk_factors", "objects"} <= {s["key"] for s in cov["sections"]}
        assert "Capital structure" not in cov["sections_not_found"] and "Basis for offer price" not in cov["sections_not_found"]
        assert store.figures(doc_id)["fresh_issue"]["crore"] == pytest.approx(800.0)

    def test_the_same_bytes_are_never_read_twice(self, stored, monkeypatch) -> None:  # noqa: ANN001
        store, doc_id = stored
        monkeypatch.setattr(reader, "read_pages", lambda data: pytest.fail("read again"))
        assert reader.ingest(store, "https://elsewhere/a.pdf", company="Example Limited", kind="DRHP", fetch=lambda url: b"%PDF-1.7 a") == doc_id

    def test_changed_bytes_are_a_new_document(self, stored, monkeypatch) -> None:  # noqa: ANN001
        store, doc_id = stored
        monkeypatch.setattr(reader, "read_pages", lambda data: body("SECTION II – RISK FACTORS"))
        other = reader.ingest(store, "https://x/a.pdf", company="Example Limited", kind="RHP", fetch=lambda url: b"%PDF-1.7 b", now=NOW + timedelta(hours=1))
        assert other != doc_id and store.by_url("https://x/a.pdf") == other

    def test_pages_without_text_are_listed_not_hidden(self, store, monkeypatch) -> None:  # noqa: ANN001
        pages = [*drhp_pages(), "", "  3  "]
        monkeypatch.setattr(reader, "read_pages", lambda data: pages)
        doc_id = reader.ingest(store, "https://x/b.pdf", company="B Limited", kind="DRHP", fetch=lambda url: b"%PDF-1.7 c", now=NOW)
        assert reader.coverage(store, doc_id)["pages_without_text"][-2:] == [len(pages) - 1, len(pages)]

    def test_a_scan_with_no_text_layer_is_refused(self, store, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(reader, "read_pages", lambda data: ["", "", ""])
        with pytest.raises(AthenaError, match="no text layer"):
            reader.ingest(store, "https://x/c.pdf", company="C", kind="DRHP", fetch=lambda url: b"%PDF-1.7 d", now=NOW)


class TestLayout:
    def test_only_pages_that_name_statement_lines_are_read_again_by_position(self) -> None:
        pages = ["cover", "Restated Statement of Assets and Liabilities\nTotal Assets 1.00 2.00", "risk factors text", "Net cash from operating activities 5.00 4.00"]
        assert reader.layout_candidates(pages) == [2, 4]

    def test_a_document_is_kept_without_layout_when_the_second_reading_fails(self, stored) -> None:  # noqa: ANN001
        store, doc_id = stored  # the bytes of the fixture are not a PDF, so the layout reading raises; the document is still kept
        assert store.document(doc_id) is not None and store.layout(doc_id) == {}

    def test_layout_pages_are_kept_per_document_and_replaced_when_read_again(self, stored, monkeypatch) -> None:  # noqa: ANN001
        store, doc_id = stored
        monkeypatch.setattr(reader, "layout_candidates", lambda pages: [2, 3])
        monkeypatch.setattr(reader, "read_layout", lambda data, wanted: {n: f"layout {n}" for n in wanted})
        kept = reader.add_layout(store, doc_id, fetch=lambda url: b"%PDF-1.7 a")
        assert kept == 2 and store.layout(doc_id) == {2: "layout 2", 3: "layout 3"}
        monkeypatch.setattr(reader, "read_layout", lambda data, wanted: {2: "only"})
        reader.add_layout(store, doc_id, fetch=lambda url: b"%PDF-1.7 a")
        assert store.layout(doc_id) == {2: "only"}

    def test_a_document_with_no_statement_looking_page_keeps_an_empty_layout(self, store, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(reader, "read_pages", lambda data: ["cover page of the offer document " * 3, "narrative text only " * 5])
        monkeypatch.setattr(reader, "read_layout", lambda data, wanted: {n: "x" for n in wanted})
        doc_id = reader.ingest(store, "https://x/n.pdf", company="N Limited", kind="DRHP", fetch=lambda url: b"%PDF-1.7 n", now=NOW)
        assert store.layout(doc_id) == {}

    def test_a_file_whose_hash_changed_is_not_added_to_the_document_that_was_read(self, stored) -> None:  # noqa: ANN001
        store, doc_id = stored
        with pytest.raises(AthenaError, match="hash changed"):
            reader.add_layout(store, doc_id, fetch=lambda url: b"%PDF-1.7 changed")

    def test_a_store_made_before_the_table_existed_gives_no_layout(self, tmp_path) -> None:  # noqa: ANN001
        import duckdb

        path = tmp_path / "old.duckdb"
        duckdb.connect(str(path)).close()
        assert DocStore(path, read_only=True).layout("anything") == {}


class TestSearch:
    def test_a_question_reads_only_the_sections_it_names(self, stored) -> None:  # noqa: ANN001
        store, doc_id = stored
        finder = Finder(store, doc_id)
        found = finder.search("working capital requirements of our company", sections=["objects"], k=3)
        assert found and found[0].section == "objects" and found[0].page == 8
        assert all(f.section == "objects" for f in found)
        assert finder.search("working capital requirements", sections=["risk_factors"], k=3) == [] or all(f.section == "risk_factors" for f in finder.search("working capital", sections=["risk_factors"]))

    def test_a_quote_must_be_in_the_passage_it_cites(self, stored) -> None:  # noqa: ANN001
        store, doc_id = stored
        finder = Finder(store, doc_id)
        hits = finder.hits("repayment prepayment of certain debts", sections=["objects"], k=2)
        key = f"{hits[0].passage.doc_id}#{hits[0].passage.chunk}"
        assert verify_quotes({key: "Repayment/prepayment, in full or in part, of certain debts"}, hits) == []
        assert verify_quotes({key: "the company will never repay its loans"}, hits) == [key]
