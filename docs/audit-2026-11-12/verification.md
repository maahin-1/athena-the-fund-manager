# Verification record — 12 November 2026

Baseline application checkout: `37f1fc1`. Tests used installed local dependencies; nothing was installed, no live LLM or broker request was made, and no credential file was opened. Public GitHub reads were separate from application testing. The audit did not launch the live terminal/supervisor.

## Completed checks

| Check | Observed result | Scope / qualification |
|---|---|---|
| Broad Python offline run | **2,555 passed, 74 skipped**, 277.28 s | Nine test modules explicitly excluded, listed below; not an unqualified full-suite pass |
| Final environment guard tests | **22 passed**, 0.61 s | Synthetic tool JSON and fake paths only; no environment target file accessed |
| Agent-focused Python review | **229 passed**, 64.92 s | Agent/council/forensic/retrieval/LLM logic with fakes; overlaps broad run |
| Terminal/broker/operations Python focus | **70 passed**, 19.79 s | Fakes and paper-only checks; overlaps broad run |
| Formula golden fixture | **Passed**: `golden file is current` | `python scripts/formula_golden.py --check`; no fixture rewrite |
| TypeScript unit/component suite | **70 files / 895 tests passed** | Existing Vitest suite, offline |
| Frontend production build | **Passed** | TypeScript + Vite; 970.10 kB minified JS / 281.41 kB gzip, large-chunk warning |
| Rust full attempt | **179 passed / 27 failed**, 206 total | Compiled with `cargo test --offline`; 23 socket-dependent failures from sandbox restrictions, four indicator-child failures investigated separately |
| Rust indicator rerun | **6 passed**, 11.07 s | `cargo test --offline pyind::tests -- --test-threads=1`, writable TEMP/TMP; resolves the four indicator-child failures as environment/temp issues |
| Patch whitespace check | Passed | `git diff --check`; new untracked Markdown/link checks performed separately |

Counts overlap and must not be summed. The broad Python run collected 18 guard cases before four final Windows regression cases were added; the separate final guard run covers all 22. Application source did not change between those runs.

## Broad Python command and exclusions

The runner selected `.venv/Scripts/python.exe`, set **only that child process's** TEMP/TMP and `tempfile.tempdir` to a writable generated audit directory, then called pytest with:

```text
-q --maxfail=3 -p no:cacheprovider
--basetemp=docs/audit-2026-11-12/full-suite-tmp5
--ignore=tests/test_dashboard_app.py
--ignore=tests/test_dashboard_live_panel.py
--ignore=tests/test_dashboard_market.py
--ignore=tests/test_dashboard_verdict_panel.py
--ignore=tests/test_llm_basics.py
--ignore=tests/test_llm_probe.py
--ignore=tests/test_llm_router.py
--ignore=tests/test_angel_login.py
--ignore=tests/test_kite_login.py
```

The four Streamlit AppTest modules were excluded after default-temp permission failures and controlled-temp attempts that did not complete and were interrupted. An earlier first-failure run recorded **373 passed, 74 skipped, one failure** at Streamlit's attempt to write its generated script into a denied sandbox temporary directory. The later noncompletion was not diagnosed sufficiently to classify as an application defect or entirely environmental. No clean Streamlit test claim is made.

The five environment/provider/account-setup modules were conservatively omitted from the broad run to honour the user's request to avoid environment files and exhausted credentials. Some router/probe logic was separately covered by the reviewer's injected-fake tests; this does not establish live provider availability. The 74 skipped cases are the suite's default live-test exclusions; `--live` was not enabled.

Initial unscoped attempts stopped on temporary-directory errors. Those interrupted runs are not presented as clean results and did not motivate application patches.

## Mechanism reproductions

- **Semantic grounding:** a negative ROE packet accepted a positive-sign claim; a P/E number accepted an unrelated revenue-growth claim; unsupported numberless certainty passed numeric checks.
- **Forensic support:** an observation about revenue and creditors passed against an unrelated board-meeting evidence item; a future-published document was accepted for an earlier case; an unknown ownership filing date was admitted.
- **Council:** numberless unsupported arguments with empty citation arrays passed the cited-ID checks.
- **Budget:** in a synthetic usage ledger, one malformed appended JSONL line reset the observed monthly count from one to zero and allowed another logical request.
- **Historical statement versions:** saving an original then a later revision made an earlier as-of latest-table read lose the original period.
- **Verdict timing:** a synthetic decision created after its five-bar return window was still awarded that earlier return.
- **PIT score tie:** equally split bullish/bearish repeats raised `ZeroDivisionError` when the modal result became neutral without a neutral confidence row.
- **Kill race:** an injected paper-only interleaving completed `kill()` before confirmation sent an order; final state had KILL present and one filled order. No broker account was connected.

See component reports for exact source lines, values, interpretation and fixes. `agent-probes/reproduce.py` retains a reusable source-only reproduction; its budget/usage files contain deliberately synthetic data. Generated paper accounts, tokens, databases and test workspaces were removed after verification.

## Not established

No Playwright/browser E2E run, live-provider probe, live-investigation benchmark, market-hours latency measurement, source refresh, real-account order test, contract-note cost reconciliation, clean-machine install, security vulnerability scan, screen-reader assessment or long-running soak was performed. No peer repository was executed. A passing fixture suite does not imply these untested properties or investment profitability.
