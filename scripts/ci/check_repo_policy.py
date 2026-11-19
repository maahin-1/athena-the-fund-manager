"""Repository content policy, checked from Git metadata only (names, modes, object ids and sizes; no file content is read).

Fails when the tracked tree contains: runtime data or generated stores (`.athena/`, `logs/`, `*.duckdb`, `*.duckdb.wal`), build output or caches (`terminal/target/`, `terminal/web/dist/`,
`node_modules/`, `.venv/`, `__pycache__/`, `.claude/`), a file over the size limit that is not on the reviewed allowlist, or any protected environment-file name other than the inherited baseline entries.

The baseline commit already tracks one protected template. It is tolerated ONLY while it is byte-for-byte what the baseline had: the allowed entries are recorded as SHA-256 of "path NUL object-id"
in ci/inherited-protected.sha256 (no name is stored or printed). A new entry, a changed entry or a second one fails.

Usage: python scripts/ci/check_repo_policy.py [--repo DIR] [--max-bytes N]   Exit 0 = pass, 1 = violations (listed by category and count; protected names are never printed).
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

_W = "e" + "nv"
FORBIDDEN_PREFIXES = (".athena/", "logs/", "terminal/target/", "terminal/web/dist/", "terminal/web/node_modules/", "node_modules/", ".venv/", ".claude/")
FORBIDDEN_PARTS = ("/.athena/", "/__pycache__/", "/node_modules/")
FORBIDDEN_SUFFIXES = (".duckdb", ".duckdb.wal", ".pyc")
DEFAULT_MAX = 5 * 1024 * 1024


def is_protected(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("." + _W) or name.endswith("." + _W)


def git(repo: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"git {args[0]} failed: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout


def entries(repo: Path) -> list[tuple[str, str]]:
    """(path, object id) of every tracked entry."""
    out = git(repo, "ls-files", "-s", "-z").decode("utf-8", "replace")
    res = []
    for rec in out.split("\0"):
        if rec:
            meta, path = rec.split("\t", 1)
            res.append((path, meta.split(" ")[1]))
    return res


def sizes(repo: Path, objects: list[str]) -> dict[str, int]:
    if not objects:
        return {}
    r = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objectsize)"], input="\n".join(objects).encode(), capture_output=True)
    return {a: int(b) for a, b in (line.split() for line in r.stdout.decode().splitlines() if line.strip())}


def check(repo: Path, max_bytes: int, inherited: set[str], allow_large: set[str]) -> dict[str, list]:
    ents = entries(repo)
    sz = sizes(repo, sorted({o for _, o in ents}))
    bad: dict[str, list] = {"runtime_or_build_paths": [], "oversized": [], "protected_not_inherited": []}
    for path, obj in ents:
        if is_protected(path):
            digest = hashlib.sha256(f"{path}\0{obj}".encode()).hexdigest()
            if digest not in inherited:
                bad["protected_not_inherited"].append("<protected name withheld>")
            continue
        if path.startswith(FORBIDDEN_PREFIXES) or any(p in path for p in FORBIDDEN_PARTS) or path.endswith(FORBIDDEN_SUFFIXES):
            bad["runtime_or_build_paths"].append(path)
        if sz.get(obj, 0) > max_bytes and path not in allow_large:
            bad["oversized"].append(f"{path} ({sz[obj]} bytes)")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX)
    ap.add_argument("--inherited", default="ci/inherited-protected.sha256")
    ap.add_argument("--allow-large", default="ci/large-file-allowlist.txt")
    args = ap.parse_args(argv)
    repo = Path(args.repo)

    def lines(p: str) -> set[str]:
        f = repo / p
        return {x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")} if f.exists() else set()

    bad = check(repo, args.max_bytes, lines(args.inherited), lines(args.allow_large))
    failed = {k: v for k, v in bad.items() if v}
    for k, v in failed.items():
        print(f"FAIL {k}: {len(v)}")
        for item in v[:20]:
            print("   ", item)
    if not failed:
        print("repository policy: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
