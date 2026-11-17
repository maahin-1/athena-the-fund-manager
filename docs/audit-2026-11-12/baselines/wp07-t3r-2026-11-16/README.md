# WP07 T3 review corrections (T3-R1 to T3-R6): verification artifacts (16 November 2026)

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Evidence for [../../WP07-T3-CORRECTIONS.md](../../WP07-T3-CORRECTIONS.md) and [../../IMPLEMENTATION-STATUS.md](../../IMPLEMENTATION-STATUS.md). Every run has its **own directory** under `runs\`, named `<yyyymmdd-HHMMSS>-<label>`; **nothing is overwritten, red, not-green and invalid runs included**. Synthetic fixtures and controlled clocks only; no production `.athena` data, environment file, live call, commit or push. The scripts are copies of those of [../wp07-t3-2026-11-16/scripts/](../wp07-t3-2026-11-16/scripts/), with these changes (the earlier directories are untouched): `run_rust.ps1` also copies and, with `-UpdateFixtures`, regenerates `terminal/web/test/fixtures/deriv-quotes.cases.json`; `mutate.py` decodes the test output as UTF-8 (see "A flaw found in the mutation runner" below). New specs: `mutations-rust-t3r.json`, `mutations-web-t3r.template.json`.

**Machine-readable outputs:** Rust `rust-results.json` and `rust-test-ids.txt`; web `web-results.json` (vitest JSON) and `web-test-ids.txt`; mutations `mutations.jsonl`, `summary.json`, `spec.json`, `logs\<id>.log`. Each run keeps `command.txt`, `git-head.txt`, `git-status*.txt`, toolchain versions and (Rust) the isolated-copy audit and source manifest.

**Code state:** commit `37f1fc19846a178083d9ced7a7a25aeffe65b1f9` with the audit work uncommitted in the working tree; no `Cargo.toml` or `Cargo.lock` change.

## Runs

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261116-124545-rust-red` | Rust suite with the new regressions **before** the implementation (the new fields exist as stubs that read nothing, so the tests compile) | 262 passed, **13 failed**: the two decoder tests, the store tests for R1, R2 and R6, the fixture test (no fixture yet) and the route test | red evidence (valid) |
| `20261116-124945-web-red` | The new web tests against the code **as it was** (no stub; vitest does not type-check, tsc reports the missing types separately: `tsc exit 1`) | 43 tests: **41 failed, 2 passed** (a repeated packet is not new activity; a feed that stops reads as the last session: both already held). The fixture was read with `readFileSync` in this run (5 guard `fs.readFileSync.outside` records, none denied); the tests were changed to import the JSON afterwards | red evidence (valid) |
| `20261116-125153-rust-green-1-update-fixture` | Full Rust suite after the implementation | 273 passed, **2 failed**: two of my tests used packet times ahead of their own test clock (the store correctly refused them as future) | **NOT GREEN**, kept; fixed in the tests |
| `20261116-125407-rust-green-2-update-fixture` | Same, with `-UpdateFixtures` (the Rust tests write `deriv-quotes.cases.json`; `data-source.cases.json` was rewritten identical, hash unchanged) | **275 passed, 0 failed, 2 ignored** | yes |
| `20261116-125731-web-green-1` | Full web suite after the implementation | 1013 passed, **18 failed**: the older tests that asserted the defects (listed in the corrections document) and one test of mine that dropped the wrong strike; `tsc exit 1` (the old test's `POLICY_V1`) | **NOT GREEN**, kept |
| `20261116-125943-web-green-2` | Same, after the intended changes | **1031 passed**, tsc 0 | yes |
| `20261116-130046-rust-green-3-verify` | Full Rust suite, no regeneration (the fixtures must equal what the Rust code produces) | **275 passed, 0 failed, 2 ignored**; no compiler warning | yes |
| `20261116-130206-web-green-3` | Full web suite after removing an unused guard in `livefeed.ts` | **1031 passed**, tsc 0 | yes |
| `20261116-130157-mutation-rust` | 20 Rust mutations on the isolated copy | 19 caught, **1 SURVIVED** (`rs-future-limit-off-by-one`: my test took its boundary from the constant it was checking) | kept, marked; the test was fixed |
| `20261116-130259-mutation-web` | 37 web mutations, **first run** | all "caught" by exit code, **logs empty**: the runner lost the output (code-page decoding) | **INVALID as evidence**, kept (see below: the same command fails to load for another reason) |
| `20261116-131141-mutation-web` | The same 37 with the output decoded | all "caught", but every log shows **all five test files failing at load** ("Vitest failed to find the runner"): the spec's lower-case drive letter in `cwd` | **INVALID as evidence**, kept |
| `20261116-131053-web-green-4` | Full web suite after adding the policy-limits test | **1032 passed**, tsc 0; guard: 142 denied `fetch`, 0 other | yes; **not a zero-violation run** |
| `20261116-131103-rust-green-4-verify` | Full Rust suite after the boundary-test fix | **275 passed, 0 failed, 2 ignored**; no compiler warning | yes |
| `20261116-131205-mutation-rust-r2` | The survivor rerun with two neighbouring boundary mutants | 4 of 4 caught | yes |
| `20261116-131652-mutation-web` | 37 web mutations with the corrected harness (baseline checked, `cwd` with an upper-case drive letter, output decoded) | 36 caught on named tests, **1 SURVIVED** (`ts-forward-unfit-input-ignored`: no test pinned `forward_input_withheld`) | yes (valid harness); survivor kept |
| `20261116-131953-web-green-5` | Full web suite after adding the test for that survivor | **1033 passed**, tsc 0; guard: 142 denied `fetch`, 0 other | yes; **not a zero-violation run** |
| `20261116-133324-mutation-web` | The survivor again, plus 5 further forward mutants (stale, age unknown, newest-input date, unfit input, session label as input label) | **6 of 6 caught** | yes |
| `20261116-133401-mutation-web` | **T5-F web spec** (8 mutants on `provenance.ts`) rerun with the corrected harness | 7 caught, **1 SURVIVED** (`ts-retained-data-not-described`: the cache age in the retained-data wording is not asserted by any test). T5 is closed; **not fixed here**, reported | yes |
| `20261116-133413-mutation-web` | **T5-R web spec** (11 mutants) rerun with the corrected harness | **11 of 11 caught** | yes |
| `20261116-133456-mutation-web` | The **first T3 report's web spec** (26 mutants) rerun on the current code | 20 caught, 0 survived, **6 pattern errors** (the code those patterns named was rewritten; they are covered by the new spec) | yes |

## T3-C1, T3-C2 and the T5-F assertion (follow-up runs)

Same rules: every run has its own directory, nothing overwritten, not-green and invalid runs kept. Record: [../../WP07-T3-CORRECTIONS.md](../../WP07-T3-CORRECTIONS.md), "Follow-up T3-C1 and T3-C2". No Rust application code changed (one Rust test in `angel/stream.rs` did); no production data, environment file, live call, commit or push.

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261116-135039-web-red-c1` | New file `optioninvalidtimes.test.ts` against the policy with only the constant renamed (`POLICY_V3`, version still 2) | 24 tests, **21 failed**, 3 passed (two controls, the old-trade rule); tsc 0 | red evidence |
| `20261116-135123-web-c1-impl-1` | Full web suite after the implementation | 1047 passed, **10 failed**: 3 page tests of mine used the wrong at-the-money strike (the page's future is 24,230, so the strike is 24,250) and 7 older assertions of the old behaviour (policy version 2, a refused packet time "recorded only") | **NOT GREEN**, kept; tests fixed, the old assertions changed on purpose and marked |
| `20261116-135254-web-c1-green` | Full web suite | **1057 passed**, tsc 0; guard 142 denied `fetch`, 0 other | yes; **not a zero-violation run** |
| `20261116-135400-web-t5f-cache-age` | `provenance.test.ts` and `datasource-fixtures.test.ts` with the new cache-age regression | 53 passed, tsc 0 | yes |
| `20261116-135837-harness-v1-probe-red` | The review's synthetic probe (suite-level `FAIL`, `SyntaxError`, `Tests no tests`, exit 1) through the **old** classifier `mutate.v1.py`, inert file | `caught` with failing `["test/synthetic.test.ts"]`: the defect | red evidence |
| `20261116-135859-reclassify-saved` | `reclassify.py` over the saved web runs `131652`, `133324`, `133401`, `133413`, `133456` and Rust runs `130157`, `131205`, from their saved logs, nothing executed | web: **all 36 + 6 + 7 + 11 + 20 catches confirmed**, the 2 survivors confirmed, 6 pattern errors unchanged, no rerun needed; Rust: no saved baseline log, **unconfirmed, rerun** | yes (`reclassification.json`, `.md`) |
| `20261116-135916-mutation-rust-v2-r2` | First rerun of the 4-mutant Rust spec on the v2 harness | all 4 `invalid`: the libtest parser read cargo's closing `error: test failed, to rerun pass …` as a build error | **parser bug**, kept; fixed with a regression test |
| `20261116-140151-mutation-rust-v2-r2` | Same spec after the fix | **4 of 4 caught** (assertion failure in the named test) | yes |
| `20261116-140403-mutation-rust-v2` | The 20-mutant Rust spec on the v2 harness | 19 caught, **1 `error_only`**: `rs-stream-does-not-reset` was killed by a 10 s timeout `.expect(...)`, not by an assertion | yes; the one non-caught result kept, test changed |
| `20261116-141356-rust-c2-verify` | Full Rust suite after the stream test used `assert!(waited.is_ok(), "…")` | 275 passed, 0 failed, 2 ignored | yes |
| `20261116-141458-mutation-rust-v2-stream` | The stream mutant again | still **`error_only`**: libtest prints only the message for `assert!(c, "msg")`, indistinguishable from `panic!` (a limit of the text parser, documented) | yes; kept |
| `20261116-141544-rust-c2-verify-2` | Full Rust suite after the test used `assert_eq!(waited.is_ok(), true, "…")` | **275 passed, 0 failed, 2 ignored** | yes |
| `20261116-141622-mutation-rust-v2-stream` | The stream mutant again | **caught** (1 assertion failure in the named test) | yes |
| `20261116-140441-mutation-web` | 20 web mutants on the v2 harness: 16 on the C1 rules, 4 on the T5-F clause (the earlier survivor included; the T5-F mutants use their own `command`, so their own baseline) | **20 of 20 caught**, each with assertion failures in named tests | yes |
| `20261116-140725-mutation-web` | **Harness self-test on real vitest** (5 mutants of `quality.ts`) | syntax error → `invalid` (suites did not load); unresolvable import → `invalid`; comment-only change → `SURVIVED`; throwing code → `error_only` (54 failed, none by an assertion); changed limit → `caught` (34 assertion failures); the script exits 1 by design | yes (expected mix) |
| `20261116-140748-web-c1c2-final` | Full web suite after all mutation runs (sources restored) | **1058 passed**, 76 files, tsc 0; guard 142 denied `fetch`, 0 other | yes; **not a zero-violation run** |

Harness self-tests (`scripts/test_mutate.py`, `python -m pytest`): 16 passed (run before the Rust reruns; no run directory, the count is in the corrections record). `mutate.v1.py` is the script that produced every earlier run (SHA-256 `0CAAAA00FF2B0A5D1B9169F49DCA8CE921572BAA2EE82C74A7E6F68AFD1ED442`); `mutate.py` is version 2.

## T6 runs (bounded queued work)

Record: [../../WP07-T6-BOUNDED-WORK.md](../../WP07-T6-BOUNDED-WORK.md). Rust only (no web change). Same rules: own directory per run, nothing overwritten. The python-based tests run for real in these runs (`run_rust.ps1` links the repository's `.venv` into the isolated copy for the run and removes the link afterwards: a read of the interpreter, nothing else). Machine-readable: `rust-results.json`, `rust-test-ids.txt`, `rust-summary.txt`, `exit-codes.json`; mutations `mutations.jsonl`, `summary.json`, `reports/`, `logs/`.

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261116-143323-rust-t6-red` | The 33 new regressions against a **stub** (types and signatures that implement no bound, deadline or cancellation); everything compiles | 275 passed, **33 failed** (all new: 25 `bounded`, 3 analysis, 2 helper, 3 server) | red evidence |
| `20261116-143900-rust-t6-green-1` | After the implementation | **308 passed**, 0 failed, 2 ignored; 2 compiler warnings (an unused import, a never-read field) | yes; warnings fixed after |
| `20261116-144056-rust-t6-green-2` | After the two fixes | 308 passed, 0 warnings in the test build | yes |
| `20261116-151725-rust-t6-green-3` | After marking test-only items `cfg(test)` (a non-test `cargo check` had listed 5 warnings in the new code; the other 9 in that build are in `snapshot.rs` and earlier code, not touched) | 308 passed, 0 warnings | yes |
| `cargo check` on the isolated copy (output `C:\Users\LEGION\ath-wp07\check.txt`, not a run directory) | non-test build | exit 0; 14 warnings, 5 of them in the new code (fixed in green-3), 9 earlier (`snapshot.rs` and others) | context |
| `20261116-151855-mutation-rust-t6` (first run) | 25 mutants on `bounded.rs`, `analyse_worker.rs`, `helper.rs`, `server.rs`, mutate.py v2 | 20 caught, **3 survived** (`t6-start-deadline-misnamed`, `t6-start-ignores-deadline`, `t6-enqueue-no-sweep`), **1 `error_only`** (`t6-running-cancel-missing`: killed only by the test's own timeout bound), **1 `invalid`** (`t6-queued-cancel-missing`: the mutant did not compile) | kept; the survivors led to the changes below |
| `20261116-153355-rust-t6-green-4` | After the changes: a never-ready fake child (the deadline arm during start), a Core test for a place held by an expired job, a Core test for cancelling a picked-up job, the redundant `min(remaining)` on the start limit removed (the deadline is a branch of the select; `t6-start-ignores-deadline` was redundant code, not a gap) | **310 passed** | yes |
| `20261116-153456-mutation-rust-t6-r2` | The 5 survivors / invalid / error_only mutants again | 3 caught, **2 `error_only`** (the tests' key claims used `assert!(…, "msg")` and `.expect`, which libtest prints like a plain panic) | kept |
| `20261116-153803-rust-t6-green-5` | After turning those claims into `assert_eq!` | **310 passed** | yes |
| `20261116-153854-mutation-rust-t6-r3` | The same 5 mutants | **5 of 5 caught** | yes |
| `20261116-154138-mutation-rust-t6-final` | **The whole set once more on the final code** (25 mutants: the 20 first-run catches, the 3 fixed survivors, the 2 replaced mutants) | **25 of 25 caught**, each with an assertion failure in a named test | yes |
| `20261116-155707-rust-t6-verify-a`, `20261116-155758-rust-t6-verify-b` | two further full runs (the tests with real children use real 1.2 s timers) | 310 passed each | yes |

The spec files: `mutations-rust-t6.json` (first), `mutations-rust-t6-r2.json` (the five), `mutations-rust-t6-final.json` (final set).

## T6 review runs (T6-R1 to T6-R4)

Record: [../../WP07-T6-BOUNDED-WORK.md](../../WP07-T6-BOUNDED-WORK.md), last section. Rust only (no web change). Same rules: own directory per run, nothing overwritten, nothing deleted (an aborted run is kept and said to be aborted).

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261116-190322-rust-t6r-red` | The new regressions against an **inert scaffold** (the new types and signatures, no behaviour: kinds ignored, `pid` absent, `shutdown` and the journal do nothing, `Admission` has no bound, `HelperResult` maps every failure to one variant) | **Aborted by hand** (no results file): `bars::join_tests::a_full_admission_refuses_the_adjusted_fetch_at_once_without_running_anything` never finished (the scaffold's `admit` waits for a place without bound). A hang is a failure of that regression, but the run could not end | kept, **not** the red evidence |
| `20261116-191009-rust-t6r-red-2` | The same scaffold sources (kept in `scaffold-src/`) with **one change**: the scaffold's wait for a place is bounded by the caller's budget, so that regression fails instead of hanging. Run in the isolated copy that run 1 had made (`README.txt` in the directory says so) | **315 passed, 34 failed** (20 `bounded`, 4 `fundamentals`, 5 `helper`, 3 `bars`, 2 `server`); 2 ignored. The new tests that already passed against the scaffold (it had the error mappings and the refusal arithmetic): `a_waiter_is_admitted_when_a_place_frees_…`, `server::a_failure_with_an_unconfirmed_worker_and_a_closed_service_have_their_own_answers`, and the three board tests below | red evidence |
| (in red-2) | **Three new `flow_board` tests passed against the scaffold without proving anything**: they used `sh` for the board module and this machine has none, so they returned early like the earlier board tests. They were rewritten to use Python (the same stand-in as the other tests) afterwards and their mutants are in the mutation runs below | | context |
| `20261116-191330-rust-t6r-green-1` | After the implementation | **349 passed**, 0 failed, 2 ignored | yes |
| `20261116-191525-rust-t6r-green-2` | Plus one more fundamentals regression (a request that waits past its deadline for a place) | **350 passed** | yes |
| `20261116-191645-mutation-rust-t6r-core` | First start of the core mutants | **Aborted by hand** after 2 mutants: a refactor (below) changed the code, so the run would not have tested the final tree | kept, not evidence |
| `20261116-192042-rust-t6r-green-3` | After folding the "may the command run again?" decision into one function (`HelperResult::next`, used by the three callers) and the two stop-to-error mappings into unit-tested functions | **352 passed**, 0 failed, 2 ignored | yes |
| `20261116-192159-mutation-rust-t6r-core-2` | 31 mutants on `bounded.rs` (stop-first checks, shutdown, failure ordering, identity, overrides, journal and restart, per-kind telemetry, admission, budget), mutate.py v2, on the green-3 tree | **30 caught, 1 survived** (`t6r-presend-deadline-ignored`: **equivalent under the paused clock**, because the biased select ends the wait at the deadline before the code just before the send is reached; killed below by a real-clock test) | kept |
| `20261116-193906-mutation-rust-t6r-callers` | The 30 caller mutants | **INVALID** (`INVALID.txt`): the harness's baseline on the unmutated tree failed 2 tests, `server::tests::a_daily_answer_from_a_snapshot_names_…` and `…the_data_source_endpoint_reports_each_readers_last_answer_…`, which share a process-wide registry and **race when the server tests are run as a subset** (the full suite, which is what every green run above is, passes them; an earlier-written note in `server.rs` says the same of the `bars` entry). Not caused by this change | kept, not evidence |
| `20261116-194301-mutation-rust-t6r-callers-2` | The same spec with the server filter narrowed to this change's tests | **Stopped by hand after 2 mutants** (not evidence): a mutant that should have been caught (`t6r-helper-refused-as-failed`) "survived" because the tests with **real Python children return early when the interpreter is not found**, and `run_rust.ps1` removes the `.venv` link when it finishes, so the isolated copy had none. By the script's own logic the earlier T6 Rust mutation runs had the same property for their real-child tests (their `bounded.rs` mutants were killed by fake-child tests, which do not need the interpreter); the harness cannot see a skipped test. From here the interpreter is linked in for the mutation runs that need it, as for the test runs | kept, not evidence |
| `20261116-194604-rust-t6r-green-4` | After the real-clock regression `a_deadline_that_passes_while_the_child_becomes_ready_stops_the_request_from_being_sent` (and a fresh isolated copy, which the interrupted mutation run had left mutated) | **353 passed**, 0 failed, 2 ignored | yes |
| `20261116-194753-mutation-rust-t6r-core-r2` | The surviving core mutant `t6r-presend-deadline-ignored` against the real-clock regression (`mutations-rust-t6r-core-r2.json`) | **caught** (1 assertion failure) | yes |
| `20261116-194859-mutation-rust-t6r-callers-3` | The 31 caller mutants (`mutations-rust-t6r-callers.json`: `helper.rs`, `fundamentals.rs`, `flow_board.rs`, `bars.rs`, `server.rs`) with the interpreter linked into the isolated copy, so the real-child tests ran, on the green-4 tree | **25 caught, 5 survived, 1 `error_only`** (survivors: `t6r-fund-helper-full-limit`, `t6r-fund-admission-unbudgeted`, `t6r-board-module-fresh-limit`, `t6r-bars-stop-falls-back`, `t6r-bars-helper-full-limit`; `error_only`: `t6r-fund-shutdown-is-timeout`, whose test failed by a `panic!` in a `let … else`) | kept; each survivor was a missing test and each led to one (below) |
| `20261116-201500-rust-t6r-green-5` | After the new tests: the helper is given what is left after the wait for a place (fundamentals and bars), the wait for a place is cut at the deadline, the board module gets what is left, a refused helper request does not run the adjusted-fetch command, and the stop-mapping tests fail by `assert_eq!` | **358 passed**, 0 failed, 2 ignored | yes |
| `20261116-201622-mutation-rust-t6r-callers-r2` | The six mutants again | **Stopped by hand** after 2: with `t6r-fund-admission-unbudgeted` the new test **deadlocked** (the test itself holds the place the mutant waits for) and the harness would have ended it as `timeout`, which is not a caught mutant | kept, not evidence |
| `20261116-203124-rust-t6r-green-6` | After wrapping that call in the test's own 5 s bound, so it fails by assertion | **358 passed**, 0 failed, 2 ignored | yes |
| `20261116-203237-mutation-rust-t6r-callers-r3` | The six mutants (`mutations-rust-t6r-callers-r2.json`), interpreter linked in, on the green-6 tree | **6 of 6 caught**, each with an assertion failure in a named test | yes |
| `20261116-203737-rust-t6r-verify-a` | A further full run of the same tree | **357 passed, 1 failed**: `analyse_worker::tests::the_deadline_includes_the_wait_so_a_second_request_behind_a_long_one_times_out_on_the_same_clock` (`first.await.unwrap().is_ok()`). **A timing flaw in the first T6 version of that test** (its first request had a 2 s deadline for a Python start and a 1.2 s answer; the machine was busy), not in the code. Its neighbour (800 ms deadline near a Python start) had the same weakness | kept; the failure is real evidence of a flaky test, fixed below |
| `20261116-203842-rust-t6r-verify-b` | The same tree, immediately after | 358 passed | yes (before the fix) |
| `20261116-204015-rust-t6r-final-a`, `…204127-rust-t6r-final-b`, `…204223-rust-t6r-final-c` | **Three full runs after the two tests warm the worker first** (and the neighbour has 2.5 s) | **358 passed** each, 0 failed, 2 ignored | yes: the final tree |

The spec files: `mutations-rust-t6r-core.json` (31 mutants on `bounded.rs`) and `mutations-rust-t6r-callers.json` (31 mutants on `helper.rs`, `fundamentals.rs`, `flow_board.rs`, `bars.rs`, `server.rs`), `mutations-rust-t6r-core-r2.json` (1) and `mutations-rust-t6r-callers-r2.json` (6). **Totals on the final tree:** core 31 of 31 caught (30, then the survivor), callers 31 of 31 caught (25, then the six). The core run (`core-2`) ran with no interpreter in the isolated copy, so its real-child tests skipped; its mutants were killed by the fake-child tests in `bounded_tests.rs`, which do not need one.
## T6 review 2 runs (the five gaps, 17 November 2026)

Record: [../../WP07-T6-BOUNDED-WORK.md](../../WP07-T6-BOUNDED-WORK.md), last section. Rust only. Same rules: own directory per run, nothing overwritten. The interpreter was linked into the isolated copy for the mutation runs this time.

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261117-001654-rust-t6r2-red` | The new regressions against an inert scaffold (new types and signatures; `Admission::run_direct` freed its place at once, the journal was written without a gate, a corrupt journal was ignored, dropping a ticket cancelled a running job) | **363 passed, 16 failed**; 2 ignored. 15 genuine failures; the 16th was an old assertion of mine in `fundamentals` (that B never starts the module), flawed and corrected | red evidence |
| `20261117-002210-rust-t6r2-green-1` | After the implementation | 378 passed, **1 failed**: the old `an_unreadable_journal_does_not_stop_the_pool_and_is_reported`, which asserted the fail-open behaviour item 4 reverses | kept; the test was rewritten on purpose |
| `20261117-002412-rust-t6r2-green-2`, `…002519-rust-t6r2-green-3` | After that, and with more tests added from the mutant analysis | **382 passed** | yes |
| `20261117-002559-mutation-rust-t6r2` | 39 mutants on the new code (`mutations-rust-t6r2.json`), interpreter linked in | **36 caught, 2 survived** (`t6r2-export-code-2`, `t6r2-bars-direct-unbudgeted`: both untested), **1 invalid** (`t6r2-direct-place-freed-before-exit` did not compile) | kept |
| `20261117-004539-rust-t6r2-green-4` | After the export and adjusted-command tests | 383 passed, **1 failed**: `server::the_indicator_run_route_takes_a_body_between_two_and_four_megabytes_…` (a connection abort sending a 4 MB body; **not this change**, passed in every other run, not touched) | kept |
| `20261117-004634-mutation-rust-t6r2-r2` | The three mutants again | **INVALID** (`INVALID.txt`): the baseline failed `fundamentals::an_export_returns_the_files_bytes_and_removes_the_file`. A real race of mine: the new export test and this one both wrote `athena-fund-<pid>-0.xlsx` (the counter was per instance). Fixed (a process-wide counter) | kept, not evidence |
| `20261117-005110-rust-t6r2-green-5` | After the counter fix | **384 passed** | yes |
| `20261117-005212-mutation-rust-t6r2-r3` | The three mutants (`mutations-rust-t6r2-r2.json`), the first one rewritten | **3 of 3 caught** | yes |
| `20261117-005619-rust-t6r2-final-a`, `…005705-…-final-b`, `…005750-…-final-c` | Three full runs on the final tree | **384 passed** each, 0 failed, 2 ignored | yes |

**Totals on the final tree:** 39 of 39 mutants caught (36, then the three). Spec files: `mutations-rust-t6r2.json`, `mutations-rust-t6r2-r2.json`.
## T6 review 3 runs (the five remaining paths, 17 November 2026)

Record: [../../WP07-T6-BOUNDED-WORK.md](../../WP07-T6-BOUNDED-WORK.md), last section. Rust only. Same rules: own directory per run, nothing overwritten. The interpreter was linked into the isolated copy.

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261117-011555-rust-t6r3-red` | The new regressions against an inert scaffold (new variants and seams that did nothing) | **387 passed, 15 failed**; 2 ignored. Three new tests passed in red because the scaffold already did what they check (guards) | red evidence |
| `20261117-012010-rust-t6r3-wip1` | After the first implementation, `bounded` tests only | 85 passed, **3 failed**: the retry on every cycle also fired on a stray wake-up and cleared the failure the tests look at (a defect of mine; now only on the recheck timer) | kept, not final |
| `20261117-012131-rust-t6r3-green-1` | After that fix | **402 passed**, 0 failed | yes (before four more tests) |
| `20261117-012235-rust-t6r3-green-2` | With four more tests and a server assertion | 405 passed, **1 failed**: a new test whose sleep landed on the owner's recheck instant (wrong clock in the test, not in the code) | kept, not final |
| `20261117-012349-rust-t6r3-green-3` | After the sleeps were made relative to the call's return | **406 passed** | yes |
| `20261117-012514-rust-t6r3-green-4` | The refresh run before the mutations (run from a detached process) | The log says `406 passed; 0 failed; 2 ignored`. **`rust-summary.txt` and `rust-results.json` show 0**: the results parser could not read the log written by the detached process; the log itself is the evidence | yes (by the log) |
| `20261117-012601-mutation-rust-t6r3` | 39 mutants (`mutations-rust-t6r3.json`) | **38 caught, 0 survived, 1 `pattern_error`** (`t6r3-shutdown-counts-swapped`: its pattern matched twice) | kept |
| `20261117-014755-mutation-rust-t6r3-r2` | That mutant with unique context (`mutations-rust-t6r3-r2.json`) | **caught** | yes |
| `20261117-014855-rust-t6r3-final-a`, `…014930-…-final-b`, `…015005-…-final-c` | Three full runs on the final tree | **406 passed** each, 0 failed, 2 ignored | yes |

**Totals on the final tree:** 39 of 39 mutants caught (38, then the one rerun). Spec files: `mutations-rust-t6r3.json`, `mutations-rust-t6r3-r2.json`.
## T6 review 4 runs (the four remaining paths, 17 November 2026)

Record: [../../WP07-T6-BOUNDED-WORK.md](../../WP07-T6-BOUNDED-WORK.md), last section. Rust only; interpreter linked in.

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261117-023856-rust-t6r4-red` | The new probes against the unchanged code (plus a test-only hook at the launch gap) | **407 passed, 7 failed**, 2 ignored | red evidence |
| `20261117-024037-rust-t6r4-green-1` | After the fixes | **414 passed** | yes |
| `20261117-024211-rust-t6r4-green-2` | Refresh before the mutations (detached process) | the log says 414 passed; `rust-summary.txt` shows 0 (the parser cannot read a log from a detached process) | by the log |
| `20261117-024258-mutation-rust-t6r4` | 14 mutants (`mutations-rust-t6r4.json`) | **13 caught, 1 timeout** (`t6r4-pool-exit-not-counted`: shutdown hangs, an older unbounded test hung the run) | kept |
| `20261117-030449-mutation-rust-t6r4-r2` | That mutant against the bounded concurrent-shutdown tests (`mutations-rust-t6r4-r2.json`) | **caught** | yes |
| `20261117-030534-rust-t6r4-final-a` … `…031201-rust-t6r4-final-i` | Nine full runs on the final tree | **414 passed** in `a, c, d, g, h, i`; **413 passed, 1 failed** in `b, e, f`: always `server::the_indicator_run_route_takes_a_body_between_two_and_four_megabytes_…` (connection abort sending 5 MB; unrelated, untouched) | `g, h, i` are three consecutive clean runs |

**Totals on the final tree:** 14 of 14 mutants caught. Spec files: `mutations-rust-t6r4.json`, `mutations-rust-t6r4-r2.json`.
## Git preparation: the body-limit test correction (17 November 2026)

Record: [../../GIT-PREPARATION.md](../../GIT-PREPARATION.md), section 3. Test-only change: the production half of `server.rs` is byte-identical to the backup taken before the change (SHA-256 of the text before `mod tests` compared). Every run below is kept, whatever it says.

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261117-111311-rust-bodylimit-wip1` | First draft of the new tests (helpers still panicked) | 47 passed, **2 failed** (draft) | **not final**: early draft, kept |
| `20261117-111437-rust-bodylimit-full-1` | Full suite, tests as first written | **417 passed**, 2 ignored | superseded by the helper fix |
| `20261117-111555-mutation-rust-bodylimit` | 5 mutants against the first helpers | two scored `error_only` (a helper panicked instead of asserting) | **not final**: led to the helper fix; run stopped, partial |
| `20261117-112018-rust-bodylimit-full-2` | Launched by that stopped job | no log | **not final**, partial |
| `20261117-112122-rust-bodylimit-full-pre` | A filtered run whose filter matched nothing | 0 tests ran (filter with `|`) | **not final**: a mistake, kept |
| `20261117-112222-mutation-rust-bodylimit` | 5 mutants on the final tests (`mutations-rust-bodylimit.json`): limit +1, limit −1, limit layer removed, handler `>=`, handler `> MAX_BODY + 1` | **5 caught** (assertion failures in the new tests) | yes |
| `20261117-112654-rust-bodylimit-full-2` | Full suite on the final tree | **417 passed**, 2 ignored | yes |
| `20261117-112801-rust-bodylimit-stress` | The 4 new tests, 200 sequential runs and 8 × 25 parallel runs, every outcome recorded | **400 of 400 passed**, 0 failed | yes |
| `20261117-114307-rust-bodylimit-full-3` … `…114917-…-full-11` | Nine more full runs, none dropped | **417 passed** in each, 0 failed | yes |
| `20261117-114959-rust-bodylimit-oldtest-repro` | The **old** test, unchanged, built from the backup's pre-change sources (isolated copy), 200 sequential and 8 × 25 parallel runs | **13 of 400 failed** (3 sequential, 10 parallel), every failure the same: `10053 ConnectionAborted` unwrapping the 5 MB request; the other 387 passed | yes (reproduction) |

**Totals:** 11 full suites on the final tree (`full-1` is the first draft's; `full-2` to `full-11` are ten on the final tree), all 417 passed; 5 of 5 mutants caught; 400 of 400 stress runs of the new tests passed; the old test failed 13 of 400 (3.3%), always with the Windows connection abort. Spec file: `mutations-rust-bodylimit.json`. Not selected: no run was repeated until it was green.

## Commands (exact)

- Rust: `scripts\run_rust.ps1 -Label <label> [-UpdateFixtures]` (a fresh allowlist copy of `*.rs`, `Cargo.toml`, `Cargo.lock`, two Python files the helper tests need and the two UI fixtures, stamped with the current time; `cargo test --offline --locked` with `CARGO_TARGET_DIR=C:\Users\LEGION\ath-wp07\t`, `CARGO_NET_OFFLINE=true`). Inspection-based; **enforces nothing at run time**: not a sandbox. With `-UpdateFixtures` the Rust tests write the fixtures in the isolated copy and the script copies them to the repository only when the run exited 0.
- Web (cwd `terminal\web`): `scripts\run_web.ps1 -Label <label> [-Files test/a.test.ts,...]`: `npm exec -- tsc --noEmit`, then `npm test -- --config <run>\vitest.config.mjs --reporter=default --reporter=json --outputFile.json=<run>\web-results.json` under guard v3 (a tripwire, not a sandbox: `../wp07-2026-11-15/GUARD-LIMITS.md`) with `npm_config_offline=true`.
- Mutations: `python scripts\mutate.py <spec> <label>` (Rust) and `scripts\run_mutations_web.ps1 -ConfigRun <web run> -Template mutations-web-t3r.template.json` (web). A mutation whose output shows a build or import error is `invalid`, a missing or ambiguous pattern is `pattern_error`; every file is restored and its hash verified.

- Follow-up (T3-C1/C2): `python scripts\mutate.py <spec> <label>` is **version 2** (judges vitest's JSON report; `mutate.v1.py` is the old one); `python scripts\reclassify.py <label> <run-dir> ...` reclassifies saved runs from their logs; `python -m pytest scripts\test_mutate.py -q -p no:cacheprovider` tests the harness; specs `mutations-web-t3c.template.json` (C1 and T5-F) and `mutations-web-harness-selftest.template.json`; web mutations still through `run_mutations_web.ps1 -ConfigRun <web run> -Template <spec>`.

## Two flaws found in the web mutation harness (and what they affect)

1. **Lost output.** The web mutation logs were **empty**. `mutate.py` read the child's output with the Windows code page; vitest prints UTF-8, the reader thread failed, the output was lost. The exit code stayed real, but the failing-test list and the "did it only break the build" check were lost. Fixed (`encoding="utf-8", errors="replace"`).
2. **A harness that failed for its own reason, so every web mutation read "caught".** With the output visible, every log showed all test files failing at load ("Vitest failed to find the runner"). Cause: the web specs' `cwd` was `d:/projects/...` (lower-case drive letter); launched that way, vitest loads two copies of itself on Windows. The same command with `D:/...` passes (102 tests). **So every web mutation result produced with such a spec was not evidence**: this correction's first two web mutation runs (kept, marked INVALID above), **the first T3 report's web result (26 of 26)**, and the web mutation runs of T5-R and T5-F, which used the same spec form. The Rust mutation runs were unaffected (their failing-test lists name real tests).
   - **Fix:** the specs here use `D:/...`, and `mutate.py` now runs the command once on the **unmutated** tree and stops the run as INVALID if it does not exit 0 (`baseline.json`, `logs/baseline-unmutated.log`), so a harness that fails for its own reasons cannot report "caught" again.
   - **Rerun:** the T5-F spec, the T5-R spec and the first T3 spec were rerun with the corrected harness against the current code (table above): T5-R 11 of 11 caught, the first T3 spec 20 caught with 6 patterns that no longer exist, and **T5-F 7 of 8 caught with one real survivor** (the retained-data wording's cache age is untested). The earlier directories under `wp07-t5*-` and `wp07-t3-2026-11-16` are **not** edited; their web mutation results should be read as not established, with the reruns here replacing them.

## Not established

The policy limits are not calibrated on real market data; a real stream, a real broker clock, a real browser and the Playwright e2e suite were not used (the e2e spec is unchanged and **not run**); the last-traded field's unit and the sequence number's behaviour were not checked on a live stream; single machine, single run of each suite; the guard is a tripwire and the Rust stage enforces nothing at run time. Python was not changed and was not rerun.
