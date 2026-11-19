# WP07 T3 independent review — 2026-11-16

**Disposition: keep T3 open; T6/T8 remain paused.** Six correction items follow. Quality rules are useful progress, but passing tests do not establish the advertised timestamp semantics or all composition boundaries.

## Verification

Independent focused web run: **114 passed**, five files: optionquality.test.ts (29), assemble.test.ts (13), optionstats.test.ts (38), optionslive.test.ts (16), optionsview.test.ts (18). Installed Vitest, separate scratch config/cache/log, empty variable-file directory and guard-v3 copy redirected to review artifacts. Full Rust/web suites and mutations were not independently rerun.

Additional pure probes used Node's built-in TypeScript stripping on temporary copies of named source modules. No application code changed, credentials/environment files inspected, production stores opened, commits or pushes made. No live market/model/broker API calls. Public read-only verification of the broker's official SDK was used to establish packet field meanings.

Saved independent results/probe JSON and command are under baselines/wp07-t3-2026-11-16/reviewer-20261116/.

## T3-R1 — P2: the field labelled last-trade time is the exchange packet timestamp

Locations: terminal/src/angel/decode.rs:70; terminal/src/deriv_quotes.rs:38–40,85; terminal/web/src/options/livefeed.ts:68–78.

The decoder reads exchange_ms at bytes 35–43, then DerivQuote and Quote call it the time of the last trade. The actual last-traded timestamp is not decoded.

