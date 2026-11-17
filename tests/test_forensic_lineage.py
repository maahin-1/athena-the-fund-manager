"""Source lineage and truthful support status in the forensic ledger (audit A2, WP05).

Before: a flag was `established` when another lane's observation, with any quote or number, cited an evidence id the flag did not. Two ids can read one document, so that was a second reading, not a second source,
and a model-supported flag read as if it had been proven. Now: `established` means a code-owned invariant is violated or a person recorded their verification; a flag backed by an independent source through
another lane is `corroborated` (model-supported, not human-reviewed); a second reading of the same source backs nothing. Everything here is synthetic."""
from __future__ import annotations

import json
from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.forensic import lineage
from athena.forensic.agent import run_agent, submit
from athena.forensic.model import ESTABLISHED, FLAG, OBSERVATION, SINGLE_SOURCE, Case, LaneSpec
from athena.forensic.scoring import CORROBORATED, classify, concern, verify_by_human
from athena.forensic.store import ForensicStore
from athena.forensic.tools import ToolContext, ToolError, base_registry, shown, Tool

CASE = Case("c1", "drhp", "Example Limited", date(2026, 11, 3))
OFFER = {"offer_total": {"crore": 1000.0}, "fresh_issue": {"crore": 800.0}, "offer_for_sale": {"crore": 150.0}, "note": "Customer concentration is 4.12% of revenue"}
FIN = {"offer_total": {"crore": 1000.0}, "receivable_days": {"fy26": 140.0, "fy25": 85.0}, "other": {"crore": 55.0}}
QUOTE = "customer concentration is 4.12% of revenue"
RELATION = {"relation": "equal", "left": {"ev": "E1", "path": "fresh_issue.crore"}, "right": {"ev": "E1", "path": "offer_total.crore"}}


@pytest.fixture
def store(tmp_path):
    s = ForensicStore(tmp_path / "f.duckdb")
    s.add_case(CASE)
    return s


def look(store):
    return lambda ev: store.evidence("c1", ev)


def flag(store, author="text", refs=("E1", "E2"), contradiction=RELATION, flag_type="arithmetic"):
    body = {"text": "the fresh issue differs from the offer total", "flag_type": flag_type, "evidence": list(refs), "innocent": ["a rounding of the printed table"]}
    if contradiction:
        body["contradiction"] = contradiction
    return store.add_entry("c1", FLAG, author, 1, body)


def doc_rows(store):
    return store.add_evidence("c1", "text", "doc.open", {"page": 1}, OFFER), store.add_evidence("c1", "text", "doc.search", {"query": "issue"}, OFFER)  # E1, E2: two tools, one document


def back(store, author, ev, values=None, quote=None, supports="L1", text="a second lane read it"):
    body = {"text": text, "evidence": [ev], "supports": supports}
    if values is not None:
        body["values"] = values
    if quote is not None:
        body["quote"] = quote
    return store.add_entry("c1", OBSERVATION, author, 2, body)


# --- lineage --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_tools_that_read_one_document_share_a_source_and_other_stores_do_not():
    key = lambda tool, args=None: {s.key for s in lineage.sources_of("DOC1", tool, args or {}, {})}  # noqa: E731
    assert key("doc.open") == key("doc.search") == key("doc.map") == key("doc.checks") == {"document:DOC1"}
    assert key("fin.table") == key("fin.detect") == {"statements:DOC1"} and key("fin.table") != key("doc.open")
    assert key("peers.filed", {"symbol": "PEER"}) == {"statements:PEER"} and key("peers.compare", {"symbol": "PEER"}) == {"document:DOC1", "statements:PEER"}
    assert key("own.holdings") == {"shareholding:DOC1"} and key("own.reconcile") == {"shareholding:DOC1", "insider:DOC1"}
    assert key("mkt.summary") == {"prices:DOC1"}
    assert key("some.unknown.tool") == set()  # an unknown lineage is stated as unknown, never guessed


def test_the_digest_of_a_source_is_kept_when_the_result_carries_one():
    (source,) = lineage.sources_of("DOC1", "doc.open", {}, {"sha256": "ab" * 32, "pages": []})
    assert source.digest == "ab" * 32 and source.key == "document:DOC1" and lineage.sources_of("DOC1", "doc.open", {}, {"sha256": 5})[0].digest is None


