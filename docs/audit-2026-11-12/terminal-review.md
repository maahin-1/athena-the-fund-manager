# Terminal, operations and broker review — 12 November 2026

Scope: static review of Rust terminal, TypeScript UI, Python process boundaries, data supervisor and broker desk; existing offline tests and a paper-only race reproduction. No broker account connection, live model call, credential-file content inspection or market-hours latency test was performed. Source was reviewed at working checkout based on `37f1fc1`; existing untracked user files were preserved.

## Verdict

The terminal is suitable for a prepared personal-project demonstration and supervised research. Its architecture is substantially more complete than a notebook of agent prompts: real push quotes, docked linked panels, a warehouse, a Python research service, contract-aware options calculations and offline tests exist. It is not yet a dependable execution/risk system for real trading. That verdict is about operational guarantees, not whether a displayed strategy could make money.

The Rust terminal itself has market-data/research routes, not an order endpoint. The wider Python project **does** implement real-order adapters behind a separate CLI desk. README statements that the entire project places no orders are therefore false as descriptions of capability, even though live orders require explicit configuration, pinned limits, a signed audit log and typed console confirmation.

## Severity-ranked findings

### T1 — High: the kill switch can finish while a concurrent confirmation still sends an order

Evidence: `src/athena/brokers/desk.py:100–138` locks confirmation through the send; `desk.py:149–168` does not acquire that lock. The last safety check is at line 125 for live orders and line 109 for paper orders; the actual send is at line 132. `kill()` writes the switch and enumerates/cancels the current open orders independently.

Interleaving: confirmation passes its last check; kill writes KILL, sees no open orders and returns; confirmation sends its already-checked order. The post-typing recheck is a good safeguard but leaves this smaller race.

Reproduced safely with the existing `tests/test_brokers.py` paper fixture: wrapped `audit.append` so that the `placing` event invokes `desk.kill()` before continuing. Result: `{'kill_exists': True, 'order_status': 'filled', 'order_count': 1}`. This did not call any external broker. The fixture is under this audit's `kill-race-fixture` folder.

Fix: set KILL immediately, then serialize the cancellation sweep with the same desk lock; recheck KILL immediately before the send. Define explicitly how an order already in transit is reconciled. The kill operation must wait until an existing send can be identified and cancelled or reported as filled/unknown. Never promise that a file can undo a fill that already happened.

### T2 — High for trading use: live daily-loss protection is deliberately bypassed

Evidence: `src/athena/brokers/safety.py:97–102`. For a live adapter, the daily-loss finding always passes with text explaining that it is not measured. This is transparent, but a configured `max_daily_loss` does not stop live orders.

Meaning: order-size and deployed-capital limits bound exposure, but do not implement an account loss budget. An analyst can work without this. A trader must not mistake the configuration field or a passing finding for a functioning loss circuit breaker.

Fix: use reconciled broker positions/trades and an explicitly defined realised/unrealised loss basis, or refuse live operation when the user requires a daily-loss limit. Represent this finding as `unavailable`, not `passed`; show enforceable and advisory limits separately. Weekday-only hours at `safety.py:126–129` also do not use the existing exchange-holiday calendar.

### T3 — Medium: one fresh options contract can label a chain live while stale contracts still generate IV and Greeks

Evidence: `terminal/web/src/options/livefeed.ts:94–97,130–163` tracks the newest time across quotes for the badge; `options/chain.ts:110–119` computes a price/IV without inspecting `Quote.time`; `options/assemble.ts:79–105` builds the chain from all supplied quotes. The data type includes individual quote timestamps, but no individual age/synchronisation filter is applied to these calculations.

Example: an actively traded near-money option updates now while an illiquid wing or the future retains an old print. The page can show “live prices” and calculate a mathematically valid IV from economically inconsistent inputs. A two-sided-price filter exists for the smile and is useful; it does not establish quote freshness. A last-trade timestamp also is not necessarily a book-update timestamp, so deleting everything with an old trade time is insufficient.

Fix: preserve receipt time, book time where available and trade time separately. Show freshness per row and the forward source's age. Withhold or mark IV/Greeks when the required input timestamps/spreads fail a defined quality rule; distinguish a calculation on last-session data from a tradable observation. This does not invalidate Black-76 itself.

### T4 — Medium: supervisor timeout releases the writer lock without waiting for process exit

Evidence: `src/athena/ops/supervisor.py:198–205`. The `max_hours` path calls `proc.terminate()` and returns immediately; `finally` releases the Job lock. The parent-disappearance branch does wait and escalate to kill, but timeout does not.

Meaning: a process handling termination slowly, or writing during its shutdown, can still own the underlying database after the scheduler advertises the writer lock as free. Windows termination is normally abrupt, reducing the window, but the stated cross-platform behaviour cannot rely on that. Child process trees are not managed as a group.

Fix: share one terminate/wait/kill/wait routine across timeout and shutdown paths. Release locks only after confirmed exit; manage child groups or Windows Job Objects where jobs spawn descendants. Cover this with a fake process that remains alive after terminate.

