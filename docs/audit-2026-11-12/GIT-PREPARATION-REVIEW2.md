# Git preparation revision 2 review - 17 November 2026

Disposition: **revision 2 fixes much of GP1-GP5, but the new verification and repository-control layer is not ready to execute commits or enforce merges. T6 remains closed with its qualifications; T8 remains paused.**

## Verified progress and review scope

- main is still 37f1fc19846a178083d9ced7a7a25aeffe65b1f9.
- The cross-language script names and judges all four stages, with plain-skip and tamper controls; the sparse candidate builder excludes the inherited protected path before checkout.
- Component grouping now keeps the hardened Rust tests and accurately labels intermediate checks as unrun.
- The maintenance proposal, settings matrix and enforcement tests now exist, and correctly distinguish local files from server enforcement in most places.
- Delta archive: 237 file entries (224 new + 13 changed), no protected or unsafe archive filename, SHA256 dbeefad3ee215d4f8c0daeb44b256f2944d0a34530d5a8ef1d97d9c4202b3a7f.
- Evidence archive: 1,659 file entries, no protected or unsafe archive filename, SHA256 fb12694ef3924d5ba3306787661a732343e5a36483aa156b8ba23d3d8d755dd2.
- Those identities match the report. The delta VERIFICATION.json reports zero copy/restore mismatches. No independent extraction/full per-file rehash was performed.
- The application Python/Rust/web broad runs and four-stage orchestration were not repeated by this reviewer.
- No real workflow, provider, broker, production store, installation, project commit/push or GitHub settings mutation occurred.

## Independent regression evidence

Artifact directory: git-prep/review2/.

Ran the nine existing manifest-comparison tests, three reviewer controls, and three new negative regressions: **12 passed, 3 failed**, exit 1. The failures reproduce:
1. an unplanned baseline file deletion being accepted;
2. nested untrusted PR text in a shell expression being accepted;
3. a wrong action pin hidden by an approved pin elsewhere being accepted.

The manifest probe creates only synthetic Git objects/indexes and directories; it creates no commits. The workflow probes use JSON-compatible workflow documents (valid YAML) with a JSON parser adapter because PyYAML is not in the project environment. They test actual checker functions after parsing; YAML parser/schema coverage is not claimed. Re-run equivalent cases with the already available real parser when fixing them.

Separately, origin_report.py with a missing log and --min-athena-processes 0 returned exit 0, zero observed processes and ok=true. This demonstrates its configured empty-observation behavior, not a complete Rust-suite reproduction.

## R2-1 - P1: final gate is not wired to its verified source manifests, and is unsafe after candidate failure

Locations: git-prep/verify/final_gate.ps1:29, :38-41; GIT-PREPARATION.md section 9.

The script omits --source-root at the compare_manifest call. expected-integration.json has 2,049 rows and expected-maintenance.json has 29; neither has absolute source entries. compare_manifest therefore cannot find their sources. The standalone documented comparison includes the roots, but the actual final orchestrator does not. A fully assembled candidate cannot pass the orchestrator as written.

Additionally, Work is documented as nonexistent but created with -Force. An existing Work/candidate makes build_candidate fail; the orchestrator deliberately continues into comparison, pytest, Rust and web against that existing directory. pytest basetemp may clear old scratch contents. This is a safety failure even though the final summary will be red.

Fix:
- Accept explicit verified source roots and pass them, in declared precedence order, to comparison. Test missing roots and source hashes.
- Reject existing Work before changing it; constrain resolved scratch paths and reject aliases/junctions to source/protected locations as appropriate.
- Treat candidate construction and access/identity checks as prerequisites. After failure, mark dependent execution stages not run; do not reuse any pre-existing candidate.
- Test failure sequencing with inert injected stages before using the orchestrator on real commits. No project commits are needed to test fail-stop control flow.

## R2-2 - P1: comparator misses unplanned baseline deletions; maintenance baseline is wrong

Location: git-prep/verify/compare_manifest.py:136-162; expected-maintenance.json.

The comparison iterates only paths still present in candidate ls-files. A baseline file outside the expected-change manifest can disappear entirely, and no missing/changed result is recorded. A clean committed deletion can therefore pass this gate. The new regression shows ok=true after deleting baseline_keep.py while the one expected file remains correct.

Compare the complete metadata delta in both directions: reject removed baseline paths unless explicitly approved, as well as changed/added paths. Include mode/type changes and the protected-entry exception without opening protected content.

The maintenance manifest contains only 29 maintenance files but still names 37f1fc1 as baseline. A maintenance tip also contains all integration changes. Comparing it against the original baseline with only those 29 expected paths will reject legitimate integration changes. Either compare maintenance against the frozen integration tip, or use an effective combined manifest rooted in the original baseline. Make baseline selection explicit rather than hardcoded in the gate.

## R2-3 - P1: Rust gate can count silently bypassed Python-backed tests as verified

Locations: git-prep/verify/rust_gate.ps1:26, :46-53.

