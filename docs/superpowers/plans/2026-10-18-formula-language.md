# Formula language Implementation Plan (5h)

> **For agentic workers:** execute task by task, tests first. Steps use checkbox syntax.

**Goal:** a formula language that compiles to the strategy tree, formula indicators on the chart, a strategy builder, Python indicator files.

**Architecture:** `terminal/web/src/formula/` (parser, evaluator, catalogue) over the Python strategy tree; server documents for formulas and strategies; a child-process runner for Python indicators.

## Global Constraints

- The tree is the strategy model's version 1; Python re-validates every tree (`parse_strategy`).
- Limits: 60 parts, 8 levels, 8 conditions per `and`/`or`, windows 2 to 1000, shifts 0 to 500.
- Unknown is neither true nor false; a bar with an unknown condition is never a signal.
- Python indicator files are never created or edited by the server; run in isolated mode with a time limit; enabled by hash.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task A1: parser

**Files:** create `formula/ast.ts`, `formula/catalogue.ts`, `formula/parse.ts`, `test/formula-parse.test.ts`.

- [ ] Tests first (operators, precedence, shifts, lines, parameters, limits, errors with positions, case, type errors); implement; commit.

### Task A2: evaluator and parity

**Files:** create `formula/eval.ts`, `scripts/formula_golden.py`, `terminal/web/test/fixtures/formula-golden.json`, `tests/test_formula_golden.py`, `test/formula-eval.test.ts`, `test/formula-parity.test.ts`.

- [ ] Tests first (each node kind, three-valued logic, warm-up); the generator and the golden file from Python; the page's parity test; a Python test that the committed file equals a fresh run and that every tree passes `parse_strategy`; implement; commit.

### Task B: formula indicators in the Chart Lab

**Files:** `formula/registry.ts` (a def from a formula), `chartlab/registry.ts` (custom defs), `chartlab/view.ts` (menu, editor), Rust `/api/formulas`; tests and e2e.

- [ ] Document store and validation; Indicators menu section; editor with errors; draw, hide, edit, delete; templates and layouts keep the name; e2e; commit.

### Task C: strategy builder

**Files:** Research tab form; Rust and Python `/api/backtest` with a strategy; `/api/strategies` document; tests and e2e.

- [ ] Python accepts a strategy and returns the summary; Rust route; form with errors; saved strategies; e2e; commit.

### Task D: Python indicators

**Files:** `src/athena/indicators_user/` runner and contract, Rust `/api/pyind` routes, Chart Lab menu section; tests and e2e.

- [ ] Contract and runner with time limit and isolated mode; listing with hashes; enable by hash; draw; the page's warning; tests including a hung file, a raising file, a changed file; e2e; commit.

### Task E: docs and suites

- [ ] TRD (2.38 to 2.41), master design, `graphify update .`, full suites, commit.

## As built (2026-10-18)

All five tasks done and committed. Formula parser, evaluator and the golden-file parity test with the Python engine (A1, A2); formula indicators with their dialog, menu section and server document (B); the strategy builder with saved strategies, end to end on a real backtest (C); the Python runner (19 tests), the Rust routes (6 tests), the Chart Lab section with the switch-on dialog and an end-to-end test (D); docs and suites (E: vitest 508, Playwright 100). Deviations from the plan: the formula registry lives in `formula/store.ts` and `formula/defs.ts` rather than `formula/registry.ts`; custom definitions are kept per source (`formula`, `python`) in `chartlab/registry.ts`; `caption` falls back to the indicator's name until a Python file has run.
