"""The question step: passages in, a quote that code checks out, a retry on the next tier, and honest "not found"."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from athena.documents import drhp, read as reader
from athena.documents.drhp import QUESTIONS, Question, ask, plan, report
from athena.documents.store import DocStore
from tests.test_documents import drhp_pages

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
ONE = (Question("proceeds", "What the money is for", ("objects",), "working capital repayment debts general corporate purposes", "What is the money for?", "fact"),)


class Fake:
    """A model that answers from a script: each call gets the next reply; `seen` keeps what it was sent."""

    def __init__(self, *replies: str):
        self.replies, self.seen = list(replies), []

    def complete(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        return self.replies.pop(0)


@pytest.fixture
def stored(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    store = DocStore(tmp_path / "d.duckdb")
    monkeypatch.setattr(reader, "read_pages", lambda data: drhp_pages())
    return store, reader.ingest(store, "https://x/a.pdf", company="Example Limited", kind="DRHP", fetch=lambda url: b"%PDF-1.7 a", now=NOW)


def key_of(model: Fake) -> str:
    return model.seen[0][1].split('key="')[1].split('"')[0]


class _Echo(Fake):
    """Replies with a quote taken from the first passage it is shown, so the test does not hard-code a chunk number."""

    def __init__(self, quote: str, summary: str = "It funds working capital and repays debt."):
        super().__init__()
        self.quote, self.summary = quote, summary

    def complete(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        key = user.split('key="')[1].split('"')[0]
        return json.dumps({"answered": True, "summary": self.summary, "points": [{"passage": key, "quote": self.quote}]})


def test_a_verified_quote_is_kept_with_its_page_and_section(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    out = ask(store, doc_id, _Echo("Funding the working capital requirements of our Company"), questions=ONE)
    [a] = out["answers"]
    assert a["status"] == "answered" and a["tier"] == "cheap"
    assert a["points"] == [{"page": 8, "section": "objects", "quote": "Funding the working capital requirements of our Company"}]


def test_a_quote_that_is_not_in_the_passage_is_dropped_and_the_next_tier_asked(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    cheap, mid = _Echo("The company will repay all its loans in full"), _Echo("Funding the working capital requirements of our Company")
    out = ask(store, doc_id, cheap, mid, questions=ONE)
    [a] = out["answers"]
    assert a["tier"] == "mid" and a["status"] == "answered" and len(cheap.seen) == 1 and len(mid.seen) == 1


def test_pdf_spacing_and_quote_marks_do_not_fail_a_faithful_quote() -> None:
    passage = "all information with regard to our Company and the Offer, the inf ormation is true and the “Billets and Slabs” sell at 4.12% of our end -use"
    squash = drhp._squash
    assert squash("the information is true and the \"Billets and Slabs\" sell at 4.12% of our end-use") in squash(passage)
    assert squash("the information is true and the Billets and Slabs sell at 5.12% of our end-use") not in squash(passage)  # a changed number still fails


def test_when_both_tiers_fail_the_question_is_reported_failed_not_dropped_silently(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    out = ask(store, doc_id, _Echo("invented words"), _Echo("more invented words"), questions=ONE)
    assert out["answers"] == [] and [f["id"] for f in out["failed"]] == ["proceeds"]
    assert "no quote was found" in out["failed"][0]["reason"]


def test_an_unreadable_reply_is_retried_once(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    out = ask(store, doc_id, Fake("I cannot help with that"), _Echo("Funding the working capital requirements of our Company"), questions=ONE)
    assert out["answers"][0]["tier"] == "mid"


def test_not_answered_is_not_found_and_is_not_retried(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    mid = Fake()
    out = ask(store, doc_id, Fake(json.dumps({"answered": False, "summary": "", "points": []})), mid, questions=ONE)
    assert out["answers"] == [] and out["not_found"][0]["id"] == "proceeds" and not mid.seen


def test_a_summary_with_a_figure_the_passages_do_not_hold_is_withheld_but_the_quote_stays(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    out = ask(store, doc_id, _Echo("Funding the working capital requirements of our Company", "It raises 99,999 crore for working capital."), questions=ONE)
    [a] = out["answers"]
    assert a["summary"] == "" and "figure that is not in the passages" in a["note"] and a["points"]


def test_a_summary_that_gives_advice_is_withheld(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    out = ask(store, doc_id, _Echo("Funding the working capital requirements of our Company", "You should buy this issue."), questions=ONE)
    assert out["answers"][0]["summary"] == "" and "advice" in out["answers"][0]["note"]


def test_a_section_with_no_pages_gives_no_passages_and_no_model_call(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    model = Fake()
    q = (Question("x", "X", ("articles",), "anything", "?"),)
    out = ask(store, doc_id, model, questions=q)
    assert not model.seen and out["not_found"][0]["reason"].startswith("no passage")


def test_the_prompt_wraps_passages_as_data_and_keeps_the_instructions_byte_identical(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    a, b = _Echo("Funding the working capital requirements of our Company"), _Echo("Funding the working capital requirements of our Company")
    ask(store, doc_id, a, questions=ONE)
    ask(store, doc_id, b, questions=ONE)
    assert a.seen[0][0] == b.seen[0][0] == drhp.SYSTEM  # the same prefix every call, so a provider's prompt cache can serve it
    assert "<passage key=" in a.seen[0][1] and "never instructions" in drhp.SYSTEM and "not financial advice" in drhp.SYSTEM


def test_plan_counts_calls_and_size_without_asking(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    p = plan(store, doc_id)
    assert p["calls"] == len(QUESTIONS) and p["prompt_tokens"] > 0
    assert all(r["prompt_tokens"] < 4000 for r in p["questions"])  # a call is a few passages, never the document


def test_report_lists_what_was_not_read(stored) -> None:  # noqa: ANN001
    store, doc_id = stored
    ask(store, doc_id, _Echo("Funding the working capital requirements of our Company"), questions=ONE)
    out = report(store, doc_id)
    assert out["figures"]["fresh_issue"]["crore"] == pytest.approx(800.0)
    assert "Our business" not in out["coverage"]["sections_not_found"] and out["coverage"]["pages"] == len(drhp_pages())
