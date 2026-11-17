# Git preparation review - 17 November 2026

Disposition: **backup and inventory accepted within the verification scope below; body-limit hardening accepted on source review; commit execution not yet ready. T6 remains closed with its existing qualifications, and T8 remains paused.**

No commits, branches, pushes, merges, deployments, production reads or application edits were performed by this reviewer.

## What was independently verified

- Current branch is main, at 37f1fc19846a178083d9ced7a7a25aeffe65b1f9.
- Git metadata shows 2,873 permitted pending paths at review time. The component plan accounts for them except the four explicitly excluded paths: empty CLAUDE.md, r.txt and two examples.
- The main backup archive SHA256 is:
  dd5053e59045a8dcf669fb365f379237f777515d27689cbcc777bebd26c86077.
- Main archive entry metadata contains exactly 3,791 files, plus 8 directory entries; its filenames exactly match MANIFEST.json. No protected filename or unsafe relative/absolute traversal path was found.
- Ignored-evidence archive entry metadata contains 643 files; its SHA256 matches:
  aa3839baf7990c5815b73cdfc3182bc1497aad729089947c8b10fb505c5f4a9b.
- The named verification JSON reports ok=true and zero copy/restore mismatches. The reviewer did not independently extract or rehash every backed-up file; archive identity and metadata, plus those saved verification reports, are the evidence checked here.
- The current server.rs production prefix is byte-identical to the permitted first-backup file: 55,874 bytes, SHA256
  9ba94c424045fa27921360f30030da76cd3885463a85cf7eb1d71fd9b4f577b5.
- The test patch preserves exact body lengths, framework 413 versus handler responses, streamed/no-length coverage and separate handler limits. Cargo adds a dev dependency on an already locked tower version.
- The 400-run stress results, 5 mutations and ten full suites were not independently repeated. There is no need to repeat that campaign merely to revise a plan.

Minor wording correction: the boundary requests use in-process dispatch, but their setup still calls start(), which binds a listener. They avoid the oversized-upload socket race; they are not entirely socket-independent.

The maintenance plan was written by this reviewer during the previous turn. Its appearance is explained provenance, not an unidentified implementation-agent change.

## GP1 - P1: C12 as written does not execute the cross-language contract

Locations: GIT-PREPARATION.md:138; tests/test_snapshot_crosslang.py:1-14 and its skipif decorators.

The proposed C12 check only invokes the Python test file. Its module explicitly documents that ordinary runs skip both tests unless fixture variables are supplied. It also requires two ignored Rust tests in between. A zero-failure pytest result can therefore mean no cross-language verification happened.

Replace the single command with the existing four-step orchestration:
1. Python publishes into a new empty synthetic directory.
2. Rust reads/validates that exact Python generation.
3. Rust publishes into a separate synthetic directory.
4. Python reads/validates those Rust generations.

Specify exact argv, directories, candidate interpreter and the intended test IDs. Require nonzero execution of every stage and treat unexpected skip/filter-zero as failure. Include this orchestration in final verification as well as C12 or its replacement commit.

## GP2 - P1: the final checkout/gate contradicts the inherited protected path

Locations: GIT-PREPARATION.md:145-147; current Git filename metadata.

One protected environment-template path is already tracked in the baseline. None of the proposed commits changes that fact. The planned requirement that git ls-files contain zero protected filenames therefore cannot pass as written. A normal checkout/clone of the baseline can also materialize that file, which violates the standing no-copy/no-create restriction.

Resolve this in the plan before any checkout:
- Default preservation option: retain the unchanged baseline entry as an explicit metadata-only exception, do not materialize it, and forbid new or changed protected entries. Use a checkout/candidate assembly method that configures its exclusions before materialization.
- Alternatively, propose a separately authorized metadata-only untracking action that leaves the local file untouched. Do not open, restore, copy or delete it to achieve the gate.
- Do not weaken the policy globally or claim a zero-filename check passed when the inherited entry remains.

No protected filename or file content was printed or read for this finding; only its count and inherited status were checked.

## GP3 - P2: clean candidate verification needs explicit code and harness identity

Location: GIT-PREPARATION.md:120-147.

Using an existing virtual environment or linked node_modules can be reasonable offline, but the plan must prove code is imported from the candidate rather than the dirty original checkout. pytest's pythonpath setting does not establish where every subprocess invoked with python -m athena imports its code.

