# Offline guard v4 for the web gate

Follow-up to the first hosted run of the pull request (17 November 2026). **v3 is kept unchanged** (`baselines/wp07-2026-11-15/scripts/guard.setup.v3.mjs`, SHA-256 `08B7B5A3DBAB4834E9A63BD000ADF9A00547050707C550646AA8C693D3548BBF`); v4 is a new, separately pinned file that replaces it in `web_gate.ps1` on the maintenance branch only. Nothing here has run on GitHub yet.

## What happened

The first hosted run of `web_gate.ps1` failed: 1,021 of 1,058 tests passed and 37 failed, all with "Test timed out in 5000ms". The gate also recorded 896,914 file-system events "outside the project", and every one of them (100%) was the guard writing its own hit log (a 230 MB file). Locally the same suite passes with no such events.

## Root cause: two defects, one hidden by the other

1. **The logger was not non-reentrant.** v3 wrote its log with `fs.appendFileSync`, which Node implements by calling `fs.writeFileSync`, and the guard had already wrapped `writeFileSync`. Whenever the log folder was judged "outside" the allowed roots, each log write was itself recorded as an outside write, which wrote the log again, until the stack overflowed. One genuine outside write produced **2,778** log records in a reproduction outside the repository (2,776 of them naming the log file).
2. **The log folder was outside the allowed roots on the runner.** The gate put the log under PowerShell's temp folder and allowed node's `os.tmpdir()`; on the hosted runner those did not compare equal as strings (the runner's node temp value is not in the saved artifacts; the probable cause, unconfirmed, is a short 8.3 name versus the long name of the same profile folder). Locally both spell the same string, so the first defect never showed.

The link between the logging storm and the 37 timeouts (those five test files ran 41 to 93 seconds each) is strong but was not proven by a rerun. No application code is implicated by the evidence.

## What v4 changes

| | v3 | v4 |
|---|---|---|
| Logging | `appendFileSync` through the guarded `fs` | a descriptor opened once, written with the captured low-level `writeSync`; a log write never passes through a guarded function |
| Re-entry from anything else | unbounded recursion | **counted and reported**: the event is kept (kind, detail) and written as a `log-reentry` record after the outer record; the gate fails on it |
| Size | unbounded | `max_records` per process (default 2000, three slots reserved); on overflow one `log-overflow` record, a stderr marker and a count in the summary; the gate fails on it |
| Write failure | swallowed | counted, a stderr marker, a `log-write-failure` record when writing works again, counters in the `guard-summary` record; refusals (a denied call still throws) are unaffected; the gate fails on any of these |
| Containment | lower-cased string prefix | canonical path (real path of the nearest existing ancestor, then the rest), compared by whole path segments: a sibling that shares a prefix is outside, a junction inside a root that points outside is outside, two spellings of one folder are the same folder |
| Protected names | the rule applied to the supplied spelling only; for a two-path call the source was judged (and could be recorded) before the destination was looked at | the rule applied to the supplied spelling of **every** path argument first (text only: a protected spelling is never resolved), then to the **canonical form** of every remaining argument (so a link or junction into a dot-folder is refused), both arguments of two-path calls; if any argument is protected the call is refused and the original function is **never called**; the outside source or destination of a refused call is still recorded; a refusal through a link names the canonical path first (`canonical <path> <- supplied <path>`) |
| Relative paths | judged as text (always "outside") | resolved against the current directory first |
| Machine-specific values | two source lines rewritten per run (hash of the copy checked for "exactly 2 lines") | none: a JSON configuration (log file, roots, limits) named by `ATHENA_GUARD_CONFIG`; the pinned file is the file that runs |
| Records of the environment | none | one `guard-config` record: Node and PowerShell temp paths (raw and canonical), the effective allowed roots (raw and canonical), the log file, whether the log folder is inside the roots, the configuration hash |
| Installing twice in one process | wrapped twice | idempotent (wrapped once); a second installation with a different configuration is refused |
| No configuration | not applicable | the guard **fails closed** (no run) |

**Unchanged on purpose:** what is refused and recorded (network from the page and from node, child processes), the protected-name rule itself (which names are protected; v4 only applies it to more spellings of the same path), the list of guarded file-system functions, the source-and-destination rule for two-path calls, and the limits (a tripwire, not a sandbox: `baselines/wp07-2026-11-15/GUARD-LIMITS.md`). The allowed roots are the same five as before (the candidate's web folder, its `node_modules`, the scratch folder, the node install, node's temp folder). Nothing was hidden or widened to make a run pass: the 1,058 tests, the existing timeouts and every existing gate check are unchanged, the 142 denied relative fetches are still reported, and any genuine outside write is still a failure.

## Files and pins

| File | Role | SHA-256 (LF bytes, also the default of the matching `web_gate.ps1` parameter) |
|---|---|---|
| `guard.setup.v4.mjs` | the guard (Vitest setup file) | `600924933BC19692A4B5F40CB114708165712F4823FA5132976F48DA29F342C8` |
| `guard_selftest_v4.mjs` | the v3 self-test case for case (68) plus 7 new in-process cases | `6BF12D2E4F18BC6E72B231E1893C3F02FEEDD69B5EE4C412EA8AB4B5A2A5AF7D` |
| `guard_regressions_v4.mjs` | 39 multi-process regressions (below) | `DE6EEB2F91B492DDBB7A77AFF10B7721909A451BD8E83DED3C8C1076C9E3334A` |
| `guard_judge_v4.mjs` | reads a hit log and says what it holds and whether it is complete | `B981D8AE8D0C39C73127774F564533AAE28CAC28F6139D04E66EAFE64A8E3AE9` |

`web_gate.ps1` refuses to run if any of the four differs from its pin, and checks that the historical v3 file still has its recorded hash. It writes `temp-paths.json` (PowerShell and node temp folders, canonical forms, the roots given and the roots the guard recorded) and the judgements of both hit logs next to its summary.

## Regressions (`guard_regressions_v4.mjs`, one child process per scenario)

1. **Mismatched log folder** (the hosted-runner failure): 25 genuine outside writes give exactly 25 records, none names the log, the log holds exactly config, installed, 25 records and a summary, and the child finishes quickly.
2. **Logger re-entry**: a hook that writes a file while a record is being built is counted and reported with its details, the outer event is recorded once, and nothing recurses.
3. **Overflow** is bounded, marked in the log and on stderr, counted in the summary, and judged incomplete.
4. **Write failure** (descriptor closed under the guard): the child survives, `fetch` is still refused, the failure and the missing summary are reported on stderr.
5. **Fails closed**: no configuration, a configuration for another version, no roots, a log that cannot be opened.
6. **Repeated installation**: three imports wrap once (one record for one outside read); a different configuration is refused.
7. **Path containment** with real directory links: sibling prefix, `..`, a junction out of a root, an alias spelling of a root, case, trailing slash, `.` and `..` segments, relative paths in two directories, missing paths; genuine outside writes (directly and through the junction) are still recorded.
8. **Node and PowerShell temp** naming one directory in two spellings: nothing is outside, and the config record holds both paths, their canonical forms and says they are one directory.
9. **The judge**: a broken line and a write-failure record make a log incomplete while real events are still counted.
10. **Protected targets behind innocent spellings, intercepted and virtual**: before the guard is installed the child replaces the file functions under test with recorders that never touch the disk (for paths in the scenario's own folder; everything else, such as the module loader reading the guard's source, passes through) and replaces the `realpath` the guard uses with one that maps `<root>/vlink` to `<root>/.vault`, a folder that does not exist. Write, read, exists, stat of the link itself, rename (protected destination; protected source), copy, symlink, promise rename and promise write through the link are all refused with the recorder never called; a protected spelling is refused from its text without being resolved; each refusal is recorded as denied with the canonical path; the outside source of a refused rename is still recorded; inside and outside controls reach the recorder exactly once.
11. **A real junction into an empty dot-folder**: write, copy and rename through it are refused and nothing reaches the folder (the source is not moved); a junction to an ordinary folder still works.

Every one of fifteen deliberate breakages of the guard (no re-entry flag, `appendFileSync` logging, prefix containment, no canonical form, no idempotency, no bound, a different configuration accepted, a swallowed write failure, outside writes not recorded, protected-name rule removed, `fetch` not refused, the two-path rule removed, the canonical form not checked for protection, the canonical form resolved before the supplied spelling is checked, the refusal made only after the original function ran) is failed by the regressions or the self-test; the unbroken copy passes both (39 and 75 cases).

## Which gate script goes with which tip

`final_gate.ps1` runs the stage scripts of the folder it is started from. The integration tip (`f405f30`) was verified with the web gate of that tip (guard v3, no `guard\` folder); to re-verify it, start `final_gate.ps1` from a checkout of the integration tip. The maintenance branch uses this web gate (v4).

## Checkout identity (for future runs)

`scripts/ci/record_checkout_identity.sh` writes `checkout-identity.json` in every job that checks out the repository and the artifacts carry it: the commit the job is on, its tree and its parents (read from the commit header only, which ends at the first empty line, so a line in the commit message that looks like `parent <sha>` is not taken for a parent; this also works in a shallow checkout), `GITHUB_SHA`, the pull request's head and base commits, the relations between them, and, when an anonymous fetch of the head commit is possible, whether its tree equals the checked-out tree. For a `pull_request` event `GITHUB_SHA` is GitHub's temporary merge commit, which is regenerated and may no longer be fetchable later.

**The first run's identity stays unresolved.** Its jobs reported checking out `15dbd9ccb5dbf34fae002b6ed1a2670f52df37bc`; that commit can no longer be fetched, GitHub now reports the merge commit as `c2f2494a8cb441299cd60b139baab9b8baecb638` (parents `37f1fc1` and `c2a6e23`, the same tree as the pull request head), but nothing recorded during the run shows that the run's merge tree was that tree. It is not inferred here.

## Limits that remain or are new

- Still a tripwire, not a sandbox (worker threads, native addons, `process.binding`, descriptors opened before the guard, the Vitest main process, processes started outside `child_process`).
- The ESM loader's own reads of files outside the roots are public file-system calls and are recorded as outside events (the regressions show this for re-imported guard files); the Vitest run records none.
- The `guard-summary` record is written when a process exits. Under Vitest's fork pool the workers are not reliably ended through exit handlers: across the local runs of the gate the summary count (`summaries` in the gate's summary) was **0 in one run and 1 in the next, for 76 workers each time**. The gate therefore reports the count as information and does not require it; losses are caught by the records written at the moment they happen (`log-overflow`, `log-write-failure` when writing works again, `log-reentry`) and by the stderr markers (`ATHENA-GUARD-V4 ...`) that Vitest passes into `web-test.log`, which the gate scans. The counters in the summary are therefore a convenience, not the safeguard.
- Canonical directories are cached per process (existing directories only); a directory replaced by a link during a run after it was first judged is not re-judged.
- Finding the canonical form of a path whose spelling is not protected resolves its links, which reads link and directory metadata along the way (on Windows the system opens the final path without data access to do so). It never reads or writes a file's contents, and a path whose spelling is already protected is never resolved. A link changed between the check and the call (a race) is outside what a tripwire can see.
- Not verified on GitHub: the hosted first run of v4, the runner's temp folder values, and the checkout-identity step have not run there. The Playwright browser suite is not part of this gate.
