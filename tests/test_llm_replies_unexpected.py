"""Batch 4 review, B4-1: a reply that is not what was expected is not a refusal. A malformed HTTP 200 body, an interruption after the request went out, or a client that reports no request must keep the
reserved unit counted, so a ceiling of one cannot be passed by replies the client could not read. Synthetic transport only; no provider is called."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from athena.contracts import AthenaError
from athena.fallback import HealthRegistry
from athena.llm.budget import BudgetExceeded, Meter, MeteredLLM
from athena.llm.client import OpenAICompatibleClient
from athena.llm.errors import ProviderError
from athena.llm.ledger import classify_failure
from athena.llm.router import FallbackLLM

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


OK = Response(200, {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})


def meter(tmp_path, ceiling):
    (tmp_path / "b.json").write_text(json.dumps({"strong": {"calls_per_month": ceiling}}), encoding="utf-8")
    return Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)


def client(steps, retries=0):
    """One model whose transport calls are counted, so a test can say a request never reached it."""
    queue, posts = list(steps), []

    def post(url, **kw):
        posts.append(url)
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    return OpenAICompatibleClient("nvidia", "m", "https://x/v1", "KEY", post=post, sleep=lambda s: None, retries=retries), posts


def chain(c):
    return FallbackLLM([c], HealthRegistry())


MALFORMED_200 = [
    {"choices": [{}]}, {"choices": [None]}, {"choices": ["text"]}, {"choices": "abc"}, {"choices": [{"message": None}]}, {"choices": [{"message": "hi"}]}, {"choices": [{"message": []}]},
    {"choices": [{"message": {"content": 5}}]}, {"choices": [{"message": {"content": ["a", "b"]}}]}, {"choices": [{"message": {"content": {"text": "x"}}}]},
]


@pytest.mark.parametrize("body", MALFORMED_200, ids=lambda b: json.dumps(b)[:50])
def test_a_malformed_200_reply_still_counts_and_the_next_request_is_blocked_before_any_transport(tmp_path, body):
    m = meter(tmp_path, ceiling=1)
    c, posts = client([Response(200, body), Response(200, body)])
    llm = chain(c)
    with pytest.raises(AthenaError):
        MeteredLLM(llm, "strong", m).complete("a", "b")
    assert len(posts) == 1 and m.calls_this_month("strong") == 1 and m.summary()["strong"]["refused"] == 0
    assert [a["outcome"] for a in llm.last_attempts] == ["bad_reply"] and llm.last_attempts[0]["status"] == 200
    with pytest.raises(BudgetExceeded):
        MeteredLLM(llm, "strong", m).complete("a", "b")
    assert len(posts) == 1  # the second logical request never reached the transport


@pytest.mark.parametrize("body", MALFORMED_200, ids=lambda b: json.dumps(b)[:50])
def test_each_malformed_reply_is_a_recorded_provider_error_and_not_an_unrecorded_key_error(body):
    c, _ = client([Response(200, body)])
    with pytest.raises(ProviderError):
        c.complete("a", "b")
    assert [a["outcome"] for a in c.last_attempts] == ["bad_reply"] and classify_failure(c.last_attempts) == "unknown"


def test_a_malformed_reply_is_retried_and_each_attempt_is_recorded():
    c, posts = client([Response(200, {"choices": [{}]}), OK], retries=1)
    assert c.complete("a", "b") == "hello" and len(posts) == 2
    assert [(a["n"], a["outcome"], a["retry"]) for a in c.last_attempts] == [(1, "bad_reply", False), (2, "ok", True)]


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1), RuntimeError("boom"), ValueError("odd")], ids=lambda e: type(e).__name__)
def test_an_interruption_or_surprise_after_the_request_went_out_keeps_the_unit_counted(tmp_path, exc):
    m = meter(tmp_path, ceiling=1)
    c, posts = client([exc, OK])
    llm = chain(c)
    with pytest.raises(BaseException):  # noqa: B017 (the interruption itself is what is being raised)
        MeteredLLM(llm, "strong", m).complete("a", "b")
    assert len(posts) == 1 and m.calls_this_month("strong") == 1
    with pytest.raises(BudgetExceeded):
        MeteredLLM(llm, "strong", m).complete("a", "b")
    assert len(posts) == 1


def test_an_interruption_while_the_reply_is_decoded_leaves_a_request_that_is_not_a_refusal():
    class Interrupted(Response):
        def json(self):
            raise KeyboardInterrupt

    c, _ = client([Interrupted(200, {})])
    with pytest.raises(KeyboardInterrupt):
        c.complete("a", "b")
    assert [a["outcome"] for a in c.last_attempts] == ["sent"] and classify_failure(c.last_attempts) == "unknown"


def test_a_client_that_was_called_and_reports_no_request_is_not_taken_to_have_sent_none(tmp_path):
    class Silent:
        name = "silent:m"

        def complete(self, system, user):
            raise ProviderError("silent:m: it failed somewhere", retryable=True)

    m = meter(tmp_path, ceiling=1)
    llm = FallbackLLM([Silent()], HealthRegistry())
    with pytest.raises(AthenaError):
        MeteredLLM(llm, "strong", m).complete("a", "b")
    assert llm.last_attempts[0]["outcome"] == "unreported" and m.calls_this_month("strong") == 1 and m.summary()["strong"]["refused"] == 0
    with pytest.raises(BudgetExceeded):
        MeteredLLM(llm, "strong", m).complete("a", "b")


def test_a_chain_that_called_nothing_because_every_model_was_skipped_is_still_free(tmp_path):
    class Skipped:
        name = "skipped:m"

        def complete(self, system, user):
            raise AssertionError("must not be called")

    health = HealthRegistry()
    health.mark_degraded("skipped:m", "no access")
    m = meter(tmp_path, ceiling=1)
    llm = FallbackLLM([Skipped()], health)
    with pytest.raises(AthenaError):
        MeteredLLM(llm, "strong", m).complete("a", "b")
    assert llm.last_attempts == [] and m.calls_this_month("strong") == 0 and m.summary()["strong"]["refused"] == 1


@pytest.mark.parametrize("usage", [{"prompt_tokens": "7"}, {"prompt_tokens": float("nan"), "completion_tokens": float("inf")}, {"prompt_tokens": -3}, {"prompt_tokens": True}, "none", [1, 2], None])
def test_a_malformed_usage_block_leaves_tokens_unreported_and_the_reply_is_still_answered(tmp_path, usage):
    m = meter(tmp_path, ceiling=2)
    reply = Response(200, {"choices": [{"message": {"content": "hello"}}], "usage": usage})
    c, _ = client([reply])
    assert MeteredLLM(chain(c), "strong", m).complete("a", "b") == "hello"
    got = m.summary()["strong"]
    assert got["calls"] == 1 and got["calls_without_usage"] == 1 and got["prompt_tokens"] == 0
