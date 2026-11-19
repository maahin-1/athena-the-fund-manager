# Agent and research reasoning audit — 12 November 2026

Reviewed checkout: `37f1fc1`; source was not changed. This is a component report for the wider architecture review. All probes used scripted values; no LLM/provider calls were made and no credential files were read.

## Verdict

The deterministic evidence preparation and refusal paths are substantially more disciplined than a collection of unrestricted finance chatbots. However, the system verifies the presence of numbers and quotations much more strongly than it verifies the truth of the conclusions made from them. It is suitable for a supervised demonstration of research infrastructure and limited document reconciliation. It is not yet evidence of an independent research analyst's reliability or of trading skill.

The word “agent” describes two different implementations here. The ordinary stock analysts are fixed prompt/schema calls inside a sequential workflow. The forensic lanes are actual bounded tool/action loops with shared evidence, questions and a supervisor. Neither is automatically a more accurate researcher because multiple model personas participate.

## Severity-ranked findings

### A1 — High: numeric grounding accepts wrong signs, wrong metrics and unsupported qualitative conclusions

Evidence: `src/athena/evaluation/grounding.py:29`, `:51`, `:56`, `:62`; enforcement in `src/athena/agents/base.py:159`.

The checker flattens every numeric value reachable in the packet, including numbers embedded in dates and text, then checks whether a number in the response matches **any** packet number. It removes signs, permits multiplication by 100 regardless of the source unit, ignores ordinary integers up to ten, and accepts prose with no checked numbers. It does not bind the claim to a metric, period, sign, company or unit.

Offline reproduction: a packet containing negative ROE `-0.25` and P/E `20` accepts both “Return on equity is positive 25%” and “Revenue grew 20%.” It also accepts “The company is certain to outperform,” with zero numbers checked. Those are deliberately false/unsupported probe statements, not observations about a real company.

This matters because schema-valid, numerically accepted reasoning can still reverse the evidence and produce a bullish/bearish signal. The README's grounding claim should be described as numeric consistency checking, not a guarantee against invented analysis.

Fix: have each factual claim return structured `metric_id`, source value, unit, period and interpretation. Code should render the numerical sentence, preserving sign and permitted unit conversion. Require citations for factual claims and separately evaluate semantic support and harmful omissions. Keep free narrative, clearly labelled as model interpretation. Do not try to solve semantic correctness solely by expanding regexes.

### A2 — High: forensic evidence verification does not verify the assertion in the entry's text

Evidence: `src/athena/forensic/verify.py:161`, `:180`, `:186`, `:195`; promotion in `src/athena/forensic/scoring.py:37` and `:44`.

`quote` and `values` are optional. An OBSERVATION needs an existing evidence ID, but its prose is not checked against that evidence. An observation saying “Revenue collapsed by 95% and all creditors refused payment” passes with E1 containing only “The company held its board meeting.” An optional genuine but irrelevant quote would not establish the asserted conclusion either.

Promotion to “established” can also use another lane's observation with a quote or values and an evidence ID that differs from the flag's. `_supports` verifies different lane ownership and different evidence ID; it does not verify that the supporting proposition entails the flag or that a different tool/query result is an independent underlying source. The registry does deduplicate identical tool/argument requests, which is a useful safeguard, but different requests can still expose the same filing.

Important positive distinction: a model-chosen arithmetic relation is deliberately **not** proof on its own; `_proof` only promotes invariants authored by the code `checks` lane. This is good design and prevents the simpler “LLM compares unrelated numbers, code calls it proof” failure. The remaining risk lies in semantic support promotion.

Fix: make evidence provenance include immutable document/source identity plus page/span, report date and fact identity; attach support to a precise proposition; distinguish independent source corroboration from another reading of the same source. Until human-labelled entailment tests pass, call model-supported flags “corroborated by model lanes,” reserving “established” for code-owned invariants or human review.

### A3 — High for historical use: forensic document tools do not enforce the case date

Evidence: `src/athena/forensic/text/lane.py:62`; `src/athena/documents/store.py:29`; historical argument in `src/athena/forensic/assemble.py:60`. Also `src/athena/forensic/ownership/lane.py:83`.

