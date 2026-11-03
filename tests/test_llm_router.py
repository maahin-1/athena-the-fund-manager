import pytest

from athena.contracts import AllSourcesFailed, AthenaError
from athena.llm.client import OpenAICompatibleClient
from athena.llm.router import PROVIDERS, ROLE_TIERS, TIERS, FallbackLLM, StaticRouter, build_router
from llm_fakes import FakePost, FakeResponse


def client(name, post):
    return OpenAICompatibleClient("p", name, "https://x.test/v1", "k", post=post, sleep=lambda s: None, retries=0)


def test_first_working_model_answers_and_failures_are_recorded():
    llm = FallbackLLM([client("a", FakePost(FakeResponse(500))), client("b", FakePost(FakeResponse(content="from b")))])
    assert llm.complete("s", "u") == "from b"
    assert llm.last_source == "p:b"
    assert llm.last_failures and llm.last_failures[0][0] == "p:a"


def test_an_empty_reply_counts_as_a_failure():
    llm = FallbackLLM([client("a", FakePost(FakeResponse(content=None))), client("b", FakePost(FakeResponse(content="ok")))])
    assert llm.complete("s", "u") == "ok" and llm.last_source == "p:b"


def test_a_model_the_account_cannot_use_is_skipped_for_the_rest_of_the_session():
    first_post = FakePost(FakeResponse(404, text="not found"))
    llm = FallbackLLM([client("a", first_post), client("b", FakePost(FakeResponse(content="ok")))])
    llm.complete("s", "u")
    llm.complete("s", "u")
    assert len(first_post.calls) == 1  # never tried again
    assert llm.health.is_degraded("p:a")


def test_a_transient_failure_does_not_degrade_the_model():
    flaky = FakePost(FakeResponse(503), FakeResponse(content="back"))
    llm = FallbackLLM([client("a", flaky)])
    with pytest.raises(AllSourcesFailed):
        llm.complete("s", "u")
    assert not llm.health.is_degraded("p:a")
    assert llm.complete("s", "u") == "back"


def test_when_every_model_fails_the_error_names_each_one():
    llm = FallbackLLM([client("a", FakePost(FakeResponse(500))), client("b", FakePost(FakeResponse(404)))])
    with pytest.raises(AllSourcesFailed) as caught:
        llm.complete("s", "u")
    assert "p:a" in str(caught.value) and "p:b" in str(caught.value)


def test_static_router_maps_roles_to_tiers_with_a_default():
    tiers = {name: FallbackLLM([client(name, FakePost(FakeResponse()))]) for name in ("cheap", "mid", "strong", "judge")}
    router = StaticRouter(tiers)
    assert router.client_for("resolver") is tiers["cheap"]
    assert router.client_for("specialist") is tiers["mid"]
    assert router.client_for("judge") is tiers["judge"]
    assert router.client_for("something-new") is tiers["mid"]
    with pytest.raises(AthenaError):
        StaticRouter({"cheap": tiers["cheap"]}).client_for("judge")


def test_every_tier_names_known_providers_and_every_role_maps_to_a_tier():
    for tier, entries in TIERS.items():
        assert entries, tier
        for provider, model in entries:
            assert provider in PROVIDERS and model, (tier, provider, model)
    assert set(ROLE_TIERS.values()) <= set(TIERS)


def test_build_router_uses_only_providers_with_keys(tmp_path):
    router = build_router(env_file=tmp_path / "none.env", environ={"NVIDIA_API_KEY": "k1"})
    strong = router.client_for("judge")
    assert isinstance(strong, FallbackLLM)
    assert strong.names == ["nvidia:nvidia/nemotron-3-ultra-550b-a55b"]


def test_build_router_with_all_keys_chains_nvidia_then_openrouter_then_openai(tmp_path):
    environ = {"NVIDIA_API_KEY": "k1", "OPENROUTER_API_KEY": "k2", "OPENAI_API_KEY": "k3"}
    router = build_router(env_file=tmp_path / "none.env", environ=environ)
    names = router.client_for("specialist").names
    assert [n.split(":")[0] for n in names] == ["nvidia", "openrouter", "openai", "nvidia", "openrouter"]  # the last two are the strong model, added when the mid-tier model was withdrawn


def test_build_router_without_any_key_raises(tmp_path):
    with pytest.raises(AthenaError, match="no LLM provider key"):
        build_router(env_file=tmp_path / "none.env", environ={})


def test_openai_clients_get_the_gpt5_parameter_profile(tmp_path):
    environ = {"OPENAI_API_KEY": "k3"}
    router = build_router(env_file=tmp_path / "none.env", environ=environ)
    openai_client = router.client_for("specialist").clients[0]
    assert openai_client.token_param == "max_completion_tokens" and not openai_client.send_temperature


def test_a_model_that_times_out_twice_in_a_row_is_left_alone_for_a_while():
    import requests

    hanging = FakePost(requests.Timeout("slow"), requests.Timeout("slow"), requests.Timeout("slow"))
    llm = FallbackLLM([client("a", hanging), client("b", FakePost(FakeResponse(content="one"), FakeResponse(content="two"), FakeResponse(content="three")))])
    assert [llm.complete("s", "u") for _ in range(3)] == ["one", "two", "three"]
    assert len(hanging.calls) == 2  # the third call did not wait for it
    assert llm.health.is_degraded("p:a") and "timed out" in llm.health.reason("p:a")


def test_an_answer_resets_the_count_of_timeouts():
    import requests

    flaky = FakePost(requests.Timeout("slow"), FakeResponse(content="back"), requests.Timeout("slow"))
    llm = FallbackLLM([client("a", flaky), client("b", FakePost(FakeResponse(content="b1"), FakeResponse(content="b2")))])
    assert llm.complete("s", "u") == "b1"  # a times out once
    assert llm.complete("s", "u") == "back"  # a answers: the count starts again
    assert llm.complete("s", "u") == "b2"  # a times out once more, still not skipped
    assert not llm.health.is_degraded("p:a")


def test_a_slow_model_is_tried_again_after_its_pause():
    from athena.fallback import HealthRegistry

    now = [1000.0]
    health = HealthRegistry(clock=lambda: now[0])
    health.mark_degraded("m", "slow", ttl_seconds=60)
    assert health.is_degraded("m")
    now[0] += 61
    assert not health.is_degraded("m")