### T5 — Medium: fallback warehouse snapshots are not identified as snapshots in the API

Evidence: `terminal/src/snapshot.rs:16–25` falls back on any open error when a snapshot exists, returning only a Connection. `terminal/src/server.rs:165–190` labels daily-bar responses “warehouse”/“warehouse, raw” without snapshot time. `src/athena/ops/snapshot.py:25–36` can retain an older snapshot if replacement fails.

This is a sensible availability technique for a local DuckDB application, but loses provenance. A chart can keep working during an ingest while showing an older generation than another panel; an unexpected main-file error can also be hidden behind the snapshot. Last bar dates mitigate part of this, not the missing database-generation identity.

Fix: return `{connection, source_path, snapshot_created_at, data_as_of, fallback_reason}` from the reader boundary; display a compact “snapshot while updating” state. Distinguish expected lock errors from corruption/missing-file errors. Avoid implying that continuous availability guarantees current data.

### T6 — Medium: operation timeouts do not bound queue time, and live analysis shares one serial worker with backtests

Evidence: `terminal/src/analyse_worker.rs:110–120` waits for its mutex before starting the 300-second answer timeout; `helper.rs:83–102` similarly waits for a slot before its response deadline. Startup has another 180/60-second timeout. The UI has a per-view busy guard, not a server-wide request queue contract.

Meaning: another tab can submit work while a long analysis runs. Its request can spend minutes waiting before its own advertised timeout starts. Backtests and live analysis block each other. Serial execution is reasonable for one person's first release, but it is not responsive concurrent service behaviour. There is no explicit user cancellation/job ID for the live-analysis worker. Forensic and verdict work have separate single-job managers, which is better bounded but duplicates orchestration patterns.

Fix: use a bounded queue with job IDs, visible queued/running/cancelled states, and deadlines covering queue + execution. Separate deterministic backtest work from LLM calls. Preserve the existing conservative worker count rather than unbounded parallelism.

### T7 — Medium for demo/reproducibility: startup can continue after native build failures

Evidence: `scripts/terminal.ps1:14–24` calls npm and cargo without checking `$LASTEXITCODE` or explicitly enabling native-command error behaviour. `$ErrorActionPreference = 'Stop'` alone does not reliably turn a native executable's nonzero exit into a terminating error across supported PowerShell environments. The script then starts the browser and runs the existing release binary.

Meaning: a failed frontend/Rust build can accidentally demo old artifacts. Source freshness considers web `src` and index.html, not all build/config/lockfile inputs.

Fix: explicitly test every external command exit code and stop before launch; build with locked dependencies; include build inputs in freshness or always perform the inexpensive frontend build. Add a `--check`/preflight mode showing source commit, artifact build ID and source-data dates.

### T8 — Low/medium: research failures are hard to diagnose; the web bundle eagerly imports most major views

Evidence: `terminal/src/analyse_worker.rs:60`, `chart_explain.rs:46`, `sentiment.rs:59`, `forensic_job.rs:130` discard child stderr. Warm helpers have a bounded error log (`helper.rs:25–32`), but this pattern is not shared. Most errors deliberately become short safe messages, which is correct for the page but should retain redacted diagnostics locally.

Build observation: the web build emits 970.10 kB of minified JS (281.41 kB gzip) and warns about large chunks. `terminal/web/src/main.ts:13–20` statically imports the large specialised pages. The app is local, so this is chiefly startup/maintainability cost, not an urgent network problem. Lazy page construction already exists for some views; dynamic imports can finish that separation.

Fix: one subprocess/job abstraction with redacted bounded logs, operation IDs and timing; lazy-load options/chart-lab/forensic pages. Do not add distributed infrastructure merely to solve a single-machine problem.

## What is well implemented

- Loopback-only binding (`terminal/src/main.rs:219`) and Host/Origin/Sec-Fetch-Site checks (`server.rs:64–110`) address DNS rebinding, cross-origin WebSockets and blind cross-site browser requests. This is intentionally a local single-user service, not a remotely authenticated product. Non-browser local processes remain trusted; that is appropriate to this deployment boundary.
- UI construction mostly uses DOM text nodes, not HTML interpolation. Fundamentals links additionally use an exchange-host allowlist (`web/src/fundamentals/view.ts:31–52`). Other link presenters should use a common parsed http/https allowlist rather than differing per-page rules.
- User Python indicators are not uploaded as arbitrary code through the API. Names and hashes are checked; enablement is tied to the bytes inspected; the runner compiles those same bytes. `terminal/src/pyind.rs:1–4,65–101,147–193` clears inherited environment, uses isolated Python and kill-on-drop timeout, bounds concurrent runs and describes honestly that it is **not a sandbox**. A trusted indicator still has the user's file/network rights. Output-size checking occurs after buffering, so it is not a memory-resource sandbox either.
- Live order confirmation is structurally separated from agent prose. Paper mode is default; live preview requires pinned limits and a keyed log; typed confirmation is required at a console; the rules are rechecked after typing (`brokers/desk.py:119–128`). Do not describe this as protection from arbitrary malicious code running as the same user; `brokers/human.py` correctly states that limit.
- `brokers/live/base.py` writes send intent before the HTTP request and blocks blindly retrying an unresolved outcome. This addresses the serious duplicate-order failure mode when a broker receives a request but its reply is lost.
- The quote hub, changed-only push frames, bar aggregation, separate derivatives keys, cached deterministic data and warm Python helpers fit the application's real needs. Python retains research/TA/data tooling while Rust handles a long-lived push server. This is a reasonable split, although every cross-language schema becomes another maintenance obligation.
- The supervisor runs jobs one at a time with named locks, backoff and parent lifetime, rather than allowing all loaders to contend for DuckDB at startup. Fake feed does not start the supervisor.

