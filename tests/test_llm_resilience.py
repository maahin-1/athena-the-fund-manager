import json
from datetime import datetime, timezone

import pytest

from athena.agents.base import Specialist, SpecialistSpec
from athena.contracts import AthenaError
from athena.fallback import HealthRegistry
from athena.llm.budget import Meter, MeteredLLM
from athena.llm.cache import CachedLLM, ReplyCache, key
from athena.llm.client import OpenAICompatibleClient
from athena.llm.errors import ProviderError
from athena.llm.router import FallbackLLM


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body if not isinstance(self._body, str) else json.loads(self._body)


def client(responses, sleeps=None, provider="openai"):
    queue = list(responses)
    calls = []

    def post(url, **kw):
        calls.append(url)
        return queue.pop(0)

    sleep = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    c = OpenAICompatibleClient(provider, "m", "https://x/v1", "KEY", post=post, sleep=sleep)
    c.calls_list = calls
    return c


OK = Response(200, {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 3}})


def test_a_429_that_means_no_credits_is_not_retried_and_a_real_rate_limit_still_is():
    sleeps = []
    broke = client([Response(429, '{"error": {"message": "You have no credits remaining. Add credits to continue."}}')], sleeps)
    with pytest.raises(ProviderError) as caught:
        broke.complete("s", "u")
    assert caught.value.retryable is False and caught.value.status == 429 and len(broke.calls_list) == 1 and sleeps == []
    limited = client([Response(429, '{"error": "slow down"}'), OK], sleeps)
    assert limited.complete("s", "u") == "hello" and len(limited.calls_list) == 2 and sleeps  # a plain rate limit is waited out


def test_a_model_found_unusable_is_remembered_across_processes_and_forgotten_after_the_ttl(tmp_path):
    now = [1000.0]
    path = tmp_path / "health.json"
    first = HealthRegistry(path, ttl_seconds=3600, clock=lambda: now[0])
    first.mark_degraded("nvidia:gone", "HTTP 404")
    second = HealthRegistry(path, ttl_seconds=3600, clock=lambda: now[0])  # another process starting later
    assert second.is_degraded("nvidia:gone") and second.reason("nvidia:gone") == "HTTP 404"
    now[0] += 3601
    assert not second.is_degraded("nvidia:gone") and HealthRegistry(path, 3600, clock=lambda: now[0]).degraded() == {}
    first.mark_degraded("a", "x")
    first.mark_healthy("a")  # the probe found it working again
    assert not HealthRegistry(path, 3600, clock=lambda: now[0]).is_degraded("a")
    memory = HealthRegistry()  # no path: in memory, never expires
    memory.mark_degraded("b", "x")
    assert memory.is_degraded("b")
    (tmp_path / "broken.json").write_text("not json")
    assert HealthRegistry(tmp_path / "broken.json").degraded() == {}


def test_a_degraded_model_is_skipped_by_the_next_chain_without_a_call(tmp_path):
    dead = client([Response(404, '{"detail": "Not Found"}')], provider="nvidia")
    first = FallbackLLM([dead, client([OK], provider="openrouter")], HealthRegistry(tmp_path / "h.json"))
    assert first.complete("s", "u") == "hello" and len(dead.calls_list) == 1 and first.last_source == "openrouter:m"
    second = FallbackLLM([dead, client([OK], provider="openrouter")], HealthRegistry(tmp_path / "h.json"))  # a new process reads the file
    assert second.complete("s", "u") == "hello" and len(dead.calls_list) == 1  # the dead model was not asked again
    assert second.last_failures[0][0] == "nvidia:m" and "degraded" in second.last_failures[0][1]


def test_failed_calls_and_the_models_tried_first_are_logged_and_failed_calls_do_not_count_against_the_ceiling(tmp_path):
    clock = lambda: datetime(2026, 10, 28, tzinfo=timezone.utc)  # noqa: E731
    meter = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", clock)
    (tmp_path / "b.json").write_text(json.dumps({"mid": {"calls_per_month": 5}}))

    class Dead:
        last_failures = ()
        last_source = ""

        def complete(self, s, u):
            raise AthenaError("all sources failed (a: HTTP 404)")

    with pytest.raises(AthenaError):
        MeteredLLM(Dead(), "mid", meter).complete("s", "u")
    assert meter.calls_this_month("mid") == 0 and meter.rows()[0]["failed"] is True and "HTTP 404" in meter.rows()[0]["error"]

    class Second:
        last_failures = (("nvidia:gone", "skipped, degraded: HTTP 404"),)
        last_source = "openrouter:m"
        last_usage = {}

        def complete(self, s, u):
            return "hello"

    MeteredLLM(Second(), "mid", meter).complete("s", "u")
    row = meter.rows()[1]
    assert row["model"] == "openrouter:m" and row["failures"] == ["nvidia:gone: skipped, degraded: HTTP 404"] and meter.calls_this_month("mid") == 1
    assert meter.summary()["mid"]["calls"] == 1


def test_a_reply_the_caller_rejects_is_dropped_from_the_cache_so_it_does_not_stick(tmp_path):
    cache = ReplyCache(tmp_path / "c.sqlite")

    class Model:
        last_source, last_usage = "m", {}

        def __init__(self):
            self.replies = ["not json", '{"ok": true}']

        def complete(self, s, u):
            return self.replies.pop(0)

    llm = CachedLLM(Model(), "mid", cache)
    assert llm.complete("s", "u") == "not json"
    llm.forget_last()
    assert cache.get(key("mid", "s", "u")) is None
    assert llm.complete("s", "u") == '{"ok": true}'  # asked again, not served the bad one
    assert cache.get(key("mid", "s", "u"))[0] == '{"ok": true}'


GOOD = {"signal": "bullish", "confidence": 90, "reasoning": "The trend is up."}


class Scripted:
    def __init__(self, *replies):
        self.replies, self.forgot = list(replies), 0

    def complete(self, system, user):
        return self.replies.pop(0)

    def forget_last(self):
        self.forgot += 1


def test_a_specialist_asks_the_cache_to_forget_a_rejected_reply_and_clamps_a_capped_confidence():
    spec = SpecialistSpec(name="T", persona="p", critical=("a",), max_confidence=40)
    packet = {"metrics": {"a": {"value": 1, "unit": "x"}}}
    llm = Scripted("garbage", json.dumps(GOOD))
    out = Specialist(spec, llm).analyze(packet)
    assert llm.forgot == 1 and out["confidence"] == 40 and out["signal"] == "bullish"
    uncapped = Specialist(SpecialistSpec(name="T", persona="p", critical=("a",)), Scripted(json.dumps(GOOD))).analyze(packet)
    assert uncapped["confidence"] == 90


def test_the_ownership_and_flow_caps_the_personas_state_in_words_are_in_the_spec():
    from athena.agents.flow import FLOW
    from athena.agents.ownership import OWNERSHIP

    assert OWNERSHIP.max_confidence == 40 and FLOW.max_confidence == 60
