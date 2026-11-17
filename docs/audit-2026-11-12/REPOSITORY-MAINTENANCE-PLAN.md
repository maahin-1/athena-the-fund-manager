# Repository maintenance and secure collaboration plan

Prepared: 17 November 2026. Status: **planned; not configured or verified on GitHub**.

This extends the backup, change inventory and Git checkpoint phase after T6 closure. Treat repository governance as a prerequisite for routine external contributions and merges, rather than postponing it until all later workpackages finish. T8 remains paused during preparation.

## 1. Verified starting point and boundaries

Observed in the current checkout:
- An MIT LICENSE exists. Python, Rust and web manifests also declare MIT.
- Python, a Rust terminal and a TypeScript/Vite web client need separate checks.
- terminal/Cargo.lock and terminal/web/package-lock.json are tracked.
- scripts/check_all.py is a manual gate; it explicitly says there is no CI service. It runs Python/Rust/web tests and an indicator golden check, but not the browser suite.
- No tracked or pending files were found in the inspected .github paths, or at the checked CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md and CODEOWNERS locations.
- No standard Python lock was found among the checked tracked lockfile names. Confirm against the fresh inventory before selecting a tool.

GitHub rulesets, permissions, installed apps, security features, repository visibility and deployment integrations have **not** been inspected. File presence will never be reported as proof that a GitHub setting is enforced.

All existing restrictions stand: no access to protected environment files or credential values, production runtime data, live models or brokers. Preserve uncommitted work. This plan does not execute commits, pushes, public messages, releases or remote settings changes.

## 2. Execution order

1. Create and verify the allowlisted, restorable backup specified in WP07-T6-CLOSURE-2026-11-17.md; capture a manifest and source hashes.
2. Recount and classify changes. Keep needed source/test/docs and evidence; keep generated build/cache output out of commits. Preserve historical evidence, including invalid/red runs.
3. Isolate the reported flaky oversized-body test correction. Preserve exact size-policy assertions with deterministic service/router coverage.
4. Prepare dependency-ordered implementation commits and separate repository-maintenance commits on a temporary integration branch. Use codex/ for Codex-created branches; document feature/, fix/, docs/ or chore/ for contributors. Do not create an unnecessary permanent develop branch.
5. Build and validate CI/contribution files against the integration candidate. Plan the bootstrap carefully: required check names must have an observed successful run before making them required.
6. Review the concrete branch, workflow and settings proposals; then apply the server-side controls in the authorized setup phase. Read them back and perform negative enforcement tests.
7. Only then merge reviewed changes. Release/deployment remains separate. A public launch needs its own readiness review.

No setting should be enabled blindly if it would strand the sole maintainer or require a check that does not yet exist. Record any tightly scoped bootstrap exception and remove it afterward.

## 3. Contribution contract and ownership

Prepare:
- CONTRIBUTING.md: setup without credential templates, isolated test commands, supported runtimes, small PR expectations, regression evidence, documentation updates and how to interpret skips/exclusions.
- SECURITY.md: supported versions and private reporting instructions using an actually verified channel. Do not invent a contact address or request credentials/private market datasets in reports.
- CODE_OF_CONDUCT.md and maintainer escalation responsibilities.
- .github/PULL_REQUEST_TEMPLATE.md: problem, final behavior, tests, exclusions, compatibility/data-schema effects and rollback.
- .github/ISSUE_TEMPLATE/: concise bug and feature forms; minimal synthetic repros, expected/actual behavior and versions.
- .github/CODEOWNERS: verified maintainer identities, catch-all ownership and explicit sensitive paths.

Sensitive ownership covers .github itself (including CODEOWNERS), dependency manifests/locks, install/build scripts, access guards, broker/execution, research/backtest accounting, financial storage/migrations and process/lock ownership. Audit policy or approved test exclusions must receive maintainer review.

Do not assume CODEOWNERS alone enforces approval; its review requirement must also be enabled in repository rules. [GitHub code-owner enforcement](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners).

Retain MIT and accurate upstream attribution. Verify that sample fixtures, data and exports intended for redistribution are permitted. A CLA, mandatory signoff or new license is not introduced by default.

## 4. Main/release branch rules

Propose an active ruleset for main:
- PRs required; no routine direct pushes.
- Required CI checks, resolved review conversations and an independent maintainer approval, including code-owner review for protected paths.
- Refresh approval after relevant new commits; require review of the latest reviewable push.
- Block force pushes and deletion; keep bypass permissions minimal and auditable.
- Define how the tested candidate includes the latest base branch; require an updated branch initially. A merge queue is optional if concurrency later justifies it.
- Start without dependency auto-merge. Bot PRs receive the same applicable checks and review.
- Protect release tags from casual replacement/deletion.

