# Chart grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Grid tab of up to four Chart Lab charts with switchable symbol, interval, crosshair and time-range sync.

**Architecture:** Pure `chartlab/grid.ts` (layouts, saved state, moments, routing between cells). `ChartLabView` gets a `cell` mode and receive methods; the renderer gets optional crosshair and range members. `GridView` builds the cells and the controls, and `main.ts` adds the tab.

**Tech Stack:** TypeScript, Lightweight Charts v5 (`setCrosshairPosition`, visible logical range), vitest, Playwright.

## Global Constraints

- Spec `docs/superpowers/specs/2026-10-19-chart-grid-design.md`.
- At most 4 cells; layouts `2h`, `2v`, `3h`, `2x2`, `1+2`.
- Defaults: Symbol and Crosshair on, Interval and Time off; intervals 1D, 1W, 1h, 15m.
- A cell sends only what the pointer does in it; a receiving cell never sends it on.
- A single Chart Lab page behaves exactly as before.

---

### Task A: pure grid logic

**Files:** create `terminal/web/src/chartlab/grid.ts`, `terminal/web/test/grid.test.ts`.

- [ ] Tests first: layout table (cell counts, CSS areas); `parseGridState` (defaults, damaged value, unknown layout, bad interval, symbol text, more cells than the layout); `momentOf(bar.t, interval)` for intraday and daily; `indexAtMoment(bars, moment, interval)` for each interval pair, before the first bar, after the last, a holiday gap; `periodIndexes(bars, from, to, interval)`; `GridSync` routing with fake cells (each switch on and off, origin never receives, symbols case, interval refused by a cell is not forced).
- [ ] Implement; `npx vitest run`, `npx tsc --noEmit`; commit.

### Task B: Chart Lab cell mode

**Files:** modify `chartlab/view.ts`, `chartlab/render.ts`; create `test/labcell.test.ts`.

- [ ] Tests first (stub renderer as in `labview.test.ts`): a cell uses its own state key and feed name; does not follow the link group; a symbol or interval chosen in it calls the callbacks, applied from outside does not; its pointer sends a moment and period; `showMoment` and `showPeriod` reach the renderer as bar indexes; derived types neither send nor receive; hidden controls are absent; shortcuts are not taken.
- [ ] Implement the `cell` option, `applySymbol`, `applyInterval`, `showMoment`, `showPeriod`, renderer `onCrosshairMoved`, `showCrosshair`, `onRangeChanged`, `showRange`; run the Chart Lab unit and e2e specs; commit.

### Task C: the Grid tab

**Files:** create `chartlab/gridview.ts`; modify `main.ts` (tab, view state), `src/style.css`; create `e2e/grid.spec.ts`.

- [ ] E2E first: four charts at 1D, 1W, 1h, 15m; Interval switch; Symbol switch; layout change keeps symbols; reload keeps layout and switches; Crosshair readout in the other cells.
- [ ] Implement; full Playwright; commit.

### Task D: docs and suites

- [ ] TRD 2.44, master design section 26, roadmap row, `graphify update .`, full vitest and Playwright, commit.

## As built (2026-10-19)

Tasks A to D done. Two additions to the plan: `sharedFetcher` (one request for the bars several cells ask for together, kept 15 seconds) after the first end-to-end run showed a symbol change fetching the same daily bars four times against a busy server, and cells share the Chart Lab page's formula and Python indicator lists (`ChartLabView.shared()`) instead of each fetching them. The grid is its own tab rather than a mode of the Chart Lab; it joins F6 after Screen so the existing order (terminal, Chart Lab, Fundamentals) is unchanged. Moments for daily-based bars are 15:29 IST so a day's moment falls inside the last bar of every intraday interval. A Playwright locator that matched 'Volume' by substring now matches exactly (the profile button's label contains the word). Suites: vitest 589; the full Playwright run passes except the Fundamentals and search specs that read the fundamentals store, which time out while the full fetch is using the machine and pass alone (checked).
