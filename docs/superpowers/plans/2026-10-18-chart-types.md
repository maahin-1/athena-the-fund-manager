# Chart types (high-low, volume candles, Kagi, point and figure) Implementation Plan

> **For agentic workers:** execute task by task, tests first. Steps use checkbox syntax.

**Goal:** four more chart types in the Chart Lab, for every symbol and interval.

**Architecture:** pure builders in `derived.ts`; pure shape geometry in `shapes.ts`; one Lightweight Charts custom series in `render.ts` that paints the shapes; model and store know the new ids.

**Tech Stack:** TypeScript, Lightweight Charts 5.2.1 (`addCustomSeries`), vitest (jsdom), Playwright.

## Global Constraints

- Price-move types (`kagi`, `pnf`) are derived: `isDerived` is true, so volume, indicators, compare and drawings are off, exactly as for Renko.
- Time-based types (`highlow`, `volcandles`) keep everything the candles have.
- Sizes: `autoSize(bars)` for the reversal amount and the box size; point and figure reversal is 3 boxes.
- No symbol is named anywhere.
- Commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task 1: builders

**Files:** modify `src/chartlab/derived.ts`; test `test/derived.test.ts`.

- [ ] Tests first: `kagi(bars, reversal)` (no line until the move reaches the reversal; a line extends in its direction; a turn needs the full reversal; the turn starts at the previous line's end; thickness becomes thick above the previous swing high and thin below the previous swing low, with the price where it flipped); `pointAndFigure(bars, box, 3)` (a rising column, a falling column after three boxes back, nothing for a smaller move, columns start one box inside the previous one, box edges on the grid); empty and one-bar input give nothing.
- [ ] Implement `kagi`, `pointAndFigure`; make `sequence` generic so extra fields survive.
- [ ] Run `npx vitest run test/derived.test.ts`. Commit.

### Task 2: shapes

**Files:** create `src/chartlab/shapes.ts`; test `test/shapes.test.ts`.

- [ ] Tests first: `highLowShapes`, `volumeShapes` (width in proportion to volume over the largest in view; minimum one pixel; wick), `kagiShapes` (verticals, connectors at the previous end, a split at the flip price, thick and thin roles), `pnfShapes` (one X or O per box from the column's low to its high, size from the box height in pixels).
- [ ] Implement the four functions over `{x, datum}` items and a price-to-y function.
- [ ] Run the tests. Commit.

### Task 3: model, store, renderer

**Files:** modify `src/chartlab/model.ts`, `store.ts`, `render.ts`, `view.ts` (the messages); tests `test/model.test.ts`, `test/labview.test.ts`, `test/resample.test.ts`.

- [ ] Tests first: the type menu lists the four new types in order; the store accepts them and refuses others; `displayBars` for `kagi` and `pnf` is derived and stamped in sequence; `highlow` and `volcandles` are not derived.
- [ ] Implement the custom series view (`ShapeSeries`) and the type wiring; update the drawings-off message to name all price-move types.
- [ ] `npx tsc --noEmit`, `npx vitest run`. Commit.

### Task 4: browser and docs

**Files:** modify `e2e/chartlab.spec.ts`; TRD (2.34), master design, memory.

- [ ] e2e: each new type on three symbols draws pixels, no console error, survives an interval change, and the drawing rail is off for Kagi and point and figure only.
- [ ] Look at each type in a real browser (screenshots), fix what looks wrong.
- [ ] Docs, `graphify update .`, full suites. Commit.

## As built (2026-10-18)

All four tasks done. Differences from the plan: the custom series lives in its own file (`shapeseries.ts`) rather than `render.ts`; the point-and-figure box is half of the average true range, not the whole one the spec said (with a whole one a column would turn only after three average ranges); price-move charts drop the volume margin. Counts at the end: see TRD 2.34 for the checks.

