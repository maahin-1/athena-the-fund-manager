# Evidence index and archive (17 November 2026)

The audit's evidence is large (about 249.96 MB in 6,199 files), mostly raw logs and bulk results that nobody reads line by line. It is split deliberately:

- **In Git (curated):** the reproducibility record: scripts and specifications, the READMEs and index documents, each run's command, toolchain, exit codes, summary, collected test IDs, small failure logs, and the reviewers' artifacts.
- **In a verified archive, indexed here:** raw logs, bulk result files, raw event logs, regenerable databases, and larger copies of records that also exist in Git. **Nothing was force-added past `.gitignore`** (raw `logs/` folders and `*.duckdb` stay ignored); 643 git-ignored files are in the archive instead.
- **Duplicates:** a larger record whose bytes are identical to another one in Git is not committed twice; it is listed in the manifest with the path of the copy that is in Git.

A run folder in Git therefore may not hold every file its README mentions (for example `rust-test.log`). Every such file is listed in [evidence-archive-manifest.json](evidence-archive-manifest.json) with its path, size, SHA-256 and reason. No evidence was deleted to reduce the count, and the two verified working-tree backups are unchanged and still hold everything.

## Counts

| Evidence folder | In Git (files, MB) | Archived (files, MB) | Duplicates indexed (files, MB) |
|---|---|---|---|
| `agent-probes` | 3 (0.00) | 457 (0.30) | 0 (0.00) |
| `git-prep/runs` | 176 (1.24) | 2209 (200.45) | 7 (0.63) |
| `maintenance-proposal/runs` | 18 (0.02) | 0 (0.00) | 0 (0.00) |
| `wp07-2026-11-15` | 43 (0.26) | 11 (0.72) | 0 (0.00) |
| `wp07-t3-2026-11-16` | 97 (0.72) | 71 (2.25) | 2 (0.15) |
| `wp07-t3r-2026-11-16` | 1248 (6.06) | 1138 (19.80) | 58 (1.89) |
| `wp07-t5-2026-11-15` | 80 (0.67) | 51 (4.03) | 3 (0.05) |
| `wp07-t5f-2026-11-16` | 112 (0.56) | 75 (2.11) | 4 (0.08) |
| `wp07-t5r-2026-11-15` | 144 (0.89) | 138 (6.02) | 4 (0.07) |
| `wp07-t6-review-2026-11-16` | 6 (0.04) | 2 (0.04) | 0 (0.00) |
| `wp07-t6r-review-2026-11-16` | 5 (0.01) | 4 (0.14) | 0 (0.00) |
| `wp07-t6r3-independent-2026-11-17` | 6 (0.02) | 5 (0.24) | 0 (0.00) |
| `wp07-t6r4-independent-2026-11-17` | 6 (0.02) | 5 (0.25) | 0 (0.00) |
| `wp07-t6rr-review-2026-11-17` | 6 (0.02) | 5 (0.20) | 0 (0.00) |
| **all** | **1950** (10.54) | **4171** (236.54) | **78** (2.87) |

| Reason archived | Files |
|---|---|
| raw log | 2146 |
| git-ignored raw log or regenerable database (not force-added) | 643 |
| reviewer scratch: synthetic repositories and fixtures made by a reviewer's pytest run (archived, not committed; nested .git folders must never be added) | 457 |
| raw event log | 293 |
| bulk raw result JSON (counts are in the run summary) | 120 |
| file-by-file audit of the isolated copy (bulk; the tree hash is kept) | 90 |
| per-file hash list of the isolated copy (bulk; the tree hash is kept) | 90 |
| raw cargo --list output (the IDs are kept in rust-test-ids.txt) | 89 |
| raw stderr of the list step | 88 |
| bulk raw JSON (> 20 KB) | 44 |
| raw guard hit log (counts are in the summaries) | 42 |
| raw baseline result of a mutation run | 28 |
| large file (> 32 KB) | 19 |
| raw collection output (IDs are in collected-ids.txt) | 13 |
| raw junit report (counts are in summary.json) | 7 |
| historical raw result of the superseded v1 guard run | 1 |
| raw self-test output | 1 |

## The archive

| | |
|---|---|
| Location (local) | `D:\projects\learn-project\athena-backups\evidence-archive-v6-20261117\` (`evidence-archive.zip`, `MANIFEST.json`, `MANIFEST.sha256`, `VERIFICATION.json`) |
| Files / bytes | 4,249 files, 239,416,071 bytes uncompressed, 10,882,852 bytes zipped |
| Zip SHA-256 | `8ed07649610a5ee1b9118ab8d15ca5301349051a71e5eeb5113b36721cadb2b1` |
| Verification | every source file re-hashed against the curated hash (0 mismatches); every zip entry read back and re-hashed against the manifest (0 mismatches, 0 entries not in the manifest, no corrupt entry) |

To check a file: find it in `evidence-archive-manifest.json`, extract it from the zip, hash it, compare. To rebuild the split from a working tree: `python docs/audit-2026-11-12/git-prep/verify/curate_evidence.py --repo <root> --out curation.json`, then `make_evidence_archive.py` (same folder). The rules are in the first lines of `curate_evidence.py`.

**Limits.** The archive is local: it is not public reproducibility. Where it is published (for example as an asset attached to a release, with this hash in the release notes) is an owner decision (GIT-PREPARATION.md, decisions). Later runs (for example the final clean-checkout verification) are new evidence and need a new archive version; this one is not edited.
