"""Tests of scripts/ci/check_repo_policy.py, scripts/ci/check_workflows.py and scripts/ci/safe_checkout.sh: each rule fails on a deliberately bad input and passes on a good one. Synthetic throwaway repositories only.
No file with a protected name is created on disk: such an entry is made in a temporary repository's INDEX only (git update-index --cacheinfo), which is all the checker looks at."""
import hashlib
import subprocess
import sys
from pathlib import Path

CI = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI))
import check_repo_policy as rp  # noqa: E402

PROTECTED = "." + ("e" + "nv") + ".sample"


def git(repo, *args, stdin=None):
    r = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def repo_with(tmp_path, files=None, index_only=None):
    git(tmp_path, "init", "-q")
    for name, data in (files or {}).items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        git(tmp_path, "add", "-f", "--", name)
    for name, data in (index_only or {}).items():
        oid = git(tmp_path, "hash-object", "-w", "--stdin", stdin=data).strip()
        git(tmp_path, "update-index", "--add", "--cacheinfo", f"100644,{oid},{name}")
    return tmp_path


def run(repo, **kw):
    return rp.check(repo, kw.get("max_bytes", rp.DEFAULT_MAX), kw.get("inherited", set()), kw.get("allow_large", set()))


def test_a_clean_tree_passes(tmp_path):
    bad = run(repo_with(tmp_path, {"src/a.py": b"x = 1\n", "docs/a.md": b"# a\n"}))
    assert not any(bad.values()), bad


def test_runtime_build_and_store_paths_fail(tmp_path):
    bad = run(repo_with(tmp_path, {".athena/x.json": b"{}", "terminal/target/debug/x": b"1", "w.duckdb": b"1", "a/__pycache__/m.pyc": b"1", "ok.py": b"1"}))
    assert sorted(bad["runtime_or_build_paths"]) == [".athena/x.json", "a/__pycache__/m.pyc", "terminal/target/debug/x", "w.duckdb"], bad


def test_an_oversized_file_fails_unless_it_is_on_the_reviewed_allowlist(tmp_path):
    repo = repo_with(tmp_path, {"big.bin": b"x" * 2048})
    assert run(repo, max_bytes=1024)["oversized"]
    assert not run(repo, max_bytes=1024, allow_large={"big.bin"})["oversized"]


def test_a_protected_name_fails_unless_it_is_exactly_the_recorded_inherited_entry(tmp_path):
    repo = repo_with(tmp_path, index_only={PROTECTED: "synthetic\n"})
    assert run(repo)["protected_not_inherited"] == ["<protected name withheld>"]
    oid = git(repo, "ls-files", "-s", "--", PROTECTED).split()[1]
    inherited = {hashlib.sha256(f"{PROTECTED}\0{oid}".encode()).hexdigest()}
    assert not run(repo, inherited=inherited)["protected_not_inherited"]
    # the same name with different content (a CHANGED inherited entry) fails again
    oid2 = git(repo, "hash-object", "-w", "--stdin", stdin="changed\n").strip()
    git(repo, "update-index", "--cacheinfo", f"100644,{oid2},{PROTECTED}")
    assert run(repo, inherited=inherited)["protected_not_inherited"]


def test_the_failure_output_never_prints_a_protected_name(tmp_path, capsys):
    repo = repo_with(tmp_path, index_only={PROTECTED: "synthetic\n"})
    rc = rp.main(["--repo", str(repo), "--inherited", "none", "--allow-large", "none"])
    out = capsys.readouterr().out
    assert rc == 1 and PROTECTED not in out and "protected_not_inherited" in out


# ---- workflow policy checker: always run with the REAL YAML parser (a missing PyYAML is a failure here, never a skip) ----

import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import textwrap  # noqa: E402

import yaml  # noqa: E402  (PyYAML is in ci/python-constraints.txt; an ImportError fails the whole module on purpose)

