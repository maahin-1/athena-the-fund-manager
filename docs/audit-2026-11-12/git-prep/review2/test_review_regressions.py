"""Reviewer regressions using inert fixtures only.
Workflow JSON is valid YAML. A JSON parsing adapter exercises the validators
after parsing without installing PyYAML; parser coverage is not claimed.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import pytest

ROOT = Path(__file__).resolve().parents[4]
VERIFY = ROOT / "docs/audit-2026-11-12/git-prep/verify"
PROPOSAL = ROOT / "docs/audit-2026-11-12/maintenance-proposal"

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def git(repo, *args, data=None):
    out = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, check=True)
    return out.stdout.decode().strip()

def make_comparison(tmp_path, delete_baseline):
    cm = load("review_compare_manifest", VERIFY / "compare_manifest.py")
    base, cand, source = [tmp_path / x for x in ("base", "candidate", "source")]
    for path in (base, cand, source):
        path.mkdir()
    git(base, "init", "-q")
    git(cand, "init", "-q")
    payload = b"value = 1\n"
    (base / "a.py").write_bytes(payload)
    (base / "baseline_keep.py").write_bytes(b"must stay\n")
    a_id = git(base, "hash-object", "-w", "a.py")
    keep_id = git(base, "hash-object", "-w", "baseline_keep.py")
    tree = git(base, "mktree", data=f"100644 blob {a_id}\ta.py\n100644 blob {keep_id}\tbaseline_keep.py\n".encode())
    (source / "a.py").write_bytes(payload)
    (cand / "a.py").write_bytes(payload)
    git(cand, "add", "--", "a.py")
    if not delete_baseline:
        (cand / "baseline_keep.py").write_bytes(b"must stay\n")
        git(cand, "add", "--", "baseline_keep.py")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"files": [{"path": "a.py", "source": str(source / "a.py"), "sha256": hashlib.sha256(payload).hexdigest()}]}))
    report = tmp_path / "report.json"
    rc = cm.main(["--expected", str(expected), "--candidate", str(cand),
                  "--baseline-repo", str(base), "--baseline-ref", tree, "--out", str(report)])
    return rc, json.loads(report.read_text())

def test_unchanged_baseline_control(tmp_path):
    rc, report = make_comparison(tmp_path, False)
    assert rc == 0, report

def test_unplanned_baseline_deletion_must_be_rejected(tmp_path):
    rc, report = make_comparison(tmp_path, True)
    assert rc == 1, f"Unplanned baseline deletion was accepted: {report}"

@pytest.fixture
def workflow_checker(monkeypatch):
    adapter = types.ModuleType("yaml")
    adapter.safe_load = json.loads
    adapter.YAMLError = ValueError
    monkeypatch.setitem(sys.modules, "yaml", adapter)
    checker = load("review_workflow_checker", PROPOSAL / "files/scripts/ci/check_workflows.py")
    monkeypatch.setitem(sys.modules, "check_workflows", checker)
    return checker

GOOD_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
BAD_SHA = "1" * 40

def workflow(run=None, include_bad=False):
    steps = [{"uses": f"actions/checkout@{GOOD_SHA}", "with": {"persist-credentials": False}}]
    if include_bad:
        steps.append({"uses": f"actions/checkout@{BAD_SHA}", "with": {"persist-credentials": False}})
    if run:
        steps.append({"run": run})
    return {"on": "pull_request", "permissions": {"contents": "read"},
            "jobs": {"test": {"timeout-minutes": 5, "runs-on": "ubuntu-latest", "steps": steps}}}

def test_safe_workflow_control(tmp_path, workflow_checker):
    path = tmp_path / "workflow.yml"
    path.write_text(json.dumps(workflow(run="echo safe")))
    assert workflow_checker.check_file(path) == []

def test_nested_event_expression_must_be_rejected(tmp_path, workflow_checker):
    path = tmp_path / "workflow.yml"
    path.write_text(json.dumps(workflow(run='echo "${{ format(\'{0}\', github.event.pull_request.title) }}"')))
    problems = workflow_checker.check_file(path)
    assert problems, "Nested untrusted event interpolation passed the checker"

def proposal_fixture(tmp_path):
    prop, repo = tmp_path / "proposal", tmp_path / "repo"
    for directory in (prop / ".github/workflows", prop / ".github/ISSUE_TEMPLATE",
                      prop / "scripts/ci", repo / "terminal/web", repo / "scripts",
                      repo / "src/athena/brokers"):
        directory.mkdir(parents=True)
    for rel in ("pyproject.toml", "terminal/Cargo.toml", "terminal/Cargo.lock",
                "terminal/web/package.json", "terminal/web/package-lock.json"):
        (repo / rel).write_text("")
    required = ["/.github/", "/pyproject.toml", "/terminal/Cargo.lock",
                "/terminal/web/package-lock.json", "/scripts/", "/src/athena/brokers/"]
    (prop / ".github/CODEOWNERS").write_text("\n".join(p + " @maintainer" for p in required))
    (prop / ".github/ISSUE_TEMPLATE/bug_report.yml").write_text(json.dumps({
        "name": "Bug", "description": "Bug", "body": [{"id": "repro", "validations": {"required": True}}]}))
    (prop / ".github/dependabot.yml").write_text(json.dumps({
        "version": 2, "updates": [{"package-ecosystem": eco, "directory": directory,
            "schedule": {"interval": "weekly"}, "open-pull-requests-limit": 3}
            for eco, directory in (("pip", "/"), ("cargo", "/terminal"),
                                   ("npm", "/terminal/web"), ("github-actions", "/"))]}))
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"actions/checkout": {"sha": GOOD_SHA, "tag": "review-record"}}))
    return prop, repo, pins

def check_proposal(mod, monkeypatch, prop, repo, pins):
    monkeypatch.setattr(sys, "argv", ["check_proposal.py", "--proposal", str(prop), "--repo", str(repo), "--pins", str(pins)])
    return mod.main()

def test_wrong_pin_alone_is_rejected_control(tmp_path, monkeypatch, workflow_checker):
    prop, repo, pins = proposal_fixture(tmp_path)
    doc = workflow()
    doc["jobs"]["test"]["steps"][0]["uses"] = f"actions/checkout@{BAD_SHA}"
    (prop / ".github/workflows/ci.yml").write_text(json.dumps(doc))
    mod = load("review_check_proposal_control", PROPOSAL / "checks/check_proposal.py")
    assert check_proposal(mod, monkeypatch, prop, repo, pins) == 1

def test_each_pin_must_match_even_when_approved_pin_also_exists(tmp_path, monkeypatch, workflow_checker):
    prop, repo, pins = proposal_fixture(tmp_path)
    (prop / ".github/workflows/ci.yml").write_text(json.dumps(workflow(include_bad=True)))
    mod = load("review_check_proposal_bad", PROPOSAL / "checks/check_proposal.py")
    rc = check_proposal(mod, monkeypatch, prop, repo, pins)
    assert rc == 1, "Wrong checkout SHA was hidden by an approved checkout elsewhere in the same document"
