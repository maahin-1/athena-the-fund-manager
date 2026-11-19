# GitHub settings matrix (proposal)

**Every row is `planned`: nothing here has been applied, read back or exercised.** No GitHub setting was inspected and nothing was changed. No ruleset was created by this work; existing settings remain uninspected. Setting names follow GitHub's current interface from documentation and may differ in wording or availability by visibility and plan: the first job of the setup phase is to **export the effective settings** and correct this table from what is actually there. File presence is never reported as server enforcement.

Status vocabulary used in every table: `planned` → `local configuration verified` → `GitHub applied` → `behaviour verified` (or `unavailable: <reason>`). Tests that would move a row to `behaviour verified` are in [ENFORCEMENT-TESTS.md](ENFORCEMENT-TESTS.md) (test numbers in the last column).

## Owner-only decisions (nothing below can be finished without them)

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Repository visibility | public / private | decide first: rulesets, code scanning, secret scanning, dependency review and private vulnerability reporting differ by visibility and plan |
| D2 | Second reviewer | name a trusted GitHub user / none | a second reviewer makes a real independent approval possible; without one the sole-owner exception (D3) applies |
| D3 | Solo-maintainer exception (if D2 is "none") | A: two rulesets: `ci-gate` with **no bypass actors**, and `review` requiring **1 approval for everyone** with exactly one narrow bypass (the owner, **pull requests only**, labelled and exported); B: no bypass at all, nothing merges until a second reviewer exists; C (rejected): 0 required approvals | A. Zero required approvals (C) would drop review of external contributions too and is not proposed. B is the safest and blocks a sole maintainer; choose it only when D2 names someone. Describe A as *a pull-request-only owner exception, not independent review* |
| D4 | Merge method, and one pull request or two, for the first audit integration | merge commit (keeps the prepared commits) / squash (one commit); one PR with head `maintenance/repo-controls` (default: the gate then runs on what is merged) / two PRs (needs a documented, time-boxed exception to the `gate` requirement for the application PR) | merge commit, one PR, for that one change; squash for normal small PRs; linear history therefore **not** required. Nothing is merged before `main` is protected (README, bootstrap steps 0 to 5) |
| D5 | Python dependency lock approach | pip constraints file (proposed, `ci/python-constraints.txt`) / pip-tools with hashes / uv | constraints now; hashes later. DuckDB stays within `>=1.5.6,<1.6` and moves with the Rust crate |
| D6 | Signed commits and tags | not yet / maintainers / required | document signing for maintainers and releases first; test bot and contributor compatibility before requiring it |
| D7 | Private reporting channel and conduct contact | GitHub private vulnerability reporting / another verified channel | reporting must be switched on and tried before `SECURITY.md` is merged; no address has been invented |

## Rulesets and branch protection

