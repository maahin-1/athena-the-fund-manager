# Response to the revision-2 review (17 November 2026)

The files beside this one (`README.md`, `summary.json`, `reviewer-tests.log`, `reviewer-results.xml`, `test_review_regressions.py`, `zero-origin-report.json`) are the reviewer's and are kept as found. The reviewer's pytest scratch folder `synthetic-cases/` (synthetic Git repositories and directory symlinks, about 220 files) is **not committed**: as the reviewer's README asks, it goes to the evidence archive.

## The six groups and where each is now covered

| Reviewer finding | Change | Tests that repeat the reviewer's case |
|---|---|---|
| Final gate omits `--source-root`, reuses `Work`, continues after a failed candidate | `verify/final_gate.ps1` rewritten; stage scripts split out | `verify/test_final_gate.ps1` (20 inert orchestration tests) |
| Unplanned deletion of an unchanged baseline file passes; maintenance manifest compared with the wrong reference | `verify/compare_manifest.py` checks removals; maintenance is compared with the integration tip | `verify/test_compare_manifest.py`: `test_reviewer_control_an_unchanged_baseline_file_passes`, `test_reviewer_regression_an_unplanned_baseline_deletion_is_rejected` (the reviewer's exact scenario, a `git mktree` baseline) and five more on real throwaway repositories |
| Rust gate accepts zero Python children | `verify/rust_gate.ps1`, `rust_evidence.py`, start records in `origin_guard/sitecustomize.py`, `verify/rust-python-tests.json` | `verify/test_rust_evidence.py` (10 tests), including the reviewer's `zero-origin-report.json` case: an empty log with a minimum of zero no longer passes the Rust gate, which now requires at least one process in the full run and per Python-backed test |
| Hosted checkout materializes the inherited protected entry | access-safe checkout step replaces `actions/checkout` | `maintenance-proposal/files/tests/test_ci_repo_policy.py` (checkout script run against a throwaway origin) |
| Workflow checker: nested expression passes; a wrong pin hides behind an approved pin | `check_workflows.py`: no expression in any script, every reference equal to its recorded repository/SHA pair | `maintenance-proposal/checks/test_check_proposal.py` (the four reviewer cases, real PyYAML) and `files/tests/test_ci_repo_policy.py` (17 tests, real PyYAML, 0 skipped, when this was written; 105 after review 3) |
| Zero required approvals for everyone; merge-first bootstrap | two rulesets, one narrow owner bypass of review only; inventory first, protect `main`, one pull request | `maintenance-proposal/SETTINGS-MATRIX.md`, `ENFORCEMENT-TESTS.md` (tests 12 to 14); not applied anywhere |

## The reviewer's own test file against the corrected code

`test_review_regressions.py` run as it stands (fresh basetemp outside the repository): **2 passed, 4 failed**. The two comparator cases pass (the control, and the unplanned deletion now rejected). The four workflow cases fail **because their fixtures belong to the earlier interfaces**, not because a defect remains: they inject a JSON adapter in place of the YAML parser, use `actions/checkout` (now forbidden by design: it would materialize the protected template), a one-argument `check_file(path)` and a `--pins` option (pins now live in `ci/workflow-policy.json`). The same four cases are repeated with the real parser and the revision-3 interfaces in `maintenance-proposal/checks/test_check_proposal.py` (4 passed). The reviewer's file is not edited and is not collected by the project's test run (`testpaths = ["tests"]`).