`DocData.document` checks that the document exists, but never compares its `published` field with `case.as_of`. The assembly CLI accepts a historical `--as-of`. Therefore the text agent can receive a document published after the date at which it is supposedly investigating. A stub document published 1 January 2027 is accepted for a case dated 1 January 2025. The prompt's “Nothing after that date exists for you” is not an executable guard.

Separately, ownership patterns with no known filing timestamp are admitted when the quarter end is on or before the case date. A quarter ending today was generally not public today merely because its accounting period ended today. The missing filing timestamp should be an explicit unknown, not permission for historical admission.

Scope: this is a concrete gap in forensic document/ownership boundaries, not a claim that the separate price/backtest pipeline has no point-in-time controls. The current IPO Analyse job uses today's case date, so the first gap is principally a historical research issue.

Fix: one shared availability gate: reject documents published after the case date and refuse unknown publication dates in strict historical mode; apply the same rule to missing ownership filing times. Store the policy and rejected-count in the report. Use the case date throughout tool boundaries, not only in prompts.

### A4 — Medium: the council does not require citations and incompletely validates field types

Evidence: `src/athena/council/debate.py:35`, `:46`; `src/athena/council/judge.py:35`, `:49`.

Unknown citation IDs are rejected, but there is no requirement to cite any ID. Both debate and judge accept a numberless unsupported claim with empty evidence/relied-on arrays. The judge is still constrained to one rating step, but that step can be justified by unsupported prose.

The exact-key checks do not constitute a full schema: for example `evidence: null` or `relied_on: null` makes `map` raise TypeError instead of becoming a repairable invalid reply. The VerdictRunner catches council exceptions and preserves the blend (`runner.py:156`), limiting impact to loss of the council result rather than destroying the stock verdict.

Fix: validate field types, lengths and non-empty citation arrays before using them; bind a claim to the cited fact; require a justification for a rating change. Keep the one-step/conviction limits and shadow operation. Add negative probes for empty citations, wrong field types, correct numbers attached to the wrong fact and qualitative contradiction.

### A5 — Medium: the monthly call ceiling can fail open and is not an atomic spend limit

Evidence: `src/athena/llm/budget.py:44`, `:47`, `:50`, `:109`; underlying retries `src/athena/llm/client.py:79`.

One malformed JSONL usage line causes `Meter.rows()` to return an empty list for the **entire** file. Reproduction: after recording one successful call against a ceiling of one, appending `{partial` makes the recorded count become zero and `check` allows the next call. The budget file also fails open to no ceilings if unreadable. This is distinct from the intentional choice to leave a tier uncapped when the owner supplies no ceiling.

The sequence `check -> request -> append` is not a reservation transaction, so simultaneous processes can each observe remaining capacity. A metered “call” also wraps a fallback chain whose clients may each retry; it is a logical request ceiling, not a ceiling on provider HTTP attempts, tokens or currency. Token usage is taken from the eventual answering client; intermediate successful-but-unusable or timed-out calls may have costs not represented in the summary.

Fix: use an atomic durable budget reservation in SQLite, parse and report individual corrupt log rows, and refuse metered paid work if the configured budget cannot be read. Name the units accurately. Track each provider attempt, usage and fallback reason; add actual token/currency ceilings only if needed. A hard currency cap cannot be inferred from the current logical-call count.

### A6 — Medium: the warehouse verdict path accepts old packets without an age gate

Evidence: `src/athena/verdicts/runner.py:70`, `:97`; packet generation `src/athena/warehouse/packets.py:99`, `:122`.

The runner checks instrument kind and whether a technical packet exists, not how old it is relative to the request. Packet building checks whether a symbol is active relative to the warehouse's latest loaded date, not whether the warehouse itself is current. With a failed ingest/supervisor, old packets can still generate a newly made verdict. The stored `data_as_of` helps a careful reader see this, but it does not implement the README's general stale-data-refusal claim. Stored fundamentals age flags are not equivalent to a hard age refusal.

Fix: define per-dataset freshness policy relative to the market calendar, gate the runner, and label intentional historical analysis separately. Preserve the old verdict with an explicit stale state when a new one cannot be made.

## What actually runs

### Ordinary stock analysis

