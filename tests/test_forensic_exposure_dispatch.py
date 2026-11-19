"""Batch 4 review, B4-3: a tool result counts as shown to a lane only when a prompt that carries it has been delivered to a model. Output left at the step cap, a prompt refused before dispatch, and a call that
failed after it may have been sent do not count as read; truncation and reopening still work. The fake model records every prompt it receives, and the exposure records are compared with those prompts."""
from __future__ import annotations

import json
from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.forensic.agent import run_agent
from athena.forensic.budget import CallBudget
from athena.forensic.model import Case, LaneSpec
from athena.forensic.store import ForensicStore
from athena.forensic.tools import MAX_SHOWN_CHARS, Tool, ToolContext, base_registry, full_text

CASE = Case("c1", "drhp", "Example Limited", date(2026, 11, 3))
READER = LaneSpec("text", "reads", ("doc.get", "ledger.read", "notes.read"))
SMALL = {"k": "x" * 24}  # 34 characters as stored
SIZE = len(json.dumps(SMALL))
BIG = {"pages": [{"page": n, "text": f"page {n} " + "".join(f"{(n * 7919 + i * 104729) % 99991:05d}" for i in range(200))} for n in range(1, 9)]}  # distinct digits, so a slice is recognisable in a prompt
TOOL = {"action": "tool", "tool": "doc.get", "args": {}}
FINISH = {"action": "finish", "summary": "done"}


@pytest.fixture
def store(tmp_path):
    s = ForensicStore(tmp_path / "f.duckdb")
    s.add_case(CASE)
    return s


def registry(result):
    return base_registry([Tool("doc.get", "canned", "", lambda ctx, args: result)])


class Model:
    """Answers with scripted actions and keeps every prompt it was sent."""

    def __init__(self, *actions, fail_on=None, error=None):
        self.actions, self.prompts, self.fail_on, self.error = [json.dumps(a) for a in actions], [], fail_on, error

    def complete(self, system, user):
        self.prompts.append(user)
        if self.fail_on is not None and len(self.prompts) == self.fail_on:
            raise self.error
        return self.actions.pop(0)


def read(offset, limit=2000):
    return {"action": "tool", "tool": "evidence.read", "args": {"id": "E1", "offset": offset, "limit": limit}}


def test_output_returned_on_the_final_step_was_never_in_any_prompt_and_stays_unread(store):
    model = Model(TOOL)
    result = run_agent(READER, CASE, store, registry(SMALL), model, 1, "look", step_cap=1)
    assert result.status == "step_cap" and len(model.prompts) == 1 and full_text(SMALL) not in model.prompts[0]
    assert store.truncated("c1") == [{"evidence": "E1", "tool": "doc.get", "total_chars": SIZE, "shown_chars": 0, "unread_chars": SIZE, "possibly_shown_chars": 0}]


def test_output_delivered_with_the_next_call_is_credited_and_matches_what_the_model_received(store):
    model = Model(TOOL, FINISH)
    run_agent(READER, CASE, store, registry(SMALL), model, 1, "look")
    assert len(model.prompts) == 2 and full_text(SMALL) in model.prompts[1] and full_text(SMALL) not in model.prompts[0]
    assert store.truncated("c1") == []  # shown whole


def test_a_cut_result_is_credited_for_exactly_the_characters_the_prompt_carried(store):
    model = Model(TOOL, FINISH)
    run_agent(READER, CASE, store, registry(BIG), model, 1, "look")
    text = full_text(BIG)
    assert text[:MAX_SHOWN_CHARS] in model.prompts[1] and text[:MAX_SHOWN_CHARS + 40] not in model.prompts[1]
    (row,) = store.truncated("c1")
    assert row["shown_chars"] == MAX_SHOWN_CHARS and row["unread_chars"] == len(text) - MAX_SHOWN_CHARS and row["possibly_shown_chars"] == 0