import check_workflows as cw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 40
SHA_B = "b" * 40
SCRIPT = "set -euo pipefail\necho checkout\n"
POLICY = {
    "actions": {"actions/setup-python": {"tag": "v1", "sha": SHA_A}, "actions/upload-artifact": {"tag": "v2", "sha": SHA_B}},
    "checkout_step_name": "Access-safe checkout",
    "checkout_script": "scripts/ci/safe_checkout.sh",
    "no_checkout_jobs": ["w.yml:gate"],
    "checkout_control_env": ["REMOTE_URL", "CHECKOUT_SHA", "ATHENA_CHECKOUT_*", "GH_TOKEN", "GITHUB_*", "GIT_*", "HOME", "USERPROFILE", "XDG_*", "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4", "BASH_XTRACEFD", "PATH", "LD_*", "*_PROXY"],
}


def checkout_step(script=SCRIPT):
    indented = textwrap.indent(script, " " * 10)
    return f"      - name: Access-safe checkout\n        shell: bash\n        env:\n          GH_TOKEN: ${{{{ github.token }}}}\n        run: |\n{indented}"


def workflow(extra_steps="      - run: echo ok\n", checkout=None, on="[pull_request]", perms="permissions:\n  contents: read\n", job_extra="", top_extra=""):
    return (f"name: t\non: {on}\n{perms}{top_extra}jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n{job_extra}    steps:\n"
            f"{checkout if checkout is not None else checkout_step()}{extra_steps}")


def errors(tmp_path, text, policy=POLICY, script=SCRIPT):
    f = tmp_path / "w.yml"
    f.write_text(text)
    return cw.check_file(f, policy, script)


def test_the_parser_in_use_is_the_real_pyyaml():
    assert yaml.__version__ and hasattr(yaml, "safe_load") and yaml.safe_load("a: [1, 2]") == {"a": [1, 2]}


def test_a_good_workflow_passes(tmp_path):
    good = workflow(extra_steps=f"      - uses: actions/setup-python@{SHA_A}\n      - run: echo ok\n")
    assert errors(tmp_path, good) == []


def test_each_basic_defect_is_rejected(tmp_path):
    good = workflow()
    cases = {
        "pull_request_target": good.replace("[pull_request]", "[pull_request_target]"),
        "workflow_run": good.replace("[pull_request]", "[workflow_run]"),
        "no permissions": good.replace("permissions:\n  contents: read\n", ""),
        "write permissions": good.replace("contents: read", "contents: write"),
        "job raises permissions": workflow(job_extra="    permissions:\n      contents: write\n"),
        "no timeout": good.replace("    timeout-minutes: 5\n", ""),
        "secrets": good.replace("echo ok", "echo ${{ secrets.X }}"),
        "the token reaches another step": good.replace("      - run: echo ok\n", "      - run: echo ok\n        env:\n          GH_TOKEN: ${{ github.token }}\n"),
    }
    for name, text in cases.items():
        assert errors(tmp_path, text), f"not rejected: {name}"


def test_event_text_and_every_nested_expression_in_a_script_is_rejected(tmp_path):
    bodies = [
        "echo ${{ github.event.pull_request.title }}",
        "echo ${{ format('{0}', github.event.pull_request.title) }}",
        "echo ${{ toJSON(github.event) }}",
        "echo ${{ fromJSON(github.event.issue.body).x }}",
        "echo ${{ contains(github.head_ref, 'x') && github.event.comment.body || 'y' }}",
        "echo ${{ github.head_ref }}",
        "echo ${{ env.ANYTHING }}",
        "echo ${{ steps.a.outputs.v }}",
        "echo ${{ format('${{ nested }}') }}",
    ]
    for body in bodies:
        errs = errors(tmp_path, workflow(extra_steps=f"      - run: {json.dumps(body)}\n"))
        assert any("expression inside a run script" in e for e in errs), body
    multi = workflow(extra_steps="      - run: |\n          echo first\n          echo second ${{ github.event.pull_request.head.ref }}\n")
    assert any("expression inside a run script" in e for e in errors(tmp_path, multi))
    # the safe pattern: the value goes through env and is read as a shell variable
    safe = workflow(extra_steps="      - run: echo \"$TITLE\"\n        env:\n          TITLE: ${{ github.event.pull_request.title }}\n")
    assert errors(tmp_path, safe) == []