def test_evidence_keeps_the_lineage_it_was_read_with(store):
    registry = base_registry([Tool("doc.get", "canned", "", lambda ctx, args: OFFER)])
    _, ev = registry.call("doc.get", {}, ToolContext(CASE, store, "text"))
    assert store.evidence("c1", ev)["lineage"] == [{"kind": "document", "ident": "Example Limited", "digest": None}]
    assert lineage.of_row(CASE.target, store.evidence("c1", ev)) == lineage.sources_of(CASE.target, "doc.get", {}, OFFER)
    old = store.add_evidence("c1", "text", "doc.open", {}, OFFER)  # written without lineage (an older row): derived from the tool, the same way
    assert store.evidence("c1", old)["lineage"] is None and {s.key for s in lineage.of_row(CASE.target, store.evidence("c1", old))} == {"document:Example Limited"}


# --- support: repeated readings and independent sources -----------------------------------------------------------------------------------------------------------------------------
def test_a_second_lane_reading_the_same_document_through_another_tool_is_a_repeated_reading_not_corroboration(store):
    doc_rows(store)
    reread = store.add_evidence("c1", "checks", "doc.checks", {}, OFFER)
    fid = flag(store)
    back(store, "checks", reread, values={"fresh": 800}, quote=QUOTE)
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == SINGLE_SOURCE and "same source" in entry.reason and "document:Example Limited" in entry.reason


def test_a_second_lane_from_an_independent_source_with_a_shared_number_corroborates_and_is_not_established(store):
    doc_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, FIN)
    fid = flag(store)
    back(store, "statements", own, values={"total": 1000})
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == CORROBORATED and entry.status != ESTABLISHED
    assert "model" in entry.reason and "not human-reviewed" in entry.reason and "statements:Example Limited" in entry.reason


def test_support_that_shares_no_number_with_what_the_flag_compares_is_unrelated_and_counts_for_nothing(store):
    doc_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, FIN)
    fid = flag(store)
    back(store, "statements", own, values={"unrelated": 55})  # a real number of the statements, but not one the flag is about
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == SINGLE_SOURCE and "unrelated" in entry.reason


def test_a_flag_that_states_no_numbers_can_only_be_model_supported_and_says_the_relevance_is_unchecked(store):
    doc_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, FIN)
    fid = flag(store, contradiction=None, flag_type="text_vs_data")
    back(store, "statements", own, values={"d": 140})
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == CORROBORATED and "relevance" in entry.reason and "not checked" in entry.reason


def test_support_read_through_a_tool_whose_source_is_unknown_is_not_independent(store):
    doc_rows(store)
    own = store.add_evidence("c1", "statements", "mystery.tool", {}, FIN)
    fid = flag(store)
    back(store, "statements", own, values={"total": 1000})
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == SINGLE_SOURCE and "not known" in entry.reason


def test_two_sources_that_disagree_do_not_corroborate_the_flag(store):
    doc_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, {"offer_total": {"crore": 900.0}})
    fid = flag(store)
    back(store, "statements", own, values={"total": 900})  # the statements print a different total: that disagrees with the flag's numbers, it does not back them
    classify(store, "c1", look(store))
    assert store.entry("c1", fid).status == SINGLE_SOURCE and "unrelated" in store.entry("c1", fid).reason


def test_a_code_owned_invariant_is_still_established_and_a_model_chosen_relation_is_not(store):
    code_a, code_b = store.add_evidence("c1", "checks", "doc.checks", {"n": 1}, OFFER), store.add_evidence("c1", "checks", "doc.checks", {"n": 2}, OFFER)
    proven = flag(store, "checks", (code_a, code_b), {**RELATION, "left": {"ev": code_a, "path": "fresh_issue.crore"}, "right": {"ev": code_a, "path": "offer_total.crore"}})
    classify(store, "c1", look(store))
    assert store.entry("c1", proven).status == ESTABLISHED and "code's own invariant" in store.entry("c1", proven).reason


