# WP07 baseline (15 November 2026): Rust and web test suites

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Current results of the existing Rust and web suites on this machine, taken before any WP07 code. **These are current results, kept apart from the historical audit baseline** in `../../verification.md`, which recorded the web suite at **70 files / 895 tests passed** (identical to now) and the Rust suite at **206 tests: 179 passed, 27 failed** (23 socket-dependent failures it attributed to sandbox restrictions and 4 indicator-child failures it attributed to temp-directory restrictions). Today's Rust run is **206 passed, 0 failed**. The difference is consistent with the audit's own attribution (loopback sockets and writable temp are available here), but this run does not prove that attribution: the machine, the isolation method (copy as working directory, short build path) and possibly the PATH differ. No application code was changed and nothing was committed. Everything ran offline with installed dependencies.

## Code state

| Item | Value |
|---|---|
| Commit | `37f1fc19846a178083d9ced7a7a25aeffe65b1f9` (audit baseline) |
| `terminal/` | **unmodified** against that commit (`git status --short -- terminal` empty before and after) |
| Rust source + lock identification | tree hash `0FC9768081D6FE90` over per-file hashes of the 35 `.rs` files, `Cargo.toml`, `Cargo.lock` (`results/rust-source-manifest.txt`, `results/rust-source-tree-hash.txt`) |
| Rest of the working tree | dirty (the audit implementation work, uncommitted); `git status` identical before and after the run (`results/git-status.*`) |
| Toolchains | cargo 1.98.1, rustc 1.98.1, node v24.18.0, npm 11.16.0, vitest 5.0.4, jsdom 30.1.2, project Python 3.13.14, Windows 11 (`results/toolchain-versions.txt`) |

## Commands (exact)

Rust (cwd = the isolated copy `C:\Users\LEGION\ath-wp07\w\terminal`; `CARGO_TARGET_DIR=C:\Users\LEGION\ath-wp07\t`; `CARGO_NET_OFFLINE=true`):

```
cargo test --offline --locked --no-run
cargo test --offline --locked -- --list
cargo test --offline --locked                      # run A: PATH as it is (no sh)
cargo test --offline --locked                      # run B: same command, C:\Program Files\Git\bin prepended to PATH
```

Web (cwd = `terminal\web`; `npm_config_update_notifier=false`, `npm_config_audit=false`, `npm_config_fund=false`, `npm_config_offline=true`):

```
npm.cmd test -- --config <scripts\vitest.review.config.v2.mjs> --reporter=default --reporter=json --outputFile.json=<results\web-results.json>
```

`npm test` is the existing script (`vitest run`). The scratch config sets `envDir` to an empty directory, `cacheDir` outside the repo, and `setupFiles` to the guard.

## Results

### Rust: 206 tests

| | Run A (no `sh` on PATH) | Run B (Git `sh` on PATH) |
|---|---|---|
| Result | **206 passed, 0 failed, 0 ignored** | **206 passed, 0 failed, 0 ignored** |
| Time | 5.65 s | 6.96 s |

**The nominal pass count of 206 in run A includes 8 tests that did not run their bodies.** They are the only tests whose body needs `sh`, and `sh` is not on this machine's PATH:

| Test | Run A | Run B | Evidence the body ran in B |
|---|---|---|---|
| `chart_explain::tests::the_request_reaches_the_child_on_standard_input_and_its_answer_comes_back` | **bypassed** (early return), reported ok | ok | precondition only |
| `chart_explain::tests::a_bad_request_a_failing_child_and_a_busy_slot_are_told_apart` | **bypassed** | ok | precondition only |
| `sentiment::tests::the_symbol_and_the_tone_flag_reach_the_command_and_an_answer_is_kept` | **bypassed** | ok | shell log `athena-sentiment-14320/calls`, 2 lines, written during run B |
| `sentiment::tests::a_bad_symbol_a_failing_child_and_a_garbled_answer_are_told_apart` | **bypassed** | ok | precondition only |
| `ipo::tests::the_warehouse_path_reaches_the_command_and_an_answer_is_kept_for_a_minute` | **bypassed** | ok | shell log `athena-ipo-14320/calls`, 1 line, written during run B |
| `ipo::tests::a_failing_child_and_a_garbled_answer_are_told_apart` | **bypassed** | ok | precondition only |
| `flow_board::tests::the_document_is_read_from_the_child_and_kept_until_the_file_changes` | **bypassed** | ok | shell log `athena-flow-board-14320/calls`, 2 lines, written during run B |
| `flow_board::tests::a_missing_history_a_busy_file_and_a_broken_child_are_told_apart` | **bypassed** | ok | precondition only |