Prefer squash merges for normal small contributions. Preserve meaningful prepared commits for the initial audit integration where needed; select the permitted merge policy accordingly. Require linear history only if compatible with that choice. Do not rewrite shared history to make it look tidier.

Rules/features depend on repository visibility and subscription. Export the actual effective rules rather than assuming availability. [Rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets), [available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

### Solo-maintainer constraint

An author cannot provide an independent approval of their own work. Verify the maintainer roster before activation. Prefer a second trusted reviewer for owner-authored PRs. If none exists, explicitly document any narrowly scoped PR-only owner exception, its reason and retained checks; never describe it as independent review or leave a broad silent admin bypass. External contributions still require maintainer review before merge.

Verified signatures attest to signing identity, not correctness or absence of malicious code. Start with documented signing for maintainers/releases; test bot and contributor compatibility before requiring signed commits globally. No key material is inspected or generated during planning. [Commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification).

## 5. CI tailored to this repository

Prepare stable, uniquely named jobs and one always-reported aggregate gate. Check these job results before configuring required checks; avoid path filters that leave the aggregate pending or turn omitted tests into a green gate.

| Check | Required evidence |
| --- | --- |
| Python | Declared toolchain, reproducible dependency resolution, synthetic offline pytest results, collected IDs and explicit skip/exclusion manifest |
| Rust | Pinned toolchain, cargo test --locked with the real intended test capabilities available; compile/load failures are failures |
| Web | npm ci from the lockfile, TypeScript check, Vitest results and production build |
| Contracts | Formula golden checks and the Python/Rust snapshot contract when relevant |
| Platform behavior | Windows coverage for PowerShell/process/lock paths; Linux coverage for claimed cross-platform behavior |
| Browser/demo | Isolated sample stores and blocked external transports; gate relevant UI changes when this harness is ready |
| Workflow/security | Workflow syntax/security checks, static analysis and dependency review according to verified support |

Use targeted checks per coherent commit and a qualified broad run on the final candidate. Benchmark runner time before expanding an expensive full matrix: bundled DuckDB needs C++ build tooling and meaningful timeouts.

Preflight must fail when required Python/sh/compiler tools are absent. Fix silent early-return tests or explicitly classify them; never count missing test bodies as verified. The existing nine Python exclusions need a named, reviewed manifest with reasons, ownership and follow-up dates. Record current skips rather than copying old totals. Zero tests, failed suite loading, unexplained count drift and missing reports fail the gate. Stabilize the oversized HTTP test instead of retrying until green.

Select a Python lock approach while preserving the existing DuckDB compatibility constraint. Keep Rust and npm locks synchronized with their manifests. Do not claim coverage or introduce arbitrary coverage thresholds without measuring a baseline.

CI dependency setup may download verified public dependencies. Application tests must not contact live providers, brokers, data-refresh routes or the owner's stores. Record what runtime network isolation actually enforces; a monkeypatch tripwire is not an OS sandbox.

## 6. Fork/untrusted-code safety

Use disposable hosted runners for external PRs, never the owner's laptop or a production-connected self-hosted runner. Standard PR jobs get minimum token permissions, no application/deployment secrets and no write/deploy capability. Disable persisted checkout credentials. Pin external actions to reviewed full commit SHAs and maintain those pins.

Do not execute untrusted PR content through privileged pull_request_target/workflow_run patterns, interpolate PR titles/branch names into shell code, or promote fork-controlled caches/artifacts into privileged release jobs. Code-owner review must cover workflow and install-hook changes.

Require manual workflow-run approval for outside collaborators initially, reviewing the changes before approval. Approval to run CI is distinct from approving a merge. [Fork run approval](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/approve-runs-from-forks), [repository Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository), [Actions secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

Local hooks remain convenience checks, not an enforcement boundary. No LLM review or agent may substitute for required human approval or acquire write/merge authority by default.

## 7. Dependabot and dependency maintenance

Prepare .github/dependabot.yml for the actual manifests:

| Ecosystem | Directory |
| --- | --- |
| Python | /, matching the selected supported lock workflow |
| Cargo | /terminal |
| npm | /terminal/web |
| GitHub Actions | / |

Start weekly version-update PRs with a modest open-PR cap per ecosystem. Group compatible minor/patch updates where useful; keep major and compatibility-sensitive upgrades separate. Security alerts receive prompt severity-based triage; do not delay them mechanically behind a routine version-update schedule.

Require CI/review for updates. Do not broadly ignore DuckDB, Vite or other packages to silence failures; preserve known constraints and plan coordinated Python/Rust compatibility changes. Any advisory exception needs rationale, an owner and an expiry/review date. Dependabot PRs are not inherently safe merely because a bot created them.

Verify ecosystem behavior after configuration is on the default branch. Configuring the YAML is not proof that every transitive dependency is visible. [Dependabot options](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference), [dependency graph support](https://docs.github.com/en/code-security/reference/supply-chain-security/dependency-graph-supported-package-ecosystems).

## 8. Security and access controls

In the authorized GitHub setup phase:
- Verify dependency graph, Dependabot alerts/security updates, secret scanning and push protection according to feature availability.
- Enable supported static analysis for Python, TypeScript/JavaScript and Rust; validate the actual language coverage and successful scan, not just an enabled toggle.
- Use dependency/advisory/license review with documented exception handling.
- Restrict collaborator and GitHub App permissions to their actual work. Encourage strong maintainer authentication; review access on departures.
- Keep deployment credentials out of PR jobs. Future publishing uses a distinct protected release workflow and short-lived identity where supported.
- Review alert metadata without retrieving or printing secret values. Do not run broad local scanners over protected files, production data or history to bypass the access policy.
- Document incident handling: contain automation, revoke affected access, investigate permitted redacted evidence, patch and communicate through authorized channels. History rewriting and credential changes require separately scoped handling.

[Security feature configuration](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository), [CodeQL capabilities](https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-code-scanning).

## 9. Git, releases and recovery

Document branch naming, small coherent commits, issue references where relevant and a clean-checkout local verification command. Hooks may help but do not replace server rules. Prevent runtime stores, credentials, build outputs and inappropriate large datasets from entering future contributions using metadata/path checks and reviewed ignore policy. Do not delete existing audit evidence or rewrite history automatically.

Releases should identify a tested commit, dependency/toolchain versions, known limitations and artifact hashes. Keep release/deployment disabled until intentionally configured; a push or merge is not approval to deploy. Plan signed release tags, changelog entries and a tested revert procedure. Database/data changes retain separate backup/migration/rollback rules.

The working-tree backup protects the current uncommitted project; a Git mirror protects committed refs. Neither substitutes for the other. Record where off-repository evidence is kept and verify restoration. GitHub issues/settings are separate from Git content and need their own export/runbook where warranted.

## 10. Acceptance: verify behavior, not file presence

Before calling the setup complete, save evidence from disposable branches/PRs in the authorized verification phase:
1. A valid synthetic contribution runs the declared checks and becomes eligible only after required review.
2. A deliberately failing test blocks merge.
3. A new reviewable push invalidates the expected approval and reruns checks.
4. A low-privilege contributor cannot push directly to main, force-push or delete it; verify safely using a disposable protected target before any main-branch attempt.
5. A workflow/ownership-policy change requires the intended owner review.
6. A fork workflow awaits the configured approval and runs without privileged credentials.
7. Required check results correspond to the exact latest candidate and expected CI provider.
8. Dependabot detects the intended manifest/lock ecosystems, and its PRs cannot bypass the gate.
9. A known flaky test cannot silently become an exclusion or a successful retry.
10. A clean restore/check-out reproduces the declared gate.

Use synthetic or provider-approved nonfunctional secret test patterns only if needed; never submit a real credential to test push protection. Do not post artificial PRs/issues or alter server settings before the concrete verification actions are authorized.

For each control record: planned, local configuration verified, GitHub applied, behavior verified, or unavailable with reason. Export effective rules, workflow run URLs, settings evidence, date and responsible maintainer. Bootstrap and solo-owner exceptions must remain visible.

## 11. Maintenance cadence and handoff

- Weekly: review dependency/security PRs and failed checks; act on critical alerts promptly rather than waiting for the weekly review.
- Monthly: review collaborators/apps, action pins, ignore/advisory exceptions, CI cost, skipped tests and flaky-test backlog.
- Per release: review change scope, validate the clean candidate, record artifacts and rollback.
- Periodically: rehearse recovery and recheck negative merge/fork tests after policy changes.

Assign actual owners and achievable response targets before promising an SLA. Record incident and maintenance work separately from application features.

Next deliverable from the implementation agent: verified backup manifest, current change inventory, commit grouping, proposed maintenance files/workflows, a GitHub settings matrix and validation plan. No application refactor, automatic commit/push or deployment is needed to make that proposal reviewable.