# --- a person's verification ------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_recorded_human_verification_establishes_a_flag_and_survives_reclassification(store):
    doc_rows(store)
    fid = flag(store)
    classify(store, "c1", look(store))
    assert store.entry("c1", fid).status == SINGLE_SOURCE
    verify_by_human(store, "c1", fid, "A. Reviewer", "compared pages 12 and 40 of the draft by hand")
    classify(store, "c1", look(store))
    entry = store.entry("c1", fid)
    assert entry.status == ESTABLISHED and "A. Reviewer" in entry.reason and "human" in entry.reason
    classify(store, "c1", look(store))
    assert store.entry("c1", fid).status == ESTABLISHED and store.verifications("c1")[0]["reviewer"] == "A. Reviewer"


@pytest.mark.parametrize("reviewer, note", [("", "x"), ("  ", "x"), ("A", ""), ("A", "  ")])
def test_a_verification_needs_a_named_reviewer_and_a_note(store, reviewer, note):
    doc_rows(store)
    fid = flag(store)
    with pytest.raises(AthenaError):
        verify_by_human(store, "c1", fid, reviewer, note)
    assert store.verifications("c1") == []


def test_only_a_kept_flag_can_be_verified(store):
    doc_rows(store)
    note = store.add_entry("c1", OBSERVATION, "text", 1, {"text": "x", "evidence": ["E1"]})
    rejected = store.add_entry("c1", FLAG, "text", 1, {"text": "x"}, status="rejected", reason="no")
    for entry_id in (note, rejected, "L99"):
        with pytest.raises(AthenaError):
            verify_by_human(store, "c1", entry_id, "A", "checked")


def test_concern_counts_corroborated_flags_at_the_high_end_only(store):
    doc_rows(store)
    own = store.add_evidence("c1", "statements", "fin.get", {}, FIN)
    flag(store)
    back(store, "statements", own, values={"total": 1000})
    classify(store, "c1", look(store))
    c = concern([e for e in store.entries("c1", kind=FLAG)])
    assert c["established"] == 0 and c["corroborated"] == 1 and c["score_low"] == 0 and c["score_high"] == 3.0


# --- A2: the prose of an entry is the model's, and only what code checked is labelled checked ----------------------------------------------------------------------------------------
def test_a_figure_in_an_observation_that_is_not_in_the_evidence_it_cites_is_rejected(store):
    ev = store.add_evidence("c1", "text", "doc.open", {}, {"text": "The company held its board meeting."})
    _, reason = submit(store, CASE, "text", 1, "OBSERVATION", {"text": "Revenue collapsed by 95% and all creditors refused payment.", "evidence": [ev]})
    assert reason and "95" in reason and "not in" in reason


def test_a_genuine_but_irrelevant_quote_checks_the_quote_and_nothing_more(store):
    ev = store.add_evidence("c1", "text", "doc.open", {}, {"text": "The company held its board meeting on the first of the month."})
    entry_id, reason = submit(store, CASE, "text", 1, "OBSERVATION", {"text": "All creditors refused payment.", "evidence": [ev], "quote": "held its board meeting on the first"})
    assert reason is None
    checked = store.entry("c1", entry_id).body["checked"]
    assert checked == {"quote": True, "values": False, "text_figures": 0, "assertion": "model_unreviewed"}


def test_a_model_cannot_write_its_own_checked_label(store):
    ev = store.add_evidence("c1", "text", "doc.open", {}, OFFER)
    entry_id, _ = submit(store, CASE, "text", 1, "OBSERVATION", {"text": "A note.", "evidence": [ev], "checked": {"quote": True, "assertion": "verified"}})
    assert store.entry("c1", entry_id).body["checked"] == {"quote": False, "values": False, "text_figures": 0, "assertion": "model_unreviewed"}


def test_figures_in_a_flag_text_must_be_in_the_evidence_it_cites_or_compares(store):
    a, b = doc_rows(store)
    ok = submit(store, CASE, "text", 1, "FLAG", {"text": "fresh issue 800 against offer total 1000", "flag_type": "arithmetic", "evidence": [a, b], "innocent": ["rounding"], "contradiction": RELATION})
    assert ok[1] is None
    bad = submit(store, CASE, "text", 1, "FLAG", {"text": "fresh issue 801 against offer total 1000", "flag_type": "document_vs_document", "evidence": [a, b], "innocent": ["rounding"]})
    assert bad[1] and "801" in bad[1]


