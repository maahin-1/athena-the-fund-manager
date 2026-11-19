# Git preparation review 3 — 17 November 2026

Verdict: the six previous correction groups are addressed on source inspection, but preparation is not ready for the committing/checkpoint phase. Four concrete items below remain. T6 remains closed with its qualifications; T8 remains paused.

No application source was changed. No repository commit, push, merge, remote setting change, provider call, production data access or protected-file access occurred. This review added only this report and isolated reviewer evidence.

## Evidence and limits

- Branch and HEAD independently checked: main, 37f1fc19846a178083d9ced7a7a25aeffe65b1f9.
- Ran git-prep/review3/test_review_regressions.py: 6 collected, 2 passed, 4 assertion failures. Saved results.txt and results.xml beside it.
- Two controls pass: an ordinary approved workflow and an ordinary filename exclusion.
- Workflow probes use the actual checker and actual approved checkout script with JSON-compatible YAML fixtures. A JSON parser adapter substitutes for unavailable PyYAML in this reviewer interpreter. This verifies checker logic on those mappings, not YAML parser behavior.
- The filename probe executes the actual script's escape expression, then asks Git which pattern matches an ordinary, nonexistent filename. It never creates a protected file or performs a checkout.
- The comparator probe intercepts a virtual protected source's read before any filesystem read. No protected source exists.
- The security consequences of checkout redirection are inferred from the approved script's URL selection and Authorization-header construction. No fetch, real token or network experiment was performed.
- The two Rust failures-alone are reported by the implementer; the stale-key cause below is independently confirmed from source. No Rust application suite was rerun in this review.
- The claimed backup/archive verification and complete stand-in runs were not independently repeated. No real-commit final gate or hosted GitHub checks have yet been demonstrated.

## R1 — authenticated checkout accepts inherited remote and revision overrides (P1)

Files: maintenance-proposal/files/scripts/ci/safe_checkout.sh, lines 7–19; check_workflows.py, check_file.

The checker enforces the checkout step's own env, but does not reject relevant workflow-level or job-level env. A workflow-level REMOTE_URL pointing at a different HTTPS origin passes. A job-level CHECKOUT_SHA overriding the tested revision also passes.

The approved script selects these overrides, adds the selected URL as origin, and attaches the GH_TOKEN-derived Authorization header to the fetch. Thus an inherited override can send that header to the selected origin, or cause the gate to test a different tree. The current proposed workflows were not shown to contain such an override, and no credential leak is claimed.

Fix: authenticated hosted mode must derive/validate the repository and requested commit from trusted job context, with no inherited remote/ref override. Keep local-fixture overrides only in an explicit tokenless mode. Validate the relevant control variables at every environment scope, and ensure the actual fetched/checked-out commit equals the intended candidate. Include negative controls for workflow and job inheritance. Never log token/header values.

Observed: both negative policy tests return an empty error list and fail their rejection assertions.

## R2 — exclusion encoding can materialize a protected path before failing (P1)

File: maintenance-proposal/files/scripts/ci/safe_checkout.sh, lines 25–32.

The conversion from a NUL-delimited Git path to a line-delimited sparse pattern escapes brackets, backslashes, stars and question marks, but not trailing spaces or line breaks. For an ordinary virtual filename ending in a space, Git selects the include-all pattern rather than the intended exclusion; the corresponding ordinary name without the space is correctly excluded.

A similarly shaped protected basename is legal in a Git tree on Linux. The final name audit runs after checkout, so detecting the mistake then would not meet the requirement to avoid materialization. A newline also cannot be inserted raw into a line-based pattern.

Fix: before checkout, reject unsupported path shapes from metadata, or encode them correctly. A conservative pre-check rejecting CR/LF and unsupported trailing whitespace is sufficient for now. Cover parent-directory components too. Use ordinary virtual names or mocked metadata for the regressions; do not materialize protected fixtures.

Observed: ordinary filename control passes; trailing-space filename selects /* and fails.

## R3 — manifest comparator filters destination names but reads source aliases unchecked (P1)

File: git-prep/verify/compare_manifest.py, lines 101–122.

Only the candidate entry's path is filtered. A safe candidate path can supply an absolute source, or a source_path under a source root, whose basename is protected. The comparator calls read_bytes on that source without validating it against the access policy. The virtual probe intercepts that attempt before any read.

Fix: validate every selected source and candidate path before content access. Check both the declared path and resolved target, restrict them to declared roots, and reject protected aliases and root escapes. Apply the same guard to every alternate source root and source_path lookup. Keep all regression targets virtual/intercepted or ordinary synthetic files.

This is an access-safety gap in the tool; it is not evidence that an existing frozen manifest currently contains such an alias.

## R4 — repair the two known order-dependent Rust assertions before checkpoints (P2)

File: terminal/src/server.rs, lines 1252 and 1281; serving registration at line 181.

Both assertions look up readers["bars"], while their own request registers a subject-specific key such as bars:SBIN:raw. A pre-existing bare bars entry created by another test makes the assertions pass without establishing what their own HTTP request registered.

Fix test code only: give each case its own subject and assert the key, provenance and generation produced by that request. Remove the stale explanation that other tests overwrite the same bare key. Do not merely pre-seed bars or depend on suite order. Run each exact test in a fresh process, the pair in both orders, and the full suite. Record the selected IDs and results; refresh the Python-backed-test discovery record if needed.

Yes: fix these before committing. They are concrete false-positive assertions, not a reason to reopen T6.

## Next boundary

Address R1–R4 and rerun the focused controls first. Then refresh the inventory/manifests/backups for the new reviewer artifacts and changes. Do not modify previously verified archives.

Keep the existing commitment boundary until these checks are reviewed. Actual intermediate-commit checks, clean-candidate final_gate execution, GitHub ruleset enforcement and hosted CI remain future verification, not completed work. Identity, visibility, review model, reporting contact and publication choices remain owner decisions; do not infer authorization from a remote URL.

No new requirement to access protected files, production stores, or live services is introduced.
