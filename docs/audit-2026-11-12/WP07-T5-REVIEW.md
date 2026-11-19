# WP07 T5 independent review — 2026-11-15

Disposition: **T5 remains under review. Do not start T3 yet.** Five correction groups below affect the promised reader/provenance contract. The immutable-generation protocol is useful progress; passing fixture tests do not cover every integration path.

No application code changed. No environment files or credential values inspected, no live calls, no production data opened, and nothing committed or pushed. Review used named source files and temporary synthetic stores.

## Independent evidence and limits

- Focused Python: **49 passed in 15.28 seconds** across tests/test_snapshot.py, tests/test_snapshot_generations.py and tests/test_snapshot_provenance.py, with pytest cache disabled and a dedicated temporary basetemp.
- Additional synthetic probes reproduced the Python cleanup race, suppressed snapshot failure, damaged-generation publication skip and lock-path misclassification described below.
- Rust/API/UI paths were reviewed from source. A pure formatter probe by the review agent reproduced the registry date-shape and misleading updating label.
- The reported 226 Rust / 906 web / 3,074 Python broad results and cross-language results were not independently rerun. Latest Rust artifact scope includes two ignored cross-language tests; keep the separately orchestrated cross-language execution distinct.
- Mutation outputs were not retained by the implementer; their success remains reported, not independently verified.
- Test IDs reconstructed after a run are not an exact execution-time collection guarantee when files changed meanwhile. Preserve that qualification.

## T5-R1 — P2: Python Warehouse loses cleanup retries and explicit snapshot failures

Locations: src/athena/warehouse/warehouse.py:66–89, especially 81–84.

The Warehouse path calls generations.resolve and then duckdb.connect separately. Its retry only covers disappearance during resolve, not deletion after validation and before opening. It also suppresses SnapshotUnavailable and the snapshot's IOException, then raises the original main-file error.

Synthetic probe: prepare two valid generations, select the older pointer, resolve that generation, then advance to the newer pointer and remove the old data before connect. Fake only the main-file lock failure. With wait_seconds=0 the actual integration called resolve once and surfaced IOException: Conflicting lock is held by synthetic writer. It did not try the usable newer generation.

A second probe with a hash-mismatched snapshot again surfaced only the main lock error, losing hash_mismatch. Thus the generation utility's explicit reasons do not reliably reach Warehouse/chart callers.

Correction: share an open-validated-generation helper that handles validation-to-open disappearance with a bounded fresh-pointer retry. Preserve both primary failure and snapshot reason through the public integration while maintaining necessary exception compatibility.

Acceptance: integration-level tests through Warehouse and chart helper for disappearance with a usable replacement, disappearance without replacement, and each rejected snapshot reason. Assert the opened generation/data and the error payload, not only resolve behavior.

## T5-R2 — P2: unchanged-source shortcut preserves a damaged current generation

Location: src/athena/warehouse/generations.py:367–372.

publish resolves the existing generation with verify_hash=False and returns unchanged when its recorded source identity matches the main file. A current snapshot with same-sized corrupted content passes this shortcut forever until the source identity changes.

Synthetic probe: publish a valid temporary store; flip one byte in the generation while keeping its length; resolve reports hash_mismatch. publish returns unchanged, and resolve still reports hash_mismatch afterwards.

Correction: establish that the existing generation is usable before returning unchanged, using the validated hash cache where appropriate. If it is damaged and the source is usable, publish a fresh generation or return an explicit failure; never advertise unchanged success for known unusable fallback data.

Acceptance: same-sized damage plus unchanged source must produce a usable replacement or explicit failed result; a healthy unchanged generation must retain the fast path. Cover refresh wrapper behavior too.

## T5-R3 — P2: registry describes opens rather than all served/displayed answers

Locations: terminal/src/server.rs:161–166; terminal/src/snapshot.rs:424–447; terminal/src/search.rs:189–202; terminal/web/src/chartlab/view.ts:69–74.

Several connected propagation gaps remain:

- Successful default adjusted bars return helper provenance directly without registering it. /api/data-source can therefore omit the most common chart response, including a cached snapshot.
- Chart Lab discards the bars response's provenance. Combined with the missing registry entry, its snapshot fallback can be invisible.
- Registry keys are reader names such as bars rather than visible consumer identities. Two panels served different generations through the same reader overwrite one record and can escape mixed detection.
- Cache hits do not renew served-answer provenance. Search may record a refresh-open failure and then return its retained index, so the badge describes an unavailable source instead of the cached data actually served. Conversely a cache can keep serving an old generation after its registry entry no longer counts as recent.

