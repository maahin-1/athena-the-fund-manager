# Batch 2 follow-up review

Reviewed the uncommitted WP02/WP03 implementation on 12 November 2026. Application source was left unchanged. No environment files, provider credentials, real broker, background supervisor or production store was accessed. Batch 3 was not started.

## Validation performed

Independent offline run: **135 passed in 17.79 seconds**, using the project venv, per-process writable TEMP/TMP and a dedicated workspace `--basetemp`. Included timing, prospective settlement, freshness, evidence classes, forensic availability, daily loss, kill-race, verdict runner/CLI, broker desk/concurrency/live gates and fake live-base tests.

The reported 2,714-pass broader run was not independently rerun in this review. Its nine exclusions and live skips remain material. Passing existing tests did not cover the three additional boundary cases reproduced below.

## B2-1 — P1: uncertain send outcome drops the marker and allows resume

**Owners:** `src/athena/brokers/desk.py` — `_confirm` exception/finally path and `resume`.

The new SENDING marker is removed unconditionally in `_confirm`'s `finally`, including when `broker.place` raises after sending but before receiving an acknowledgement. A real adapter can retain that order in its unresolved ledger (`live/base.py::unresolved`). `kill()` detects that unresolved ledger and raises, but `resume()` checks only SENDING, so it removes KILL while the broker still has an unknown outcome. The desk also lacks a global unresolved-broker gate before a different new order; the existing adapter only prevents repeating the same fingerprint.

**Synthetic reproduction:** use the existing temporary paper desk fixture with an injected `place` function that adds its client ID to a fake unresolved ledger and raises `TimeoutError`. After confirmation raises `BrokerError`, the ledger remains nonempty but SENDING is absent. `kill()` raises for the unknown outcome; `resume()` then succeeds and removes KILL. Observed output:

```text
Unknown broker send remains: True SENDING remains: False
Resume allowed with unknown broker send: True
```

No order was sent externally; the injected broker only recorded synthetic state.

**Required repair:** distinguish definite pre-send refusal/known terminal outcomes from uncertain transport or post-send failures. Preserve a durable unresolved send until explicit reconciliation; check both desk state and the broker's unresolved ledger before resume/new sending. An unreadable unresolved ledger cannot establish that it is clear. Define how `resolve-send` and the adapter's existing `resolve` cooperate, with audited state changes and no automatic retry of unknown sends.

**Acceptance:** fake lost acknowledgement, timeout after acceptance, failure writing the post-send audit line, restart with an unknown outcome, and new order with a different fingerprint. Resume/new sending remain refused until durable reconciliation. Known definite rejection can resolve safely; normal completed sends still clear their active marker.

## B2-2 — P1: kill reports cancellation when cancellation is merely requested

**Owner:** `src/athena/brokers/desk.py::kill`, cancellation sweep.

The sweep ignores the `Order` returned by `broker.cancel` and immediately appends its ID to the cancelled list. `LiveBase.cancel` can legitimately return an order still open after the cancel request, or an order that filled while cancellation raced. A successful request is not confirmation of cancellation. The current result can report success with no failed/unresolved entry while an order remains open.

**Synthetic reproduction:** create one resting paper order and replace `cancel` with a fake that returns the unchanged open order. `kill()` returns normally with:

```text
Reported cancelled: ['P000001'] actual remaining status: open
reported unresolved: [] reported failures: []
```

**Required repair:** inspect returned status and reconcile with a bounded order-state lookup/poll policy. Only confirmed cancellation belongs in the cancelled list. Report filled/partially filled outcomes explicitly and leave still-open, unavailable or ambiguous results failed/unresolved. Keep KILL set and never imply the file undid an existing fill. Continue attempting cancellation of other orders when one cannot be resolved.

**Acceptance:** acknowledged-but-open cancellation, fill racing cancellation, partial fill, missing order after cancel and lookup failure. A fake adapter must establish these states offline; this is not deferred merely because real broker timing is separately unvalidated.

## B2-3 — P2: missing benchmark rows silently move entry and holding sessions

**Owner:** `src/athena/verdicts/decisions.py::settle_prospective`.

The function defines exchange sessions as `sorted(benchmark)`. Missing an entire benchmark row therefore makes a genuine exchange session disappear. This differs from an existing row whose open is None, which the new tests correctly classify as a gap. A missing entry row shifts the trade to a later observed benchmark date; a missing interior row extends the nominal horizon. No gap is reported.

**Synthetic reproduction:** decision on Wednesday 7 January 2026; stock bars are present Thursday January 8 and Friday January 9; benchmark observations contain only Friday. A one-session settlement selects January 9 and returns `settled`, although the declared next-session entry is January 8 and its benchmark price is unavailable.

```text
Expected exchange entry Jan8; selected: 2026-01-09 status: settled
```

**Required repair:** determine intended entry/exit dates from an explicit exchange-session calendar or another independently established session sequence, not the set of prices successfully downloaded. Price lookup then returns named gaps for absent rows/fields. An unestablished future session/window remains pending or calendar-unavailable; missing observations must not masquerade as holidays or move the policy dates.

**Acceptance:** missing whole benchmark entry row, missing interior and exit rows, known holiday, missing stock row, and future incomplete window. The intended entry/exit dates remain invariant when benchmark observations are deleted. Preserve legacy association and prospective result separation.

## Other observations and disposition

The implementation makes meaningful progress: named timing policies; decision-completion capture after council; archival/approximate scorecards; legacy separation; document/shareholding availability records; runner-level price freshness; and explicit unavailable daily-loss protection are present. Yahoo remains approximate and only price freshness is gated, as disclosed. Those limits must stay visible.

**Disposition:** close the three reproduced cases before treating Batch 2 as complete. Preserve the original audit and implementation history; update IMPLEMENTATION-STATUS with corrective evidence. This review supplies findings, not a claim that all other branches are proven correct. No application edits, commits or Batch 3 migrations were performed.