## Why and when the design appeared

These are documented rationales, not assumptions about the author's intent:

- 14 October: terminal spec `docs/superpowers/specs/2026-10-14-phase-4a-terminal-design.md` says Streamlit reruns could not supply tick-driven docked workspaces. It chose Rust/axum, plain TypeScript DOM, Dockview and Lightweight Charts, keeping Python research separately.
- 16 October addenda moved stored analysis/verdicts, responsive layouts and full reasoning into the terminal. The documented issue was an over-wide laptop layout and too-small reasoning. This is evidence of iteration on actual usability, not merely feature collecting.
- 27 October (`0510a48`): broker-neutral paper desk, limits, confirmation tokens and audit chain.
- 28 October (`ad44f27`): desk locks, live quote checks and local API hardening.
- 29 October (`3296699`): five live adapters behind console/pin/signature gates; (`ac7511d`) supervisor tied to terminal lifetime; (`f80b5d9`) snapshots to keep charts/pages available during writes.
- 7 November (`aba6505`): forensic analysis became start-and-poll with a versioned report schema.
- `terminal/src/helper.rs:1–5` records the measured reason for warm helpers: about 1.2 s of a 1.3 s page was Python startup; keeping helpers alive aimed at 0.1–0.3 s replies. Those are source-recorded historical measurements, not timings independently remeasured in this audit.

## Verification actually performed

| Check | Result | Interpretation |
|---|---|---|
| `npm test -- --reporter=dot` in terminal/web | 70 files, 895 tests passed | Existing UI logic/component checks passed offline |
| `npm run build` | TypeScript check + Vite passed | Production assets compile; bundle-size warning noted |
| `cargo test --offline` | 179 passed, 27 failed, 206 total | Compiles; 23 network/socket tests blocked in sandbox (Windows error 10013 or resulting timeout); 4 indicator-child failures needed controlled-temp rerun |
| `cargo test --offline pyind::tests -- --test-threads=1` with TEMP/TMP in writable audit folder | All 6 passed, 11.07 s | Resolves the indicator-child failures as temporary-directory/environment issues; remaining full-suite limitation is localhost socket access |
| Python targeted broker/concurrency/live-gate/supervisor/helper/indicator suite | 70 passed, 19.79 s, with workspace `--basetemp` | First attempts failed at temporary-directory setup; these were environment failures, not product assertions |
| Kill-versus-confirm paper reproduction | Order filled after kill returned, switch still on | Direct mechanism evidence for T1; no real account involved |

No Playwright browser run was performed. Existing E2E config starts a fake-feed server, seeds fixtures and disables the chart model, but uses an existing warehouse path; other research buttons can still require credentials/network. A dedicated demonstrator profile should guarantee every source/model route is a stored fixture. No live tick latency, broker account reconciliation, real contract-note costs, accessibility screen-reader run or production soak was validated here. Passing offline tests does not certify those.

## Readiness by use

**Prepared demo: conditional yes.** Freeze a build, seed known data, show a recorded/stored verdict and quoted documentary evidence, and label synthetic options prices. Make failure states part of the demonstration. The demo should not depend on an exchange site or LLM quota being available on demand.

**Personal analyst desk: useful with supervision.** It can reduce searching and organise evidence. Source dates, coverage, basis and reconciliation still require the analyst's judgement. Add exportable reports with immutable provenance, operation diagnostics and a saved-case list before calling it a repeatable daily workflow.

**Active trader/execution tool: no readiness claim supported.** Resolve T1, implement or explicitly disable unmet loss protections, qualify per-contract quote age, validate real account order-state transitions and costs in a deliberately controlled programme, and run long market-hours disconnect/restart/reconcile tests. Low UI latency does not establish reliable market data or execution.

**Remote/multi-user service: not this architecture's current contract.** Binding it to a public interface is not a deployment switch. It would need account/session isolation, permissioned operations, authenticated transports, storage concurrency changes and a different process/secret boundary. Those requirements should not be imposed on a single-user demo prematurely.