def test_an_expression_inside_shell_or_uses_is_rejected(tmp_path):
    assert any("shell" in e for e in errors(tmp_path, workflow(extra_steps="      - run: echo ok\n        shell: ${{ github.event.x }}\n")))
    assert any("expression inside `uses:`" in e for e in errors(tmp_path, workflow(extra_steps=f"      - uses: actions/setup-python@${{{{ github.sha }}}}\n")))


def test_every_reference_must_match_its_recorded_repository_and_sha_pair(tmp_path):
    # one approved pin elsewhere in the file must not hide a wrong one
    both_right = workflow(extra_steps=f"      - uses: actions/setup-python@{SHA_A}\n      - uses: actions/upload-artifact@{SHA_B}\n")
    assert errors(tmp_path, both_right) == []
    wrong_sha = workflow(extra_steps=f"      - uses: actions/setup-python@{SHA_A}\n      - uses: actions/upload-artifact@{'c' * 40}\n")
    assert any("upload-artifact" in e and "recorded pair" in e for e in errors(tmp_path, wrong_sha))
    swapped = workflow(extra_steps=f"      - uses: actions/setup-python@{SHA_B}\n")  # a SHA that is right for ANOTHER repository
    assert any("recorded pair" in e for e in errors(tmp_path, swapped))
    unknown = workflow(extra_steps=f"      - uses: someone/else@{SHA_A}\n")
    assert any("not in the recorded pins" in e for e in errors(tmp_path, unknown))
    for ref, why in ((f"actions/setup-python@v5", "not a full 40-hex"), (f"actions/setup-python@{SHA_A[:39]}", "not a full 40-hex"), ("actions/setup-python", "no ref"),
                     ("./local-action", "local path"), ("docker://alpine:3", "container image")):
        assert any(why in e for e in errors(tmp_path, workflow(extra_steps=f"      - uses: {ref}\n"))), ref
    sub = workflow(extra_steps=f"      - uses: actions/setup-python/sub/path@{'d' * 40}\n")  # a path inside a listed repository still has to be the recorded SHA
    assert any("recorded pair" in e for e in errors(tmp_path, sub))


def test_a_reusable_workflow_at_job_level_is_validated_too(tmp_path):
    text = f"name: t\non: [pull_request]\npermissions:\n  contents: read\njobs:\n  a:\n    uses: actions/setup-python/.github/workflows/x.yml@{'e' * 40}\n    timeout-minutes: 5\n"
    assert any("recorded pair" in e for e in errors(tmp_path, text))


def test_actions_checkout_is_never_allowed_and_every_job_must_start_with_the_access_safe_step(tmp_path):
    plain = workflow(checkout=f"      - uses: actions/checkout@{SHA_A}\n        with:\n          persist-credentials: false\n")
    errs = errors(tmp_path, plain)
    assert any("actions/checkout is not allowed" in e for e in errs) and any("first step must be" in e for e in errs), errs
    not_first = workflow(checkout="      - run: echo first\n", extra_steps=checkout_step())
    assert any("first step must be" in e for e in errors(tmp_path, not_first))
    no_steps = "name: t\non: [pull_request]\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps: []\n"
    assert any("first step must be" in e for e in errors(tmp_path, no_steps))
    # a job on the reviewed no-checkout list is exempt
    gate = "name: t\non: [pull_request]\npermissions:\n  contents: read\njobs:\n  gate:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n      - run: echo ok\n"
    assert errors(tmp_path, gate) == []


