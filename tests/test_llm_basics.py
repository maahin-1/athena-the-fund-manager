import pytest

from athena.llm.envfile import read_setting
from athena.llm.errors import ModelNotAllowed
from athena.llm.policy import OPENAI_CHEAP_MODELS, allow_all, allowlist, free_only


def test_environment_variable_wins_over_the_file(tmp_path):
    env_file = tmp_path / "settings.env"
    env_file.write_text("MY_KEY=from-file\n", encoding="utf-8")
    assert read_setting("MY_KEY", env_file, {"MY_KEY": "from-environ"}) == "from-environ"
    assert read_setting("MY_KEY", env_file, {}) == "from-file"


def test_env_file_parsing_handles_comments_quotes_blanks_and_equals_in_values(tmp_path):
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        '# a comment\n\nA="quoted"\nB=\'single\'\nC=plain=with=equals\n  D = spaced \nNOEQUALS\nEMPTY=\n', encoding="utf-8"
    )
    assert read_setting("A", env_file, {}) == "quoted"
    assert read_setting("B", env_file, {}) == "single"
    assert read_setting("C", env_file, {}) == "plain=with=equals"
    assert read_setting("D", env_file, {}) == "spaced"
    assert read_setting("EMPTY", env_file, {}) == ""
    assert read_setting("NOEQUALS", env_file, {}) == ""
    assert read_setting("MISSING", env_file, {}) == ""


def test_missing_env_file_gives_an_empty_value(tmp_path):
    assert read_setting("ANY", tmp_path / "nope.env", {}) == ""


def test_free_only_policy_accepts_free_ids_and_the_free_router_only():
    policy = free_only("openrouter")
    assert policy.allows("nvidia/nemotron-3-super-120b-a12b:free")
    assert policy.allows("openrouter/free")
    assert not policy.allows("nvidia/nemotron-3-super-120b-a12b")
    assert not policy.allows("openai/gpt-5.4")
    with pytest.raises(ModelNotAllowed, match="free models only"):
        policy.check("anthropic/claude-opus-5-5")


def test_allowlist_policy_rejects_anything_not_listed():
    policy = allowlist("openai", OPENAI_CHEAP_MODELS)
    assert policy.allows("gpt-5.4-nano") and policy.allows("gpt-4o-mini")
    for expensive in ("gpt-5.4", "gpt-5", "o3", "gpt-4o", "gpt-5.4-pro"):
        assert not policy.allows(expensive), expensive
    with pytest.raises(ModelNotAllowed, match="allowlist"):
        policy.check("gpt-5.4")


def test_allow_all_policy_allows_any_id():
    assert allow_all("nvidia").allows("anything/at-all")
