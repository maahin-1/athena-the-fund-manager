import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "src" / "athena" / "indicators_user" / "runner.py"

SMA = '''NAME = "Two-bar average"
PANE = "overlay"


def compute(bars):
    out = []
    for i in range(len(bars)):
        out.append(None if i < 1 else (bars[i]["c"] + bars[i - 1]["c"]) / 2)
    return {"avg": out}
'''
BARS = [{"t": i, "o": 1.0, "h": 2.0, "l": 0.5, "c": float(c), "v": 10.0} for i, c in enumerate([1, 3, 5, 7])]


def run(args, stdin=""):
    result = subprocess.run([sys.executable, "-I", str(RUNNER), *map(str, args)], input=stdin, capture_output=True, text=True, timeout=60)
    return result.returncode, json.loads(result.stdout) if result.stdout.strip() else None, result.stderr


def put(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(tmp_path, name, sha, bars=BARS):
    return run(["run", tmp_path, name, sha], json.dumps(bars))[1]


def test_the_listing_reads_name_pane_and_hash_without_running_the_file(tmp_path):
    sha = put(tmp_path, "avg.py", SMA)
    put(tmp_path, "boom.py", "raise SystemExit('this must not run just to be listed')\nNAME = 'Boom'\n")
    put(tmp_path, "broken.py", "def compute(:\n")
    put(tmp_path, "notes.txt", "not an indicator")
    put(tmp_path, "bad/name.py".replace("/", "_"), "def compute(bars): return {}\n")
    code, listing, _ = run(["list", tmp_path])
    assert code == 0
    by = {row["file"]: row for row in listing}
    assert by["avg.py"] == {"file": "avg.py", "name": "Two-bar average", "pane": "overlay", "sha256": sha, "size": (tmp_path / "avg.py").stat().st_size, "error": None}
    assert by["boom.py"]["name"] == "Boom" and by["boom.py"]["error"] is None  # listed, not run
    assert "syntax error" in by["broken.py"]["error"]
    assert "notes.txt" not in by
    assert by["bad_name.py"]["name"] == "bad_name"  # no NAME: the file's name


def test_a_file_with_an_unsafe_name_or_too_large_is_not_listed_as_usable(tmp_path):
    (tmp_path / "ok.py").write_text("def compute(bars): return {}\n")
    (tmp_path / "big.py").write_text("x = 1\n" * 60_000)
    (tmp_path / "weird$name.py").write_text("def compute(bars): return {}\n")
    code, listing, _ = run(["list", tmp_path])
    by = {row["file"]: row for row in listing}
    assert by["ok.py"]["error"] is None
    assert "too large" in by["big.py"]["error"]
    assert "weird$name.py" not in by


def test_a_missing_folder_lists_nothing(tmp_path):
    assert run(["list", tmp_path / "nowhere"])[1] == []


def test_it_runs_the_file_on_the_bars_and_returns_its_lines(tmp_path):
    sha = put(tmp_path, "avg.py", SMA)
    assert call(tmp_path, "avg.py", sha) == {"lines": {"avg": [None, 2.0, 4.0, 6.0]}}


def test_it_refuses_a_file_that_is_not_the_one_that_was_switched_on(tmp_path):
    put(tmp_path, "avg.py", SMA)
    reply = call(tmp_path, "avg.py", "0" * 64)
    assert "changed" in reply["error"] and "lines" not in reply


def test_what_the_file_prints_does_not_reach_the_reply(tmp_path):
    sha = put(tmp_path, "loud.py", "def compute(bars):\n    print('hello from the indicator')\n    return {'x': [1.0] * len(bars)}\n")
    code, reply, err = run(["run", tmp_path, "loud.py", sha], json.dumps(BARS))
    assert reply == {"lines": {"x": [1.0, 1.0, 1.0, 1.0]}}


@pytest.mark.parametrize(
    "body, message",
    [
        ("return 5", "must return a dict"),
        ("return {}", "at least one line"),
        ("return {'a': [1.0]}", "values for 4 bars"),  # one value for four bars
        ("return {'a': 'abc'}", "a list"),
        ("return {'a': ['x'] * len(bars)}", "numbers"),
        ("return {'a': [True] * len(bars)}", "numbers"),
        ("return {str(i): [1.0] * len(bars) for i in range(9)}", "at most 8 lines"),
        ("return {'x' * 40: [1.0] * len(bars)}", "name"),
        ("raise ValueError('bad input')", "ValueError: bad input"),
        ("return {1: [1.0] * len(bars)}", "name"),
    ],
)
def test_a_result_that_breaks_the_contract_is_an_error_that_says_how(tmp_path, body, message):
    sha = put(tmp_path, "x.py", f"def compute(bars):\n    {body}\n")
    reply = call(tmp_path, "x.py", sha)
    assert message in reply["error"], reply


def test_not_a_number_becomes_empty_and_the_line_of_a_failure_is_named(tmp_path):
    sha = put(tmp_path, "nan.py", "def compute(bars):\n    return {'a': [float('nan'), float('inf'), 1.5, None]}\n")
    assert call(tmp_path, "nan.py", sha) == {"lines": {"a": [None, None, 1.5, None]}}
    sha = put(tmp_path, "boom.py", "def compute(bars):\n    x = 1\n    return {'a': [1 / 0]}\n")
    assert "line 3" in call(tmp_path, "boom.py", sha)["error"]


def test_a_file_without_compute_or_with_a_syntax_error_is_refused(tmp_path):
    sha = put(tmp_path, "none.py", "x = 1\n")
    assert "compute" in call(tmp_path, "none.py", sha)["error"]
    sha = put(tmp_path, "syn.py", "def compute(:\n")
    assert "syntax error" in call(tmp_path, "syn.py", sha)["error"]


def test_bad_input_is_refused_before_anything_runs(tmp_path):
    sha = put(tmp_path, "avg.py", SMA)
    assert "bars" in run(["run", tmp_path, "avg.py", sha], "not json")[1]["error"]
    assert "20,000" in run(["run", tmp_path, "avg.py", sha], json.dumps([BARS[0]] * 20_001))[1]["error"]
    assert "name" in run(["run", tmp_path, "../avg.py", sha], "[]")[1]["error"]
    assert "name" in run(["run", tmp_path, "a/b.py", sha], "[]")[1]["error"]
