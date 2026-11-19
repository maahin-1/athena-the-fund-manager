"""Proposal-level regressions with the REAL YAML parser (a missing PyYAML fails the module; nothing is skipped). They repeat, against the whole proposal and check_proposal.py, the cases the second reviewer
supplied (docs/audit-2026-11-12/git-prep/review2): a safe control, a wrong pin on its own, a wrong pin hidden next to an approved pin of the same repository in the same file, and a nested
untrusted event expression inside a script. The reviewer's own fixtures used a JSON adapter instead of a parser and the revision-1 interfaces (`--pins`, `actions/checkout`); these use the
revision-3 interfaces (ci/workflow-policy.json, the access-safe checkout step) and PyYAML.

Run: python -m pytest docs/audit-2026-11-12/maintenance-proposal/checks/test_check_proposal.py -p no:cacheprovider      (PyYAML must be importable)
"""
import json
import re
import shutil
import sys
from pathlib import Path

import yaml  # noqa: F401  (hard import on purpose)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PROPOSAL = HERE.parent / "files"
REPO = HERE.parents[3]  # the repository root (docs/audit-2026-11-12/maintenance-proposal/checks -> root)

import check_proposal as cp  # noqa: E402


def copy_proposal(tmp_path):
    dest = tmp_path / "files"
    shutil.copytree(PROPOSAL, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dest


def run(prop, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_proposal.py", "--proposal", str(prop), "--repo", str(REPO)])
    sys.modules.pop("check_workflows", None)  # load_checker imports it from the copy being checked
    return cp.main()


def edit(prop, rel, fn):
    f = prop / rel
    f.write_text(fn(f.read_text(encoding="utf-8")), encoding="utf-8")


def test_control_the_unmodified_proposal_passes(tmp_path, monkeypatch):
    assert run(copy_proposal(tmp_path), monkeypatch) == 0


def test_a_wrong_pin_on_its_own_is_rejected(tmp_path, monkeypatch, capsys):
    prop = copy_proposal(tmp_path)
    good = json.loads((prop / "ci/workflow-policy.json").read_text())["actions"]["actions/setup-node"]["sha"]
    edit(prop, ".github/workflows/ci.yml", lambda t: t.replace(good, "1" * 40))  # setup-node is used once: its only reference is wrong
    assert run(prop, monkeypatch) == 1
    assert "recorded pair" in capsys.readouterr().out


def test_a_wrong_pin_is_not_hidden_by_an_approved_pin_of_the_same_repository_in_the_same_file(tmp_path, monkeypatch, capsys):
    prop = copy_proposal(tmp_path)
    pol = json.loads((prop / "ci/workflow-policy.json").read_text())
    good = pol["actions"]["actions/upload-artifact"]["sha"]
    text = (prop / ".github/workflows/ci.yml").read_text()
    assert text.count(f"actions/upload-artifact@{good}") >= 2, "the control needs the approved pin to exist elsewhere in the same document"
    edit(prop, ".github/workflows/ci.yml", lambda t: t.replace(f"actions/upload-artifact@{good}", f"actions/upload-artifact@{'1' * 40}", 1))  # ONE reference wrong, the others stay approved
    assert run(prop, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "upload-artifact" in out and "recorded pair" in out


def test_a_nested_untrusted_event_expression_inside_a_script_is_rejected(tmp_path, monkeypatch, capsys):
    prop = copy_proposal(tmp_path)
    edit(prop, ".github/workflows/ci.yml", lambda t: re.sub(r"run: python scripts/ci/check_repo_policy.py", "run: echo \"${{ format('{0}', github.event.pull_request.title) }}\"", t, count=1))
    assert run(prop, monkeypatch) == 1
    assert "expression inside a run script" in capsys.readouterr().out