1. Resolve name/ticker/ISIN; a genuine ambiguity returns clarification instead of guessing.
2. `routing.py` routes equities to valuation, moat/quality, technicals, earnings intelligence and news. ETF routing has an unbuilt ETF analyst plus technicals. Flow and ownership specialist definitions exist, but they are not in this main route.
3. Packet builders compute evidence in Python. Specialist critical/optional input declarations derive coverage; missing critical inputs produce a neutral zero-confidence abstention without asking the model.
4. The common Specialist wrapper presents an explicit JSON task, enforces output contract and coverage, runs the numeric check, retries invalid output up to two attempts, evicts rejected cached replies, and either raises or abstains. All regular specialists normally share the same routed provider tier; they are different analytical prompts, not independent models or measurements.
5. `Orchestrator` runs specialists sequentially and forms a weighted blend. Confidence is supplied by the model; partial coverage gets weight 0.7. Opposing calls above confidence 60 trigger a conflict and conviction cap 40. This is deterministic aggregation of opinions, not a learned estimator of expected return.
6. Optional risk overlay and unexplained-price-break checks can hold back purchase calls. The warehouse VerdictRunner does not supply a personal risk overlay; its notes say none was given.
7. VerdictRunner dampens displayed conviction using saved outcome calibration, creates a typed decision, optionally convenes a shadow council and stores the blend and council separately.
8. The council is opt-in, can run on conflicts/REVIEW or always, gives bull and bear one argument each, then a judge. Code limits the judge to one rating step and prevents conviction increases. Its answer does not replace the production blend. This is an excellent experimental boundary; keep it until a paired comparison supports promotion.

Main CLI analysis and warehouse verdict generation are related but not identical paths. The former uses Orchestrator directly; saved calibration/council are applied by VerdictRunner. Avoid presenting all capabilities as one universal automatic pipeline.

### Forensic investigation

The November layer uses JSON actions (`tool`, `write`, `note`, `finish`) because provider-native tool calls were unreliable. Registry tools enforce an allowlist per lane, run calculations in code and store complete tool results under evidence IDs. The model sees truncated views, has bounded steps/calls, can ask other lanes questions, and is stopped after repeated wasted steps. A mostly deterministic supervisor routes questions, classifies flags and computes a provisional concern range. An optional narrative cannot change that score; invalid narrative falls back to plain text generated by code.

There are two product surfaces:

- `forensic.job`, used by the terminal IPO Analyse button, runs only the deterministic checks lane with **zero model calls** (`job.py:155`). It is a document reconciliation feature.
- `forensic.assemble --agent` can run the bounded model lanes. DRHP currently assembles code checks + text + peers. Listed cases assemble statements + ownership + market. The current CLI selects `reader_retry` directly; its docstring's “cheap tier first and mid on failure” description does not match this wiring.

Document tools cap opened pages and show page references; missing/unreadable coverage is explicit. However, `shown()` truncates all results at 2,500 characters while page-open results can be substantially longer. The agent therefore does not necessarily see the whole returned page or all fields. Evidence can be complete in the store while the model has insufficient visible context. Report coverage of source material actually shown, and give paging/field-selection tools where truncation is frequent.

## Why and when these choices appeared

This chronology comes from checked Git history and local specs, rather than an inference that today's implementation was always planned:

- **5–6 October:** provider abstraction and per-specialist visible metric sets appeared. This keeps acquisition and arithmetic outside personas, and reduces accidental cross-domain reasoning.
- **24 October:** evidence sheet, debate, judge, shadow council, monthly call counting, provider usage and disk reply cache were added in separate commits. The shadow council lets the project measure whether extra reasoning beats the simpler blend without silently changing the main result. The cache reduces repeated cost but keys by tier/prompts, not immutable provider-model version: it is operational reuse, not a complete reproducibility record.
- **25 October:** judge/debate prompts were corrected to interpret the valuation model relative to the typical company, matching the valuation analyst. This shows why prompt consistency across layers matters: a fact can survive transport while its meaning changes.
- **27–28 October:** fallback and resilience were changed after provider/model availability failures; rejection eviction, remembered unavailable models and confidence caps were added. These are practical responses to unreliable external providers.
- **3–4 November:** the forensic ledger/action loop was introduced, then hardened after independent review, including code-only proof ownership and supporter independence. That history explains the explicit evidence/status machinery.
- **7 November:** outside text fences and provider-token-based report cost accounting were added; the IPO checks-only job was wired to the terminal.
- **9 November:** ownership/market/peers lanes and assembly were added. The design record explicitly says these new model lanes had not been exercised with a live model; tool checks on real data and scripted models establish plumbing, not investigation quality.

