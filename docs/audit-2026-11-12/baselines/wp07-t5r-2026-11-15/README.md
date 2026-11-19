# WP07 T5 review corrections: verification artifacts (15 November 2026)

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Evidence for the corrections of review items T5-R1 to T5-R5 ([../../WP07-T5-REVIEW.md](../../WP07-T5-REVIEW.md); what changed and why: [../../IMPLEMENTATION-STATUS.md](../../IMPLEMENTATION-STATUS.md), "T5 review corrections"). Every run has its **own directory** under `runs\`, named `<yyyymmdd-HHMMSS>-<label>`; **nothing is overwritten, red, failed and invalid runs included**. Synthetic stores and temporary paths only; no production `.athena` data, environment file, live call, commit or push. Earlier T5 evidence stays in [../wp07-t5-2026-11-15/](../wp07-t5-2026-11-15/README.md).

**Machine-readable outputs:** pytest `junit.xml` and `results.json` (one record per test), `collected-ids.txt`; Rust `rust-results.json` (one record per test), `rust-test-ids.txt`; web `web-results.json` (vitest JSON) and `web-test-ids.txt`; mutations `mutations.jsonl` (one record per mutation: id, file, outcome, failing tests, seconds), `summary.json`, `spec.json` and `logs\<id>.log`. Each run also keeps `command.json`/`command.txt`, `git-head.txt`, `git-status*.txt` and toolchain versions. Mutation records list only the first failing test of a pytest run (`-x`).

**Code state:** commit `37f1fc19846a178083d9ced7a7a25aeffe65b1f9` with the audit work uncommitted in the working tree. Rust source plus lock tree hash of the green runs: `FE9837BBA8C7C1B2` (`runs\20261115-175307-rust-green-2\rust-source-tree-hash.txt`; 0 files different from the isolated copy); no `Cargo.toml` or `Cargo.lock` change. The Rust and web sources did not change after those green runs (the mutation runs restore them, hash-verified); the Python sources changed after `py-broad` only in two comments, and one test was added (see `py-final-snapshot`).

## Runs

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261115-173840-py-red-new-integration` | New Python regressions **before** the fix | 49 tests: **41 failed**, 8 passed. The 8 are guards that must hold before and after: a healthy unchanged generation (fast path), a real lock message still a writer (5 paths), a missing file, a real second-process lock | red evidence (valid) |
| `20261115-174008-py-green-1` | Snapshot Python files after the fix | 98 tests: 97 passed, **1 failed**: an older test asserted that the first read hashes a generation the same process just published (changed on purpose: the publishing process now remembers its own hash) | **NOT GREEN**, kept |
| `20261115-174049-py-green-2` | Same files, test updated | **98 passed** | yes |
| `20261115-174307-rust-red` | Rust suite **before** the fix | 227 passed, **6 failed** (4 `server::`, 2 `snapshot::`), 2 ignored | red evidence (valid) |
| `20261115-174932-rust-fixture-gen` | One Rust test with `ATHENA_UPDATE_FIXTURES=1`: writes `terminal/web/test/fixtures/data-source.cases.json` from the real registry | 1 passed; `data-source.cases.generated.json` kept beside it | yes |
| `20261115-175110-web-red` | New web tests against the **pre-fix** sources (the T5 `provenance.ts`, `chartlab/view.ts` from HEAD; swapped in by `web_red.ps1` and restored, hash-verified) | 51 tests: **20 failed**; `tsc` exit 1 | red evidence (valid) |
| `20261115-175125-rust-green-1` | Full Rust suite after the fix | 241 passed, **2 failed**: both defects of my tests (one held a connection while overwriting the file, which Windows refuses; one re-opened a deliberately corrupted file as a database) | **NOT GREEN**, kept |
| `20261115-175126-web-green-1` | Full web suite after the fix | **2 tests failed** (the badge title listed dates only for the worst reader: a real defect, fixed; an older assertion on the failure shape, changed on purpose) and `tsc` exit 1 (the fixture test imported `node:fs`; no `@types/node`, so it now imports the JSON) | **NOT GREEN**, kept |
| `20261115-175307-rust-green-2` | Full Rust suite, isolated copy | **243 passed, 0 failed, 2 ignored** (226 before: +17) | yes |
| `20261115-175308-web-green-2` | `tsc --noEmit`, then full web suite, guard v3 | **tsc exit 0; 72 files, 934 tests passed** (906 before: +28); guard: 142 denied `fetch` (relative `/api/...`), 0 absolute, 0 other denials | yes; **not a zero-violation run** (see Guard) |
| `20261115-175441-mutation-python` | Python mutations, first attempt | all 11 **invalid**: the runner started the base interpreter, which lacks the project's packages, so every test file failed to import | **INVALID**, kept |
| `20261115-175442-mutation-rust` | 12 Rust mutations on the isolated copy | **12 caught** | yes |
| `20261115-175501-mutation-web` | 11 web mutations, first attempt | 8 caught, **3 pattern errors**: the spec was written in the console's default encoding and the `●` of the badge text was mangled | **INVALID (partial)**, kept |
| `20261115-175548-mutation-python` | 11 Python mutations (interpreter fixed) | **11 caught** | yes |
| `20261115-175632-mutation-web` | 11 web mutations (spec in UTF-8), guard hits copied | **11 caught** | yes |
| `20261115-180033-mutation-rust-r5` | The review's original lock rule restored in Rust (any of the old bare words, a path included) | **caught** by `a_path_naming_a_lock_does_not_make_a_corrupt_or_unreadable_file_a_writer` | yes |
| `20261115-180055-mutation-python-2` | The Python mutations again, the lock mutant now the review's original rule | **11 caught**; the lock mutant is caught by `test_a_corrupt_file_whose_path_contains_lock_is_unreadable_not_locked[clock.duckdb]` | yes |
| `20261115-180159-py-broad` | Broad offline Python run, the nine standing exclusions | **3,199 collected: 3,123 passed, 76 skipped, 0 failed** (3,148 IDs before: +49 new tests +2 cross-language tests, env-gated, which the earlier ID list did not hold: 3,148 + 49 + 2 = 3,199) | yes |
| `20261115-180159-xlang` (+ `20261115-180200-xlang-rust-full`) | Cross-language contract, both directions, after a full Rust run | Rust **243 passed, 0 failed, 2 ignored**; all four steps passed (Python writes, Rust reads; Rust writes, Python reads) | yes |
| `20261115-180745-py-final-snapshot` | Snapshot, supervisor and cross-language Python files after a comment-only edit and one added test (retention is nominal) | 130 collected: **128 passed, 2 skipped** (the two cross-language tests, run in `xlang`) | yes |

