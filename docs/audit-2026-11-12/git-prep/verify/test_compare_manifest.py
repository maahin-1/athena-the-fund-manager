"""Tests of compare_manifest.py: it accepts exactly the declared line-ending differences and rejects every other difference. Run with:
python -m pytest docs/audit-2026-11-12/git-prep/verify/test_compare_manifest.py -p no:cacheprovider
Synthetic files in a temporary directory only."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import compare_manifest as cm  # noqa: E402

ATTRS = "*.py text eol=lf\n*.ps1 text eol=crlf\n/ev/** -text\n"


def run(tmp_path, files, make_candidate, extra_candidate=(), baseline=None):
    """files: {path: source bytes}; make_candidate(path, bytes) -> candidate bytes or None (absent)."""
    src, cand = tmp_path / "src", tmp_path / "cand"
    exp = []
    for p, b in files.items():
        (src / p).parent.mkdir(parents=True, exist_ok=True)
        (src / p).write_bytes(b)
        exp.append({"path": p, "source": str(src / p), "sha256": hashlib.sha256(b).hexdigest()})
        c = make_candidate(p, b)
        if c is not None:
            (cand / p).parent.mkdir(parents=True, exist_ok=True)
            (cand / p).write_bytes(c)
    (tmp_path / "attrs").write_text(ATTRS)
    (tmp_path / "exp.json").write_text(json.dumps({"files": exp}))
    listing = list(files) + list(extra_candidate)
    (tmp_path / "list.txt").write_text("\n".join(listing))
    out = tmp_path / "report.json"
    rc = cm.main(["--expected", str(tmp_path / "exp.json"), "--candidate", str(cand), "--attrs-from", str(tmp_path / "attrs"), "--candidate-list", str(tmp_path / "list.txt"), "--source-root", str(src), "--no-removal-check", "--out", str(out)])
    return rc, json.loads(out.read_text())


FILES = {"a.py": b"x = 1\r\ny = 2\r\n", "s.ps1": b"Write-Host 1\n", "ev/log.txt": b"line\r\nline\r\n", "pic.bin": b"\x00\x01\r\n\x02"}


def test_declared_line_ending_differences_are_accepted(tmp_path):
    def cand(p, b):
        return {"a.py": cm.lf(b), "s.ps1": cm.crlf(b), "ev/log.txt": b, "pic.bin": b}[p]
    rc, rep = run(tmp_path, FILES, cand)
    assert rc == 0 and rep["accepted_by_class"] == {"eol_lf": 1, "eol_crlf": 1, "exact": 2}, rep


def test_evidence_with_changed_line_endings_is_a_mismatch(tmp_path):
    rc, rep = run(tmp_path, FILES, lambda p, b: cm.lf(b) if p == "ev/log.txt" else (cm.lf(b) if p == "a.py" else (cm.crlf(b) if p == "s.ps1" else b)))
    assert rc == 1 and rep["mismatched"] == ["ev/log.txt"], rep


def test_a_content_change_is_a_mismatch_even_when_endings_are_right(tmp_path):
    rc, rep = run(tmp_path, FILES, lambda p, b: cm.lf(b).replace(b"2", b"3") if p == "a.py" else (cm.crlf(b) if p == "s.ps1" else b))
    assert rc == 1 and rep["mismatched"] == ["a.py"], rep


def test_wrong_style_for_a_named_path_is_a_mismatch(tmp_path):
    rc, rep = run(tmp_path, FILES, lambda p, b: b if p != "a.py" else b)  # a.py must be LF under the policy, but it kept CRLF
    assert rc == 1 and "a.py" in rep["mismatched"], rep


def test_binary_bytes_must_be_exact(tmp_path):
    rc, rep = run(tmp_path, FILES, lambda p, b: cm.lf(b) if p == "pic.bin" else (cm.lf(b) if p == "a.py" else (cm.crlf(b) if p == "s.ps1" else b)))
    assert rc == 1 and rep["mismatched"] == ["pic.bin"], rep


def test_missing_and_unexpected_files_are_reported(tmp_path):
    rc, rep = run(tmp_path, FILES, lambda p, b: None if p == "s.ps1" else (cm.lf(b) if p == "a.py" else b), extra_candidate=["stray.txt"])
    assert rc == 1 and rep["missing"] == ["s.ps1"] and rep["unexpected_tracked"] == ["stray.txt"], rep


def test_a_source_that_no_longer_matches_its_manifest_hash_is_reported(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_bytes(b"changed\n")
    (tmp_path / "attrs").write_text(ATTRS)
    (tmp_path / "exp.json").write_text(json.dumps({"files": [{"path": "a.py", "source": str(src / "a.py"), "sha256": "0" * 64}]}))
    (tmp_path / "cand").mkdir()
    rc = cm.main(["--expected", str(tmp_path / "exp.json"), "--candidate", str(tmp_path / "cand"), "--attrs-from", str(tmp_path / "attrs"), "--source-root", str(src), "--no-removal-check", "--out", str(tmp_path / "r.json")])
    assert rc == 1 and json.loads((tmp_path / "r.json").read_text())["source_no_longer_matches_manifest"] == ["a.py"]


def test_sources_are_found_under_source_roots_in_order_and_self_exempt_files_need_no_hash(tmp_path):
    older, newer, cand = tmp_path / "older", tmp_path / "newer", tmp_path / "cand"
    for d in (older, newer, cand):
        d.mkdir()
    (older / "a.py").write_bytes(b"old\n")
    (newer / "a.py").write_bytes(b"new\n")  # the later backup wins
    (older / "b.md").write_bytes(b"b\n")
    (older / "plan.json").write_bytes(b"{}\n")
    for n, b in (("a.py", b"new\n"), ("b.md", b"b\n"), ("plan.json", b"{}\n")):
        (cand / n).write_bytes(b)
    (tmp_path / "attrs").write_text(ATTRS)
    exp = {"files": [{"path": "a.py", "sha256": hashlib.sha256(b"new\n").hexdigest()}, {"path": "b.md", "sha256": hashlib.sha256(b"b\n").hexdigest()}, {"path": "plan.json", "sha256": "0" * 64}], "self_exempt": ["plan.json"]}
    (tmp_path / "exp.json").write_text(json.dumps(exp))
    argv = ["--expected", str(tmp_path / "exp.json"), "--candidate", str(cand), "--attrs-from", str(tmp_path / "attrs"), "--source-root", str(newer), "--source-root", str(older), "--no-removal-check", "--out", str(tmp_path / "r.json")]
    assert cm.main(argv) == 0
    exp["files"].append({"path": "gone.py", "sha256": "1" * 64})
    (tmp_path / "exp.json").write_text(json.dumps(exp))
    assert cm.main(argv) == 1  # a file in no source root is reported, not skipped


def test_protected_names_are_counted_and_never_opened(tmp_path):
    protected = "." + ("e" + "nv") + ".example"  # a name only; the file is never created
    files = dict(FILES)
    rc, rep = run(tmp_path, files, lambda p, b: (cm.lf(b) if p == "a.py" else (cm.crlf(b) if p == "s.ps1" else b)), extra_candidate=[protected])
    assert rc == 0 and rep["protected_names_skipped_not_opened"] == 1, rep
    assert not (tmp_path / "cand" / protected).exists()


# ---- removals, and the maintenance branch compared with the integration tip (real throwaway git repositories) ----

def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _commit(repo, files, message, remove=()):
    for name, data in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(data)
    for name in remove:
        _git(repo, "rm", "-q", "--", name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    base = _commit(repo, {"keep.txt": b"keep\n", "other.txt": b"other\n", ".gitattributes": b"* -text\n"}, "baseline")
    return repo, base


def _manifest(tmp_path, files):
    src = tmp_path / "src"
    entries = []
    for name, data in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
        entries.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    return {"files": entries}


def _compare(tmp_path, repo, expected, baseline_ref):
    (tmp_path / "exp.json").write_text(json.dumps(expected))
    out = tmp_path / "rep.json"
    argv = ["--expected", str(tmp_path / "exp.json"), "--candidate", str(repo), "--baseline-repo", str(repo), "--baseline-ref", baseline_ref, "--source-root", str(tmp_path / "src"), "--out", str(out)]
    return cm.main(argv), json.loads(out.read_text())


def test_an_unplanned_deletion_of_an_unchanged_baseline_file_fails(tmp_path):
    repo, base = _repo(tmp_path)
    _commit(repo, {"new.txt": b"new\n"}, "candidate", remove=["keep.txt"])
    rc, rep = _compare(tmp_path, repo, _manifest(tmp_path, {"new.txt": b"new\n"}), base)
    assert rc == 1 and rep["baseline_files_removed_without_being_planned"] == ["keep.txt"] and not rep["unexpected_tracked"], rep


def test_a_planned_deletion_passes_and_a_planned_deletion_that_is_still_present_fails(tmp_path):
    repo, base = _repo(tmp_path)
    exp = _manifest(tmp_path, {"new.txt": b"new\n"})
    exp["deleted"] = ["keep.txt"]
    _commit(repo, {"new.txt": b"new\n"}, "candidate", remove=["keep.txt"])
    rc, rep = _compare(tmp_path, repo, exp, base)
    assert rc == 0 and rep["baseline_files_removed_without_being_planned"] == [], rep
    exp2 = _manifest(tmp_path, {"new.txt": b"new\n"})
    exp2["deleted"] = ["other.txt"]  # planned for deletion but still tracked
    rc, rep = _compare(tmp_path, repo, exp2, base)
    assert rc == 1 and rep["planned_deletions_still_present"] == ["other.txt"], rep


def test_a_missing_baseline_is_an_error_not_a_pass(tmp_path):
    repo, base = _repo(tmp_path)
    _commit(repo, {"new.txt": b"new\n"}, "candidate", remove=["keep.txt"])
    (tmp_path / "exp.json").write_text(json.dumps(_manifest(tmp_path, {"new.txt": b"new\n"})))
    out = tmp_path / "rep.json"
    rc = cm.main(["--expected", str(tmp_path / "exp.json"), "--candidate", str(repo), "--source-root", str(tmp_path / "src"), "--out", str(out)])
    rep = json.loads(out.read_text())
    assert rc == 1 and rep["removal_check"].startswith("NOT RUN"), rep


def test_the_maintenance_branch_is_compared_with_the_integration_tip_not_the_original_baseline(tmp_path):
    repo, base = _repo(tmp_path)
    tip = _commit(repo, {"app/a.py": b"print(1)\n", "docs/audit.md": b"audit\n"}, "integration tip")
    _commit(repo, {".github/workflows/ci.yml": b"name: ci\n"}, "maintenance")
    maint = _manifest(tmp_path, {".github/workflows/ci.yml": b"name: ci\n"})
    rc, rep = _compare(tmp_path, repo, maint, tip)
    assert rc == 0 and rep["unexpected_tracked"] == [], rep
    # against the ORIGINAL baseline the same candidate shows the whole integration branch as unexpected: the wrong reference is visible, not silently accepted
    rc, rep = _compare(tmp_path, repo, maint, base)
    assert rc == 1 and sorted(rep["unexpected_tracked"]) == ["app/a.py", "docs/audit.md"], rep


def test_a_maintenance_commit_that_alters_or_removes_an_integration_file_fails(tmp_path):
    repo, base = _repo(tmp_path)
    tip = _commit(repo, {"app/a.py": b"print(1)\n", "app/b.py": b"print(2)\n"}, "integration tip")
    _commit(repo, {"app/a.py": b"print(99)\n", ".github/x.yml": b"a: 1\n"}, "maintenance", remove=["app/b.py"])
    rc, rep = _compare(tmp_path, repo, _manifest(tmp_path, {".github/x.yml": b"a: 1\n"}), tip)
    assert rc == 1 and rep["baseline_files_changed_without_being_expected"] == ["app/a.py"] and rep["baseline_files_removed_without_being_planned"] == ["app/b.py"], rep


# ---- the second reviewer's supplied regression, repeated against the revision-3 comparator (their baseline is a tree object made with `git mktree`, their candidate a repository with an index and no commit) ----

def _reviewer_case(tmp_path, delete_baseline):
    base, cand, source = (tmp_path / x for x in ("base", "candidate", "source"))
    for d in (base, cand, source):
        d.mkdir()
    _git(base, "init", "-q")
    _git(cand, "init", "-q")
    payload = b"value = 1\n"
    (base / "a.py").write_bytes(payload)
    (base / "baseline_keep.py").write_bytes(b"must stay\n")
    a_id = _git(base, "hash-object", "-w", "a.py")
    keep_id = _git(base, "hash-object", "-w", "baseline_keep.py")
    r = subprocess.run(["git", "-C", str(base), "mktree"], input=f"100644 blob {a_id}\ta.py\n100644 blob {keep_id}\tbaseline_keep.py\n".encode(), capture_output=True, check=True)
    tree = r.stdout.decode().strip()
    (source / "a.py").write_bytes(payload)
    (cand / "a.py").write_bytes(payload)
    _git(cand, "add", "--", "a.py")
    if not delete_baseline:
        (cand / "baseline_keep.py").write_bytes(b"must stay\n")
        _git(cand, "add", "--", "baseline_keep.py")
    (tmp_path / "expected.json").write_text(json.dumps({"files": [{"path": "a.py", "source": str(source / "a.py"), "sha256": hashlib.sha256(payload).hexdigest()}]}))
    report = tmp_path / "report.json"
    rc = cm.main(["--expected", str(tmp_path / "expected.json"), "--candidate", str(cand), "--baseline-repo", str(base), "--baseline-ref", tree, "--source-root", str(source), "--out", str(report)])
    return rc, json.loads(report.read_text())


def test_reviewer_control_an_unchanged_baseline_file_passes(tmp_path):
    rc, rep = _reviewer_case(tmp_path, delete_baseline=False)
    assert rc == 0, rep


def test_reviewer_regression_an_unplanned_baseline_deletion_is_rejected(tmp_path):
    rc, rep = _reviewer_case(tmp_path, delete_baseline=True)
    assert rc == 1 and rep["baseline_files_removed_without_being_planned"] == ["baseline_keep.py"], rep