def test_the_checkout_step_must_be_the_canonical_script_with_only_the_token(tmp_path):
    assert any("byte-for-byte" in e for e in errors(tmp_path, workflow(checkout=checkout_step(SCRIPT + "echo extra\n"))))
    assert any("byte-for-byte" in e for e in errors(tmp_path, workflow(checkout=checkout_step(SCRIPT.replace("pipefail", "")))))
    other_env = workflow().replace("GH_TOKEN: ${{ github.token }}", "GH_TOKEN: ${{ github.token }}\n          EXTRA: 1")
    assert any("may only have env" in e for e in errors(tmp_path, other_env))
    wrong_shell = workflow().replace("        shell: bash\n", "        shell: pwsh\n", 1)
    assert any("shell: bash" in e for e in errors(tmp_path, wrong_shell))


def test_the_shipped_workflows_pass_the_shipped_policy():
    policy = cw.load_policy(ROOT / "ci" / "workflow-policy.json")
    script = (ROOT / "scripts" / "ci" / "safe_checkout.sh").read_text(encoding="utf-8")
    files = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert files, "no workflow files found"
    for f in files:
        assert cw.check_file(f, policy, script) == [], f.name


# ---- the access-safe checkout script itself, against a throwaway origin whose commit holds a protected entry (created in the INDEX only: no such file ever exists on disk) ----

