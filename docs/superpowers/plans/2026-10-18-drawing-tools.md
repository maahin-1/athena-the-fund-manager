# Drawing tools (first slice) — implementation plan

> Spec: `docs/superpowers/specs/2026-10-18-drawing-tools-design.md`. Execute inline, tests first, one commit per task. Web code in `terminal/web/src/chartlab/drawings/`, tests in `terminal/web/test/`, browser tests in `terminal/web/e2e/`.

**Goal:** draw, edit, lock, hide and delete 22 tool types on the Chart Lab chart, kept per symbol.
**Architecture:** pure engine modules (model, projection, geometry, magnet, creator, store, action log) tested without a canvas; one Lightweight Charts series primitive paints; one controller turns pointer events into engine calls; rail, properties bar and Objects tab in the view; one Rust route pair for persistence.
**Tech:** TypeScript, vitest, Playwright, Lightweight Charts 5.2 primitives, Rust/axum `LayoutStore`.

## Global constraints
- Anchors are `{t, p}` with `t` a bar's unix seconds; drawings never store pixels.
- Nothing the engine does mutates a list it was given.
- A document that fails validation is refused and the old drawings kept.
- No drawing on Renko, Line break, Range bars.
- The chart's own pan and zoom behave exactly as before whenever the cursor tool is on and the press is not on a drawing.

## Task 1 — model and validation
Files: create `chartlab/drawings/model.ts`, `test/drawings-model.test.ts`.
Interface: `ToolId`, `TOOLS: Record<ToolId, {label, group, anchors, clicks}>`, `Anchor`, `Style`, `Drawing`, `Doc`, `newDrawing(tool, points, style?)`, `validateDoc(raw): Doc` (throws `DrawingError`), `serializeDoc`, `add/remove/update/moveBy/moveHandle/duplicate/reorder/setLocked/setHidden/removeAll(list, ...) -> list`, `MAX_DRAWINGS = 500`.
Tests: every tool has anchors and group; validation of good docs and each failure (unknown tool, wrong count, NaN, colour, text length, over 500); every operation returns a new list and leaves the old; locked drawings refuse moves; duplicate offsets and gets a new id; reorder front/back.

## Task 2 — projection and magnet
Files: create `drawings/projection.ts`, `drawings/magnet.ts`, `test/drawings-projection.test.ts`.
Interface: `class Projection { constructor(times: number[], step: number, axes: { x(index): number|null; index(x): number|null; y(price): number|null; price(y): number|null }) ; x(t); t(x); y(p); price(y); indexOf(t) }`, `snap(projection, bars, x, y, mode: 'off'|'weak'|'strong'): Anchor`.
Tests: interpolation between bars, extrapolation past the end and before the start by whole steps, round trip t -> x -> t, a different interval's times, null axes give null; magnet off keeps the price, weak snaps within 12 px and not beyond, strong always snaps to the nearest of open/high/low/close, time always snaps to a bar.

## Task 3 — geometry and hit-testing
Files: create `drawings/geometry.ts`, `test/drawings-geometry.test.ts`.
Interface: `Shape = Line | Poly | Rect | Ellipse | Label | Fill`, `shapesOf(d, proj, bounds)`, `handlesOf(d, proj)`, `hit(d, proj, x, y, tolerance = 6)`, `fibLevels(d)`, `measureOf(d, times)`, `positionOf(d)`.
Tests: for a linear projection, each of the 22 tools produces the expected lines, extents (ray, extended, horizontal ray, vertical), channel offset, flat channel, ellipse box, triangle, fib prices from the spec formulas, extension, measure numbers (price change, percent, bars, span), position risk/reward and ratio, handle positions, hit on handle vs body vs miss, tolerance edge, topmost drawing wins, clipping to bounds.

## Task 4 — creator
Files: create `drawings/creator.ts`, `test/drawings-creator.test.ts`.
Interface: `class Creator { begin(tool); click(a: Anchor): Drawing | null; move(a: Anchor): Drawing | null; cancel(); get active(): ToolId | null; get preview(): Drawing | null }` plus position-tool completion (two clicks, stop mirrored).
Tests: one-, two- and three-anchor tools, preview follows the cursor, Escape cancels, a position tool ends in three anchors with the stop mirrored, beginning a tool while one is half drawn drops the old.

## Task 5 — store, action log and the server route
Files: create `drawings/store.ts`, `drawings/log.ts`, `test/drawings-store.test.ts`; modify `terminal/src/server.rs`, `terminal/src/main.rs`, `terminal/src/layouts.rs` (or a new `drawings.rs` for the key encoding).
Interface: `symbolKey(symbol) -> string | null` (same function in Rust), `class DrawingStore { load(symbol): Promise<Drawing[]>; save(symbol, list) debounced 400 ms; flush() }`, `class ActionLog { push(kind: 'chart'|'drawings'); undo(): kind|null; redo(): kind|null }`. Rust: `GET /api/drawings/:symbol`, `PUT /api/drawings/:symbol`, folder `drawings` beside `lists`.
Tests: key encoding (`M&M`, `NIFTY 50`, `A_B`, too long, bad characters), save debounce, local fallback when the server is down and resave when it is back, bad server document ignored; action-log order across kinds; Rust routes (204, round trip, size limit, bad symbol 400) and key encoding.

## Task 6 — primitive, renderer hooks, controller
Files: create `drawings/primitive.ts`, `drawings/controller.ts`; modify `chartlab/render.ts` (optional `drawingHost()` exposing chart, main series and times; `lockInteraction(bool)`; `requestRedraw()`), `chartlab/view.ts`.
Behaviour per spec section 7. Controller owns selection, drag, creation, keys; it is the only place that touches pointer events and talks to the store and log.
Tests: vitest for the controller with a stand-in host (select, drag a drawing, drag a handle, locked refuses, Escape, Delete, keep-drawing, magnet applied); the existing lab view tests must still pass with the stand-in renderer lacking the new optional methods.

## Task 7 — interface
Files: modify `chartlab/view.ts`, `style.css`; create `drawings/rail.ts`, `drawings/props.ts`, `drawings/objects.ts`.
Rail with groups and flyouts, magnet, keep drawing, lock all, hide all, remove all; properties bar; Objects tab; derived-type notice; narrow-window behaviour.
Tests: e2e `drawings.spec.ts` as in spec section 10; layout audit rerun.

## Task 8 — docs and close
TRD §2.30, master design §19, memory update, `graphify update .`, full vitest, Playwright, cargo test, commit.