def test_a_prompt_refused_by_the_case_budget_before_dispatch_credits_nothing(store):
    model = Model(TOOL, FINISH)
    budget = CallBudget(total=1, store=store, case_id="c1")
    result = run_agent(READER, CASE, store, registry(SMALL), budget.client(model, "text", 1), 1, "look")
    assert result.status == "budget" and len(model.prompts) == 1  # the second prompt never reached the model
    assert store.truncated("c1")[0]["shown_chars"] == 0 and store.truncated("c1")[0]["unread_chars"] == SIZE and store.truncated("c1")[0]["possibly_shown_chars"] == 0


def test_a_call_that_failed_after_it_may_have_been_sent_is_reported_as_possibly_shown_and_not_counted(store):
    model = Model(TOOL, fail_on=2, error=AthenaError("every model failed"))
    result = run_agent(READER, CASE, store, registry(SMALL), model, 1, "look")
    assert result.status == "failed" and len(model.prompts) == 2 and full_text(SMALL) in model.prompts[1]  # the prompt did go to the fake model, which then failed
    (row,) = store.truncated("c1")
    assert row["shown_chars"] == 0 and row["possibly_shown_chars"] == SIZE and row["unread_chars"] == SIZE


def test_reopening_in_slices_credits_each_slice_when_it_is_delivered_and_the_last_one_only_with_the_final_call(store):
    total = len(full_text(BIG))
    offsets = list(range(MAX_SHOWN_CHARS, total, 2000))
    model = Model(TOOL, *(read(o) for o in offsets), FINISH)
    run_agent(READER, CASE, store, registry(BIG), model, 1, "look", step_cap=len(offsets) + 2)
    assert store.truncated("c1") == []
    for step, offset in enumerate(offsets, start=2):  # the prompt that follows each slice carries exactly that slice
        assert full_text(BIG)[offset : offset + 40] in model.prompts[step]


def test_the_last_slice_left_at_the_step_cap_is_still_unread(store):
    total = len(full_text(BIG))
    offsets = list(range(MAX_SHOWN_CHARS, total, 2000))
    model = Model(TOOL, *(read(o) for o in offsets))  # no finishing call: the cap ends the run after the last slice came back
    run_agent(READER, CASE, store, registry(BIG), model, 1, "look", step_cap=len(offsets) + 1)
    (row,) = store.truncated("c1")
    last_start = offsets[-1]
    assert row["shown_chars"] == last_start and row["unread_chars"] == total - last_start
    assert full_text(BIG)[last_start : last_start + 40] not in "".join(model.prompts)


def test_reading_one_field_by_path_credits_none_of_the_stored_payload(store):
    model = Model(TOOL, {"action": "tool", "tool": "evidence.read", "args": {"id": "E1", "path": "pages.2.text", "limit": 40}}, FINISH)
    run_agent(READER, CASE, store, registry(BIG), model, 1, "look")
    (row,) = store.truncated("c1")
    assert row["shown_chars"] == MAX_SHOWN_CHARS and "page 3" in model.prompts[2]


def test_a_queued_result_whose_text_is_not_in_the_delivered_prompt_is_not_credited(store):
    from athena.forensic.agent import _Dispatch, _Queued, _settle

    store.add_evidence("c1", "text", "doc.get", {}, SMALL)
    store.add_exposure("c1", "text", "E1", 0, 0, SIZE, "queued")
    sent = _Dispatch(Model("{}"))
    sent.complete("s", "u")  # a prompt was delivered, but it does not carry the result's history line (the line has rolled out of the window, or was never added)
    queue = [_Queued("step 9: doc.get({}) -> E1: <the text>", "E1", 0, SIZE, SIZE, "tool")]
    _settle(store, CASE, "text", queue, "a prompt without that line", sent)
    assert queue and store.truncated("c1")[0]["shown_chars"] == 0
    _settle(store, CASE, "text", queue, "a prompt with step 9: doc.get({}) -> E1: <the text> in it", sent)
    assert queue == [] and store.truncated("c1") == []


def test_calling_the_reopen_tool_directly_records_nothing_because_nothing_was_shown(store):
    reg = registry(BIG)
    ctx = ToolContext(CASE, store, "text")
    _, ev = reg.call("doc.get", {}, ctx)
    reg.call("evidence.read", {"id": ev, "offset": 0, "limit": 500}, ctx)
    assert store.truncated("c1") == []  # no exposure rows at all: only a lane that is given the text can have read it