| ID | Setting | Proposed value | Depends on | Status | Read back / verify | Test |
|---|---|---|---|---|---|---|
| R1 | Two rulesets on the default branch (`main`), both enforcement **Active** (create as *Evaluate* first if available, read the insights, then activate): **`ci-gate`** with **no bypass actors**, and **`review`** with the single narrow bypass of R13. Existing protections found by the inventory are kept; the strictest rule wins | `ci-gate`: R2, R7 to R10 and R8's required check. `review`: R3 to R6 | D1, inventory | planned | export both ruleset JSONs | 4, 12, 13 |
| R2 | Require a pull request before merging (in `ci-gate`, so it binds the owner too) | yes; no direct pushes by anyone | none | planned | try a direct push, as the owner and as a contributor, on a disposable protected branch | 4, 13 |
| R3 | Required approving reviews (in `review`) | **1, for everyone**: external contributors and collaborators with write access need an approval from someone other than the pusher of the latest commit. **Never 0** | D2 | planned | a PR from a contributor account cannot merge without the approval | 1, 3, 12 |
| R4 | Dismiss stale approvals when new commits are pushed (in `review`) | yes | R3 | planned | push to an approved PR, approval disappears | 3 |
| R5 | Require approval of the most recent reviewable push (in `review`) | yes | R3 | planned | the pusher's own approval does not count | 3 |
| R6 | Require review from code owners (in `review`) | yes, once the owner login is verified | CODEOWNERS | planned | change a path in CODEOWNERS and watch the PR wait for the owner | 5 |
| R7 | Require conversation resolution (in `ci-gate`) | yes | none | planned | an open thread blocks merge | 1 |
| R8 | Require status checks to pass (in `ci-gate`, no bypass) | exactly **`gate`** (and `dependency-review` only after it has run once), **source: GitHub Actions** so a different provider cannot report the name; "require branches to be up to date": yes | a successful observed run of `ci` (bootstrap) | planned | the required-check list shows the app; a check from another source does not satisfy it | 2, 7 |
| R9 | Block force pushes | yes | none | planned | force-push attempt on a disposable protected branch | 4 |
| R10 | Restrict deletions | yes | none | planned | delete attempt on a disposable protected branch | 4 |
| R11 | Require linear history | **no** | D4 | planned | n/a | |
| R12 | Allowed merge methods | merge commit and squash; rebase off | D4 | planned | repository settings export | |
| R13 | Bypass list | `ci-gate`: **empty, administrators included**. `review`: **one** actor, the repository owner, mode **pull requests only** (never direct pushes), only while D2 is "none"; named, dated, reviewed monthly, removed when a second reviewer exists. Every use carries the PR label `owner-exception` and a one-line statement of what no second person reviewed. No other role, app or team is listed. Where the plan does not allow a per-user bypass the nearest role is used and the matrix says which | D3 | planned | export both bypass lists and the ruleset insights (bypass events); list them in the maintenance record | 4, 12, 13 |
| R14 | Require signed commits | not yet | D6 | planned | | |
| R15 | Merge queue | off | none | planned | | |
| R16 | Tag ruleset for release tags (`v*`) | block creation, update and deletion except by maintainers | none | planned | tag update/delete attempt on a disposable tag pattern | 4 |
| R17 | Auto-delete head branches | yes | none | planned | merge a disposable PR | |
| R18 | Allow auto-merge | **off** (no dependency auto-merge) | none | planned | settings export | 8 |

## Actions and untrusted contributions

