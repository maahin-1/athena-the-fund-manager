# Repository maintenance and secure collaboration: proposals (17 November 2026, revision 3)

Written for the repository owner and the reviewer who set the scope in [REPOSITORY-MAINTENANCE-PLAN.md](../REPOSITORY-MAINTENANCE-PLAN.md). **Everything here is a local proposal.** Nothing was added to a branch, pushed, opened as a PR or issue, or configured on GitHub; no setting was inspected; the workflows have never run. A file in this folder is not evidence that anything is enforced.

Revision 3 changes (review 3, R1 and R2): authenticated hosted checkout takes the remote and the commit from the job context only and refuses inherited overrides, the workflow checker rejects workflow-level and job-level variables that steer the checkout, and unsupported path shapes (line breaks, trailing spaces and dots, backslashes, a protected name as a directory) are rejected from metadata before anything is checked out; see "Access-safe checkout on hosted runners" below.

Revision 2 changes: workflows no longer use `actions/checkout` (it would write the inherited protected template to the runner; an access-safe checkout replaces it); the workflow checker validates every action reference against its recorded repository/SHA pair and rejects any expression inside a script; the review model no longer proposes zero required approvals; the bootstrap order no longer starts by merging the audit under unknown protections.

## What is in the proposal

| Path | What it is |
|---|---|
| `files/.github/workflows/ci.yml` | CI: `policy`, `python`, `rust`, `web` and an always-reporting aggregate `gate`; every repository-reading job starts with the access-safe checkout |
| `files/.github/workflows/dependency-review.yml` | PR dependency review (needs the dependency graph); reads no repository file, so it has no checkout |
| `files/.github/dependabot.yml` | pip, cargo, npm, github-actions; weekly; grouped; DuckDB in its own group; no auto-merge, no ignore |
| `files/.github/CODEOWNERS` | review requests for sensitive paths (enforced only with ruleset row R6) |
| `files/.github/PULL_REQUEST_TEMPLATE.md`, `files/.github/ISSUE_TEMPLATE/*` | contribution contract and issue forms |
| `files/CONTRIBUTING.md`, `files/SECURITY.md`, `files/CODE_OF_CONDUCT.md` | contribution, private reporting (no invented address), conduct |
| `files/ci/python-constraints.txt`, `files/ci/expected-counts.json`, `files/ci/inherited-protected.sha256` | constraints from the tested environment; exact counts the gates must reproduce; the one inherited protected entry as a hash only |
| `files/ci/workflow-policy.json` | the only approved action repositories with their recorded commit SHAs, the checkout step's name and script, the environment-variable names that may not steer the checkout, and the jobs that need no checkout |
| `files/scripts/ci/safe_checkout.sh` | the access-safe checkout (sparse-checkout exclusion configured before the first checkout), embedded verbatim in each job |
| `files/scripts/ci/check_repo_policy.py`, `check_workflows.py`, `files/tests/test_ci_repo_policy.py` | CI policy checks and their 105 tests |
| [SETTINGS-MATRIX.md](SETTINGS-MATRIX.md) | server settings, all `planned`, with the owner-only decisions D1 to D7 |
| [ENFORCEMENT-TESTS.md](ENFORCEMENT-TESTS.md) | the 15 negative and behaviour tests, to run on disposable branches in the authorized phase |
| `checks/check_proposal.py` | local static checks of the whole proposal |
| `runs/` | the output of those local checks |

`files/` mirrors the repository root so that placing a file is a move to the same relative path. The copies are inert where they sit (GitHub reads workflows, ownership and templates only from the root).

## What was checked locally, and what it does and does not show

