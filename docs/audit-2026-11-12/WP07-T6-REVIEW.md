# WP07 T6 independent review — 2026-11-16

**Disposition: keep T6 open and T8 paused.** Existing fake tests cover useful paths, but three independently reproduced lifecycle failures and an end-to-end admission bypass remain.

## Independent verification

An isolated temporary Cargo crate copied only terminal/src/bounded.rs and bounded_tests.rs and added three reviewer tests using immediately-ready fake children. It did not include DuckDB, start the terminal, run Python workers, or use production data. Offline Cargo resolved tokio 1.53.2, serde 1.0.229 and serde_json 1.0.154, matching terminal/Cargo.lock for these direct dependencies.

Result, reproduced twice: **27 existing bounded tests passed; all 3 reviewer regression probes failed**. These are deliberately failing probes of current application code, not an assertion that Claude's original 310-test suite failed. Full project Rust and mutation runs were inspected as reported/saved evidence, not independently repeated.

Copies of the isolated crate, reviewer regressions, lockfile and raw test log are in baselines/wp07-t6-review-2026-11-16/. Run the crate with cargo test --offline --locked --manifest-path <artifact>/Cargo.toml and a separate disposable target directory.

No application code changed, protected environment files or credential values inspected, production data accessed, live broker/model calls made, commits or pushes performed.

## T6-R1 — P1: pre-cancelled and already-expired assigned work can execute

Locations: terminal/src/bounded.rs:744–798; Core::cancel and sweep.

run_job spawns before checking cancellation/deadline. Its biased select prioritises the work future. An immediately-ready child can win over a stored cancellation notification or an already-elapsed deadline.

Independent probes:
1. enqueue a job, cancel it synchronously before any worker task runs, then await it. One request executes and outcome is Reply(ok:true), not cancellation.
2. enqueue a job with a 5 ms limit, prevent worker pickup for 25 ms on a single-thread runtime, then await it. One request executes and outcome is Reply(ok:true), despite expiry before pickup.

sweep only removes Core.queue entries; enqueue has already moved this job to Assigned. That gap is not covered by ordinary queued-expiry tests.

Correction: check cancellation/expiry before spawning and immediately before dispatching a request. Give stop conditions appropriate priority, and do not accept a result outside the permitted deadline. Preserve slot ownership during cleanup.

Acceptance: assigned-before-pickup cancellation and expiry, zero-time limit, a warm child immediately ready to answer, stop/reply becoming ready together, and a job whose startup consumes the deadline. Assert zero child requests/spawns where work was never authorised to start.

## T6-R2 — P2: dropping pool then completing final ticket can strand an idle child

Locations: bounded.rs:612–619, Ticket::result/Drop:627–659, run_slot:726–740.

Pool::drop wakes workers, but a Ticket still owns a strong Inner reference. After the worker replies and consumes the wake, it can wait idle with a live child and only a Weak Inner reference. Consuming the completed final ticket then destroys Inner without notifying that idle task.

Independent probe: enqueue immediate fake job -> drop Pool but retain Ticket -> yield until worker has replied and returned idle -> await Ticket -> yield again. Child-drop counter stays zero. The task/child persists until runtime teardown rather than pool/ticket lifetime ending.

Correction: explicit shutdown signalling/ownership, independent of incidental Arc count disappearance. Define whether accepted jobs drain or cancel, terminate/reap idle children, and ensure busy/retained workers observe shutdown. Do not rely on kill_on_drop alone as confirmed lifecycle management.

Acceptance: pool drop with idle child; pool drop while Ticket is retained and later consumed or dropped; busy child; retained child; no remaining task/child after normal shutdown, or an explicit still-unconfirmed state.

## T6-R3 — P1: helper fallback bypasses admission, total deadline and quarantine

Locations: terminal/src/helper.rs:75–80; bars.rs:232–244; fundamentals.rs:223–234; flow_board.rs:68–79.

