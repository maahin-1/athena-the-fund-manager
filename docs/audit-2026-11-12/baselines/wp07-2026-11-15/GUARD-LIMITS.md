# What the web-test guard intercepts, and what it does not (WP07 baseline, 15 November 2026)

`guard.setup.v2.mjs` is a **tripwire**, loaded as a vitest setup file in every test worker. It is **not an operating-system sandbox**, and nothing in this baseline claims the web suite is "isolated" on its strength. The suite's isolation claim rests on three things together: (1) a static scan of the full import closure (70 test files, 113 source modules; `web-closure-scan.txt`), (2) this tripwire at run time, and (3) the observations recorded in `README.md` (guard hits, files touched).

## Protected paths

A filesystem call is refused (and recorded) when its path, in any directory:

| Pattern | Examples refused (self-test, all without creating or reading any such file) |
|---|---|
| basename `.env` | `.env`, `x/y/.env` |
| basename `.env.*` | `.env.local`, `.env.production`, `.env.example` |
| basename `*.env` | `prod.env`, `a.b.env` |
| basename `.envrc` | `.envrc` |
| any dot-directory segment | `.athena/warehouse.duckdb`, `.venv/Scripts/python.exe`, `.git/config` |
| any basename starting with a dot | `.hidden` |

The refusal happens before the filesystem is consulted, so a protected file is neither opened nor stat'ed by the guarded calls. The self-test proves this with paths that do not exist: the error is the guard's message, not "file not found".

## Intercepted

- **Filesystem** (default export of `node:fs`, `node:fs/promises`, and named imports through live-binding sync): `readFile`, `readFileSync`, `open`, `openSync`, `readdir`, `opendir`, `createReadStream`, `writeFile`, `appendFile`, `createWriteStream`, `mkdir`, `rm`, `rename`, `copyFile`, `unlink`, and the metadata probes `stat`, `lstat`, `access`, `existsSync`, `realpath`, `readlink`, in synchronous, callback and promise forms. The promise forms return a rejection.
- **Network as the page sees it:** `fetch`, `WebSocket`, `EventSource`, `XMLHttpRequest`, `navigator.sendBeacon`, on `globalThis` and on `window` (jsdom).
- **Network from node:** TCP connects (`net.Socket.prototype.connect`, including the normalised-argument form `net.connect` uses), `tls.connect`, `http`/`https` `request` and `get`, `dns.lookup`/`resolve*`, `dgram.createSocket`. A named pipe or IPC path is not TCP and is left alone (vitest's own workers need it).
- **Processes:** `child_process` `spawn`, `spawnSync`, `exec`, `execSync`, `execFile`, `execFileSync`, `fork`.
- **Recording:** every refusal, and every read outside the project, the temp directory and the node install, is appended to `guard-hits.jsonl` with the running test's name.

## Not intercepted (limits)

- It is JavaScript patched into the same runtime it guards. Code that wants to get around it can: native addons, `process.binding`, `process.dlopen`, WASI, WebAssembly with host imports.
- **Worker threads** started by test or source code get their own globals, which the guard does not patch. The self-test demonstrates this (`fetch` inside a `worker_threads` worker is the native one).
- **Descriptors** opened before the guard ran, and reads made through them.
- **The vitest/vite main process**, which reads configuration and the module sources of the closure, and spawns the worker processes. Vite's own automatic variable-file loading is disabled by pointing `envDir` at an empty directory, which does not stop test code from reading files; that is the guard's job and the closure scan's.
- **Processes** started by anything other than the `child_process` API.
- **Third-party internals** (`vitest`, `jsdom`, `lightweight-charts`, `fancy-canvas`) are not audited line by line.
- **Existence of paths** under the project is not hidden: only protected names are refused.

## Defects found in the guard by its own self-test (both fixed before the baseline was accepted)

1. `net.connect` was **not** intercepted by the first version: Node passes `Socket.prototype.connect` a normalised array, which the check did not unwrap. A real connection attempt to `127.0.0.1:1` reached the network stack (refused by the host). **The first web run (guard v1) therefore proves nothing about raw TCP use** and is superseded. Its results and log survive as `historical-v1/v1-web-results.json` and `historical-v1/v1-web-test.log`. **Its guard-hit log does not survive** (`v1-guard-hits.jsonl` was deleted by mistake during self-test cleanup and is not available); nothing in this folder should be read as containing it.
2. The `fs.promises.*` wrappers threw synchronously instead of returning a rejected promise.

## What the baseline run under this guard showed: not a zero-violation run

The web suite **passed its assertions (895 of 895) while the guard denied 130 `fetch` attempts** (all relative `/api/...` URLs from unstubbed default collaborators; no absolute URL; no other kind of violation). It is wrong to describe that run as clean or as having made no network attempts: the tests made 130 attempts that the guard refused and the views swallowed. Whether any of those would have produced traffic without the guard was not established; a relative URL cannot be resolved by node's `fetch`, but that is a property of node, not of the suite.

## Known gap in guard v2, fixed in v3 before any reuse: destination paths were not checked

Guard v2 checked only the **first** path argument of each filesystem call. For calls with two paths (`rename`, `copyFile`, `cp`, `link`, `symlink`, in synchronous, callback and promise forms), the **destination was not checked**, so a copy or rename *to* a protected name (`.env`, `.env.*`, `*.env`, `.envrc`, a dot-directory) was not refused. No test in the baseline does this (by the closure scan and by the guard recording no `fs.*.outside` hits), but the guard could not have stopped it. `guard.setup.v3.mjs` checks **every** path argument of those calls (source and destination, and a symlink's target), and `guard_selftest_v3.mjs` tests destinations with protected names using real temporary *source* files and no protected file created. **The baseline in this folder was taken under v2 and has not been rerun.** v2 stays as the artifact of that run; reuse the guard only as v3.

## Run artifacts are never overwritten

Every later run writes into its own uniquely named directory (`<yyyyMMdd-HHmmss>-<label>`), including a **failed build** (its log is kept under that run's name and is never replaced by a later successful one). The baseline here predates that rule: its first failed Rust build log was overwritten by the successful one, as recorded in `README.md`.

## The Rust stage has no guard

The Rust test binaries run from an allowlist-only isolated copy as the working directory, with offline locked `cargo`. By inspection no test reads a configuration or environment file, opens a runtime store, or contacts a non-loopback host. Nothing enforces that: a test binary and the project's Python (reached through a junction named `.venv`, for read use) can read whatever the user can. Isolation of the Rust stage is **inspection-based and unenforced**.
