# Review 3 regressions, run BEFORE any fix (17 November 2026)

Each file is the unedited output of the new regression tests against the code as it stood when review 3 arrived. They are kept to show that the tests fail for the reviewed defect and not for some other reason. Nothing here is a result for the fixed code: see the runs named in `runs/README.md` (rows for review 3) for those.

| File | What ran | Outcome before the fix |
|---|---|---|
| comparator-sources-before.txt | `test_compare_manifest_sources.py` (R3): a spy on every file-system entry point records each path the comparator touches | 30 failed, 1 passed in 4.75s. The one control (an ordinary source) passed; every protected-source, root-escape, unsupported-shape and link case failed, and the protected cases show the comparator touching the protected path |
| checkout-script-and-checker-before.txt | `test_ci_repo_policy.py` (R1, R2) including the earlier tests | 76 failed, 26 passed in 40.98s. The first harness run used the real git instead of the recording stand-in (the stand-in was not first on Git Bash's PATH) and a hostile `REMOTE_URL` was then really attempted (a DNS failure for the override host, no credential involved). That was attempted network access, not an offline fake run, and that first run is excluded from every offline-isolation claim; the harness was corrected to put the stand-in in front through `BASH_ENV` and a guard test proves it is the git that runs. The file kept here is the corrected run |
| rust-alone-snapshot-test-before.txt, rust-alone-endpoint-test-before.txt | each of the two named Rust tests alone in a fresh process | both fail at their `readers["bars"]` assertion (R4) |

Harness defects found while writing these tests are listed in `runs/README.md`.
