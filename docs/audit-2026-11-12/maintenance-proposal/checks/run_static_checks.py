"""Runs the local static checks of the maintenance proposal and records the results in a NEW folder: the checks on the proposal itself, the policy tests (real PyYAML), and a negative control in which each
defect below is applied to a fresh copy of the proposal and must make check_proposal.py fail with the expected message. Everything is local and offline; nothing is installed (PyYAML is taken from a
folder given with --pyyaml-path when the interpreter lacks it); nothing is read from or written to GitHub.

Usage: python run_static_checks.py --proposal <files dir> --repo <repository root> --out <new folder> [--pyyaml-path <folder holding yaml>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = HERE / "check_proposal.py"
SHA_CHECKOUT_LIKE = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def sub(text: str, old: str, new: str, count: int = 1) -> str:
    assert old in text, f"defect cannot be applied: {old[:50]!r}"
    return text.replace(old, new, count)


def defect_wrong_sha(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    pol = json.loads((prop / "ci/workflow-policy.json").read_text(encoding="utf-8"))
    good = pol["actions"]["actions/upload-artifact"]["sha"]
    f.write_text(sub(f.read_text(encoding="utf-8"), good, "0" * 40), encoding="utf-8")  # well formed, wrong: one wrong pin among correct ones


def defect_swapped_sha(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    pol = json.loads((prop / "ci/workflow-policy.json").read_text(encoding="utf-8"))
    f.write_text(sub(f.read_text(encoding="utf-8"), pol["actions"]["actions/setup-node"]["sha"], pol["actions"]["actions/setup-python"]["sha"]), encoding="utf-8")


def defect_nested_expression(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "run: python scripts/ci/check_repo_policy.py", "run: echo ${{ format('{0}', github.event.pull_request.title) }}"), encoding="utf-8")


def defect_actions_checkout(prop: Path):
    f = prop / ".github/workflows/dependency-review.yml"
    t = f.read_text(encoding="utf-8")
    f.write_text(sub(t, "    steps:\n", f"    steps:\n      - uses: actions/checkout@{SHA_CHECKOUT_LIKE}\n"), encoding="utf-8")


def defect_changed_checkout_script(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "--depth=1", "--depth=2"), encoding="utf-8")


def defect_workflow_remote_override(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "env:\n  PYTHON_VERSION: '3.13'\n", "env:\n  PYTHON_VERSION: '3.13'\n  REMOTE_URL: https://checkout-review.invalid/repo.git\n"), encoding="utf-8")


def defect_job_revision_override(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "    timeout-minutes: 10\n", "    timeout-minutes: 10\n    env:\n      CHECKOUT_SHA: 0000000000000000000000000000000000000000\n"), encoding="utf-8")


def defect_git_config_variable(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "env:\n  PYTHON_VERSION: '3.13'\n", "env:\n  PYTHON_VERSION: '3.13'\n  GIT_CONFIG_COUNT: '1'\n"), encoding="utf-8")


def defect_env_expression(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "env:\n  PYTHON_VERSION: '3.13'\n  RUST_VERSION: '1.98.1'\n  NODE_VERSION: '24.18.0'\n", "env: ${{ fromJSON(vars.EXTRA_ENV) }}\n"), encoding="utf-8")


def defect_container_job(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "    timeout-minutes: 10\n", "    timeout-minutes: 10\n    container: ubuntu:latest\n"), encoding="utf-8")


def defect_script_honours_overrides_with_a_token(prop: Path):
    """The script file is edited so that an override is no longer refused, while the copies embedded in the workflows keep the approved text. The checker compares each embedded copy with
    scripts/ci/safe_checkout.sh, so changing one without the other must fail."""
    f = prop / "scripts/ci/safe_checkout.sh"
    f.write_text(sub(f.read_text(encoding="utf-8"), 'if [ -n "$fixture" ] || [ -n "${REMOTE_URL:-}" ] || [ -n "${CHECKOUT_SHA:-}" ]; then\n    refuse', 'if false; then\n    refuse'), encoding="utf-8")


def defect_privileged_trigger(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(sub(f.read_text(encoding="utf-8"), "on:\n  pull_request:\n", "on:\n  pull_request_target:\n"), encoding="utf-8")


def defect_no_timeout(prop: Path):
    f = prop / ".github/workflows/ci.yml"
    f.write_text(re.sub(r"(  policy:\n(?:.*\n)*?)    timeout-minutes: 10\n", r"\1", f.read_text(encoding="utf-8"), count=1), encoding="utf-8")


def defect_extra_ecosystem(prop: Path):
    f = prop / ".github/dependabot.yml"
    f.write_text(f.read_text(encoding="utf-8") + "  - package-ecosystem: docker\n    directory: /\n    schedule:\n      interval: weekly\n", encoding="utf-8")


def defect_codeowners_path_removed(prop: Path):
    f = prop / ".github/CODEOWNERS"
    f.write_text("\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.startswith("/pyproject.toml")) + "\n", encoding="utf-8")


DEFECTS = [
    ("a well-formed but wrong SHA beside correct pins", defect_wrong_sha, "recorded pair"),
    ("a SHA that is right for another repository", defect_swapped_sha, "recorded pair"),
    ("a nested expression inside a run script", defect_nested_expression, "expression inside a run script"),
    ("actions/checkout reintroduced", defect_actions_checkout, "actions/checkout is not allowed"),
    ("a changed copy of the checkout script", defect_changed_checkout_script, "byte-for-byte"),
    ("a workflow-level REMOTE_URL", defect_workflow_remote_override, "steers the checkout"),
    ("a job-level CHECKOUT_SHA", defect_job_revision_override, "steers the checkout"),
    ("a workflow-level GIT_CONFIG_COUNT", defect_git_config_variable, "steers the checkout"),
    ("a workflow env that is an expression", defect_env_expression, "literal mapping"),
    ("a job that uses a container", defect_container_job, "container"),
    ("the script edited to honour overrides while a token is present", defect_script_honours_overrides_with_a_token, "byte-for-byte"),
    ("a privileged trigger", defect_privileged_trigger, "privileged trigger"),
    ("a job without a timeout", defect_no_timeout, "no timeout-minutes"),
    ("an extra Dependabot ecosystem", defect_extra_ecosystem, "ecosystems/directories differ"),
    ("a sensitive CODEOWNERS path removed", defect_codeowners_path_removed, "sensitive path"),
]


def run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pyyaml-path", default="")
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        print(f"output folder exists (never reused): {out}")
        return 2
    out.mkdir(parents=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if a.pyyaml_path:
        env["PYTHONPATH"] = a.pyyaml_path
    prop = Path(a.proposal)

    base = run([sys.executable, str(CHECK), "--proposal", str(prop), "--repo", a.repo, "--out", str(out / "proposal.json")], env)
    (out / "proposal.txt").write_text(base.stdout + base.stderr, encoding="utf-8")

    tests = run([sys.executable, "-m", "pytest", str(prop / "tests" / "test_ci_repo_policy.py"), "-p", "no:cacheprovider", "-q", "-rs"], env)
    (out / "policy-tests.txt").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    skipped = re.search(r"(\d+) skipped", tests.stdout) is not None

    control = []
    for label, apply, expected in DEFECTS:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "files"
            shutil.copytree(prop, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            apply(copy)
            r = run([sys.executable, str(CHECK), "--proposal", str(copy), "--repo", a.repo], env)
            text = r.stdout + r.stderr
            control.append({"defect": label, "exit": r.returncode, "failed": r.returncode == 1, "expected_message_found": expected in text, "first_failure": next((l for l in text.splitlines() if l.startswith("FAIL")), "")[:220]})
    (out / "negative-control.json").write_text(json.dumps(control, indent=2), encoding="utf-8")
    (out / "negative-control.txt").write_text("\n".join(f"{'rejected' if c['failed'] and c['expected_message_found'] else 'NOT REJECTED AS EXPECTED'}: {c['defect']} -> {c['first_failure']}" for c in control) + "\n", encoding="utf-8")

    summary = {
        "proposal_check_exit": base.returncode,
        "policy_tests_exit": tests.returncode,
        "policy_tests_summary": next((l for l in reversed(tests.stdout.splitlines()) if " passed" in l or " failed" in l), ""),
        "policy_tests_skipped_any": skipped,
        "negative_control_defects": len(control),
        "negative_control_all_rejected_with_expected_message": all(c["failed"] and c["expected_message_found"] for c in control),
    }
    summary["ok"] = summary["proposal_check_exit"] == 0 and summary["policy_tests_exit"] == 0 and not skipped and summary["negative_control_all_rejected_with_expected_message"]
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