Correction: carry provenance with cache entries and report the actual response at the serving boundary. Keep refresh failures separate from provenance of retained answers. Retain enough consumer/panel identity to compare simultaneously displayed data, or explicitly narrow the shell's claim. Make Chart Lab consume the response provenance.

Acceptance: default adjusted path, adjusted cache hit, two panels/symbols through the same reader with differing generations, cached data after refresh failure, and cache serving beyond the registry window. Exercise actual endpoint payloads and visible consumers rather than only raw adj=0 fixtures.

## T5-R4 — P2: shell date contract and fallback wording are wrong

Locations: terminal/src/snapshot.rs:348; terminal/web/src/provenance.ts:56,106.

Registry data_as_of is a per-dataset object such as {daily_bars: "2026-11-13"}, but readProvenance accepts only a string. A pure formatter probe produces a tooltip saying data to an unknown date despite a known manifest date.

The same formatter displays snapshot while updating even when fallback_reason is main_unreadable or main_missing. A snapshot is not proof an update is running.

Correction: handle the registry's actual per-dataset date shape, without inventing a single date where datasets differ. Use fallback-specific wording; reserve updating for an identified writer-lock condition. Preserve failed-publication status in the shell as well as panel notes.

Acceptance: registry-shaped payload fixtures with multiple dataset dates; missing and corrupt/unreadable primary; locked primary; failed replacement. Verify badge and tooltip text.

## T5-R5 — P2: lock classification matches substrings in file paths

Locations: src/athena/warehouse/generations.py:248–253; terminal/src/snapshot.rs:291–301.

Both classifiers search for the bare substring lock anywhere in the error text. DuckDB errors include paths, so a directory or filename containing clock, block or lock can make corruption or permission failures appear to be an active writer.

Synthetic Python probe: a corrupt temporary clock.duckdb and an error saying it is not a valid DuckDB database file were classified locked_by_writer. Rust uses the same substring rule by source inspection.

Correction: prefer structured error information if available; otherwise match specific lock-conflict phrases or OS sharing-violation indications, not arbitrary path substrings.

Acceptance: real-format lock messages plus corrupt/unreadable/missing fixtures whose paths contain clock/block/lock. Verify API reasons and UI wording together.

## Nonblocking qualifications

Keep documented: real large-store performance, real long writer/terminal behavior, deferred T3/T6/T8 and A6 integration, and the existing guard's coverage limits. Up to two generations is a nominal retention target, not a disk-space ceiling: deferred deletions and crash orphans can retain more files. A source/protocol manifest hash does not establish a company's underlying facts.

Correct these five groups and preserve machine-readable test and mutation evidence for the follow-up. No further full baseline rebuild is requested solely for this review; run affected regressions and broaden when justified by the fixes.


## Follow-up review — T5 corrections, 2026-11-15

**Disposition: R1, R2 and R5 accepted as fixed_offline_verified on synthetic fixtures. The primary R4 date/wording corrections are present. R3 remains incomplete; two remaining correction items below should be addressed before T5 closes or T3 starts.**

Independent Python run: **99 passed in 29.43 seconds**, covering test_snapshot.py, test_snapshot_generations.py, test_snapshot_provenance.py and test_snapshot_integration.py, using a dedicated temporary basetemp and no pytest cache. Reviewed the current Python reader/publication/classification paths and terminal API/cache/UI integration. Saved web JSON reports 934 passed / zero failed; that suite, Rust, broad Python and mutation runs were not independently rerun. The implementation report correctly distinguishes the 49 new cases in the broad run from the final integration file's 50 after the retention test was added.

Accepted corrections include bounded fresh-pointer retry across validation/open, structured WarehouseUnavailable failure reporting, chart-helper JSON failure output, repair of hash-damaged unchanged copies, default adjusted response registration, reader/subject keys, SearchCache served provenance, actual Rust/UI payload fixtures, dataset-specific dates and narrower lock phrases. The narrowed server claim (recently served subjects, not actual browser-panel tracking) and missing Chart Lab comparison-overlay provenance remain documented limitations.

### T5-F1 — P2: fundamentals price cache still loses served provenance

Location: terminal/src/fundamentals.rs:25–26,96–128; existing cache regression at 325–334.

The closes cache contains only main/WAL timestamps and prices. price() returns cache hits without registering a served answer. On refresh failure read_closes() calls snapshot::open with reader name screen, which records the failure; price() nevertheless returns the retained close.