**Early-return condition confirmed** (`results/post-summary.txt`): each test begins with `Command::new("sh").arg("-c").arg("true").status().ok()?`, which returns early only when `sh` cannot be started. Run A: `sh` is not resolvable, so the spawn fails and all eight returned early. Run B: `sh` resolves to `C:\Program Files\Git\bin\sh.exe` and `sh -c true` exits 0, so the early-return branch is not taken. No `calls` file was written during run A (only the shell writes it), and the three above appear only in run B's time window. The other five tests leave no artifact; for them the evidence is the precondition logic, not an observation of the body.

**Commands in the eight remain offline and use temporary fixtures** (read before running): the stand-ins are `sh -c` scripts using `echo`, `cat`, `wc` and `exit`; logs go to `%TEMP%\athena-<name>-<pid>`; the flow-board store is a temporary file; `w.duckdb` is only a command-line argument that is echoed, never opened.

**Not the eight-test filtered run.** I intended to replace run B by a filtered run of these eight, and a watcher was set to stop the full run B after run A. The watcher matched no process, run B (all 206 tests) completed in 8 seconds before it acted, and per your fallback its results are used and no third pass was made. `scripts/run_baseline4.ps1` (the filtered run) was prepared and **never executed**.

**Other early returns (precondition met, execution not independently shown): 18 tests** that return early when the project Python is missing. Python resolves from the isolated working directory (`../.venv/Scripts/python.exe` exists and runs, `results/rust-preconditions.txt`), so the early-return branch should not have been taken, but Rust gives no per-test proof:

| Module | Tests |
|---|---|
| `pyind` (5) | `a_file_is_listed_off_until_switched_on_for_the_version_the_user_saw_and_then_runs`, `a_file_changed_after_it_was_switched_on_is_off_again_and_does_not_run`, `a_file_that_fails_says_how_and_one_that_hangs_is_stopped`, `the_servers_environment_does_not_reach_the_indicator_and_bad_requests_are_refused`, `a_missing_folder_lists_nothing` |
| `helper` (1) | `a_helper_answers_with_what_the_command_prints_and_a_failure_or_silence_is_none` |
| `analyse_worker` (4) | `a_request_gets_its_own_answer_and_a_failure_keeps_the_worker`, `a_backtest_goes_through_the_same_worker`, `a_strategy_is_passed_on_whole_and_a_bad_or_huge_one_is_refused_before_the_worker_hears_of_it`, `a_dead_worker_is_reported_and_replaced_on_the_next_request` |
| `forensic_job` (3) | `a_report_of_the_known_schema_is_carried_whole_to_the_page`, `the_jobs_own_error_is_shown_short_and_anything_else_is_a_fixed_message`, `the_same_document_may_be_asked_twice_but_another_waits_and_the_arguments_reach_the_job` |
| `fundamentals` (5) | `the_child_gets_the_symbol_basis_store_and_price_and_its_document_comes_back`, `no_price_is_passed_when_the_warehouse_has_none`, `the_childs_failures_become_fixed_errors`, `the_valuation_runs_its_own_module_with_the_same_arguments_and_is_refused_when_not_configured`, `an_export_returns_the_files_bytes_and_removes_the_file` |

Reading of the 206: 8 bypassed in run A (executed in run B), 18 precondition-gated with the precondition met, 180 with no early-return path found by inspection. A few Windows-only tests (`verdict_job` uses `cmd`/`ping 127.0.0.1`, `fund_fetch` uses `cmd`/`ping 127.0.0.1`) run only because this is Windows; on another OS they would use other commands or not apply.

### Web: 70 test files, 895 tests