Helpers::run converts full queue, expired deadline, retained/unconfirmed worker, startup failure and command failure into None. Callers interpret None as permission to spawn directly.

Consequences by source inspection:
- adjusted bars can launch direct children after queue saturation without the pool bound;
- fundamentals/board wait on separate semaphores before submitting, so their overall request wait is still unbounded;
- after spending the helper deadline, fallback receives a fresh full timeout;
- an unconfirmed old child may remain alive while a fallback starts another execution of the same work.

This is explicitly disclosed, but it defeats the workpackage's end-to-end bounded-work contract; it is not just a capacity-tuning question.

Correction: typed outcomes must distinguish unavailable optimisation from refusal/expiry/unconfirmed execution. Refusal, exhausted deadline and unknown execution must not silently trigger a duplicate direct run. Any supported fallback must share bounded admission and the original absolute deadline, including caller-side semaphore waits, and use confirmed child cleanup.

Acceptance: end-to-end caller/endpoint tests with saturated helpers; queued wait plus startup/execution/fallback within one deadline; zero fallback spawns after expiry or held outcome; bounded concurrent fallback; old unconfirmed child blocks conflicting replacement. Preserve legitimate direct execution where no helper is configured by routing it through the same admission contract.

## T6-R4 — P2: operator/restart recovery lacks process identity and overstates parent-exit cleanup

Locations: bounded.rs JobRecord/status, Live/ProcessLive, hold and resolution; server.rs WorkResolveBody/api_work_resolve; WP07-T6-BOUNDED-WORK.md limits.

Status identifies pool/slot/job but not the child's PID or birth identity. The operator is asked to confirm a process is gone without being told which process. Resolutions record only resolved_by_person, without who/why.

The document also says children end when the terminal ends through kill_on_drop. That cannot establish cleanup after abrupt termination: destructors need not run, and even normal drop requests a kill without proving exit. In-memory quarantine is then gone on restart.

Correction: expose process identity and retain enough unresolved-child information for meaningful recovery. Either recover durable unconfirmed ownership before admitting conflicting work after restart, or establish an OS-enforced parent/child lifetime and explicitly limit the guarantee. Record operator identity/reason for overriding an unconfirmed guard. Do not claim parent-exit confirmation based on kill_on_drop.

Acceptance: unknown/known PID, terminal restart with an unresolved child, save/recovery failure, and explicit operator resolution. Use fakes and temporary state; no production supervisor or real job is required. Direct-child-only remains a disclosed limit rather than an implicit process-tree claim.

## Answers to the implementation questions

1. **Reply at the deadline:** yes. Caller latency may end while cleanup continues, provided response/status distinguish deadline reached, cleanup pending and cancel_unconfirmed, and no admission slot/guard is released early. Helper fallback must not restart work after this response.
2. **Client disconnect:** choose and test the contract explicitly. Recommended: cancel work still waiting; let an already-running read-only analysis finish and discard an abandoned result, preserving the warm worker; explicit cancellation still stops it. The claim that axum necessarily drops the handler on every HTTP disconnect is not established by Ticket-drop tests. Add a loopback HTTP test with an inert fake worker and correct documentation to observed behavior. No real model/server data required.
3. **Bound fallback:** yes, as part of T6-R3. Otherwise the queue and total deadline protect only an optional path.

## Smaller follow-up and evidence qualifications

Add a safe operation kind (view/backtest/page/chart_bars/etc.) to job telemetry. Queue/run durations alone cannot attribute contention between backtests and model views. Job IDs are currently pool-local; include pool with an ID when referencing it externally.

Red runs against a deliberately inert stub establish new tests reject missing functionality, not an exact reproduction of every old behavior. Keep that distinction in claims.

Do not repeat all full suites merely to address reporting. Fix these boundaries with failing integration regressions, then run affected Rust suites and valid-harness mutations. Keep all current access restrictions; T8 stays paused.