The gate validates the supplied interpreter but accepts any existing candidate/.venv directory. It never verifies the actual .venv/Scripts/python.exe path that Rust tests use. Existing tests can return early if that path is missing while libtest reports ok.

It then runs the origin reporter with minimum zero and checks only violations==0. A missing recorder/log and zero executed Python children can therefore pass that portion of the gate.

Validate and exercise the actual child interpreter and required sh path before tests. Require evidence from the intended Python-backed test bodies, not merely a successful preflight process. Fake inline children need not import athena; distinguish process/body execution from modules imported, and fail if required observations are absent. Report terminated/unobserved processes honestly: an atexit recorder alone cannot prove every child ran or exited normally.

## R2-4 - P1: proposed GitHub checkouts bypass the inherited-path exclusion

Locations: maintenance-proposal/files/.github/workflows/ci.yml:41, :64, :98, :144; dependency-review.yml checkout.

The local builder excludes the inherited protected template before checkout. Every proposed hosted checkout instead uses the default full checkout with only persist-credentials:false. It would materialize the inherited protected path. check_repo_policy validates its object identity but does not prevent or diagnose materialization.

Carry the approved no-materialization policy into every applicable checkout before it writes files, and verify both unchanged metadata and actual absence. A check after copying the file is too late. Do not run these proposed workflows unchanged to validate them.

This is a source-inspected policy contradiction in unactivated proposals; no protected file was materialized by this review.

## R2-5 - P2: workflow validators have demonstrated bypasses

Locations:
- maintenance-proposal/files/scripts/ci/check_workflows.py:22 and :60.
- maintenance-proposal/checks/check_proposal.py:57-65.

The workflow checker only catches untrusted contexts immediately following the expression opener. A nested format expression using github.event.pull_request.title in run passes with no errors. Treat the complete expression as untrusted; route event data through safely quoted inputs instead of interpolating it into shell text.

The pin checker searches concatenated raw text for one approved repo@sha. One good occurrence hides a different SHA used by another step. Parse every actual uses node (including supported reusable-workflow forms), compare exact repository/SHA pairs with reviewed provenance, ignore comments as evidence, and ensure the intended CI policy path exercises the rule. SHA shape is not pin provenance.

Both negative regressions fail while the safe-workflow and wrong-pin-alone controls behave as expected. The existing proposed workflows themselves do not currently interpolate PR text into run or expose application secrets; the finding is about the claimed policy enforcement.

## R2-6 - P2: solo-maintainer and bootstrap recommendations weaken the requested review contract

Locations: maintenance-proposal/SETTINGS-MATRIX.md D3/R3; maintenance-proposal/README.md:42.

Zero required approvals is a repository-wide setting, not an author-specific owner exception. Under option A, an external PR also has no enforced approval requirement. Human intention to review before clicking merge is weaker than the stated enforced review gate.

Prefer one approval/code-owner review for external contributions. For a solo owner, consider separate baseline and review rulesets: baseline CI/PR/force-push controls have no bypass; a clearly logged PR-only owner exception is limited to the review ruleset. It is still a bypass capability that needs explicit usage policy and audit, not independent review. Feature availability and actual owner access must be verified before applying. A second trusted reviewer is preferable.

Do not automatically merge the audit first under whatever protections happen to exist. Keep the integration and stacked maintenance work unmerged until the bootstrap/test sequence is concrete and reviewed. Any minimal bootstrap merge/exception must be explicitly described and authorized, not inferred from the absence of known controls.

Also replace unverified statements that no remote ruleset exists with the accurate statement that none was created/inspected by this work.

## Owner decisions and recommended defaults

These do not prevent the local harness corrections:
- Keep the inherited entry metadata-only; no untracking is required.
- Preserve prepared commits with a merge commit for the audit PR; squash normal small PRs.
- Prefer a supported, platform-aware hash-locked Python dependency workflow. The current constraints snapshot can document tested versions, but is not a hash-verified lock or proof of a fresh hosted installation.
- Document maintainer/release signing first; do not require signing from all contributors without testing compatibility.
- Keep the evidence archive local until publication/privacy/licensing review and explicit publication instructions.
- Visibility, a real second reviewer, current maintainer account/write permission and a private reporting/contact channel still need owner confirmation or permitted metadata verification before activation. Do not invent them.
- Assign excluded-test ownership/follow-up explicitly before calling the maintenance handoff complete; proposals may remain clearly unconfirmed.

## Next step

Add failing regressions for the gate safety and coverage paths and fix R2-1 to R2-6. Run focused harness/policy tests and inert end-to-end orchestration tests; do not repeat broad application campaigns merely to change planning/configuration. The real per-commit and clean-candidate gates remain required once commits are authorized.

Refresh hashes/curation and make a new versioned delta for later review files; do not modify existing archives. Keep generated synthetic Git fixture directories out of normal commits, while preserving canonical repro scripts and summary logs.

No commits, pushes, merges, remote settings or T8 yet. These findings concern verification and repository governance, not a reopening of T6.
