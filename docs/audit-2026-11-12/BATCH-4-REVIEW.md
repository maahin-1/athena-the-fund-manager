# Batch 4 review — 14 November 2026

Reviewed the uncommitted WP05/WP06 implementation, with focused checks by the primary reviewer and two component reviewers. Application code was left unchanged. Tests/probes used synthetic fixtures, fake providers and temporary stores; no environment file, credential value, real model/provider/broker, production store or migration was accessed. Batch 5 was not started.

## Verification

Independent focused run: **233 passed in 39.14 seconds**. Covered typed claims/specialists, council binding/evidence/debate/judge/run, forensic lineage, ledger/envelopes, legacy specialist plumbing, budget/resilience/cache and verdict runner. Project venv, per-process workspace TEMP/TMP, and disposable workspace `--basetemp` were used. The implementer's 2,944-pass broad result was not independently rerun; the nine exclusions and 74 live skips remain applicable to that reported result.

Four additional boundary probes below exposed cases absent from the passing focused suite. They are implementation defects, distinct from the explicitly pending human evaluation or real-provider compliance work.

## B4-1 — P1: malformed HTTP 200 reply refunds a consumed request

**Owners:** `src/athena/llm/client.py:89–99,129`; `src/athena/llm/ledger.py::classify_failure` and fallback attempt propagation.

The HTTP-200 branch records an attempt only after `_read()` succeeds or raises `ProviderError`. `_read()` directly indexes `choices[0]["message"]`. A body such as `{"choices":[{}]}` raises `KeyError`, bypassing attempt recording. The fallback consequently reports an empty attempt list. `classify_failure([])` interprets that as definitely no provider work and marks the reservation refused, releasing the unit.

**Offline reproduction:** temporary ledger and ceiling of one, `OpenAICompatibleClient` with injected fake HTTP returning status 200 and the malformed body, fallback and metered wrappers. Two consecutive logical requests issue **two fake HTTP posts**, each ends in `AllSourcesFailed`, but counted usage stays **zero**, with `refused=2` and `provider_attempts=0`.

**Required correction:** validate the response shape/content and convert expected parsing/type/key failures into recorded bad-reply outcomes. More generally, an HTTP attempt that began must not disappear when processing its response fails. Preserve unknown/consumed accounting conservatively; an empty attempt list should mean no request was attempted only when that is actually established.

**Acceptance:** malformed choices/message/content and usage shapes, malformed JSON, interrupted parsing, ordinary declined request and skipped-model paths. After the first unknown HTTP-200 failure at ceiling one, the second logical request is refused before its fake transport runs. Count attempts separately from logical reservations; keep unreported tokens unknown.

## B4-2 — P2: mixed unknown lineage launders relevant support as independent

**Owner:** `src/athena/forensic/scoring.py:65–107`.

`_sources()` unions known source IDs and drops unknown lineage. `assess_support()` then checks only that those unions are nonempty, while the supporting numbers can come from the whole entry's evidence. An unrelated known-source item can therefore make a relevant unknown-source item appear to be independent corroboration.

**Offline reproduction:** a text flag compares document values 1000 and 800. Another lane supports it with two references: a known `fin.table` result containing only 55, and an unknown `mystery.tool` result containing 1000. Its `values` and prose cite 1000. Entry validation accepts this, and assessment returns `independent`, source `statements:TARGET`, relevance `shared number`. The supporting 1000 came only from the unknown source, not the named statements source.

**Required correction:** tie each relied-on support value/proposition to its exact evidence row/path and lineage. As a conservative interim rule, mixed unknown supporting lineage must not become independent merely because another referenced row has a known source. Retain the declared semantic limitation: even known-source numeric overlap does not establish entailment.

**Acceptance:** relevant unknown + irrelevant known source; relevant known + irrelevant unknown; repeated underlying source through different IDs; truly independent known source; and missing lineage. Status and explanation must identify the source of the fact actually relied upon.

## B4-3 — P2: evidence is credited as exposed before any model receives it

**Owner:** `src/athena/forensic/agent.py:237` and the exposure-recording boundary.

The agent records exposure immediately after executing a tool and appending its result to history. A run that reaches its step cap on that action ends without making a subsequent model request. The report can nevertheless credit the tool result as examined. A failed/budget-blocked next request presents the same distinction between queued history and delivered evidence.

**Offline reproduction:** one-step `run_agent` with a scripted model whose only action is `tool doc.get`. The tool returns 35 characters. The run stops at `step_cap`; its exposure row credits the entire result, but the sole model prompt contains no tool output.

**Required correction:** track pending content separately and commit exposure when the rendered spans are actually dispatched in a subsequent model request. Apply the final transport/truncation boundary consistently. Appending to internal history is not delivery, and delivery is not proof that a model understood the source.

**Acceptance:** final-step tool call, next-call budget refusal, model-call failure before dispatch, ordinary successful next turn, and bounded reopening by offset/path. Undelivered spans remain unread in report coverage.

The earlier candidate concerning double JSON truncation of `evidence.read` was discarded after examining the live branch: the agent already renders that tool's text specially. It is not a finding.

## B4-4 — P2: unknown provider attempts become the number seven

