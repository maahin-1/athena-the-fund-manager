# Heatmap and calendar Implementation Plan

> **For agentic workers:** execute task by task, tests first. Steps use checkbox syntax.

**Goal:** a heatmap and two calendars in the Screen tab.

**Architecture:** pure squarified treemap in TypeScript; an `events` table (Python) and `/api/calendar` (Rust) over `events` and `actions`; the Screen tab gains Table, Heatmap and Calendar modes.

**Tech Stack:** TypeScript, Python + DuckDB, Rust (axum), vitest, Playwright.

## Global Constraints

- No symbol or sector is named in code.
- A missing figure is grey on the heatmap, never zero.
- Calendar API: at most 366 days, 5,000 rows per list; bad dates are 400.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task 1: treemap (pure)

**Files:** create `terminal/web/src/treemap.ts`, `terminal/web/test/treemap.test.ts`.

- [ ] Tests: areas proportional to values, tiles inside the box and not overlapping, groups as boxes with their tiles inside, squarified aspect ratios, deterministic order, one item, zero and negative values skipped, empty input.
- [ ] Implement `layout(items, width, height, groupPad)`; commit.

### Task 2: heatmap in the Screen tab

**Files:** create `terminal/web/src/screens/heatmap.ts`, `colour.ts` and tests; modify `screen.ts`, `style.css`; e2e `e2e/heatmap.spec.ts`.

- [ ] Colour scale tests; heatmap view (group by, size by, colour by, legend, tooltip, selection); mode switch Table, Heatmap; follows filters and query.
- [ ] e2e on real data; commit.

### Task 3: events and the calendar API

**Files:** create `src/athena/warehouse/events.py`, `tests/test_warehouse_events.py`; modify `warehouse.py`, `build.py`; Rust `terminal/src/calendar.rs`, `server.rs`.

- [ ] Python tests and implementation; run the real refresh.
- [ ] Rust tests (classifier, route) and implementation; commit.

### Task 4: calendar page

**Files:** create `terminal/web/src/screens/calendar.ts`, test; modify `screen.ts`, `style.css`; e2e `e2e/calendar.spec.ts`.

- [ ] Range and filters, grouping by date, only my screen, watchlist marks; e2e on real data; commit.

### Task 5: docs and suites

- [ ] TRD (2.37), master design, `graphify update .`, full suites, commit.

## As built (2026-10-18)

All five tasks done. Differences from the plan: the events table also drops an event the exchange withdrew from a date its listing still covers; the corporate-action classifier gained `meeting` and `distribution` after checking all stored subjects (5,379 are general meetings and 942 interest payments); the heatmap writes a symbol only where it fits. Counts: pytest +6, cargo 126, vitest +36, Playwright 9 new (5 heatmap, 4 calendar).