| | |
|---|---|
| Result | **895 passed, 0 failed, 0 skipped, 0 todo** (306 suites) in 19 s |
| Guard records | 70 `guard-installed`, **130 denied `fetch` attempts**, 0 other denied calls, 0 reads outside the allowed roots |
| Fetch targets | all relative (`/api/pyind` 56, `/api/formulas` 56, `/api/valuation/<symbol>` 18); **0 absolute URLs**; 67 distinct tests |

**This was not a zero-violation run: the 895 assertions passed while the guard denied 130 `fetch` attempts.** The guard used (v2) also did not check the destination path of two-path filesystem calls (`rename`, `copyFile`, `cp`, `link`, `symlink`); see `GUARD-LIMITS.md`. That is fixed in `scripts\guard.setup.v3.mjs`, which has not been used for a baseline run; this baseline was not rerun for it.
**Guard violations: 130, all `fetch`.** They are unstubbed default `fetch` collaborators constructed by the Chart Lab, grid and fundamentals views (`serverFormulaIO()`, `serverPyIO()`, the valuation fetch) when a test does not inject one. Each was refused by the guard, swallowed by the view's own error handling, and the 895 tests still pass. Without the guard, a relative URL cannot reach the network from node (no base URL), so no traffic is expected, but that is a property of node's `fetch`, not a test-suite guarantee, and these tests depend on a failing default rather than on an injected stub. None of the 130 was an absolute URL.
No `WebSocket`, `EventSource`, `XMLHttpRequest`, TCP, TLS, HTTP, DNS, UDP, child-process or protected-path call was attempted.

## Isolation: what is and is not established

- **Rust stage:** isolated copy, **allowlist only** (35 `*.rs` files, `Cargo.toml`, `Cargo.lock`, plus `runner.py` and `__init__.py` for the indicator tests): 39 files. Audit of the copy's names (`results/isolated-copy-audit.txt`, `results/isolated-copy-name-check.txt`): 0 outside the allowlist, **0 matches for every protected pattern: basename `.env`, `.env.*`, non-dot `*.env`, `.envrc`, any dot-directory segment, any dot-named file** (the patterns were checked against synthetic names to show they fire). The check read names only; no protected file was opened. Existing runtime data is not in the copy: no `.athena` directory, so the relative paths the tests use (`.athena/terminal/helper.log`) land inside the copy, and the real `terminal/.athena/terminal/helper.log` was neither opened nor modified (size 0 and modification time 2026-10-28 11:17:41 before and after). The one thing reached outside the copy is the project Python through a junction named `.venv` (read use), removed after the run. **There is no run-time guard on the Rust stage**; isolation is inspection-based and unenforced. The test binaries and that Python can read whatever the user can.
- **Web stage:** full import-closure scan (70 test files, 113 source modules; `results/web-closure-scan.txt`) and the guard tripwire. **The guard is not a sandbox**; its coverage and limits are in `GUARD-LIMITS.md`. Interception paths the self-test exercised: `fetch`, `WebSocket`, `XMLHttpRequest`, `net.connect`, `http.get`, `dns.lookup`, `child_process.spawn`, `readFileSync`, `existsSync`, `statSync`, `fs.promises.readFile`, named imports from `node:fs` and `node:fs/promises`, twelve protected-path forms (26 checks, all pass, `results/guard-selftest-output.txt`). **Implemented but not exercised by the self-test:** `tls.connect`, `https.request/get`, `http.request`, `dgram.createSocket`, `dns.resolve*`, `exec`, `execFile`, `fork` and the `*Sync` process calls, `EventSource`, `sendBeacon`, the callback forms of the fs calls, `createReadStream`, `opendir`, `copyFile`, `rename`, `unlink`. **Not covered:** worker threads (demonstrated unguarded), native addons, `process.binding`/`dlopen`, descriptors opened before the guard, the vitest/vite main process, processes started outside `child_process`, third-party package internals.
- **Test-suite side effects:** `git status` identical before and after; 0 files under `terminal/` (outside `target` and `node_modules`) modified during the run; `node_modules/.vite` unchanged (the vitest cache went to the scratch `cacheDir`); build output went to `C:\Users\LEGION\ath-wp07\t`.

## Defects and mistakes found and recorded

