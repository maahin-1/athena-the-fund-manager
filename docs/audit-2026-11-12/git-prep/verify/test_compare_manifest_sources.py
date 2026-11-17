"""Source-safety tests of compare_manifest.py (review 3, R3): every declared and resolved source or candidate path is validated BEFORE any filesystem access to it.
Run with: python -m pytest docs/audit-2026-11-12/git-prep/verify/test_compare_manifest_sources.py -p no:cacheprovider

No protected file is ever created. A protected path is only DECLARED (as a name in a manifest) and a spy on every filesystem entry point proves the comparator never touched it. Synthetic ordinary
files in a temporary directory otherwise.
"""
import builtins
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import compare_manifest as cm  # noqa: E402

W = "e" + "nv"  # the policy word, assembled so this file names no protected path literally
PROT = "." + W
ATTRS = "*.py text eol=lf\n"


def _is_protected(text: str) -> bool:
    parts = [p for p in str(text).replace("\\", "/").split("/") if p]
    return any(p.lower().startswith("." + W) or p.lower().endswith("." + W) for p in parts)


class Spy:
    """Records every path handed to the filesystem entry points the comparator could use."""

    NAMES = ("os.stat", "os.lstat", "os.readlink", "os.scandir", "os.listdir", "os.path.realpath", "os.path.exists", "os.path.isfile", "builtins.open", "io.open")
    PATH_METHODS = ("is_file", "exists", "read_bytes", "resolve", "stat", "lstat", "is_symlink", "open", "read_text")

    def __init__(self):
        self.seen: list[str] = []
        self.patches = []

    def _wrap(self, real):
        def inner(target, *a, **k):
            try:
                self.seen.append(os.fspath(target))
            except TypeError:
                pass
            return real(target, *a, **k)
        return inner

    def _wrap_method(self, real):
        def inner(path, *a, **k):
            self.seen.append(str(path))
            return real(path, *a, **k)
        return inner

    def __enter__(self):
        for dotted in self.NAMES:
            mod_name, attr = dotted.rsplit(".", 1)
            mod = {"os": os, "os.path": os.path, "builtins": builtins, "io": io}[mod_name]
            p = patch.object(mod, attr, self._wrap(getattr(mod, attr)))
            p.start()
            self.patches.append(p)
        for m in self.PATH_METHODS:
            p = patch.object(Path, m, self._wrap_method(getattr(Path, m)))
            p.start()
            self.patches.append(p)
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()

    def protected_accesses(self) -> list[str]:
        return [s for s in self.seen if _is_protected(s)]


def _expected(tmp_path, entries):
    (tmp_path / "attrs").write_text(ATTRS)
    (tmp_path / "exp.json").write_text(json.dumps({"files": entries}))


def _run(tmp_path, entries, roots=(), cand_files=None):
    cand = tmp_path / "cand"
    cand.mkdir(exist_ok=True)
    for rel, data in (cand_files or {}).items():
        (cand / rel).parent.mkdir(parents=True, exist_ok=True)
        (cand / rel).write_bytes(data)
    _expected(tmp_path, entries)
    argv = ["--expected", str(tmp_path / "exp.json"), "--candidate", str(cand), "--attrs-from", str(tmp_path / "attrs"), "--no-removal-check", "--out", str(tmp_path / "rep.json")]
    for r in roots:
        argv += ["--source-root", str(r)]
    with Spy() as spy:
        rc = cm.main(argv)
    return rc, json.loads((tmp_path / "rep.json").read_text()), spy


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _src_root(tmp_path, files):
    root = tmp_path / "srcroot"
    root.mkdir(exist_ok=True)
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


# ---- controls: ordinary declarations still work ----

