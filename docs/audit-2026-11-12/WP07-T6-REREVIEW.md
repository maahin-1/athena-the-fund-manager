# WP07 T6 re-review - 2026-11-16

Disposition: keep T6 open and T8 paused. The original pre-start stop checks and last-ticket lifetime defects are fixed in the current source and covered by passing focused fake tests. Typed helper outcomes and shared time budgets are present. End-to-end cleanup and restart safety remain incomplete.

## Independent evidence

The isolated crate in baselines/wp07-t6r-review-2026-11-16 copies only terminal/src/bounded.rs and bounded_tests.rs, plus four reviewer probes. It excludes the terminal, DuckDB and Python helper execution. It ran offline with fake children and synthetic journals only.

Result: **49 existing tests passed; all 4 reviewer regression probes failed** (53 total, exit 101). This deliberately red reviewer run is separate from Claude's reported 358-test full application runs; the full application suite was not rerun.

For isolation, the copied tests' journal_path helper changes its recursive deletion to a fresh-directory assertion. No application source or existing test was edited. No production data, protected environment file, credentials, model/provider/broker call, commit or push was involved.

Raw output: baselines/wp07-t6r-review-2026-11-16/review-test.log. Reproduce with offline Cargo against that artifact Cargo.toml and a separate target directory.

## T6-N1 - P1: direct execution still releases admission before confirmed exit

Source: terminal/src/fundamentals.rs:298 and :340; flow_board.rs:95; bars.rs:275.

These commands use timeout around wait_with_output, wait or output, with kill_on_drop(true). When the future times out or is cancelled, the enclosing Admission Permit drops. No termination confirmation, retained child identity or durable quarantine occurs on these direct paths. A kill request is not proof of exit. A subsequent request can therefore take the capacity while the prior direct child has not been confirmed gone. Pool shutdown also has no ownership of these children.

Correction: route direct execution through a managed child owner, or give it equivalent stop/terminate/wait/kill/wait and retained-guard handling. The caller may return at its deadline, but a separate owner must retain the process and admission until confirmed exit or explicit audited resolution. Apply to absent-helper and eligible fallback paths, and export. Check the budget before any spawn, including expiry during admission.

Acceptance: fake direct child ignores termination/kill confirmation -> timeout or cancellation returns -> a conflicting replacement remains refused -> confirmed exit frees capacity. Cover shutdown too. This is source-inspected, not a real-process reproduction.

## T6-N2 - P2: dropping an admission waiter leaks its queue place

Source: bounded.rs:1631.

admit increments state.waiting before awaiting the semaphore, and decrements only after that await completes. Aborting or dropping the future skips the decrement. Each cancellation can permanently consume one waiting place, causing false queue_full responses under later contention.

Independent failing probe: hold the running permit; spawn one waiter; observe waiting=1; abort and await its task; waiting remains 1 instead of 0.

Correction: an RAII waiting guard must release the count on every return/drop/unwind path. Test cancellation and subsequent actual admission, not just timeout.

## T6-N3 - P1: unconfirmed idle children disappear on shutdown

Source: bounded.rs:1294.

When shutdown ends a warm idle child, the final run_slot cleanup calls end and increments the unconfirmed counter. It never retains/persists that child's identity. The in-memory report acknowledges the failure while the next process sees no unresolved record and accepts work.

Independent failing probe: warm an immortal fake -> shutdown reports unconfirmed=1 -> rebuild pool from its journal -> enqueue is accepted.

Correction: retain and persist every unconfirmed child at shutdown, including idle/warm children, before discarding its handle. Verify restart refuses conflicting work and preserves PID/launch identity.

## T6-N4 - P1: recovery failures and capacity changes discard guards

Source: bounded.rs:1143 and :459.

A malformed/unreadable journal only sets a status error; the pool starts fully available. Core::restore also skips entries with slots outside the new worker count (and duplicate occupied slots). These are unresolved process records, not disposable scheduling preferences.

Two independent failing probes:
- corrupt synthetic journal -> fresh enqueue is accepted;
- journal holds slot 1 -> restart configured with one worker -> enqueue is accepted.

Correction: unreadable/incompatible recovery state must put admission into an explicit blocked recovery state. Preserve every unresolved record independently of current slot count, or refuse startup with an actionable error until audited resolution. Do not encode the current fail-open behavior as the intended acceptance test. Validate pool identity/schema as part of recovery. A documented deliberate choice can still contradict the required no-overlap contract.

## T6-N5 - P1: journal publication is not serialized with the state snapshot

Source: bounded.rs:1054 and :876.

persist snapshots held entries under the core mutex, releases it, then writes/renames a shared fixed temporary file without a journal transaction lock. With multiple workers, A can capture [A], B capture and publish [A,B], then A publish stale [A]. B is held in memory but absent after restart. The shared temporary pathname also permits concurrent writes/renames to collide.

Correction: serialize state snapshot capture and publication together for every persistence path, using a consistent lock order and atomic replacement; use uniquely owned temporary files as appropriate. Enforce single process ownership or provide interprocess coordination if the same pool/journal can be opened twice. Save failures must be explicit and must not silently allow a claimed recovery guarantee.

Acceptance: deterministic barriers around two independent hold/release transitions, then restart and verify every still-unresolved child remains protected. Test write/rename failures and operator-clear ordering. This finding is based on the source's allowed interleaving; no deterministic concurrency reproduction was run here.

## Review numbering

The saved WP07-T6-REVIEW.md numbers are:
- original R1: stop conditions;
- original R2: final-ticket/idle-child lifetime;
- original R3: helper fallback, total budget and cleanup;
- original R4: identity/operator/restart recovery.
Operation-kind telemetry was an additional follow-up. Claude's paragraph-based mapping differs; preserve a crosswalk rather than silently reusing numbers.

## Decisions and remaining qualifications

1. Return to the caller at deadline/cancellation: acceptable while an independent owner retains cleanup and capacity, with cleanup-pending/unconfirmed status. Direct execution currently violates that condition.
2. Client disconnect: cancel queued work; for already-running read-only analysis, finish and discard an abandoned result. Explicit cancellation stops it. Test the actual loopback HTTP behavior; Ticket drop alone does not prove it.
3. no_answer after send: confirmed exit prevents ongoing overlap but does not prove the work never completed. Default to a final unknown-result failure. Permit a single fallback only for an explicitly idempotent/read-only operation under the same original deadline; do not treat every post-send failure as universally retryable.
4. Journal and Windows Job Objects solve different parts. Keep portable fail-closed recovery; OS process lifetime enforcement is a separate enhancement. Do not require a Windows-only implementation for this correction slice, and do not claim journaled held workers cover normally-running children at an abrupt parent death.

Active children at parent crash, direct-child-only cleanup, uncalibrated capacities, untouched managers and missing real HTTP disconnect validation remain disclosed limits. Red tests against an inert scaffold establish rejection of missing functionality, not exact reproduction of the former implementation. Keep this distinction in the evidence record.
