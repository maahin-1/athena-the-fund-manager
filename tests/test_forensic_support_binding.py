"""Batch 4 review, B4-2: a supporting figure counts through the rows that hold it. An unrelated row of known source must not lend its identity to a relevant figure that only a row of unknown source holds, and
a figure held by a repeated reading and an unknown row is not independent. This checks provenance, not whether the support entails the flag. Synthetic evidence only."""
from __future__ import annotations

from datetime import date

import pytest

from athena.forensic.model import FLAG, OBSERVATION, SINGLE_SOURCE, Case
from athena.forensic.scoring import CORROBORATED, assess_support, classify
from athena.forensic.store import ForensicStore
from athena.forensic.verify import verify_entry

CASE = Case("c1", "drhp", "Example Limited", date(2026, 11, 3))
OFFER = {"offer_total": {"crore": 1000.0}, "fresh_issue": {"crore": 800.0}, "note": "Customer concentration is 4.12% of revenue"}
RELATION = {"relation": "equal", "left": {"ev": "E1", "path": "fresh_issue.crore"}, "right": {"ev": "E1", "path": "offer_total.crore"}}
UNRELATED = {"other": {"crore": 55.0}}
HOLDS_1000 = {"offer_total": {"crore": 1000.0}}
QUOTE = "customer concentration is 4.12% of revenue"


@pytest.fixture
def store(tmp_path):
    s = ForensicStore(tmp_path / "f.duckdb")
    s.add_case(CASE)
    return s


def look(store):
    return lambda ev: store.evidence("c1", ev)


def flag(store, contradiction=RELATION, refs=("E1", "E2")):
    body = {"text": "the fresh issue differs from the offer total", "flag_type": "arithmetic", "evidence": list(refs), "innocent": ["a rounding of the printed table"]}
    if contradiction:
        body["contradiction"] = contradiction
    return store.add_entry("c1", FLAG, "text", 1, body)


def author_rows(store):
    return store.add_evidence("c1", "text", "doc.open", {"page": 1}, OFFER), store.add_evidence("c1", "text", "doc.search", {"query": "issue"}, OFFER)  # E1, E2: one document


def support(store, rows, values=None, quote=None, lane="statements"):
    body = {"text": "a second lane read it", "evidence": list(rows), "supports": "L1"}
    if values is not None:
        body["values"] = values
    if quote is not None:
        body["quote"] = quote
    return store.add_entry("c1", OBSERVATION, lane, 2, body)


def assess(store, entry_id, flag_id="L1"):
    return assess_support(store.entry("c1", entry_id), store.entry("c1", flag_id), look(store), "Example Limited")


def test_the_reproduced_case_unrelated_known_evidence_does_not_make_relevant_unknown_evidence_independent(store):
    author_rows(store)
    known = store.add_evidence("c1", "statements", "fin.get", {}, UNRELATED)
    unknown = store.add_evidence("c1", "statements", "mystery.tool", {}, HOLDS_1000)
    fid = flag(store)
    sid = support(store, [known, unknown], values={"amount": 1000})
    assert verify_entry(OBSERVATION, store.entry("c1", sid).body, look(store)) is None  # validation accepts it: the figure is in cited evidence; provenance is the assessment's question
    got = assess(store, sid)
    assert got.kind == "unknown" and "not known" in got.reason and got.sources == ()
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == SINGLE_SOURCE and "not known" in entry.reason and "statements:Example Limited" not in entry.reason.split("(")[0]


def test_a_relevant_figure_held_by_a_known_independent_row_still_qualifies_whatever_else_is_cited(store):
    author_rows(store)
    known = store.add_evidence("c1", "statements", "fin.get", {}, HOLDS_1000)
    unrelated_unknown = store.add_evidence("c1", "statements", "mystery.tool", {}, UNRELATED)
    fid = flag(store)
    sid = support(store, [known, unrelated_unknown], values={"amount": 1000})
    got = assess(store, sid)
    assert got.kind == "independent" and got.sources == ("statements:Example Limited",) and got.relevance == "shared number"
    classify(store, "c1", look(store))
    assert store.entry("c1", fid).status == CORROBORATED


def test_a_figure_held_by_both_an_independent_row_and_an_unknown_one_is_backed_by_the_independent_one(store):
    author_rows(store)
    known = store.add_evidence("c1", "statements", "fin.get", {}, HOLDS_1000)
    unknown = store.add_evidence("c1", "statements", "mystery.tool", {}, HOLDS_1000)
    flag(store)
    got = assess(store, support(store, [unknown, known], values={"amount": 1000}))
    assert got.kind == "independent" and got.sources == ("statements:Example Limited",)


