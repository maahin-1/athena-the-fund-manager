# Custom intervals and bar volume profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any interval a person types, built in the page from the finer interval the server serves; and a volume profile beside the price from the bars in view.

**Architecture:** A pure `interval.ts` parses, plans the source, groups bars and gives a bar's start time; `resample.ts`, `bars.ts`, `ranges.ts` and `model.ts` ask it for anything that is not built in. A pure `profile.ts` works out rows, point of control and value area; a second series primitive paints them in the price pane. No server change.

**Tech Stack:** TypeScript, Lightweight Charts v5 primitives, vitest, Playwright.

## Global Constraints

- Spec `docs/superpowers/specs/2026-10-18-intervals-profile-design.md`.
- Limits: minutes 1 to 375, hours 1 to 6, days 2 to 60, weeks 2 to 26, months 2 to 24.
- Grouping independent of the history loaded (fixed anchors since 1970 and the 09:15 IST open).
- Profile total over rows equals the total volume of the bars; value area 70%.
- No server change; built-in intervals behave exactly as before.

---

### Task A: interval parsing, planning and grouping

**Files:** create `terminal/web/src/interval.ts`, `terminal/web/test/interval.test.ts`; modify `ranges.ts` (`isIntraday` takes any text), `bars.ts` (`barTime`, `tickFitsChart`, `applyTick` take any text), `chartlab/resample.ts` (`sourceOf`, `build` for custom), `chartlab/model.ts` (`stepSeconds`, `tf` text).

- [ ] Tests first: parse (`7m`, `90`, `2h`, `3D`, `2W`, `3M`, `60m` is `1h`, `1d` is `1D`, `1M` month vs `1m` minute, refusals with limits); `planOf` source and depth; grouping for minutes (90-minute bars from 09:15, short last bar), days (holiday, weekend), weeks, months; the live-tick property (`barTime` of every source bar equals the stamp of the bar that contains it); `rangesFor` and `defaultRange`.
- [ ] Implement `parseInterval`, `planOf`, `groupBars`, `startOf`, `rangesFor`, `defaultRange`, `stepOf`; widen the signatures; keep built-in tables as they are.
- [ ] Run `npx vitest run`, `npx tsc --noEmit`; commit.

### Task B: the interval box in the Chart Lab

**Files:** modify `chartlab/view.ts` (box, recent buttons, `range()`, `reload()`, compare load, title), `src/style.css`; create `e2e/intervals.spec.ts`.

- [ ] Test first (e2e with a stubbed `/api/bars`): typing `7m` shows `SBIN · 7m`, a button that is on, the 1D and 5D ranges and bars grouped by seven minutes; `1d` selects the built-in 1D; `abc` and `500m` are refused with the reason and nothing changes; a reload keeps the recent buttons.
- [ ] Implement; run the full e2e for the Chart Lab; commit.

### Task C: volume profile

**Files:** create `chartlab/profile.ts`, `chartlab/profilepaint.ts`, `test/profile.test.ts`; modify `model.ts` (`profile` state, `profileOf()`), `store.ts` (optional in saved state), `render.ts` (second primitive, visible range), `view.ts` (toggle, rows, data window lines), `style.css`; create `e2e/profile.spec.ts`.

- [ ] Tests first: rows sum to the volume; single-row bar; a bar over two rows splits by overlap; rising/falling split; point of control; value area holds at least 70% and is contiguous; zero volume gives no rows; rows clamp to 4..200; saved state without `profile` still opens.
- [ ] Implement pure `volumeProfile(bars, rows)`; the painter (rows from the right edge, 30% of the pane width at most, point-of-control line, value area stronger); the toggle and row choice; data window lines; off for derived types and for zero volume (with the reason).
- [ ] E2E: turn it on, the data window gives the point of control; a reload keeps it on; off again.
- [ ] Run suites; commit.

### Task D: docs and suites

- [ ] TRD 2.42 (intervals) and 2.43 (profile), master design section 25, roadmap row, `graphify update .`, full vitest, Playwright, commit.
