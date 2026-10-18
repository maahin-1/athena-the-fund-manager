import json
import subprocess
import sys
from pathlib import Path

import pytest

from athena.strategies.parse import StrategyError, parse_strategy

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "terminal" / "web" / "test" / "fixtures" / "formula-golden.json"


def load():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_committed_golden_file_is_what_the_engine_gives_now():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "formula_golden.py"), "--check"], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_tree_in_it_is_a_strategy_the_engine_accepts():
    for case in load()["cases"]:
        if case["kind"] == "cond":
            strategy = {"name": case["name"][:50], "entry": case["tree"], "exit": case["tree"]}
        else:  # a number-valued formula is tried as the left side of a comparison
            condition = {"op": "gt", "left": case["tree"], "right": {"const": 0}}
            strategy = {"name": case["name"][:50], "entry": condition, "exit": condition}
        parse_strategy(strategy)


def test_the_limits_the_page_enforces_are_the_engines_own():
    deep = {"const": 1.0}
    for _ in range(9):
        deep = {"arith": "add", "left": deep, "right": {"const": 1.0}}
    with pytest.raises(StrategyError, match="nested more than 8"):
        parse_strategy({"name": "x", "entry": {"op": "gt", "left": deep, "right": {"const": 0}}, "exit": {"op": "gt", "left": {"price": "close"}, "right": {"const": 0}}})
    nine = {"all": [{"op": "gt", "left": {"price": "close"}, "right": {"const": i}} for i in range(9)]}
    with pytest.raises(StrategyError, match="1 to 8 conditions"):
        parse_strategy({"name": "x", "entry": nine, "exit": nine})
    with pytest.raises(StrategyError, match="fast length must be shorter"):
        parse_strategy({"name": "x", "entry": {"op": "gt", "left": {"ind": "macd", "params": {"fast": 26, "slow": 12}}, "right": {"const": 0}}, "exit": {"op": "gt", "left": {"price": "close"}, "right": {"const": 0}}})
