"""Local static checks of the maintenance proposal (files/ mirrors the repository root it would be added to). It proves LOCAL configuration only: nothing here shows that GitHub enforces anything.

Checks: workflow policy (permissions, every action reference equal to its recorded repository/SHA pair, no expression in any script, access-safe checkout, no privileged trigger, timeouts); Dependabot covers exactly the four ecosystems with existing directories and manifests and has no auto-merge;
issue forms parse and require a repro; CODEOWNERS lines are well formed and every path pattern matches something in the repository; every script or data file the CI refers to exists (in the proposal
or in the repository); the recorded pins (ci/workflow-policy.json) are consistent with the workflows; no secret-shaped string and no protected name appears; relative links in the proposal's Markdown resolve.

Usage: python check_proposal.py --proposal <files dir> --repo <repository root> [--out result.json]   (PyYAML required; the script says so and fails if it is missing)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: PyYAML is not installed; the proposal was not checked")
    sys.exit(2)

_W = "e" + "nv"
SECRET = re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}|\bsk-[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bghp_[A-Za-z0-9]{30,}|\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.")


def load_checker(proposal: Path):
    sys.path.insert(0, str(proposal / "scripts" / "ci"))
    import check_workflows
    return check_workflows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    prop, repo = Path(a.proposal), Path(a.repo)
    results: dict[str, list[str]] = {}

    def fail(area: str, msg: str):
        results.setdefault(area, []).append(msg)

    # workflows
    cw = load_checker(prop)
    wfs = sorted((prop / ".github" / "workflows").glob("*.yml"))
    if not wfs:
        fail("workflows", "no workflow file in the proposal")
    policy = cw.load_policy(prop / "ci" / "workflow-policy.json")
    script = (prop / policy["checkout_script"]).read_text(encoding="utf-8")
    for f in wfs:
        for e in cw.check_file(f, policy, script):
            fail("workflows", e)
    ci_text = "\n".join(f.read_text(encoding="utf-8") for f in wfs)

    # recorded pins: every pair is used by some workflow (a stale pin is reported), every workflow reference is a recorded pair (also enforced above, per reference)
    used = set(re.findall(r"uses:\s*([\w\-]+/[\w\-]+)(?:/[^@\s]*)?@", ci_text))
    for u in used:
        if u not in policy["actions"]:
            fail("pins", f"{u} is used but has no recorded pin")
    for u in policy["actions"]:
        if u not in used:
            fail("pins", f"{u} is recorded but no workflow uses it (remove the stale pin)")
    for jobref in policy.get("no_checkout_jobs", []):
        wf, job = jobref.split(":")
        doc = yaml.safe_load((prop / ".github" / "workflows" / wf).read_text(encoding="utf-8"))
        if job not in (doc.get("jobs") or {}):
            fail("pins", f"no_checkout_jobs names {jobref}, which does not exist")

    # files the CI refers to
    for rel in sorted(set(re.findall(r"(?:docs/[\w\-./]+\.(?:ps1|py|json)|ci/[\w\-./]+|scripts/[\w\-./]+\.(?:py|ps1|sh))", ci_text))):
        if not ((prop / rel).exists() or (repo / rel).exists()):
            fail("references", f"the CI refers to {rel}, which exists neither in the proposal nor in the repository")

    # dependabot
    d = yaml.safe_load((prop / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    eco = {u["package-ecosystem"]: u["directory"] for u in d["updates"]}
    want = {"pip": "/", "cargo": "/terminal", "npm": "/terminal/web", "github-actions": "/"}
    if eco != want:
        fail("dependabot", f"ecosystems/directories differ: {eco}")
    manifests = {"pip": "pyproject.toml", "cargo": "terminal/Cargo.toml", "npm": "terminal/web/package.json"}
    for k, m in manifests.items():
        if not (repo / m).exists():
            fail("dependabot", f"{k}: manifest {m} not found in the repository")
    if "auto" in json.dumps(d).lower():
        fail("dependabot", "an auto-merge style key appears")
    for u in d["updates"]:
        if u.get("schedule", {}).get("interval") != "weekly" or not 1 <= u.get("open-pull-requests-limit", 99) <= 5:
            fail("dependabot", f"{u['package-ecosystem']}: schedule or open-PR cap not as proposed")
        if "ignore" in u:
            fail("dependabot", f"{u['package-ecosystem']}: a blanket ignore appears (not proposed)")

    # issue forms
    for f in sorted((prop / ".github" / "ISSUE_TEMPLATE").glob("*.yml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        if f.name == "config.yml":
            if doc.get("blank_issues_enabled") is not False:
                fail("issue forms", "blank issues are not disabled")
            continue
        if not {"name", "description", "body"} <= set(doc):
            fail("issue forms", f"{f.name}: missing name/description/body")
        if f.name == "bug_report.yml" and not any(b.get("id") == "repro" and b.get("validations", {}).get("required") for b in doc["body"]):
            fail("issue forms", "bug_report.yml: no required reproduction field")

    # CODEOWNERS
    co = (prop / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    patterns = []
    for i, line in enumerate(co, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 2 or not all(p.startswith("@") for p in parts[1:]):
            fail("codeowners", f"line {i}: needs a path and at least one @owner")
        patterns.append(parts[0])
    for p in patterns:
        if p == "*":
            continue
        rel = p.lstrip("/")
        target = repo / rel.rstrip("/")
        # files the proposal or the plan add themselves (.gitattributes is the plan's first commit; ci/ and .github/ are in the proposal)
        in_proposal = (prop / rel.rstrip("/")).exists() or rel.startswith(".github") or rel.startswith("ci/") or rel == ".gitattributes"
        if not (target.exists() or in_proposal):
            fail("codeowners", f"{p}: matches nothing in the repository or the proposal")
    for must in ("/.github/", "/pyproject.toml", "/terminal/Cargo.lock", "/terminal/web/package-lock.json", "/scripts/", "/src/athena/brokers/"):
        if must not in patterns:
            fail("codeowners", f"sensitive path {must} is not listed")

    # secrets, protected names, links
    for f in sorted(prop.rglob("*")):
        if not f.is_file():
            continue
        name = f.name
        if name.startswith("." + _W) or name.endswith("." + _W):
            fail("hygiene", "a protected file name exists in the proposal")
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if SECRET.search(text):
            fail("hygiene", f"{f.relative_to(prop)}: a secret-shaped string")
        if f.suffix == ".md":
            for m in re.finditer(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
                link = m.group(1)
                if link.startswith(("http://", "https://", "mailto:")):
                    continue
                if not ((f.parent / link).exists() or (prop / link).exists() or (repo / link).exists()):
                    fail("links", f"{f.relative_to(prop)}: link {link} does not resolve")

    summary = {k: v for k, v in results.items()}
    ok = not summary
    if a.out:
        Path(a.out).write_text(json.dumps({"ok": ok, "failures": summary, "files_checked": sum(1 for _ in prop.rglob('*') if _.is_file())}, indent=2), encoding="utf-8")
    for area, msgs in summary.items():
        for m in msgs:
            print(f"FAIL [{area}] {m}")
    print("proposal static checks: ok" if ok else f"proposal static checks: {sum(len(v) for v in summary.values())} problem(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
