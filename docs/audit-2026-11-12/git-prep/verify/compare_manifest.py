"""Compares a candidate checkout with the verified source files by an EXPLICIT allowlist manifest, never by recursively diffing directories.

Why not `git diff --no-index a b`: that walks everything under both roots (repository metadata, linked environments, build trees, protected inherited files). Here only the paths named in the
expected manifest are opened, and the candidate's own file list comes from `git ls-files` (or a given list), so nothing else is traversed. Protected (environment-file) names are never opened,
hashed or printed: they are counted and skipped.

expected.json: {"files": [{"path": "<repo-relative, forward slashes>", "sha256": "<hash of the verified source bytes>", "source_path": "<optional: where the source sits when it differs from the path it takes in the candidate>", "source": "<optional absolute path; otherwise the file is looked up by source_path or path under each --source-root in order>"}], "self_exempt": ["<paths of files that cannot carry their own hash>"]}

A file is accepted when its candidate bytes equal what the DECLARED line-ending policy produces from the verified source bytes, and nothing else may differ (no encoding or content change):
  attribute text unset (-text)          candidate bytes == source bytes                         (class "exact")
  text + eol=lf                         candidate bytes == source with CRLF -> LF               (class "eol_lf")
  text + eol=crlf                       candidate bytes == source normalised to CRLF            (class "eol_crlf")
  no rule (follows core.autocrlf)       candidate == source, or source with CRLF -> LF          (class "exact" / "eol_auto"); a file with a NUL byte must be exact
Anything else is a mismatch. Missing files, files tracked in the candidate that are neither expected nor identical to the baseline, baseline files that changed, AND baseline files that are
absent from the candidate without being listed under "deleted" in the expected manifest (an unplanned removal) are reported.

The "baseline" is whatever commit the candidate is meant to differ from: the original baseline commit for the integration branch, the integration TIP for the maintenance branch (so the maintenance
manifest lists only maintenance changes and everything else must be identical to the tip). Giving no baseline is an error unless --no-removal-check is passed (a partial staging rehearsal); the
report then says the removal check was skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

_W = "e" + "nv"  # the policy word, assembled so this file names no protected path
_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def is_policy_component(name: str) -> bool:
    n = name.lower()  # a case-insensitive file system treats `.ENV` and `.env` as one file
    return n.startswith("." + _W) or n.endswith("." + _W)


def is_policy_name(path: str) -> bool:
    return is_policy_component(path.replace("\\", "/").rsplit("/", 1)[-1])


def has_policy_component(path: str) -> bool:
    return any(is_policy_component(c) for c in path.replace("\\", "/").split("/"))


def withheld(path: str) -> str:
    """What a report may show for a path: never a protected name, not even as a fragment of another name."""
    return "<protected name withheld>" if ("." + _W) in path.lower() else path


def unsafe_component(c: str) -> str | None:
    if c == "":
        return "empty component"
    if c in (".", ".."):
        return "a dot component"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in c):
        return "a control character"
    if "\\" in c or ":" in c:
        return "a backslash or colon (separator, drive or stream)"
    if c.startswith(" ") or c.endswith(" ") or c.endswith("."):
        return "a space at the start or end of a component, or a trailing dot (Windows aliases it to another name)"
    if c.split(".")[0].upper() in _DEVICES:
        return "a Windows device name"
    return None


def unsafe_relpath(rel) -> str | None:
    """Why a DECLARED repository-relative path is not acceptable, judged from the text alone (no file system access), or None. A protected name as a directory component is refused too."""
    if not isinstance(rel, str) or rel == "":
        return "empty or not a string"
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return "absolute"
    for c in rel.split("/"):
        why = unsafe_component(c)
        if why:
            return why
    if any(is_policy_component(c) for c in rel.split("/")[:-1]):
        return "a protected name used as a directory"
    return None


def unsafe_resolved(root: Path, resolved: Path, declared_parts: list[str]) -> str | None:
    """Why a RESOLVED target (the path after links, junctions and short names are followed) is not acceptable, or None. `root` is already resolved."""
    try:
        parts = list(resolved.relative_to(root).parts)
    except ValueError:
        return "resolves outside its root"
    if any(is_policy_component(p) for p in parts):
        return "resolves to a protected name"
    if [p.lower() for p in parts] != [p.lower() for p in declared_parts]:
        return "resolves to a different name than declared (a link, junction or other alias)"
    return None


def unsafe_source(s) -> str | None:
    """Why an explicit absolute `source` is not acceptable, from its text alone (no file system access), or None."""
    if not isinstance(s, str) or s == "":
        return "empty or not a string"
    if not os.path.isabs(s):
        return "an explicit source must be an absolute path"
    pure = PureWindowsPath(s) if os.name == "nt" else PurePosixPath(s)
    for c in pure.parts[1:]:  # parts[0] is the anchor (drive or root)
        why = unsafe_component(c)
        if why:
            return why
        if is_policy_component(c):
            return "names a protected path"
    return None


def locate_absolute_source(s: str, roots: list[Path]) -> tuple[Path | None, str | None]:
    """An explicit absolute source must lie inside a declared --source-root (judged from the text first, so a path outside every root is never touched), and then resolves like any other."""
    if not roots:
        return None, "an explicit source needs a declared --source-root to be contained in"
    declared = Path(os.path.abspath(s))
    for r in roots:
        try:
            rel = declared.relative_to(Path(os.path.abspath(r)))
        except ValueError:
            continue
        return resolve_under(r, list(rel.parts))
    return None, "outside every declared source root"


def resolve_under(root: Path, declared_parts: list[str]) -> tuple[Path | None, str | None]:
    """(resolved path, None) when `root/declared_parts` is safe to open, else (None, reason). `root` is resolved here; the declared parts have already passed the text checks."""
    try:
        root_res = root.resolve()
        resolved = (root_res / Path(*declared_parts)).resolve()
    except (OSError, RuntimeError, ValueError):
        return None, "cannot be resolved (loop or invalid name)"
    why = unsafe_resolved(root_res, resolved, declared_parts)
    return (None, why) if why else (resolved, None)


def lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def crlf(b: bytes) -> bytes:
    return lf(b).replace(b"\n", b"\r\n")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(repo: str | Path, *args: str, stdin: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.safecrlf=false", *args], input=stdin, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout


def attributes(repo: Path, paths: list[str]) -> dict[str, tuple[str, str]]:
    """(text, eol) as git resolves them for each path, from the .gitattributes in `repo` (paths need not exist)."""
    out = git(repo, "check-attr", "-z", "--stdin", "text", "eol", stdin=("\0".join(paths) + "\0").encode())
    parts = out.decode("utf-8", errors="replace").split("\0")
    res: dict[str, dict[str, str]] = {}
    for i in range(0, len(parts) - 2, 3):
        res.setdefault(parts[i], {})[parts[i + 1]] = parts[i + 2]
    return {p: (v.get("text", "unspecified"), v.get("eol", "unspecified")) for p, v in res.items()}


def judge(source: bytes, cand: bytes, text: str, eol: str) -> str | None:
    """The class the candidate bytes fall into, or None when they are not what the policy produces."""
    if text == "unset":
        return "exact" if cand == source else None
    if eol == "lf":
        return "eol_lf" if cand == lf(source) else None
    if eol == "crlf":
        return "eol_crlf" if cand == crlf(source) else None
    if cand == source:
        return "exact"
    if b"\0" not in source and cand == lf(source):
        return "eol_auto"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--source-root", action="append", default=[], help="verified backup tree(s) holding the source files, searched in order (a later backup first)")
    ap.add_argument("--attrs-from", help="a .gitattributes file to resolve attributes with when the candidate is not a git checkout")
    ap.add_argument("--candidate-list", help="newline-separated candidate paths, when the candidate is not a git checkout")
    ap.add_argument("--baseline-repo")
    ap.add_argument("--baseline-ref")
    ap.add_argument("--no-removal-check", action="store_true", help="explicit opt-out for a partial candidate (rehearsal); recorded in the report")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    cand = Path(args.candidate)
    doc = json.loads(Path(args.expected).read_text(encoding="utf-8-sig"))
    exp = doc["files"]
    exempt = set(doc.get("self_exempt", []))
    planned_deleted = set(doc.get("deleted", []))
    all_exp = exp
    # a protected name as the candidate path's LAST component is counted and skipped (never opened, hashed or printed); every other declaration is validated below before any file is touched
    skipped_policy = [e for e in all_exp if isinstance(e.get("path"), str) and is_policy_name(e["path"])]
    exp = [e for e in all_exp if not (isinstance(e.get("path"), str) and is_policy_name(e["path"]))]

    unsafe_declarations: list[dict] = []

    def flag(e: dict, field: str, reason: str) -> None:
        p = e.get("path")
        unsafe_declarations.append({"path": withheld(p) if isinstance(p, str) else "<not a string>", "field": field, "reason": reason})

    # text checks of every declared path, BEFORE any file system access to it
    declared_ok = []
    for e in exp:
        why = unsafe_relpath(e.get("path"))
        if why:
            flag(e, "path", why)
            continue
        sp = e.get("source_path")
        if sp is not None:
            why = unsafe_relpath(sp)
            if why:
                flag(e, "source_path", why)
                continue
            if any(is_policy_component(c) for c in sp.split("/")):
                flag(e, "source_path", "names a protected path")
                continue
        s = e.get("source")
        if s is not None:
            why = unsafe_source(s)
            if why:
                flag(e, "source", why)
                continue
        declared_ok.append(e)
    exp = declared_ok

    with tempfile.TemporaryDirectory() as tmp:
        attr_repo = cand
        if args.attrs_from:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            Path(tmp, ".gitattributes").write_bytes(Path(args.attrs_from).read_bytes())
            attr_repo = Path(tmp)
        attrs = attributes(attr_repo, [e["path"] for e in exp])

    classes: dict[str, int] = {}
    mismatched, missing, source_changed = [], [], []
    roots = [Path(r) for r in args.source_root]
    for e in exp:
        # the candidate file: lexically safe already; its resolved target must stay inside the candidate and carry the declared name
        cand_file, why = resolve_under(cand, e["path"].split("/"))
        if why:
            flag(e, "path", why)
            continue
        src_path = None
        if "source" in e:
            src_path, why = locate_absolute_source(e["source"], roots)
            if why:
                flag(e, "source", why)
                continue
        else:
            rel = e.get("source_path", e["path"])
            blocked = False
            for r in roots:
                found, why = resolve_under(r, rel.split("/"))
                if why:
                    flag(e, "source_path", why)  # fail closed: a link out of one root is not a reason to try another
                    blocked = True
                    break
                if found is not None and found.is_file():
                    src_path = found
                    break
            if blocked:
                continue
        if src_path is None or not src_path.is_file():
            source_changed.append(e["path"] + " (source file not found)")
            continue
        src = src_path.read_bytes()
        if e["path"] not in exempt and sha(src) != e["sha256"]:
            source_changed.append(e["path"])  # the verified source itself no longer matches the manifest
            continue
        p = cand_file
        if not p.is_file():
            missing.append(e["path"])
            continue
        c = judge(src, p.read_bytes(), *attrs.get(e["path"], ("unspecified", "unspecified")))
        if c is None:
            mismatched.append(e["path"])
        else:
            classes[c] = classes.get(c, 0) + 1

    unexpected, baseline_changed, tracked = [], [], None
    if args.candidate_list:
        tracked = [x for x in Path(args.candidate_list).read_text(encoding="utf-8").splitlines() if x]
    elif (cand / ".git").exists():
        tracked = [x for x in git(cand, "ls-files", "-z").decode("utf-8", "replace").split("\0") if x]
    protected_in_candidate = 0
    removed_unplanned: list[str] = []
    planned_deletion_still_present: list[str] = []
    protected_baseline_entries_missing = 0
    removal_check = "run"
    if args.no_removal_check:
        removal_check = "skipped (explicit --no-removal-check)"
    elif tracked is None or not (args.baseline_repo and args.baseline_ref):
        removal_check = "NOT RUN: a tracked-file list and a baseline are both required (pass --baseline-repo/--baseline-ref, or --no-removal-check for a partial rehearsal)"
    base_blobs: dict[str, str] = {}
    if tracked is not None:
        expected_paths = {e["path"] for e in exp}
        if args.baseline_repo and args.baseline_ref:
            for line in git(args.baseline_repo, "ls-tree", "-r", "-z", args.baseline_ref).decode("utf-8", "replace").split("\0"):
                if line:
                    meta, path = line.split("\t", 1)
                    base_blobs[path] = meta.split(" ")[2]
        cand_blobs: dict[str, str] = {}
        if (cand / ".git").exists():
            for line in git(cand, "ls-files", "-s", "-z").decode("utf-8", "replace").split("\0"):
                if line:
                    meta, path = line.split("\t", 1)
                    cand_blobs[path] = meta.split(" ")[1]
        for t in tracked:
            if is_policy_name(t):
                protected_in_candidate += 1
                continue
            if t in expected_paths:
                continue
            if t in base_blobs and (t not in cand_blobs or cand_blobs[t] == base_blobs[t]):
                continue
            if t in base_blobs:
                baseline_changed.append(t)
            else:
                unexpected.append(t)

    if removal_check == "run":
        tracked_set = set(tracked)
        for path in base_blobs:
            if path in tracked_set:
                if path in planned_deleted:
                    planned_deletion_still_present.append(path)
                continue
            if is_policy_name(path):
                protected_baseline_entries_missing += 1
            elif path not in planned_deleted:
                removed_unplanned.append(path)

    report = {
        "expected_files": len(all_exp) - len(skipped_policy), "accepted_by_class": classes, "accepted": sum(classes.values()),
        "mismatched": mismatched, "missing": missing, "source_no_longer_matches_manifest": source_changed,
        "candidate_tracked_files": None if tracked is None else len(tracked),
        "unexpected_tracked": unexpected, "baseline_files_changed_without_being_expected": baseline_changed,
        "protected_names_skipped_not_opened": len(skipped_policy) + protected_in_candidate,
        "removal_check": removal_check,
        "baseline_files_removed_without_being_planned": removed_unplanned,
        "planned_deletions_still_present": planned_deletion_still_present,
        "protected_baseline_entries_missing": protected_baseline_entries_missing,
        "unsafe_declarations": unsafe_declarations,
        "ok": not (mismatched or missing or source_changed or unexpected or baseline_changed or removed_unplanned or planned_deletion_still_present or protected_baseline_entries_missing or unsafe_declarations)
        and removal_check in ("run", "skipped (explicit --no-removal-check)"),
    }
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in report.items()}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
