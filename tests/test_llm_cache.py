from athena.llm.budget import Meter, MeteredLLM
from athena.llm.cache import CachedLLM, ReplyCache, key
from athena.llm.router import StaticRouter


class Fake:
    names = ["fake-model"]
    last_source = "fake-model"
    last_usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return f"reply {self.calls}"


def test_the_same_prompt_to_the_same_tier_is_answered_from_disk_and_a_different_one_is_not(tmp_path):
    inner = Fake()
    llm = CachedLLM(inner, "mid", ReplyCache(tmp_path / "c.sqlite"))
    assert llm.complete("sys", "user") == "reply 1" and llm.last_usage["prompt_tokens"] == 10
    assert llm.complete("sys", "user") == "reply 1" and inner.calls == 1
    assert llm.last_source == "cache (fake-model)" and llm.last_usage == {}
    assert llm.complete("sys", "user two") == "reply 2" and inner.calls == 2
    assert CachedLLM(inner, "judge", ReplyCache(tmp_path / "c.sqlite")).complete("sys", "user") == "reply 3"  # another tier is another key
    assert key("mid", "a", "b") != key("mid", "ab", "")  # the join cannot be confused


def test_a_reply_older_than_the_ttl_is_asked_for_again(tmp_path):
    now = {"t": 1000.0}
    cache = ReplyCache(tmp_path / "c.sqlite", ttl_days=7, clock=lambda: now["t"])
    inner = Fake()
    llm = CachedLLM(inner, "mid", cache)
    llm.complete("s", "u")
    now["t"] += 6 * 86400
    llm.complete("s", "u")
    assert inner.calls == 1
    now["t"] += 2 * 86400
    llm.complete("s", "u")
    assert inner.calls == 2


def test_a_cache_that_cannot_be_used_never_fails_the_call(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    inner = Fake()
    llm = CachedLLM(inner, "mid", ReplyCache(blocker / "c.sqlite"))  # a path under a file: cannot be created
    assert llm.complete("s", "u") == "reply 1" and llm.complete("s", "u") == "reply 2"


def test_a_hit_adds_nothing_to_the_meter_and_is_not_stopped_by_the_ceiling(tmp_path):
    import json
    from datetime import datetime, timezone

    (tmp_path / "b.json").write_text(json.dumps({"judge": {"calls_per_month": 1}}), encoding="utf-8")
    meter = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: datetime(2026, 10, 24, tzinfo=timezone.utc))
    router = StaticRouter({"judge": Fake()}, meter=meter, cache=ReplyCache(tmp_path / "c.sqlite"))
    client = router.client_for("judge")
    assert client.complete("s", "u") == "reply 1" and client.complete("s", "u") == "reply 1"  # the second is a hit, though the ceiling of one call is spent
    assert meter.calls_this_month("judge") == 1
    assert isinstance(StaticRouter({"judge": Fake()}, meter=meter).client_for("judge"), MeteredLLM)