def _bash():
    for c in (os.environ.get("BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if c and Path(c).exists() and "System32" not in c:
            return c
    raise AssertionError("no bash found for the checkout-script test")


def _fixture_env(origin, sha):
    """The explicit tokenless FIXTURE mode: the only mode in which REMOTE_URL / CHECKOUT_SHA are honoured."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "REMOTE_URL", "CHECKOUT_SHA", "ATHENA_CHECKOUT_TOKENLESS") and not k.startswith("GIT_")}
    env.update(ATHENA_CHECKOUT_TOKENLESS="1", REMOTE_URL=str(origin), CHECKOUT_SHA=sha, GITHUB_REPOSITORY="x/y", GITHUB_SHA=sha)
    return env


def _origin(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q")
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "src").mkdir()
    (origin / "src" / "a.py").write_text("x = 1\n")
    git(origin, "add", "--", "src/a.py")
    oid = git(origin, "hash-object", "-w", "--stdin", stdin="synthetic\n").strip()
    git(origin, "update-index", "--add", "--cacheinfo", f"100644,{oid},{PROTECTED}")
    git(origin, "update-index", "--add", "--cacheinfo", f"100644,{oid},sub dir/{PROTECTED}")  # the same rule at depth, with a space in the path
    git(origin, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "c")
    return origin, git(origin, "rev-parse", "HEAD").strip()


def test_the_checkout_script_never_writes_a_protected_name_and_keeps_the_entries_as_metadata(tmp_path):
    origin, sha = _origin(tmp_path)
    work = tmp_path / "ws"
    work.mkdir()
    env = _fixture_env(origin, sha)
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "protected names on disk: 0" in r.stdout and "metadata only: 2" in r.stdout, r.stdout
    on_disk = [p.name for p in work.rglob("*") if p.is_file() and ".git" not in p.parts]
    assert on_disk == ["a.py"], on_disk
    flagged = [x for x in git(work, "ls-files", "-v").splitlines() if x.startswith("S ")]
    assert len(flagged) == 2
    assert git(work, "status", "--porcelain").strip() == ""
    assert not (work / PROTECTED).exists()


def test_the_checkout_script_fails_loudly_when_the_fetch_fails(tmp_path):
    work = tmp_path / "ws"
    work.mkdir()
    env = _fixture_env(tmp_path / "no-such-origin", "0" * 40)
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and not [p for p in work.rglob("*") if p.is_file() and ".git" not in p.parts]


# ---- review 3, R1: no inherited remote or revision override in authenticated hosted operation ----

import pytest  # noqa: E402

CONTROL_NAMES = ["REMOTE_URL", "CHECKOUT_SHA", "ATHENA_CHECKOUT_TOKENLESS", "ATHENA_CHECKOUT_ANYTHING", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND", "GIT_DIR", "GH_TOKEN", "GITHUB_SHA",
                 "GITHUB_REPOSITORY", "HOME", "XDG_CONFIG_HOME", "BASH_ENV", "SHELLOPTS", "PS4", "HTTPS_PROXY", "remote_url", "Checkout_Sha"]


@pytest.mark.parametrize("scope", ["workflow", "job"])
@pytest.mark.parametrize("name", CONTROL_NAMES)
def test_a_workflow_or_job_environment_variable_that_steers_the_checkout_is_rejected(tmp_path, name, scope):
    text = workflow(top_extra=f"env:\n  {name}: x\n") if scope == "workflow" else workflow(job_extra=f"    env:\n      {name}: x\n")
    errs = errors(tmp_path, text)
    assert any("steers the checkout" in e and name.upper() in e.upper() and scope in e for e in errs), errs


@pytest.mark.parametrize("scope", ["workflow", "job"])
def test_an_environment_block_that_is_not_a_literal_mapping_is_rejected_because_it_could_carry_any_name(tmp_path, scope):
    expr = "${{ fromJSON(vars.EXTRA_ENV) }}"
    text = workflow(top_extra=f"env: {expr}\n") if scope == "workflow" else workflow(job_extra=f"    env: {expr}\n")
    assert any("literal mapping" in e for e in errors(tmp_path, text))


@pytest.mark.parametrize("scope", ["workflow", "job"])
def test_ordinary_environment_variables_are_still_allowed_at_both_levels(tmp_path, scope):
    text = workflow(top_extra="env:\n  PYTHON_VERSION: '3.13'\n  NODE_VERSION: '24'\n") if scope == "workflow" else workflow(job_extra="    env:\n      PYTHON_VERSION: '3.13'\n")
    assert errors(tmp_path, text) == []


def test_a_job_container_is_rejected_because_it_brings_its_own_git_and_environment(tmp_path):
    errs = errors(tmp_path, workflow(job_extra="    container: ubuntu:latest\n"))
    assert any("container" in e and "not allowed" in e for e in errs), errs
    with_env = errors(tmp_path, workflow(job_extra="    container:\n      image: ubuntu:latest\n      env:\n        REMOTE_URL: x\n"))
    assert any("container" in e for e in with_env), with_env


def test_the_shipped_policy_records_the_control_names():
    policy = cw.load_policy(ROOT / "ci" / "workflow-policy.json")
    assert {"REMOTE_URL", "CHECKOUT_SHA", "ATHENA_CHECKOUT_*", "GIT_*"} <= set(policy["checkout_control_env"])


SHIM = r"""#!/usr/bin/env bash
# a stand-in for git that records what the checkout script asked of it (a file, never the output) and answers like a successful fetch
{ printf 'ARGS:'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\n'; env | grep '^GIT_' | sed 's/^/ENV:/'; } >> "$SHIM_LOG"
sub=""; skip=0
for a in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  case "$a" in -c) skip=1 ;; -*) ;; *) sub="$a"; break ;; esac
done
case "$sub" in
  fetch) exit "${SHIM_FETCH_RC:-0}" ;;
  rev-parse) echo "${SHIM_REVPARSE:-$GITHUB_SHA}" ;;
esac
exit 0
"""

HEX = "0123456789abcdef0123456789abcdef01234567"


def _posix(p: Path) -> str:
    s = p.resolve().as_posix()
    return "/" + s[0].lower() + s[2:] if len(s) > 2 and s[1] == ":" else s  # C:/x -> /c/x for Git Bash


def _shimmed(tmp_path, **extra):
    """Runs the real checkout script with `git` replaced by a recording stand-in. Git Bash puts its own git first on PATH, so the stand-in is put in front through BASH_ENV, which a
    non-interactive bash reads before the script."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "git").write_bytes(SHIM.encode())
    os.chmod(shim / "git", 0o755)
    (shim / "prepend.sh").write_bytes(f'export PATH="{_posix(shim)}:$PATH"\n'.encode())
    log = tmp_path / "shim.log"
    log.write_text("")
    work = tmp_path / "ws"
    work.mkdir()
    base = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "REMOTE_URL", "CHECKOUT_SHA", "ATHENA_CHECKOUT_TOKENLESS") and not k.startswith("GIT_")}
    env = dict(base, BASH_ENV=_posix(shim / "prepend.sh"), SHIM_LOG=str(log), GITHUB_REPOSITORY="owner/repo", GITHUB_SHA=HEX, GH_TOKEN="dummy-token-value")
    env.update(extra)
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=env, capture_output=True, text=True)
    return r, log.read_text(), work


