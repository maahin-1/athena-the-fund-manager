import json
from datetime import datetime, timezone

import pytest

from athena.llm.budget import BudgetExceeded, Meter, MeteredLLM
from athena.llm.router import StaticRouter

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


class Fake:
    names = ["fake-model"]
    last_source = "fake-model"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return "reply"


def meter(tmp_path, ceiling=None, clock=lambda: NOW):
    if ceiling is not None:
        (tmp_path / "b.json").write_text(json.dumps({"strong": {"calls_per_month": ceiling}}), encoding="utf-8")
    return Meter(tmp_path / "u.jsonl", tmp_path / "b.json", clock)


def test_calls_are_counted_by_tier_with_the_model_that_answered(tmp_path):
    m = meter(tmp_path)
    llm = MeteredLLM(Fake(), "mid", m)
    assert llm.complete("sys", "user") == "reply" and llm.names == ["fake-model"] and llm.last_source == "fake-model"
    llm.complete("sys", "user")
    got = m.summary()["mid"]
    assert got["calls"] == 2 and got["chars_in"] == 14 and got["chars_out"] == 10 and got["models"] == {"fake-model": 2} and got["ceiling"] is None


def test_a_call_past_the_ceiling_is_refused_before_it_is_made(tmp_path):
    m = meter(tmp_path, ceiling=2)
    inner = Fake()
    strong = MeteredLLM(inner, "strong", m)
    strong.complete("a", "b")
    strong.complete("a", "b")
    with pytest.raises(BudgetExceeded, match="2 calls this month.*ceiling.*is 2"):
        strong.complete("a", "b")
    assert inner.calls == 2
    MeteredLLM(Fake(), "mid", m).complete("a", "b")  # another tier has no ceiling


def test_a_new_month_starts_the_count_again(tmp_path):
    day = {"now": NOW}
    m = meter(tmp_path, ceiling=1, clock=lambda: day["now"])
    llm = MeteredLLM(Fake(), "strong", m)
    llm.complete("a", "b")
    with pytest.raises(BudgetExceeded):
        llm.complete("a", "b")
    day["now"] = datetime(2026, 11, 1, tzinfo=timezone.utc)
    llm.complete("a", "b")
    assert m.calls_this_month("strong") == 1


def test_a_missing_or_broken_budget_file_means_no_ceiling(tmp_path):
    m = meter(tmp_path)
    assert m.ceilings() == {}
    (tmp_path / "b.json").write_text("not json", encoding="utf-8")
    assert m.ceilings() == {}
    MeteredLLM(Fake(), "strong", m).complete("a", "b")


def test_the_router_hands_out_metered_clients_only_when_given_a_meter(tmp_path):
    inner = Fake()
    assert StaticRouter({"strong": inner}).client_for("judge") is inner
    assert isinstance(StaticRouter({"strong": inner}, meter=meter(tmp_path)).client_for("judge"), MeteredLLM)