The forensic spec (`docs/superpowers/specs/2026-11-03-forensic-agent-loop-design.md:82`, `:331`, `:354`) explicitly distinguishes the statistical study of statement measures from evaluation of the model loop. The loop still needs its own benchmark. The forward grading date in that record is no earlier than 6 November 2027; do not treat it as an available result today.

## Strengths worth preserving

- Computed evidence and tool arithmetic are separate from LLM interpretation.
- Coverage abstention and genuine instrument ambiguity are executable behavior, not just prompt instructions.
- Clear schemas and stable evidence IDs make failures inspectable.
- Rejected forensic entries remain stored with reasons; missing lanes/questions remain visible.
- Code-owned invariant proof is correctly stronger than an LLM-chosen relation.
- The council stays beside the baseline until evidence supports promotion.
- Model failures usually degrade to skipped specialists, old verdict retention, or explicit failure rather than a fabricated successful result.
- Structured tool allowlists and absence of arbitrary code execution in model action dispatch are appropriate for this research application.
- Deterministic concern/conviction limits constrain what a final persuasive narrative can change.

## Evaluation and ordered improvements

1. Correct the evidence/date boundary gaps A1–A4 before describing model statements as verified. Build a small human-labelled adversarial suite containing sign reversals, units, periods, wrong entity, irrelevant exact quotes, conflicting documents, prompt injection text and missing filing dates.
2. Separate evaluation targets: tool execution reliability, claim correctness, evidence retrieval recall, contradiction detection, coverage/abstention, usefulness to an analyst, and return prediction. Passing one does not imply the others.
3. Run the same locked cases with deterministic rules alone, one evidence-constrained reader, current specialists/blend, shadow council and forensic loops. Use identical evidence and a fixed cost/time budget. Report supported claims, missed material facts, false flags, abstentions, latency and provider attempts. Multiple personas need to demonstrate incremental value over one good reader.
4. Keep a trace envelope per analysis: dataset snapshot/content hash, source publication/ingestion dates, prompt/schema version, actual answering model, cache origin, attempted models, validation failures and raw/accepted outputs. Current payload model strings can describe configured fallback names rather than the exact responder for each specialist.
5. Improve the budget/attempt ledger and freshness gate before routine unattended analyst use.
6. Perform a supervised analyst workflow trial: time required to verify a report and locate the original filing, errors found by a reviewer, and decisions the tool helped investigate. This can establish research usefulness even if market-outperformance evidence remains absent.

Do not add a graph framework or more agents merely to increase apparent sophistication. The current imperative state machine is readable and testable. Add durable workflow scheduling/checkpointing only when measured restart/resume and long-run orchestration requirements justify its operational cost.

## Verification performed

Focused offline suite: **229 passed in 64.92 seconds**, covering grounding, Specialist base/visibility, council evidence/debate/judge/run, LLM budget/cache/client/router/resilience, forensic core/assembly/text, retrieval/review and scope guard.

Command used Python from `.venv`, `pytest -q`, and an explicit workspace `--basetemp=docs/audit-2026-11-12/agent-pytest-temp`. The initial run encountered sandbox temporary-directory permission errors and was stopped; rerunning with workspace temporary files passed. Those initial errors are environmental, not product test failures.

`agent-probes/reproduce.py` reproduces semantic grounding, citation omission, forensic prose validation and historical admission gaps with fake evidence only. The separate synthetic budget probe recorded one fake call against a ceiling of one, appended a partial JSON line and observed `calls_this_month` fall from 1 to 0 with the next call allowed. Its synthetic files are in `agent-probes`; no live usage ledger was changed.

Limits: no live LLM calls, no claim of measured investigation quality, no full live-provider availability check, no financial advice or real-order action. Current local source and historical design records are evidence of implementation and recorded experiments, not proof of present market performance.