## Commands (exact)

- Python: `python -m pytest -p no:cacheprovider --junitxml=<run>\junit.xml -q <args>` by `scripts\run_pytest.py <label> <args>`; broad run args: `tests` plus `--ignore=` for `tests/test_dashboard_app.py`, `test_live_panel.py`, `test_market.py`, `test_verdict_panel.py`, `test_llm_basics.py`, `test_llm_probe.py`, `test_llm_router.py`, `test_angel_login.py`, `test_kite_login.py`. IDs: `--collect-only -q` (not doubled).
- Rust: `scripts\run_rust.ps1`: a fresh allowlist copy of `*.rs`, `Cargo.toml`, `Cargo.lock`, two Python files the helper tests need, and the **one UI fixture** (`terminal/web/test/fixtures/data-source.cases.json`) at `C:\Users\LEGION\ath-wp07\w`, stamped with the current time (a stale-binary trap), then `cargo test --offline --locked --no-run`, `-- --list` and `cargo test --offline --locked` with `CARGO_TARGET_DIR=C:\Users\LEGION\ath-wp07\t` and `CARGO_NET_OFFLINE=true`. The audit of the copy's file names is in each run (`isolated-copy-audit.txt`). The stage is inspection-based and **enforces nothing at run time**: it is not a sandbox.
- Web (cwd `terminal\web`): `scripts\run_web.ps1`: `npm exec -- tsc --noEmit`, then `npm test -- --config <run>\vitest.config.mjs --reporter=default --reporter=json --outputFile.json=<run>\web-results.json` with `npm_config_offline=true`. A run with any test file that did not load, a failed test, a failed `tsc` or a non-zero exit is marked NOT GREEN in `web-summary.txt`.
- Mutations: `scripts\mutate.py <spec> <label>` (specs: `mutations-python.json`, `mutations-rust.json`, `mutations-rust-r5.json`, `mutations-web.template.json`; web by `scripts\run_mutations_web.ps1`). A mutation that breaks the build is `invalid`, not caught; a pattern that is missing or ambiguous is `pattern_error`; the file is always restored and its hash verified.
- Cross-language: `scripts\run_xlang.ps1` (also the docstring of `tests/test_snapshot_crosslang.py`).
- Fixtures: `scripts\run_rust.ps1 -Label rust-fixture-gen -Filter the_registry_payloads_match_the_ui_fixtures -UpdateFixtures` regenerates the UI fixtures from the Rust registry; without the switch the same test **fails** when the Rust payloads and the file differ.

## Guard

`guard.setup.v3.mjs` (kept in `../wp07-t5-2026-11-15/scripts/`) is a tripwire, not a sandbox; its coverage and limits are in `../wp07-2026-11-15/GUARD-LIMITS.md`. Under it the web run recorded **142 denied `fetch` attempts** and no other denial: the 130 of the baseline plus 12 from six new Chart Lab instances in `labview.test.ts`, each making the same relative requests the existing instances make (`/api/formulas`, `/api/pyind`). That is **not a zero-violation run**. The mutation runs wrote their own guard hits (`20261115-175632-mutation-web\guard-hits.jsonl`).

## Not established

A real long-running writer lock with the real terminal and a real multi-gigabyte warehouse; DuckDB's POSIX lock wording (the Windows wording was captured from a real second process on this machine; the POSIX line in the tests is DuckDB's documented format, not captured here); the Rust lock classifier is tested on DuckDB's message text because one process cannot lock a file against itself; single machine, single run of each suite; mutation records name only the first failing test of a pytest run.