The existing synthetic test closes_are_read_again_when_the_warehouse_changes_and_kept_when_it_cannot_be_opened exercises the retained-price scenario explicitly: it removes the warehouse file and still returns 3300.0. Source inspection establishes the registry mismatch; the test currently checks the price, not its provenance. This remains the same attempted-open-versus-served-data problem corrected for SearchCache, and is not listed among the implementer's deliberate exclusions.

Correction: retain the price source provenance in the cache; register the answer actually returned on cache hits and failed refreshes; keep refresh errors separate; use a distinct reader identity rather than screen, so it cannot overwrite the unrelated screen response. Carry price-date and cache-age information where needed.

Acceptance: cache initially populated from main and from snapshot; repeated cache hit; failed refresh with retained price; no retained price; differing screen and cached-price generations. Assert returned price and registry/UI provenance together.

### T5-F2 — P2: stale refresh failures remain current forever

Locations: terminal/src/snapshot.rs, Registry::summarize refresh_failures construction around 520–536; terminal/web/src/provenance.ts: readProvenance and badge refresh branch.

Served entries receive age_seconds and recent, but refresh_failures receive only at. readProvenance defaults omitted recent to true. The badge filters refresh failures by that default, so failures never leave its recent-data view unless explicitly cleared by a successful rebuild.

A pure formatter probe used no recent served entries, an empty recent_generations list, and refresh_failures.search.at=1. It still displayed data refresh failed and claimed the data being served was from an earlier build. No current served answer established that claim.

Correction: calculate age/recent for refresh records using the same clock/window, and associate retained-data wording with an actually recent matching served answer. Historical failures may remain available as history, but must not be presented as current serving activity. Do not merely hide an active failed refresh when old data is still being served.

Acceptance: old failure and no recent serve; old failure with renewed cached serving; current failure with retained data; recovery clears failure; expiry exactly at the window boundary. Test the actual Rust-generated payload through the shared UI fixtures.

### Evidence and limits

Review did not change application code, inspect environment files or credentials, use live providers/brokers, open production stores, commit or push. Only this review record was extended; synthetic review fixtures were temporary.

The hash-cache same-size/same-mtime limitation, real large-store/long-lock behavior, POSIX message capture, and comparison-series provenance remain qualified. They are not additional newly reproduced blockers in this follow-up. Preserve recorded green, red, invalid and mutation runs.


## Closure review — 2026-11-16

**T5-F1 and T5-F2 are closed as fixed_offline_verified within the documented synthetic-fixture scope. T5 may close; T3 is the next slice.**

Independent current verification: **52 passed** across terminal/web/test/provenance.test.ts (32) and datasource-fixtures.test.ts (20), using the installed Vitest runner, a scratch config with an empty variable-file directory, and a copy of guard v3 redirected only to this review's temporary artifacts. Full suites and mutations were not independently repeated. The previous independent 99-case Python pass remains historical evidence; no Python changed in this submitted follow-up.

Code review confirms:
- Fundamentals price cache keeps provenance, registers returned cached prices under its own reader name, separates failed refreshes from served answers, and clears failures after a good read. It no longer overwrites screen.
- Refresh records now receive reader, age_seconds and recent from the same window as served entries. The UI requires explicit recency and checks reader/time before claiming retained data is still being served.
- Shared Rust-generated fixture payloads exercise the corrected parser/formatter contract. Both original stale-refresh formatter cases are corrected.

No further significant closure blocker was found. This is not a claim that every endpoint, comparison overlay, or underlying market input is fully validated.

Retain explicit limits: the price reader describes the aggregate closes cache, has no symbol subject, and uses the latest close day in that cache. The returned price note retains its symbol-specific date. Do not present the aggregate date as proof that an individual symbol's price is current; any future per-symbol freshness gate must use that symbol's actual date. Reader-name matching and second-resolution timestamps do not prove exact causal ordering. Retain Chart Lab comparison-overlay provenance, same-size/same-mtime hash-cache behavior, real large-store/long-lock testing, POSIX lock capture, and guard coverage qualifications.

Independent UI result and command are retained under baselines/wp07-t5f-2026-11-16/reviewer-20261116/. The implementer's reported 252 Rust / 952 web counts and mutation results were reviewed as saved/reported evidence, not independently rerun here.

No application code changed, environment files or credential values inspected, live calls made, production stores opened, commits or pushes performed. Only review records and disposable synthetic verification artifacts were written.