| Check | Result |
|---|---|
| Workflow policy with the real YAML parser (PyYAML 6.0.3): read-only permissions, timeouts, no privileged trigger or secrets, **every action reference equal to its recorded repository/SHA pair**, **no expression of any kind inside a run script**, first step is the access-safe checkout equal to `scripts/ci/safe_checkout.sh` | **ok** (both workflows) |
| The same checker on deliberately defective inputs: a well-formed but wrong SHA beside a correct pin, a SHA that is right for another repository, a reusable workflow at job level, nested `format()`/`toJSON()`/`contains()` expressions, a changed checkout script, `actions/checkout`, the token passed to another step, and (review 3) `REMOTE_URL`, `CHECKOUT_SHA` or `GIT_CONFIG_COUNT` in the workflow-level or a job-level `env:`, an `env:` that is an expression, a job `container:`, and a script edited to honour overrides while a token is present | **each rejected** (15 defects in the negative control) |
| The checkout script run for real (Git Bash) against a throwaway origin whose commit holds protected-name entries created in its index only (also with pattern characters, spaces and other letter cases in the names) | **0 protected names on disk, entries kept as skip-worktree metadata, status clean**; a failed fetch fails the step |
| The checkout script run for real with a recording stand-in for git, with a token present | an inherited remote, revision or fixture-mode override is refused before any git command; remote and commit come from the job context only; inherited `GIT_*` dropped; a fetched commit that differs from the requested one stops it before anything is checked out; the token never appears in the output |
| The checkout script against twelve unsupported path shapes (trailing space or dot, line feed, carriage return, tab, other control byte, backslash, leading space, a protected name with a trailing space or a line feed or as a directory) | **each rejected before anything is written** (HEAD still unborn, nothing on disk); protected names withheld from the message |
| Dependabot covers the four ecosystems with existing directories and manifests; no auto-merge; no blanket ignore | **ok** |
| CODEOWNERS well formed; every path pattern matches something; every sensitive path from the plan is listed | **ok** |
| Everything the CI calls exists (verify scripts, `ci/` files, `scripts/ci/`) | **ok** |
| `tests/test_ci_repo_policy.py` | **105 passed**, 0 skipped (it was 17 before review 3) |
| The CI's gates themselves | executed locally on a stand-in candidate (see `GIT-PREPARATION.md`); **not executed on GitHub** |
| Anything on GitHub | **nothing verified**: no run, no ruleset created or inspected, no Dependabot log, no CodeQL scan, no fork test; the access-safe checkout step has never fetched from GitHub (it needs git 2.35 or newer and the runner's fetch of a commit SHA) |

Limits: PyYAML was installed into a scratch folder only (not into the project environment); `actionlint` and GitHub's own validation were not available. The workflows are written for GitHub-hosted Windows and Ubuntu runners and may need small fixes on first run (runner PowerShell and Git Bash differences are the likely ones). A Dependabot update of an action changes a SHA and therefore fails the policy job until `ci/workflow-policy.json` is updated in the same pull request: that is intended (the owner reviews every action change) but it is friction.

## Review model (applies to everyone)

Zero required approvals is **not** proposed. It would remove review of external contributions as well as owner self-review, and a protection that only the owner can satisfy protects nothing. The proposal is two rulesets on `main` (details in [SETTINGS-MATRIX.md](SETTINGS-MATRIX.md), rows R1 to R3 and R13):

* **Ruleset `ci-gate` (no bypass actors at all, administrators included):** pull request required (no direct push), required status check `gate` from GitHub Actions on the latest head, conversation resolution, no force push, no deletion. Nobody can merge a change that the baseline CI has not passed.
* **Ruleset `review` (one narrow exception):** one approving review required from someone other than the pusher of the latest commit, stale approvals dismissed, code-owner review required. Everyone, including collaborators with write access and every external contributor, needs that approval. The only bypass is the repository owner, **limited to pull requests** (never direct pushes), and it does not touch `ci-gate`.

The owner exception exists only because a sole maintainer cannot approve their own pull request. It is narrow and auditable: one actor; pull-request-only; every use carries the label `owner-exception` and a one-line statement of what no second person reviewed; the bypass list and the ruleset insights are exported and reviewed monthly and whenever D2 changes; it is deleted the day a second trusted reviewer exists (D2). Residual risk, stated plainly: while the exception is in use the owner's own changes to workflows, CODEOWNERS and the policy files are not independently reviewed; what still applies to them is the `ci-gate` ruleset, the policy checks inside CI, the required label and the monthly export.

## Bootstrap sequence (required order; nothing in it depends on the current server settings being known)

0. **Inventory, read-only, no change.** Export the effective settings that exist today: branch protection and rulesets, collaborator roles, Actions settings, installed apps, secret-scanning and Dependabot state. Record them in the maintenance record. If anything found is not understood, or conflicts with the rows of SETTINGS-MATRIX.md, **stop and ask**. Existing protections are never removed by this plan; the new rulesets are added beside them and the strictest rule wins.
1. **Owner decisions** D1 to D7 (visibility first: it decides which features exist).
2. **Protect `main` before anything is merged.** Create `review` and `ci-gate`. If GitHub will not accept the name `gate` as a required check before it has ever reported, create `ci-gate` without the status-check rule first (it still requires a pull request and blocks force pushes and deletion), and add the check as soon as step 4 has shown it passing and **before the merge in step 5**. `main` is never unprotected at any point.
3. **Push the prepared branches** `integration/audit-2026-11-12` (P00 to E13) and `maintenance/repo-controls` (M01 to M05, stacked on the integration tip). Before pushing, run `final_gate.ps1` on each tip from a clean access-safe candidate (commands in `GIT-PREPARATION.md`): integration against the original baseline, maintenance against the integration tip.
4. **Open ONE pull request** with head `maintenance/repo-controls` and base `main`. Reason: the gate only exists in the maintenance commits, so a pull request holding the application commits alone would have nothing to run, and merging it unchecked is what this order avoids. The commits stay separate (application commits first, maintenance commits last) and are reviewed commit by commit; `ci` runs on the pull request. Read the run: fix, repeat. **A check is never made required before it has a successful observed run** (otherwise the gate can strand the only maintainer). If the owner prefers two pull requests (D4), the application pull request needs a documented, time-boxed exception to the `gate` requirement; that is not recommended.
5. **Merge** with the merge commit method (D4, so the prepared commits survive), after the required approval. If no second reviewer exists, the owner exception is used once and recorded (label, statement, export).
6. **Turn on the security features** (S1 to S8) and read each back. Add `dependency-review` as required only after it has run once.
7. **Run the enforcement tests** (ENFORCEMENT-TESTS.md) on disposable branches, including the contributor test that proves the owner exception does not apply to anyone else, then the clean-clone gate, and record every control's status.
8. Releases and deployment stay disabled until deliberately designed. A push or a merge is never approval to deploy.

## Access-safe checkout on hosted runners

The baseline commit tracks one protected template, so an ordinary `actions/checkout` would write it to the runner's disk. The workflows therefore replace that action with one step, identical in every job and verified by the policy job to equal `scripts/ci/safe_checkout.sh`: it initializes an empty repository, fetches the commit, lists the tracked names (names only), writes a non-cone sparse-checkout file whose exclusion entries cover every protected name (any letter case), and only then checks out. The entry stays in the index as skip-worktree metadata, exactly as in `verify/build_candidate.ps1`; the step then counts protected names on disk and fails the job if there is one. The token is used for the fetch only, is not stored and is never printed.

Three rules were added in revision 3 (review 3, R1 and R2):

* **No inherited remote or revision override while a token is present.** In authenticated hosted operation the remote (`https://github.com/<GITHUB_REPOSITORY>.git`, with the repository checked to be a plain owner/name) and the commit (`GITHUB_SHA`, checked to be a full lower-case 40-hex SHA) come from the job context only. `REMOTE_URL`, `CHECKOUT_SHA` and `ATHENA_CHECKOUT_TOKENLESS` set together with a token make the step refuse before it runs git at all; every inherited `GIT_*` variable (for example `GIT_CONFIG_COUNT`, which can rewrite the remote) is dropped and only its name is reported. After the fetch the commit must equal the requested one, and again after the checkout. The two override variables exist for local fixtures and are honoured only in the explicit tokenless mode (`ATHENA_CHECKOUT_TOKENLESS=1` and no token); with a token that mode is refused too. The workflow checker rejects any workflow-level or job-level `env:` that defines one of these controls (or another variable that can steer git, the shell or a proxy; the list is `checkout_control_env` in `ci/workflow-policy.json`), an `env:` that is an expression, and a job `container:`.
* **Unsupported path shapes are rejected from metadata before anything is written.** After the fetch the step reads the commit's path list and refuses, before the sparse file is written or any checkout starts, a path with a control character (a line break included), a backslash, a component that begins with a space or ends with a space or a dot, or a protected name used as a directory. The failure message withholds protected names. The escape that turns a name into a sparse pattern was also corrected: it did not protect a trailing space (Git strips unescaped trailing spaces from a pattern, so the exclusion selected the include-all pattern for such a name); it now escapes a trailing space. It also now spells its backslash as `\x5c`, so the line means the same whether bash reads it from a file or from an argument that Windows has already unescaped (the reviewer's probe passes it as an argument). Brackets, stars, question marks and backslashes were escaped correctly before. The on-disk audit after the checkout is a second line of defence, not the first.
* **The local builder needs no such guard**: `verify/build_candidate.ps1` refuses any protected entry that differs from the baseline's single inherited one, so an unusual shape cannot reach its exclusion list.