def test_the_stand_in_git_really_is_the_one_the_script_runs(tmp_path):
    """Guards the harness itself: a test that 'refuses before running git' proves nothing if the stand-in was never the git that ran."""
    r, log, _ = _shimmed(tmp_path)
    assert "[init] [--quiet] [.]" in log, "the stand-in was not used:\n" + log + r.stdout + r.stderr


def test_authenticated_mode_takes_the_remote_and_the_commit_from_the_trusted_job_context_and_forwards_no_git_control_variables(tmp_path):
    r, log, _ = _shimmed(tmp_path, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="url.https://evil.invalid/.insteadOf", GIT_CONFIG_VALUE_0="https://github.com/", GIT_SSH_COMMAND="evil", GIT_DIR=str(tmp_path / "elsewhere"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[remote] [add] [origin] [https://github.com/owner/repo.git]" in log, log
    assert f"[fetch] [--quiet] [--no-tags] [--depth=1] [origin] [{HEX}]" in log, log
    assert "ENV:" not in log, "no inherited GIT_* control variable may reach git:\n" + log
    assert "dummy-token-value" not in r.stdout + r.stderr, "the token is never logged"
    assert "GIT_CONFIG_COUNT" in r.stdout and "GIT_SSH_COMMAND" in r.stdout, "the removed variable NAMES (never values) are reported"
    assert "evil" not in r.stdout


@pytest.mark.parametrize("override", [{"REMOTE_URL": "https://checkout-review.invalid/repo.git"}, {"CHECKOUT_SHA": "0" * 40}, {"ATHENA_CHECKOUT_TOKENLESS": "1"}])
def test_authenticated_mode_refuses_any_override_before_it_runs_git_at_all(tmp_path, override):
    r, log, work = _shimmed(tmp_path, **override)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "override" in (r.stdout + r.stderr).lower() and "dummy-token-value" not in r.stdout + r.stderr
    assert "http.extraheader" not in log and "fetch" not in log, "no authenticated operation may start:\n" + log
    assert "checkout" not in log


@pytest.mark.parametrize("repo", ["owner", "a/b/c", "x@evil.invalid/y", "owner/re po", "../x", "owner/..", "https://evil.invalid/x"])
def test_authenticated_mode_refuses_a_repository_that_is_not_a_plain_owner_and_name(tmp_path, repo):
    r, log, _ = _shimmed(tmp_path, GITHUB_REPOSITORY=repo)
    assert r.returncode != 0 and "fetch" not in log, r.stdout + r.stderr + log


@pytest.mark.parametrize("sha", ["abc123", HEX.upper(), HEX[:39], HEX + "0", "HEAD", "main", ""])
def test_authenticated_mode_refuses_a_commit_that_is_not_a_full_lowercase_sha(tmp_path, sha):
    r, log, _ = _shimmed(tmp_path, GITHUB_SHA=sha)
    assert r.returncode != 0 and "fetch" not in log, r.stdout + r.stderr + log


def test_the_fetched_commit_must_equal_the_requested_one_before_anything_is_checked_out(tmp_path):
    r, log, _ = _shimmed(tmp_path, SHIM_REVPARSE="1" * 40)
    assert r.returncode != 0 and "does not equal" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert "[checkout]" not in log, "the tree of the wrong commit must never be written:\n" + log


def test_overrides_are_honoured_only_in_the_explicit_tokenless_fixture_mode(tmp_path):
    origin, sha = _origin(tmp_path)
    work = tmp_path / "ws"
    work.mkdir()
    env = _fixture_env(origin, sha)
    env.pop("ATHENA_CHECKOUT_TOKENLESS")
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "override" in (r.stdout + r.stderr).lower(), "an override without the explicit fixture flag is refused even with no token"
    assert not [p for p in work.rglob("*") if p.is_file() and ".git" not in p.parts]
    both = dict(_fixture_env(origin, sha), GH_TOKEN="dummy-token-value")
    r2 = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=both, capture_output=True, text=True)
    assert r2.returncode != 0 and "dummy-token-value" not in r2.stdout + r2.stderr, "the fixture flag together with a token is refused"


def test_the_token_is_never_written_to_the_repository_configuration(tmp_path):
    origin, sha = _origin(tmp_path)
    work = tmp_path / "ws"
    work.mkdir()
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=_fixture_env(origin, sha), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "extraheader" not in (work / ".git" / "config").read_text()


# ---- review 3, R2: unsupported path shapes are rejected from metadata BEFORE any checkout ----

def _git_b(repo, *args, stdin=b""):
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.protectNTFS=false", *args], input=stdin, capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    return r.stdout


def _origin_with_paths(tmp_path, paths):
    """A throwaway origin whose commit holds exactly these tree paths (built with mktree, so no file with such a name ever exists on disk)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git_b(origin, "init", "-q")
    _git_b(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    blob = _git_b(origin, "hash-object", "-w", "--stdin", stdin=b"synthetic\n").decode().strip()
    tree: dict = {}
    for p in paths:
        node = tree
        parts = p.split("/")
        for d in parts[:-1]:
            node = node.setdefault(d, {})
        node[parts[-1]] = None

    def build(node):
        out = b""
        for name, child in node.items():
            out += (b"100644 blob " + blob.encode() + b"\t" + name.encode() + b"\0") if child is None else (b"040000 tree " + build(child).encode() + b"\t" + name.encode() + b"\0")
        return _git_b(origin, "mktree", "-z", stdin=out).decode().strip()

    root = build(tree)
    c = _git_b(origin, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit-tree", root, "-m", "c").decode().strip()
    return origin, c


SHAPES = {
    "trailing space": ["src/a.py", "src/b.txt "],
    "trailing space in a directory": ["src/a.py", "dir /x.txt"],
    "line feed": ["src/a.py", "src/b\nc.txt"],
    "carriage return": ["src/a.py", "src/b\rc.txt"],
    "tab": ["src/a.py", "src/b\tc.txt"],
    "other control byte": ["src/a.py", "src/b\x1bc.txt"],
    "trailing dot": ["src/a.py", "src/b."],
    "backslash": ["src/a.py", "src/b\\c.txt"],
    "leading space": ["src/a.py", "src/ b.txt"],
    "protected name with a trailing space": ["src/a.py", PROTECTED + " "],
    "protected name with a line feed": ["src/a.py", "ok\n" + PROTECTED],
    "protected name as a directory": ["src/a.py", "d." + "e" + "nv" + "/f.txt"],
}


def test_protected_names_that_carry_pattern_characters_are_excluded_exactly_by_the_real_script(tmp_path):
    """Real runs of the script on protected names that carry pattern characters: each is excluded exactly, written nowhere, and kept as skip-worktree metadata."""
    # `*` and `?` are covered by the escape probe below only: Git for Windows refuses them in any index path, so a real checkout of such a tree cannot be run on this machine
    names = ["cfg[1]." + "e" + "nv", "sub dir/[x]." + "e" + "nv", "." + "e" + "nv" + "#1", "." + "e" + "nv" + "!2"]
    origin, sha = _origin_with_paths(tmp_path, ["src/a.py", *names])
    work = tmp_path / "ws"
    work.mkdir()
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=_fixture_env(origin, sha), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "metadata only: 4" in r.stdout and "protected names on disk: 0" in r.stdout, r.stdout
    assert sorted(p.name for p in work.rglob("*") if p.is_file() and ".git" not in p.parts) == ["a.py"]
    flagged = [x for x in git(work, "ls-files", "-v").splitlines() if x.startswith("S ")]
    assert len(flagged) == 4, flagged


def test_a_protected_name_in_another_case_is_excluded_like_the_exact_case_and_never_written(tmp_path):
    """On a case-insensitive file system `.ENV...` is the same file as `.env...`, so the predicate is case-insensitive (as the local builder's is)."""
    origin, sha = _origin_with_paths(tmp_path, ["src/a.py", PROTECTED, PROTECTED.upper(), "sub/" + PROTECTED.title(), "Prod." + "E" + "NV"])
    work = tmp_path / "ws"
    work.mkdir()
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=_fixture_env(origin, sha), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "metadata only: 4" in r.stdout and "protected names on disk: 0" in r.stdout, r.stdout
    assert sorted(p.name for p in work.rglob("*") if p.is_file() and ".git" not in p.parts) == ["a.py"]


@pytest.mark.parametrize("shape", list(SHAPES))
def test_an_unsupported_path_shape_is_rejected_before_any_checkout(tmp_path, shape):
    origin, sha = _origin_with_paths(tmp_path, SHAPES[shape])
    work = tmp_path / "ws"
    work.mkdir()
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=_fixture_env(origin, sha), capture_output=True, text=True)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "unsupported path" in out, out
    assert not [p for p in work.rglob("*") if p.is_file() and ".git" not in p.parts], "nothing may be materialised"
    unborn = subprocess.run(["git", "-C", str(work), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True)
    assert unborn.returncode != 0, "no checkout may have happened: HEAD must still be unborn"
    assert PROTECTED not in out and PROTECTED.upper() not in out and "e" + "nv" + "/" not in out.lower(), "a protected name is never printed"


def test_ordinary_names_with_spaces_inside_and_brackets_and_stars_are_still_checked_out(tmp_path):
    origin, sha = _origin_with_paths(tmp_path, ["src/a b.py", "src/c[1].py", "src/d#e.py", "src/!f.py", "src/g;h.py", "docs/inner space/i.md"])
    work = tmp_path / "ws"
    work.mkdir()
    r = subprocess.run([_bash(), str(ROOT / "scripts" / "ci" / "safe_checkout.sh")], cwd=work, env=_fixture_env(origin, sha), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file() and ".git" not in p.parts) == sorted(["src/a b.py", "src/c[1].py", "src/d#e.py", "src/!f.py", "src/g;h.py", "docs/inner space/i.md"])


@pytest.mark.parametrize("name", ["blocked", "blocked ", "blocked  ", " lead", "a[1]*?", "[x]", "#hash", "!bang", "a b "])
def test_the_exclusion_line_encodes_a_name_so_git_selects_the_exclusion_not_the_include_all_pattern(tmp_path, name):
    """The reviewer's probe, kept: run the script's own escape line on a name and ask git which pattern then matches it. Nothing here exists on disk.
    (Names containing a backslash are not probed: Windows git rewrites `\\` to `/` in a path argument, and the script rejects them before escaping anyway; see the shape tests.)
    The line contains no doubled backslash (it uses \\x5c) so that it means the same whether bash reads it from a file or from an argument that Windows has already unescaped."""
    script = (ROOT / "scripts" / "ci" / "safe_checkout.sh").read_text(encoding="utf-8")
    esc_line = next(line.strip() for line in script.splitlines() if line.strip().startswith("esc="))
    out = subprocess.run([_bash(), "-c", 'p="$1"\n' + esc_line + '\nprintf "%s" "$esc"', "probe", name], capture_output=True, check=True).stdout.decode("utf-8")
    (tmp_path / "repo").mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    (tmp_path / "repo" / ".gitignore").write_text("/*\n!/" + out + "\n", encoding="utf-8")
    r = subprocess.run(["git", "-C", str(tmp_path / "repo"), "check-ignore", "--no-index", "-z", "-v", "--stdin"], input=name.encode() + b"\0", capture_output=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split(b"\0")[2].startswith(b"!"), repr(r.stdout)
