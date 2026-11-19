"""Static policy for GitHub workflow files (a local check, not a replacement for GitHub's own validation or for actionlint).

Each file must parse as YAML (PyYAML; a missing parser fails the check, it never passes silently) and then:
  * never use `pull_request_target` or `workflow_run`, and never use secrets;
  * declare top-level `permissions` that grant nothing beyond `contents: read`, and no job may raise them;
  * give every job a timeout;
  * contain NO `${{ ... }}` expression inside any `run:` script. Nested forms (`format(...)`, `toJSON(...)`, `contains(...)`, `fromJSON(...)`) and every other context are rejected the same way, because a
    script is code: pass a value through `env:` and read it as a shell variable instead;
  * reference every action and reusable workflow (`uses:`, in steps AND at job level) exactly as recorded in ci/workflow-policy.json: the repository must be listed and the 40-hex commit SHA must be the
    recorded one. A SHA that is merely well-formed, or a pin that is right for another repository, fails. Local `./` paths, `docker://` images and expressions in `uses:` are rejected;
  * never use `actions/checkout` (it would write every tracked path, including the protected inherited template, to the runner). Every job not listed under `no_checkout_jobs` must START with the step
    named in the policy ("Access-safe checkout"), whose script is byte-for-byte scripts/ci/safe_checkout.sh, with `shell: bash` and no environment other than `GH_TOKEN: ${{ github.token }}`; no other step
    may receive the token.

Usage: python scripts/ci/check_workflows.py <workflows-dir> [--policy ci/workflow-policy.json] [--checkout-script scripts/ci/safe_checkout.sh]   Exit 0 = pass, 1 = violations, 2 = no parser.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # the check is meaningless without a parser: fail loudly, never pass silently
    print("FAIL: PyYAML is not installed, so the workflow files were not checked")
    sys.exit(2)

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
TOKEN_USE = re.compile(r"github\.token|GITHUB_TOKEN", re.I)
SAFE_ENV = {"GH_TOKEN": "${{ github.token }}"}


def normalise(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().splitlines())


def load_policy(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for repo, pin in doc.get("actions", {}).items():
        if not FULL_SHA.match(pin.get("sha", "")):
            raise ValueError(f"policy pin for {repo} is not a 40-hex SHA")
    if not doc.get("checkout_control_env"):
        raise ValueError("policy lists no checkout_control_env: the checker could not tell which variables steer the checkout")
    return doc


def steers_checkout(name: str, patterns: list[str]) -> bool:
    """True when an environment variable NAME is one of the controls of the checkout (matched case-insensitively: Windows runners treat names that way)."""
    return any(fnmatch.fnmatchcase(str(name).upper(), pat.upper()) for pat in patterns)


def check_env_scope(label: str, env, patterns: list[str]) -> list[str]:
    """Workflow-level and job-level `env:` reach every step, including the checkout. It must be a literal mapping (an expression could produce any name) and may not define a checkout control.
    Values are never printed."""
    if env is None:
        return []
    if not isinstance(env, dict):
        return [f"{label}: env must be a literal mapping (an expression could carry any variable name, including the checkout controls)"]
    return [f"{label}: env defines {name}, which steers the checkout (remote, revision, credentials or git configuration)" for name in env if steers_checkout(name, patterns)]


def check_reference(uses: str, policy: dict) -> str | None:
    """None when the reference is exactly an approved repository/SHA pair, otherwise the reason it is not."""
    if "${{" in uses:
        return "an expression inside `uses:`"
    if uses.startswith(("./", "../")):
        return "a local path is not allowed here"
    if uses.startswith("docker://"):
        return "a container image reference is not allowed here"
    if "@" not in uses:
        return "no ref (`@<sha>`)"
    target, ref = uses.rsplit("@", 1)
    parts = target.split("/")
    if len(parts) < 2 or not all(parts[:2]):
        return "not an owner/repository reference"
    repo = "/".join(parts[:2])
    if repo == "actions/checkout":
        return "actions/checkout is not allowed: use the access-safe checkout step"
    if not FULL_SHA.match(ref):
        return f"`{ref}` is not a full 40-hex commit SHA"
    pin = (policy.get("actions") or {}).get(repo)
    if pin is None:
        return f"{repo} is not in the recorded pins"
    if pin["sha"] != ref:
        return f"{repo} is pinned to {ref[:12]}..., the recorded pair is {pin['sha'][:12]}... ({pin.get('tag', '?')})"
    return None


def check_file(path: Path, policy: dict | None = None, checkout_script: str | None = None) -> list[str]:
    root = path.resolve().parents[2] if len(path.resolve().parents) > 2 else path.parent
    if policy is None:
        policy = load_policy(root / "ci" / "workflow-policy.json")
    if checkout_script is None:
        checkout_script = (root / policy.get("checkout_script", "scripts/ci/safe_checkout.sh")).read_text(encoding="utf-8")
    want_script = normalise(checkout_script)
    step_name = policy.get("checkout_step_name", "Access-safe checkout")
    no_checkout = set(policy.get("no_checkout_jobs", []))

    errs: list[str] = []
    text = path.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"{path.name}: not valid YAML ({e})"]
    if not isinstance(doc, dict):
        return [f"{path.name}: not a mapping"]
    on = doc.get(True, doc.get("on"))  # YAML 1.1 reads the key `on` as True
    triggers = set(on) if isinstance(on, dict) else ({on} if isinstance(on, str) else set(on or []))
    for bad in ("pull_request_target", "workflow_run"):
        if bad in triggers:
            errs.append(f"{path.name}: privileged trigger {bad}")
    perms = doc.get("permissions")
    if perms is None:
        errs.append(f"{path.name}: no top-level permissions block")
    elif perms != {"contents": "read"} and perms != {}:
        errs.append(f"{path.name}: top-level permissions are not read-only: {perms}")
    if re.search(r"\$\{\{[^}]*\bsecrets\b", text) or re.search(r"(?m)^\s*secrets:", text):
        errs.append(f"{path.name}: uses secrets")
    controls = policy["checkout_control_env"]
    errs += check_env_scope(f"{path.name}: workflow-level", doc.get("env"), controls)

    for name, job in (doc.get("jobs") or {}).items():
        where = f"{path.name}: job {name}"
        if "timeout-minutes" not in job:
            errs.append(f"{where} has no timeout-minutes")
        errs += check_env_scope(where, job.get("env"), controls)
        if "container" in job:  # a container brings its own git binary and its own environment, both outside what this policy can see
            errs.append(f"{where}: `container:` is not allowed (its environment and git binary are not covered by the checkout policy, and its env could steer the checkout)")
        jp = job.get("permissions")
        if jp not in (None, {}, {"contents": "read"}):
            errs.append(f"{where} raises permissions: {jp}")
        if "uses" in job:  # a reusable workflow called at job level
            why = check_reference(str(job["uses"]), policy)
            if why:
                errs.append(f"{where}: `{job['uses']}`: {why}")
        steps = job.get("steps") or []
        exempt = f"{path.name}:{name}" in no_checkout
        if not exempt and "uses" not in job:
            first = steps[0] if steps else {}
            if first.get("name") != step_name:
                errs.append(f"{where}: the first step must be `{step_name}` (jobs that read repository files may not use any other checkout)")
        for i, step in enumerate(steps):
            sw = f"{where} step {i}"
            is_safe = step.get("name") == step_name
            uses = step.get("uses")
            if uses:
                why = check_reference(str(uses), policy)
                if why:
                    errs.append(f"{sw}: `{uses}`: {why}")
            run = step.get("run")
            if run is not None and "${{" in str(run):
                exprs = [m.strip() for m in re.findall(r"\$\{\{(.*?)\}\}", str(run), re.S)] or ["(unterminated)"]
                errs.append(f"{sw}: expression inside a run script ({'; '.join(e[:60] for e in exprs[:3])}): pass it through env: instead")
            if "${{" in str(step.get("shell", "")):
                errs.append(f"{sw}: expression inside `shell:`")
            if is_safe:
                if run is None or normalise(str(run)) != want_script:
                    errs.append(f"{sw}: the checkout step's script is not byte-for-byte {policy.get('checkout_script', 'scripts/ci/safe_checkout.sh')}")
                if step.get("shell") != "bash":
                    errs.append(f"{sw}: the checkout step must use `shell: bash`")
                if step.get("env", {}) != SAFE_ENV or uses or step.get("with"):
                    errs.append(f"{sw}: the checkout step may only have env GH_TOKEN: ${{{{ github.token }}}} and nothing else")
            elif TOKEN_USE.search(json.dumps({"env": step.get("env"), "with": step.get("with"), "run": step.get("run")}, default=str)):
                errs.append(f"{sw}: the token is passed to a step other than the access-safe checkout")
    return errs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflows", nargs="?", default=".github/workflows")
    ap.add_argument("--policy", default="ci/workflow-policy.json")
    ap.add_argument("--checkout-script", default="scripts/ci/safe_checkout.sh")
    a = ap.parse_args(argv[1:])
    d = Path(a.workflows)
    files = sorted(list(d.glob("*.yml")) + list(d.glob("*.yaml")))
    if not files:
        print(f"FAIL: no workflow files in {d}")
        return 1
    policy = load_policy(Path(a.policy))
    script = Path(a.checkout_script).read_text(encoding="utf-8")
    errs = [e for f in files for e in check_file(f, policy, script)]
    for e in errs:
        print("FAIL", e)
    if not errs:
        print(f"workflow policy: ok ({len(files)} file(s))")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
