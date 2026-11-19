Work in `D:\projects\learn-project\athena-the-fund-manager`.

Implement the Athena audit recommendations in staged, tested batches. Begin now with Batch 1; this is implementation work, not a request for another general architecture review.

First read:
1. Root `AGENTS.md`.
2. `docs/audit-2026-11-12/CLAUDE-HANDOFF.md`.
3. `docs/audit-2026-11-12/IMPLEMENTATION-WORKPACKAGES.md`, focusing on WP01.
4. `docs/audit-2026-11-12/verification.md` and the relevant source/tests.

The audit baseline was `37f1fc1`; inspect current source and `git status --short` before editing. Preserve existing changes, untracked files and historical reports. Leave root `CLAUDE.md` intact.

Respect the existing environment-file prohibition: do not read, grep, modify, create, delete or print `.env`, `.env.*` including examples, `*.env` or `.envrc`, or inspect exported credential values. Use named source reads and the exclusion globs in AGENTS.md. Do not bypass the existing guard.

Credentials were exhausted. Make no live LLM calls, provider probes, broker/login/order requests, exchange refreshes or background supervisor runs. Use synthetic data and injected fakes. Public documentation reads are fine. Implement migration tools and copied-store dry runs only; production `.athena` migration requires separate explicit authorization. Do not rescore or overwrite old research results silently.

For the first batch, reproduce and fix:
- **Q6:** tied bullish/bearish PIT repeats divide by zero when the chosen neutral signal has no confidence row.
- **A4:** council citation arrays accept missing evidence and malformed types; invalid council results must preserve the blend. Keep council shadow-only and existing rating/conviction limits.
- **T4:** supervisor timeout terminates a child but releases its writer lock before confirmed exit. Use bounded terminate/wait/kill/wait behavior.
- **T7:** PowerShell startup can continue after failed native builds. Prove a failed npm/cargo stub prevents launch without running the live terminal.

For each, confirm the current mechanism, add a meaningful failing regression, implement the smallest coherent correction and run focused offline checks. If already fixed, demonstrate it with source and regression evidence rather than duplicating work. Keep changes reviewable and compatible; no framework rewrite, additional personas or asset-class expansion.

Create `docs/audit-2026-11-12/IMPLEMENTATION-STATUS.md` recording finding IDs, changed files, evidence, exact test commands/results, compatibility impact, exclusions and next steps. Finish Batch 1 coherently and report its outcome before progressing to Batch 2. Missing credentials do not block this batch.

The handoff covers all 21 audit findings and later work on timing, immutable vintages, claim support, budgets/manifests, quote/snapshot quality, jobs, backtest accounting, demo/export and evaluation. Follow its dependencies when continuing. Do not claim analyst accuracy, profitability or real-market execution readiness from passing fixture tests.