def test_control_an_ordinary_source_path_and_an_ordinary_absolute_source_under_a_root_are_accepted(tmp_path):
    root = _src_root(tmp_path, {"a.py": b"x = 1\n", "d/b.py": b"y = 2\n"})
    entries = [{"path": "a.py", "source_path": "a.py", "sha256": _sha(b"x = 1\n")},
               {"path": "b.py", "source": str(root / "d" / "b.py"), "sha256": _sha(b"y = 2\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root], cand_files={"a.py": b"x = 1\n", "b.py": b"y = 2\n"})
    assert rc == 0 and rep["accepted"] == 2 and rep.get("unsafe_declarations", []) == [], rep
    assert spy.protected_accesses() == []


# ---- a protected name as a declared source is refused without being opened ----

@pytest.mark.parametrize("declared", [
    {"source": "{tmp}/" + PROT},                       # the reviewer's case: a safe candidate path with a protected absolute source
    {"source": "{root}/" + PROT},                      # absolute, inside the root
    {"source_path": PROT},
    {"source_path": "notes/" + PROT},
    {"source_path": "notes/prod." + W},
    {"source_path": PROT + ".sample"},
    {"source_path": "x." + W + "/data.txt"},           # a protected name as a parent directory
    {"source_path": "notes/" + PROT.upper()},          # case variants alias on a case-insensitive file system
])
def test_a_protected_source_declaration_is_refused_and_never_touched(tmp_path, declared):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    fixed = {k: v.format(tmp=str(tmp_path).replace("\\", "/"), root=str(root).replace("\\", "/")) for k, v in declared.items()}
    entries = [{"path": "safe.py", "sha256": _sha(b"x = 1\n"), **fixed}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root], cand_files={"safe.py": b"x = 1\n"})
    assert spy.protected_accesses() == [], f"the comparator touched a protected path: {spy.protected_accesses()}"  # asserted first: this is the access-safety property itself
    assert rc == 1 and rep["ok"] is False, rep
    assert len(rep["unsafe_declarations"]) == 1 and rep["unsafe_declarations"][0]["path"] == "safe.py", rep
    assert PROT not in json.dumps(rep) and "prod." + W not in json.dumps(rep), "the report must not print a protected name"


# ---- root containment ----

def test_a_source_path_that_climbs_out_of_the_root_is_refused_and_the_outside_file_is_not_read(tmp_path):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    (tmp_path / "outside.py").write_bytes(b"x = 1\n")
    entries = [{"path": "safe.py", "source_path": "../outside.py", "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root], cand_files={"safe.py": b"x = 1\n"})
    assert rc == 1 and len(rep["unsafe_declarations"]) == 1, rep
    assert not [s for s in spy.seen if s.replace("\\", "/").endswith("/outside.py")], "the file outside the root was touched"


def test_an_absolute_source_outside_every_declared_root_is_refused(tmp_path):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    (tmp_path / "elsewhere.py").write_bytes(b"x = 1\n")
    entries = [{"path": "safe.py", "source": str(tmp_path / "elsewhere.py"), "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root], cand_files={"safe.py": b"x = 1\n"})
    assert rc == 1 and len(rep["unsafe_declarations"]) == 1, rep
    assert not [s for s in spy.seen if s.replace("\\", "/").endswith("/elsewhere.py")]


def test_an_absolute_source_with_no_declared_root_is_refused(tmp_path):
    (tmp_path / "elsewhere.py").write_bytes(b"x = 1\n")
    entries = [{"path": "safe.py", "source": str(tmp_path / "elsewhere.py"), "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[], cand_files={"safe.py": b"x = 1\n"})
    assert rc == 1 and len(rep["unsafe_declarations"]) == 1, rep


@pytest.mark.parametrize("bad", ["/abs/file.py", "C:/abs/file.py", "a//b.py", "a/./b.py", "a/../b.py", "a\\b.py", "a/b.py ", "a/b.py.", " a/b.py", "a/b:stream", "a/\x01b.py", "a/b\nc.py", "", "NUL", "dir/CON.txt"])
def test_unsupported_declared_shapes_are_refused_for_sources_and_for_candidate_paths(tmp_path, bad):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    entries = [{"path": "safe.py", "source_path": bad, "sha256": _sha(b"x = 1\n")},
               {"path": bad, "source_path": "ok.py", "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root], cand_files={"safe.py": b"x = 1\n"})
    assert rc == 1 and len(rep["unsafe_declarations"]) == 2, rep
    assert {u["field"] for u in rep["unsafe_declarations"]} == {"source_path", "path"}, rep


def test_a_candidate_path_that_climbs_out_of_the_candidate_is_refused_and_the_outside_file_is_not_read(tmp_path):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    (tmp_path / "outside.py").write_bytes(b"x = 1\n")
    entries = [{"path": "../outside.py", "source_path": "ok.py", "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root])
    assert rc == 1 and len(rep["unsafe_declarations"]) == 1 and rep["unsafe_declarations"][0]["field"] == "path", rep
    assert not [s for s in spy.seen if s.replace("\\", "/").endswith("/outside.py")]


# ---- resolved targets: links and junctions ----

def _link_dir(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        return r.returncode == 0 and link.exists()
    return False


def test_a_directory_link_inside_the_root_that_leads_outside_it_is_refused(tmp_path):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "f.py").write_bytes(b"x = 1\n")
    if not _link_dir(root / "alias", outside):
        pytest.fail("this machine can create neither a symlink nor a junction, so the link case cannot be exercised here")
    entries = [{"path": "safe.py", "source_path": "alias/f.py", "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root], cand_files={"safe.py": b"x = 1\n"})
    assert rc == 1 and len(rep["unsafe_declarations"]) == 1, rep
    assert rep["accepted"] == 0


def test_a_directory_link_inside_the_candidate_that_leads_outside_it_is_refused(tmp_path):
    root = _src_root(tmp_path, {"ok.py": b"x = 1\n"})
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "f.py").write_bytes(b"x = 1\n")
    cand = tmp_path / "cand"
    cand.mkdir()
    if not _link_dir(cand / "alias", outside):
        pytest.fail("this machine can create neither a symlink nor a junction, so the link case cannot be exercised here")
    entries = [{"path": "alias/f.py", "source_path": "ok.py", "sha256": _sha(b"x = 1\n")}]
    rc, rep, spy = _run(tmp_path, entries, roots=[root])
    assert rc == 1 and len(rep["unsafe_declarations"]) == 1, rep


def test_the_resolved_target_check_names_a_protected_alias_and_a_root_escape_without_any_file(tmp_path):
    root = tmp_path / "root"
    assert cm.unsafe_resolved(root, root / "a" / "b.py", ["a", "b.py"]) is None
    assert "protected" in cm.unsafe_resolved(root, root / "a" / PROT, ["a", "safe.py"])      # a safe declared name that RESOLVES to a protected one
    assert "protected" in cm.unsafe_resolved(root, root / ("x." + W) / "f.py", ["x", "f.py"])
    assert "outside" in cm.unsafe_resolved(root, tmp_path / "elsewhere" / "b.py", ["a", "b.py"])
    assert "different" in cm.unsafe_resolved(root, root / "other" / "b.py", ["a", "b.py"])  # reached through a link or another alias
