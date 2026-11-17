# WP07 T5 follow-up corrections: verification artifacts (16 November 2026)

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Evidence for the corrections of T5-F1 and T5-F2 ([../../WP07-T5-REVIEW.md](../../WP07-T5-REVIEW.md), "Follow-up review"; what changed and why: [../../IMPLEMENTATION-STATUS.md](../../IMPLEMENTATION-STATUS.md), "T5 follow-up corrections"). Every run has its **own directory** under `runs\`, named `<yyyymmdd-HHMMSS>-<label>`; **nothing is overwritten, red, failed and invalid runs included**. Synthetic stores and temporary paths only; no production `.athena` data, environment file, live call, commit or push. The scripts are copies of those in [../wp07-t5r-2026-11-15/scripts/](../wp07-t5r-2026-11-15/scripts/) (the run directory is relative to the script, so this folder's runs stay here), plus `mutations-rust-f.json`, `mutations-rust-f2.json`, `mutations-web-f.template.json` and a `-Template` switch on `run_mutations_web.ps1`.

**Machine-readable outputs:** Rust `rust-results.json` (one record per test), `rust-test-ids.txt`; web `web-results.json` (vitest JSON) and `web-test-ids.txt`; mutations `mutations.jsonl`, `summary.json`, `spec.json`, `logs\<id>.log`. Each run keeps `command.txt`, `git-head.txt`, `git-status*.txt`, toolchain versions and the isolated-copy audit (Rust).

**Code state:** commit `37f1fc19846a178083d9ced7a7a25aeffe65b1f9` with the audit work uncommitted in the working tree. Rust source plus lock tree hash of the final green run: `90195D94C8327BD0` (`runs\20261116-085455-rust-green-2\rust-source-tree-hash.txt`); no `Cargo.toml` or `Cargo.lock` change. No Python file was changed in this follow-up, so no Python suite was rerun (the Python evidence is in [../wp07-t5r-2026-11-15/](../wp07-t5r-2026-11-15/README.md)).

## Runs

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261116-084319-rust-red` | Rust suite with the new regressions, **before** the fix | build failed: a moved value in my new test (`failure` used after being moved into the registry) | **INVALID** (a compile error is not a red result), kept |
| `20261116-084323-web-red` | New web tests against the **pre-fix** `provenance.ts` | 952 tests: **19 failed** (12 in `datasource-fixtures.test.ts`, 7 in `provenance.test.ts`); `tsc` 0. The fixture file lacked the Rust-generated cases yet, so some fixture failures are "case missing"; the 7 formatter failures are behavioural | red evidence (valid) |
| `20261116-084414-rust-red-2` | Rust suite, test compile fixed, still **before** the fix (a reader-name field and a test-only registry accessor exist but are unused) | 242 passed, **10 failed**: 7 price-cache tests, 2 registry tests, the fixture drift test; 2 ignored | red evidence (valid) |
| `20261116-084541-rust-fixture-gen` | One Rust test with `ATHENA_UPDATE_FIXTURES=1` after the fix (writes `terminal/web/test/fixtures/data-source.cases.json` from the real registry) | 1 passed; generated copy kept beside it | yes (superseded by `-2`: the `main_only` case then used the real reader name) |
| `20261116-084630-rust-fixture-gen-2` | Same, final | 1 passed | yes |
| `20261116-084650-rust-green-1` | Full Rust suite after the fix | **252 passed, 0 failed, 2 ignored** (243 before) | yes (tests since added: one assertion block, see `-green-2`) |
| `20261116-084725-web-green-1` | Full web suite after the fix | 951 passed, **1 failed**: a test of mine used a snapshot entry as "another reader", which outranks the refresh badge | **NOT GREEN**, kept |
| `20261116-084759-web-green-2` | Full web suite, test fixed, guard v3 | **tsc exit 0; 72 files, 952 tests passed** (934 before: +18); guard: 142 denied `fetch` (relative `/api/...`), 0 absolute, 0 other denials | yes; **not a zero-violation run** (see Guard) |
| `20261116-084905-mutation-rust-f` | 12 Rust mutations (price cache, refresh-failure age and recency) on the isolated copy | 11 caught, **1 survived**: `rs-failure-written-over-served` (a failed refresh registered as the answer served: equivalent for a held symbol, a real gap for a symbol the held closes lack) | kept, survivor marked |
| `20261116-084910-mutation-web` | 8 web mutations | **8 caught** | yes |
| `20261116-085455-rust-green-2` | Full Rust suite after adding the regression for the survivor (a symbol the held closes lack, asked while the refresh fails) | **252 passed, 0 failed, 2 ignored**; no compiler warning; 254 listed tests | yes |
| `20261116-085536-mutation-rust-f2` | The survivor again, against the new regression | **caught** by `cached_prices_after_a_refresh_failure_keep_serving_with_their_own_provenance_and_the_failure_is_kept_apart` | yes |

## Commands (exact)

- Rust: `scripts\run_rust.ps1 -Label <label> [-Filter <filter>] [-UpdateFixtures]`: a fresh allowlist copy of `*.rs`, `Cargo.toml`, `Cargo.lock`, two Python files the helper tests need and the one UI fixture, stamped with the current time, then `cargo test --offline --locked --no-run`, `-- --list` and `cargo test --offline --locked` with `CARGO_TARGET_DIR=C:\Users\LEGION\ath-wp07\t` and `CARGO_NET_OFFLINE=true`. The stage is inspection-based and **enforces nothing at run time**: it is not a sandbox.
- Web (cwd `terminal\web`): `scripts\run_web.ps1 -Label <label>`: `npm exec -- tsc --noEmit`, then `npm test -- --config <run>\vitest.config.mjs --reporter=default --reporter=json --outputFile.json=<run>\web-results.json` with `npm_config_offline=true`. A run with a test file that did not load, a failed test, a failed `tsc` or a non-zero exit is marked NOT GREEN.
- Mutations: `scripts\mutate.py <spec> <label>` (Rust: `mutations-rust-f.json`, `mutations-rust-f2.json`; web: `scripts\run_mutations_web.ps1 -ConfigRun <web run> -Template mutations-web-f.template.json`). A mutation that breaks the build is `invalid`, a pattern that is missing or ambiguous is `pattern_error`; the file is always restored and its hash verified.
- Fixtures: `run_rust.ps1 -Label <label> -Filter the_registry_payloads_match_the_ui_fixtures -UpdateFixtures` regenerates them; without the switch the same test **fails** when the Rust payloads and the file differ.

## Guard

`guard.setup.v3.mjs` is a tripwire, not a sandbox (coverage and limits: [../wp07-2026-11-15/GUARD-LIMITS.md](../wp07-2026-11-15/GUARD-LIMITS.md)). The web run recorded the same **142 denied `fetch` attempts** as the previous run and no other denial. That is **not a zero-violation run**.

## Not established

The F1 fixture cases replay the entries the real price cache wrote, with only the clock replaced, so registration timing is covered by the Rust tests and not by the fixtures; a real long writer lock and a real multi-gigabyte warehouse; DuckDB's POSIX lock wording; the Chart Lab compare series (documented qualification); the hash cache's same-size, same-mtime limitation (documented qualification); single machine, single run of each suite; the Rust mutation records list the failing tests, the web mutation records list none (the outcome is caught or survived; the per-mutation log holds the vitest output).