| ID | Setting | Proposed value | Status | Read back / verify | Test |
|---|---|---|---|---|---|
| A1 | Allowed actions | GitHub-owned actions only; the workflows use them by full commit SHA and `ci/workflow-policy.json` records each repository/SHA pair: the policy job fails on any other pair (a well-formed but different SHA included), local or container references, and any use of `actions/checkout` | planned (checker locally verified with the real YAML parser) | settings export; the policy job result | 6 |
| A2 | Default `GITHUB_TOKEN` permissions | **read** repository contents only; workflows declare their own minimum | planned | export; a workflow asking for write fails the policy job | 6 |
| A3 | Allow Actions to create and approve pull requests | **off** | planned | export | |
| A4 | Fork pull request workflows | **require approval for all outside collaborators** at first; review the change before approving a run (approving a run is not approving the merge) | planned | open a fork PR from a disposable account: the run waits | 6 |
| A5 | Send write tokens / secrets to workflows from forks | **off** (there are no secrets in this repository's CI at all) | planned | export | 6 |
| A6 | Self-hosted runners | **none**; hosted, disposable runners only; never the owner's machine | planned | runner list is empty | |
| A7 | Artifact and log retention | 14 days (workflow also sets `retention-days: 14`) | planned | export | |
| A8 | `pull_request_target` / `workflow_run` | not used by any workflow (policy job fails if added) | local configuration verified (checker, with a negative control) | policy job result | 6 |
| A9 | Cache use | no dependency caches in the proposed workflows | local configuration verified | file review | |
| A10 | Checkout of the protected inherited template | workflows never use `actions/checkout`; every repository-reading job starts with the access-safe checkout (sparse exclusion configured before the first checkout, token used for the fetch only); with a token the remote and the commit come from the job context only and any inherited override is refused (workflow-level and job-level control variables are also rejected by the policy job); unsupported path shapes are rejected from metadata before anything is written; the step counts protected names on disk and fails on any | local configuration verified (script run against a throwaway origin); never run on GitHub | first run's log line `protected names on disk: 0` (no name printed) | 14, 15 |
| A11 | Expressions inside scripts | none: any `${{ ... }}` inside a `run:` script, nested or not, fails the policy job; values reach scripts through `env:` | local configuration verified (checker, negative controls) | policy job result | 6 |

## Security features (availability depends on D1 and plan: verify each, do not assume)

| ID | Setting | Proposed value | Status | Read back / verify | Test |
|---|---|---|---|---|---|
| S1 | Dependency graph | on | planned | Insights → Dependency graph lists pip/cargo/npm manifests | 8 |
| S2 | Dependabot alerts | on | planned | alerts page | 8 |
| S3 | Dependabot security updates | on; critical alerts are triaged at once, not on the weekly schedule | planned | settings export | 8 |
| S4 | Dependabot version updates | `.github/dependabot.yml` (weekly, grouped, no auto-merge) once on the default branch; the first run's log must show each ecosystem parsed | planned (file locally verified) | Dependabot log per ecosystem | 8 |
| S5 | Secret scanning | on | planned | settings export | 9 |
| S6 | Push protection | on; tested only with provider-approved non-functional patterns, **never a real credential** | planned | blocked push of a test pattern | 9 |
| S7 | Private vulnerability reporting | on, and the form opened once | planned | `SECURITY.md` precondition | |
| S8 | Code scanning (CodeQL) | default setup for Python, JavaScript/TypeScript, GitHub Actions, and Rust **if the language is supported for this repository**; validate the successful scan and its actual language coverage, not just the toggle | planned | scan result lists each language analysed | |
| S9 | Dependency review on PRs | `dependency-review.yml`, not required until it has run | planned (file locally verified) | a PR adding a known-vulnerable package fails it | 8 |
| S10 | Alert metadata review | read counts and rule names only; never print or retrieve a secret value | planned | procedure note | |

## Access

| ID | Setting | Proposed value | Status |
|---|---|---|---|
| X1 | Collaborators, teams and roles | least privilege; contributors work from forks; nobody gets write access by default | planned |
| X2 | GitHub Apps and OAuth app grants | review the list; remove what is unused; none may have admin | planned |
| X3 | Two-factor authentication for maintainers | required for the account; strong method preferred | planned (owner action) |
| X4 | Deploy keys, webhooks, Pages, environments, releases | none until a release process is deliberately created; a push or merge is not deployment approval | planned |
| X5 | Review access on departures; incident handling | procedure in [README.md](README.md#incident-handling) | planned |

## Files in the repository that support these settings

| File | Role | Local check |
|---|---|---|
| `.github/workflows/ci.yml` | the `gate` aggregate and the jobs behind it | static policy: **ok** |
| `.github/workflows/dependency-review.yml` | PR dependency review | static policy: **ok** |
| `.github/dependabot.yml` | four ecosystems, weekly, grouped, no auto-merge | structure: **ok** |
| `ci/workflow-policy.json`, `scripts/ci/safe_checkout.sh`, `scripts/ci/check_workflows.py` | the recorded action pins, the access-safe checkout and the checker that enforces both | 105 tests: **ok** |
| `.github/CODEOWNERS` | review requests for sensitive paths (enforced only if R6 is on) | well-formed, paths exist: **ok** |
| `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/*` | contribution contract | forms parse: **ok** |
| `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` | contribution, reporting, conduct | links resolve: **ok** |