**Owner:** `src/athena/llm/envelope.py:116`.

Attempt metadata is deliberately the string `"unknown"` when unavailable. The summary calculates `sum(len(c.get("attempts") or []) ...)`, so it takes the length of that string and reports seven provider attempts. This happens for a supported metered client that exposes no attempt data, and the unmetered cached-client path also emits this representation.

**Pure offline reproduction:** add one call with `attempts="unknown"` to a `RunRecorder`, then assemble its envelope. Observed `summary.provider_attempts == 7`. No provider or store was involved.

**Required correction:** count only structured known attempt lists. Preserve uncertainty in the aggregate—for example known-attempt subtotal plus a count of requests with unknown attempt metadata, or an unknown total. Do not silently turn missing attempt data into either seven or a proven zero.

**Acceptance:** known list, unknown metadata, cache hit with zero attempts, and mixed known/unknown requests; aggregate summaries and displayed units remain truthful.

## Scope and status qualifications

- The typed claim contract is implemented and offline-tested but opt-in. The ordinary default specialist still uses numeric consistency with `binds_claims: false`. A1's default-path risk is disclosed, not eliminated. Track typed-contract rollout/model compliance separately; no live call is required or authorised by this review.
- Human verification, code-owned proof and model corroboration now have distinct statuses. This is useful progress. Human entailment/analyst usefulness remains unmeasured.
- Run envelopes currently cover stored verdicts, not forensic reports or evaluation outputs. WP06 attribution coverage is therefore partial. Keep those remaining surfaces open rather than marking universal provenance complete.
- Transactional reservations address the original count-then-spend race, and unknown timeout reservations are conservatively retained. B4-1 is a specific exception-path hole in that improvement.
- Dirty working-tree identity, specialist memory-cache attribution and actual provider-reported model identity merit later provenance review; they were not reproduced as additional blockers in this bounded pass.

**Disposition:** address B4-1 through B4-4 and retain the explicit rollout/coverage qualifications before closing this Batch 4 review. No application implementation, live validation, commits or Batch 5 work was performed by the reviewers.


## Follow-up review — 2026-11-15

**Disposition: B4-1 through B4-4 are closed as fixed_offline_verified on synthetic fixtures.** This supersedes the original review's recommendation to keep these four gaps open. It does not establish live provider behavior, semantic entailment, complete pipeline provenance, or market readiness.

Independent verification: **337 passed in 75.28 seconds**. Named focused files:

- tests/test_llm_replies_unexpected.py
- tests/test_forensic_support_binding.py
- tests/test_forensic_exposure_dispatch.py
- tests/test_claims.py
- tests/test_specialist_claims.py
- tests/test_council_binding.py
- tests/test_forensic_lineage.py
- tests/test_llm_ledger.py
- tests/test_llm_envelope.py
- tests/test_specialist_base.py
- tests/test_council_debate_judge.py
- tests/test_council_run.py
- tests/test_council_evidence.py
- tests/test_llm_budget.py
- tests/test_llm_resilience.py
- tests/test_llm_cache.py
- tests/test_verdicts_runner.py
- tests/test_forensic.py

Ran with the project virtualenv, pytest cache disabled, and a dedicated temporary basetemp; no live flag. Reviewed named corrected source paths, including fallback accounting and store exposure aggregation. Whitespace check passed for the reviewed source/regression files; Git emitted LF-to-CRLF notices.

### Correction assessment

- **B4-1:** attempts are recorded as sent before transport. Shape failures become recorded bad replies; unexpected interruptions leave counted reservations. A called client lacking telemetry contributes unreported rather than an empty history. A fully skipped chain remains free. Regression cases verify that ceiling one blocks a second fake transport call.
- **B4-2:** relevance is resolved through the supporting lane's rows holding the figure or passage. Unrelated known-source rows no longer supply provenance. Unknown flag sources conservatively prevent an independence claim. This remains row-level provenance and numeric/quote matching, not entity/metric entailment or company-level truth.
- **B4-3:** queued output starts unread; successful subsequent delivery credits spans only if the history line appears in the prompt. Pre-dispatch case-budget refusal credits nothing, uncertain failure stays possibly shown, and reopening itself does not credit exposure.
- **B4-4:** only list histories contribute attempt counts. Unknown histories propagate unknown total, with a separate known subtotal and unknown-call count.

No additional closure-blocking defect was reproduced in this focused follow-up. The implementer's mutation results were inspected in the status document but mutations were not independently rerun.

### Remaining qualifications

The implementer's broad 3,028 passed / 74 skipped run with nine exclusions was not independently rerun. Its roughly 24 unexplained additional cases remain a test-inventory reconciliation item; the independent 337-case result is the verification claimed here. Do not describe the broad result as an unqualified full-suite pass.

Default specialists still do not use typed claim binding. Real provider/model behavior, semantic support for conclusions, dirty-working-tree code identity, specialist memory-cache coverage, exact provider/model identity, and forensic/evaluation envelope integration retain their existing open qualifications.

Batch 4 may close within those stated limits. Batch 5 was not started. No application code changed, nothing committed, and no environment files/exported credentials, production stores, live providers/models or brokers were accessed. Only this review document was updated and temporary review fixtures were created and removed.
