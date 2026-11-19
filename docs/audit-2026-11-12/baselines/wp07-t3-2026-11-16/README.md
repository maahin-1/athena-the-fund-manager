# WP07 T3 (options quote quality): verification artifacts (16 November 2026)

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Evidence for [../../IMPLEMENTATION-STATUS.md](../../IMPLEMENTATION-STATUS.md), "Batch 5, WP07 T3". Every run has its **own directory** under `runs\`, named `<yyyymmdd-HHMMSS>-<label>`; **nothing is overwritten, red and not-green runs included**. Synthetic fixtures and controlled clocks only; no production `.athena` data, environment file, live call, commit or push. The scripts are those of [../wp07-t5f-2026-11-16/scripts/](../wp07-t5f-2026-11-16/scripts/) (copied; the run directory is relative to the script), plus `mutations-rust-t3.json`, `mutations-rust-t3b.json` and `mutations-web-t3.template.json`.

**Machine-readable outputs:** Rust `rust-results.json` and `rust-test-ids.txt`; web `web-results.json` (vitest JSON) and `web-test-ids.txt`; mutations `mutations.jsonl`, `summary.json`, `spec.json`, `logs\<id>.log`. Each run keeps `command.txt`, `git-head.txt`, `git-status*.txt`, toolchain versions and (Rust) the isolated-copy audit.

**Code state:** commit `37f1fc19846a178083d9ced7a7a25aeffe65b1f9` with the audit work uncommitted in the working tree; no `Cargo.toml` or `Cargo.lock` change. The mutation runs restore every file they touch (hash-verified); `web-final` ran after them.

## Runs

| Run directory | What | Result | Valid |
|---|---|---|---|
| `20261116-120927-rust-red` | Rust suite with the new regressions **before** the implementation (the receipt fields exist, `update_at` is a stub that only does what `update` did) | 252 passed, **6 failed**: five `deriv_quotes::` tests and the quotes-route test | red evidence (valid) |
| `20261116-121050-web-red` | New web tests against a permissive stub of `quality.ts` (types and policy, every decision `ok`) and the old `assemble` | 984 tests: **32 failed**, 952 passed; `tsc` exit 1 (the new tests use types the old code lacks) | red evidence (valid) |
| `20261116-121651-web-green-1` | Web suite after the implementation | 982 passed, **2 failed**: an older `assemble` test whose quote had no book (now withheld, changed on purpose) and the "exchange clock disagrees" test, whose stand-in set the server's receipt time to the skewed exchange time | **NOT GREEN**, kept |
| `20261116-121804-web-green-2` | Same, after changing that test and the stand-in (receipts in the server's clock) | **985 passed**, tsc 0 | yes |
| `20261116-121839-rust-green-1` | Full Rust suite after the implementation | **258 passed, 0 failed, 2 ignored** (252 before); no compiler warning | yes |
| `20261116-122051-web-green-3` | Web suite with three more tests (the forward's time is its oldest input; a stale pair is not a parity input; a server clock apart from the page's) | **988 passed**, tsc 0; guard: 142 denied `fetch`, 0 other | yes; **not a zero-violation run** |
| `20261116-122216-mutation-rust-t3` | 8 Rust mutations on the isolated copy | 7 caught, **1 pattern error** (`rs-first-sight-no-book-time`: the pattern spanned lines) | kept, marked |
| `20261116-122502-mutation-rust-t3b` | The pattern-error mutant, one line | **caught** | yes |
| `20261116-122217-mutation-web` | 26 web mutations (quality rules, forward choice, filtering, clock, session, badge) | **26 caught** | yes |
| `20261116-122549-web-final` | Web suite after the mutation runs and one e2e expectation changed | **988 passed**, tsc 0; guard: 142 denied `fetch`, 0 other | yes |

## Commands (exact)

- Rust: `scripts\run_rust.ps1 -Label <label>` (a fresh allowlist copy of `*.rs`, `Cargo.toml`, `Cargo.lock`, two Python files the helper tests need and the one UI fixture, stamped with the current time; `cargo test --offline --locked` with `CARGO_TARGET_DIR=C:\Users\LEGION\ath-wp07\t`, `CARGO_NET_OFFLINE=true`). Inspection-based; **enforces nothing at run time**: not a sandbox.
- Web (cwd `terminal\web`): `scripts\run_web.ps1 -Label <label>`: `npm exec -- tsc --noEmit`, then `npm test -- --config <run>\vitest.config.mjs --reporter=default --reporter=json --outputFile.json=<run>\web-results.json` under guard v3 (a tripwire, not a sandbox: `../wp07-2026-11-15/GUARD-LIMITS.md`) with `npm_config_offline=true`.
- Mutations: `scripts\mutate.py <spec> <label>` (Rust) and `scripts\run_mutations_web.ps1 -ConfigRun <web run> -Template mutations-web-t3.template.json` (web). A mutation that breaks the build is `invalid`, a missing or ambiguous pattern is `pattern_error`; every file is restored and its hash verified.

## Not established

The policy limits are not calibrated on real market data; a real stream, a real broker clock, a real browser and the Playwright e2e suite (one expectation changed, **not run**) were not used; a quiet, valid standing book cannot be told from a stale one with this stream; single machine, single run of each suite; the guard is a tripwire and the Rust stage enforces nothing at run time. Python was not changed and was not rerun.