def test_a_figure_held_only_by_a_repeated_reading_and_an_unknown_row_is_a_repeated_reading(store):
    author_rows(store)
    reread = store.add_evidence("c1", "statements", "doc.checks", {}, HOLDS_1000)  # the flag's own document, read through another tool
    unknown = store.add_evidence("c1", "statements", "mystery.tool", {}, HOLDS_1000)
    flag(store)
    got = assess(store, support(store, [reread, unknown], values={"amount": 1000}))
    assert got.kind == "repeated" and "same source" in got.reason and "document:Example Limited" in got.reason


def test_an_independent_row_that_holds_the_figure_outweighs_a_repeated_reading_of_it(store):
    author_rows(store)
    reread = store.add_evidence("c1", "statements", "doc.checks", {}, HOLDS_1000)
    independent = store.add_evidence("c1", "statements", "fin.get", {}, HOLDS_1000)
    flag(store)
    assert assess(store, support(store, [reread, independent], values={"amount": 1000})).kind == "independent"


def test_absent_lineage_on_every_row_that_holds_the_figure_is_unknown_not_independent(store):
    author_rows(store)
    only = store.add_evidence("c1", "statements", "mystery.tool", {}, HOLDS_1000)
    flag(store)
    got = assess(store, support(store, [only], values={"amount": 1000}))
    assert got.kind == "unknown" and "not known" in got.reason


def test_a_flag_with_one_row_of_unknown_source_cannot_be_shown_to_have_independent_support(store):
    store.add_evidence("c1", "text", "doc.open", {"page": 1}, OFFER)  # E1
    store.add_evidence("c1", "text", "mystery.tool", {}, OFFER)  # E2: could be the very source the support reads
    independent = store.add_evidence("c1", "statements", "fin.get", {}, HOLDS_1000)
    flag(store)
    got = assess(store, support(store, [independent], values={"amount": 1000}))
    assert got.kind == "unknown" and "flag's evidence" in got.reason


def test_a_figure_found_only_in_evidence_the_flag_cites_is_not_the_lanes_own_support(store):
    author_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, UNRELATED)
    flag(store)
    got = assess(store, support(store, ["E1", own], values={"amount": 1000}))  # valid for validation (1000 is in E1), but no row of the lane's own holds it
    assert got.kind == "none" and "own" in got.reason


def test_a_support_that_shares_no_compared_number_stays_unrelated(store):
    author_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, UNRELATED)
    flag(store)
    assert assess(store, support(store, [own], values={"other": 55})).kind == "unrelated"


# --- a flag that states no numbers: every figure and quoted passage of the support must be backed ----------------------------------------------------------------------------------------
def test_without_stated_numbers_a_quote_from_an_independent_row_and_a_figure_only_an_unknown_row_holds_is_not_independent(store):
    author_rows(store)
    independent = store.add_evidence("c1", "statements", "fin.get", {}, {"note": "Customer concentration is 4.12% of revenue"})
    unknown = store.add_evidence("c1", "statements", "mystery.tool", {}, {"receivable_days": 140.0})
    flag(store, contradiction=None)
    got = assess(store, support(store, [independent, unknown], values={"days": 140}, quote=QUOTE))
    assert got.kind == "unknown" and got.relevance == ""


def test_without_stated_numbers_a_quote_and_figure_from_independent_rows_are_model_supported_with_relevance_unchecked(store):
    author_rows(store)
    independent = store.add_evidence("c1", "statements", "fin.get", {}, {"note": "Customer concentration is 4.12% of revenue", "receivable_days": 140.0})
    unrelated_unknown = store.add_evidence("c1", "statements", "mystery.tool", {}, UNRELATED)
    fid = flag(store, contradiction=None)
    sid = support(store, [independent, unrelated_unknown], values={"days": 140}, quote=QUOTE)
    got = assess(store, sid)
    assert got.kind == "independent" and got.relevance == "not checked" and got.sources == ("statements:Example Limited",)
    classify(store, "c1", look(store))
    assert store.entry("c1", fid).status == CORROBORATED and "not checked" in store.entry("c1", fid).reason


def test_without_stated_numbers_a_quote_held_only_by_a_repeated_reading_is_not_independent(store):
    author_rows(store)
    reread = store.add_evidence("c1", "statements", "doc.checks", {}, OFFER)
    independent = store.add_evidence("c1", "statements", "fin.get", {}, {"receivable_days": 140.0})
    flag(store, contradiction=None)
    assert assess(store, support(store, [reread, independent], values={"days": 140}, quote=QUOTE)).kind == "repeated"
