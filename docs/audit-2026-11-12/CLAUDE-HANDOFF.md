# Claude implementation handoff: Athena research reliability

Prepared 12 November 2026. Workspace: `D:\projects\learn-project\athena-the-fund-manager`. Audited application baseline: `37f1fc1`. This document commissions staged implementation of the audit recommendations, not another general review. Revalidate source before editing: the audit describes a point in time.

## Objective and boundaries

Make Athena a reproducible, evidence-first Indian equity research desk. Fix incorrect scoring, evidence/date boundaries, misleading verification, and operational failure behavior before expanding features. Keep execution hardening as a distinct track; offline fixes are included, real-account operation is not.

The project already has substantial deterministic analytics, filing ingestion, charts, a Rust/TypeScript terminal and bounded agent components. Preserve that work. The current research results do not establish LLM predictive skill. Implementation completion must not be described as proof of profitability or professional analyst accuracy.

### Non-negotiable access constraints

- Read root `AGENTS.md` first. Environment files are off limits: `.env`, `.env.*` including examples, `*.env`, `.envrc`, at any depth. Do not inspect exported credential values, bypass the hook, or print secrets through diagnostics.
- Credentials were exhausted at handoff. Use injected fakes and stored non-secret fixtures; make no live LLM/provider calls, login probes, broker requests, order sends, exchange refreshes or background supervisor runs. Public documentation/repository reads are allowed.
- `.claude/settings.json` locally registers `scripts/block_env_access.py`; see [guard details](env-guard.md). Check the host's hook registration without opening credential files. The hook is a common-path guard, not an OS sandbox.
- Inspect `git status --short`; preserve existing user changes. At handoff `CLAUDE.md`, `examples/`, `r.txt`, audit artifacts, `AGENTS.md` and guard additions were untracked. Do not replace root `CLAUDE.md` with this handoff.
- Work on temporary databases and sample copies. This handoff authorizes migration tooling, copied-store dry runs and rollback verification. Applying a migration to production `.athena` data requires separate explicit authorization; a successful dry run is not that authorization. Preserve existing research artifacts and historical scores; new semantics get new versions/results.

## Read in this order

1. This document, root `AGENTS.md`, [verification record](verification.md), then [implementation work packages](IMPLEMENTATION-WORKPACKAGES.md).
2. Read the section of [REPORT.md](REPORT.md) covering the current batch. Load only the relevant component report: [agent](agent-review.md), [quant/data](quant-review.md), or [terminal/broker/operations](terminal-review.md).
3. Open the owning source and existing tests listed in the work package. Audit line numbers are historical pointers, not patch coordinates.
4. For cross-project design ideas, use [peer comparison](peer-comparison.md) and the source links in the work packages. Treat peer patterns as references, not validated drop-in components.

### Architecture facts that prevent scope mistakes

- The ordinary stock path is resolver → deterministic packets → sequential specialist calls → blend. Stored verdict generation adds calibration and an optional shadow council. These entry points do not currently have identical behavior.
- The default equity route does not run every existing specialist definition. Flow/ownership capabilities exist outside that universal route.
- The terminal IPO Analyse job is checks-only and makes zero model calls. Opt-in forensic assembly is a separate bounded tool/action system.
- Code-authored accounting invariants are stronger evidence than model prose. Preserve their ownership and the council's shadow-only boundary.
- Historical signal scores, daily strategy simulation, verdict settlement and paper/live broker accounting are separate paths. Do not imply they share one portfolio ledger today.
- The terminal has no order endpoint; the wider repository does have gated live adapters. Preserve that separation.
- Graphify is a navigation aid containing inferred/document edges, not proof that a route runs or a date gate exists.

## Execution order

| Batch | Scope | Completion gate |
|---|---|---|
| **1 — Start here** | WP01: tied-vote crash, malformed council citations, supervisor terminate/wait and startup build exits | Each finding reproduced by a meaningful regression; fixes pass focused tests; no live/model/account dependency |
| **2 — Immediate correctness** | WP02 timing/freshness/publication gates; WP03 offline broker containment | No prospective score predates the decision; future/unknown evidence cannot enter strict history; kill/loss behavior is explicit and tested |
| **3 — Data foundations** | WP04 immutable financial vintages and dated valuation/cohort inputs | Rerunnable migration on copies; current views remain correct; historical originals recover where available and gaps remain explicit |
| **4 — Evidence and reproducibility** | WP05 typed claims/source support; WP06 budget/attempt/run manifests | Wrong sign/metric/period and irrelevant support cannot obtain strong verified status; concurrent/corrupt budgets fail safely; outputs attributable |
| **5 — Desk usability** | WP07 quote/snapshot provenance and jobs; WP08 backtest accounting; WP09 credential-free demo/export/docs | Mixed-age output is visible; bounded/cancellable work; consistent accounting; reproducible demo and source-linked export |
| **6 — Evaluation and optional expansion** | WP10 human-review and prospective evaluation tooling; later richer portfolio simulator | Infrastructure tested offline; human/live-dependent outcomes remain pending; added agent complexity must earn its cost |

Execute one bounded batch at a time. Split large work packages into coherent patches. Reordering within a batch is fine when justified by a dependency; record the reason. Implementation does not require launching real services or obtaining credentials.

## First session: concrete instructions

1. Establish the current diff and read the four WP01 owners/tests. Confirm each mechanism is still present.
2. Add focused failing regressions. Prefer fake processes, synthetic council replies and simple tie cases over wall-clock sleeps or network servers.
3. Fix Q6, A4, T4 and T7 with small compatible changes. Preserve existing behavior outside the documented correction. Update comments that describe superseded behavior.
4. Run affected tests; run broader checks only for changed boundaries. For PowerShell startup, use stub native executables or an isolated harness to prove a nonzero build prevents launch; inspecting the script alone is not runtime validation.
5. Write `IMPLEMENTATION-STATUS.md` in this directory with finding IDs, changed files, regression evidence, exact commands/results, exclusions and the next unfinished batch. Leave a coherent reviewable diff.

If a finding is already fixed, show the owning code and a passing regression, mark it verified fixed, and continue. If a genuine product decision is needed, state the smallest decision and continue independent work. Missing credentials are not a blocker for the offline batches.

## Definition of done and reporting

For every finding, record: reproduced/current status → implementation → regression evidence → compatibility/data impact → remaining limitation. Use statuses `open`, `in_progress`, `fixed_offline_verified`, `needs_human_review`, `needs_live_validation`, or `deferred_with_reason`. Do not turn an unavailable validation into passed.

Preserve source reporting period, publication/availability time, ingestion/observation time, decision completion time and execution time as different concepts. Default policy details and migration constraints are in the work packages.

The previous audit observed 2,555 scoped Python passes, 74 live skips, 895 web passes and a successful web build. Nine Python modules were excluded, and Rust socket tests were sandbox-blocked. These are historical reference results, not today's baseline or an instruction to permanently skip failing tests. Use [verification.md](verification.md) for exact scope and reproducible temporary-directory handling.

At each session end, report what is fixed, what remains, exact tests, and whether a claim requires later human or live validation. Never silently rescore old studies or declare execution readiness after fixture-only tests.

## Start prompt

Copy [CLAUDE-START-PROMPT.md](CLAUDE-START-PROMPT.md) into Claude Code from the repository root. It starts Batch 1 and points back here for continuation. No application implementation was performed while preparing this handoff.
