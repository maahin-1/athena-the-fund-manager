# Git preparation review 4 — 17 November 2026

## Verdict

The four findings in GIT-PREPARATION-REVIEW3.md are closed at the reviewed local/offline scope. No additional blocking defect was established in this targeted review. This clears those corrections for the planned local checkpoint phase when the owner authorizes commits; it does not certify GitHub enforcement, intermediate commit states, or production readiness.

T6 stays closed with its existing qualifications. T8 stays paused.

## Independent verification

- Branch/HEAD read back: main at 37f1fc19846a178083d9ced7a7a25aeffe65b1f9.
- Ran the unchanged six reviewer probes plus test_compare_manifest_sources.py: 37 passed, zero failures. Fresh results are in git-prep/review4/results.txt and results.xml. Earlier reviewer evidence was not overwritten.
- The workflow probes exercise policy logic using JSON-compatible YAML and a JSON parser adapter, as before. They do not establish PyYAML parsing behavior.
- Source inspected: safe_checkout.sh, check_workflows.py, workflow-policy.json, compare_manifest.py, the relevant checkout-harness tests, review3/RESPONSE.md, and the two corrected Rust tests.
- Authenticated checkout refuses the prior explicit overrides, drops inherited GIT_* controls, selects the hosted repository/commit context, and compares fetched and checked-out commit identities.
- The metadata check rejects the previously unsafe filename shapes before checkout; the original escape regression passes.
- The comparator checks the reviewed source aliases and resolved containment; the original intercepted-read regression and 31 source-safety cases pass.
- Rust tests now use their own symbols and assert bars:<symbol>:raw plus the provenance/generation their own request supplied. This fixes the independently confirmed stale bare-bars assertions.

## Limits

The implementer's 172-test total, full Rust run, individual Rust executions, backup/archive hashes, manifest rehearsals and 43-test Python-child gate were not independently rerun here. The test-only Rust changes were source-reviewed. No hosted workflow, GitHub setting, Playwright suite, production store or real-commit final gate was exercised.

No application code was changed by this reviewer. No commit, push, merge or remote change was made. Only reviewer evidence and this acceptance record were added.

## Small documentation corrections

- SETTINGS-MATRIX.md says no GitHub setting was inspected and then says no ruleset exists. The first statement does not establish the second. Change this to: no ruleset was created by this work; existing effective settings are unknown until the planned inventory.
- Preserve the disclosed real-git/DNS attempt as an attempted external network operation in the harness history. Its lack of real credentials does not make it an offline fake run. Exclude that run from offline-isolation claims; it need not invalidate the corrected runs.

## Next sequence

1. Keep the reviewed implementation stable. Finish owner choices needed for the next authorized phase: maintainer identity, reviewer/owner exception, merge approach, exclusion ownership, and the remaining settings/publication choices before their corresponding hosted actions.
2. Once local commits are authorized, execute the existing coherent commit plan on the integration branch. Check the exact staged allowlist for every commit; preserve the protected-entry exception without materializing it, and keep reviewer scratch/nested repositories outside staged content.
3. Run each planned intermediate-state check. Resolve a failing dependency boundary by adjusting the coherent commit grouping rather than claiming that final-tree results validate every earlier commit.
4. Run final_gate.ps1 end to end on actual committed candidates, including the combined maintenance tip, and save the manifest, count, import-origin and cross-language evidence.
5. Treat push, GitHub configuration, merge and deployment as later separately authorized actions. Inspect effective settings, establish protection, and verify hosted enforcement before merging. T8 does not restart as a side effect of Git preparation.

Do not rerun unrelated broad suites solely to inflate evidence. Re-run the checks required by the actual new commit states and any changes made to resolve their failures.
