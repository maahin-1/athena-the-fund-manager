# Peers and screens Implementation Plan

> **For agentic workers:** execute task by task, tests first. Steps use checkbox syntax.

**Goal:** peers on the company page; a typed query, saved screens, industry and index filters on the Screen tab; for every screened stock.

**Architecture:** `index_members` table (Python) feeds two extra `/api/screen` columns (Rust); peers and the query language are pure TypeScript over the rows; saved screens are one server document.

**Tech Stack:** Python + DuckDB, Rust (axum), TypeScript, vitest, Playwright.

## Global Constraints

- No ticker, sector or industry is named in code; membership and industry come from the exchange's files.
- A missing figure meets no limit and is never zero.
- Percent literals are fractions internally (`15%` is 0.15).
- Saved screens: at most 100, names 1 to 60 characters, document at most 200 KB.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task 1: index membership (Python)

**Files:** create `src/athena/warehouse/members.py`, `tests/test_warehouse_members.py`; modify `warehouse.py` (table, `replace_members`, `members_status`), `build.py` (step).

- [ ] Tests: `candidate_file(name)`; `parse_members(csv)` (Symbol and Industry columns, blank rows skipped); `refresh_members` over a temp warehouse with a fake `get` (a found file stores rows, a 404 is skipped and counted, an all-failing fetch keeps the old table, the industry of a symbol is the most common one).
- [ ] Implement; run the real refresh against the real warehouse and record the counts.
- [ ] Commit.

### Task 2: the screen API carries industry and indices (Rust)

**Files:** modify `terminal/src/analysis.rs`; test in the same file.

- [ ] Test on a temp warehouse with and without `index_members`; implement; `cargo test`; commit.

### Task 3: peers (TypeScript, pure)

**Files:** create `terminal/web/src/peers.ts`, `terminal/web/test/peers.test.ts`.

- [ ] Tests: basis industry vs sector, minimum four, ranking by market-cap closeness, limit, subject excluded, medians skip nulls, percentile with direction, a subject with no market cap, an unknown symbol.
- [ ] Implement; commit.

### Task 4: query language (TypeScript, pure)

**Files:** create `terminal/web/src/screenquery.ts`, `terminal/web/test/screenquery.test.ts`; modify `screenlogic.ts` (columns `industry`, `indices`).

- [ ] Tests: tokens, every operator, `between`, `in`, `is null`, precedence (`not` over `and` over `or`), parentheses, percent, quoted text, case, set fields, missing values, errors with positions, unknown field named with a suggestion.
- [ ] Implement `parseQuery`, `evaluate`, `FIELDS`; commit.

### Task 5: saved screens (Rust + TypeScript)

**Files:** modify `terminal/src/server.rs` (routes `/api/screens`); create `terminal/web/src/screens/store.ts` and its test.

- [ ] Tests: save and load over HTTP, validation (count, name, size), the TypeScript validator; implement; commit.

### Task 6: Screen tab (TypeScript)

**Files:** modify `terminal/web/src/screen.ts`, `style.css`; e2e `e2e/screen.spec.ts`.

- [ ] Query box with error line and field list; saved screens bar; industry and in-index filters; preset query text; CSV export; column choice.
- [ ] e2e: a query filters, an error shows, a screen saves and comes back after a reload.
- [ ] Commit.

### Task 7: Peers section (TypeScript)

**Files:** modify `terminal/web/src/fundamentals/view.ts`, `style.css`, `command.ts`; e2e `e2e/peers.spec.ts`.

- [ ] Section with breadcrumb, index chips, table with median row and column choice, "Against peers" table; `PEERS [SYM]` command.
- [ ] e2e for a bank, an IT company and a renamed company.
- [ ] Commit.

### Task 8: docs and suites

- [ ] TRD (2.36), master design, `graphify update .`, full suites, commit.

## As built (2026-10-18)

All eight tasks done. Differences from the plan: percentage fields take percent with or without the sign; the industry is spelled one way per industry and read from the largest index (a second pass over the first refresh, which showed `FINANCIAL SERVICES` and `Financial Services` as two industries); `/api/screens` is the plain document route as watchlists have, with validation on the page; the shared cell formatter moved to `screens/format.ts`. Counts: pytest 1,556 + 7, cargo 119, vitest 380, Playwright 12 new (5 Screen, 7 Peers).