# --- reopening what a tool result cut ------------------------------------------------------------------------------------------------------------------------------------------------
BIG = {"pages": [{"page": n, "text": f"page {n} " + "x" * 1000} for n in range(1, 9)]}
READER = LaneSpec("text", "reads", ("doc.get", "ledger.read", "notes.read"))


class Script:
    def __init__(self, *actions):
        self.actions = [a if isinstance(a, str) else json.dumps(a) for a in actions]

    def complete(self, system, user):
        return self.actions.pop(0)


def test_a_cut_result_says_how_to_reopen_it_and_reopening_is_bounded_by_offset_and_path(store):
    registry = base_registry([Tool("doc.get", "canned", "", lambda ctx, args: BIG)])
    ctx = ToolContext(CASE, store, "text")
    _, ev = registry.call("doc.get", {}, ctx)
    assert "evidence.read" in shown(BIG, ev) and ev in shown(BIG, ev)
    got, _ = registry.call("evidence.read", {"id": ev, "offset": 0, "limit": 500}, ctx)
    assert got["total_chars"] == len(json.dumps(BIG, ensure_ascii=False, default=str)) and len(got["text"]) == 500 and got["more"] is True and got["next_offset"] == 500
    sub, _ = registry.call("evidence.read", {"id": ev, "path": "pages.2.text", "limit": 40}, ctx)
    assert sub["text"].startswith("page 3") and sub["path"] == "pages.2.text"
    for bad in ({"id": "E99"}, {"id": ev, "path": "pages.99"}, {"id": ev, "offset": -1}, {"id": ev, "limit": 0}, {"id": ev, "offset": "x"}, {}):
        with pytest.raises(ToolError):
            registry.call("evidence.read", bad, ctx)
    huge, _ = registry.call("evidence.read", {"id": ev, "limit": 10**9}, ctx)
    assert len(huge["text"]) == 2000  # a request is cut to what a prompt can carry (READ_LIMIT), whatever was asked


def test_what_was_shown_is_kept_apart_from_what_is_stored_and_unread_material_is_reported_not_counted_as_examined(store):
    registry = base_registry([Tool("doc.get", "canned", "", lambda ctx, args: BIG)])
    run_agent(READER, CASE, store, registry, Script({"action": "tool", "tool": "doc.get", "args": {}}, {"action": "finish", "summary": "read it"}), 1, "look")
    (row,) = store.truncated("c1")
    total = len(json.dumps(BIG, ensure_ascii=False, default=str))
    assert row["evidence"] == "E1" and row["tool"] == "doc.get" and row["total_chars"] == total and row["shown_chars"] == 2500 and row["unread_chars"] == total - 2500
    # the lane reopens the rest, in slices: it is examined only once every character has been shown
    offset, steps = 2500, []
    while offset < total:
        steps.append({"action": "tool", "tool": "evidence.read", "args": {"id": "E1", "offset": offset, "limit": 2000}})
        offset += 2000
    run_agent(READER, CASE, store, registry, Script(*steps, {"action": "finish", "summary": "read the rest"}), 2, "look", step_cap=len(steps) + 1)
    assert store.truncated("c1") == []


def test_the_report_lists_truncated_material_that_nobody_read(store):
    from athena.forensic.supervisor import Supervisor

    registry = base_registry([Tool("doc.get", "canned", "", lambda ctx, args: BIG)])
    script = Script({"action": "tool", "tool": "doc.get", "args": {}}, {"action": "finish", "summary": "read"}, "no advice here")
    report = Supervisor(store, registry, {"text": READER}, script, max_rounds=1, kind_lanes={"drhp": ("text",)}).investigate(CASE)
    assert report["truncated_unread"] and report["truncated_unread"][0]["evidence"] == "E1" and "evidence.read" in report["coverage_note"]
    assert report["status_key"]["established"].startswith("a code-owned") and "not human-reviewed" in report["status_key"]["corroborated"]
