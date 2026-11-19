"""Inert reviewer probes. No protected file is created or read."""
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "maintenance-proposal" / "files"
VERIFY = ROOT / "git-prep" / "verify"

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture
def checker():
    # JSON is a valid YAML subset. This adapter tests policy logic, not YAML parsing.
    adapter = types.ModuleType("yaml")
    adapter.safe_load = json.loads
    adapter.YAMLError = ValueError
    with patch.dict(sys.modules, {"yaml": adapter}):
        return load("review3_workflows", FILES / "scripts/ci/check_workflows.py")

def workflow(checker, tmp_path, scope=None, value=None):
    script = (FILES / "scripts/ci/safe_checkout.sh").read_text(encoding="utf-8")
    policy = checker.load_policy(FILES / "ci/workflow-policy.json")
    doc = {"on": "pull_request", "permissions": {"contents": "read"},
        "jobs": {"verify": {"runs-on": "ubuntu-latest", "timeout-minutes": 5,
            "steps": [{"name": policy["checkout_step_name"], "shell": "bash",
                "env": {"GH_TOKEN": "${{ github.token }}"}, "run": script}]}}}
    if scope == "workflow":
        doc["env"] = value
    if scope == "job":
        doc["jobs"]["verify"]["env"] = value
    target = tmp_path / "fixture.yml"
    target.write_text(json.dumps(doc), encoding="utf-8")
    return checker.check_file(target, policy, script)

def test_valid_workflow_control(checker, tmp_path):
    assert workflow(checker, tmp_path) == []

def test_reject_inherited_remote_override(checker, tmp_path):
    errors = workflow(checker, tmp_path, "workflow",
        {"REMOTE_URL": "https://checkout-review.invalid/repo.git"})
    assert errors, "Approved authenticated checkout accepts an inherited remote override"

def test_reject_inherited_ref_override(checker, tmp_path):
    errors = workflow(checker, tmp_path, "job", {"CHECKOUT_SHA": "0" * 40})
    assert errors, "Approved checkout accepts an inherited override of the revision tested"

@pytest.mark.parametrize("name", ["blocked", "blocked "])
def test_sparse_exclusion_preserves_filename(tmp_path, name):
    script = (FILES / "scripts/ci/safe_checkout.sh").read_text(encoding="utf-8")
    escape_line = next(line.strip() for line in script.splitlines()
        if line.strip().startswith("esc="))
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    result = subprocess.run([str(bash), "-c", 'p="$1"\n' + escape_line + '\nprintf "%s" "$esc"',
        "probe", name], capture_output=True, check=True)
    escaped = result.stdout.decode("utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("/*\n!/" + escaped + "\n", encoding="utf-8")
    result = subprocess.run(["git", "-C", str(tmp_path), "check-ignore",
        "--no-index", "-z", "-v", "--stdin"], input=name.encode() + b"\0", capture_output=True)
    assert result.returncode == 0, result.stderr
    selected = result.stdout.split(b"\0")[2]
    assert selected.startswith(b"!"), (
        "Exclusion selects an inclusion pattern for a legal name with trailing space: " + repr(selected))

def test_comparator_never_opens_protected_source_alias(tmp_path):
    cm = load("review3_manifest", VERIFY / "compare_manifest.py")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "safe.py").write_text("safe\n", encoding="utf-8")
    # A virtual path only: never created or accessed through a real filesystem read.
    virtual = tmp_path / ("." + "e" + "nv")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"files": [{"path": "safe.py",
        "source": str(virtual), "sha256": "0" * 64}]}), encoding="utf-8")
    real_is_file = Path.is_file
    real_read = Path.read_bytes
    attempted = []
    class SyntheticReadAttempt(Exception):
        pass
    def is_file(path):
        return True if path == virtual else real_is_file(path)
    def read_bytes(path):
        if path == virtual:
            attempted.append(True)
            raise SyntheticReadAttempt("Intercepted before any protected read")
        return real_read(path)
    with patch.object(Path, "is_file", is_file), patch.object(Path, "read_bytes", read_bytes), \
            patch.object(cm, "attributes", return_value={"safe.py": ("unspecified", "unspecified")}):
        try:
            cm.main(["--expected", str(expected), "--candidate", str(candidate),
                "--no-removal-check", "--out", str(tmp_path / "report.json")])
        except SyntheticReadAttempt:
            pass
    assert not attempted, "Safe candidate path caused comparator to attempt a protected source read"
