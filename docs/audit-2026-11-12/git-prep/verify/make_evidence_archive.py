"""Builds a verified archive of the evidence files that are NOT committed to Git (raw logs, bulk results, regenerable databases, de-duplicated copies) and its manifest.

Input: curation.json ({"files": {repo-relative path: {"action": "commit"|"archive"|"duplicate", "why": ..., "size": ..., "sha256": ..., "canonical": ...}}}) and the repository root.
Output (a new folder, never overwritten): evidence-archive.zip, MANIFEST.json (path, size, sha256, why, canonical), MANIFEST.sha256, VERIFICATION.json.
Verification: every source file is re-hashed against the curated hash; every zip entry is read back and re-hashed against the manifest; the zip's own SHA-256 is recorded.
A local archive is not public reproducibility: where it is published (for example as a release asset) is an owner decision.
Usage: python make_evidence_archive.py --curation curation.json --repo <root> --out <new folder> --label <name>"""
import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path


def lp(p: str) -> str:
    return "\\\\?\\" + os.path.abspath(p) if os.name == "nt" else p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curation", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        print("refusing to reuse an existing folder:", out)
        return 2
    out.mkdir(parents=True)
    files = json.loads(Path(a.curation).read_text(encoding="utf-8"))["files"]
    chosen = {p: v for p, v in files.items() if v["action"] in ("archive", "duplicate")}
    manifest, bad_source = [], []
    zpath = out / "evidence-archive.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(chosen):
            v = chosen[p]
            src = os.path.join(a.repo, p.replace("/", os.sep))
            data = open(lp(src), "rb").read()
            if hashlib.sha256(data).hexdigest() != v["sha256"]:
                bad_source.append(p)
                continue
            z.writestr(zipfile.ZipInfo(p, date_time=(2026, 11, 17, 0, 0, 0)), data, compress_type=zipfile.ZIP_DEFLATED)
            manifest.append({"path": p, "size": v["size"], "sha256": v["sha256"], "kind": v["action"], "why": v["why"], **({"canonical_in_git": v["canonical"]} if "canonical" in v else {})})
    (out / "MANIFEST.json").write_text(json.dumps({"label": a.label, "files": manifest}, indent=1), encoding="utf-8")
    (out / "MANIFEST.sha256").write_text("".join(f"{m['sha256']} *{m['path']}\n" for m in manifest), encoding="utf-8")
    # read every entry back out of the archive
    mismatches, names = [], set()
    with zipfile.ZipFile(zpath) as z:
        corrupt = z.testzip()
        for m in manifest:
            names.add(m["path"])
            if hashlib.sha256(z.read(m["path"])).hexdigest() != m["sha256"]:
                mismatches.append(m["path"])
        extra = [n for n in z.namelist() if n not in names]
    zsha = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (out / "evidence-archive.zip.sha256").write_text(f"{zsha} *evidence-archive.zip\n", encoding="utf-8")
    res = {"label": a.label, "files": len(manifest), "bytes_uncompressed": sum(m["size"] for m in manifest), "zip_bytes": zpath.stat().st_size, "zip_sha256": zsha,
           "source_hash_mismatches": len(bad_source), "zip_entry_mismatches": len(mismatches), "zip_corrupt_entry": corrupt, "zip_entries_not_in_manifest": len(extra),
           "ok": not (bad_source or mismatches or corrupt or extra)}
    (out / "VERIFICATION.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