Limits: the step has never run on GitHub; git 2.35 or newer is required; the job after it compares the tracked entry with the recorded hash in `ci/inherited-protected.sha256` through `check_repo_policy.py`. Git for Windows refuses `*` and `?` in any path, so a tree that holds such a name cannot be checked out on a Windows runner at all (it fails safely, before any file is written).

## Incident handling

Contain automation first (disable the workflow or revoke the token); revoke affected access; investigate using permitted, redacted evidence (alert metadata, run logs) and never retrieve or print a secret value; patch; communicate through authorized channels. A committed credential is treated as compromised and revoked before anything else. Rewriting history or rotating credentials is a separate, explicitly authorized action, never automatic.

## Open decisions (owner only)

See [SETTINGS-MATRIX.md](SETTINGS-MATRIX.md): D1 visibility, D2 second reviewer, D3 solo-maintainer exception (default: the narrow, labelled, pull-request-only owner bypass of `review` only), D4 merge method and single versus two pull requests, D5 Python lock approach, D6 signing, D7 reporting channel and conduct contact. Also: confirm that `@maahin-1` (taken from the repository's remote URL) is the maintainer's GitHub login with write access, before CODEOWNERS is relied on.

## Not done

No file was added to the repository root. No workflow, ruleset, Dependabot, CodeQL, secret-scanning or fork test was run on GitHub. No PR or issue was created. No hook or personal `.claude/settings.json` content is part of any public file; if hooks are to be part of onboarding, document a sanitized installation separately and describe it as a convenience, not as server enforcement.