1. **Guard v1 did not intercept `net.connect`** (found by the self-test: Node passes a normalised argument array). The first web run used v1; its result is retained as **historical evidence with that limitation**: `historical-v1/v1-web-results.json`, `v1-web-test.log` (also 895 passed). Its raw-TCP observations are void. **`historical-v1/v1-guard-hits.jsonl` is missing: I deleted it during the self-test's cleanup** (the cleanup removed the hits file of the v1 run). The 130 fetch denials and 70 installs I reported for v1 were read before that deletion; they match v2's counts.
2. The v1 guard's `fs.promises` wrappers threw instead of rejecting; fixed in v2.
3. The **first Rust build failed** (`cl.exe` exit 2 compiling bundled DuckDB) under the long scratch path; the cause is probably the Windows path-length limit, **not confirmed**: it did not recur under the short path `C:\Users\LEGION\ath-wp07`. The failing log was overwritten by the later successful build. The partial long-path build directory (`...\scratchpad\baseline\review-target`) is retained.
4. The watcher meant to stop the old script after run A matched no process (see above).

## Not established

- That the 18 Python-gated tests executed their bodies (precondition met, no per-test proof).
- That the five `sh`-gated tests without a shell log executed beyond the precondition logic.
- Anything about a real terminal, a real warehouse, a real feed, or timing under load. Several tests are timing-based (sleeps, deadlines) and were run once on one machine.
- That the audit-time Rust failures (27) were caused by its sandbox, as it states: today's all-pass run is consistent with that, not proof of it.

## Artifact paths

Review folder (durable copy): `d:\projects\learn-project\athena-the-fund-manager\docs\audit-2026-11-12\baselines\wp07-2026-11-15\`

| What | Path (relative to the folder above) |
|---|---|
| Primary baseline script (run as executed) | `scripts\run_baseline3.ps1` |
| Copy allowlist (inside that script: `*.rs` under `terminal\src`, `Cargo.toml`, `Cargo.lock`, `runner.py`, `__init__.py`) | `scripts\run_baseline3.ps1`, section "isolated copy"; audit `results\isolated-copy-audit.txt` |
| Protected-name check script and output | `scripts\check_names.ps1`, `results\isolated-copy-name-check.txt` |
| Scratch vitest configuration | `scripts\vitest.review.config.v2.mjs` |
| Guard (v2) | `scripts\guard.setup.v2.mjs` |
| Guard self-test and its output | `scripts\guard_selftest.mjs`, `results\guard-selftest-output.txt` |
| Guard coverage and limits | `GUARD-LIMITS.md` |
| Import-closure scan | `scripts\closure_scan.mjs`, `results\web-closure-scan.txt`, `results\web-closure-files.json` |
| Watcher (did not act) / filtered run (not executed) | `scripts\watch_stop_after_A.ps1`, `results\watcher.txt` / `scripts\run_baseline4.ps1` |
| Parsing, summary and collection scripts | `scripts\parse_rust.ps1`, `scripts\post.ps1`, `scripts\fetch_summary.ps1`, `scripts\collect.ps1` |
| Rust logs | `results\rust-test.A-no-sh.log`, `results\rust-test.B-with-sh.log`, `results\rust-results-summary.txt` (build log: scratch only, below) |
| Rust collected test IDs (206) | `results\rust-test-ids.txt`, raw `results\rust-list.raw.txt` |
| Web results and logs | `results\web-results.json`, `results\web-test.log` |
| Web collected test IDs (895, with status) | `results\web-test-ids.txt` |
| Guard records (200 lines) | `results\guard-hits.jsonl` |
| Preconditions, versions, code state, side effects | `results\rust-preconditions.txt`, `results\toolchain-versions.txt`, `results\git-head.txt`, `results\git-status.*.txt`, `results\helper-log.*.txt`, `results\repo-touched.txt`, `results\rust-source-manifest.txt`, `results\rust-source-tree-hash.txt`, `results\post-summary.txt`, `results\timeline3.txt` |
| Historical (first attempts, guard v1) | `historical-v1\` |

Scratch originals and build artifacts (retained, not in the repo): `C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline\` (includes `rust-build.log`, `vite-cache`, the failed long-path `review-target`); Rust build artifacts of the successful run: `C:\Users\LEGION\ath-wp07\t`; isolated copy: `C:\Users\LEGION\ath-wp07\w`.
