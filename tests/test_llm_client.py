import pytest
import requests

from athena.llm.client import OpenAICompatibleClient
from athena.llm.errors import ModelNotAllowed, ProviderError, SpendCapExceeded
from athena.llm.policy import allow_all, allowlist, free_only
from athena.llm.spend import SpendGuard
from llm_fakes import SECRET, FakePost, FakeResponse

def make(post, policy=None, model="some/model", **options):
    sleeps = []
    client = OpenAICompatibleClient(
        "test", model, "https://example.test/v1/", SECRET, policy or allow_all("test"),
        post=post, sleep=sleeps.append, **options,
    )
    client.sleeps = sleeps
    return client


def test_success_sends_an_openai_style_request_and_returns_the_text():
    post = FakePost(FakeResponse(content="  the answer  "))
    client = make(post, max_tokens=500)
    assert client.complete("be brief", "hi") == "  the answer  "
    call = post.calls[0]
    assert call["url"] == "https://example.test/v1/chat/completions"
    assert call["headers"] == {"Authorization": f"Bearer {SECRET}"}
    assert call["json"]["model"] == "some/model"
    assert call["json"]["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert call["json"]["max_tokens"] == 500 and call["json"]["temperature"] == 0


def test_parameter_profile_can_use_max_completion_tokens_and_no_temperature():
    post = FakePost(FakeResponse())
    make(post, token_param="max_completion_tokens", send_temperature=False, max_tokens=300).complete("s", "u")
    sent = post.calls[0]["json"]
    assert sent["max_completion_tokens"] == 300 and "max_tokens" not in sent and "temperature" not in sent


def test_empty_content_from_a_reasoning_model_comes_back_as_an_empty_string():
    assert make(FakePost(FakeResponse(content=None))).complete("s", "u") == ""


def test_a_disallowed_model_is_rejected_when_the_client_is_built_before_any_request():
    post = FakePost(FakeResponse())
    with pytest.raises(ModelNotAllowed):
        make(post, policy=free_only("openrouter"), model="openai/gpt-5.4")
    with pytest.raises(ModelNotAllowed):
        make(post, policy=allowlist("openai", ["gpt-5.4-nano"]), model="gpt-5.4")
    assert post.calls == []


def test_rate_limits_and_server_errors_are_retried_with_backoff_then_succeed():
    post = FakePost(FakeResponse(429), FakeResponse(503), FakeResponse(content="finally"))
    client = make(post, retries=2)
    assert client.complete("s", "u") == "finally"
    assert len(post.calls) == 3 and client.calls == 3
    assert client.sleeps == [2.0, 4.0]


def test_retries_run_out_with_a_retryable_error():
    client = make(FakePost(FakeResponse(500, text="boom")), retries=1)
    with pytest.raises(ProviderError) as caught:
        client.complete("s", "u")
    assert caught.value.retryable and caught.value.status == 500 and client.calls == 2


def test_not_found_and_auth_errors_are_not_retried():
    for status in (401, 403, 404):
        post = FakePost(FakeResponse(status, text="nope"))
        with pytest.raises(ProviderError) as caught:
            make(post).complete("s", "u")
        assert caught.value.status == status and not caught.value.retryable and len(post.calls) == 1


def test_network_errors_and_timeouts_are_retryable():
    post = FakePost(requests.Timeout("slow"), FakeResponse(content="ok"))
    assert make(post, retries=1).complete("s", "u") == "ok"
    with pytest.raises(ProviderError) as caught:
        make(FakePost(requests.ConnectionError("down")), retries=0).complete("s", "u")
    assert caught.value.retryable and caught.value.status is None


def test_the_api_key_never_appears_in_error_messages():
    echo = FakePost(FakeResponse(400, text=f"bad request for key {SECRET}"))
    with pytest.raises(ProviderError) as caught:
        make(echo).complete("s", "u")
    assert SECRET not in str(caught.value) and "<key>" in str(caught.value)


def test_spend_is_recorded_from_reported_usage(tmp_path):
    guard = SpendGuard("test", 1.0, tmp_path / "spend.json")
    client = make(FakePost(FakeResponse(usage={"prompt_tokens": 1000, "completion_tokens": 2000})), guard=guard)
    client.complete("s", "u")
    assert guard.spent() == pytest.approx(0.0225)


def test_missing_usage_is_charged_at_the_worst_case(tmp_path):
    guard = SpendGuard("test", 10.0, tmp_path / "spend.json")
    make(FakePost(FakeResponse()), guard=guard, max_tokens=1000).complete("s" * 30, "u" * 30)
    assert guard.spent() == pytest.approx(SpendGuard.cost(20, 1000))


def test_a_request_past_the_spend_cap_is_blocked_before_it_is_sent(tmp_path):
    guard = SpendGuard("test", 0.001, tmp_path / "spend.json")
    post = FakePost(FakeResponse())
    client = make(post, guard=guard, max_tokens=3000)  # worst case $0.03 against a $0.001 cap
    with pytest.raises(SpendCapExceeded):
        client.complete("s", "u")
    assert post.calls == [] and client.calls == 0


def test_a_200_reply_carrying_a_retryable_error_body_is_retried():
    rate_limited = FakeResponse(body={"error": {"message": "Provider returned error", "code": 429}})
    client = make(FakePost(rate_limited, FakeResponse(content="ok")), retries=1)
    assert client.complete("s", "u") == "ok" and client.calls == 2


def test_a_200_reply_carrying_a_permanent_error_body_is_not_retried():
    gone = FakeResponse(body={"error": {"message": "no such model", "code": 404}})
    post = FakePost(gone)
    with pytest.raises(ProviderError) as caught:
        make(post, retries=2).complete("s", "u")
    assert caught.value.status == 404 and not caught.value.retryable and len(post.calls) == 1


def test_a_200_reply_with_no_choices_or_not_json_is_a_retryable_provider_error():
    for body in ({"unexpected": True}, {"choices": []}, ValueError("not json")):
        with pytest.raises(ProviderError) as caught:
            make(FakePost(FakeResponse(body=body)), retries=0).complete("s", "u")
        assert caught.value.retryable, body
