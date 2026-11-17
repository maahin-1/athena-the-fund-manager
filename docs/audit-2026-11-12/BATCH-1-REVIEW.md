# Batch 1 follow-up review

Reviewed the uncommitted Q6/A4/T4/T7 changes against the handoff. Application code was left unchanged. No environment files, credentials, live provider/broker calls or production stores were accessed.

## Finding: T4 handover failure still admits a second writer

**Priority: P2. Owner:** `src/athena/ops/jobs.py:131–141`, interacting with `_take` at `:108–112` and the supervisor's unconfirmed-child path.

`Job.hand_over()` catches a lock rewrite `OSError`/`ValueError`, then unconditionally clears `_held`. If that rewrite fails, the lock still names the supervisor PID. `_take()` deliberately treats a lock owned by the current PID as reclaimable, so a subsequent job in that same supervisor deletes/replaces the lock. An unconfirmed old child may still be writing. The no-usable-PID path has the same ownership issue. Successful child-PID handover is covered by the new tests; failed handover is not.

**Offline reproduction performed:** create a `Job` holding `writer:sample.duckdb` in a temporary directory; inject an `OSError` from `athena.ops.jobs._write` only during `hand_over(424242)`; restore writes; attempt a second same-process `Job` for that writer while `pid_alive` returns true. Observed: `Second writer admitted after failed handover: True`. No child process or database was started. Temporary reproduction files were removed.

**Required completion:** make lock handover return a verifiable outcome rather than silently assuming success. On partial/failed transfer or missing PID, block conflicting scheduling and preserve an explicit unresolved/quarantined ownership state. Retaining the supervisor PID alone is insufficient because of same-process reclamation and supervisor restart. Define recovery so an unresolved live writer cannot be treated as stale simply because its supervisor ended. Preserve successful transfer behavior and bounded shutdown; a lock-storage failure must be surfaced to status/diagnostics.

**Regressions:** failed rewrite, missing/unusable PID, partial success across multiple locks, same-supervisor next writer, and restart/recovery while child exit remains unresolved. Assert no conflicting writer is launched. Use fakes and temporary paths only.

## Other reviewed changes

- Q6 implements the specified zero-confidence synthetic neutral without changing the existing modal policy.
- A4 validates citation/prose types before use, repairs/rejects malformed replies, and distinguishes no-change insufficient support. Semantic entailment remains WP05; cited is not verified.
- T7 checks native failures and uses an isolated PowerShell stub harness. Windows PowerShell 5.1 evidence does not establish PowerShell 7 behavior.
- T4's normal terminate/wait/kill/wait and successful handover improve the original path, subject to the finding above and the documented direct-child scope.

Independent focused rerun: **128 passed in 34.10 seconds**, covering PIT score/scorecard, council validation/run/evidence, supervisor/jobs, startup harness and check runner. The command used the project venv, `-q -p no:cacheprovider`, a dedicated workspace `--basetemp`, and per-process TEMP/TMP. This does not exercise the newly identified failed-transfer case; the synthetic reproduction above supplies that evidence. Claude's broader 2,627-pass result was read from the implementation status, not independently rerun here. No commit or Batch 2 implementation was performed. Resolve the handover-failure case, update the status with evidence, then use the handoff for Batch 2.

## Follow-up: original handover gap addressed; blocked retry erases restart recovery

The subsequent implementation added explicit handover results, unresolved lock records, in-memory quarantine and startup recovery. Inspection confirms that the original failed-transfer/same-process gap is addressed. An independent rerun of `tests/test_supervisor.py tests/test_ops.py` passed **47 tests in 18.54 seconds** with isolated workspace temporary directories.

**Remaining P2:** `src/athena/ops/supervisor.py:298–299` deletes a task's previous `unconfirmed_child` whenever the current `_run` does not set a new one. `_run` clears its per-attempt field before acquisition; a quarantined lock returns code 6 without launching a child. Consequently a normal scheduled retry erases the durable record of the still-unresolved earlier child. A blocked retry does not establish that child exited.

**Reproduced offline with no real child/database:** seed an unconfirmed child PID and writer lock in temporary files; simulate a failed lock rewrite during `recover()`, producing the intended in-memory quarantine; call `step()` for the due task. Result: `Blocked retry code: 6`, but `Recovery record survived blocked retry: False`. Clear only process memory to simulate restart, invoke recovery again, and use a counting completed fake as the next spawn. Result: `Run after simulated restart: 0 writers launched: 1`, while the original child PID is still modeled alive. All temporary probe files were removed.

This needs neither simultaneous failure of all storage nor loss of supervisor.json: the scheduler itself overwrites valid recovery information after only the lock rewrite failed.

**Correction:** retain unresolved child records across refusal, retry scheduling and unrelated attempts. Remove them only after confirmed resolution or an explicit durable operator-clear decision. Distinguish per-attempt diagnostics from outstanding child lifecycle state. Add a sequence regression covering failed handover/reinstatement → ordinary `step()` retry returning 6 → restart → conflicting writer still refused. Also verify that operator clearing resolves the durable record so startup recovery does not immediately reinstate a cleared unknown-PID quarantine.

Application source and implementation status were left unchanged by this follow-up review. Batch 2 remains untouched; no commits were made.

## Final scoped follow-up: reproduced T4 findings closed offline

Reviewed the subsequent lifecycle-state correction in `jobs.py` and `supervisor.py`. Outstanding child records now survive per-attempt refusal/results; recovery iterates outstanding records; operator resolution updates durable lock membership before clearing quarantine. These changes address both reproduced findings above within the direct-child, fake-process verification scope.

Independent rerun of `tests/test_supervisor.py tests/test_ops.py`: **53 passed**. This review did not rerun the reported 177-test focus or 2,641-test broad suite. Those remain implementer-reported results with the existing nine exclusions and live skips.

**Disposition:** no further blocker identified for the defined Batch 1 offline acceptance. Proceed with the staged Batch 2 work packages. This is not a certification of live process termination or concurrent recovery control.

**Retained limitation:** operator resolution and scheduler state saves still use whole-file read/modify/write without cross-process transaction isolation. Atomic replacement prevents a torn JSON file but not lost concurrent updates. Track serialized state mutation/CAS or transactional persistence as an operational hardening follow-up; until tested, perform operator recovery with the supervisor stopped and verify durable state before restarting. Direct descendants/process groups and live termination remain unvalidated. No application edits, real supervisor actions, commits or Batch 2 implementation were performed by this review.
