from athena.llm.envfile import read_setting


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