Primary source: [Angel One's official SmartAPI Python decoder](https://github.com/angel-one/smartapi-python/blob/main/SmartApi/smartWebSocketV2.py), _parse_binary_data: exchange_timestamp at 35–43, last_traded_timestamp at 123–131, and sequence_number at 27–35. These are distinct protocol fields. The repository's src/athena/live/angel.py also treats exchange_timestamp and last_traded_timestamp separately.

Impact: the advertised trade/book/receipt separation is not actually implemented. Trade age and the description of delivery_delay_ms are based on a different event. A new exchange packet need not imply a new trade.

Correction: decode and carry distinct packet/event and last-trade times, verify their units, and use the appropriate field for ordering, delivery lag and trade age. Keep receipt-based book freshness explicitly labelled; do not invent an exchange book timestamp. Version the policy/wire semantics as needed without breaking older clients silently.

Acceptance: a binary fixture with different packet and trade times; book-only update with no new trade; trade update; unavailable trade timestamp; unit conversion and malformed/future stamps. Exercise decoder -> store -> API -> frontend, not only handcrafted frontend quotes.

## T3-R2 — P2: missing timestamps erase the ordering watermark

Location: terminal/src/deriv_quotes.rs:186–208.

Older packets are refused only if both incoming and held timestamps are Some. A timestamp-less packet replaces the held exchange_ms with None, so a later stale timestamp is no longer compared with the greatest known one.

Source-level sequence: known packet time 200 -> packet with no time -> stale packet time 100. Use valid epoch-sized values in the regression. The last packet is accepted and can replace book content and stamp book_seen_ms as current.

Correction: maintain an ordering watermark independent of the nullable current payload field. Define timestamp-less-update handling explicitly and use the protocol sequence where appropriate, including reconnect/reset semantics.

Acceptance: known -> missing -> older; known -> missing -> newer; duplicates; same event time with distinct sequence; reconnect. Assert content, receipt/book times and discard count.

## T3-R3 — P2: stale timestamp advances can create a live session; clock basis is inconsistent

Location: terminal/web/src/options/livefeed.ts:117–120,146–149,192–195.

note() marks advancedAt=now for any newly observed larger timestamp, even if it is still yesterday. session() and badge() then trust the observation for two minutes. They also compare against the browser clock, despite tracking server clock skew elsewhere.

Pure fake-fetch probe against the actual livefeed module:
- first quote t=now-24h -> last_session / last session prices;
- next quote t increases by 1 second, remains yesterday -> live / live prices.

This can also happen when a page switches from older cached contracts to slightly newer cached contracts. Advancing observed historical data does not establish current market activity. Downstream IV checks can still withhold some stale inputs; the feed/session assertion itself is wrong and changes the age reference.

Correction: one explicit server-clock/session policy must distinguish current activity from stale replay, cached contract changes and genuine clock skew. Use packet/event receipt/delivery evidence appropriately after R1; do not simply replace a legitimate fresh book with a last-trade-age test.

Acceptance: stale advancing packets, expiry/underlying switch, repeated identical packets, reconnect, browser clock ahead/behind server, fresh book with old last trade, and a stopped feed. Test session, feed badge and assembled page together.

## T3-R4 — P2: forward construction drops input labels and does not check input synchronisation

Locations: terminal/web/src/options/assemble.ts:91–108; quality.ts:272–291.

Labelled futures/parity quotes are usable, but decideForward receives their raw quotes and only checks oldest time/age. It never carries their quote-quality flags forward or tests the span between its own inputs.

Full assemble probe: future has wide_book, delayed_feed and out_of_order_discarded; ATM call/put are clean. The future's own assessment is labelled, but the selected forward has state ok, flags [], and chain status ok. The label disappears from a required input.

A second probe passes parity inputs ten minutes apart (both under the 15-minute age limit). decideForward returns state ok with no forward_unsynced. Comparing options later with only the oldest forward timestamp is not validation of the inputs used to calculate that forward.

Correction: aggregate input decisions into the selected forward, preserving labels/consequences and provenance. Validate synchronisation across all contributing parity inputs before treating the derived forward as fit. Record rejection/fallback reasons for every candidate actually evaluated.

Acceptance: wide/delayed/out-of-order future; labelled parity contributor; unsynchronised call/put and multi-strike contributors; required chain badge and IV/Greeks labels. No input warning may disappear merely because an input became a derived forward.

## T3-R5 — P2: absent required ATM sides disappear from the quality decision

Location: terminal/web/src/options/quality.ts:329–333; assemble.ts:140–143.

chainQuality adds atm_call/atm_put only when their decisions are non-null. The stated contract requires forward plus both ATM sides.

Full assemble probe: valid future, one structural ATM call and no put contract. Chain status is ok and required contains only forward and atm_call. A required input was omitted rather than reported missing.

Correction: represent missing required inputs explicitly and apply a named consequence. If a one-sided chain is intentionally supported, label the limited headline coverage instead of claiming the normal required set passed. Empty-chain and no-ATM cases need explicit states too.

Acceptance: missing call, missing put, missing quote for an existing contract, empty strikes, no ATM, and complete pair. Check required input list and visible badge, not only available IVs.

## T3-R6 — P2: future-dated times are counted as fresh

Location: terminal/web/src/options/quality.ts:174–196,277–287.

Only excessive positive age is rejected. A book time one hour after the decision gives bookAgeMs=-3600000, state ok and flags []. A future forward time is accepted similarly.

Correction: define timestamp-validity and allowed-skew policy separately from age. Beyond any explicitly allowed tolerance, mark future times unknown/invalid and label or withhold as declared; never silently call them fresh. Record the invalid value and reason. Do not confuse delivery-delay clock skew with successful freshness validation.

Acceptance: future bookTime, bookSeenAt, trade time and forward inputs, invalid/nonfinite values, allowed skew boundary and server clock rollback. Verify the chain cannot report ordinary live/fit status through negative ages.

## Evidence qualifications

The reported permissive-stub web red run is useful evidence that new assertions need the feature, but is not an exact reproduction of every defect in the pre-change implementation. Its TypeScript errors should remain separate from behavioral failures, as the report acknowledges.

Policy thresholds remain uncalibrated on real market data; quiet standing books versus stale books, last-session display, real feed timing and the unrun Playwright expectation retain their stated limits. The six findings concern implemented correctness, not a demand for live validation before offline completion.


## Policy-v2 follow-up review — 2026-11-16

**Disposition: keep T3 open for the remaining invalid-time consequence gap and verification-harness correction below.** The decoder field separation, persistent ordering watermark, server-clock session/replay rule, forward input composition and missing-ATM representation address the original R1–R5 findings within their stated fixture scope. R6 is only partially addressed.

### Independent verification

**159 focused tests passed**, seven files: optionquality.test.ts (29), optiontimes.test.ts (27), optionsession.test.ts (18), assemble.test.ts (13), optionstats.test.ts (38), optionslive.test.ts (16), optionsview.test.ts (18). Installed runner, separate scratch config/cache and copied guard v3 with independent logging, empty variable-file directory. No full Rust/web rerun or application-source mutation.

Pure probes used temporary stripped copies of named TS modules. A separate probe imported the saved mutation runner and mutated only an inert temporary text file, with synthetic child output, not application code.

The new saved mutation logs were checked for assertion evidence:
- 20261116-131652-mutation-web: 36 caught logs, all contain assertion-failure markers;
- 20261116-133324-mutation-web: 6 caught, same;
- 20261116-133401-mutation-web: 7 caught, same;
- 20261116-133413-mutation-web: 11 caught, same;
- 20261116-133456-mutation-web: 20 caught, same.

This supports treating those reruns differently from the earlier empty/load-failure logs. It does not turn the classifier into a reliable proof or independently establish every mutant's causal validity. Six missing patterns remain pattern errors, not caught mutants.

### T3-C1 — P2: invalid receipt/packet timestamps are recorded but can remain unlabelled and fit

Location: terminal/web/src/options/quality.ts:319–325,370.

checkTime identifies invalid receivedAt and packetTime, and the server's tf refusal is preserved. Only book and trade failures have effective consequences, however. Receipt faults, packet faults and server-refused packet timestamps can leave flags empty and state ok.

Independent pure probes, with otherwise valid two-sided book and a live session:
- receivedAt one hour ahead -> state ok, flags [], timeFaults contains future receipt;
- packetTime one hour ahead -> state ok, flags [], timeFaults contains future packet;
- packetTime absent because the server supplied tf=[{f:'t', v:<future>, why:'future'}] -> state ok, flags [], server fault recorded;
- session unknown with reason no_receipts, otherwise valid book -> state ok, flags [] (only no_event_time currently produces session_unverified).

A valid quote on another contract may make the aggregate session live; it must not erase this contract's own invalid metadata. Recording a fault alone does not satisfy the claim that invalid times prevent ordinary fit/live status. Valid book data need not necessarily be discarded, but the per-field consequence must be declared and actually applied.

Correction: define consequences for invalid receipt and packet timestamps, including server-origin refusals. Apply them before forward/chain aggregation. Label all genuinely unverified session reasons appropriately, or explicitly justify a narrower policy without claiming unknown session evidence is verified. Preserve old-trade/fresh-book behavior.

Acceptance: the four probes above, plus mixed chains with one valid contract and an invalid future/option. Assert row flags, forward labels, required-input status and visible badge; inspecting timeFaults alone is insufficient. A malformed unrelated timestamp should not silently become a no-warning ok decision.

### T3-C2 — P2: the mutation classifier still counts suite-load errors as caught

Location: baselines/wp07-t3r-2026-11-16/scripts/mutate.py:66–73.

The unmutated precheck catches an initially broken harness. It does not protect against a mutation that breaks test loading. FAIL_PATTERNS.vitest accepts a suite-level line such as FAIL test/synthetic.test.ts as a failing test. BUILD_ERRORS is ignored whenever that list is non-empty, and other non-zero exits also become caught.

Synthetic run_one probe, inert file only, child stdout:
    FAIL test/synthetic.test.ts
    SyntaxError: deliberate synthetic load failure
    Tests no tests
exit 1 -> outcome caught, failing ["test/synthetic.test.ts"].

Correction: validate each mutated execution's machine-readable result. A caught mutant requires an executed assertion failure attributable to the intended tests. Build/transform/import/setup errors, no-test runs, missing reports and unrelated process exits must be invalid or separately classified; a suite-level FAIL line is not assertion evidence. Validate baseline execution/counts likewise, and baseline any per-mutation command override. Retain logs even on decoding errors.

Acceptance: passing baseline plus mutated syntax/transform/import/setup error, no tests, missing report, unexplained non-zero exit, timeout, real assertion failure and a surviving mutant. A mixed run with assertions plus suite-load failures must not be described as an unqualified caught mutation.

Do not invalidate all replacement results merely because this flaw exists: the named saved logs above contain assertion evidence. Reclassify from saved reports/logs where possible and rerun only ambiguous cases and newly added harness checks.

### T5-F survivor decision

Fix the missing cache-age assertion now, using a controlled clock/known cache age and a regression that kills ts-retained-data-not-described. It is a narrow coverage correction to a closed slice, not permission for a wider T5 rewrite. Keep T5's functional closure unless that exercise reveals a functional defect. Report the earlier web mutation results as invalid/superseded, the current survivor honestly, and the eventual rerun separately.

No application code changed, production data or environment files/credentials accessed, live broker/model calls made, commits or pushes performed. T6/T8 remain paused. Review results and probes are retained under baselines/wp07-t3r-2026-11-16/reviewer-20261116/.


## Policy-v3 closure review — 2026-11-16

**Disposition: T3-C1 and T3-C2 are closed as fixed_offline_verified in the reviewed scope. T3 may close on synthetic fixtures. The T5-F cache-age coverage gap is closed; T5's functional closure stands.**

Independent current checks:
- **236 web tests passed**, ten files: optionquality.test.ts (29), optiontimes.test.ts (27), optionsession.test.ts (18), optioninvalidtimes.test.ts (24), assemble.test.ts (13), optionstats.test.ts (38), optionslive.test.ts (16), optionsview.test.ts (18), provenance.test.ts (33), datasource-fixtures.test.ts (20).
- **16 mutation-harness self-tests passed in 10.44 seconds**, using inert temporary target files and fake runner reports. These tests do not mutate application code.
- Current focused web run used installed Vitest, an empty variable-file directory, separate scratch config/cache and copied guard v3 redirected to this review's own artifacts.
- Full web, Rust, real-Vitest mutation self-test and actual application mutation runs were reviewed as saved/reported evidence, not independently repeated in this closure run. Python application tests were not repeated because this submitted change has no Python application edits.

### Accepted policy choice and implementation

Label rather than withhold is accepted for invalid receipt/packet metadata when a separately valid book timestamp remains available and the book passes its own rules. This is a research-display policy, not certification that a quote is executable. An invalid or stale book still withholds. The policy must remain versioned; real calibration and feed validation are pending.

POLICY_V3 declares invalid_receipt_time and invalid_packet_time as labels, including server-origin refusals and unrecognised refused fields. Every unknown session state gets session_unverified. The per-contract flags survive forward input composition and the required-input badge. Valid unrelated contracts cannot erase them. A wing outside the headline's required inputs can remain locally labelled without changing the headline; its cell warning and counts must remain visible.

### Verification-harness assessment

The former suite-level FAIL / SyntaxError / no-tests false-positive path is covered by self-tests. The v2 harness now reads per-execution reports, distinguishes assertion failures from load/errors/no-test/inconsistent executions, compares against clean baseline tests, and baselines command overrides independently. Raw output is preserved. Mixed/error-only/invalid/timeout/unattributed outcomes remain separate from caught.

Stable-libtest text cannot reliably distinguish every custom assert message from panic/expect. The current conservative error_only classification is an explicit limitation, not evidence that a mutant survived. Using an assertion with recognisable output for the stream regression establishes the intended check without changing application behavior. The harness can show baseline-passing assertions rejected a mutant; it cannot establish that all mutations are meaningful, complete or representative.

Saved old INVALID mutation runs stay invalid. Replacement and reclassified runs must stay separately attributed. No prior percentage should be retrospectively described as originally reliable.

### T5-F cache-age regression

The new test isolates the retained-data clause rather than finding cache ages elsewhere in the tooltip. It checks controlled ages, multiple answers and the non-hit case. The reported formerly surviving mutant is now caught, with adjacent mutants. No functional defect was exposed; no wider T5 reopening is needed.

### Qualifications and next slice

No additional significant closure blocker was found. Retain uncalibrated thresholds, magnitude-based trade-time unit interpretation, assumed sequence behavior and tolerated future packet watermark effects; quiet standing book ambiguity; inability to separate shut market from stopped feed; no real stream/browser/Playwright validation; and the guard's non-sandbox limitations.

T6 (bounded queue/deadline/cancellation work) is next. T8 remains separate. T3 closure does not close remaining A6 dataset freshness integration, WP08–WP10, provenance follow-ups or live/human validation.

Evidence: baselines/wp07-t3r-2026-11-16/reviewer-20261116-v3/. No application code changed, environment files or credentials inspected, production data opened, live calls made, commits or pushes performed. Only review records and disposable synthetic verification artifacts were written.
