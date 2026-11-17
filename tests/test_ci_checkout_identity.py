"""scripts/ci/record_checkout_identity.sh: the record of which commit a CI job actually has checked out (commit, tree, parents, the event's commit and, for a pull request, its head and base).

The first hosted run could not later say what its jobs had tested: for a pull_request event GITHUB_SHA is a temporary merge commit that GitHub regenerates, so its identity has to be written down by the job
itself. These tests build real throwaway repositories (no network, no token) and run the real script in Git Bash.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "record_checkout_identity.sh"


def _bash() -> str:
    for c in (os.environ.get("BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if c and Path(c).exists():
            return c
    raise AssertionError("no bash found for the checkout-identity test")


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not (k.startswith(("GIT_", "GITHUB_", "PR_", "IDENTITY_", "RUNNER_")) or k == "GH_TOKEN")}
    env.update(extra)
    return env


def _origin(tmp_path: Path, *, merge_tree: str = "head") -> dict:
    """An origin shaped like a pull request: base commit B, head commit H ahead of it, and a merge commit M with parents (B, H), as GitHub's temporary merge commit has.
    merge_tree='head' gives M the tree of H (B is an ancestor of H, so the merge adds nothing); merge_tree='base' gives it a different tree."""
    o = tmp_path / "origin"
    o.mkdir()
    _git(o, "init", "-q", "-b", "main", ".")
    (o / "a.txt").write_text("base\n", encoding="utf-8")
    _git(o, "add", "a.txt")
    _git(o, "commit", "-q", "-m", "base")
    base = _git(o, "rev-parse", "HEAD")
    base_tree = _git(o, "rev-parse", "HEAD^{tree}")
    _git(o, "switch", "-q", "-c", "feature")
    (o / "b.txt").write_text("head\n", encoding="utf-8")
    _git(o, "add", "b.txt")
    _git(o, "commit", "-q", "-m", "head")
    head = _git(o, "rev-parse", "HEAD")
    head_tree = _git(o, "rev-parse", "HEAD^{tree}")
    tree = head_tree if merge_tree == "head" else base_tree
    merge = _git(o, "commit-tree", tree, "-p", base, "-p", head, "-m", "temporary merge")
    _git(o, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(o, "switch", "-q", "--detach", merge)
    return {"path": o, "base": base, "head": head, "merge": merge, "merge_tree": tree, "head_tree": head_tree}


def _run(cwd: Path, out: Path, **env: str):
    r = subprocess.run([_bash(), str(SCRIPT)], cwd=cwd, env=_clean_env(IDENTITY_DIR=str(out), **env), capture_output=True, text=True)
    return r, (json.loads((out / "checkout-identity.json").read_text(encoding="utf-8")) if (out / "checkout-identity.json").exists() else None)


def test_it_records_the_commit_its_tree_and_both_parents_of_a_pull_request_merge_commit(tmp_path):
    o = _origin(tmp_path)
    r, doc = _run(o["path"], tmp_path / "id", GITHUB_SHA=o["merge"], PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"], GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/1/merge", GITHUB_REPOSITORY="owner/repo")
    assert r.returncode == 0, r.stderr
    assert doc["schema"] == "athena-checkout-identity-1"
    assert doc["git"]["head"] == o["merge"] and doc["git"]["tree"] == o["merge_tree"]
    assert doc["git"]["parents"] == [o["base"], o["head"]] and doc["git"]["parent_count"] == 2
    assert doc["relations"] == {"head_equals_github_sha": True, "github_sha_is_pull_request_head": False, "pull_request_head_is_a_parent": True, "pull_request_base_is_a_parent": True}
    assert doc["github"]["event_name"] == "pull_request" and doc["github"]["sha"] == o["merge"]
    assert doc["pull_request"] == {"head_sha": o["head"], "base_sha": o["base"]}


def test_a_shallow_checkout_still_lists_both_parents(tmp_path):
    o = _origin(tmp_path)
    work = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--no-local", "--no-checkout", o["path"].as_uri(), str(work))
    _git(work, "fetch", "-q", "--depth", "1", "origin", o["merge"])
    _git(work, "checkout", "-q", "--detach", o["merge"])
    r, doc = _run(work, tmp_path / "id", GITHUB_SHA=o["merge"], PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"])
    assert r.returncode == 0, r.stderr
    assert doc["git"]["shallow"] == "true"
    assert doc["git"]["parents"] == [o["base"], o["head"]]


def _parent_like_message(o: dict) -> str:
    """A message whose subject and body hold lines shaped exactly like commit-header parent lines (one naming a real commit, one a made-up id)."""
    return f"parent {o['head']}\n\nparent {'1' * 40}\nparent {o['base']}\n"


def test_parent_like_lines_in_the_commit_message_are_not_parents(tmp_path):
    o = _origin(tmp_path)
    merge = _git(o["path"], "commit-tree", o["head_tree"], "-p", o["base"], "-p", o["head"], "-m", _parent_like_message(o))
    single = _git(o["path"], "commit-tree", o["head_tree"], "-p", o["head"], "-m", _parent_like_message(o))
    assert f"parent {'1' * 40}" in _git(o["path"], "cat-file", "commit", merge), "the message really holds the parent-like lines"
    for commit, want in ((merge, [o["base"], o["head"]]), (single, [o["head"]])):
        _git(o["path"], "switch", "-q", "--detach", commit)
        r, doc = _run(o["path"], tmp_path / f"id-{commit[:7]}", GITHUB_SHA=commit, PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"])
        assert r.returncode == 0, r.stderr
        assert doc["git"]["parents"] == want and doc["git"]["parent_count"] == len(want)
    assert doc["relations"]["pull_request_base_is_a_parent"] is False, "the base named only in the message is not a parent of the single-parent commit"


def test_a_shallow_checkout_with_parent_like_message_lines_lists_only_the_real_parents(tmp_path):
    o = _origin(tmp_path)
    merge = _git(o["path"], "commit-tree", o["head_tree"], "-p", o["base"], "-p", o["head"], "-m", _parent_like_message(o))
    work = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--no-local", "--no-checkout", o["path"].as_uri(), str(work))
    _git(work, "fetch", "-q", "--depth", "1", "origin", merge)
    _git(work, "checkout", "-q", "--detach", merge)
    r, doc = _run(work, tmp_path / "id", GITHUB_SHA=merge, PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"])
    assert r.returncode == 0, r.stderr
    assert doc["git"]["shallow"] == "true"
    assert doc["git"]["parents"] == [o["base"], o["head"]]


def test_the_tree_of_the_pull_request_head_is_compared_when_it_can_be_fetched(tmp_path):
    o = _origin(tmp_path)
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--no-local", "--no-checkout", o["path"].as_uri(), str(work))
    _git(work, "fetch", "-q", "--no-tags", "origin", o["merge"])
    _git(work, "checkout", "-q", "--detach", o["merge"])
    r, doc = _run(work, tmp_path / "id", GITHUB_SHA=o["merge"], PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"])
    assert r.returncode == 0, r.stderr
    assert doc["tree_comparison"]["pull_request_head_tree"] == o["head_tree"]
    assert doc["tree_comparison"]["equals_checked_out_tree"] is True


def test_a_checked_out_tree_that_differs_from_the_pull_request_head_tree_is_reported_as_different(tmp_path):
    o = _origin(tmp_path, merge_tree="base")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--no-local", "--no-checkout", o["path"].as_uri(), str(work))
    _git(work, "fetch", "-q", "--no-tags", "origin", o["merge"])
    _git(work, "checkout", "-q", "--detach", o["merge"])
    r, doc = _run(work, tmp_path / "id", GITHUB_SHA=o["merge"], PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"])
    assert r.returncode == 0, r.stderr
    assert doc["tree_comparison"]["equals_checked_out_tree"] is False


def test_it_records_a_disagreement_and_does_not_hide_or_decide_it(tmp_path):
    o = _origin(tmp_path)
    other = "0" * 40
    r, doc = _run(o["path"], tmp_path / "id", GITHUB_SHA=other, PR_HEAD_SHA=o["head"], PR_BASE_SHA=o["base"])
    assert r.returncode == 0, "the script records; it does not fail the job"
    assert doc["relations"]["head_equals_github_sha"] is False
    assert doc["git"]["head"] == o["merge"] and doc["github"]["sha"] == other


def test_outside_a_pull_request_the_pull_request_fields_are_null_and_nothing_is_fetched(tmp_path):
    o = _origin(tmp_path)
    r, doc = _run(o["path"], tmp_path / "id", GITHUB_SHA=o["merge"], GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/main")
    assert r.returncode == 0, r.stderr
    assert doc["pull_request"] == {"head_sha": None, "base_sha": None}
    assert doc["tree_comparison"]["equals_checked_out_tree"] is None and doc["tree_comparison"]["pull_request_head_tree"] is None
    assert doc["relations"]["github_sha_is_pull_request_head"] is False


def test_a_quote_in_a_reference_name_keeps_the_json_valid_and_no_token_is_read_or_written(tmp_path):
    o = _origin(tmp_path)
    secret = "ghs_" + "a1B2" * 9
    r, doc = _run(o["path"], tmp_path / "id", GITHUB_SHA=o["merge"], GITHUB_REF='refs/heads/odd"name', GH_TOKEN=secret)
    assert r.returncode == 0, r.stderr
    assert doc["github"]["ref"] == 'refs/heads/odd"name'
    assert secret not in (tmp_path / "id" / "checkout-identity.json").read_text(encoding="utf-8") and secret not in r.stdout + r.stderr
    text = SCRIPT.read_text(encoding="utf-8")
    assert "GH_TOKEN" not in text and "github.token" not in text.lower() and "GITHUB_TOKEN" not in text


def test_it_refuses_to_run_without_an_output_folder(tmp_path):
    o = _origin(tmp_path)
    r = subprocess.run([_bash(), str(SCRIPT)], cwd=o["path"], env=_clean_env(), capture_output=True, text=True)
    assert r.returncode != 0 and "IDENTITY_DIR is not set" in r.stderr
