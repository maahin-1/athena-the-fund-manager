# WP07 T5 verification artifacts (15 November 2026)

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Index of the runs that verified the T5 change (snapshot generations and provenance). The change itself and its review points are in [../../IMPLEMENTATION-STATUS.md](../../IMPLEMENTATION-STATUS.md) ("Batch 5, WP07 T5") and [../../WP07-PLAN.md](../../WP07-PLAN.md). Every run has its **own directory** under `runs\`, named `<yyyyMMdd-HHmmss>-<label>`; **nothing is overwritten, failed and invalid runs included**. Synthetic stores and temporary paths only; no production `.athena` data, environment file, live call, commit or push.

**Code state:** commit `37f1fc19846a178083d9ced7a7a25aeffe65b1f9` with the audit work uncommitted in the working tree (`runs\20261115-171736-t5-python\git-status.txt`, `git-diff-stat.txt`). `terminal/` Rust source plus lock tree hash `77295909FF308C71` (37 of 37 files identical to the isolated copy, `runs\20261115-171629-t5-rust-4\rust-source-manifest.txt`); no `Cargo.toml`/`Cargo.lock` change.

## Runs

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261115-170535-t5-rust-1` | Rust, first build of the change | 225 passed, 1 failed (a racy assertion of mine on a process-wide registry shared by parallel server tests) | yes (the failure was the test's, fixed next) |
| `20261115-170727-t5-rust-2` | Rust after that fix | 226 passed | yes (its source manifest mislabels files "DIFFERENT": a path-prefix bug in the script, fixed in run 4; the tests ran from the right files) |
| `20261115-171558-t5-rust-3` | Rust | 223 passed, 3 failed | **INVALID** (`INVALID.txt`): cargo reused a stale binary, the last mutation build, because `Copy-Item` kept old timestamps; the failures are the mutation being caught. The script now stamps copies with the current time |
| `20261115-171629-t5-rust-4` | Rust, forced rebuild, corrected manifest | **226 passed, 0 failed** | yes (code-state run) |
| `20261115-172303-t5-rust-5` | Rust, with the two cross-language tests added | **226 passed, 0 failed, 2 ignored** | yes |
| `20261115-172303-t5-xlang` | Cross-language contract: Python writes / Rust reads, Rust writes / Python reads | all four steps passed (`timeline.txt`) | yes |
| `20261115-171259-t5-web-1` | Web under guard v3 | summary line said "684 passed, 0 failed" | **INVALID**: 19 jsdom suites did not load (vitest serves a setup file only from an allowed directory); a failed suite is not a failed test, so the script's first summary line hid it. Fixed: `server.fs.allow` in the config, and the script now reports INVALID when any file did not pass |
| `20261115-171525-t5-web-2` | Web, `tsc --noEmit` then `npm test`, guard v3 | **71 files, 906 tests passed**, tsc exit 0, 130 denied `fetch` (relative `/api/...`), 0 other denials | yes |
| `20261115-171736-t5-python` | Broad offline Python run, nine exclusions | **3,074 passed, 74 skipped** | yes; `test-ids.txt` holds 3,148 IDs (= 3,074 + 74), regenerated after the run (its own collect step wrote none: a doubled `-q`, see `test-ids-note.txt`); two cross-language tests added after the run began are not in it |
| `20261115-164945-guard-selftest-v3` | First guard v3 self-test | failed in the test's own helper (it called `existsSync` on a protected name, which the guard correctly refused) | superseded by the passing run `20261115-164956-guard-selftest-v3` (68 of 68), kept in `../wp07-2026-11-15/results/guard-selftest-v3` |

## Commands (exact)

- Rust (cwd `C:\Users\LEGION\ath-wp07\w\terminal`, a fresh allowlist copy stamped with the current time, `CARGO_TARGET_DIR=C:\Users\LEGION\ath-wp07\t`, `CARGO_NET_OFFLINE=true`): `cargo test --offline --locked --no-run`, `cargo test --offline --locked -- --list`, `cargo test --offline --locked`.
- Cross-language: see `scripts\run_xlang.ps1` and the docstring of `tests/test_snapshot_crosslang.py`.
- Web (cwd `terminal\web`): `npm exec -- tsc --noEmit`, then `npm test -- --config <run>\vitest.config.mjs --reporter=default --reporter=json --outputFile.json=<run>\web-results.json` with `npm_config_offline=true`.
- Python: `python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_dashboard_app.py --ignore=tests/test_live_panel.py --ignore=tests/test_market.py --ignore=tests/test_verdict_panel.py --ignore=tests/test_llm_basics.py --ignore=tests/test_llm_probe.py --ignore=tests/test_llm_router.py --ignore=tests/test_angel_login.py --ignore=tests/test_kite_login.py` (`runs\...-t5-python\command.txt`, `exclusions.txt`).

## Mutation checks (scripts in `scripts\`, each restored afterwards)

`t5_mutation_py.ps1` (12, all caught; plus the concurrent stress test 5 of 5), `t5_mutation_rust.ps1` and `t5_mutation_rust2.ps1` (12, all caught, on the isolated copy only), `t5_mutation_ts.ps1` (7, all caught). Their output was read from the console and is not saved as files.

## Guard

`scripts\guard.setup.v3.mjs` checks every path argument of two-path calls (the destination gap of v2, recorded in `../wp07-2026-11-15/GUARD-LIMITS.md`) and writes its hits to a run-scoped file. It is a tripwire, not a sandbox; its coverage and limits are unchanged otherwise. Under it the web suite recorded the same 130 denied `fetch` attempts as the baseline and nothing else; that is **not a zero-violation run**. Debugging directories `99999999-*` (the bisect of the vitest setup-file path problem) are scratch and are not copied; the scripts that produced them are (`bisect_v3.ps1`, `bisect2.ps1`).

## Not established

A real long-running writer lock with the real terminal and a real multi-gigabyte warehouse; the Rust lock classifier is tested on DuckDB's message text because one process cannot lock a file against itself; mutation outputs were not saved as files; the Python broad run is one run on one machine.