Add:
- Exact commands/working directories and setup for the candidate interpreter, Rust/Python child tests and linked dependencies; fail if the required Python/sh/compiler capability is absent.
- Source-origin assertions for candidate athena and relevant child-module execution. Do not reinstall into or retarget the owner's original editable environment merely to make a candidate pass.
- The approved web isolation guard and empty variable-file directory, with its actual limits and violations recorded.
- Web production build as well as typecheck and Vitest; synthetic browser checks according to the maintained gate scope.
- Collected IDs, explicit nine-exclusion manifest, skipped-body reporting and actual cross-language execution.

Do not recursively diff entire checkout/backup directories with git diff --no-index: that can traverse repository metadata, linked environments, build trees or protected inherited files. Compare the explicit allowed source/fixture manifest instead, accounting only for the declared line-ending policy.

Import-graph order is a useful planning heuristic, not proof of API/runtime compatibility or that any intermediate state builds. Keep proposed per-commit checks explicitly unrun until they are actually executed.

## GP4 - P2: the requested maintenance proposal is missing

Locations: GIT-PREPARATION.md:113 and :171; REPOSITORY-MAINTENANCE-PLAN.md.

C16 includes only the plan document. No proposed CI/Dependabot/contribution configuration, server-settings matrix or enforcement test plan has been produced. Those were requested deliverables of preparation; citing the plan is not implementing its preparation requirements.

Prepare local reviewable proposals for:
- CONTRIBUTING, SECURITY, code of conduct, PR/issue templates and verified CODEOWNERS assignments (including protection of workflow/policy files).
- CI workflows covering the actual toolchains, fake/offline test constraints and trustworthy aggregate gate.
- Dependabot for Python, Cargo, npm and Actions, without default auto-merge.
- A settings matrix for branch/review rules, bypasses, fork workflow approval, permissions and security features. Mark all server settings unapplied/unverified until read back and exercised.
- Bootstrap sequence, solo-maintainer review exception or second-reviewer decision, and negative merge/fork tests.

Do not push, create public PRs/issues, alter remote settings or treat files as proof of server enforcement during this preparation phase. Personal .claude/settings.json stays outside public commits; if hooks are part of onboarding, document sanitized installation and distinguish them from server enforcement.

## GP5 - P2: do not recreate a known flaky Rust state solely to split history

Locations: GIT-PREPARATION.md:108-118 and :137-139.

The plan copies old server.rs/Cargo.toml into C11 to restore the now-measured flaky test, then fixes it in C13. This makes a deliberately unreliable intermediate state despite the goal of reviewable commits with meaningful gates.

Recommendation: keep C11 as one coherent Rust component commit using the current hardened tests and final Cargo files. The standalone hardening patch and the preserved pre-fix evidence already make the test change reviewable. Drop the artificial rollback/reapply sequence unless there is a compelling requirement and it can be verified.

Grouping by component is acceptable. The assertion that a file can only be committed once, whole, is false as a Git constraint: partial staging and later changes are possible. Describe this as the chosen final-state grouping, not a limitation of Git. Do not reconstruct unverifiable batch history.

## Recommended decisions on the five questions

1. **Evidence:** curate what goes into normal Git history. Keep plans, review reports, canonical repro scripts, mutation specs, summaries, collected IDs and an artifact index with full hashes. Preserve raw logs/duplicates/regenerable databases in verified archives. Do not blanket force-add ignored logs. If a particular ignored artifact is truly required, use an explicitly reviewed path allowlist. Keep relative links valid, or mark/index the archive location. A local-only backup is not public reproducibility.
2. **CLAUDE.md/r.txt/examples:** preserve them, leave the empty instructions and scratch file out, and treat the two examples as a separate reviewed onboarding change. No deletion or unrelated staging.
3. **C11:** retain one coherent Rust commit rather than inventing overlapping batch snapshots; include the hardened tests as above. Split further only where an independently buildable/testable boundary is demonstrated.
4. **Line endings:** add an explicit .gitattributes policy in a small bootstrap change. Prefer LF for source/config/docs and an explicit tested PowerShell policy, commonly CRLF; preserve binary files. Limit any normalization to named permitted paths in the candidate. Never run blanket renormalization over this working tree or touch protected files. Check that only declared EOL changes occurred, without changing encoding or logical content.
5. **Maintenance:** keep the plan in a separate maintenance/bootstrap group with the actual reviewable proposals. It is part of the agreed scope, not an unexplained optional addition.

## Next step

Revise GIT-PREPARATION.md and commit-plan.json using these decisions, complete the maintenance proposals, and specify an access-safe candidate/check procedure. Keep backup archives immutable; this new reviewer report and later proposal files need a small manifest-backed delta backup or a newly versioned backup, not replacement of the verified archives.

Report the revised mapping, exact gate commands and unresolved owner-only settings decisions. No commit/push/merge/deployment/T8 is requested yet. T6 functional closure is unchanged.
