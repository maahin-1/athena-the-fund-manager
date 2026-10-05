from athena.llm.probe import PROBE_USER, main, probe_models
from athena.llm.router import TIERS
from llm_fakes import FakePost, FakeResponse


def distinct_models():
    return {entry for entries in TIERS.values() for entry in entries}


def test_probe_tries_each_distinct_model_once_for_providers_with_keys(tmp_path):
    post = FakePost(FakeResponse(content="OK"))
    results = probe_models(env_file=tmp_path / "none", environ={"NVIDIA_API_KEY": "k"}, post=post)
    nvidia_models = {model for provider, model in distinct_models() if provider == "nvidia"}
    assert {r.model for r in results} == nvidia_models and len(results) == len(nvidia_models)
    assert all(r.ok and r.provider == "nvidia" for r in results)
    assert all(call["json"]["messages"][1]["content"] == PROBE_USER for call in post.calls)


def test_probe_reports_each_failure_kind_without_raising(tmp_path):
    post = FakePost(FakeResponse(404, text="gone"), FakeResponse(content=None), FakeResponse(content="OK"))
    results = probe_models(env_file=tmp_path / "none", environ={"NVIDIA_API_KEY": "k"}, post=post)
    assert [r.ok for r in results] == [False, False, True]
    assert "HTTP 404" in results[0].detail and results[1].detail == "empty reply"


def test_probe_with_no_keys_returns_nothing_and_the_cli_says_so(tmp_path, capsys):
    assert probe_models(env_file=tmp_path / "none", environ={}) == []
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    import os

    saved = {k: os.environ.pop(k) for k in ("NVIDIA_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY") if k in os.environ}
    try:
        assert main(["--env-file", str(empty_env)]) == 1
    finally:
        os.environ.update(saved)
    assert "no provider key is set" in capsys.readouterr().out
